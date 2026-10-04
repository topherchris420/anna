"""The /api/v1 collections and document routes the workbench depends on.

Runs against a real (in-memory SQLite) CollectionStore. Skipped without Flask
or SQLAlchemy.
"""

import pytest

pytest.importorskip("flask")
pytest.importorskip("sqlalchemy")

from flask import Flask  # noqa: E402

from allthethings.engine_api import views  # noqa: E402
from allthethings.engine_api.views import engine_api  # noqa: E402
from engine.collections import CollectionStore  # noqa: E402
from engine.documents import Document  # noqa: E402

WORKSPACE = "ws_" + "a" * 32
OTHER = "ws_" + "b" * 32


@pytest.fixture()
def client(monkeypatch):
    store = CollectionStore(database_url="sqlite:///:memory:")
    store.init_db()
    monkeypatch.setattr(views, "_store", lambda: store)
    app = Flask(__name__)
    app.register_blueprint(engine_api)
    return app.test_client()


def _create(client, name="RTOS refs", owner=WORKSPACE):
    response = client.post(
        "/api/v1/collections", json={"owner": owner, "name": name}
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


class TestCollections:
    def test_create_save_list_and_remove(self, client):
        coll = _create(client)
        saved = client.post(
            f"/api/v1/collections/{coll['id']}/bookmarks",
            json={
                "owner": WORKSPACE,
                "document_id": "arxiv:1",
                "title": "Kalman filters",
                "url": "https://arxiv.org/abs/1",
                "source": "arxiv",
            },
        )
        assert saved.status_code == 201
        listed = client.get(
            f"/api/v1/collections?owner={WORKSPACE}&with_bookmarks=1"
        ).get_json()
        assert listed["owner"] == WORKSPACE
        [only] = listed["collections"]
        assert only["bookmark_count"] == 1
        assert only["bookmarks"][0]["document_id"] == "arxiv:1"

        removed = client.delete(
            f"/api/v1/collections/{coll['id']}/bookmarks/arxiv:1"
            f"?owner={WORKSPACE}"
        )
        assert removed.status_code == 200
        detail = client.get(
            f"/api/v1/collections/{coll['id']}?owner={WORKSPACE}"
        ).get_json()
        assert detail["bookmarks"] == []

    def test_listing_omits_bookmarks_unless_asked(self, client):
        _create(client)
        [only] = client.get(
            f"/api/v1/collections?owner={WORKSPACE}"
        ).get_json()["collections"]
        assert "bookmarks" not in only

    def test_workspaces_are_isolated(self, client):
        coll = _create(client)
        assert (
            client.get(f"/api/v1/collections?owner={OTHER}").get_json()[
                "collections"
            ]
            == []
        )
        assert (
            client.get(
                f"/api/v1/collections/{coll['id']}?owner={OTHER}"
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/collections/{coll['id']}/bookmarks",
                json={"owner": OTHER, "document_id": "arxiv:1"},
            ).status_code
            == 404
        )
        assert (
            client.delete(
                f"/api/v1/collections/{coll['id']}?owner={OTHER}"
            ).status_code
            == 404
        )

    @pytest.mark.parametrize(
        "owner", ["x" * 129, "has space", "<script>", "-leading-dash"]
    )
    def test_malformed_owner_is_rejected(self, client, owner):
        response = client.post(
            "/api/v1/collections", json={"owner": owner, "name": "n"}
        )
        assert response.status_code == 400
        assert "owner" in response.get_json()["error"]

    def test_omitted_owner_is_the_shared_anonymous_workspace(self, client):
        _create(client, owner=None)
        listed = client.get("/api/v1/collections").get_json()
        assert listed["owner"] == "anonymous"
        assert len(listed["collections"]) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": ""},
            {"name": "   "},
            {"name": 42},
            {"name": "x" * 201},
            {"name": "ok", "description": "x" * 2001},
            {"name": "ok", "description": ["not", "text"]},
        ],
    )
    def test_invalid_collection_fields_are_400(self, client, payload):
        payload["owner"] = WORKSPACE
        assert (
            client.post("/api/v1/collections", json=payload).status_code
            == 400
        )

    def test_non_object_body_is_400_not_500(self, client):
        response = client.post("/api/v1/collections", json=["name"])
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"document_id": ""},
            {"document_id": "x" * 256},
            {"document_id": "arxiv:1", "note": "x" * 2001},
        ],
    )
    def test_invalid_bookmarks_are_400(self, client, payload):
        coll = _create(client)
        payload["owner"] = WORKSPACE
        response = client.post(
            f"/api/v1/collections/{coll['id']}/bookmarks", json=payload
        )
        assert response.status_code == 400

    def test_only_http_links_are_stored(self, client):
        # A bookmark URL is rendered as a link; javascript: must not survive.
        coll = _create(client)
        bookmark = client.post(
            f"/api/v1/collections/{coll['id']}/bookmarks",
            json={
                "owner": WORKSPACE,
                "document_id": "arxiv:1",
                "url": "javascript:alert(1)",
                "title": "t" * 5000,
            },
        ).get_json()
        assert bookmark["url"] == ""
        assert len(bookmark["title"]) == 1024

    def test_database_outage_is_a_json_503_without_driver_text(
        self, client, monkeypatch
    ):
        class _Down:
            def list_collections(self, *a, **k):
                raise RuntimeError('could not connect to "db.internal"')

        monkeypatch.setattr(views, "_store", lambda: _Down())
        response = client.get(f"/api/v1/collections?owner={WORKSPACE}")
        assert response.status_code == 503
        body = response.get_json()
        assert body["collections"] == []
        assert "unavailable" in body["error"]
        assert "db.internal" not in body["error"]


