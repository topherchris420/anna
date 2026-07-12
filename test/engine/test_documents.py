"""Unit tests for the unified Document schema (no external services)."""

import pytest

from engine.documents import Document, DocumentKind


class TestDocument:
    def test_make_id_is_deterministic_and_namespaced(self):
        a = Document.make_id("arxiv", "2401.00001")
        b = Document.make_id("arxiv", "2401.00001")
        c = Document.make_id("github", "2401.00001")
        assert a == b
        assert a.startswith("arxiv:")
        assert a != c  # source is part of the identity

    def test_equation_autodetection(self):
        doc = Document(
            id="x", source="arxiv", kind=DocumentKind.PAPER,
            title="t", abstract=r"The relation $E = mc^2$ holds.",
        )
        assert doc.has_equations is True

    def test_no_false_equation_detection(self):
        doc = Document(
            id="x", source="arxiv", kind=DocumentKind.PAPER,
            title="t", abstract="Just plain prose about controllers.",
        )
        assert doc.has_equations is False

    def test_repository_kind_flags_code(self):
        doc = Document(id="x", source="github", kind=DocumentKind.REPOSITORY, title="r")
        assert doc.has_code is True

    def test_search_text_combines_fields(self):
        doc = Document(
            id="x", source="arxiv", kind=DocumentKind.PAPER, title="Kalman Filter",
            abstract="state estimation", authors=["A. Kalman"], categories=["eess.SY"],
        )
        text = doc.search_text()
        assert "Kalman Filter" in text
        assert "state estimation" in text
        assert "A. Kalman" in text
        assert "eess.SY" in text

    def test_to_index_doc_drops_none_embedding(self):
        doc = Document(id="x", source="s", kind=DocumentKind.OTHER, title="t")
        idx = doc.to_index_doc()
        assert "embedding" not in idx
        assert idx["kind"] == "other"
        assert "search_text" in idx

    def test_to_index_doc_keeps_embedding(self):
        doc = Document(id="x", source="s", kind=DocumentKind.OTHER, title="t")
        doc.embedding = [0.1, 0.2, 0.3]
        idx = doc.to_index_doc()
        assert idx["embedding"] == [0.1, 0.2, 0.3]

    def test_round_trip_from_source(self):
        doc = Document(
            id="arxiv:abc", source="arxiv", kind=DocumentKind.PAPER, title="t",
            abstract="a", tags=["x"], categories=["c"],
        )
        idx = doc.to_index_doc()
        restored = Document.from_source({"_id": "arxiv:abc", **idx})
        assert restored.id == "arxiv:abc"
        assert restored.title == "t"
        assert restored.tags == ["x"]

    def test_embedding_text_caps_body(self):
        doc = Document(
            id="x", source="s", kind=DocumentKind.OTHER, title="t",
            abstract="short", body="y" * 5000,
        )
        assert len(doc.embedding_text()) < 3000
