"""Query terms, coverage and term weights shared by both lexical backends."""

import math

import pytest

from engine.lexical import (
    MAX_TERMS,
    STOP_WORDS,
    QueryTerm,
    bm25_idf,
    parse_query,
    required_matches,
    split_terms,
)


def labels(query):
    return [t.label for t in parse_query(query)]


class TestParseQuery:
    def test_questions_reduce_to_their_content_terms(self):
        assert labels("how do I estimate state with a kalman filter") == [
            "estimate", "state", "kalman", "filter",
        ]

    def test_quotes_make_one_phrase_term(self):
        terms = parse_query('"circular buffer" dma')
        assert terms == [
            QueryTerm("circular buffer", phrase=True),
            QueryTerm("dma"),
        ]

    def test_stop_words_inside_a_phrase_are_part_of_it(self):
        assert labels('"state of the art" control') == [
            '"state of the art"', "control",
        ]

    def test_a_leading_minus_excludes_a_word_or_a_phrase(self):
        assert labels('signal -noise -"segmentation fault"') == [
            "signal", "-noise", '-"segmentation fault"',
        ]
        included, excluded = split_terms(parse_query("rl -robot"))
        assert [t.text for t in included] == ["rl"]
        assert [t.text for t in excluded] == ["robot"]

    def test_hyphenated_words_stay_whole(self):
        # A hyphen inside a word is not an exclusion: "RISC-V" is one term.
        assert labels("RISC-V real-time x86-64") == [
            "RISC-V", "real-time", "x86-64",
        ]

    def test_a_dangling_minus_or_quote_is_forgiven(self):
        assert labels("- dash alone") == ["dash", "alone"]
        assert labels('unclosed "quote runs on') == [
            "unclosed", '"quote runs on"',
        ]

    def test_queries_without_content_have_no_terms(self):
        for query in ("", "   ", "the of and", '"the"', "how?", "-- ??", '""'):
            assert parse_query(query) == [], query

    def test_repeats_count_once_regardless_of_case(self):
        assert labels("Kalman kalman KALMAN filter") == ["Kalman", "filter"]

    def test_a_quoted_single_word_is_a_plain_term(self):
        assert parse_query('"kalman"') == [QueryTerm("kalman")]

    def test_terms_are_capped_so_a_long_query_cannot_multiply_the_work(self):
        query = " ".join(f"word{i}" for i in range(300))
        terms = parse_query(query)
        assert len(terms) == MAX_TERMS == 32
        assert terms[0].text == "word0" and terms[-1].text == "word31"

    def test_stop_words_are_postgres_english_list(self):
        # The Snowball list Postgres uses for 'english': 127 words, and
        # notably "how", "do" and "does" — question words that are not in
        # Lucene's shorter English list.
        assert len(STOP_WORDS) == 127
        assert {"how", "do", "does", "i", "the"} <= STOP_WORDS
        assert "kalman" not in STOP_WORDS


class TestRequiredMatches:
    """Elasticsearch ``minimum_should_match`` semantics, case by case."""

    @pytest.mark.parametrize(
        "terms, required",
        # "2<70%": one or two terms are all required; from three on, 70% of
        # the terms rounded down.
        [(1, 1), (2, 2), (3, 2), (4, 2), (5, 3), (6, 4), (7, 4), (10, 7)],
    )
    def test_default_rule(self, terms, required):
        assert required_matches("2<70%", terms) == required

    @pytest.mark.parametrize(
        "spec, required",
        [
            # The examples from Elasticsearch's own reference, for 4 terms.
            ("3", 3),
            ("-2", 2),
            ("75%", 3),
            ("-25%", 3),
            ("3<90%", 3),
            ("2<-25% 9<-3", 3),
        ],
    )
    def test_reference_examples(self, spec, required):
        assert required_matches(spec, 4) == required

    def test_multiple_conditions_pick_the_largest_bound_below(self):
        spec = "2<-25% 9<-3"
        assert required_matches(spec, 2) == 2  # no bound below: all
        assert required_matches(spec, 8) == 6  # 8 - floor(8 * 25%)
        assert required_matches(spec, 12) == 9  # 12 - 3

    def test_the_result_is_clamped_to_the_terms_available(self):
        assert required_matches("5", 3) == 3
        assert required_matches("70", 3) == 3  # a count, not a percentage
        assert required_matches("0", 3) == 1
        assert required_matches("-9", 3) == 1
        assert required_matches("10%", 3) == 1

    def test_no_terms_require_nothing(self):
        assert required_matches("2<70%", 0) == 0

    @pytest.mark.parametrize("spec", ["", "lots", "2<", "a<70%", "3< 70%", None])
    def test_unreadable_settings_require_every_term(self, spec):
        assert required_matches(spec, 4) == 4


class TestBm25Idf:
    def test_matches_the_bm25_definition(self):
        assert bm25_idf(1, 312) == pytest.approx(
            math.log(1 + (312 - 1 + 0.5) / (1 + 0.5))
        )

    def test_rare_terms_weigh_more_and_every_weight_is_positive(self):
        weights = [bm25_idf(df, 312) for df in (1, 10, 100, 312)]
        assert weights == sorted(weights, reverse=True)
        assert all(w > 0 for w in weights)

    def test_impossible_counts_are_clamped(self):
        assert bm25_idf(500, 312) == bm25_idf(312, 312)
        assert bm25_idf(-3, 312) == bm25_idf(0, 312)