class TestDocumentRoutes:
    DOC = Document(
        id="arxiv:1", source="arxiv", kind="paper", title="Kalman filters"
    )

    def test_document_outage_is_503_not_500(self, client, monkeypatch):
        def down(doc_id):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(views.backend, "get_document", down)
        assert client.get("/api/v1/document/arxiv:1").status_code == 503

    def test_overlong_document_id_is_400(self, client):
        assert client.get("/api/v1/document/" + "x" * 600).status_code == 400

    def test_compare_fetches_both_documents_in_one_call(
        self, client, monkeypatch
    ):
        other = Document(
            id="arxiv:2", source="arxiv", kind="paper", title="Kalman drift"
        )
        calls = []

        def get_documents(ids):
            calls.append(list(ids))
            return {d.id: d for d in (self.DOC, other)}

        monkeypatch.setattr(views.backend, "get_documents", get_documents)
        body = client.post(
            "/api/v1/compare", json={"a": "arxiv:1", "b": "arxiv:2"}
        ).get_json()
        assert calls == [["arxiv:1", "arxiv:2"]]
        assert body["shared_terms"] == ["kalman"]

    def test_compare_rejects_the_same_document_twice(self, client):
        response = client.post(
            "/api/v1/compare", json={"a": "arxiv:1", "b": "arxiv:1"}
        )
        assert response.status_code == 400

    def test_compare_reports_missing_documents(self, client, monkeypatch):
        monkeypatch.setattr(
            views.backend, "get_documents", lambda ids: {"arxiv:1": self.DOC}
        )
        response = client.post(
            "/api/v1/compare", json={"a": "arxiv:1", "b": "arxiv:9"}
        )
        assert response.status_code == 404
        assert response.get_json()["missing"] == ["arxiv:9"]

    def test_related_outage_is_503(self, client, monkeypatch):
        class _Down:
            def related(self, *a, **k):
                raise RuntimeError("connection refused")

        monkeypatch.setattr(views, "_search_service", _Down())
        response = client.get("/api/v1/document/arxiv:1/related")
        assert response.status_code == 503
        assert response.get_json()["related"] == []
