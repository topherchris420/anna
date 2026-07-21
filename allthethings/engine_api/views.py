"""Vers3Dynamics Engineering Intelligence — REST API (v1).

A clean JSON API over the hybrid search engine, summarizer, and collections
store. All endpoints are under ``/api/v1``.

Endpoints
---------
GET  /api/v1/health                          engine + index status
GET  /api/v1/sources                         registered ingestion sources
GET|POST /api/v1/search                       hybrid search (query string or JSON body)
POST /api/v1/agent/search                     LLM-agent search (flat, context-ready)
GET  /api/v1/agent/openapi.json               OpenAPI spec for the agent endpoint
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

from typing import Any, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from engine import __version__ as engine_version
from engine import backend
from engine.config import get_config
from engine.search import SearchFilters
from engine.summarize import Summarizer, compare_documents
from allthethings.engine_api.agent_search import (
    agent_error_body,
    parse_agent_search_request,
    resolve_domain_filter,
    results_to_agent_dict,
)
from allthethings.engine_api.agent_spec import AGENT_OPENAPI_SPEC
from allthethings.engine_api.serialize import (
    document_to_dict,
    hit_to_dict,
    results_to_dict,
)

engine_api = Blueprint("engine_api", __name__, url_prefix="/api/v1")

_search_service = None
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


def _service():
    global _search_service
    if _search_service is None:
        _search_service = backend.get_search_service()
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


# --- JSON body coercion (for POST /search) --------------------------------- #
def _coerce_int(value: Any, default: Optional[int]) -> Optional[int]:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_str_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def _parse_search_request() -> Tuple[str, str, int, int, SearchFilters]:
    """Read search parameters from a JSON body (POST) or the query string (GET).

    Accepted JSON keys (all optional except the query):
      query|q, mode, page, per_page, source(s), kind(s), category(ies),
      language(s), version, has_code, has_equations, year_from, year_to.
    """
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        query = str(body.get("query") or body.get("q") or "").strip()
        mode = body.get("mode", "hybrid")
        page = _coerce_int(body.get("page"), 1) or 1
        per_page = _coerce_int(body.get("per_page"), 20) or 20
        filters = SearchFilters(
            sources=_as_str_list(body.get("sources", body.get("source"))),
            kinds=_as_str_list(body.get("kinds", body.get("kind"))),
            categories=_as_str_list(body.get("categories", body.get("category"))),
            language=_as_str_list(body.get("language", body.get("languages"))),
            version=body.get("version") or None,
            has_code=_coerce_bool(body.get("has_code")),
            has_equations=_coerce_bool(body.get("has_equations")),
            year_from=_coerce_int(body.get("year_from"), None),
            year_to=_coerce_int(body.get("year_to"), None),
        )
    else:
        query = (request.args.get("q") or request.args.get("query") or "").strip()
        mode = request.args.get("mode", "hybrid")
        page = _int_arg("page") or 1
        per_page = _int_arg("per_page") or 20
        filters = _filters_from_request()

    if mode not in ("hybrid", "bm25", "semantic"):
        mode = "hybrid"
    return query, mode, page, per_page, filters


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
@engine_api.get("/health")
def health():
    from engine import backend as es_index

    config = get_config()
    status = {
        "service": "vers3dynamics-engineering-intelligence",
        "engine_version": engine_version,
        "backend": config.backend,
        "index": config.index_name,
        "embedding_model": config.embedding_model,
    }
    try:
        status["index_exists"] = es_index.index_exists(config)
        status["document_count"] = es_index.count(config)
        status["backend_status"] = "ok"
        if config.backend == "postgres":
            # pgvector is optional on free plans; report whether semantic kNN
            # is available or the engine is running full-text-only.
            from engine.pg.store import get_store

            has_vec = get_store(config).has_vector()
            status["retrieval"] = "hybrid" if has_vec else "fulltext-only"
            status["vector_search"] = has_vec
    except Exception as exc:
        status["backend_status"] = f"unavailable: {exc}"
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
@engine_api.route("/search", methods=["GET", "POST"])
def search():
    """Hybrid search. Reads params from a JSON body (POST) or query string (GET).

    Returns structured JSON (no server-side templates): result metadata plus a
    document content block per hit.
    """
    query, mode, page, per_page, filters = _parse_search_request()
    try:
        results = _service().search(
            query,
            filters=filters,
            mode=mode,
            page=page,
            per_page=per_page,
        )
        return jsonify(results_to_dict(results))
    except Exception as exc:
        # Elasticsearch unreachable / query failure -> 503 with an empty,
        # well-formed body so clients can render a graceful error state.
        return (
            jsonify(
                {"error": str(exc), "hits": [], "total": 0, "query": query}
            ),
            503,
        )


@engine_api.post("/agent/search")
def agent_search():
    """LLM-agent search: strict contract, flat citation-ready results.

    Unlike /search (which serves the human-facing frontend: facets, paging,
    highlight markup), this returns compact plain-text chunks with 0–1
    relevance scores. The contract lives in
    allthethings.engine_api.agent_search; the machine-readable spec is served
    at /api/v1/agent/openapi.json.
    """
    parsed, error = parse_agent_search_request(request.get_json(silent=True))
    if error is not None:
        return jsonify(agent_error_body(error)), 400
    try:
        results = _service().search(
            parsed.query,
            filters=resolve_domain_filter(parsed.domain_filter),
            mode="hybrid",
            page=1,
            per_page=parsed.limit,
            include_facets=False,
        )
    except Exception as exc:
        return jsonify(agent_error_body(str(exc))), 503
    return jsonify(results_to_agent_dict(results, min_score=parsed.min_score))


@engine_api.get("/agent/openapi.json")
def agent_openapi():
    """Machine-readable contract for the agent search endpoint."""
    return jsonify(AGENT_OPENAPI_SPEC)


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
    from engine import backend as es_index

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

    from engine import backend as es_index

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

    from engine import backend as es_index

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
