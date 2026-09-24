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


class TestEvidenceGrounding:
    def test_unrelated_documents_do_not_become_an_answer(self):
        summary = _summarizer().summarize(
            "quantum computing",
            [
                _doc(
                    1,
                    "DMA",
                    "The controller transfers data from the device into system memory.",
                )
            ],
        )
        assert summary.grounding == "insufficient-evidence"
        assert summary.citations == []

    def test_stop_words_do_not_count_as_evidence(self):
        summary = _summarizer().summarize(
            "What is the quantum behavior?",
            [
                _doc(
                    1,
                    "DMA",
                    "The controller is the component that transfers data.",
                )
            ],
        )
        assert summary.citations == []

    def test_body_evidence_and_offsets_are_exact(self):
        doc = _doc(
            1,
            "DMA",
            "An introduction unrelated to the query.",
            body="😀 Context.\n  DMA transfers samples into circular buffers.\nMore detail.",
        )
        summary = _summarizer().summarize("DMA circular buffers", [doc])
        assert summary.grounding == "source-extract"
        for cite in summary.to_dict()["citations"]:
            for excerpt in cite["excerpts"]:
                assert excerpt["field"] == "body"
                assert (
                    doc.body[excerpt["start"] : excerpt["end"]]
                    == excerpt["quote"]
                )
                assert excerpt["offset_unit"] == "unicode-code-points"

    def test_only_used_sources_are_cited_and_duplicates_do_not_multiply(self):
        unrelated = _doc(
            1, "Other", "A discussion of unrelated botanical research."
        )
        relevant = _doc(
            2, "DMA", "DMA transfers samples into circular buffers."
        )
        summary = _summarizer().summarize(
            "DMA", [unrelated, relevant, relevant]
        )
        assert [c.id for c in summary.citations] == [relevant.id]
        assert summary.answer.endswith("[1]")
        assert len(summary.citations[0].excerpts) == 1

    def test_original_numeric_references_cannot_impersonate_citations(self):
        summary = _summarizer().summarize(
            "DMA",
            [_doc(1, "DMA", "DMA transfers are discussed in reference [99].")],
        )
        assert "[99]" not in summary.answer
        assert "[99]" in summary.citations[0].excerpts[0]["quote"]

    def test_invalid_model_citations_fall_back_to_source_text(
        self, monkeypatch
    ):
        service = Summarizer(EngineConfig(llm_enabled=True))
        monkeypatch.setattr(
            service, "_llm_answer", lambda *a: "A fabricated DMA claim. [99]"
        )
        summary = service.summarize(
            "DMA",
            [_doc(1, "DMA", "DMA transfers samples into circular buffers.")],
        )
        assert summary.generator == "extractive"
        assert summary.fallback_reason == "invalid-citations"
        assert "fabricated" not in summary.answer

    def test_uncited_sentence_is_rejected_even_with_a_valid_cited_sentence(
        self,
    ):
        from engine.evidence import citation_references_valid

        assert not citation_references_valid(
            "An unsupported claim. A cited claim. [1]", 1
        )
        assert not citation_references_valid("An unsupported claim. [0]", 1)
        assert not citation_references_valid("An unsupported claim.", 1)
        assert citation_references_valid("One claim. [1] Another claim. [2]", 2)

    def test_model_reference_checks_are_not_labeled_entailment(
        self, monkeypatch
    ):
        service = Summarizer(EngineConfig(llm_enabled=True))
        monkeypatch.setattr(
            service, "_llm_answer", lambda *a: "DMA transfers samples. [1]"
        )
        summary = service.summarize(
            "DMA",
            [_doc(1, "DMA", "DMA transfers samples into circular buffers.")],
        )
        assert summary.generator == "llm"
        assert summary.grounding == "references-only"
        assert summary.citations[0].excerpts

    def test_model_never_runs_without_query_matching_evidence(
        self, monkeypatch
    ):
        service = Summarizer(EngineConfig(llm_enabled=True))

        def forbidden(*args):
            raise AssertionError("The model must not run without evidence")

        monkeypatch.setattr(service, "_llm_answer", forbidden)
        assert (
            service.summarize(
                "quantum",
                [
                    _doc(
                        1, "DMA", "DMA transfers samples into circular buffers."
                    )
                ],
            ).citations
            == []
        )

    def test_literal_helper_rejects_negated_claim_with_shared_tokens(self):
        from engine.summarize import verify_citation_entailment

        assert not verify_citation_entailment(
            "DMA is not supported.", "DMA is supported."
        )
        assert verify_citation_entailment(
            "DMA is supported.", "The manual says: DMA is supported."
        )

    def test_invalid_excerpt_limits_are_rejected(self):
        import pytest

        for limit in (0, -1, True, 100, "5"):
            with pytest.raises(ValueError):
                _summarizer().summarize("DMA", [], max_sentences=limit)
