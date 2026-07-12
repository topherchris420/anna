"""Unit tests for engine configuration helpers."""

from engine.config import EngineConfig, _normalize_db_url


class TestNormalizeDbUrl:
    def test_rewrites_postgres_scheme(self):
        # Managed hosts hand out postgres://, which SQLAlchemy 1.4 rejects.
        assert (
            _normalize_db_url("postgres://u:p@host:5432/db")
            == "postgresql://u:p@host:5432/db"
        )

    def test_leaves_postgresql_scheme(self):
        url = "postgresql+psycopg2://u:p@host/db"
        assert _normalize_db_url(url) == url

    def test_leaves_sqlite(self):
        assert _normalize_db_url("sqlite:///x.db") == "sqlite:///x.db"


class TestEngineConfig:
    def test_database_url_normalized_from_env(self, monkeypatch):
        monkeypatch.setenv("ENGINE_DATABASE_URL", "postgres://u:p@h/db")
        cfg = EngineConfig()
        assert cfg.database_url.startswith("postgresql://")

    def test_defaults(self, monkeypatch):
        for var in ("ENGINE_DATABASE_URL", "DATABASE_URL"):
            monkeypatch.delenv(var, raising=False)
        cfg = EngineConfig()
        assert cfg.database_url.startswith("sqlite")
        assert cfg.index_name == "engineering_docs"
        assert cfg.embedding_dims == 384
