"""Contract tests for the LLM-agent endpoint (POST /api/v1/agent/search).

Everything here exercises pure functions — no Flask, no backend. The route
itself is covered in test_api_agent_routes.py (skipped without Flask).
"""

import json
from pathlib import Path

import pytest

from allthethings.engine_api.agent_search import (
    CHUNK_MAX_CHARS,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    build_content_chunk,
    normalize_relevance,
    parse_agent_search_request,
    resolve_domain_filter,
    results_to_agent_dict,
)
from allthethings.engine_api.agent_spec import AGENT_OPENAPI_SPEC
from engine.documents import Document
from engine.search import SearchHit, SearchResults, fused_score_ceiling


class TestParseAgentSearchRequest:
    def test_minimal_body_gets_defaults(self):
        parsed, err = parse_agent_search_request({"query": "kalman filter"})
        assert err is None
        assert parsed.query == "kalman filter"
        assert parsed.domain_filter is None
        assert parsed.limit == DEFAULT_LIMIT
        assert parsed.min_score == 0.0

    def test_full_body(self):
        parsed, err = parse_agent_search_request(
            {
                "query": "  dma  ",
                "domain_filter": "arxiv",
                "limit": 10,
                "min_score": 0.4,
            }
        )
        assert err is None
        assert parsed.query == "dma"
        assert parsed.domain_filter == "arxiv"
        assert parsed.limit == 10
        assert parsed.min_score == 0.4

    @pytest.mark.parametrize("body", [None, [], "query", 42])
    def test_non_object_body_rejected(self, body):
        parsed, err = parse_agent_search_request(body)
        assert parsed is None and "JSON object" in err

    @pytest.mark.parametrize(
        "query", [None, "", "   ", 42, ["kalman"], {"q": "x"}]
    )
    def test_bad_query_rejected(self, query):
        body = {"query": query} if query is not None else {}
        parsed, err = parse_agent_search_request(body)
        assert parsed is None and "'query'" in err

    def test_limit_clamped_not_rejected(self):
        parsed, _ = parse_agent_search_request({"query": "x", "limit": 0})
        assert parsed.limit == 1
        parsed, _ = parse_agent_search_request({"query": "x", "limit": 999})
        assert parsed.limit == MAX_LIMIT

    def test_limit_accepts_numeric_string_and_integral_float(self):
        parsed, _ = parse_agent_search_request({"query": "x", "limit": "10"})
        assert parsed.limit == 10
        parsed, _ = parse_agent_search_request({"query": "x", "limit": 10.0})
        assert parsed.limit == 10

    @pytest.mark.parametrize("limit", ["ten", True, 5.5, [5]])
    def test_bad_limit_rejected(self, limit):
        parsed, err = parse_agent_search_request({"query": "x", "limit": limit})
        assert parsed is None and "'limit'" in err

    def test_min_score_clamped(self):
        parsed, _ = parse_agent_search_request(
            {"query": "x", "min_score": -0.5}
        )
        assert parsed.min_score == 0.0
        parsed, _ = parse_agent_search_request({"query": "x", "min_score": 1.5})
        assert parsed.min_score == 1.0

    @pytest.mark.parametrize("score", ["high", False, [0.5], float("nan")])
    def test_bad_min_score_rejected(self, score):
        parsed, err = parse_agent_search_request(
            {"query": "x", "min_score": score}
        )
        assert parsed is None and "'min_score'" in err

    def test_unknown_keys_ignored(self):
        parsed, err = parse_agent_search_request(
            {"query": "x", "mode": "semantic", "page": 3}
        )
        assert err is None and parsed.query == "x"

    def test_empty_domain_filter_treated_as_absent(self):
        parsed, err = parse_agent_search_request(
            {"query": "x", "domain_filter": "  "}
        )
        assert err is None and parsed.domain_filter is None

    def test_non_string_domain_filter_rejected(self):
        parsed, err = parse_agent_search_request(
            {"query": "x", "domain_filter": ["arxiv"]}
        )
        assert parsed is None and "'domain_filter'" in err


class TestResolveDomainFilter:
    def test_none_and_empty_produce_no_filters(self):
        assert resolve_domain_filter(None).to_es_filters() == []
        assert resolve_domain_filter("").to_es_filters() == []

    def test_bare_kind_filters_by_kind(self):
        assert resolve_domain_filter("paper").kinds == ["paper"]
        assert resolve_domain_filter("PAPER").kinds == ["paper"]

    def test_bare_registered_source_filters_by_source(self):
        assert resolve_domain_filter("arxiv").sources == ["arxiv"]
        assert resolve_domain_filter("GitHub").sources == ["github"]

    def test_explicit_prefixes_pin_the_facet(self):
        assert resolve_domain_filter("source:arxiv").sources == ["arxiv"]
        assert resolve_domain_filter("kind:Paper").kinds == ["paper"]
        assert resolve_domain_filter("category:cs.RO").categories == ["cs.RO"]
        assert resolve_domain_filter("topic:robotics").categories == [
            "robotics"
        ]

    def test_prefix_beats_bare_interpretation(self):
        # "category:paper" must NOT become a kind filter.
        filters = resolve_domain_filter("category:paper")
        assert filters.categories == ["paper"] and filters.kinds == []

    def test_unknown_value_falls_through_to_category(self):
        filters = resolve_domain_filter("quantum computing")
        assert filters.categories == ["quantum computing"]

    def test_unknown_prefix_is_kept_verbatim_as_category(self):
        # arXiv categories like "math:GT" contain a colon of their own.
        assert resolve_domain_filter("math:GT").categories == ["math:GT"]


