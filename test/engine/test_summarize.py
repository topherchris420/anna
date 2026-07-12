"""Unit tests for extractive summaries and document comparison."""

from engine.config import EngineConfig
from engine.documents import Document, DocumentKind
from engine.summarize import Summarizer, compare_documents


def _doc(i, title, abstract, **kw):
    return Document(
        id=f"s:{i}",
        source="arxiv",
        kind=DocumentKind.PAPER,
        title=title,
        abstract=abstract,
        **kw,
    )


def _summarizer():
    # Ensure the LLM path is off so we test the extractive path deterministically.
    return Summarizer(EngineConfig(llm_enabled=False))


class TestExtractiveSummary:
    def test_answer_has_citations(self):
        docs = [
            _doc(
                1,
                "DMA on STM32",
                "The STM32 DMA controller supports circular buffer mode for continuous transfers. "
                "It offloads the CPU during data acquisition.",
            ),
            _doc(
                2,
                "ADC sampling",
                "Circular DMA lets the ADC sample continuously into a ring buffer without CPU load.",
            ),
        ]
        summary = _summarizer().summarize("circular buffer DMA", docs)
        assert summary.generator == "extractive"
        assert len(summary.citations) == 2
        assert "[1]" in summary.answer or "[2]" in summary.answer

    def test_empty_documents(self):
        summary = _summarizer().summarize("anything", [])
        assert summary.citations == []
        assert "No relevant documents" in summary.answer

    def test_to_dict_shape(self):
        docs = [
            _doc(
                1,
                "t",
                "Some relevant sentence about controllers and stability margins here.",
            )
        ]
        d = _summarizer().summarize("controllers", docs).to_dict()
        assert set(d) >= {"query", "answer", "citations", "generator"}
        assert d["citations"][0]["n"] == 1


class TestCompareDocuments:
    def test_comparison_structure(self):
        a = _doc(
            1,
            "Kalman filter tutorial",
            "state estimation with kalman filters",
            categories=["eess.SY"],
            version="1",
        )
        b = _doc(
            2,
            "Kalman filtering in robotics",
            "kalman filter for robot localization",
            categories=["cs.RO", "eess.SY"],
        )
        result = compare_documents(a, b)
        assert result["a"]["id"] == "s:1"
        assert result["b"]["id"] == "s:2"
        assert "eess.SY" in result["shared_categories"]
        assert "cs.RO" in result["only_b_categories"]
        assert 0.0 <= result["text_similarity"] <= 1.0
        assert "kalman" in result["shared_terms"]
