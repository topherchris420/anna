"""Unit tests for the ShadowLibraries source plugin (offline, no network)."""

from engine.documents import DocumentKind
from engine.ingest import get_plugin, plugin_names
from engine.ingest.sources.shadowlibraries import (
    ShadowLibrariesSource,
    load_catalog,
)

ACCESS_METHODS = {
    "Direct Download",
    "Torrent",
    "IRC",
    "Telegram Bot",
    "Read Online",
}


class TestCatalogData:
    def test_catalog_loads_and_is_non_trivial(self):
        catalog = load_catalog()
        entries = catalog["entries"]
        assert len(entries) >= 15
        assert catalog["attribution"]["source_url"].startswith(
            "https://shadowlibraries.github.io"
        )

    def test_entries_have_required_fields(self):
        for entry in load_catalog()["entries"]:
            assert entry.get("id")
            assert entry.get("name")
            assert entry.get("url")
            assert entry.get("description")
            assert entry.get("access_method")

    def test_ids_are_unique(self):
        ids = [e["id"] for e in load_catalog()["entries"]]
        assert len(ids) == len(set(ids))

    def test_all_access_methods_represented(self):
        methods = {e["access_method"] for e in load_catalog()["entries"]}
        assert ACCESS_METHODS.issubset(methods)


class TestRegistration:
    def test_registered(self):
        assert "shadowlibraries" in plugin_names()

    def test_get_plugin(self):
        plugin = get_plugin("shadowlibraries")
        assert isinstance(plugin, ShadowLibrariesSource)
        assert plugin.default_kind == DocumentKind.LIBRARY
        assert plugin.supports_query is True


class TestOfflineFetch:
    def test_documents_offline(self):
        docs = list(ShadowLibrariesSource().documents(limit=1000))
        assert len(docs) >= 15
        assert all(d.source == "shadowlibraries" for d in docs)
        assert all(d.kind == DocumentKind.LIBRARY.value for d in docs)
        assert all(d.title and d.url for d in docs)

    def test_annas_archive_present(self):
        docs = list(ShadowLibrariesSource().documents(limit=1000))
        anna = next(d for d in docs if "Anna's Archive" in d.title)
        assert anna.extra["access_method"] == "Direct Download"
        assert "books" in anna.extra["formats"]
        assert anna.kind == DocumentKind.LIBRARY.value

    def test_fetch_respects_limit(self):
        raw = list(ShadowLibrariesSource().fetch(limit=3))
        assert len(raw) == 3

    def test_query_filters(self):
        src = ShadowLibrariesSource()
        hits = list(src.documents(query="sci-hub", limit=1000))
        assert hits, "expected at least one Sci-Hub match"
        assert all("sci-hub" in (d.title + d.abstract).lower() for d in hits)

    def test_query_no_match_is_empty(self):
        src = ShadowLibrariesSource()
        assert list(src.documents(query="zzz-no-such-library", limit=50)) == []


class TestNormalize:
    def test_normalize_enriches(self):
        raw = {
            "id": "example",
            "name": "Example Library",
            "access_method": "Direct Download",
            "category": "Direct Downloads",
            "url": "https://example.org",
            "description": "An example.",
            "formats": ["books"],
            "languages": ["en", "es"],
            "tags": ["demo"],
        }
        doc = ShadowLibrariesSource().normalize(raw)
        assert doc.kind == DocumentKind.LIBRARY.value
        assert doc.categories == ["Direct Downloads"]
        assert doc.language == "en"
        assert "shadow-library" in doc.tags
        assert "direct-download" in doc.tags
        assert doc.extra["access_method"] == "Direct Download"
        assert doc.extra["formats"] == ["books"]

    def test_normalize_skips_nameless(self):
        assert ShadowLibrariesSource().normalize({"url": "https://x"}) is None
