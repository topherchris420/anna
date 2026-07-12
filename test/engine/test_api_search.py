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
            "/api/v1/search", method="POST", json={"query": "x", "mode": "bogus"}
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
        resp = _app().test_client().options(
            "/api/v1/search", headers={"Origin": "https://x.vercel.app"}
        )
        assert resp.status_code in (200, 204)
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
