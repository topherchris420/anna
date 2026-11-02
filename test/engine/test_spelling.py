"""Spelling correction: which words are corrected, into what, and how."""

import contextlib
import sys
import types
from collections import Counter

import pytest

from engine import spelling
from engine.config import EngineConfig
from engine.lexical import QueryTerm, parse_query


def _never(word):
    raise AssertionError(f"no index lookup expected for {word!r}")


def _terms(query, unknown=()):
    """Each term of ``query`` with a document frequency: 0 when listed."""
    return [
        (term, 0 if term.text.strip(",.?").lower() in unknown else 3)
        for term in parse_query(query)
    ]


class TestSlips:
    def test_neighbouring_keys_and_vowels_are_likely_slips(self):
        assert spelling.likely_key("k", "i")  # one row apart
        assert spelling.likely_key("n", "m")
        assert spelling.likely_key("e", "o")  # vowel for vowel
        assert not spelling.likely_key("l", "w")  # across the keyboard

    def test_one_slip_undoes_each_kind_of_typing_error(self):
        assert "kalman" in spelling.one_slip("kalmna")  # swapped letters
        assert "estimate" in spelling.one_slip("estimae")  # dropped letter
        assert "functions" in spelling.one_slip("functiions")  # extra letter
        assert "estimation" in spelling.one_slip("estimatkon")  # next key
        assert "stability" in spelling.one_slip("satbility")

    def test_one_slip_keeps_the_first_letter_and_unlikely_keys_out(self):
        assert all(w[0] == "k" for w in spelling.one_slip("kalmna"))
        # "sleep" is not a slip for "sweep": l and w are far apart.
        assert "sweep" not in spelling.one_slip("sleep")
        assert "kalmna" not in spelling.one_slip("kalmna")

    def test_slips_counts_a_swap_as_one_and_stops_at_the_limit(self):
        assert spelling.slips("kalman", "kalmna") == 1
        assert spelling.slips("estimation", "estimaoin") == 2
        assert spelling.slips("reinforcement", "reinforecemnt") == 2
        assert spelling.slips("kalman", "kalman") == 0
        assert spelling.slips("kalman", "xyz") == 3
        assert spelling.slips("kalman", "kalmanfilter", limit=2) == 3


class TestEligibleWords:
    @pytest.mark.parametrize(
        "term, word",
        [
            (QueryTerm("kalmna"), "kalmna"),
            (QueryTerm("Kalmna,"), "kalmna"),
            (QueryTerm("reinforcemsnt?"), "reinforcemsnt"),
        ],
    )
    def test_plain_unknown_words_are_suspects(self, term, word):
        assert spelling.suspect(term) == word

    @pytest.mark.parametrize(
        "term",
        [
            QueryTerm("risc-v"),  # split by the index: a name, not a typo
            QueryTerm("stm32h7"),  # a part number
            QueryTerm("fpgaa"[:4]),  # four letters: too short to judge
            QueryTerm("kalmna filtr", phrase=True),  # quoted on purpose
            QueryTerm("kalmna", negated=True),  # excluded on purpose
            QueryTerm("cooking"),  # English: absent from a corpus, not wrong
            QueryTerm("naïve"),  # non-ASCII letters
        ],
    )
    def test_names_short_words_phrases_and_english_are_left_alone(self, term):
        assert spelling.suspect(term) is None

    def test_the_word_list_is_english_and_loaded_once(self):
        words = spelling.EnglishWords()
        assert "cooking" in words and "Launch" in words
        assert "kalmna" not in words and "reinforcemsnt" not in words
        assert None not in words and "" not in words
        assert words._text.count("\n") > 150_000


