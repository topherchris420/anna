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


# Prose is analyzed as English — lower-cased, stop words dropped and stemmed,
# so "estimate" finds "estimation" — which is how Postgres builds its
# ``search_vector`` too. Names, equations and identifiers keep the standard
# analyzer: stemming an author's name or a LaTeX token helps nobody. On the
# repository's judged queries this alone moved lexical nDCG@10 from 0.712 to
# 0.756 (test/relevance/README.md).
PROSE_ANALYZER = "english"
PROSE_FIELDS = ("title", "abstract", "body", "search_text")
_SETTINGS = {"index.number_of_replicas": 0, "index.refresh_interval": "5s"}


def build_mappings(config: EngineConfig) -> Dict[str, Any]:
    """Elasticsearch mappings for the hybrid engineering-docs index."""
    return {
        "dynamic": "false",
        "properties": {
            "source": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": PROSE_ANALYZER,
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "abstract": {"type": "text", "analyzer": PROSE_ANALYZER},
            "body": {"type": "text", "analyzer": PROSE_ANALYZER},
            "search_text": {"type": "text", "analyzer": PROSE_ANALYZER},
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
        settings=_SETTINGS,
    )


def reset_index(config: Optional[EngineConfig] = None) -> None:
    """Drop and recreate the index."""
    create_index(config, recreate=True)


def prose_analyzer(config: Optional[EngineConfig] = None) -> Optional[str]:
    """The analyzer the index applies to prose, or None without an index.

    An index created before English analysis existed reports "standard":
    it still works, but without stemming — :func:`upgrade_index` fixes it.
    """
    config = config or get_config()
    client = get_client(config)
    if not client.indices.exists(index=config.index_name):
        return None
    mapping = client.indices.get_mapping(index=config.index_name)
    properties = next(iter(mapping.values()))["mappings"].get("properties", {})
    return properties.get("title", {}).get("analyzer", "standard")


def upgrade_index(
    config: Optional[EngineConfig] = None, echo=lambda message: None
) -> Dict[str, Any]:
    """Rebuild the index with the current mapping, keeping every document.

    Documents (vectors included — they live in ``_source``) are copied to a
    scratch index, the index is recreated with :func:`build_mappings`, and
    the documents are copied back and re-analyzed. Nothing is re-fetched or
    re-embedded. The original is deleted only after the scratch copy holds
    every document, and the scratch copy only after the index holds them
    again; on any failure the error names the index that still has them.
    """
    config = config or get_config()
    client = get_client(config)
    name = config.index_name
    if not client.indices.exists(index=name):
        return {"index": name, "upgraded": False, "reason": "no index"}
    before = prose_analyzer(config)
    if before == PROSE_ANALYZER:
        return {"index": name, "upgraded": False, "reason": "already current"}

    def copy(source: str, dest: str) -> int:
        client.options(request_timeout=3600).reindex(
            source={"index": source},
            dest={"index": dest},
            refresh=True,
            wait_for_completion=True,
        )
        return int(client.count(index=dest)["count"])

    total = int(client.count(index=name)["count"])
    scratch = f"{name}__upgrade"
    client.options(ignore_status=[404]).indices.delete(index=scratch)
    client.indices.create(
        index=scratch, mappings=build_mappings(config), settings=_SETTINGS
    )
    echo(f"copying {total} documents to {scratch}…")
    if copy(name, scratch) != total:
        raise RuntimeError(
            f"{scratch} received fewer than {total} documents; {name} is "
            f"untouched"
        )
    echo(f"recreating {name} with {PROSE_ANALYZER} analysis…")
    client.indices.delete(index=name)
    client.indices.create(
        index=name, mappings=build_mappings(config), settings=_SETTINGS
    )
    echo(f"copying {total} documents back…")
    if copy(scratch, name) != total:
        raise RuntimeError(
            f"{name} did not receive all {total} documents back; they are "
            f"all still in {scratch}"
        )
    client.indices.delete(index=scratch)
    return {
        "index": name,
        "upgraded": True,
        "documents": total,
        "analyzer": {"before": before, "after": PROSE_ANALYZER},
    }


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


def get_document(
    doc_id: str, config: Optional[EngineConfig] = None
) -> Optional[Document]:
    config = config or get_config()
    client = get_client(config)
    from elasticsearch import NotFoundError  # lazy import

    try:
        resp = client.get(index=config.index_name, id=doc_id)
    except NotFoundError:
        return None
    return Document.from_source({"_id": resp["_id"], **resp["_source"]})


def get_documents(
    doc_ids: List[str], config: Optional[EngineConfig] = None
) -> Dict[str, Document]:
    """Fetch several documents in one ``mget``; absent ids are omitted."""
    config = config or get_config()
    ids = list(dict.fromkeys(i for i in doc_ids if i))
    if not ids:
        return {}
    resp = get_client(config).mget(
        index=config.index_name, ids=ids, _source_excludes=["embedding"]
    )
    return {
        d["_id"]: Document.from_source({"_id": d["_id"], **d["_source"]})
        for d in resp.get("docs", [])
        if d.get("found")
    }


def count(config: Optional[EngineConfig] = None) -> int:
    config = config or get_config()
    client = get_client(config)
    if not client.indices.exists(index=config.index_name):
        return 0
    return int(client.count(index=config.index_name)["count"])
