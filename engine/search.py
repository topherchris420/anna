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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document
from engine.embeddings import Embedder, get_embedder
from engine.index import get_client


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

        if run_bm25:
            resp = client.search(
                index=self.config.index_name,
                query=self._bm25_query(query, es_filters),
                size=bm25_n,
                _source_excludes=["embedding"],
                highlight={
                    "fields": {"search_text": {}, "abstract": {}},
                    "fragment_size": 160,
                    "number_of_fragments": 2,
                },
            )
            took += resp.get("took", 0)
            ids = self._collect(resp, source_by_id, highlights_by_id)
            rankings.append(ids)

        if run_knn:
            vector = self.embedder.encode(query)
            knn = {
                "field": "embedding",
                "query_vector": vector,
                "k": knn_n,
                "num_candidates": max(self.config.knn_num_candidates, knn_n),
            }
            if es_filters:
                knn["filter"] = {"bool": {"filter": es_filters}}
            resp = client.search(
                index=self.config.index_name,
                knn=knn,
                size=knn_n,
                _source_excludes=["embedding"],
            )
            took += resp.get("took", 0)
            ids = self._collect(resp, source_by_id, highlights_by_id)
            rankings.append(ids)

        if run_browse:
            resp = client.search(
                index=self.config.index_name,
                query={"bool": {"filter": es_filters}}
                if es_filters
                else {"match_all": {}},
                size=want,
                sort=[{"published": {"order": "desc", "missing": "_last"}}],
                _source_excludes=["embedding"],
            )
            took += resp.get("took", 0)
            ids = self._collect(resp, source_by_id, highlights_by_id)
            rankings.append(ids)

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
            facets, agg_total = self._facets(client, query, es_filters)
            if run_browse:
                total = agg_total

        return SearchResults(
            query=query,
            mode=mode,
            total=total,
            hits=hits,
            facets=facets,
            took_ms=took,
            page=page,
            per_page=per_page,
        )

    # ------------------------------------------------------------------ #
    def _bm25_query(
        self, query: str, es_filters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return {
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
                    }
                },
                "filter": es_filters,
            }
        }

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
            base_query: Dict[str, Any] = {
                "bool": {
                    "must": {
                        "multi_match": {
                            "query": query,
                            "fields": ["search_text", "title", "abstract"],
                        }
                    },
                    "filter": es_filters,
                }
            }
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
