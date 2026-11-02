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
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document
from engine.embeddings import Embedder, get_embedder
from engine.index import get_client
from engine.lexical import (
    QueryTerm,
    bm25_idf,
    parse_query,
    required_matches,
    split_terms,
)
from engine.retrieval import (
    explain_rankings,
    retrieval_report,
    vector_fusion_skipped,
)


class SearchBackendError(RuntimeError):
    """Raised when Elasticsearch is unavailable for every retriever.

    The Flask layer maps this to an HTTP 503 so clients can degrade gracefully
    instead of seeing a raw stack trace.
    """


# --------------------------------------------------------------------------- #
# Lexical query construction (pure, dependency-free)
# --------------------------------------------------------------------------- #
LEXICAL_FIELDS = (
    "title^3",
    "abstract^2",
    "search_text",
    "authors^2",
    "equations^2",
)
PHRASE_FIELDS = ("title^3", "abstract^2", "search_text")


def term_clause(term: QueryTerm, name: Optional[str] = None) -> Dict[str, Any]:
    """One query term, matched across the lexical fields.

    ``type: phrase`` makes a term the analyzer splits ("RISC-V", a quoted
    phrase) match only in one piece; a one-token term is an ordinary term
    query. The best field counts (title over abstract over body), and
    ``name`` lets a response say which terms each hit carried.
    """
    clause: Dict[str, Any] = {
        "multi_match": {
            "query": term.text,
            "fields": list(LEXICAL_FIELDS),
            "type": "phrase",
        }
    }
    if name:
        clause["multi_match"]["_name"] = name
    return clause


def build_term_rule(
    query: str, *, minimum_should_match: str = "70%"
) -> Optional[Dict[str, Any]]:
    """Which documents match ``query`` lexically — the rule, unscored.

    One named ``should`` clause per query term (``t0``, ``t1`` …, see
    :mod:`engine.lexical`) of which the document must carry
    ``required_matches(minimum_should_match, n)``, and a ``must_not`` for
    excluded terms. Postgres counts the same terms against the same number,
    so both backends accept the same documents. ``None`` when the query has
    no terms (only stop words or punctuation): nothing can match it.
    """
    included, excluded = split_terms(parse_query(query))
    if not included:
        return None
    rule: Dict[str, Any] = {
        "bool": {
            "should": [
                term_clause(term, f"t{i}") for i, term in enumerate(included)
            ],
            "minimum_should_match": required_matches(
                minimum_should_match, len(included)
            ),
        }
    }
    if excluded:
        rule["bool"]["must_not"] = [term_clause(term) for term in excluded]
    return rule


def term_weights(
    words: Sequence[str], config: Optional[EngineConfig] = None
) -> Dict[str, float]:
    """BM25 IDF of each word over the index, in one ``_msearch``.

    Each word's document frequency is a count of documents whose
    ``search_text`` contains it (as a phrase, so a word the analyzer splits
    stays whole). A word the analyzer drops entirely counts as present in
    every document (``zero_terms_query: all``), so a stop word weighs nothing
    rather than looking unique.
    """
    config = config or get_config()
    client = get_client(config)
    searches: List[Dict[str, Any]] = []
    for word in words:
        searches.append({"index": config.index_name})
        searches.append(
            {
                "size": 0,
                "track_total_hits": True,
                "query": {
                    "match_phrase": {
                        "search_text": {
                            "query": word,
                            "zero_terms_query": "all",
                        }
                    }
                },
            }
        )
    responses = client.msearch(searches=searches)["responses"]
    corpus = int(client.count(index=config.index_name)["count"])
    return {
        word: bm25_idf(int(r["hits"]["total"]["value"]), corpus)
        for word, r in zip(words, responses)
        if "error" not in r
    }


