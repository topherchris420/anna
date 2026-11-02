"""Ranking quality and lexical matching, measured against real backends.

Everything else in test/engine runs without a database; this module is the
one that executes the actual SQL and query DSL. It loads the judged corpus
(test/relevance) into a scratch index or table, ``anna_relevance_test``, and
holds each backend to:

- **quality floors** on the judged queries — the measured scores, less a
  margin, so a change that brings back the old failure modes (every term
  required, or term-hashing noise on the page) fails CI;
- **exact behaviours** the product promises — a plain-English question finds
  its paper, gibberish finds nothing, quotes and exclusions work, each hit
  names the terms it carries, hybrid on term-hashing vectors runs lexically.

Skipped unless a backend is named (CI's relevance job names both)::

    ANNA_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/anna
    ANNA_TEST_ELASTICSEARCH=http://localhost:9200
"""

import os
import re

import pytest

from engine import evaluation
from engine.config import EngineConfig
from engine.embeddings import Embedder

INDEX = "anna_relevance_test"

BACKENDS = [
    name
    for name, variable in (
        ("postgres", "ANNA_TEST_DATABASE_URL"),
        ("elasticsearch", "ANNA_TEST_ELASTICSEARCH"),
    )
    if os.getenv(variable)
]

# Measured with the shipped defaults (test/relevance/README.md): Postgres
# nDCG@10 0.741 / MRR 0.966, Elasticsearch 0.757 / 0.977, noise 0.67. The
# floors leave room for honest tuning, not for the old behaviour (0.43 with
# every term required; 0.67 with term-hashing noise fused into hybrid).
FLOORS = {
    "postgres": {"ndcg": 0.72, "mrr": 0.94, "p5": 0.58},
    "elasticsearch": {"ndcg": 0.73, "mrr": 0.95, "p5": 0.58},
}
MAX_NOISE = 1.0

KALMAN_PAPER = "arxiv:8bbe0f7de8c6440b"
ESP32_DMA_NOTE = "espressif:1020b2afe9f87462"
SRAM_IN_MEMORY = "arxiv:28d147fb56635f2f"
PROCESSING_IN_MEMORY = "arxiv:218830759f9c273c"


def _config(backend):
    return EngineConfig(
        backend=backend,
        index_name=INDEX,
        database_url=os.getenv(
            "ANNA_TEST_DATABASE_URL", "postgresql://unset/unset"
        ),
        elasticsearch_host=os.getenv(
            "ANNA_TEST_ELASTICSEARCH", "http://unset:9200"
        ),
        embedding_force_fallback=True,
    )


@pytest.fixture(
    scope="module",
    params=BACKENDS
    or [pytest.param(None, marks=pytest.mark.skip(reason="no live backend"))],
)
def service(request):
    config = _config(request.param)
    embedder = Embedder(config)
    judged = evaluation.load_judgments()
    indexed = evaluation.load_corpus(judged.corpus, config, embedder)
    assert indexed >= 300
    if config.backend == "postgres":
        from engine.pg.search import PgSearchService

        svc = PgSearchService(config, embedder=embedder)
    else:
        from engine.search import SearchService

        svc = SearchService(config, embedder=embedder)
    yield svc
    if config.backend == "postgres":
        with svc.store.connect() as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {INDEX}")
    else:
        from engine.index import get_client

        get_client(config).options(ignore_status=[404]).indices.delete(
            index=INDEX
        )


def _ids(svc, query, mode="hybrid", n=10, **kwargs):
    results = svc.search(query, mode=mode, per_page=n, include_facets=False, **kwargs)
    return [hit.document.id for hit in results.hits]


def _lexical_name(svc):
    return "fts" if svc.config.backend == "postgres" else "bm25"


class TestQualityFloors:
    @pytest.mark.parametrize("mode", ["hybrid", "bm25"])
    def test_judged_queries_rank_well(self, service, mode):
        judged = evaluation.load_judgments()
        report = evaluation.evaluate(
            lambda q, m, n: _ids(service, q, m, n), judged, mode=mode
        )
        summary = report["summary"]
        floors = FLOORS[service.config.backend]
        table = evaluation.format_report(report)
        for metric, floor in floors.items():
            assert summary[metric] >= floor, f"{metric} below {floor}\n{table}"
        assert summary["noise"] <= MAX_NOISE, table


class TestRelatedWork:
    def test_related_documents_stay_on_topic(self, service):
        # Measured 0.427 (Postgres) and 0.432 (Elasticsearch); the shared-
        # title-word and default more_like_this rankings managed 0.28 and
        # left papers with nothing related at all.
        judged = evaluation.load_judgments()
        report = evaluation.evaluate_related(
            lambda doc_id, n: [
                hit.document.id for hit in service.related(doc_id, size=n)
            ],
            judged,
        )
        assert report["seeds"] >= 100
        assert report["empty"] == 0, report
        assert report["on_topic"] >= 0.38, report


