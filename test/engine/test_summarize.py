"""Unit tests for extractive summaries and document comparison."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine import records
from engine.config import EngineConfig
from engine.documents import Document, DocumentKind
from engine.evidence import sentence_spans
from engine.summarize import Summarizer, compare_documents

EVIDENCE_JS = Path(__file__).resolve().parents[2] / "frontend" / "evidence.js"


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

    def test_stop_words_are_not_shared_terms(self):
        # "for", "the" and "of" are shared by nearly every pair of English
        # abstracts; reporting them (or counting them toward similarity)
        # made unrelated documents look alike.
        a = _doc(1, "A DMA engine for the ESP32", "the state of the art")
        b = _doc(2, "demo/tiny-rtos", "a kernel for the cortex-m")
        result = compare_documents(a, b)
        assert result["shared_terms"] == []
        assert result["text_similarity"] == 0.0


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

    def test_invalid_excerpt_limits_are_rejected(self):
        for limit in (0, -1, True, 100, "5"):
            with pytest.raises(ValueError):
                _summarizer().summarize("DMA", [], max_sentences=limit)


SPLIT_CASES = [
    (
        "Fluctuations are buffered by heart rate: i.e. the arrhythmia. It varies.",
        [
            "Fluctuations are buffered by heart rate: i.e. the arrhythmia.",
            "It varies.",
        ],
    ),
    (
        "Use a ring buffer, e.g. a circular queue. It wraps.",
        ["Use a ring buffer, e.g. a circular queue.", "It wraps."],
    ),
    (
        "Smith et al. measured the coupling. It was weak.",
        ["Smith et al. measured the coupling.", "It was weak."],
    ),
    (
        "Compare DMA vs. polling. DMA wins.",
        ["Compare DMA vs. polling.", "DMA wins."],
    ),
    (
        "The bound (cf. Fig. 2 and Eq. 3) holds. It is tight.",
        ["The bound (cf. Fig. 2 and Eq. 3) holds.", "It is tight."],
    ),
    (
        "J. R. Smith and J.R.R. Tolkien agree. Others do not.",
        ["J. R. Smith and J.R.R. Tolkien agree.", "Others do not."],
    ),
    # Real boundaries still split, and a line break always ends a sentence.
    (
        "We measured x. Then y. Is it? Yes!",
        ["We measured x.", "Then y.", "Is it?", "Yes!"],
    ),
    ("Buffered, i.e.\nthe arrhythmia.", ["Buffered, i.e.", "the arrhythmia."]),
]


class TestSentenceSegmentation:
    @pytest.mark.parametrize("text, sentences", SPLIT_CASES)
    def test_abbreviations_do_not_end_sentences(self, text, sentences):
        assert [text[s:e] for s, e in sentence_spans(text)] == sentences

    def test_abbreviation_does_not_produce_a_fragment_citation(self):
        # arXiv 1007.2229: the abstract was cut after "i.e.", so the cited
        # excerpt was "the respiratory sinus arrhythmia." on its own.
        first = (
            "Using a model of blood pressure dynamics, fluctuations are buffered "
            "by appropriate heart rate changes: i.e. the respiratory sinus "
            "arrhythmia."
        )
        doc = _doc(1, "RSA", first + " The buffering depends on timing.")
        summary = _summarizer().summarize("respiratory sinus arrhythmia", [doc])
        [excerpt] = summary.citations[0].excerpts
        assert excerpt["quote"] == first
        assert (excerpt["start"], excerpt["end"]) == (0, len(first))
        record = {
            "schema": records.RECORD_SCHEMA,
            "summary": summary.to_dict(),
            "hits": [],
        }
        report = records.verify_record(record, lambda doc_id: doc)
        assert [r["status"] for r in report["excerpts"]] == ["verified"]

    @pytest.mark.skipif(
        shutil.which("node") is None, reason="node not installed"
    )
    def test_browser_splits_identically(self):
        texts = [text for text, _ in SPLIT_CASES]
        completed = subprocess.run(
            [
                "node",
                "-e",
                "const e = require(process.argv[1]);"
                "const texts = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                "process.stdout.write("
                "JSON.stringify(texts.map((t) => e.sentenceSpans(t))));",
                str(EVIDENCE_JS),
            ],
            input=json.dumps(texts),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        # ASCII text, so UTF-16 and code-point offsets coincide.
        assert json.loads(completed.stdout) == [
            [list(span) for span in sentence_spans(text)] for text in texts
        ]
