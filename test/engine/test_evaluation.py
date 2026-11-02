"""Ranking metrics, the evaluation runner, and the shipped judgment set."""

import gzip
import json
import math

import pytest

from engine import evaluation as ev


class TestMetrics:
    grades = {"a": 2, "b": 1, "c": 0}

    def test_ndcg_is_one_for_the_ideal_order(self):
        assert ev.ndcg(["a", "b", "c"], self.grades) == pytest.approx(1.0)

    def test_ndcg_discounts_relevant_documents_ranked_low(self):
        ideal = 3 / math.log2(2) + 1 / math.log2(3)
        swapped = 1 / math.log2(2) + 3 / math.log2(3)
        assert ev.ndcg(["b", "a"], self.grades) == pytest.approx(swapped / ideal)

    def test_ndcg_counts_only_the_cut_off(self):
        assert ev.ndcg(["c", "x", "a"], self.grades, k=2) == 0.0

    def test_unjudged_documents_score_as_not_relevant(self):
        assert ev.ndcg(["x", "a", "b"], self.grades) < 1.0
        assert ev.precision(["x", "y"], self.grades, 2) == 0.0

    def test_ndcg_without_any_relevant_document_is_zero(self):
        assert ev.ndcg(["a"], {"a": 0}) == 0.0

    def test_reciprocal_rank_of_the_first_relevant_document(self):
        assert ev.reciprocal_rank(["c", "x", "b"], self.grades) == pytest.approx(1 / 3)
        assert ev.reciprocal_rank(["c"], self.grades) == 0.0

    def test_precision_and_recall(self):
        assert ev.precision(["a", "c", "b", "x", "y"], self.grades, 5) == 0.4
        assert ev.recall(["a", "c"], self.grades, 10) == 0.5
        assert ev.recall(["a"], {"a": 0}, 10) == 0.0


def _judgments(*topics):
    return ev.JudgmentSet(
        topics=[
            ev.Topic(id=t, query=q, grades=g, type=kind)
            for t, q, g, kind in topics
        ]
    )


class TestEvaluate:
    def test_report_scores_answerable_topics_and_counts_noise(self):
        judged = _judgments(
            ("a", "kalman", {"d1": 2, "d2": 0}, "keywords"),
            ("b", "how?", {"d3": 1}, "question"),
            ("n", "sourdough", {}, "no-answer"),
        )
        rankings = {"kalman": ["d1", "d2"], "how?": ["d9", "d3"], "sourdough": ["d5"]}
        seen = []

        def search(query, mode, n):
            seen.append((query, mode, n))
            return rankings[query]

        report = ev.evaluate(search, judged, mode="bm25", k=10)
        assert {mode for _, mode, _ in seen} == {"bm25"}
        assert all(n >= 10 for _, _, n in seen)
        rows = {r["id"]: r for r in report["topics"]}
        assert rows["a"]["ndcg"] == pytest.approx(1.0)
        assert rows["b"]["mrr"] == 0.5 and rows["b"]["unjudged"] == 1
        assert rows["n"]["noise"] == 1 and "ndcg" not in rows["n"]
        summary = report["summary"]
        assert summary["topics"] == 2 and summary["no_answer_topics"] == 1
        assert summary["mrr"] == pytest.approx(0.75)
        assert summary["noise"] == 1.0
        assert set(summary["by_type"]) == {"keywords", "question"}
        assert summary["failed_topics"] == []

    def test_a_topic_with_nothing_relevant_found_is_named(self):
        judged = _judgments(("a", "q", {"d1": 1}, "keywords"))
        report = ev.evaluate(lambda q, m, n: ["x"], judged)
        assert report["summary"]["failed_topics"] == ["a"]
        assert "no relevant result: a" in ev.format_report(report)


class TestEvaluateRelated:
    def test_scores_the_on_topic_share_of_each_seeds_related_documents(self):
        judged = _judgments(
            ("a", "q", {"d1": 2, "d2": 1, "d3": 0}, "keywords"),
            ("b", "r", {"d4": 2}, "keywords"),
        )
        related = {"d1": ["d1", "d2", "d3"], "d4": []}
        report = ev.evaluate_related(lambda d, k: related[d], judged, k=2)
        # d1: itself is skipped; d2 on topic, d3 not -> 0.5; d4: none -> 0.
        assert report == {"k": 2, "seeds": 2, "on_topic": 0.25, "empty": 1}


