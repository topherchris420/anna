"""Unit tests for the collections/bookmarks store (in-memory SQLite).

Skipped automatically when SQLAlchemy is not installed.
"""

import pytest

pytest.importorskip("sqlalchemy")

from engine.collections import CollectionStore  # noqa: E402


@pytest.fixture()
def store():
    # A fresh in-memory database per test.
    s = CollectionStore(database_url="sqlite:///:memory:")
    s.init_db()
    return s


class TestCollections:
    def test_create_and_list(self, store):
        store.create_collection("alice", "RTOS refs", "notes")
        colls = store.list_collections("alice")
        assert len(colls) == 1
        assert colls[0]["name"] == "RTOS refs"
        assert colls[0]["bookmark_count"] == 0

    def test_owner_isolation(self, store):
        store.create_collection("alice", "A")
        store.create_collection("bob", "B")
        assert len(store.list_collections("alice")) == 1
        assert len(store.list_collections("bob")) == 1

    def test_add_and_list_bookmarks(self, store):
        coll = store.create_collection("alice", "C")
        store.add_bookmark(
            coll["id"],
            "arxiv:abc",
            owner="alice",
            title="Paper",
            source="arxiv",
        )
        bms = store.list_bookmarks(coll["id"])
        assert len(bms) == 1
        assert bms[0]["document_id"] == "arxiv:abc"

    def test_duplicate_bookmark_is_idempotent(self, store):
        coll = store.create_collection("alice", "C")
        store.add_bookmark(coll["id"], "arxiv:abc", owner="alice")
        store.add_bookmark(
            coll["id"], "arxiv:abc", owner="alice", note="updated"
        )
        assert len(store.list_bookmarks(coll["id"])) == 1

    def test_remove_bookmark(self, store):
        coll = store.create_collection("alice", "C")
        store.add_bookmark(coll["id"], "arxiv:abc", owner="alice")
        assert (
            store.remove_bookmark(coll["id"], "arxiv:abc", owner="alice")
            is True
        )
        assert store.list_bookmarks(coll["id"]) == []

    def test_get_collection_includes_bookmarks(self, store):
        coll = store.create_collection("alice", "C")
        store.add_bookmark(coll["id"], "d1", owner="alice", title="t")
        full = store.get_collection(coll["id"], owner="alice")
        assert full["bookmark_count"] == 1
        assert full["bookmarks"][0]["document_id"] == "d1"

    def test_private_collection_hidden_from_others(self, store):
        coll = store.create_collection("alice", "C", is_public=False)
        assert store.get_collection(coll["id"], owner="bob") is None

    def test_delete_requires_owner(self, store):
        coll = store.create_collection("alice", "C")
        assert store.delete_collection(coll["id"], owner="bob") is False
        assert store.delete_collection(coll["id"], owner="alice") is True
