"""Environment-driven configuration for the engineering intelligence engine.

Configuration is read once from the environment and cached. Everything has a
sensible default so the engine can run in a bare dev environment (SQLite +
local Elasticsearch + CPU embeddings) without any ``.env`` edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _normalize_db_url(url: str) -> str:
    """Normalize a database URL for SQLAlchemy 1.4.

    Managed hosts (Render, Heroku, ...) hand out ``postgres://`` URLs, but
    SQLAlchemy 1.4+ only accepts ``postgresql://``. Rewrite the scheme so the
    collections store works out of the box on those platforms.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


@dataclass(frozen=True)
class EngineConfig:
    """Immutable configuration snapshot for the engine."""

    # --- Retrieval backend ---
    # "elasticsearch" (default) or "postgres" (Postgres FTS + pgvector), the
    # latter enabling a genuinely zero-cost deployment with no Elasticsearch.
    backend: str = field(
        default_factory=lambda: _env("ENGINE_BACKEND", "elasticsearch").lower()
    )

    # --- Elasticsearch ---
    elasticsearch_host: str = field(
        default_factory=lambda: _env(
            "ELASTICSEARCH_HOST", "http://elasticsearch:9200"
        )
    )
    index_name: str = field(
        default_factory=lambda: _env("ENGINE_INDEX", "engineering_docs")
    )

    # --- Embeddings ---
    embedding_model: str = field(
        default_factory=lambda: _env(
            "ENGINE_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    embedding_dims: int = field(
        default_factory=lambda: _env_int("ENGINE_EMBEDDING_DIMS", 384)
    )
    embedding_device: str = field(
        default_factory=lambda: _env("ENGINE_EMBEDDING_DEVICE", "cpu")
    )
    embedding_batch_size: int = field(
        default_factory=lambda: _env_int("ENGINE_EMBEDDING_BATCH_SIZE", 32)
    )
    # When true, never load the ML model; use the deterministic hashing fallback.
    embedding_force_fallback: bool = field(
        default_factory=lambda: _env_bool("ENGINE_EMBEDDING_FALLBACK", False)
    )

    # --- Hybrid retrieval ---
    # Reciprocal Rank Fusion constant. Larger => flatter contribution curve.
    rrf_k: int = field(default_factory=lambda: _env_int("ENGINE_RRF_K", 60))
    bm25_candidates: int = field(
        default_factory=lambda: _env_int("ENGINE_BM25_CANDIDATES", 100)
    )
    knn_candidates: int = field(
        default_factory=lambda: _env_int("ENGINE_KNN_CANDIDATES", 100)
    )
    knn_num_candidates: int = field(
        default_factory=lambda: _env_int("ENGINE_KNN_NUM_CANDIDATES", 200)
    )

    # --- Collections / bookmarks database ---
    database_url: str = field(
        default_factory=lambda: _normalize_db_url(
            _env(
                "ENGINE_DATABASE_URL",
                _env("DATABASE_URL", "sqlite:///engine_collections.db"),
            )
        )
    )

    # --- Optional local LLM for summaries ---
    llm_enabled: bool = field(
        default_factory=lambda: _env_bool("ENGINE_LLM_ENABLED", False)
    )
    llm_base_url: str = field(
        default_factory=lambda: _env(
            "ENGINE_LLM_BASE_URL", "http://localhost:11434"
        )
    )
    llm_model: str = field(
        default_factory=lambda: _env("ENGINE_LLM_MODEL", "llama3.1:8b")
    )

    # --- Ingestion ---
    user_agent: str = field(
        default_factory=lambda: _env(
            "ENGINE_USER_AGENT",
            "Vers3Dynamics-EngineeringIntelligence/0.1 (+https://vers3dynamics.io)",
        )
    )
    github_token: Optional[str] = field(
        default_factory=lambda: os.getenv("GITHUB_TOKEN") or None
    )
    request_timeout: int = field(
        default_factory=lambda: _env_int("ENGINE_REQUEST_TIMEOUT", 30)
    )

    # --- REST API ---
    # CORS allow-list for /api/v1. "*" allows any origin (fine for a public,
    # read-mostly API consumed by a static frontend); otherwise a comma-
    # separated list of exact origins, e.g.
    # "https://app.vercel.app,https://app.dappling.network".
    cors_origins: str = field(
        default_factory=lambda: _env("ENGINE_CORS_ORIGINS", "*")
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def cors_origin_for(self, request_origin: str) -> Optional[str]:
        """Resolve the ``Access-Control-Allow-Origin`` value for a request.

        Returns ``"*"`` when any origin is allowed, the echoed request origin
        when it is in the allow-list, or ``None`` when it is not allowed.
        """
        configured = (self.cors_origins or "").strip()
        if configured == "*" or configured == "":
            return "*"
        allowed = {o.strip() for o in configured.split(",") if o.strip()}
        return request_origin if request_origin in allowed else None


@lru_cache(maxsize=1)
def get_config() -> EngineConfig:
    """Return the cached engine configuration."""
    return EngineConfig()


def reset_config_cache() -> None:
    """Clear the cached config (used by tests that mutate the environment)."""
    get_config.cache_clear()
