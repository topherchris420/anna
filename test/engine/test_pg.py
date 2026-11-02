"""Unit tests for the Postgres backend (pure logic; no live database)."""

import datetime

import pytest

from engine import backend
from engine.config import EngineConfig
from engine.lexical import parse_query
from engine.pg.store import PgStore, parse_date, psycopg2_dsn, vector_literal
from engine.pg.search import (
    _ANALYZE_SQL,
    _HL_DELIM,
    _HL_START,
    _HL_STOP,
    LexicalPlan,
    PgSearchService,
    _with_terms,
    build_plan,
    distinctive_terms,
    headline_fragments,
    lexical_match_sql,
    lexical_rank_sql,
    rank_fragments,
)
from engine.search import SearchFilters, SearchService


def _pg_config():
    return EngineConfig(
        backend="postgres", database_url="postgresql://u:p@h/db"
    )


def _plan(query, corpus=100, frequencies=None, spec="70%"):
    """A plan as ``_plan`` would build it, without a database.

    Each term "normalizes" to its lower-cased text and occurs in one
    document unless ``frequencies`` says otherwise.
    """
    terms = parse_query(query)
    frequencies = frequencies or {}
    rows = [
        (f"'{t.text.lower()}'", 1, frequencies.get(t.text, 1), corpus)
        for t in terms
    ]
    return build_plan(terms, rows, spec)


class TestHelpers:
    def test_vector_literal(self):
        assert vector_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"
        assert vector_literal(None) is None
        assert vector_literal([]) is None

    def test_parse_date(self):
        assert parse_date("2024-05-01") == datetime.date(2024, 5, 1)
        assert parse_date("2023-06") == datetime.date(2023, 6, 1)
        assert parse_date("2022") == datetime.date(2022, 1, 1)
        assert parse_date("2024-01-01T12:00:00Z") == datetime.date(2024, 1, 1)
        assert parse_date(None) is None
        assert parse_date("not-a-date") is None

    def test_dsn_normalization(self):
        assert (
            psycopg2_dsn("postgresql+psycopg2://u:p@h/db")
            == "postgresql://u:p@h/db"
        )
        assert psycopg2_dsn("postgres://u:p@h/db") == "postgresql://u:p@h/db"
        assert psycopg2_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
        neon_url = "postgresql+psycopg2://u:p@ep-delicate-poetry-av3ypqpv-pooler.c-11.us-east-1.aws.neon.tech/neondb?sslmode=require&options=endpoint%3Dep-delicate-poetry-av3ypqpv"
        assert (
            psycopg2_dsn(neon_url)
            == "postgresql://u:p@ep-delicate-poetry-av3ypqpv-pooler.c-11.us-east-1.aws.neon.tech/neondb?sslmode=require"
        )


class TestFilterSql:
    def _svc(self):
        return PgSearchService(_pg_config())

    def test_empty_filters(self):
        sql, params = self._svc()._where(SearchFilters())
        assert sql == "" and params == {}

    def test_term_filters(self):
        sql, params = self._svc()._where(
            SearchFilters(
                sources=["arxiv"], kinds=["paper"], categories=["cs.LG"]
            )
        )
        assert "source = ANY(%(f_sources)s::text[])" in sql
        assert "kind = ANY(%(f_kinds)s::text[])" in sql
        assert "categories && %(f_cats)s::text[]" in sql
        assert params["f_sources"] == ["arxiv"]
        assert params["f_cats"] == ["cs.LG"]

    def test_bool_and_year_filters(self):
        sql, params = self._svc()._where(
            SearchFilters(has_code=True, year_from=2020, year_to=2023)
        )
        assert "has_code = %(f_has_code)s" in sql
        assert params["f_has_code"] is True
        assert params["f_yfrom"] == datetime.date(2020, 1, 1)
        assert params["f_yto"] == datetime.date(2023, 12, 31)


class TestBackendFacade:
    def test_default_backend_is_elasticsearch(self):
        assert backend.backend_name(EngineConfig()) == "elasticsearch"

    def test_postgres_search_service(self):
        svc = backend.get_search_service(_pg_config())
        assert isinstance(svc, PgSearchService)

    def test_elasticsearch_search_service(self):
        svc = backend.get_search_service(EngineConfig(backend="elasticsearch"))
        assert isinstance(svc, SearchService)


