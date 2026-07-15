"""Hybrid retrieval on PostgreSQL: Full-Text Search + pgvector kNN, fused.

Mirrors :class:`engine.search.SearchService` (same inputs, same
``SearchResults`` output) but runs entirely on Postgres:

- **Lexical**  — ``websearch_to_tsquery`` matched against the ``search_vector``
  ``tsvector`` column, ranked with ``ts_rank_cd`` (BM25-like).
- **Semantic** — cosine kNN via the pgvector ``<=>`` operator on ``embedding``.
- **Fusion**   — the same Reciprocal Rank Fusion the Elasticsearch backend uses.

Filters, facets, paging, highlights, and ``related`` are all supported. Queries
run on a single short-lived connection; a dropped connection surfaces as
:class:`~engine.search.SearchBackendError` (HTTP 503).
"""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List, Optional, Tuple

from engine.config import EngineConfig, get_config
from engine.embeddings import Embedder, get_embedder
from engine.search import (
    SearchBackendError,
    SearchFilters,
    SearchHit,
    SearchResults,
    reciprocal_rank_fusion,
)
from engine.pg.store import (
    _SELECT_COLUMNS,
    get_store,
    row_to_document,
    vector_literal,
)

_FACET_TERM_FIELDS = ("source", "kind", "language")

# --------------------------------------------------------------------------- #
# Highlighting (``ts_headline``)
# --------------------------------------------------------------------------- #
# Fragments carry the same contract as the Elasticsearch highlighter: raw
# document text with ``<em>``/``</em>`` around matched terms — consumers are
# responsible for escaping everything else. ``ts_headline`` is told to mark
# matches with control characters rather than a literal "<em>" a document
# could plainly contain; a document that somehow carries these bytes can at
# worst yield a stray (inert) ``<em>`` marker, never an arbitrary tag. The
# sentinels are swapped for ``<em>`` tags in :func:`headline_fragments`.
_HL_START = "\x02"
_HL_STOP = "\x03"
_HL_DELIM = "\x01"
_HL_OPTIONS = (
    f'StartSel="{_HL_START}", StopSel="{_HL_STOP}", '
    f'FragmentDelimiter="{_HL_DELIM}", MaxFragments=2, MaxWords=25, MinWords=8'
)
# ``ts_headline`` walks the whole input; cap it so a huge crawled body cannot
# stall the page-window fetch (matches beyond this depth are rare anyway).
_HL_MAX_CHARS = 50_000


def headline_fragments(raw: Optional[str]) -> List[str]:
    """Convert one ``ts_headline`` result into ES-style highlight fragments.

    Splits on the fragment delimiter and keeps only fragments that contain an
    actual match — ``ts_headline`` returns a matchless text prefix when the
    query terms are absent (e.g. a purely semantic kNN hit), and the ES
    backend returns no highlights for those either.
    """
    if not raw:
        return []
    fragments: List[str] = []
    for frag in raw.split(_HL_DELIM):
        frag = frag.strip()
        if _HL_START not in frag:
            continue
        fragments.append(
            frag.replace(_HL_START, "<em>").replace(_HL_STOP, "</em>")
        )
    return fragments