class TestCorrect:
    def test_a_word_one_slip_away_in_the_found_documents_wins(self):
        context = Counter({"estimation": 4, "estimator": 1, "kalman": 3})
        fixed = spelling.correct(
            "kalman state estimatkon",
            _terms("kalman state estimatkon", {"estimatkon"}),
            context,
            _never,
        )
        assert fixed.query == "kalman state estimation"
        assert fixed.words == (("estimatkon", "estimation"),)

    def test_the_index_lookup_answers_when_the_documents_do_not(self):
        asked = []

        def lookup(word):
            asked.append(word)
            return "kalman"

        fixed = spelling.correct("kalmna", _terms("kalmna", {"kalmna"}), Counter(), lookup)
        assert fixed.query == "kalman" and asked == ["kalmna"]

    def test_two_slips_only_from_the_documents_and_only_for_long_words(self):
        context = Counter({"reinforcement": 2, "learning": 5})
        fixed = spelling.correct(
            "reinforecemnt learning",
            _terms("reinforecemnt learning", {"reinforecemnt"}),
            context,
            lambda word: None,
        )
        assert fixed.query == "reinforcement learning"
        # Five letters: "satet" is two slips from "state", too far to guess.
        assert spelling.slips("satet", "state") == 2
        assert spelling.correct(
            "satet kalman",
            _terms("satet kalman", {"satet"}),
            Counter({"state": 9}),
            lambda word: None,
        ) is None

    def test_known_english_and_quoted_words_are_never_changed(self):
        context = Counter({"launch": 5, "packet": 5})
        for query, unknown in [
            ("kalman filter", ()),  # every word is in the index
            ("lunch pocket robot", {"lunch", "pocket"}),  # English words
            ('"kalmna filter"', {"kalmna filter"}),  # a quoted phrase
        ]:
            assert spelling.correct(query, _terms(query, unknown), context, _never) is None

    def test_capitals_are_kept(self):
        for typed, expected in [
            ("Kalmna", "Kalman"),
            ("KALMNA", "KALMAN"),
            ("kalmna", "kalman"),
        ]:
            fixed = spelling.correct(
                typed, _terms(typed, {"kalmna"}), Counter({"kalman": 1}), _never
            )
            assert fixed.query == expected

    def test_nothing_found_is_no_correction(self):
        assert spelling.correct(
            "zzqxvb", _terms("zzqxvb", {"zzqxvb"}), Counter(), lambda w: None
        ) is None

    def test_report_names_the_typed_query_and_each_correction(self):
        fixed = spelling.Correction(
            typed="kalmna filtr", query="kalman filter",
            words=(("kalmna", "kalman"), ("filtr", "filter")),
        )
        assert fixed.report() == {
            "typed": "kalmna filtr",
            "query": "kalman filter",
            "corrections": [
                {"typed": "kalmna", "corrected": "kalman"},
                {"typed": "filtr", "corrected": "filter"},
            ],
        }


class TestNearest:
    def test_the_closest_spelling_in_the_documents_ties_to_the_most_common(self):
        texts = ["Estimation of state; the estimator estimates.", "estimation"]
        assert spelling.nearest("estimatkon", texts) == "estimation"
        assert spelling.nearest("controler", ["the controller", "control"]) == "controller"

    def test_a_different_first_letter_or_three_slips_is_not_a_spelling(self):
        assert spelling.nearest("beach", ["graph theory", "reach"]) is None
        assert spelling.nearest("kalmna", ["kalmanfilters"]) is None


# --------------------------------------------------------------------------- #
# The backends: when they correct, and what they ask the index
# --------------------------------------------------------------------------- #
def _psycopg2_stub(monkeypatch):
    fake = types.ModuleType("psycopg2")
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    fake.extras = extras
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)


