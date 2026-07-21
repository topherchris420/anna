"""Route tests for /api/v1/agent/search. Skipped without Flask.

The contract logic itself (parsing, filter resolution, serialization) is
covered flask-free in test_api_agent_search.py; these tests pin the HTTP
behavior: status codes, error shapes, engine call parameters, CORS.
"""

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api import views  # noqa: E402
from allthethings.engine_api.views import engine_api  # noqa: E402
from engine.documents import Document  # noqa: E402
from engine.search import (  # noqa: E402
    SearchHit,
    SearchResults,
    fused_score_ceiling,
)


def _app():
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app


def _canned_results(query):
    ceiling = fused_score_ceiling(2, 60)
    doc = Document(
        id="arxiv:8c4f01f9a3b2d715",
        source="arxiv",
        kind="paper",
        title="Kalman Filter Divergence Analysis",
        abstract="A study of divergence in extended Kalman filters.",
        url="https://arxiv.org/abs/2401.00001",
        authors=["A. Author"],
        published="2024-01-15",
    )
    return SearchResults(
        query=query,
        mode="hybrid",
        total=1,
        hits=[SearchHit(document=doc, score=ceiling * 0.92)],
        facets={},
        score_ceiling=ceiling,
    )


class _StubService:
    """Search-service double recording calls, returning one canned hit."""

    def __init__(self):
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return _canned_results(query)


@pytest.fixture
def stub_service(monkeypatch):
    service = _StubService()
    monkeypatch.setattr(views, "_search_service", service)
    return service


@pytest.fixture
def client():
    # Keep the app referenced for the whole test: Flask's response.get_json()
    # resolves the app through a weakref.
    return _app().test_client()


class TestAgentSearchEndpoint:
    def test_success_response(self, client, stub_service):
        resp = client.post(
            "/api/v1/agent/search",
            json={"query": "kalman", "domain_filter": "arxiv", "limit": 3},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        assert body["total_results"] == 1 == len(body["results"])
        assert body["results"][0]["relevance_score"] == 0.92
        # The route must translate the agent contract into engine parameters.
        _, kwargs = stub_service.calls[0]
        assert kwargs["per_page"] == 3
        assert kwargs["mode"] == "hybrid"
        assert kwargs["include_facets"] is False
        assert kwargs["filters"].sources == ["arxiv"]

    def test_min_score_filters_results(self, client, stub_service):
        resp = client.post(
            "/api/v1/agent/search", json={"query": "kalman", "min_score": 0.95}
        )
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["total_results"] == 0 and body["results"] == []

    def test_missing_query_is_400(self, client, stub_service):
        resp = client.post("/api/v1/agent/search", json={})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["status"] == "error" and body["results"] == []
        assert stub_service.calls == []  # rejected before touching the engine

    def test_non_json_body_is_400(self, client, stub_service):
        resp = client.post(
            "/api/v1/agent/search", data="query", content_type="text/plain"
        )
        assert resp.status_code == 400
        assert resp.get_json()["status"] == "error"

    def test_backend_failure_is_503_with_error_shape(self, client, monkeypatch):
        class _Down:
            def search(self, *args, **kwargs):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(views, "_search_service", _Down())
        resp = client.post("/api/v1/agent/search", json={"query": "kalman"})
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "error" and body["total_results"] == 0

    def test_openapi_spec_is_served(self, client):
        resp = client.get("/api/v1/agent/openapi.json")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["openapi"].startswith("3.1")
        assert "/api/v1/agent/search" in body["paths"]

    def test_preflight_options_allowed(self, client):
        resp = client.options(
            "/api/v1/agent/search",
            headers={"Origin": "https://x.vercel.app"},
        )
        assert resp.status_code in (200, 204)
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