class PgSearchService:
    def __init__(
        self,
        config: Optional[EngineConfig] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.config = config or get_config()
        self.store = get_store(self.config)
        self.embedder = embedder or get_embedder()
        self.table = self.config.index_name

    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        filters: Optional[SearchFilters] = None,
        mode: str = "hybrid",
        page: int = 1,
        per_page: int = 20,
        include_facets: bool = True,
    ) -> SearchResults:
        query = (query or "").strip()
        filters = filters or SearchFilters()
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        where_sql, params = self._where(filters)

        want = page * per_page
        cand = max(self.config.bm25_candidates, want)

        # pgvector is optional. When it is unavailable, only lexical retrieval
        # is possible, so ``semantic`` mode degrades to full-text search.
        vector_ok = self.store.has_vector()
        run_knn = mode in ("hybrid", "semantic") and bool(query) and vector_ok
        run_fts = bool(query) and (
            mode in ("hybrid", "bm25") or (mode == "semantic" and not vector_ok)
        )
        run_browse = not query

        started = time.time()
        try:
            from psycopg2.extras import RealDictCursor

            with self.store.connect() as conn:
                rankings: List[List[str]] = []
                if run_fts:
                    rankings.append(self._fts_ids(conn, query, where_sql, params, cand))
                if run_knn:
                    qvec = vector_literal(self.embedder.encode(query))
                    rankings.append(self._knn_ids(conn, qvec, where_sql, params, cand))
                if run_browse:
                    rankings.append(self._browse_ids(conn, where_sql, params, want))

                if len(rankings) > 1:
                    fused = reciprocal_rank_fusion(rankings, k=self.config.rrf_k)
                elif rankings:
                    fused = [
                        (doc_id, 1.0 / (i + 1))
                        for i, doc_id in enumerate(rankings[0])
                    ]
                else:
                    fused = []

                start = (page - 1) * per_page
                window = fused[start : start + per_page]
                docs, highlights = self._fetch(
                    conn,
                    [doc_id for doc_id, _ in window],
                    RealDictCursor,
                    query=query,
                )
                hits = [
                    SearchHit(
                        document=docs[doc_id],
                        score=round(score, 6),
                        highlights=highlights.get(doc_id, []),
                    )
                    for doc_id, score in window
                    if doc_id in docs
                ]

                facets: Dict[str, List[Dict[str, Any]]] = {}
                total = len(fused)
                if include_facets:
                    try:
                        facets, agg_total = self._facets(conn, query, where_sql, params)
                        if run_browse:
                            total = agg_total
                    except Exception:
                        facets = {}
        except Exception as exc:  # connection drop / SQL error
            raise SearchBackendError(f"Postgres search failed: {exc}") from exc

        return SearchResults(
            query=query,
            mode=mode,
            total=total,
            hits=hits,
            facets=facets,
            took_ms=int((time.time() - started) * 1000),
            page=page,
            per_page=per_page,
        )

    # ------------------------------------------------------------------ #
    # Filters -> SQL
    # ------------------------------------------------------------------ #
    def _where(self, filters: SearchFilters) -> Tuple[str, Dict[str, Any]]:
        conds: List[str] = []
        params: Dict[str, Any] = {}
        if filters.sources:
            conds.append("source = ANY(%(f_sources)s::text[])")
            params["f_sources"] = filters.sources
        if filters.kinds:
            conds.append("kind = ANY(%(f_kinds)s::text[])")
            params["f_kinds"] = filters.kinds
        if filters.categories:
            conds.append("categories && %(f_cats)s::text[]")
            params["f_cats"] = filters.categories
        if filters.language:
            conds.append("language = ANY(%(f_langs)s::text[])")
            params["f_langs"] = filters.language
        if filters.version:
            conds.append("version = %(f_version)s")
            params["f_version"] = filters.version
        if filters.has_code is not None:
            conds.append("has_code = %(f_has_code)s")
            params["f_has_code"] = filters.has_code
        if filters.has_equations is not None:
            conds.append("has_equations = %(f_has_eq)s")
            params["f_has_eq"] = filters.has_equations
        if filters.year_from is not None:
            conds.append("published_date >= %(f_yfrom)s")
            params["f_yfrom"] = datetime.date(filters.year_from, 1, 1)
        if filters.year_to is not None:
            conds.append("published_date <= %(f_yto)s")
            params["f_yto"] = datetime.date(filters.year_to, 12, 31)
        return " AND ".join(conds), params

    @staticmethod
    def _and(where_sql: str) -> str:
        return f" AND {where_sql}" if where_sql else ""

    # ------------------------------------------------------------------ #
    # Retrieval primitives
    # ------------------------------------------------------------------ #
    def _fts_ids(self, conn, query, where_sql, params, limit) -> List[str]:
        sql = (
            f"SELECT id FROM {self.table}, "
            f"websearch_to_tsquery('english', %(q)s) AS query "
            f"WHERE search_vector @@ query{self._and(where_sql)} "
            f"ORDER BY ts_rank_cd(search_vector, query) DESC LIMIT %(limit)s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {**params, "q": query, "limit": limit})
            return [r[0] for r in cur.fetchall()]

    def _knn_ids(self, conn, qvec, where_sql, params, limit) -> List[str]:
        sql = (
            f"SELECT id FROM {self.table} "
            f"WHERE embedding IS NOT NULL{self._and(where_sql)} "
            f"ORDER BY embedding <=> %(qvec)s::vector LIMIT %(limit)s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {**params, "qvec": qvec, "limit": limit})
            return [r[0] for r in cur.fetchall()]

    def _browse_ids(self, conn, where_sql, params, limit) -> List[str]:
        where = f" WHERE {where_sql}" if where_sql else ""
        sql = (
            f"SELECT id FROM {self.table}{where} "
            f"ORDER BY published_date DESC NULLS LAST LIMIT %(limit)s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, {**params, "limit": limit})
            return [r[0] for r in cur.fetchall()]

    def _fetch(
        self, conn, ids: List[str], dict_cursor, query: str = ""
    ) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
        """Fetch the page-window documents, plus highlights when searching.

        Returns ``(documents_by_id, highlight_fragments_by_id)``. Highlights
        are computed here — for the final page window only, never for every
        candidate — via ``ts_headline`` over the abstract and body.
        """
        if not ids:
            return {}, {}
        cols = ", ".join(_SELECT_COLUMNS)
        params: Dict[str, Any] = {"ids": ids}
        hl_col = ""
        if query:
            hl_col = (
                ", ts_headline('english', "
                "left(coalesce(abstract, '') || ' ' || coalesce(body, ''), "
                "%(hl_chars)s), "
                "websearch_to_tsquery('english', %(hl_q)s), %(hl_opts)s) AS hl"
            )
            params.update(hl_q=query, hl_opts=_HL_OPTIONS, hl_chars=_HL_MAX_CHARS)
        with conn.cursor(cursor_factory=dict_cursor) as cur:
            cur.execute(
                f"SELECT {cols}{hl_col} FROM {self.table} "
                f"WHERE id = ANY(%(ids)s::text[])",
                params,
            )
            docs: Dict[str, Any] = {}
            highlights: Dict[str, List[str]] = {}
            for row in cur.fetchall():
                frags = headline_fragments(row.pop("hl", None))
                doc = row_to_document(row)
                docs[doc.id] = doc
                if frags:
                    highlights[doc.id] = frags
            return docs, highlights

    # ------------------------------------------------------------------ #
    def _facets(
        self, conn, query: str, where_sql: str, params: Dict[str, Any]
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
        base_conds: List[str] = []
        p = dict(params)
        if query:
            base_conds.append(
                "search_vector @@ websearch_to_tsquery('english', %(q)s)"
            )
            p["q"] = query
        if where_sql:
            base_conds.append(where_sql)
        base = (" WHERE " + " AND ".join(base_conds)) if base_conds else ""

        facets: Dict[str, List[Dict[str, Any]]] = {}
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.table}{base}", p)
            total = int(cur.fetchone()[0])

            for field in _FACET_TERM_FIELDS:
                cur.execute(
                    f"SELECT {field} AS v, count(*) AS c FROM {self.table}{base} "
                    f"{'AND' if base else 'WHERE'} {field} IS NOT NULL "
                    f"GROUP BY {field} ORDER BY c DESC LIMIT 20",
                    p,
                )
                facets[field] = [
                    {"value": v, "count": c} for v, c in cur.fetchall()
                ]

            cur.execute(
                f"SELECT v, count(*) AS c FROM {self.table}, "
                f"unnest(categories) AS v{base} GROUP BY v ORDER BY c DESC LIMIT 20",
                p,
            )
            facets["categories"] = [
                {"value": v, "count": c} for v, c in cur.fetchall()
            ]

            for field in ("has_code", "has_equations"):
                cur.execute(
                    f"SELECT {field} AS v, count(*) AS c FROM {self.table}{base} "
                    f"GROUP BY {field}",
                    p,
                )
                facets[field] = [
                    {"value": v, "count": c} for v, c in cur.fetchall()
                ]
        return facets, total

    # ------------------------------------------------------------------ #
    def related(self, doc_id: str, size: int = 8) -> List[SearchHit]:
        from psycopg2.extras import RealDictCursor

        cols = ", ".join(_SELECT_COLUMNS)
        has_vec = self.store.has_vector()
        base_sel = "embedding, title" if has_vec else "title"
        try:
            with self.store.connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT {base_sel} FROM {self.table} WHERE id = %s",
                        (doc_id,),
                    )
                    base = cur.fetchone()
                    if not base:
                        return []
                    if has_vec and base["embedding"] is not None:
                        cur.execute(
                            f"SELECT {cols} FROM {self.table} "
                            f"WHERE id != %s AND embedding IS NOT NULL "
                            f"ORDER BY embedding <=> %s::vector LIMIT %s",
                            (doc_id, base["embedding"], size),
                        )
                    else:
                        cur.execute(
                            f"SELECT {cols} FROM {self.table} "
                            f"WHERE id != %s AND search_vector @@ "
                            f"websearch_to_tsquery('english', %s) "
                            f"ORDER BY ts_rank_cd(search_vector, "
                            f"websearch_to_tsquery('english', %s)) DESC LIMIT %s",
                            (doc_id, base["title"] or "", base["title"] or "", size),
                        )
                    rows = cur.fetchall()
            return [SearchHit(document=row_to_document(r), score=0.0) for r in rows]
        except Exception:
            return []
