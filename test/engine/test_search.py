"""Unit tests for RRF fusion and filter construction (no Elasticsearch)."""

import pytest

from engine.search import SearchFilters, reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_document_in_both_lists_ranks_first(self):
        bm25 = ["a", "b", "c"]
        knn = ["b", "d", "a"]
        fused = reciprocal_rank_fusion([bm25, knn])
        ids = [doc_id for doc_id, _ in fused]
        # 'a' and 'b' appear in both lists and should outrank single-list docs.
        assert set(ids[:2]) == {"a", "b"}
        assert "d" in ids and "c" in ids

    def test_scores_are_descending(self):
        fused = reciprocal_rank_fusion([["a", "b", "c"]])
        scores = [s for _, s in fused]
        assert scores == sorted(scores, reverse=True)

    def test_k_constant_affects_scores(self):
        low_k = reciprocal_rank_fusion([["a", "b"]], k=1)
        high_k = reciprocal_rank_fusion([["a", "b"]], k=1000)
        assert low_k[0][1] > high_k[0][1]

    def test_weights_bias_a_ranking(self):
        # Same two lists, but weight the second one heavily.
        r1 = ["a", "b"]
        r2 = ["b", "a"]
        fused = reciprocal_rank_fusion([r1, r2], weights=[0.1, 10.0])
        assert fused[0][0] == "b"  # r2's top wins under heavy weight

    def test_mismatched_weights_raise(self):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])

    def test_empty_input(self):
        assert reciprocal_rank_fusion([]) == []


class TestSearchFilters:
    def test_empty_filters_produce_no_clauses(self):
        assert SearchFilters().to_es_filters() == []

    def test_terms_filters(self):
        f = SearchFilters(
            sources=["arxiv"], kinds=["paper"], categories=["cs.LG"]
        )
        clauses = f.to_es_filters()
        assert {"terms": {"source": ["arxiv"]}} in clauses
        assert {"terms": {"kind": ["paper"]}} in clauses
        assert {"terms": {"categories": ["cs.LG"]}} in clauses

    def test_boolean_filters(self):
        clauses = SearchFilters(
            has_code=True, has_equations=False
        ).to_es_filters()
        assert {"term": {"has_code": True}} in clauses
        assert {"term": {"has_equations": False}} in clauses

    def test_year_range(self):
        clauses = SearchFilters(year_from=2020, year_to=2023).to_es_filters()
        ranges = [c for c in clauses if "range" in c]
        assert ranges
        rng = ranges[0]["range"]["published"]
        assert rng["gte"] == "2020" and rng["lte"] == "2023"

    def test_version_filter(self):
        clauses = SearchFilters(version="v5.1").to_es_filters()
        assert {"term": {"version": "v5.1"}} in clauses
