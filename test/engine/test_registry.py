"""Unit tests for the source-plugin registry and base behavior."""

import pytest

from engine.documents import Document, DocumentKind
from engine.ingest import all_plugins, get_plugin, plugin_names
from engine.ingest.base import SourcePlugin, register

EXPECTED_SOURCES = {
    "arxiv", "github", "nasa", "doe", "ieee", "nist",
    "linux_kernel", "stm32", "espressif", "arm", "riscv",
}


class TestRegistry:
    def test_all_expected_sources_registered(self):
        assert EXPECTED_SOURCES.issubset(set(plugin_names()))

    def test_get_plugin_returns_instance(self):
        plugin = get_plugin("arxiv")
        assert isinstance(plugin, SourcePlugin)
        assert plugin.name == "arxiv"

    def test_unknown_source_raises(self):
        with pytest.raises(KeyError):
            get_plugin("does-not-exist")

    def test_all_plugins_have_valid_info(self):
        for plugin in all_plugins():
            info = plugin.info()
            assert info["name"]
            assert info["display_name"]
            assert "default_kind" in info

    def test_duplicate_registration_rejected(self):
        with pytest.raises(ValueError):
            @register
            class Dup(SourcePlugin):
                name = "arxiv"  # already taken

                def fetch(self, **kwargs):
                    return iter([])

                def normalize(self, raw):
                    return None

    def test_nameless_plugin_rejected(self):
        with pytest.raises(ValueError):
            @register
            class NoName(SourcePlugin):
                def fetch(self, **kwargs):
                    return iter([])

                def normalize(self, raw):
                    return None


class _FakeSource(SourcePlugin):
    name = "_fake_test_source"
    default_kind = DocumentKind.OTHER

    def fetch(self, *, query=None, limit=100, **kwargs):
        for i in range(limit + 5):  # yield more than requested
            yield {"i": i}

    def normalize(self, raw):
        if raw["i"] % 7 == 0:
            return None  # exercise the skip path
        return self.make_document(str(raw["i"]), title=f"doc {raw['i']}")


class TestSourcePluginBase:
    def test_documents_respects_limit_and_skips(self):
        src = _FakeSource()
        docs = list(src.documents(limit=10))
        assert len(docs) == 10
        assert all(isinstance(d, Document) for d in docs)
        assert all(d.source == "_fake_test_source" for d in docs)

    def test_make_document_applies_identity(self):
        src = _FakeSource()
        doc = src.make_document("42", title="hi")
        assert doc.source == "_fake_test_source"
        assert doc.id == Document.make_id("_fake_test_source", "42")
        assert doc.kind == DocumentKind.OTHER
