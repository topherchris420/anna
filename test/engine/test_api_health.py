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
        lambda config: SimpleNamespace(status=lambda: (True, 12, True)),
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
        lambda config: SimpleNamespace(status=lambda: (True, 12, False)),
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
        # The class name only: driver messages name hosts and users.
        backend_status="unavailable: RuntimeError",
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
        backend_status="unavailable: RuntimeError",
    )


def test_postgres_vector_probe_failure_normalizes_unavailable_contract(monkeypatch):
    config = _base_backend(monkeypatch, "postgres")

    def unavailable():
        raise RuntimeError("vector probe failed")

    monkeypatch.setattr(
        "engine.pg.store.get_store",
        lambda config: SimpleNamespace(status=unavailable),
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
        backend_status="unavailable: RuntimeError",
    )


def _hashing_embedder(monkeypatch, known):
    import engine.embeddings

    class _Embedder:
        semantic_if_known = known

        @property
        def semantic(self):
            raise AssertionError("/health must not load a model")

    monkeypatch.setattr(engine.embeddings, "get_embedder", _Embedder)


def test_hashing_vectors_are_not_reported_as_semantic(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")
    _hashing_embedder(monkeypatch, False)
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["vector_search"] is True
    assert body["embedding"] == "hashing"
    assert body["semantic_search"] is False


def test_model_vectors_are_reported_as_semantic(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")
    _hashing_embedder(monkeypatch, True)
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["embedding"] == "sentence-transformer"
    assert body["semantic_search"] is True


def test_health_never_echoes_driver_messages(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")

    def unavailable(config):
        raise RuntimeError(
            'connection to server at "ep-secret.neon.tech", port 5432 '
            'failed: password authentication failed for user "owner"'
        )

    monkeypatch.setattr(views.backend, "index_exists", unavailable)
    _, client = _client()
    text = client.get("/api/v1/health").get_data(as_text=True)
    assert "neon.tech" not in text and "owner" not in text


def test_a_model_not_yet_loaded_is_reported_without_loading_it(monkeypatch):
    _base_backend(monkeypatch, "elasticsearch")
    _hashing_embedder(monkeypatch, None)
    _, client = _client()
    body = client.get("/api/v1/health").get_json()
    assert body["embedding"] == "not-loaded"
    assert body["semantic_search"] is None
