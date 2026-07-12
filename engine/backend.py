"""Retrieval-backend facade.

A thin dispatch layer so the rest of the app (API, web UI, CLI, ingestion) is
agnostic to whether search runs on **Elasticsearch** or **PostgreSQL
(FTS + pgvector)**. The backend is chosen by ``ENGINE_BACKEND``
(``elasticsearch`` default, or ``postgres``).

Exposes the same surface the Elasticsearch ``engine.index`` module did
(``create_index``, ``reset_index``, ``index_exists``, ``count``, ``bulk_index``,
``get_document``) plus ``get_search_service()``, so call sites only swap their
import.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document


def backend_name(config: Optional[EngineConfig] = None) -> str:
    return (config or get_config()).backend


def _is_postgres(config: Optional[EngineConfig]) -> bool:
    return backend_name(config) == "postgres"


# --------------------------------------------------------------------------- #
# Index management
# --------------------------------------------------------------------------- #
def create_index(config: Optional[EngineConfig] = None, recreate: bool = False) -> None:
    if _is_postgres(config):
        from engine.pg.store import get_store

        get_store(config).create_index(recreate=recreate)
        return
    from engine import index as es

    es.create_index(config, recreate=recreate)


def reset_index(config: Optional[EngineConfig] = None) -> None:
    create_index(config, recreate=True)


def index_exists(config: Optional[EngineConfig] = None) -> bool:
    if _is_postgres(config):
        from engine.pg.store import get_store

        return get_store(config).index_exists()
    from engine import index as es

    return es.index_exists(config)


def count(config: Optional[EngineConfig] = None) -> int:
    if _is_postgres(config):
        from engine.pg.store import get_store

        return get_store(config).count()
    from engine import index as es

    return es.count(config)


def bulk_index(
    documents: Iterable[Document],
    config: Optional[EngineConfig] = None,
    refresh: bool = False,
) -> Tuple[int, List[Any]]:
    if _is_postgres(config):
        from engine.pg.store import get_store

        return get_store(config).bulk_index(documents)
    from engine import index as es

    return es.bulk_index(documents, config, refresh=refresh)


def get_document(
    doc_id: str, config: Optional[EngineConfig] = None
) -> Optional[Document]:
    if _is_postgres(config):
        from engine.pg.store import get_store

        return get_store(config).get_document(doc_id)
    from engine import index as es

    return es.get_document(doc_id, config)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
def get_search_service(config: Optional[EngineConfig] = None):
    """Return the active backend's search service (same interface either way)."""
    if _is_postgres(config):
        from engine.pg.search import PgSearchService

        return PgSearchService(config)
    from engine.search import SearchService

    return SearchService(config)
