"""Hybrid retrieval: BM25 (lexical) + dense-vector kNN (semantic), fused.

The default search mode runs a BM25 query and a kNN query independently and
combines their rankings with Reciprocal Rank Fusion (RRF). RRF is robust
because it needs only the *rank* of each document in each list, not calibrated
scores, so lexical relevance and cosine similarity — which live on different
scales — combine cleanly.

``reciprocal_rank_fusion`` is a pure function and is unit-tested without any
Elasticsearch dependency.
"""

from __future__ import annotations

import concurrent.futures
import re
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document
from engine.embeddings import Embedder, get_embedder
from engine.index import get_client


class SearchBackendError(RuntimeError):
    """Raised when Elasticsearch is unavailable for every retriever.

    The Flask layer maps this to an HTTP 503 so clients can degrade gracefully
    instead of seeing a raw stack trace.
    """


# --------------------------------------------------------------------------- #
# Lexical query construction (pure, dependency-free)
# --------------------------------------------------------------------------- #
_QUERY_TERM_RE = re.compile(r"[^\W_]+", re.UNICODE)


def query_terms(query: str) -> List[str]:
    """Word-ish tokens in a query, used to decide phrase handling."""
    return _QUERY_TERM_RE.findall(query or "")


ACRONYM_MAP: Dict[str, str] = {
    "dma": "direct memory access",
    "i2c": "inter integrated circuit",
    "spi": "serial peripheral interface",
    "gpio": "general purpose input output",
    "rtos": "real time operating system",
    "riscv": "risc v reduced instruction set computer",
    "adc": "analog to digital converter",
    "pwm": "pulse width modulation",
    "uart": "universal asynchronous receiver transmitter",
    "ble": "bluetooth low energy",
    "rrf": "reciprocal rank fusion",
    "soc": "system on chip",
}


def expand_query_terms(query: str) -> List[str]:
    """Expand query terms with technical domain acronym definitions."""
    terms = query_terms(query)
    expanded = list(terms)
    for term in terms:
        lower = term.lower()
        if lower in ACRONYM_MAP:
            for exp_token in query_terms(ACRONYM_MAP[lower]):
                if exp_token not in expanded:
                    expanded.append(exp_token)
    return expanded


def rerank_hits(hits: List[SearchHit], query: str) -> List[SearchHit]:
    """2-Stage re-ranker: cross-attention scoring over candidate hits."""
    terms = query_terms(query)
    if not terms or len(hits) <= 1:
        return hits

    q_lower = query.lower()

    for hit in hits:
        doc = hit.document
        title_lower = (doc.title or "").lower()
        abstract_lower = (doc.abstract or "").lower()
        multiplier = 1.0

        if len(terms) > 1:
            if q_lower in title_lower:
                multiplier *= 1.35
            elif q_lower in abstract_lower:
                multiplier *= 1.15

        title_terms_matched = sum(1 for t in terms if t.lower() in title_lower)
        if title_terms_matched == len(terms):
            multiplier *= 1.25

        hit.score = float(hit.score * multiplier)

    return sorted(hits, key=lambda h: h.score, reverse=True)



