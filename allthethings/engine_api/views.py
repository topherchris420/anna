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
POST /api/v1/evidence/verify {record|packet}  re-check a saved research record
GET  /api/v1/collections?owner=...            list collections (&with_bookmarks=1)
POST /api/v1/collections {owner,name,...}     create a collection
GET  /api/v1/collections/<id>                 get a collection with bookmarks
DELETE /api/v1/collections/<id>?owner=...     delete a collection
POST /api/v1/collections/<id>/bookmarks       add a bookmark
DELETE /api/v1/collections/<id>/bookmarks/<document_id>   remove a bookmark
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

from engine import __version__ as engine_version
from engine import backend, records
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
log = logging.getLogger(__name__)

_search_service = None
_summarizer: Optional[Summarizer] = None

# Document ids are short, opaque strings ("arxiv:8bbe0f7de8c6440b").
MAX_DOCUMENT_ID = 512
# Search requests: the query length /summarize already enforces, and paging
# bounds (100 per page, 50 pages).
MAX_QUERY_CHARS = 2000
MAX_PER_PAGE = 100
MAX_PAGE = 50


def _failure_reason(exc: BaseException) -> str:
    """A client-safe name for a backend failure.

    Driver messages carry database hostnames, user names and SQL, and some
    endpoints (``/health``) are public. Clients get the failing class — the
    root cause's when the engine wrapped one — and the full error goes to the
    server log, where an operator can read it.
    """
    log.warning("engine backend failure: %r", exc)
    root = exc.__cause__ or exc
    return type(root).__name__


def _unavailable(what: str, exc: BaseException, **extra: Any):
    """HTTP 503 for an unreachable index or database, without leaking it."""
    body: Dict[str, Any] = {
        "error": f"{what} unavailable ({_failure_reason(exc)})"
    }
    body.update(extra)
    return jsonify(body), 503


