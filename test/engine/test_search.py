"""Unit tests for RRF fusion and filter construction (no Elasticsearch)."""

import pytest

from engine.config import EngineConfig
from engine.search import (
    SearchFilters,
    SearchService,
    build_lexical_query,
    fused_score_ceiling,
    query_terms,
    reciprocal_rank_fusion,
)


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


class TestFusedScoreCeiling:
    def test_no_rankings_has_zero_ceiling(self):
        assert fused_score_ceiling(0, 60) == 0.0

    def test_single_ranking_uses_reciprocal_rank_scale(self):
        assert fused_score_ceiling(1, 60) == 1.0

    def test_multi_ranking_matches_rrf_maximum(self):
        # A document ranked first in every list reaches exactly the ceiling.
        fused = reciprocal_rank_fusion([["a", "b"], ["a", "c"]], k=60)
        assert fused[0][1] == pytest.approx(fused_score_ceiling(2, 60))
        assert fused_score_ceiling(3, 60) == pytest.approx(3 / 61)


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


class TestQueryTerms:
    def test_splits_on_punctuation_and_whitespace(self):
        assert query_terms("circular-buffer, DMA") == ["circular", "buffer", "DMA"]

    def test_empty_query_has_no_terms(self):
        assert query_terms("") == []
        assert query_terms(None) == []


class TestLexicalQuery:
    def _must(self, clause):
        return clause["bool"]["must"]["multi_match"]

    def test_long_queries_require_most_terms(self):
        # Without minimum_should_match an OR match returns documents carrying
        # a single term of a long question.
        clause = build_lexical_query("kalman filter divergence in flight", [])
        assert self._must(clause)["minimum_should_match"] == "2<70%"

    def test_minimum_should_match_is_configurable(self):
        clause = build_lexical_query("a b c", [], minimum_should_match="100%")
        assert self._must(clause)["minimum_should_match"] == "100%"

    def test_multi_term_query_boosts_the_phrase(self):
        clause = build_lexical_query("circular buffer dma", [])
        phrase = clause["bool"]["should"][0]["multi_match"]
        assert phrase["type"] == "phrase"
        assert phrase["query"] == "circular buffer dma"
        assert phrase["boost"] == 2.0
        assert phrase["slop"] == 2

    def test_phrase_bonus_never_becomes_a_requirement(self):
        # A `should` clause alongside a `must` cannot filter: recall is the
        # same set the multi_match matched, only the order changes.
        clause = build_lexical_query("circular buffer dma", [])
        assert "must" in clause["bool"]
        assert "minimum_should_match" not in clause["bool"]

    def test_single_term_query_skips_the_phrase_clause(self):
        clause = build_lexical_query("kalman", [])
        assert "should" not in clause["bool"]

    def test_phrase_boost_is_configurable(self):
        clause = build_lexical_query("circular buffer", [], phrase_boost=5.0)
        assert clause["bool"]["should"][0]["multi_match"]["boost"] == 5.0

    def test_filters_are_carried_into_the_bool_query(self):
        filters = SearchFilters(sources=["arxiv"]).to_es_filters()
        clause = build_lexical_query("dma", filters)
        assert clause["bool"]["filter"] == [{"terms": {"source": ["arxiv"]}}]

    def test_filters_are_copied_not_aliased(self):
        filters = SearchFilters(sources=["arxiv"]).to_es_filters()
        clause = build_lexical_query("dma", filters)
        clause["bool"]["filter"].append({"terms": {"kind": ["paper"]}})
        assert len(filters) == 1

    def test_title_outranks_body_text(self):
        fields = self._must(build_lexical_query("dma", []))["fields"]
        assert fields.index("title^3") < fields.index("search_text")


class TestServiceQueryWiring:
    def _service(self, **config_kwargs):
        # A stub embedder keeps the ML stack out of a pure ranking test.
        return SearchService(EngineConfig(**config_kwargs), embedder=object())

    def test_service_applies_configured_ranking_knobs(self):
        service = self._service(
            lexical_minimum_should_match="100%", lexical_phrase_boost=3.5
        )
        clause = service._bm25_query("circular buffer", [])
        assert (
            clause["bool"]["must"]["multi_match"]["minimum_should_match"]
            == "100%"
        )
        assert clause["bool"]["should"][0]["multi_match"]["boost"] == 3.5

    def test_semantic_hits_are_highlighted_against_the_query(self):
        # A kNN hit has no lexical query of its own; without highlight_query
        # the UI has no snippet showing why the document was retrieved.
        spec = self._service()._highlight_spec("esp32 dma")
        assert spec["highlight_query"]["multi_match"]["query"] == "esp32 dma"
        assert set(spec["fields"]) == {"search_text", "abstract"}

    def test_lexical_highlighting_needs_no_highlight_query(self):
        assert "highlight_query" not in self._service()._highlight_spec()
