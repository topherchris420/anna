"""Unit tests for the Postgres backend (pure logic; no live database)."""

import datetime

import pytest

from engine import backend
from engine.config import EngineConfig
from engine.pg.store import PgStore, parse_date, psycopg2_dsn, vector_literal
from engine.pg.search import PgSearchService
from engine.search import SearchFilters, SearchService


def _pg_config():
    return EngineConfig(backend="postgres", database_url="postgresql://u:p@h/db")


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
        assert psycopg2_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"
        assert psycopg2_dsn("postgres://u:p@h/db") == "postgresql://u:p@h/db"
        assert psycopg2_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"


class TestFilterSql:
    def _svc(self):
        return PgSearchService(_pg_config())

    def test_empty_filters(self):
        sql, params = self._svc()._where(SearchFilters())
        assert sql == "" and params == {}

    def test_term_filters(self):
        sql, params = self._svc()._where(
            SearchFilters(sources=["arxiv"], kinds=["paper"], categories=["cs.LG"])
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
            svc, "_fts_ids",
            lambda *a, **k: calls.__setitem__("fts", calls["fts"] + 1) or [],
        )
        monkeypatch.setattr(
            svc, "_knn_ids",
            lambda *a, **k: calls.__setitem__("knn", calls["knn"] + 1) or [],
        )

        class _Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, *a, **k): pass
            def fetchone(self): return [0]
            def fetchall(self): return []

        class _Conn:
            def cursor(self, *a, **k): return _Cur()

        import contextlib

        @contextlib.contextmanager
        def _connect():
            yield _Conn()

        monkeypatch.setattr(svc.store, "connect", _connect)

        svc.search("kalman filter", mode="semantic", include_facets=False)
        assert calls["fts"] == 1  # degraded to full-text
        assert calls["knn"] == 0  # kNN skipped (no vector column)


class TestDocumentRow:
    def test_row_shape_and_embedding_last(self):
        pytest.importorskip("psycopg2")
        from engine.documents import Document, DocumentKind
        from engine.pg.store import _COLUMNS, document_row

        doc = Document(
            id="arxiv:1", source="arxiv", kind=DocumentKind.PAPER, title="t",
            abstract="a", authors=["X"], categories=["c"], published="2024-05-01",
            embedding=[0.1, 0.2],
        )
        row = document_row(doc)
        assert len(row) == len(_COLUMNS)
        assert row[0] == "arxiv:1"
        assert row[-1] == "[0.1,0.2]"  # embedding literal, last column