def _valid_document_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= MAX_DOCUMENT_ID
    )


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
        response.headers[
            "Access-Control-Allow-Methods"
        ] = "GET, POST, DELETE, OPTIONS"
        response.headers[
            "Access-Control-Allow-Headers"
        ] = "Content-Type, Authorization"
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
            categories=_as_str_list(
                body.get("categories", body.get("category"))
            ),
            language=_as_str_list(body.get("language", body.get("languages"))),
            version=body.get("version") or None,
            has_code=_coerce_bool(body.get("has_code")),
            has_equations=_coerce_bool(body.get("has_equations")),
            year_from=_coerce_int(body.get("year_from"), None),
            year_to=_coerce_int(body.get("year_to"), None),
        )
    else:
        query = (
            request.args.get("q") or request.args.get("query") or ""
        ).strip()
        mode = request.args.get("mode", "hybrid")
        page = _int_arg("page") or 1
        per_page = _int_arg("per_page") or 20
        filters = _filters_from_request()

    if mode not in ("hybrid", "bm25", "semantic"):
        mode = "hybrid"
    # Deep pages widen every retriever's candidate LIMIT (page * per_page):
    # keep requests inside what a reader can page through.
    per_page = max(1, min(per_page, MAX_PER_PAGE))
    page = max(1, min(page, MAX_PAGE))
    for bound in ("year_from", "year_to"):
        year = getattr(filters, bound)
        if year is not None and not 1 <= year <= 9999:
            setattr(filters, bound, None)  # not a calendar year: ignore it
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
        if config.backend == "postgres":
            from engine.pg.store import get_store

            exists, total, has_vector = get_store(config).status()
        else:
            exists = es_index.index_exists(config)
            total = es_index.count(config)
            has_vector = True
        status["index_exists"] = exists
        status["document_count"] = total
        status["backend_status"] = "ok"
        status["ready"] = bool(exists)
        status["retrieval"] = "hybrid" if has_vector else "fulltext-only"
        status["vector_search"] = has_vector
    except Exception as exc:
        status["backend_status"] = f"unavailable: {_failure_reason(exc)}"
        status["index_exists"] = False
        status["document_count"] = 0
        status["ready"] = False
        status["retrieval"] = "unavailable"
        status["vector_search"] = False
    # What the vectors actually are. The hashing fallback keeps the kNN
    # plumbing running but is not semantic; clients label it accordingly
    # instead of advertising "Semantic" search a deployment cannot do.
    # Reported without loading a model: until the first query loads it, a
    # model deployment says "not-loaded" and clients learn the rest from
    # the search response's retrieval.embedding.
    from engine.embeddings import get_embedder

    semantic = get_embedder().semantic_if_known
    status["embedding"] = (
        "not-loaded"
        if semantic is None
        else "sentence-transformer"
        if semantic
        else "hashing"
    )
    status["semantic_search"] = (
        None if semantic is None else bool(status["vector_search"] and semantic)
    )
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
    if len(query) > MAX_QUERY_CHARS:
        return (
            jsonify(
                {
                    "error": "q must be at most "
                    f"{MAX_QUERY_CHARS} characters",
                    "hits": [],
                    "total": 0,
                }
            ),
            400,
        )
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
        # Index unreachable / query failure -> 503 with an empty,
        # well-formed body so clients can render a graceful error state.
        return _unavailable(
            "search index", exc, hits=[], total=0, query=query
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
        reason = _failure_reason(exc)
        return (
            jsonify(agent_error_body(f"search index unavailable ({reason})")),
            503,
        )
    return jsonify(results_to_agent_dict(results, min_score=parsed.min_score))


@engine_api.get("/agent/openapi.json")
def agent_openapi():
    """Machine-readable contract for the agent search endpoint."""
    return jsonify(AGENT_OPENAPI_SPEC)


@engine_api.get("/document/<path:doc_id>/related")
def related(doc_id: str):
    if not _valid_document_id(doc_id):
        return jsonify({"error": "invalid document id", "related": []}), 400
    size = max(1, min(_int_arg("size") or 8, 25))
    try:
        hits = _service().related(doc_id, size=size)
    except Exception as exc:
        return _unavailable("search index", exc, related=[])
    return jsonify({"id": doc_id, "related": [hit_to_dict(h) for h in hits]})


@engine_api.get("/document/<path:doc_id>")
def document(doc_id: str):
    if not _valid_document_id(doc_id):
        return jsonify({"error": "invalid document id"}), 400
    try:
        doc = backend.get_document(doc_id)
    except Exception as exc:
        return _unavailable("search index", exc, id=doc_id)
    if doc is None:
        return jsonify({"error": "not found", "id": doc_id}), 404
    return jsonify(document_to_dict(doc, full=True))


# --------------------------------------------------------------------------- #
# Summaries & comparison
# --------------------------------------------------------------------------- #
@engine_api.post("/summarize")
def summarize():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "a JSON object is required"}), 400
    query = payload.get("q")
    ids = payload.get("ids", [])
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        return (
            jsonify(
                {
                    "error": "q must be a nonempty string of at most 2000 characters"
                }
            ),
            400,
        )
    if (
        not isinstance(ids, list)
        or len(ids) > 8
        or any(
            not isinstance(i, str) or not i.strip() or len(i) > 512 for i in ids
        )
    ):
        return (
            jsonify(
                {
                    "error": "ids must be a list of at most 8 nonempty document ids"
                }
            ),
            400,
        )
    query = query.strip()
    ids = list(dict.fromkeys(ids))

    try:
        if ids:
            # One round trip for the page's top hits, in the order given;
            # fetching them one by one opened a database connection each.
            found = backend.get_documents(ids)
            docs = [found[i] for i in ids if i in found]
        else:
            results = _service().search(query, per_page=6, include_facets=False)
            docs = [h.document for h in results.hits]
        summary = _summary().summarize(query, docs)
        return jsonify(summary.to_dict())
    except Exception as exc:
        return _unavailable("summary", exc)