class TestShippedJudgmentSet:
    """test/relevance/: the judged queries every ranking change is held to."""

    @pytest.fixture(scope="class")
    def judged(self):
        return ev.load_judgments()

    @pytest.fixture(scope="class")
    def corpus_ids(self, judged):
        return {doc["id"] for doc in ev.read_corpus(judged.corpus)}

    def test_it_loads_with_its_corpus(self, judged, corpus_ids):
        assert judged.corpus.name == "corpus.jsonl.gz"
        assert len(judged.topics) >= 40
        assert len(corpus_ids) >= 300

    def test_every_judged_document_is_in_the_corpus(self, judged, corpus_ids):
        for topic in judged.topics:
            assert set(topic.grades) <= corpus_ids, topic.id

    def test_topics_are_written_down_and_unique(self, judged):
        ids = [t.id for t in judged.topics]
        assert len(ids) == len(set(ids))
        for topic in judged.topics:
            assert topic.query.strip() and topic.need.strip(), topic.id
            assert set(topic.grades.values()) <= {0, 1, 2}, topic.id
            assert topic.type in {"keywords", "question", "acronym", "no-answer"}

    def test_it_covers_questions_acronyms_and_unanswerable_queries(self, judged):
        kinds = [t.type for t in judged.topics]
        assert kinds.count("question") >= 4
        assert kinds.count("acronym") >= 2
        assert sum(1 for t in judged.topics if not t.answerable) >= 3

    def test_corpus_documents_construct(self, judged):
        from engine.documents import Document

        with gzip.open(judged.corpus, "rt", encoding="utf-8") as handle:
            first = json.loads(handle.readline())
        assert Document(**first).id == first["id"]

    def test_a_file_in_another_format_is_refused(self, tmp_path):
        bad = tmp_path / "j.json"
        bad.write_text(json.dumps({"format": "something-else", "topics": []}))
        with pytest.raises(ValueError, match="anna-relevance-judgments/v1"):
            ev.load_judgments(bad)


class TestEvaluateMisspellings:
    judged = _judgments(
        ("a", "kalman", {"d1": 2}, "keywords"),
        ("b", "filter", {"d2": 1}, "keywords"),
    )

    @staticmethod
    def _search(corrections):
        rankings = {"kalman": ["d1"], "filter": ["d2"], "kalmna": ["x"]}

        def search(query, spell, n):
            fixed = corrections.get(query) if spell else None
            return rankings[fixed or query], fixed

        return search

    def test_uncorrected_corrected_and_intended_rankings_are_scored(self):
        report = ev.evaluate_misspellings(
            self._search({"kalmna": "kalman"}),
            self.judged,
            [ev.Misspelling("a", "kalmna", "kalman", 1)],
        )
        assert report["slips"]["1"] == {
            "queries": 1,
            "ndcg_uncorrected": 0.0,
            "ndcg": 1.0,
            "ndcg_intended": 1.0,
            "corrected": 1,
            "restored": 1,
        }
        assert report["false_corrections"] == []
        assert "1 slip  ( 1): 0.000 → 1.000 (1.000)" in ev.format_misspelling_report(report)

    def test_correcting_a_judged_query_is_a_false_correction(self):
        report = ev.evaluate_misspellings(
            self._search({"filter": "kalman"}), self.judged, []
        )
        assert report["false_corrections"] == ["b"]
        assert "judged queries corrected: 1 of 2 (b)" in ev.format_misspelling_report(report)


class TestShippedMisspellings:
    path = ev.DEFAULT_JUDGMENTS.with_name("misspellings.json")

    def test_each_query_misspells_an_answerable_judged_topic(self):
        topics = {t.id: t for t in ev.load_judgments().topics}
        items = ev.load_misspellings(self.path)
        assert len(items) >= 70
        for item in items:
            topic = topics[item.topic]
            assert topic.answerable and item.intended == topic.query
            assert item.query != item.intended and item.slips in (1, 2)

    def test_the_generator_reproduces_the_file(self, tmp_path, monkeypatch):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "misspell", self.path.with_name("misspell.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "OUT", tmp_path / "misspellings.json")
        monkeypatch.setattr(module, "ROOT", tmp_path)
        module.main()
        assert (tmp_path / "misspellings.json").read_bytes() == self.path.read_bytes()

    def test_another_format_is_refused(self, tmp_path):
        bad = tmp_path / "m.json"
        bad.write_text(json.dumps({"format": "x", "queries": []}))
        with pytest.raises(ValueError, match="anna-misspellings/v1"):
            ev.load_misspellings(bad)