def build_lexical_query(
    query: str,
    es_filters: Sequence[Dict[str, Any]],
    *,
    minimum_should_match: str = "70%",
    phrase_boost: float = 2.0,
    phrase_slop: int = 2,
) -> Dict[str, Any]:
    """Build the BM25 ``bool`` query for ``query``.

    Two properties matter for precision on technical corpora, and neither
    comes from a plain ``multi_match``:

    - **Coverage.** Requiring every term finds nothing for most questions;
      requiring any returns a document that carries one word of a six-word
      question. The term rule (:func:`build_term_rule`) sits between, and
      each matched term adds its own BM25 score — rare terms weigh most.
    - **Proximity.** "circular buffer dma" should rank a document that says
      exactly that above one that mentions the three words in three unrelated
      paragraphs. A ``phrase`` clause in ``should`` adds that as a bonus, never
      as a requirement, so recall is unchanged.

    Pure: takes and returns plain dicts, so ranking behaviour is unit-tested
    without an Elasticsearch server.
    """
    rule = build_term_rule(query, minimum_should_match=minimum_should_match)
    if rule is None:
        return {"bool": {"must": {"match_none": {}}, "filter": list(es_filters)}}
    clause: Dict[str, Any] = {
        "bool": {"must": rule, "filter": list(es_filters)}
    }
    included, _ = split_terms(parse_query(query))
    # A single-term query has no phrase to match — the clause would score
    # every hit identically and only cost a pass over the postings. A boost of
    # 1.0 means the bonus is switched off: an unweighted `should` clause still
    # adds to the score, so the clause has to go, not just its weight.
    if phrase_boost != 1.0 and len(included) > 1:
        clause["bool"]["should"] = [
            {
                "multi_match": {
                    "query": " ".join(term.text for term in included),
                    "fields": list(PHRASE_FIELDS),
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
    explanation: Dict[str, Any] = field(default_factory=dict)


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
    retrieval: Dict[str, Any] = field(default_factory=dict)


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
        named_rankings: Dict[str, List[str]] = {}

        run_bm25 = mode in ("hybrid", "bm25") and bool(query)
        # Hybrid fuses lexical matching with *semantic* vectors; without a
        # semantic model there is nothing semantic to fuse (see
        # vector_fusion_skipped). Asked for by name, term vectors still run.
        skipped = vector_fusion_skipped(mode, query, True, self.embedder)
        run_knn = mode in ("hybrid", "semantic") and bool(query) and not skipped
        # An empty query browses the corpus (filter-only) via a match_all list.
        run_browse = not query
        # The lexical rule: which documents the query's terms admit. It gates
        # hashing vectors and says, per hit, which terms it carries.
        terms = parse_query(query) if query else []
        included, excluded = split_terms(terms)
        rule = (
            build_term_rule(
                query,
                minimum_should_match=self.config.lexical_minimum_should_match,
            )
            if query
            else None
        )

        took = 0

        # Build the retrieval tasks. In hybrid mode BM25 and kNN are two
        # independent Elasticsearch requests, so we fire them concurrently and
        # fuse afterwards — the kNN round-trip no longer waits on BM25.
        tasks: List[Tuple[str, Callable[[], Dict[str, Any]]]] = []
        if run_bm25:
            tasks.append(
                (
                    "bm25",
                    partial(
                        self._bm25_search, client, query, es_filters, bm25_n
                    ),
                )
            )
        if run_knn:
            # Encoding belongs to the retriever task: a model failure must
            # not prevent a healthy lexical result from being returned.
            def semantic_search():
                vector = self.embedder.encode(query)
                # Hashing vectors are not semantic (Embedder.semantic): they
                # may re-rank documents the lexical rule admits, never surface
                # one through a bucket collision or a lone shared word.
                gated = not self.embedder.using_model
                if gated and rule is None:
                    return {"hits": {"hits": []}, "took": 0}
                return self._knn_search(
                    client,
                    vector,
                    es_filters,
                    knn_n,
                    query=query,
                    gate=rule if gated else None,
                )

            tasks.append(("knn", semantic_search))
        if run_browse:
            tasks.append(
                (
                    "browse",
                    partial(self._browse_search, client, es_filters, want),
                )
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
            ids = self._collect(resp, source_by_id, highlights_by_id)
            rankings.append(ids)
            named_rankings[name] = ids

        # Fuse. Weight lexical and semantic equally in hybrid mode.
        if len(rankings) > 1:
            fused = reciprocal_rank_fusion(rankings, k=self.config.rrf_k)
        elif rankings:
            fused = [
                (doc_id, 1.0 / (i + 1)) for i, doc_id in enumerate(rankings[0])
            ]
        else:
            fused = []

        explanations = explain_rankings(named_rankings, self.config.rrf_k)
        start = (page - 1) * per_page
        window = [
            (doc_id, score)
            for doc_id, score in fused[start : start + per_page]
            if doc_id in source_by_id
        ]
        carried = (
            self._carried_terms(client, rule, [d for d, _ in window])
            if rule is not None and window
            else {}
        )
        labels = [term.label for term in included]
        hits = []
        for doc_id, score in window:
            explanation = explanations.get(doc_id, {})
            if doc_id in carried:
                names = carried[doc_id]
                explanation = {
                    **explanation,
                    "matched_terms": [
                        label
                        for i, label in enumerate(labels)
                        if f"t{i}" in names
                    ],
                    "missing_terms": [
                        label
                        for i, label in enumerate(labels)
                        if f"t{i}" not in names
                    ],
                }
            hits.append(
                SearchHit(
                    document=Document.from_source(
                        {"_id": doc_id, **source_by_id.get(doc_id, {})}
                    ),
                    score=round(score, 6),
                    highlights=highlights_by_id.get(doc_id, []),
                    explanation=explanation,
                )
            )

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

        ceiling = fused_score_ceiling(len(rankings), self.config.rrf_k)
        return SearchResults(
            query=query,
            mode=mode,
            total=total,
            hits=hits,
            facets=facets,
            took_ms=took,
            page=page,
            per_page=per_page,
            score_ceiling=ceiling,
            retrieval=retrieval_report(
                mode,
                named_rankings,
                backend="elasticsearch",
                candidate_count=len(fused),
                score_ceiling=ceiling,
                unavailable=[name for name, _ in errors],
                embedding=(
                    "sentence-transformer"
                    if self.embedder.using_model
                    else "hashing"
                )
                if "knn" in named_rankings or skipped
                else None,
                browse=run_browse and include_facets and bool(facets),
                skipped=skipped,
                lexical={
                    "terms": [term.label for term in included],
                    "excluded": [term.label for term in excluded],
                    "required": required_matches(
                        self.config.lexical_minimum_should_match,
                        len(included),
                    ),
                }
                if query
                else None,
            ),
        )

    def _carried_terms(
        self, client, rule: Dict[str, Any], ids: List[str]
    ) -> Dict[str, set]:
        """Which query terms each page-window document carries.

        One small request — the page's ids, the rule's named term clauses
        as optional ``should`` — whose ``matched_queries`` name the terms.
        It covers every hit on the page, however it was retrieved. Best
        effort: on failure the hits simply go without the term list.
        """
        try:
            resp = client.search(
                index=self.config.index_name,
                query={
                    "bool": {
                        "filter": [{"ids": {"values": list(ids)}}],
                        "should": rule["bool"]["should"],
                    }
                },
                size=len(ids),
                _source=False,
            )
        except Exception:  # noqa: BLE001 - enrichment only
            return {}
        return {
            hit["_id"]: set(hit.get("matched_queries") or [])
            for hit in resp["hits"]["hits"]
        }

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
        gate: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dense-vector kNN cosine search over the 384-dim ``embedding`` field.

        A kNN hit has no lexical query to highlight against, so passing the
        query text as ``highlight_query`` is what gives semantically-retrieved
        documents a snippet showing *why* they are on screen. Without it the
        UI falls back to the first 300 characters of the abstract, which
        rarely contains what the reader searched for.

        ``gate`` (a :func:`build_term_rule` rule) pre-filters candidates to
        the documents the lexical rule admits; it is set for hashing-fallback
        vectors, whose neighbours are otherwise bucket collisions.
        """
        knn = {
            "field": "embedding",
            "query_vector": vector,
            "k": size,
            "num_candidates": max(self.config.knn_num_candidates, size),
        }
        knn_filters = list(es_filters)
        if gate is not None:
            knn_filters.append(gate)
        if knn_filters:
            knn["filter"] = {"bool": {"filter": knn_filters}}
        return client.search(
            index=self.config.index_name,
            knn=knn,
            size=size,
            _source_excludes=["embedding"],
            highlight=self._highlight_spec(query),
        )

    @staticmethod
    def _highlight_spec(query: str = "") -> Dict[str, Any]:
        """Highlighter settings shared by the lexical and semantic retrievers.

        With a query, the included terms are what gets highlighted — never
        an excluded one ("dma -esp32" must not mark "esp32").
        """
        spec: Dict[str, Any] = {
            "fields": {"search_text": {}, "abstract": {}},
            "fragment_size": 160,
            "number_of_fragments": 2,
            # Best-scoring fragment first, not document order: pages show
            # the first one, and it should be the one the query is about.
            "order": "score",
        }
        included, _ = split_terms(parse_query(query))
        if included:
            spec["highlight_query"] = {
                "multi_match": {
                    "query": " ".join(term.text for term in included),
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
        # Hashing-fallback neighbours are collisions, not relatives.
        if not vector or not self.embedder.semantic:
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
