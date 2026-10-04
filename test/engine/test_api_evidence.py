"""POST /api/v1/evidence/verify re-checks a saved research record against the
current index. Skipped without Flask; the verification logic itself is
covered Flask-free in test_records.py."""

import pytest

pytest.importorskip("flask")

from flask import Flask  # noqa: E402

from allthethings.engine_api import views  # noqa: E402
from allthethings.engine_api.views import engine_api  # noqa: E402
from engine import records  # noqa: E402
from engine.config import EngineConfig  # noqa: E402
from engine.documents import Document  # noqa: E402

DOC = Document(
    id="espressif:1020b2afe9f87462",
    source="espressif",
    kind="documentation",
    title="ESP32 DMA and Circular Buffers",
    abstract="The ESP32 DMA engine supports circular buffers.",
    url="https://docs.espressif.com/",
)


def _app():
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app


def _record():
    excerpt = {
        "document_id": DOC.id,
        "field": "abstract",
        "offset_unit": "unicode-code-points",
        "start": 0,
        "end": len(DOC.abstract),
        "quote": DOC.abstract,
        "matched_terms": ["dma"],
    }
    return {
        "schema": records.RECORD_SCHEMA,
        "provider": "live",
        "request": {
            "q": "DMA",
            "mode": "hybrid",
            "page": 1,
            "per_page": 20,
            "filters": {},
        },
        "retrieval": {"executed": ["fts"]},
        "result_count": 1,
        "scope": "current-page",
        "hits": [],
        "summary": {
            "query": "DMA",
            "answer": DOC.abstract + " [1]",
            "grounding": "source-extract",
            "citations": [
                {
                    "n": 1,
                    "id": DOC.id,
                    "title": DOC.title,
                    "url": DOC.url,
                    "source": DOC.source,
                    "excerpts": [excerpt],
                }
            ],
        },
    }


def _packet(record=None):
    record = record or _record()
    return {
        "captured_at": "2026-10-04T00:00:00.000Z",
        "content_sha256": records.fingerprint(record),
        "record": record,
    }


@pytest.fixture
def live(monkeypatch):
    """A reachable index holding DOC, on a Postgres backend."""
    monkeypatch.setattr(
        views,
        "get_config",
        lambda: EngineConfig(backend="postgres", index_name="engineering_docs"),
    )
    monkeypatch.setattr(
        views.backend,
        "get_document",
        lambda doc_id: DOC if doc_id == DOC.id else None,
    )


class TestValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            "record",
            {"unrelated": 1},
            {"record": {"schema": "x"}, "content_sha256": "nope"},
        ],
    )
    def test_malformed_payloads_are_400_without_lookup(
        self, payload, monkeypatch
    ):
        def forbidden(doc_id):
            raise AssertionError("validation must precede retrieval")

        monkeypatch.setattr(views.backend, "get_document", forbidden)
        app = _app()
        response = app.test_client().post(
            "/api/v1/evidence/verify", json=payload
        )
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_oversized_bodies_are_413(self, monkeypatch):
        monkeypatch.setattr(views, "MAX_VERIFY_BYTES", 16)
        app = _app()
        response = app.test_client().post(
            "/api/v1/evidence/verify", json=_packet()
        )
        assert response.status_code == 413

    def test_excerpt_budget_is_enforced(self, monkeypatch, live):
        monkeypatch.setattr(views, "MAX_VERIFY_EXCERPTS", 0)
        app = _app()
        response = app.test_client().post(
            "/api/v1/evidence/verify", json=_packet()
        )
        assert response.status_code == 400
        assert "excerpts" in response.get_json()["error"]


class TestVerification:
    def test_intact_record_verifies_against_the_live_index(self, live):
        app = _app()
        response = app.test_client().post(
            "/api/v1/evidence/verify",
            json=_packet(),
            headers={"Origin": "https://x.vercel.app"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["fingerprint"]["matches"] is True
        assert body["checked_against"] == {
            "backend": "postgres",
            "index": "engineering_docs",
        }
        assert [e["status"] for e in body["excerpts"]] == ["verified"]
        assert body["counts"]["verified"] == 1
        assert body["ok"] is True
        # The static workbench calls this cross-origin like every other route.
        assert response.headers.get("Access-Control-Allow-Origin") == "*"

    def test_tampered_record_is_reported_not_rejected(self, live):
        packet = _packet()
        packet["record"]["summary"][
            "answer"
        ] = "A claim the sources never made [1]"
        app = _app()
        body = (
            app.test_client()
            .post("/api/v1/evidence/verify", json=packet)
            .get_json()
        )
        assert body["fingerprint"]["matches"] is False
        assert (
            body["excerpts"][0]["status"] == "verified"
        )  # the excerpt itself still holds
        assert body["ok"] is False

    def test_changed_source_text_is_drift(self, live, monkeypatch):
        changed = Document(
            id=DOC.id,
            source=DOC.source,
            kind=DOC.kind,
            title=DOC.title,
            abstract="The ESP32 DMA engine supports ring buffers.",
        )
        monkeypatch.setattr(
            views.backend, "get_document", lambda doc_id: changed
        )
        app = _app()
        body = (
            app.test_client()
            .post("/api/v1/evidence/verify", json=_packet())
            .get_json()
        )
        assert body["excerpts"][0]["status"] == "drifted"
        assert body["counts"]["drifted"] == 1
        assert body["ok"] is False

    def test_bare_record_has_no_declared_fingerprint(self, live):
        app = _app()
        body = (
            app.test_client()
            .post("/api/v1/evidence/verify", json=_record())
            .get_json()
        )
        assert body["fingerprint"]["declared"] is None
        assert body["fingerprint"]["matches"] is None
        assert body["excerpts"][0]["status"] == "verified"

    def test_unreachable_index_is_503(self, live, monkeypatch):
        def broken(doc_id):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(views.backend, "get_document", broken)
        app = _app()
        response = app.test_client().post(
            "/api/v1/evidence/verify", json=_packet()
        )
        assert response.status_code == 503
        error = response.get_json()["error"]
        assert "unavailable" in error
        # The driver's message stays in the server log.
        assert "connection refused" not in error