class TestVectorOptional:
    """pgvector is optional: the schema and retrieval must degrade to FTS."""

    def test_table_sql_includes_vector_when_available(self):
        store = PgStore(_pg_config())
        sql = store._create_table_sql(vector_ok=True)
        assert f"embedding     vector({store.dims})" in sql

    def test_table_sql_omits_vector_when_unavailable(self):
        store = PgStore(_pg_config())
        sql = store._create_table_sql(vector_ok=False)
        # No pgvector column at all (``to_tsvector(`` legitimately contains the
        # substring "vector(", so assert on the actual column instead).
        assert "embedding" not in sql
        assert f"vector({store.dims})" not in sql
        # The full-text column must still be present.
        assert "search_vector tsvector" in sql

    def test_generated_column_avoids_non_immutable_functions(self):
        """STORED generated columns require IMMUTABLE functions only.

        ``array_to_string`` is STABLE and makes Postgres reject the table with
        "generation expression is not immutable" — guard against re-adding it.
        """
        store = PgStore(_pg_config())
        for sql in (
            store._create_table_sql(vector_ok=True),
            store._create_table_sql(vector_ok=False),
        ):
            assert "array_to_string" not in sql
            # The three immutable two-arg to_tsvector clauses remain.
            assert sql.count("to_tsvector('english'") == 3

    def test_has_vector_is_cached(self):
        store = PgStore(_pg_config())
        store._vector_enabled = True
        assert store.has_vector() is True  # no DB probe needed
        store._vector_enabled = False
        assert store.has_vector() is False

    def test_semantic_degrades_to_fts_without_vector(self, monkeypatch):
        """With no pgvector column, ``semantic`` mode must run FTS, not kNN."""
        import sys
        import types

        # search() does ``from psycopg2.extras import RealDictCursor``; the test
        # environment has no psycopg2, so provide a minimal stand-in.
        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        svc = PgSearchService(_pg_config())
        monkeypatch.setattr(svc.store, "has_vector", lambda: False)

        calls = {"fts": 0, "knn": 0}
        monkeypatch.setattr(
            svc,
            "_fts_ids",
            lambda *a, **k: calls.__setitem__("fts", calls["fts"] + 1) or [],
        )
        monkeypatch.setattr(
            svc,
            "_knn_ids",
            lambda *a, **k: calls.__setitem__("knn", calls["knn"] + 1) or [],
        )

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return [0]

            def fetchall(self):
                return []

        class _Conn:
            def cursor(self, *a, **k):
                return _Cur()

        import contextlib

        @contextlib.contextmanager
        def _connect():
            yield _Conn()

        monkeypatch.setattr(svc.store, "connect", _connect)

        result = svc.search(
            "kalman filter", mode="semantic", include_facets=False
        )
        assert result.retrieval["executed"] == ["fts"]
        assert result.retrieval["unavailable"] == ["knn"]
        assert result.retrieval["degraded"] is True
        assert calls["fts"] == 1  # degraded to full-text
        assert calls["knn"] == 0  # kNN skipped (no vector column)


class TestEncoderFailure:
    """Parity with Elasticsearch: a model failure never discards lexical hits."""

    @staticmethod
    def _service(monkeypatch):
        import contextlib
        import sys
        import types

        from engine.documents import Document

        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        svc = PgSearchService(_pg_config())
        monkeypatch.setattr(svc.store, "has_vector", lambda: True)

        def broken(*args):
            raise RuntimeError("model failure")

        def knn_forbidden(*args, **kwargs):
            raise AssertionError("kNN must not run without a query vector")

        # A semantic model that fails while encoding the query.
        svc.embedder = types.SimpleNamespace(
            semantic=True, using_model=True, encode=broken
        )
        monkeypatch.setattr(svc, "_plan", lambda conn, query: _plan(query))
        monkeypatch.setattr(svc, "_fts_ids", lambda *a, **k: ["d1"])
        monkeypatch.setattr(svc, "_knn_ids", knn_forbidden)
        doc = Document(id="d1", source="arxiv", kind="paper", title="t")
        monkeypatch.setattr(
            svc,
            "_fetch",
            lambda conn, ids, cursor, plan=None: ({"d1": doc}, {}, {}),
        )

        class _Conn:
            def cursor(self, *a, **k):
                raise AssertionError("only patched primitives may run SQL")

        @contextlib.contextmanager
        def _connect():
            yield _Conn()

        monkeypatch.setattr(svc.store, "connect", _connect)
        return svc

    def test_hybrid_keeps_lexical_hits_and_reports_knn_unavailable(
        self, monkeypatch
    ):
        svc = self._service(monkeypatch)
        result = svc.search("kalman", mode="hybrid", include_facets=False)
        assert [h.document.id for h in result.hits] == ["d1"]
        assert result.retrieval["executed"] == ["fts"]
        assert result.retrieval["unavailable"] == ["knn"]
        assert result.retrieval["degraded"] is True
        # kNN did not execute, so no embedding kind is claimed.
        assert result.retrieval["embedding"] is None
        assert result.retrieval["score_ceiling"] == 1.0
        assert result.hits[0].explanation["ranks"] == {"fts": 1}

    def test_semantic_only_failure_is_a_backend_error(self, monkeypatch):
        from engine.search import SearchBackendError

        svc = self._service(monkeypatch)
        with pytest.raises(SearchBackendError, match="embedding failed"):
            svc.search("kalman", mode="semantic", include_facets=False)