@engine_api.post("/compare")
def compare():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}
    id_a, id_b = payload.get("a"), payload.get("b")
    if not _valid_document_id(id_a) or not _valid_document_id(id_b):
        return (
            jsonify({"error": "both 'a' and 'b' document ids are required"}),
            400,
        )
    if id_a == id_b:
        return jsonify({"error": "choose two different documents"}), 400

    try:
        found = backend.get_documents([id_a, id_b])
    except Exception as exc:
        return _unavailable("search index", exc)
    doc_a, doc_b = found.get(id_a), found.get(id_b)
    if doc_a is None or doc_b is None:
        missing = [i for i, d in ((id_a, doc_a), (id_b, doc_b)) if d is None]
        return (
            jsonify({"error": "document(s) not found", "missing": missing}),
            404,
        )
    return jsonify(compare_documents(doc_a, doc_b))


# --------------------------------------------------------------------------- #
# Evidence verification
# --------------------------------------------------------------------------- #
# A research record is small (one result page plus a handful of excerpts);
# these caps keep a hostile upload from turning verification into a bulk
# document-fetch service.
MAX_VERIFY_BYTES = 2_000_000
MAX_VERIFY_EXCERPTS = 500


@engine_api.post("/evidence/verify")
def verify_evidence():
    """Re-check a saved research record against the current index.

    Accepts the packet the workbench exports (``{captured_at,
    content_sha256, record}``) or a bare ``anna-research-record/v1``.
    Recomputes the record's SHA-256 fingerprint, then re-reads every cited
    excerpt from the live index and reports whether it is still at the
    recorded offsets (``verified``), elsewhere in the field
    (``relocated``), gone (``drifted``), or uncheckable
    (``missing-document``, ``missing-field``, ``invalid-excerpt``).
    A failed check is a 200 with ``ok: false``; only malformed input (400,
    413) and an unreachable index (503) are errors. Nothing is stored.
    """
    if (request.content_length or 0) > MAX_VERIFY_BYTES:
        return (
            jsonify(
                {
                    "error": "request body must be at most "
                    f"{MAX_VERIFY_BYTES} bytes"
                }
            ),
            413,
        )
    payload = request.get_json(silent=True)
    try:
        record, _ = records.unwrap_packet(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if records.count_excerpts(record) > MAX_VERIFY_EXCERPTS:
        return (
            jsonify(
                {
                    "error": "at most "
                    f"{MAX_VERIFY_EXCERPTS} excerpts can be verified per request"
                }
            ),
            400,
        )
    config = get_config()
    try:
        report = records.verify_record(
            payload,
            backend.get_document,
            checked_against={
                "backend": config.backend,
                "index": config.index_name,
            },
        )
    except Exception as exc:  # index unreachable mid-verification
        return _unavailable("search index", exc)
    return jsonify(report)


# --------------------------------------------------------------------------- #
# Collections & bookmarks
# --------------------------------------------------------------------------- #
# There are no user accounts. A collection belongs to an ``owner`` string; the
# static workbench uses a random per-browser workspace key ("ws_" + 32 hex
# characters), which is unguessable, so it behaves as a bearer capability:
# whoever holds the key can read and change that workspace's private
# collections. "anonymous" (the default) is a shared, public workspace.
_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")
MAX_COLLECTION_NAME = 200
MAX_COLLECTION_DESCRIPTION = 2000
MAX_BOOKMARK_NOTE = 2000
# Bookmark columns are String(255)/String(1024)/String(2048)/String(64).
MAX_BOOKMARK_DOCUMENT_ID = 255
MAX_BOOKMARK_TITLE = 1024
MAX_BOOKMARK_URL = 2048
MAX_BOOKMARK_SOURCE = 64


def _json_object() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _owner() -> Optional[str]:
    """The requesting workspace, or None when the value is malformed."""
    owner = request.args.get("owner") or _json_object().get("owner")
    if owner in (None, ""):
        return "anonymous"
    if not isinstance(owner, str) or not _OWNER_RE.fullmatch(owner):
        return None
    return owner


def _bad_owner():
    return (
        jsonify(
            {
                "error": "owner must be 1-128 letters, digits or ._:@- "
                "characters"
            }
        ),
        400,
    )


def _text(value: Any, limit: int) -> str:
    """A bounded plain string (non-strings become empty)."""
    return value[:limit] if isinstance(value, str) else ""


def _http_url(value: Any) -> str:
    """Keep only http(s) links: a bookmark URL is rendered as a link."""
    if not isinstance(value, str) or len(value) > MAX_BOOKMARK_URL:
        return ""
    url = value.strip()
    return url if re.match(r"https?://", url, re.IGNORECASE) else ""


@engine_api.get("/collections")
def list_collections():
    owner = _owner()
    if owner is None:
        return _bad_owner()
    with_bookmarks = _bool_arg("with_bookmarks") is True
    try:
        rows = _store().list_collections(owner, with_bookmarks=with_bookmarks)
    except Exception as exc:
        return _unavailable("collections database", exc, collections=[])
    return jsonify({"owner": owner, "collections": rows})


@engine_api.post("/collections")
def create_collection():
    payload = _json_object()
    owner = _owner()
    if owner is None:
        return _bad_owner()
    name = payload.get("name")
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        return jsonify({"error": "name is required"}), 400
    if len(name) > MAX_COLLECTION_NAME:
        return (
            jsonify(
                {
                    "error": "name must be at most "
                    f"{MAX_COLLECTION_NAME} characters"
                }
            ),
            400,
        )
    description = payload.get("description", "")
    if not isinstance(description, str) or (
        len(description) > MAX_COLLECTION_DESCRIPTION
    ):
        return (
            jsonify(
                {
                    "error": "description must be a string of at most "
                    f"{MAX_COLLECTION_DESCRIPTION} characters"
                }
            ),
            400,
        )
    try:
        coll = _store().create_collection(
            owner=owner,
            name=name,
            description=description,
            is_public=payload.get("is_public") is True,
        )
    except Exception as exc:
        return _unavailable("collections database", exc)
    return jsonify(coll), 201


@engine_api.get("/collections/<int:collection_id>")
def get_collection(collection_id: int):
    owner = _owner()
    if owner is None:
        return _bad_owner()
    try:
        coll = _store().get_collection(collection_id, owner=owner)
    except Exception as exc:
        return _unavailable("collections database", exc)
    if coll is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(coll)


@engine_api.delete("/collections/<int:collection_id>")
def delete_collection(collection_id: int):
    owner = _owner()
    if owner is None:
        return _bad_owner()
    try:
        ok = _store().delete_collection(collection_id, owner=owner)
    except Exception as exc:
        return _unavailable("collections database", exc)
    return (
        (jsonify({"deleted": True}), 200)
        if ok
        else (jsonify({"error": "not found"}), 404)
    )


@engine_api.post("/collections/<int:collection_id>/bookmarks")
def add_bookmark(collection_id: int):
    payload = _json_object()
    owner = _owner()
    if owner is None:
        return _bad_owner()
    document_id = payload.get("document_id")
    if not _valid_document_id(document_id) or (
        len(document_id) > MAX_BOOKMARK_DOCUMENT_ID
    ):
        return jsonify({"error": "document_id is required"}), 400
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > MAX_BOOKMARK_NOTE:
        return (
            jsonify(
                {
                    "error": "note must be a string of at most "
                    f"{MAX_BOOKMARK_NOTE} characters"
                }
            ),
            400,
        )
    try:
        bm = _store().add_bookmark(
            collection_id,
            document_id,
            owner=owner,
            title=_text(payload.get("title"), MAX_BOOKMARK_TITLE),
            url=_http_url(payload.get("url")),
            source=_text(payload.get("source"), MAX_BOOKMARK_SOURCE),
            note=note,
        )
    except Exception as exc:
        return _unavailable("collections database", exc)
    if bm is None:
        return jsonify({"error": "collection not found"}), 404
    return jsonify(bm), 201


@engine_api.delete(
    "/collections/<int:collection_id>/bookmarks/<path:document_id>"
)
def remove_bookmark(collection_id: int, document_id: str):
    owner = _owner()
    if owner is None:
        return _bad_owner()
    try:
        ok = _store().remove_bookmark(
            collection_id, document_id, owner=owner
        )
    except Exception as exc:
        return _unavailable("collections database", exc)
    return (
        (jsonify({"deleted": True}), 200)
        if ok
        else (jsonify({"error": "not found"}), 404)
    )
