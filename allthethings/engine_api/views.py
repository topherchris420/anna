"""Vers3Dynamics Engineering Intelligence — REST API (v1).

A clean JSON API over the hybrid search engine, summarizer, and collections
store. All endpoints are under ``/api/v1``.

Endpoints
---------
GET  /api/v1/health                          engine + index status
GET  /api/v1/sources                         registered ingestion sources
GET  /api/v1/search?q=...                     hybrid search (facets, filters, paging)
GET  /api/v1/document/<id>                    fetch one document
GET  /api/v1/document/<id>/related            related-document recommendations
POST /api/v1/summarize {q, ids?}              citation-first AI summary
POST /api/v1/compare {a, b}                   side-by-side document comparison
GET  /api/v1/collections?owner=...            list collections
POST /api/v1/collections {owner,name,...}     create a collection
GET  /api/v1/collections/<id>                 get a collection with bookmarks
DELETE /api/v1/collections/<id>?owner=...     delete a collection
POST /api/v1/collections/<id>/bookmarks       add a bookmark
DELETE /api/v1/collections/<id>/bookmarks/<document_id>   remove a bookmark
"""

from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify, request

from engine import __version__ as engine_version
from engine.config import get_config
from engine.search import SearchFilters, SearchService
from engine.summarize import Summarizer, compare_documents
from allthethings.engine_api.serialize import (
    document_to_dict,
    hit_to_dict,
    results_to_dict,
)

engine_api = Blueprint("engine_api", __name__, url_prefix="/api/v1")

_search_service: Optional[SearchService] = None
_summarizer: Optional[Summarizer] = None


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
# The API is designed to be called from a separate static frontend (e.g. hosted
# on Vercel or Dappling Network), so it emits CORS headers. Allowed origins are
# configured via ENGINE_CORS_ORIGINS ("*" by default). Flask auto-handles the
# preflight OPTIONS requests; this adds the headers to every API response.
@engine_api.after_request
def _add_cors_headers(response):
    origin = get_config().cors_origin_for(request.headers.get("Origin", ""))
    if origin is not None:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization"
        )
        response.headers["Access-Control-Max-Age"] = "86400"
        if origin != "*":
            response.headers.add("Vary", "Origin")
    return response


def _service() -> SearchService:
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service


def _summary() -> Summarizer:
    global _summarizer
    if _summarizer is None:
        _summarizer = Summarizer()
    return _summarizer


def _store():
    # Imported lazily: collections require SQLAlchemy which may be absent.
    from engine.collections import get_store

    return get_store()


def _bool_arg(name: str) -> Optional[bool]:
    raw = request.args.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name: str) -> Optional[int]:
    raw = request.args.get(name)
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _filters_from_request() -> SearchFilters:
    return SearchFilters(
        sources=request.args.getlist("source"),
        kinds=request.args.getlist("kind"),
        categories=request.args.getlist("category"),
        language=request.args.getlist("language"),
        version=request.args.get("version") or None,
        has_code=_bool_arg("has_code"),
        has_equations=_bool_arg("has_equations"),
        year_from=_int_arg("year_from"),
        year_to=_int_arg("year_to"),
    )


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
@engine_api.get("/health")
def health():
    from engine import index as es_index

    config = get_config()
    status = {
        "service": "vers3dynamics-engineering-intelligence",
        "engine_version": engine_version,
        "index": config.index_name,
        "embedding_model": config.embedding_model,
    }
    try:
        status["index_exists"] = es_index.index_exists(config)
        status["document_count"] = es_index.count(config)
        status["elasticsearch"] = "ok"
    except Exception as exc:
        status["elasticsearch"] = f"unavailable: {exc}"
        status["index_exists"] = False
        status["document_count"] = 0
    return jsonify(status)


@engine_api.get("/sources")
def sources():
    from engine.ingest import all_plugins

    return jsonify({"sources": [p.info() for p in all_plugins()]})


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #
@engine_api.get("/search")
def search():
    query = request.args.get("q", "")
    mode = request.args.get("mode", "hybrid")
    if mode not in ("hybrid", "bm25", "semantic"):
        mode = "hybrid"
    page = _int_arg("page") or 1
    per_page = _int_arg("per_page") or 20
    try:
        results = _service().search(
            query,
            filters=_filters_from_request(),
            mode=mode,
            page=page,
            per_page=per_page,
        )
        return jsonify(results_to_dict(results))
    except Exception as exc:
        return (
            jsonify(
                {"error": str(exc), "hits": [], "total": 0, "query": query}
            ),
            503,
        )


