"""CORS behavior on the REST API. Skipped when Flask is not installed."""

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api.views import engine_api  # noqa: E402
from engine.config import reset_config_cache  # noqa: E402


def _client(monkeypatch, origins=None):
    if origins is None:
        monkeypatch.delenv("ENGINE_CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("ENGINE_CORS_ORIGINS", origins)
    reset_config_cache()
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app.test_client()


class TestCors:
    def test_wildcard_by_default(self, monkeypatch):
        # /sources needs no Elasticsearch, so it is safe to hit in a unit test.
        resp = _client(monkeypatch).get(
            "/api/v1/sources", headers={"Origin": "https://foo.vercel.app"}
        )
        assert resp.status_code == 200
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

    def test_allowlist_echoes_known_origin(self, monkeypatch):
        client = _client(monkeypatch, origins="https://app.vercel.app")
        resp = client.get(
            "/api/v1/sources",
            headers={"Origin": "https://app.vercel.app"},
        )
        assert resp.headers.get("Access-Control-Allow-Origin") == "https://app.vercel.app"

    def test_allowlist_blocks_unknown_origin(self, monkeypatch):
        client = _client(monkeypatch, origins="https://app.vercel.app")
        resp = client.get(
            "/api/v1/sources", headers={"Origin": "https://evil.example"}
        )
        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_preflight_options(self, monkeypatch):
        resp = _client(monkeypatch).options(
            "/api/v1/search", headers={"Origin": "https://foo.vercel.app"}
        )
        assert resp.status_code in (200, 204)
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")

    def teardown_method(self):
        reset_config_cache()
