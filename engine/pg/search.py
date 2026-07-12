"""Hybrid retrieval on PostgreSQL: Full-Text Search + pgvector kNN, fused.

Mirrors :class:`engine.search.SearchService` (same inputs, same
``SearchResults`` output) but runs entirely on Postgres:

- **Lexical**  — ``websearch_to_tsquery`` matched against the ``search_vector``
  ``tsvector`` column, ranked with ``ts_rank_cd`` (BM25-like).
- **Semantic** — cosine kNN via the pgvector ``<=>`` operator on ``embedding``.
- **Fusion**   — the same Reciprocal Rank Fusion the Elasticsearch backend uses.

Filters, facets, paging, and ``related`` are all supported. Queries run on a
single short-lived connection; a dropped connection surfaces as
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

        run_fts = mode in ("hybrid", "bm25") and bool(query)
        run_knn = mode in ("hybrid", "semantic") and bool(query)
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
                docs = self._fetch(conn, [doc_id for doc_id, _ in window], RealDictCursor)
                hits = [
                    SearchHit(document=docs[doc_id], score=round(score, 6))
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

    def _fetch(self, conn, ids: List[str], dict_cursor) -> Dict[str, Any]:
        if not ids:
            return {}
        cols = ", ".join(_SELECT_COLUMNS)
        with conn.cursor(cursor_factory=dict_cursor) as cur:
            cur.execute(
                f"SELECT {cols} FROM {self.table} WHERE id = ANY(%(ids)s::text[])",
                {"ids": ids},
            )
            return {row["id"]: row_to_document(row) for row in cur.fetchall()}

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
        try:
            with self.store.connect() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        f"SELECT embedding, title FROM {self.table} WHERE id = %s",
                        (doc_id,),
                    )
                    base = cur.fetchone()
                    if not base:
                        return []
                    if base["embedding"] is not None:
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
