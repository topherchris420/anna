"""Elasticsearch hybrid index management.

Defines the ``engineering_docs`` index that powers *both* lexical (BM25) and
semantic (dense-vector kNN) retrieval from a single document, and provides
create / reset / bulk-index helpers used by the ingestion pipeline and CLI.

The Elasticsearch client is created directly here (rather than via the Flask
extension) so the same code path works from web requests, Celery workers, and
one-off CLI ingestion jobs.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document

_client_cache: Dict[str, Any] = {}


def get_client(config: Optional[EngineConfig] = None):
    """Return a cached Elasticsearch client for the configured host."""
    config = config or get_config()
    host = config.elasticsearch_host
    if host not in _client_cache:
        from elasticsearch import Elasticsearch  # lazy import

        _client_cache[host] = Elasticsearch(
            host, request_timeout=config.request_timeout
        )
    return _client_cache[host]


def build_mappings(config: EngineConfig) -> Dict[str, Any]:
    """Elasticsearch mappings for the hybrid engineering-docs index."""
    return {
        "dynamic": "false",
        "properties": {
            "source": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "title": {
                "type": "text",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "abstract": {"type": "text"},
            "body": {"type": "text"},
            "search_text": {"type": "text"},
            "equations": {"type": "text"},
            "url": {"type": "keyword", "index": False},
            "pdf_url": {"type": "keyword", "index": False},
            "authors": {
                "type": "text",
                "fields": {"raw": {"type": "keyword", "ignore_above": 256}},
            },
            "published": {
                "type": "date",
                "format": "strict_date_optional_time||epoch_millis||yyyy-MM||yyyy",
                "ignore_malformed": True,
            },
            "updated": {
                "type": "date",
                "format": "strict_date_optional_time||epoch_millis||yyyy-MM||yyyy",
                "ignore_malformed": True,
            },
            "version": {"type": "keyword"},
            "categories": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "language": {"type": "keyword"},
            "identifiers": {"type": "object", "enabled": True},
            "has_equations": {"type": "boolean"},
            "has_code": {"type": "boolean"},
            "popularity": {"type": "float"},
            "indexed_at": {
                "type": "date",
                "format": "strict_date_optional_time||epoch_millis",
                "ignore_malformed": True,
            },
            "embedding": {
                "type": "dense_vector",
                "dims": config.embedding_dims,
                "index": True,
                "similarity": "cosine",
            },
        },
    }


def index_exists(config: Optional[EngineConfig] = None) -> bool:
    config = config or get_config()
    client = get_client(config)
    return bool(client.indices.exists(index=config.index_name))


def create_index(
    config: Optional[EngineConfig] = None, recreate: bool = False
) -> None:
    """Create the engineering-docs index (optionally dropping it first)."""
    config = config or get_config()
    client = get_client(config)
    if recreate:
        client.options(ignore_status=[400, 404]).indices.delete(
            index=config.index_name
        )
    if client.indices.exists(index=config.index_name):
        return
    client.indices.create(
        index=config.index_name,
        mappings=build_mappings(config),
        settings={
            "index.number_of_replicas": 0,
            "index.refresh_interval": "5s",
        },
    )


def reset_index(config: Optional[EngineConfig] = None) -> None:
    """Drop and recreate the index."""
    create_index(config, recreate=True)


def bulk_index(
    documents: Iterable[Document],
    config: Optional[EngineConfig] = None,
    refresh: bool = False,
) -> Tuple[int, List[Any]]:
    """Bulk-index an iterable of :class:`Document` objects.

    Returns ``(success_count, errors)``.
    """
    from elasticsearch import helpers  # lazy import

    config = config or get_config()
    client = get_client(config)

    def actions():
        for doc in documents:
            yield {
                "_op_type": "index",
                "_index": config.index_name,
                "_id": doc.id,
                "_source": doc.to_index_doc(),
            }

    success, errors = helpers.bulk(
        client, actions(), raise_on_error=False, request_timeout=120
    )
    if refresh:
        client.indices.refresh(index=config.index_name)
    return success, errors


def get_document(doc_id: str, config: Optional[EngineConfig] = None) -> Optional[Document]:
    config = config or get_config()
    client = get_client(config)
    from elasticsearch import NotFoundError  # lazy import

    try:
        resp = client.get(index=config.index_name, id=doc_id)
    except NotFoundError:
        return None
    return Document.from_source({"_id": resp["_id"], **resp["_source"]})


def count(config: Optional[EngineConfig] = None) -> int:
    config = config or get_config()
    client = get_client(config)
    if not client.indices.exists(index=config.index_name):
        return 0
    return int(client.count(index=config.index_name)["count"])
