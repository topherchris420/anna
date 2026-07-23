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


def _assert_legacy_contract(
    body, config, *, index_exists, document_count, backend_status
):
    assert body["service"] == "vers3dynamics-engineering-intelligence"
    assert body["engine_version"] == views.engine_version
    assert body["embedding_model"] == config.embedding_model
    assert body["backend"] == config.backend
    assert body["index"] == config.index_name
    assert body["backend_status"] == backend_status
    assert body["index_exists"] is index_exists
    assert body["document_count"] == document_count


def test_elasticsearch_health_reports_ready_hybrid(monkeypatch):
    config = _base_backend(monkeypatch, "elasticsearch")
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is True
    assert body["retrieval"] == "hybrid"
    assert body["vector_search"] is True
    _assert_legacy_contract(
        body,
        config,
        index_exists=True,
        document_count=12,
        backend_status="ok",
    )


def test_postgres_with_vector_reports_ready_hybrid(monkeypatch):
    config = _base_backend(monkeypatch, "postgres")
    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(has_vector=lambda: True),
    )
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is True
    assert body["retrieval"] == "hybrid"
    assert body["vector_search"] is True
    _assert_legacy_contract(
        body,
        config,
        index_exists=True,
        document_count=12,
        backend_status="ok",
    )


def test_postgres_without_vector_reports_fulltext_only(monkeypatch):
    config = _base_backend(monkeypatch, "postgres")
    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(has_vector=lambda: False),
    )
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is True
    assert body["retrieval"] == "fulltext-only"
    assert body["vector_search"] is False
    _assert_legacy_contract(
        body,
        config,
        index_exists=True,
        document_count=12,
        backend_status="ok",
    )


def test_unavailable_backend_is_not_ready(monkeypatch):
    config = _base_backend(monkeypatch, "elasticsearch")

    def unavailable(config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(views.backend, "index_exists", unavailable)
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is False
    assert body["retrieval"] == "unavailable"
    assert body["vector_search"] is False
    _assert_legacy_contract(
        body,
        config,
        index_exists=False,
        document_count=0,
        backend_status="unavailable: connection refused",
    )


def test_count_failure_normalizes_unavailable_contract(monkeypatch):
    config = _base_backend(monkeypatch, "elasticsearch")

    def unavailable(config):
        raise RuntimeError("count failed")

    monkeypatch.setattr(views.backend, "count", unavailable)
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is False
    assert body["retrieval"] == "unavailable"
    assert body["vector_search"] is False
    _assert_legacy_contract(
        body,
        config,
        index_exists=False,
        document_count=0,
        backend_status="unavailable: count failed",
    )


def test_postgres_vector_probe_failure_normalizes_unavailable_contract(monkeypatch):
    config = _base_backend(monkeypatch, "postgres")

    def unavailable():
        raise RuntimeError("vector probe failed")

    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(has_vector=unavailable),
    )
    _, client = _client()
    response = client.get("/api/v1/health")
    body = response.get_json()
    assert response.status_code == 200
    assert body["ready"] is False
    assert body["retrieval"] == "unavailable"
    assert body["vector_search"] is False
    _assert_legacy_contract(
        body,
        config,
        index_exists=False,
        document_count=0,
        backend_status="unavailable: vector probe failed",
    )
