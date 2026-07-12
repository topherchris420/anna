"""JSON serialization helpers for the REST API.

Keeps API payloads stable and free of internal fields (notably the raw
embedding vector, which is large and useless to clients).
"""

from __future__ import annotations

from typing import Any, Dict

from engine.documents import Document
from engine.search import SearchHit, SearchResults


def document_to_dict(doc: Document, full: bool = False) -> Dict[str, Any]:
    data = {
        "id": doc.id,
        "source": doc.source,
        "kind": str(doc.kind),
        "title": doc.title,
        "abstract": doc.abstract,
        "url": doc.url,
        "pdf_url": doc.pdf_url,
        "authors": doc.authors,
        "published": doc.published,
        "updated": doc.updated,
        "version": doc.version,
        "categories": doc.categories,
        "tags": doc.tags,
        "language": doc.language,
        "identifiers": doc.identifiers,
        "has_equations": doc.has_equations,
        "has_code": doc.has_code,
        "popularity": doc.popularity,
    }
    if full:
        data["body"] = doc.body
        data["equations"] = doc.equations
        data["extra"] = doc.extra
    return data


def hit_to_dict(hit: SearchHit) -> Dict[str, Any]:
    return {
        "score": hit.score,
        "highlights": hit.highlights,
        "document": document_to_dict(hit.document),
    }


def results_to_dict(results: SearchResults) -> Dict[str, Any]:
    return {
        "query": results.query,
        "mode": results.mode,
        "total": results.total,
        "page": results.page,
        "per_page": results.per_page,
        "took_ms": results.took_ms,
        "facets": results.facets,
        "hits": [hit_to_dict(h) for h in results.hits],
    }