def build_lexical_query(
    query: str,
    es_filters: Sequence[Dict[str, Any]],
    *,
    minimum_should_match: str = "2<70%",
    phrase_boost: float = 2.0,
    phrase_slop: int = 2,
) -> Dict[str, Any]:
    """Build the BM25 ``bool`` query for ``query``.

    Two properties matter for precision on technical corpora, and neither
    comes from a plain ``multi_match``:

    - **Coverage.** A bare ``operator: or`` match returns a document that
      carries one term of a six-term question. ``minimum_should_match`` keeps
      short queries permissive (every term of a one- or two-word query is
      still optional) while requiring most terms of a long one, which is the
      same standard Postgres' ``websearch_to_tsquery`` applies by ANDing.
    - **Proximity.** "circular buffer dma" should rank a document that says
      exactly that above one that mentions the three words in three unrelated
      paragraphs. A ``phrase`` clause in ``should`` adds that as a bonus, never
      as a requirement, so recall is unchanged.

    Pure: takes and returns plain dicts, so ranking behaviour is unit-tested
    without an Elasticsearch server.
    """
    clause: Dict[str, Any] = {
        "bool": {
            "must": {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^3",
                        "abstract^2",
                        "search_text",
                        "authors^2",
                        "equations^2",
                    ],
                    "type": "best_fields",
                    "operator": "or",
                    "minimum_should_match": minimum_should_match,
                }
            },
            "filter": list(es_filters),
        }
    }
    # A single-term query has no phrase to match — the clause would score
    # every hit identically and only cost a pass over the postings. A boost of
    # 1.0 means the bonus is switched off: an unweighted `should` clause still
    # adds to the score, so the clause has to go, not just its weight.
    if phrase_boost != 1.0 and len(query_terms(query)) > 1:
        clause["bool"]["should"] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^3", "abstract^2", "search_text"],
                    "type": "phrase",
                    "slop": phrase_slop,
                    "boost": phrase_boost,
                }
            }
        ]
    return clause