class TestPostgres:
    @staticmethod
    def _service(monkeypatch, frequencies):
        from engine.pg.search import build_plan
        from engine.pg.search import PgSearchService

        _psycopg2_stub(monkeypatch)
        svc = PgSearchService(EngineConfig(backend="postgres", database_url="postgresql://u:p@h/db"))
        monkeypatch.setattr(svc.store, "has_vector", lambda: False)

        def plan(conn, query):
            terms = parse_query(query)
            rows = [(f"'{t.text.lower()}'", 1, frequencies.get(t.text.lower(), 2), 100) for t in terms]
            return build_plan(terms, rows, "70%")

        monkeypatch.setattr(svc, "_plan", plan)
        monkeypatch.setattr(svc, "_fts_ids", lambda conn, plan, *a: ["d1"] if plan.matchable else [])
        monkeypatch.setattr(svc, "_fetch", lambda conn, ids, cursor, plan=None: ({}, {}, {}))

        @contextlib.contextmanager
        def connect():
            yield object()

        monkeypatch.setattr(svc.store, "connect", connect)
        return svc

    def test_a_corrected_query_is_searched_and_says_what_was_typed(self, monkeypatch):
        svc = self._service(monkeypatch, {"kalmna": 0})
        seen = []

        def correction(conn, query, plan, context_ids):
            seen.append((query, context_ids))
            if query == "kalmna filter":
                return spelling.Correction("kalmna filter", "kalman filter", (("kalmna", "kalman"),))
            return None

        monkeypatch.setattr(svc, "_correction", correction)
        results = svc.search("kalmna filter", mode="bm25", include_facets=False)
        assert results.query == "kalman filter"
        assert results.spelling["typed"] == "kalmna filter"
        # The corrected search is not corrected again.
        assert seen == [("kalmna filter", ["d1"])]

    def test_spell_false_searches_exactly_what_was_typed(self, monkeypatch):
        svc = self._service(monkeypatch, {"kalmna": 0})
        monkeypatch.setattr(svc, "_correction", lambda *a: pytest.fail("not asked"))
        results = svc.search("kalmna filter", mode="bm25", include_facets=False, spell=False)
        assert results.query == "kalmna filter" and results.spelling is None

    def test_the_lookup_is_three_queries_in_a_savepoint(self, monkeypatch):
        from engine.pg.search import build_plan
        from engine.pg.search import PgSearchService

        svc = PgSearchService(EngineConfig(backend="postgres", database_url="postgresql://u:p@h/db"))
        executed = []

        class _Cursor:
            rows = []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                executed.append(sql.split()[0] if "SELECT" not in sql[:7] else sql)
                if "id = ANY" in sql:
                    self.rows = [("Kalman filtering of the state",)]
                elif "plainto_tsquery" in sql:
                    assert "kalman" in params["sp_words"]
                    self.rows = [("'kalman'",)]
                elif "LIMIT %(sp_n)s" in sql:
                    assert params["sp_lex"] == "'kalman'"
                    self.rows = [("the Kalman gain",)]
                else:
                    self.rows = []

            def fetchall(self):
                return self.rows

        class _Conn:
            def cursor(self):
                return _Cursor()

        terms = parse_query("kalmna gain")
        plan = build_plan(terms, [("'kalmna'", 1, 0, 9), ("'gain'", 1, 3, 9)], "70%")
        fixed = svc._correction(_Conn(), "kalmna gain", plan, ["d1"])
        assert fixed.query == "kalman gain"
        assert executed[0] == "SAVEPOINT" and executed[-1] == "RELEASE"

    def test_a_failing_lookup_costs_the_correction_not_the_search(self, monkeypatch):
        from engine.pg.search import build_plan
        from engine.pg.search import PgSearchService

        svc = PgSearchService(EngineConfig(backend="postgres", database_url="postgresql://u:p@h/db"))
        statements = []

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                statements.append(sql.split()[0])
                if sql.startswith("SELECT"):
                    raise RuntimeError("statement timeout")

        class _Conn:
            def cursor(self):
                return _Cursor()

        terms = parse_query("kalmna")
        plan = build_plan(terms, [("'kalmna'", 1, 0, 9)], "70%")
        assert svc._correction(_Conn(), "kalmna", plan, ["d1"]) is None
        assert statements[-1] == "ROLLBACK"


class TestElasticsearch:
    @staticmethod
    def _client(frequencies, texts):
        requests = []

        class _Client:
            def search(self, **kwargs):
                requests.append(kwargs)
                if "aggs" in kwargs:
                    filters = kwargs["aggs"]["words"]["filters"]["filters"]
                    return {
                        "aggregations": {
                            "words": {
                                "buckets": {
                                    w: {"doc_count": frequencies.get(w, 0)}
                                    for w in filters
                                }
                            }
                        }
                    }
                word = kwargs["query"]["match"]["search_text"]
                return {
                    "hits": {
                        "hits": [
                            {"_source": {"title": t, "abstract": ""}}
                            for t in texts.get(word, [])
                        ]
                    }
                }

        return _Client(), requests

    def _service(self):
        from engine.search import SearchService

        return SearchService(EngineConfig(), embedder=types.SimpleNamespace(using_model=False))

    def test_only_unmatched_eligible_terms_cost_a_count(self):
        client, requests = self._client({"kalman": 4}, {"kalman": ["Kalman filters"]})
        included = parse_query("kalmna filter cooking")
        fixed = self._service()._correction(
            client, "kalmna filter cooking", included, {"t1"}, [{"title": "a Kalman filter"}]
        )
        assert fixed.query == "kalman filter cooking"
        # "filter" was matched by a hit and "cooking" is English: only
        # "kalmna" was counted.
        first = requests[0]["aggs"]["words"]["filters"]["filters"]
        assert list(first) == ["kalmna"]

    def test_a_term_that_some_document_carries_is_not_corrected(self):
        client, requests = self._client({"kalmna": 1}, {})
        included = parse_query("kalmna")
        assert self._service()._correction(client, "kalmna", included, set(), []) is None
        assert len(requests) == 1  # the one count, no lookup

    def test_the_lookup_takes_the_most_common_candidate_spelled_by_documents(self):
        client, _ = self._client(
            {"processor": 14, "processe": 60}, {"processe": ["Process mining"], "processor": ["a processor"]}
        )
        # "processe" and "process" share a stem with 60 documents; the
        # documents spell it "process", two slips from "processoe".
        assert self._service()._spelled(client, "processoe") == "process"

    def test_a_failing_index_costs_the_correction_not_the_search(self):
        class _Broken:
            def search(self, **kwargs):
                raise RuntimeError("connection reset")

        included = parse_query("kalmna")
        assert self._service()._correction(_Broken(), "kalmna", included, set(), []) is None
