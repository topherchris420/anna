"""Truthful /api/v1/health retrieval capability reporting."""

from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api import views  # noqa: E402
from allthethings.engine_api.views import engine_api  # noqa: E402
from engine.config import EngineConfig  # noqa: E402


def _client():
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app, app.test_client()


def _base_backend(monkeypatch, backend_name):
    config = EngineConfig(backend=backend_name)
    monkeypatch.setattr(views, "get_config", lambda: config)
    monkeypatch.setattr(views.backend, "index_exists", lambda config: True)
    monkeypatch.setattr(views.backend, "count", lambda config: 12)
    return config


def test_elasticsearch_health_reports_ready_hybrid(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["ready"] is True
    assert body["retrieval"] == "hybrid"
    assert body["vector_search"] is True
    assert body["document_count"] == 12


def test_postgres_without_vector_reports_fulltext_only(monkeypatch):
    _base_backend(monkeypatch, "postgres")
    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(has_vector=lambda: False),
    )
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["ready"] is True
    assert body["retrieval"] == "fulltext-only"
    assert body["vector_search"] is False


def test_unavailable_backend_is_not_ready(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")

    def unavailable(config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(views.backend, "index_exists", unavailable)
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["ready"] is False
    assert body["retrieval"] == "unavailable"
    assert body["vector_search"] is False
    assert body["index_exists"] is False