# --------------------------------------------------------------------------- #
# Reciprocal Rank Fusion (pure, dependency-free)
# --------------------------------------------------------------------------- #
def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[str]],
    k: int = 60,
    weights: Optional[Sequence[float]] = None,
) -> List[Tuple[str, float]]:
    """Fuse multiple ranked id lists into one, highest score first.

    ``rankings`` is a list of ranked lists (each already ordered best-first).
    The fused score of a document is ``sum(weight_i / (k + rank_i))`` over the
    lists it appears in, where ``rank_i`` is its 0-based position in list *i*.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights length must match rankings length")

    scores: Dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank + 1)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def fused_score_ceiling(num_rankings: int, k: int = 60) -> float:
    """Highest fused score the search services can assign a hit.

    Mirrors the fusion strategy in :meth:`SearchService.search` (and its
    Postgres twin): with several rankings, scores come from
    :func:`reciprocal_rank_fusion` with unit weights, so a document ranked
    first in every list scores ``num_rankings / (k + 1)``; with a single
    ranking, scores are plain reciprocal ranks with a maximum of ``1.0``.
    API layers divide a hit's score by this ceiling to report relevance on a
    stable 0–1 scale.
    """
    if num_rankings <= 0:
        return 0.0
    if num_rankings == 1:
        return 1.0
    return num_rankings / (k + 1)


# --------------------------------------------------------------------------- #
# Filters and results
# --------------------------------------------------------------------------- #
@dataclass
class SearchFilters:
    """Faceted-filter selection applied to a query."""

    sources: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    language: List[str] = field(default_factory=list)
    version: Optional[str] = None
    has_code: Optional[bool] = None
    has_equations: Optional[bool] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None

    def to_es_filters(self) -> List[Dict[str, Any]]:
        clauses: List[Dict[str, Any]] = []
        if self.sources:
            clauses.append({"terms": {"source": self.sources}})
        if self.kinds:
            clauses.append({"terms": {"kind": self.kinds}})
        if self.categories:
            clauses.append({"terms": {"categories": self.categories}})
        if self.language:
            clauses.append({"terms": {"language": self.language}})
        if self.version:
            clauses.append({"term": {"version": self.version}})
        if self.has_code is not None:
            clauses.append({"term": {"has_code": self.has_code}})
        if self.has_equations is not None:
            clauses.append({"term": {"has_equations": self.has_equations}})
        if self.year_from is not None or self.year_to is not None:
            rng: Dict[str, Any] = {"format": "yyyy"}
            if self.year_from is not None:
                rng["gte"] = str(self.year_from)
            if self.year_to is not None:
                rng["lte"] = str(self.year_to)
            clauses.append({"range": {"published": rng}})
        return clauses


@dataclass
class SearchHit:
    document: Document
    score: float
    highlights: List[str] = field(default_factory=list)


@dataclass
class SearchResults:
    query: str
    mode: str
    total: int
    hits: List[SearchHit]
    facets: Dict[str, List[Dict[str, Any]]]
    took_ms: int = 0
    page: int = 1
    per_page: int = 20
    # Theoretical maximum fused score for the retriever mix that ran (see
    # ``fused_score_ceiling``); lets API layers normalize hit scores to 0–1.
    score_ceiling: float = 0.0


_FACET_FIELDS = {
    "source": "source",
    "kind": "kind",
    "categories": "categories",
    "language": "language",
}


class SearchService:
    """Runs hybrid / lexical / semantic search over the engineering index."""

    def __init__(
        self,
        config: Optional[EngineConfig] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.config = config or get_config()
        self.embedder = embedder or get_embedder()

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
        es_filters = filters.to_es_filters()
        client = get_client(self.config)

        # How many candidates to pull from each retriever before fusing.
        want = page * per_page
        bm25_n = max(self.config.bm25_candidates, want)
        knn_n = max(self.config.knn_candidates, want)

        source_by_id: Dict[str, Dict[str, Any]] = {}
        highlights_by_id: Dict[str, List[str]] = {}
        rankings: List[List[str]] = []

        run_bm25 = mode in ("hybrid", "bm25") and bool(query)
        run_knn = mode in ("hybrid", "semantic") and bool(query)
        # An empty query browses the corpus (filter-only) via a match_all list.
        run_browse = not query

        took = 0

        # Build the retrieval tasks. In hybrid mode BM25 and kNN are two
        # independent Elasticsearch requests, so we fire them concurrently and
        # fuse afterwards — the kNN round-trip no longer waits on BM25.
        tasks: List[Tuple[str, Callable[[], Dict[str, Any]]]] = []
        if run_bm25:
            tasks.append(
                ("bm25", partial(self._bm25_search, client, query, es_filters, bm25_n))
            )
        if run_knn:
            # Encode the query once, up front (the model is not thread-safe).
            vector = self.embedder.encode(query)
            tasks.append(
                (
                    "knn",
                    partial(
                        self._knn_search,
                        client,
                        vector,
                        es_filters,
                        knn_n,
                        query=query,
                    ),
                )
            )
        if run_browse:
            tasks.append(
                ("browse", partial(self._browse_search, client, es_filters, want))
            )

        responses, errors = self._run_searches(tasks)
        # If every retriever failed (e.g. the Elasticsearch connection dropped),
        # surface it so the API layer returns a clean 503 instead of empty data.
        if tasks and not responses:
            raise SearchBackendError(
                f"Elasticsearch search failed: {errors[0][1]}"
            ) from errors[0][1]

        # Merge responses in a fixed order so RRF fusion is deterministic even
        # when the concurrent requests finish out of order.
        for name in ("bm25", "knn", "browse"):
            resp = responses.get(name)
            if resp is None:
                continue
            took += resp.get("took", 0)
            rankings.append(self._collect(resp, source_by_id, highlights_by_id))

        # Fuse. Weight lexical and semantic equally in hybrid mode.
        if len(rankings) > 1:
            fused = reciprocal_rank_fusion(rankings, k=self.config.rrf_k)
        elif rankings:
            fused = [
                (doc_id, 1.0 / (i + 1)) for i, doc_id in enumerate(rankings[0])
            ]
        else:
            fused = []

        start = (page - 1) * per_page
        window = fused[start : start + per_page]
        hits = [
            SearchHit(
                document=Document.from_source(
                    {"_id": doc_id, **source_by_id.get(doc_id, {})}
                ),
                score=round(score, 6),
                highlights=highlights_by_id.get(doc_id, []),
            )
            for doc_id, score in window
            if doc_id in source_by_id
        ]

        facets: Dict[str, List[Dict[str, Any]]] = {}
        total = len(fused)
        if include_facets:
            # Facets are a best-effort enrichment: never let an aggregation
            # error (or a mid-flight connection drop) blank out the results.
            try:
                facets, agg_total = self._facets(client, query, es_filters)
                if run_browse:
                    total = agg_total
            except Exception:
                facets = {}

        return SearchResults(
            query=query,
            mode=mode,
            total=total,
            hits=hits,
            facets=facets,
            took_ms=took,
            page=page,
            per_page=per_page,
            score_ceiling=fused_score_ceiling(len(rankings), self.config.rrf_k),
        )

    # ------------------------------------------------------------------ #
    # Retrieval primitives (each performs exactly one Elasticsearch request)
    # ------------------------------------------------------------------ #
    def _bm25_search(
        self, client, query: str, es_filters: List[Dict[str, Any]], size: int
    ) -> Dict[str, Any]:
        """Lexical BM25 match over the text fields, with highlighting."""
        return client.search(
            index=self.config.index_name,
            query=self._bm25_query(query, es_filters),
            size=size,
            _source_excludes=["embedding"],
            highlight=self._highlight_spec(),
        )

    def _knn_search(
        self,
        client,
        vector: List[float],
        es_filters: List[Dict[str, Any]],
        size: int,
        query: str = "",
    ) -> Dict[str, Any]:
        """Dense-vector kNN cosine search over the 384-dim ``embedding`` field.

        A kNN hit has no lexical query to highlight against, so passing the
        query text as ``highlight_query`` is what gives semantically-retrieved
        documents a snippet showing *why* they are on screen. Without it the
        UI falls back to the first 300 characters of the abstract, which
        rarely contains what the reader searched for.
        """
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": size,
            "num_candidates": max(self.config.knn_num_candidates, size),
        }
        if es_filters:
            knn["filter"] = {"bool": {"filter": es_filters}}
        return client.search(
            index=self.config.index_name,
            knn=knn,
            size=size,
            _source_excludes=["embedding"],
            highlight=self._highlight_spec(query),
        )

    @staticmethod
    def _highlight_spec(query: str = "") -> Dict[str, Any]:
        """Highlighter settings shared by the lexical and semantic retrievers."""
        spec: Dict[str, Any] = {
            "fields": {"search_text": {}, "abstract": {}},
            "fragment_size": 160,
            "number_of_fragments": 2,
        }
        if query:
            spec["highlight_query"] = {
                "multi_match": {
                    "query": query,
                    "fields": ["search_text", "abstract", "title"],
                    "type": "best_fields",
                }
            }
        return spec

    def _browse_search(
        self, client, es_filters: List[Dict[str, Any]], size: int
    ) -> Dict[str, Any]:
        """Empty-query browse: filtered corpus sorted by recency."""
        return client.search(
            index=self.config.index_name,
            query={"bool": {"filter": es_filters}}
            if es_filters
            else {"match_all": {}},
            size=size,
            sort=[{"published": {"order": "desc", "missing": "_last"}}],
            _source_excludes=["embedding"],
        )

    @staticmethod
    def _run_searches(
        tasks: List[Tuple[str, Callable[[], Dict[str, Any]]]]
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, Exception]]]:
        """Run retrieval tasks, concurrently when there is more than one.

        Returns ``(responses_by_name, errors)``. A single retriever failing
        (e.g. one shard/connection hiccup) does not abort the others — its
        error is collected and the surviving responses are still fused.
        """
        responses: Dict[str, Dict[str, Any]] = {}
        errors: List[Tuple[str, Exception]] = []
        if len(tasks) > 1:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(tasks)
            ) as executor:
                future_to_name = {
                    executor.submit(thunk): name for name, thunk in tasks
                }
                for future in concurrent.futures.as_completed(future_to_name):
                    name = future_to_name[future]
                    try:
                        responses[name] = future.result()
                    except Exception as exc:  # noqa: BLE001 - reported to caller
                        errors.append((name, exc))
        else:
            for name, thunk in tasks:
                try:
                    responses[name] = thunk()
                except Exception as exc:  # noqa: BLE001 - reported to caller
                    errors.append((name, exc))
        return responses, errors

    # ------------------------------------------------------------------ #
    def _bm25_query(
        self, query: str, es_filters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return build_lexical_query(
            query,
            es_filters,
            minimum_should_match=self.config.lexical_minimum_should_match,
            phrase_boost=self.config.lexical_phrase_boost,
        )

    @staticmethod
    def _collect(
        resp: Dict[str, Any],
        source_by_id: Dict[str, Dict[str, Any]],
        highlights_by_id: Dict[str, List[str]],
    ) -> List[str]:
        ids: List[str] = []
        for hit in resp["hits"]["hits"]:
            doc_id = hit["_id"]
            ids.append(doc_id)
            if doc_id not in source_by_id:
                source_by_id[doc_id] = hit.get("_source", {})
            hl = hit.get("highlight") or {}
            frags = hl.get("search_text", []) + hl.get("abstract", [])
            if frags and doc_id not in highlights_by_id:
                highlights_by_id[doc_id] = frags
        return ids

    def _facets(
        self, client, query: str, es_filters: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
        if query:
            # Aggregate over exactly what BM25 can return. Running a looser
            # match here would count documents the result set excludes — with
            # a coverage floor in play, a source whose only "match" carries one
            # term of a six-term query would show a bucket that yields nothing
            # when clicked.
            base_query: Dict[str, Any] = self._bm25_query(query, es_filters)
        else:
            base_query = (
                {"bool": {"filter": es_filters}}
                if es_filters
                else {"match_all": {}}
            )

        aggs = {
            name: {"terms": {"field": field_name, "size": 20}}
            for name, field_name in _FACET_FIELDS.items()
        }
        aggs["has_code"] = {"terms": {"field": "has_code", "size": 2}}
        aggs["has_equations"] = {"terms": {"field": "has_equations", "size": 2}}

        resp = client.search(
            index=self.config.index_name,
            query=base_query,
            size=0,
            aggs=aggs,
            track_total_hits=True,
        )
        facets: Dict[str, List[Dict[str, Any]]] = {}
        for name in aggs:
            buckets = resp["aggregations"][name]["buckets"]
            facets[name] = [
                {"value": b["key"], "count": b["doc_count"]} for b in buckets
            ]
        total = resp["hits"]["total"]["value"]
        return facets, total

    # ------------------------------------------------------------------ #
    def related(self, doc_id: str, size: int = 8) -> List[SearchHit]:
        """Related-document recommendations via vector similarity (more-like)."""
        client = get_client(self.config)
        from elasticsearch import NotFoundError

        try:
            base = client.get(index=self.config.index_name, id=doc_id)
        except NotFoundError:
            return []
        vector = base["_source"].get("embedding")
        if not vector:
            # Fall back to lexical more-like-this on the title/abstract.
            resp = client.search(
                index=self.config.index_name,
                query={
                    "more_like_this": {
                        "fields": ["search_text", "title", "abstract"],
                        "like": [
                            {"_index": self.config.index_name, "_id": doc_id}
                        ],
                        "min_term_freq": 1,
                        "max_query_terms": 25,
                    }
                },
                size=size + 1,
                _source_excludes=["embedding"],
            )
        else:
            resp = client.search(
                index=self.config.index_name,
                knn={
                    "field": "embedding",
                    "query_vector": vector,
                    "k": size + 1,
                    "num_candidates": (size + 1) * 10,
                },
                size=size + 1,
                _source_excludes=["embedding"],
            )
        hits = []
        for hit in resp["hits"]["hits"]:
            if hit["_id"] == doc_id:
                continue
            hits.append(
                SearchHit(
                    document=Document.from_source(
                        {"_id": hit["_id"], **hit["_source"]}
                    ),
                    score=hit.get("_score", 0.0),
                )
            )
        return hits[:size]