class TestLexicalPlan:
    """A query's terms as Postgres matches them (see engine.lexical)."""

    def test_terms_carry_postgres_forms_frequencies_and_idf_weights(self):
        plan = _plan("kalman state", corpus=312,
                     frequencies={"kalman": 1, "state": 150})
        kalman, state = plan.included
        assert (kalman.tsquery, kalman.document_frequency) == ("'kalman'", 1)
        # Rarity is what the ranking pays for.
        assert kalman.weight > 5 > 1 > state.weight > 0
        assert plan.corpus_size == 312

    def test_terms_that_normalize_alike_count_once(self):
        terms = parse_query("filter filters")
        rows = [("'filter'", 1, 3, 10), ("'filter'", 1, 3, 10)]
        plan = build_plan(terms, rows, "2<70%")
        assert [t.term.text for t in plan.included] == ["filter"]
        assert plan.required == 1

    def test_terms_the_index_cannot_match_are_dropped_not_required(self):
        # "---" is not even a term (no word characters) …
        assert [t.text for t in parse_query("kalman ---")] == ["kalman"]
        # … while a term Postgres normalizes to nothing is reported dropped.
        terms = parse_query("kalman C++")
        plan = build_plan(
            terms, [("'kalman'", 1, 1, 10), ("", 0, 0, 10)], "2<70%"
        )
        assert [t.term.text for t in plan.included] == ["kalman"]
        assert [t.text for t in plan.dropped] == ["C++"]
        assert plan.required == 1

    def test_required_follows_the_minimum_should_match_setting(self):
        query = "kalman filter state estimation"
        assert _plan(query).required == 2  # "70%" of 4, rounded down
        assert _plan(query, spec="100%").required == 4
        assert _plan("kalman filter").required == 1  # either term
        assert _plan("kalman filter", spec="2<70%").required == 2

    def test_excluded_terms_are_planned_but_never_counted(self):
        plan = _plan('reinforcement learning -robot')
        assert [t.term.label for t in plan.excluded] == ["-robot"]
        assert len(plan.included) == 2 and plan.required == 1

    def test_report_states_the_rule(self):
        report = _plan('dma "circular buffer" -esp32').report()
        assert report["terms"] == ["dma", '"circular buffer"']
        assert report["excluded"] == ["-esp32"]
        assert report["required"] == 1
        assert set(report["weights"]) == {"dma", '"circular buffer"'}

    def test_a_query_without_terms_matches_nothing(self):
        assert not LexicalPlan().matchable
        assert lexical_match_sql(LexicalPlan()) == ("FALSE", {})

    def test_plan_is_one_round_trip(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor(
            all_rows=[("'kalman'", 1, 1, 50), ("'filter'", 1, 4, 50)]
        )
        plan = svc._plan(_RecordingConn(cur), "how does a kalman filter work")
        (sql, params), = cur.calls
        assert sql == _ANALYZE_SQL.format(table="engineering_docs")
        # Stop words never reach Postgres; "work" does (it is a content word).
        assert params == {"lx_texts": ["kalman", "filter", "work"]}
        assert [t.term.text for t in plan.included] == ["kalman", "filter"]

    def test_stop_words_alone_skip_the_database(self):
        svc = PgSearchService(_pg_config())

        class _NoConn:
            def cursor(self, *a, **k):
                raise AssertionError("nothing to analyze")

        assert not svc._plan(_NoConn(), "how do the").matchable


class TestLexicalSql:
    """Coverage matching and IDF ranking, as SQL with bound parameters."""

    def test_requiring_every_term_is_one_and_query_for_the_index(self):
        sql, params = lexical_match_sql(_plan("kalman filter", spec="100%"))
        assert sql == "search_vector @@ %(lx_all)s::tsquery"
        assert params == {"lx_all": "( 'kalman' ) & ( 'filter' )"}

    def test_requiring_any_term_is_one_or_query_for_the_index(self):
        # Two terms under the default rule: either one admits a document.
        sql, params = lexical_match_sql(_plan("kalman filter"))
        assert sql == "search_vector @@ %(lx_any)s::tsquery"
        assert params == {"lx_any": "( 'kalman' ) | ( 'filter' )"}

    def test_coverage_is_one_exact_tsquery_for_the_index(self):
        # Two of four terms, written out as the OR of every two-term AND, so
        # the GIN index returns exactly the qualifying documents.
        sql, params = lexical_match_sql(_plan("kalman filter state estimation"))
        assert sql == "search_vector @@ %(lx_cover)s::tsquery"
        cover = params["lx_cover"]
        assert cover.count(" & ") == 6 and cover.count(" | ") == 5
        assert cover.startswith("( ( 'kalman' ) & ( 'filter' ) ) | ")
        assert cover.endswith("( ( 'state' ) & ( 'estimation' ) )")

    def test_long_queries_count_the_terms_each_document_carries(self):
        # Nine terms, six required: 84 combinations is past the bound, so
        # the index narrows to any-term matches and the count decides.
        query = "kalman filter state estimation drone grid warehouse map lidar"
        sql, params = lexical_match_sql(_plan(query))
        assert sql.startswith("search_vector @@ %(lx_any)s::tsquery AND (")
        assert sql.count("(search_vector @@ %(lx_t") == 9
        assert sql.endswith(") >= %(lx_required)s")
        assert params["lx_required"] == 6
        assert params["lx_t0"] == "'kalman'"

    def test_excluded_terms_are_a_negated_condition(self):
        sql, params = lexical_match_sql(_plan("reinforcement learning -robot"))
        assert sql.endswith(" AND NOT search_vector @@ %(lx_not)s::tsquery")
        assert params["lx_not"] == "( 'robot' )"

    def test_query_text_never_reaches_the_sql(self):
        plan = _plan("x');DROP kalman filter state")
        for sql, params in (
            lexical_match_sql(plan),
            lexical_rank_sql(plan, 2.0),
        ):
            assert "drop" not in sql.lower() and "x')" not in sql
            assert any("x')" in str(value) for value in params.values())

    def test_rank_weights_each_term_by_its_idf(self):
        plan = _plan("kalman state", frequencies={"kalman": 1, "state": 60})
        sql, params = lexical_rank_sql(plan, 1.0)
        assert sql == (
            "(%(lx_w0)s * ts_rank(search_vector, %(lx_t0)s::tsquery) + "
            "%(lx_w1)s * ts_rank(search_vector, %(lx_t1)s::tsquery))"
        )
        assert params["lx_w0"] == plan.included[0].weight
        assert params["lx_w0"] > params["lx_w1"]

    def test_multi_term_query_boosts_an_exact_phrase(self):
        sql, params = lexical_rank_sql(_plan("circular buffer dma"), 2.0)
        assert "phraseto_tsquery('english', %(lx_phrase)s)" in sql
        assert params["lx_phrase"] == "circular buffer dma"
        assert params["phrase_boost"] == 2.0
        # The bonus multiplies the rank; it never filters (ELSE 1).
        assert sql.endswith("THEN %(phrase_boost)s ELSE 1 END)")
        assert "2.0" not in sql

    def test_single_term_and_boost_of_one_have_no_bonus(self):
        for plan, boost in ((_plan("dma"), 2.0), (_plan("dma buffer"), 1.0)):
            sql, params = lexical_rank_sql(plan, boost)
            assert "phraseto_tsquery" not in sql
            assert "phrase_boost" not in params

    def test_fts_ids_orders_by_rank_then_id_and_binds_everything(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor(all_rows=[("d1",)])
        ids = svc._fts_ids(
            _RecordingConn(cur), _plan("circular buffer"),
            "source = ANY(%(f_sources)s::text[])", {"f_sources": ["arxiv"]}, 10,
        )
        assert ids == ["d1"]
        (sql, params), = cur.calls
        assert " AND source = ANY(%(f_sources)s::text[]) " in sql
        assert sql.endswith(" DESC, id LIMIT %(limit)s")
        assert params["limit"] == 10 and params["f_sources"] == ["arxiv"]
        assert params["lx_any"] and params["phrase_boost"] == 2.0

    def test_fts_ids_without_terms_runs_no_sql(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor()
        assert svc._fts_ids(_RecordingConn(cur), LexicalPlan(), "", {}, 5) == []
        assert svc._fts_ids(_RecordingConn(cur), None, "", {}, 5) == []
        assert cur.calls == []


class TestMatchedTerms:
    def test_explanation_names_carried_and_missing_terms(self):
        plan = _plan("kalman filter state")
        explained = _with_terms({"ranks": {"fts": 1}}, plan, [True, False, True])
        assert explained == {
            "ranks": {"fts": 1},
            "matched_terms": ["kalman", "state"],
            "missing_terms": ["filter"],
        }

    def test_hits_without_term_flags_keep_their_explanation(self):
        explanation = {"ranks": {"browse": 1}}
        assert _with_terms(explanation, None, None) is explanation
        assert _with_terms(explanation, _plan("dma"), None) is explanation


class TestHighlights:
    """``ts_headline`` highlights: parity with the Elasticsearch backend."""

    def test_fragments_convert_sentinels_to_em(self):
        raw = f"a {_HL_START}kalman{_HL_STOP} filter"
        assert headline_fragments(raw) == ["a <em>kalman</em> filter"]

    def test_fragments_split_on_delimiter(self):
        raw = (
            f"one {_HL_START}dma{_HL_STOP} x"
            f"{_HL_DELIM}two {_HL_START}dma{_HL_STOP} y"
        )
        assert headline_fragments(raw) == [
            "one <em>dma</em> x",
            "two <em>dma</em> y",
        ]

    def test_matchless_prefix_is_dropped(self):
        # With no lexical match (e.g. a purely semantic kNN hit) ts_headline
        # returns the start of the text unmarked; the Elasticsearch backend
        # returns no highlights for those hits, so neither must this one.
        assert headline_fragments("just the document prefix") == []
        assert headline_fragments("") == []
        assert headline_fragments(None) == []

    def test_the_most_informative_fragment_comes_first(self):
        # Document order put "the existing <em>state</em> of warehouse…"
        # before the fragment saying "Kalman Filter"; pages show the first.
        plan = _plan(
            "kalman filter state estimation",
            corpus=312,
            frequencies={"kalman": 1, "filter": 4, "state": 80},
        )
        fragments = [
            "the existing <em>state</em> of warehouse environment",
            "A simple yet effective <em>Kalman</em> <em>Filter</em> was used",
        ]
        assert rank_fragments(fragments, plan) == fragments[::-1]

    def test_stemmed_lexemes_match_inflected_words_and_ties_keep_order(self):
        terms = parse_query("estimation")
        plan = build_plan(terms, [("'estim'", 1, 10, 100)], "70%")
        fragments = ["no marks here", "two <em>estimations</em> differ"]
        assert rank_fragments(fragments, plan)[0] == fragments[1]
        tied = ["<em>x</em> one", "<em>x</em> two"]
        assert rank_fragments(tied, _plan("y")) == tied
        assert rank_fragments(tied, None) == tied

    def test_document_markup_stays_raw_text(self):
        # Contract: fragments are raw text plus <em> markers — escaping is the
        # consumer's job. Literal markup in a document must survive verbatim so
        # consumers can escape it; only the sentinels become tags.
        raw = f"<b>bold</b> {_HL_START}term{_HL_STOP}"
        assert headline_fragments(raw) == ["<b>bold</b> <em>term</em>"]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _fake_conn(rows):
        captured = {}

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                captured["sql"] = sql
                captured["params"] = params

            def fetchall(self):
                return rows

        class _Conn:
            def cursor(self, *a, **k):
                return _Cur()

        return _Conn(), captured

    def test_fetch_computes_highlights_and_carried_terms_for_queries(self):
        svc = PgSearchService(_pg_config())
        row = {
            "id": "d1",
            "source": "arxiv",
            "kind": "paper",
            "title": "t",
            "hl": f"the {_HL_START}kalman{_HL_STOP} gain",
            "lx_carried": [True, False],
        }
        conn, captured = self._fake_conn([row])
        docs, highlights, carried = svc._fetch(
            conn, ["d1"], dict, plan=_plan("kalman filter")
        )
        assert "ts_headline" in captured["sql"]
        # Any carried term is highlighted: a partial match still shows why.
        assert captured["params"]["hl_q"] == "( 'kalman' ) | ( 'filter' )"
        assert "ARRAY[search_vector @@ %(lx_t0)s::tsquery" in captured["sql"]
        assert docs["d1"].title == "t"
        assert highlights == {"d1": ["the <em>kalman</em> gain"]}
        assert carried == {"d1": [True, False]}

    def test_fetch_skips_headline_when_browsing(self):
        svc = PgSearchService(_pg_config())
        row = {"id": "d1", "source": "arxiv", "kind": "paper", "title": "t"}
        conn, captured = self._fake_conn([row])
        docs, highlights, carried = svc._fetch(conn, ["d1"], dict)
        assert "ts_headline" not in captured["sql"]
        assert "d1" in docs and highlights == {} and carried == {}

    def test_search_attaches_highlights_to_hits(self, monkeypatch):
        import contextlib
        import sys
        import types

        # search() imports psycopg2.extras; provide the same minimal stand-in
        # the vector-degradation test uses (the test env has no psycopg2).
        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        svc = PgSearchService(_pg_config())
        monkeypatch.setattr(svc.store, "has_vector", lambda: False)
        monkeypatch.setattr(svc, "_fts_ids", lambda *a, **k: ["d1"])

        from engine.documents import Document

        doc = Document(id="d1", source="arxiv", kind="paper", title="t")
        monkeypatch.setattr(svc, "_plan", lambda conn, query: _plan(query))
        monkeypatch.setattr(
            svc,
            "_fetch",
            lambda conn, ids, cursor, plan=None: (
                {"d1": doc},
                {"d1": ["the <em>kalman</em> gain"]},
                {"d1": [True, False]},
            ),
        )

        class _Conn:
            def cursor(self, *a, **k):
                raise AssertionError("only patched primitives may run SQL")

        @contextlib.contextmanager
        def _connect():
            yield _Conn()

        monkeypatch.setattr(svc.store, "connect", _connect)

        results = svc.search("kalman gain", mode="bm25", include_facets=False)
        hit = results.hits[0]
        assert hit.highlights == ["the <em>kalman</em> gain"]
        assert hit.document.id == "d1"
        assert hit.explanation["matched_terms"] == ["kalman"]
        assert hit.explanation["missing_terms"] == ["gain"]
        assert results.retrieval["lexical"]["terms"] == ["kalman", "gain"]


class TestDocumentRow:
    def test_row_shape_and_embedding_last(self):
        pytest.importorskip("psycopg2")
        from engine.documents import Document, DocumentKind
        from engine.pg.store import _COLUMNS, document_row

        doc = Document(
            id="arxiv:1",
            source="arxiv",
            kind=DocumentKind.PAPER,
            title="t",
            abstract="a",
            authors=["X"],
            categories=["c"],
            published="2024-05-01",
            embedding=[0.1, 0.2],
        )
        row = document_row(doc)
        assert len(row) == len(_COLUMNS)
        assert row[0] == "arxiv:1"
        assert row[-1] == "[0.1,0.2]"  # embedding literal, last column


class _RecordingCursor:
    def __init__(self, rows=(), all_rows=None):
        self.rows = list(rows)
        self.all_rows = all_rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows if self.all_rows is None else self.all_rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _RecordingConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *a, **k):
        return self._cursor


class TestHashingVectorGate:
    """Hashing vectors may re-rank lexical matches, never surface others.

    Measured on a 312-document arXiv corpus: ungated, the hashing fallback's
    nearest neighbour for "kalman filter" shared no term with it and "zzqx
    plorfnog" returned every document; gated only on a shared term, "kalman
    filter state estimation" filled 100 results with documents that said
    "state" and left the one Kalman-filter paper out of the top six. The
    gate is the lexical rule itself, so a model-free deployment's vector
    retriever can only re-order documents the query explains.
    """

    def test_gate_is_the_lexical_rule_bound_as_parameters(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor(all_rows=[("d1",)])
        plan = _plan("x'); -- kalman filter state")
        ids = svc._knn_ids(_RecordingConn(cur), "[0.1]", "", {}, 10, gate=plan)
        assert ids == ["d1"]
        sql, params = cur.calls[0]
        match_sql, match_params = lexical_match_sql(plan)
        assert f" AND {match_sql} ORDER BY embedding <=>" in sql
        # The query text is bound, never interpolated.
        assert "x');" not in sql
        assert {k: params[k] for k in match_params} == match_params

    def test_no_gate_without_a_plan(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor()
        svc._knn_ids(_RecordingConn(cur), "[0.1]", "", {}, 10)
        sql, params = cur.calls[0]
        assert "search_vector" not in sql
        assert not any(k.startswith("lx_") for k in params)

    def test_a_gate_nothing_can_pass_runs_no_sql(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor()
        assert svc._knn_ids(
            _RecordingConn(cur), "[0.1]", "", {}, 10, gate=LexicalPlan()
        ) == []
        assert cur.calls == []

    @staticmethod
    def _gate_seen_by_search(monkeypatch, using_model):
        import contextlib
        import sys
        import types

        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        embedder = types.SimpleNamespace(
            encode=lambda text: [0.5, 0.5], using_model=using_model
        )
        svc = PgSearchService(_pg_config(), embedder=embedder)
        monkeypatch.setattr(svc.store, "has_vector", lambda: True)
        monkeypatch.setattr(svc, "_fts_ids", lambda *a, **k: [])
        plan = _plan("kalman filter")
        monkeypatch.setattr(svc, "_plan", lambda conn, query: plan)
        seen = {}

        def knn(conn, qvec, where_sql, params, limit, gate=None):
            seen["gate"] = gate
            return []

        monkeypatch.setattr(svc, "_knn_ids", knn)
        monkeypatch.setattr(
            svc, "_fetch", lambda conn, ids, cursor, plan=None: ({}, {}, {})
        )

        @contextlib.contextmanager
        def _connect():
            yield object()

        monkeypatch.setattr(svc.store, "connect", _connect)
        svc.search("kalman filter", mode="semantic", include_facets=False)
        return seen["gate"], plan

    def test_search_gates_hashing_vectors(self, monkeypatch):
        gate, plan = self._gate_seen_by_search(monkeypatch, False)
        assert gate is plan

    def test_search_leaves_model_vectors_ungated(self, monkeypatch):
        gate, _ = self._gate_seen_by_search(monkeypatch, True)
        assert gate is None

    def test_hybrid_without_a_semantic_model_runs_full_text_alone(
        self, monkeypatch
    ):
        import contextlib
        import sys
        import types

        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        def must_not_run(*args, **kwargs):
            raise AssertionError("term vectors are not fused in hybrid")

        embedder = types.SimpleNamespace(
            semantic=False, using_model=False, encode=must_not_run
        )
        svc = PgSearchService(_pg_config(), embedder=embedder)
        monkeypatch.setattr(svc.store, "has_vector", lambda: True)
        monkeypatch.setattr(svc, "_plan", lambda conn, query: _plan(query))
        monkeypatch.setattr(svc, "_fts_ids", lambda *a, **k: [])
        monkeypatch.setattr(svc, "_knn_ids", must_not_run)
        monkeypatch.setattr(
            svc, "_fetch", lambda conn, ids, cursor, plan=None: ({}, {}, {})
        )

        @contextlib.contextmanager
        def _connect():
            yield object()

        monkeypatch.setattr(svc.store, "connect", _connect)
        report = svc.search("kalman", mode="hybrid", include_facets=False).retrieval
        assert report["executed"] == ["fts"]
        assert report["skipped"] == ["knn"]
        assert report["unavailable"] == [] and report["degraded"] is False
        assert report["embedding"] == "hashing"


class TestRelated:
    def test_outage_is_a_backend_error_not_an_empty_list(self, monkeypatch):
        import contextlib
        import sys
        import types

        from engine.search import SearchBackendError

        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        svc = PgSearchService(
            _pg_config(), embedder=types.SimpleNamespace(semantic=False)
        )
        monkeypatch.setattr(svc.store, "has_vector", lambda: True)

        @contextlib.contextmanager
        def _down():
            raise RuntimeError("connection refused")
            yield  # pragma: no cover

        monkeypatch.setattr(svc.store, "connect", _down)
        with pytest.raises(SearchBackendError):
            svc.related("arxiv:x")

    def test_hashing_deployments_relate_documents_by_distinctive_words(
        self, monkeypatch
    ):
        import contextlib
        import sys
        import types

        fake = types.ModuleType("psycopg2")
        fake_extras = types.ModuleType("psycopg2.extras")
        fake_extras.RealDictCursor = object
        fake.extras = fake_extras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_extras)

        svc = PgSearchService(
            _pg_config(), embedder=types.SimpleNamespace(semantic=False)
        )
        monkeypatch.setattr(svc.store, "has_vector", lambda: True)
        calls = []
        results = [
            # The document's lexemes: (lexeme, fields, n, df, corpus).
            [
                ("kalman", ["A", "B"], 2, 3, 100),
                ("filter", ["B"], 1, 20, 100),
                ("onlyhere", ["A"], 1, 1, 100),
            ],
            [],  # the related documents
        ]

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                calls.append((sql, params))

            def fetchone(self):
                return {"id": "arxiv:x"}

            def fetchall(self):
                return results.pop(0)

        class _Conn:
            def cursor(self, *a, **k):
                return _Cur()

        @contextlib.contextmanager
        def _connect():
            yield _Conn()

        monkeypatch.setattr(svc.store, "connect", _connect)
        assert svc.related("arxiv:x") == []
        (base_sql, _), (lexeme_sql, lexeme_params), (like_sql, like_params) = (
            calls
        )
        # The base row is read without its (meaningless) hashing vector.
        assert "embedding" not in base_sql
        assert "unnest(d.search_vector)" in lexeme_sql
        assert lexeme_params == {"id": "arxiv:x"}
        assert "<=>" not in like_sql
        assert "ts_rank(search_vector, %(lx_t0)s::tsquery)" in like_sql
        assert like_sql.endswith(" DESC, id LIMIT %(size)s")
        # A word only this document carries relates it to nothing.
        assert like_params["lx_any"] == "( 'kalman' ) | ( 'filter' )"
        assert like_params["id"] == "arxiv:x" and like_params["size"] == 8


class TestDistinctiveTerms:
    def test_words_rank_by_frequency_in_the_document_times_rarity(self):
        plan = distinctive_terms(
            [
                ("system", ["A", "B", "B"], 3, 80, 100),  # frequent, common
                ("kalman", ["B"], 1, 2, 100),  # rare
                ("filter", ["A"], 1, 10, 100),
            ]
        )
        assert [t.term.text for t in plan.included] == [
            "kalman", "filter", "system",
        ]
        assert plan.required == 1 and plan.corpus_size == 100

    def test_title_words_outweigh_body_words_at_equal_rarity(self):
        plan = distinctive_terms(
            [("bodyword", ["C"], 1, 5, 100), ("titleword", ["A"], 1, 5, 100)]
        )
        assert [t.term.text for t in plan.included] == ["titleword", "bodyword"]

    def test_only_words_another_document_shares_can_relate_it(self):
        assert not distinctive_terms([("onlyhere", ["A"], 1, 1, 100)]).matchable
        assert not distinctive_terms([]).matchable

    def test_at_most_twelve_words_are_chosen_and_quoted_for_tsquery(self):
        rows = [(f"w{i:02d}", ["B"], 1, 2 + i, 1000) for i in range(30)]
        rows.append(("o'brien", ["A"], 1, 2, 1000))
        plan = distinctive_terms(rows)
        assert len(plan.included) == 12
        assert plan.included[0].tsquery == "'o''brien'"
        assert plan.included[1].term.text == "w00"


class TestFacetPopulation:
    def test_search_facets_count_the_fused_candidates(self):
        # Hybrid results include term-gated vector hits the strict lexical
        # query does not match; counting only the latter told the reader
        # "arxiv 1" above four arXiv results.
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor(rows=[(4,)], all_rows=[])
        svc._facets(
            _RecordingConn(cur),
            "source = ANY(%(f_sources)s::text[])",
            {"f_sources": ["arxiv"]},
            ids=["a", "b"],
        )
        count_sql, count_params = cur.calls[0]
        assert "id = ANY(%(facet_ids)s::text[])" in count_sql
        # The candidates were already filtered; the filter is not re-applied.
        assert "f_sources" not in count_sql
        assert "tsquery" not in count_sql
        assert count_params == {"facet_ids": ["a", "b"]}

    def test_browsing_facets_count_the_filtered_corpus(self):
        svc = PgSearchService(_pg_config())
        cur = _RecordingCursor(rows=[(9,)], all_rows=[])
        svc._facets(
            _RecordingConn(cur),
            "source = ANY(%(f_sources)s::text[])",
            {"f_sources": ["arxiv"]},
        )
        count_sql, _ = cur.calls[0]
        assert "f_sources" in count_sql
        assert "facet_ids" not in count_sql
