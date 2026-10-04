"""JSON serialization helpers for the REST API.

Keeps API payloads stable and free of internal fields (notably the raw
embedding vector, which is large and useless to clients).
"""

from __future__ import annotations

from typing import Any, Dict

from engine.documents import Document
from engine.retrieval import normalize_relevance
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


def hit_to_dict(hit: SearchHit, score_ceiling: float = 0.0) -> Dict[str, Any]:
    """Serialize one hit.

    ``relevance`` (0–1, the hit's share of the best score the executed
    retrievers could assign) is only present when the caller knows that
    ceiling — search results do; related-document lists carry raw backend
    scores and omit it rather than invent a scale.
    """
    data: Dict[str, Any] = {
        "score": hit.score,
        "highlights": hit.highlights,
        "explanation": hit.explanation,
        "document": document_to_dict(hit.document),
    }
    if score_ceiling > 0:
        data["relevance"] = normalize_relevance(hit.score, score_ceiling)
    return data


def results_to_dict(results: SearchResults) -> Dict[str, Any]:
    return {
        "query": results.query,
        "mode": results.mode,
        "total": results.total,
        "page": results.page,
        "per_page": results.per_page,
        "took_ms": results.took_ms,
        "facets": results.facets,
        "retrieval": results.retrieval,
        "hits": [hit_to_dict(h, results.score_ceiling) for h in results.hits],
    }
