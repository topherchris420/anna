"""GET/POST parsing for the /api/v1/search endpoint. Skipped without Flask."""

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api.views import (  # noqa: E402
    engine_api,
    _parse_search_request,
)


def _app():
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app


class TestSearchRequestParsing:
    def test_post_json_body(self):
        with _app().test_request_context(
            "/api/v1/search",
            method="POST",
            json={
                "query": "kalman filter",
                "source": ["arxiv"],
                "has_code": True,
                "mode": "semantic",
                "page": 2,
                "per_page": 10,
            },
        ):
            query, mode, page, per_page, filters = _parse_search_request()
        assert query == "kalman filter"
        assert mode == "semantic"
        assert page == 2 and per_page == 10
        assert filters.sources == ["arxiv"]
        assert filters.has_code is True

    def test_post_accepts_q_alias_and_plural_keys(self):
        with _app().test_request_context(
            "/api/v1/search",
            method="POST",
            json={"q": "dma", "sources": ["stm32", "espressif"]},
        ):
            query, _, _, _, filters = _parse_search_request()
        assert query == "dma"
        assert filters.sources == ["stm32", "espressif"]

    def test_paging_is_bounded(self):
        # page * per_page is every retriever's candidate LIMIT.
        with _app().test_request_context(
            "/api/v1/search?q=dma&page=1000000&per_page=100000"
        ):
            _, _, page, per_page, _ = _parse_search_request()
        assert (page, per_page) == (50, 100)

    def test_impossible_years_are_ignored_not_a_503(self):
        with _app().test_request_context(
            "/api/v1/search?q=dma&year_from=0&year_to=99999"
        ):
            _, _, _, _, filters = _parse_search_request()
        assert filters.year_from is None and filters.year_to is None

    def test_overlong_query_is_400(self, monkeypatch):
        from allthethings.engine_api import views

        def forbidden():
            raise AssertionError("validation must precede retrieval")

        monkeypatch.setattr(views, "_service", forbidden)
        response = (
            _app().test_client().get("/api/v1/search?q=" + "x" * 2001)
        )
        assert response.status_code == 400

    def test_get_query_string(self):
        with _app().test_request_context(
            "/api/v1/search?q=risc-v&source=riscv&mode=bm25"
        ):
            query, mode, _, _, filters = _parse_search_request()
        assert query == "risc-v"
        assert mode == "bm25"
        assert filters.sources == ["riscv"]

    def test_invalid_mode_falls_back_to_hybrid(self):
        with _app().test_request_context(
            "/api/v1/search",
            method="POST",
            json={"query": "x", "mode": "bogus"},
        ):
            _, mode, _, _, _ = _parse_search_request()
        assert mode == "hybrid"


class TestSearchEndpoint:
    def test_post_is_routable(self):
        # Elasticsearch is unavailable in unit tests, so the endpoint returns
        # 503 — but crucially it must accept POST (not 405) and echo the query.
        client = _app().test_client()
        resp = client.post("/api/v1/search", json={"query": "abc"})
        assert resp.status_code != 405
        assert resp.get_json()["query"] == "abc"

    def test_preflight_options_allowed(self):
        resp = (
            _app()
            .test_client()
            .options(
                "/api/v1/search", headers={"Origin": "https://x.vercel.app"}
            )
        )
        assert resp.status_code in (200, 204)
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")


class TestSummaryRequestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            [],
            ["q"],
            None,
            {"q": 123},
            {"q": " "},
            {"q": "x" * 2001},
            {"q": "dma", "ids": "doc"},
            {"q": "dma", "ids": [42]},
            {"q": "dma", "ids": ["d"] * 9},
            {"q": "dma", "ids": [""]},
        ],
    )
    def test_invalid_payload_returns_400_without_lookup(
        self, payload, monkeypatch
    ):
        from allthethings.engine_api import views

        def forbidden():
            raise AssertionError("validation must precede retrieval")

        monkeypatch.setattr(views, "_service", forbidden)
        response = _app().test_client().post("/api/v1/summarize", json=payload)
        assert response.status_code == 400

    def test_response_preserves_exact_source_evidence(self, monkeypatch):
        from allthethings.engine_api import views
        from engine.documents import Document
        from engine.summarize import Summarizer
        from engine.config import EngineConfig

        doc = Document(
            id="test:a",
            source="test",
            kind="paper",
            title="DMA",
            abstract="DMA transfers samples into circular buffers.",
        )
        # The top hits are fetched in one round trip, not one per id.
        monkeypatch.setattr(
            views.backend, "get_documents", lambda ids: {doc.id: doc}
        )
        monkeypatch.setattr(
            views, "_summarizer", Summarizer(EngineConfig(llm_enabled=False))
        )
        app = _app()
        response = app.test_client().post(
            "/api/v1/summarize", json={"q": "DMA", "ids": [doc.id]}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["grounding"] == "source-extract"
        assert data["citations"][0]["excerpts"][0]["quote"] == doc.abstract


class TestRetrievalSerialization:
    def test_search_exposes_actual_paths_and_per_hit_explanations(
        self, monkeypatch
    ):
        from allthethings.engine_api import views
        from engine.documents import Document
        from engine.search import SearchHit, SearchResults

        explanation = {
            "method": "reciprocal-rank",
            "ranks": {"fts": 1},
            "contributions": {"fts": 1.0},
        }
        report = {"executed": ["fts"], "degraded": True, "unavailable": ["knn"]}
        doc = Document(id="x", source="test", kind="paper", title="DMA")

        class Service:
            def search(self, query, **kwargs):
                return SearchResults(
                    query,
                    "hybrid",
                    1,
                    [SearchHit(doc, 1.0, explanation=explanation)],
                    {},
                    retrieval=report,
                )

        monkeypatch.setattr(views, "_search_service", Service())
        app = _app()
        data = app.test_client().get("/api/v1/search?q=DMA").get_json()
        assert data["mode"] == "hybrid"
        assert data["retrieval"] == report
        assert data["hits"][0]["explanation"] == explanation

    def test_hits_report_relevance_on_the_agent_endpoint_scale(
        self, monkeypatch
    ):
        from allthethings.engine_api import views
        from allthethings.engine_api.agent_search import normalize_relevance
        from engine.documents import Document
        from engine.search import SearchHit, SearchResults, fused_score_ceiling

        ceiling = fused_score_ceiling(2, 60)
        docs = [
            Document(id=i, source="test", kind="paper", title=i)
            for i in ("a", "b")
        ]

        class Service:
            def search(self, query, **kwargs):
                return SearchResults(
                    query,
                    "hybrid",
                    2,
                    [
                        SearchHit(docs[0], ceiling),
                        SearchHit(docs[1], ceiling / 2),
                    ],
                    {},
                    score_ceiling=ceiling,
                )

        monkeypatch.setattr(views, "_search_service", Service())
        app = _app()  # keep the app alive while the response is decoded
        data = app.test_client().get("/api/v1/search?q=DMA").get_json()
        assert [h["relevance"] for h in data["hits"]] == [1.0, 0.5]
        assert data["hits"][1]["relevance"] == normalize_relevance(
            ceiling / 2, ceiling
        )

    def test_hits_without_a_known_ceiling_do_not_invent_relevance(self):
        from allthethings.engine_api.serialize import hit_to_dict
        from engine.documents import Document
        from engine.search import SearchHit

        hit = SearchHit(
            Document(id="a", source="test", kind="paper", title="A"), 3.2
        )
        assert "relevance" not in hit_to_dict(hit)
        assert hit_to_dict(hit, score_ceiling=4.0)["relevance"] == 0.8