@engine_api.get("/document/<path:doc_id>/related")
def related(doc_id: str):
    try:
        hits = _service().related(doc_id, size=_int_arg("size") or 8)
        return jsonify(
            {"id": doc_id, "related": [hit_to_dict(h) for h in hits]}
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "related": []}), 503


@engine_api.get("/document/<path:doc_id>")
def document(doc_id: str):
    from engine import index as es_index

    doc = es_index.get_document(doc_id)
    if doc is None:
        return jsonify({"error": "not found", "id": doc_id}), 404
    return jsonify(document_to_dict(doc, full=True))


# --------------------------------------------------------------------------- #
# Summaries & comparison
# --------------------------------------------------------------------------- #
@engine_api.post("/summarize")
def summarize():
    payload = request.get_json(silent=True) or {}
    query = (payload.get("q") or "").strip()
    ids = payload.get("ids") or []
    if not query:
        return jsonify({"error": "q is required"}), 400

    from engine import index as es_index

    try:
        if ids:
            docs = [d for d in (es_index.get_document(i) for i in ids) if d]
        else:
            results = _service().search(query, per_page=6, include_facets=False)
            docs = [h.document for h in results.hits]
        summary = _summary().summarize(query, docs)
        return jsonify(summary.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@engine_api.post("/compare")
def compare():
    payload = request.get_json(silent=True) or {}
    id_a, id_b = payload.get("a"), payload.get("b")
    if not id_a or not id_b:
        return (
            jsonify({"error": "both 'a' and 'b' document ids are required"}),
            400,
        )

    from engine import index as es_index

    doc_a = es_index.get_document(id_a)
    doc_b = es_index.get_document(id_b)
    if doc_a is None or doc_b is None:
        missing = [i for i, d in ((id_a, doc_a), (id_b, doc_b)) if d is None]
        return (
            jsonify({"error": "document(s) not found", "missing": missing}),
            404,
        )
    return jsonify(compare_documents(doc_a, doc_b))


# --------------------------------------------------------------------------- #
# Collections & bookmarks
# --------------------------------------------------------------------------- #
def _owner() -> str:
    return (
        request.args.get("owner")
        or (request.get_json(silent=True) or {}).get("owner")
        or "anonymous"
    )


@engine_api.get("/collections")
def list_collections():
    try:
        return jsonify({"collections": _store().list_collections(_owner())})
    except Exception as exc:
        return jsonify({"error": str(exc), "collections": []}), 503


@engine_api.post("/collections")
def create_collection():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        coll = _store().create_collection(
            owner=_owner(),
            name=name,
            description=payload.get("description", ""),
            is_public=bool(payload.get("is_public", False)),
        )
        return jsonify(coll), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@engine_api.get("/collections/<int:collection_id>")
def get_collection(collection_id: int):
    coll = _store().get_collection(collection_id, owner=_owner())
    if coll is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(coll)


@engine_api.delete("/collections/<int:collection_id>")
def delete_collection(collection_id: int):
    ok = _store().delete_collection(collection_id, owner=_owner())
    return (
        (jsonify({"deleted": True}), 200)
        if ok
        else (jsonify({"error": "not found"}), 404)
    )


@engine_api.post("/collections/<int:collection_id>/bookmarks")
def add_bookmark(collection_id: int):
    payload = request.get_json(silent=True) or {}
    document_id = payload.get("document_id")
    if not document_id:
        return jsonify({"error": "document_id is required"}), 400
    bm = _store().add_bookmark(
        collection_id,
        document_id,
        owner=_owner(),
        title=payload.get("title", ""),
        url=payload.get("url", ""),
        source=payload.get("source", ""),
        note=payload.get("note", ""),
    )
    if bm is None:
        return jsonify({"error": "collection not found"}), 404
    return jsonify(bm), 201


@engine_api.delete(
    "/collections/<int:collection_id>/bookmarks/<path:document_id>"
)
def remove_bookmark(collection_id: int, document_id: str):
    ok = _store().remove_bookmark(collection_id, document_id, owner=_owner())
    return (
        (jsonify({"deleted": True}), 200)
        if ok
        else (jsonify({"error": "not found"}), 404)
    )