class TestPromisedBehaviour:
    def test_a_plain_english_question_finds_its_paper(self, service):
        # Four content terms; the one paper about Kalman filtering carries
        # three of them, and "kalman" is the rare one. Requiring every term
        # found nothing here; any-term vectors put eye-tracking first.
        question = "how do I estimate state with a kalman filter"
        assert _ids(service, question)[0] == KALMAN_PAPER

    def test_nonsense_and_stop_words_find_nothing(self, service):
        for query in ("zzqx plorfnog", "the of and"):
            assert _ids(service, query) == [], query
            assert _ids(service, query, mode="semantic") == [], query

    def test_a_quoted_phrase_must_match_as_a_phrase(self, service):
        assert _ids(service, '"circular buffers"') == [ESP32_DMA_NOTE]
        assert _ids(service, '"buffers circular"') == []

    def test_an_excluded_term_removes_its_documents(self, service):
        results = service.search(
            "reinforcement learning -robot", per_page=50, include_facets=False
        )
        assert results.hits
        for hit in results.hits:
            text = f"{hit.document.title} {hit.document.abstract}".lower()
            assert not re.search(r"\brobot", text), hit.document.title

    def test_a_hyphenated_term_matches_every_spelling(self, service):
        # Matching, not ranking: "In-memory" in one title and "Processing-
        # In-Memory" in the other are both matches for the term.
        found = _ids(service, '"in-memory"', mode="bm25", n=100)
        assert SRAM_IN_MEMORY in found and PROCESSING_IN_MEMORY in found

    def test_each_hit_names_the_terms_it_carries(self, service):
        results = service.search(
            "kalman filter state estimation", per_page=10, include_facets=False
        )
        lexical = results.retrieval["lexical"]
        assert lexical["terms"] == ["kalman", "filter", "state", "estimation"]
        assert lexical["required"] == 2
        for hit in results.hits:
            carried = hit.explanation["matched_terms"]
            missing = hit.explanation["missing_terms"]
            assert len(carried) >= 2, hit.document.title
            assert sorted(carried + missing) == sorted(lexical["terms"])
        top = results.hits[0]
        assert top.document.id == KALMAN_PAPER
        assert {"kalman", "filter"} <= set(top.explanation["matched_terms"])

    def test_partial_matches_are_highlighted(self, service):
        results = service.search(
            "kalman filter state estimation", per_page=10, include_facets=False
        )
        assert any("<em>" in " ".join(h.highlights) for h in results.hits)
        # The snippet a page shows first is the one about the rare term.
        assert "<em>kalman</em>" in results.hits[0].highlights[0].lower()

    def test_hybrid_without_a_semantic_model_runs_lexically(self, service):
        results = service.search("model predictive control", include_facets=False)
        report = results.retrieval
        assert report["executed"] == [_lexical_name(service)]
        assert report["skipped"] == ["knn"] and report["degraded"] is False
        assert report["embedding"] == "hashing"

    def test_term_vectors_rerank_only_lexical_matches(self, service):
        lexical = set(
            _ids(service, "model predictive control", mode="bm25", n=100)
        )
        vectors = _ids(service, "model predictive control", mode="semantic", n=100)
        assert vectors and set(vectors) <= lexical

    def test_both_forms_of_the_coverage_rule_accept_the_same_documents(
        self, service, monkeypatch
    ):
        # Postgres writes "r of n terms" as one exact tsquery when it can and
        # counts carried terms otherwise; the two must never disagree.
        if service.config.backend != "postgres":
            pytest.skip("Postgres-only SQL forms")
        import engine.pg.search as pg_search

        queries = (
            "kalman filter state estimation",
            "neural network accelerator memory bandwidth",
            "spiking neural network hardware -memristor",
        )
        with service.store.connect() as conn:
            for query in queries:
                plan = service._plan(conn, query)
                assert 1 < plan.required < len(plan.included), query
                exact = service._fts_ids(conn, plan, "", {}, 1000)
                monkeypatch.setattr(pg_search, "_MAX_COVER_CLAUSES", 0)
                counted = service._fts_ids(conn, plan, "", {}, 1000)
                monkeypatch.undo()
                assert exact and exact == counted, query

    def test_term_weights_follow_rarity_and_stop_words_weigh_nothing(
        self, service
    ):
        from engine import backend

        weights = backend.term_weights(
            ["kalman", "control", "the"], service.config
        )
        assert weights["kalman"] > weights["control"] > 0
        assert weights.get("the", 0.0) < 0.01

    def test_the_source_report_leads_with_the_informative_excerpt(
        self, service
    ):
        from engine import backend
        from engine.evidence import content_terms
        from engine.summarize import Summarizer

        question = "how do I estimate state with a kalman filter"
        docs = [
            hit.document
            for hit in service.search(
                question, per_page=8, include_facets=False
            ).hits
        ]
        weights = backend.term_weights(content_terms(question), service.config)
        summary = Summarizer(service.config).summarize(
            question, docs, weights=weights
        )
        first = summary.citations[0]
        assert first.id == KALMAN_PAPER
        assert "Kalman" in first.excerpts[0]["quote"]

    def test_pages_do_not_repeat_documents(self, service):
        first = _ids(service, "control", n=10)
        second = _ids(service, "control", n=10, page=2)
        assert first and second and not set(first) & set(second)

    def test_facets_count_the_candidates(self, service):
        results = service.search("reinforcement learning")
        sources = {b["value"]: b["count"] for b in results.facets["source"]}
        assert sources.get("arxiv", 0) >= len(results.hits) > 0