class TestRelevanceNormalization:
    def test_zero_ceiling_maps_to_zero(self):
        assert normalize_relevance(0.5, 0.0) == 0.0

    def test_score_at_ceiling_is_one(self):
        assert normalize_relevance(2 / 61, fused_score_ceiling(2, 60)) == 1.0

    def test_score_above_ceiling_clamps_to_one(self):
        assert normalize_relevance(99.0, 1.0) == 1.0

    def test_proportional_mapping(self):
        assert normalize_relevance(0.25, 0.5) == 0.5


def _doc(**overrides):
    payload = dict(
        id="arxiv:8c4f01f9a3b2d715",
        source="arxiv",
        kind="paper",
        title="Kalman Filter Divergence Analysis",
        abstract="A study of divergence in extended Kalman filters.",
        url="https://arxiv.org/abs/2401.00001",
        authors=["A. Author"],
        published="2024-01-15",
    )
    payload.update(overrides)
    return Document(**payload)


def _results(hits, *, num_rankings=2, query="kalman"):
    return SearchResults(
        query=query,
        mode="hybrid",
        total=len(hits),
        hits=hits,
        facets={},
        score_ceiling=fused_score_ceiling(num_rankings, 60),
    )


class TestContentChunk:
    def test_highlights_win_and_markup_is_stripped(self):
        hit = SearchHit(
            document=_doc(),
            score=0.01,
            highlights=["…the <em>Kalman</em> gain…", "…<em>diverges</em>…"],
        )
        chunk = build_content_chunk(hit)
        assert "<em>" not in chunk and "</em>" not in chunk
        assert "Kalman" in chunk and "diverges" in chunk

    def test_falls_back_to_abstract_then_body(self):
        hit = SearchHit(document=_doc(), score=0.01)
        assert build_content_chunk(hit) == _doc().abstract
        hit = SearchHit(
            document=_doc(abstract="", body="Body   text\nhere"), score=0.01
        )
        assert build_content_chunk(hit) == "Body text here"

    def test_long_text_truncated_on_word_boundary(self):
        hit = SearchHit(
            document=_doc(abstract="", body="word " * 1000), score=0.01
        )
        chunk = build_content_chunk(hit)
        assert len(chunk) <= CHUNK_MAX_CHARS + 2  # trailing " …"
        assert chunk.endswith("…") and not chunk[:-2].endswith(" ")


class TestResultsToAgentDict:
    def test_contract_shape(self):
        ceiling = fused_score_ceiling(2, 60)
        hit = SearchHit(document=_doc(), score=ceiling * 0.9)
        payload = results_to_agent_dict(_results([hit]))
        assert payload["status"] == "success"
        assert payload["query"] == "kalman"
        assert payload["total_results"] == 1 == len(payload["results"])
        result = payload["results"][0]
        assert set(result) == {
            "id",
            "title",
            "authors",
            "content_chunk",
            "source_url",
            "relevance_score",
            "source",
            "kind",
            "published",
        }
        assert result["relevance_score"] == 0.9
        assert result["source_url"] == "https://arxiv.org/abs/2401.00001"

    def test_min_score_prunes_low_relevance_tail(self):
        ceiling = fused_score_ceiling(2, 60)
        hits = [
            SearchHit(document=_doc(id="a:1"), score=ceiling * 0.9),
            SearchHit(document=_doc(id="a:2"), score=ceiling * 0.4),
        ]
        payload = results_to_agent_dict(_results(hits), min_score=0.5)
        assert payload["total_results"] == 1
        assert payload["results"][0]["id"] == "a:1"

    def test_source_url_falls_back_to_pdf_url(self):
        hit = SearchHit(
            document=_doc(url="", pdf_url="https://x.test/a.pdf"), score=1.0
        )
        payload = results_to_agent_dict(_results([hit], num_rankings=1))
        assert payload["results"][0]["source_url"] == "https://x.test/a.pdf"


class TestSpecMatchesDocs:
    def test_checked_in_spec_is_in_sync(self):
        # docs/openapi-agent-search.json is what james_library codegens the
        # Rust client from; regenerate it whenever agent_spec.py changes:
        #   python3 -c "import json; from allthethings.engine_api.agent_spec \
        #     import AGENT_OPENAPI_SPEC; print(json.dumps(AGENT_OPENAPI_SPEC, \
        #     indent=2))" > docs/openapi-agent-search.json
        spec_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "openapi-agent-search.json"
        )
        assert json.loads(spec_path.read_text()) == AGENT_OPENAPI_SPEC

    def test_spec_advertises_the_contract_limits(self):
        request_schema = AGENT_OPENAPI_SPEC["components"]["schemas"][
            "AgentSearchRequest"
        ]
        assert request_schema["properties"]["limit"]["default"] == DEFAULT_LIMIT
        assert request_schema["properties"]["limit"]["maximum"] == MAX_LIMIT
        assert request_schema["required"] == ["query"]
