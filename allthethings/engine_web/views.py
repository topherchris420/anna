"""Anna · R.A.I.N. DataMatrix Engine — the server-rendered pages.

Home, search, document detail, compare, collections and about, backed by the
same engine as the REST API and the static workbench, and held to the same
contract: an unreachable index is reported as an outage (never as an empty
result), hits carry the shared 0–1 relevance and their ranking explanation,
mode labels say what the connected backend actually runs, the source report
is fetched client-side as a progressive enhancement, and every document can
be cited. Search results are rendered server-side for speed and SEO.

This blueprint owns the application's root routes. The legacy Anna's Archive
book search is preserved under ``/legacy`` (see :func:`allthethings.app.create_app`).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from flask import Blueprint, redirect, render_template, request, send_from_directory, url_for
from markupsafe import Markup, escape

from engine import __version__ as engine_version
from engine import backend, citations
from engine.config import get_config
from engine.retrieval import normalize_relevance
from engine.search import SearchFilters, SearchResults

engine_web = Blueprint("engine_web", __name__, template_folder="templates")
log = logging.getLogger(__name__)


@engine_web.app_template_filter("snippet")
def snippet_filter(text: str) -> Markup:
    """Render a highlight fragment: escape everything except the ``<em>`` marks.

    Fragments contain raw document text (crawled content can carry markup), so
    ``|safe`` would let a hostile document inject HTML into the results page.
    Escape the fragment, then re-enable only the ``<em>``/``</em>`` markers the
    retrieval layer inserted — the same policy the static frontend applies
    client-side in ``highlight()``.
    """
    escaped = str(escape(text or ""))
    return Markup(
        escaped.replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")
    )


_service = None

_EXAMPLES = [
    "STM32 DMA circular buffer",
    "reinforcement learning for control systems",
    "RISC-V vector extension",
    "ESP32 deep sleep power consumption",
    "finite element method stress analysis",
]

_MULTI_PARAMS = [
    ("source", "Source"),
    ("kind", "Type"),
    ("category", "Category"),
    ("language", "Language"),
]
_BOOL_PARAMS = [("has_code", "Has code"), ("has_equations", "Has equations")]
# facet-group name (from SearchService) -> query-param name
_GROUP_PARAM = {
    "source": "source",
    "kind": "kind",
    "categories": "category",
    "language": "language",
}
_GROUP_LABEL = {
    "source": "Source",
    "kind": "Type",
    "categories": "Category",
    "language": "Language",
}


def _svc():
    global _service
    if _service is None:
        _service = backend.get_search_service()
    return _service


@engine_web.context_processor
def _inject_globals():
    return {"engine_version": engine_version}


def _failure_reason(exc: BaseException) -> str:
    """A page-safe name for a backend failure: the root error's class.

    The same policy as the API — driver messages carry hostnames, user names
    and SQL, so they go to the server log and only the class name is shown.
    """
    log.warning("engine backend failure: %r", exc)
    root = exc.__cause__ or exc
    return type(root).__name__


# --------------------------------------------------------------------------- #
# What the connected backend can do, for labels that tell the truth
# --------------------------------------------------------------------------- #
def capabilities(
    results: Optional[SearchResults] = None, probe: bool = True
) -> Dict[str, Any]:
    """``{backend, vector_search, embedding}`` as the workbench learns them.

    A search's retrieval report is believed first (it says which paths ran
    and what the vectors were); what it leaves open is asked of the store
    and the embedder without loading a model. Unknowns stay ``None`` — a
    label is only downgraded on evidence, never on a guess.
    """
    config = get_config()
    report: Dict[str, Any] = results.retrieval if results is not None else {}
    vector: Optional[bool] = None
    if "knn" in (report.get("unavailable") or []):
        vector = False
    elif "knn" in (report.get("executed") or []):
        vector = True
    if vector is None and probe:
        try:
            if config.backend == "postgres":
                from engine.pg.store import get_store

                vector = bool(get_store(config).has_vector())
            else:
                vector = True  # the Elasticsearch index always has the field
        except Exception:
            vector = None
    embedding = report.get("embedding")
    if embedding not in ("hashing", "sentence-transformer"):
        try:
            from engine.embeddings import get_embedder

            semantic = get_embedder().semantic_if_known
        except Exception:
            semantic = None
        embedding = (
            None
            if semantic is None
            else "sentence-transformer"
            if semantic
            else "hashing"
        )
    return {
        "backend": report.get("backend") or config.backend,
        "vector_search": vector,
        "embedding": embedding,
    }


def mode_labels(caps: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The three modes, named for what this backend runs (the workbench's
    ``modeLabel`` rules): Postgres runs full-text search, not BM25, and
    term-hashing vectors are not semantic. A mode that cannot run (no
    vectors) is marked unavailable rather than offered."""
    lexical = "full-text" if caps.get("backend") == "postgres" else "BM25"
    hashing = caps.get("embedding") == "hashing"
    vectors = caps.get("vector_search") is not False
    return {
        "bm25": {
            "label": f"Lexical ({lexical})",
            "title": "Keyword matching only",
            "available": True,
        },
        "hybrid": {
            "label": f"Hybrid ({lexical} + "
            + ("term vectors)" if hashing else "Vector)"),
            "title": (
                "Lexical and term-hashing vector rankings fused by rank; "
                "the vectors are a bag-of-terms signal, not meaning"
                if hashing
                else "Lexical and semantic vector rankings fused by rank"
            ),
            "available": vectors,
        },
        "semantic": {
            "label": "Term vectors (not semantic)"
            if hashing
            else "Semantic (Vector)",
            "title": (
                "Term-hashing vectors only: documents sharing query terms, "
                "ordered by hashed-term overlap"
                if hashing
                else "Sentence-embedding vectors only"
            ),
            "available": vectors,
        },
    }


def vectors_label(caps: Dict[str, Any]) -> str:
    """One phrase for the home page's status line."""
    if caps.get("vector_search") is False:
        return "none (full-text only)"
    embedding = caps.get("embedding")
    if embedding == "sentence-transformer":
        return "semantic (sentence-embedding model)"
    if embedding == "hashing":
        return "term-hashing (not semantic)"
    return "not loaded yet"


def retrieval_status(report: Optional[Dict[str, Any]]) -> str:
    """The retrieval paths that ran, in the words the workbench uses."""
    if not report or not report.get("executed"):
        return "Retrieval details unavailable"
    text = " + ".join(report["executed"])
    text += f" · {report.get('candidate_count', 0)} candidates"
    if report.get("embedding") == "hashing":
        text += " · term-hashing vectors (no semantic model)"
    if report.get("degraded"):
        text += " · unavailable: " + ", ".join(report.get("unavailable") or [])
    return text


def hit_views(results: SearchResults) -> List[Dict[str, Any]]:
    """Hits with the shared 0–1 relevance when the backend knows its score
    ceiling (the API's ``hits[].relevance``), else none — never invented."""
    ceiling = results.score_ceiling
    return [
        {
            "document": hit.document,
            "score": hit.score,
            "highlights": hit.highlights,
            "explanation": hit.explanation or {},
            "relevance": normalize_relevance(hit.score, ceiling)
            if ceiling > 0
            else None,
        }
        for hit in results.hits
    ]


def _sources_info():
    from engine.ingest import all_plugins

    return [p.info() for p in all_plugins()]


# --------------------------------------------------------------------------- #
# URL builders for filters / paging / mode
# --------------------------------------------------------------------------- #
def _pairs(exclude=()) -> List[Tuple[str, str]]:
    return [
        (k, v) for k, v in request.args.items(multi=True) if k not in exclude
    ]


def _url(pairs: List[Tuple[str, str]]) -> str:
    qs = urlencode([(k, v) for k, v in pairs if v != ""])
    return url_for("engine_web.search") + (f"?{qs}" if qs else "")


def _toggle_multi(param: str, value: str) -> str:
    current = request.args.getlist(param)
    pairs = _pairs(exclude=(param, "page"))
    remaining = (
        [v for v in current if v != value]
        if value in current
        else current + [value]
    )
    pairs.extend((param, v) for v in remaining)
    return _url(pairs)


def _toggle_bool(param: str) -> str:
    active = request.args.get(param) in ("true", "1", "on")
    pairs = _pairs(exclude=(param, "page"))
    if not active:
        pairs.append((param, "true"))
    return _url(pairs)


def _mode_url(mode: str) -> str:
    pairs = _pairs(exclude=("mode", "page"))
    pairs.append(("mode", mode))
    return _url(pairs)


def _page_url(page: int) -> str:
    pairs = _pairs(exclude=("page",))
    pairs.append(("page", str(page)))
    return _url(pairs)


def _bool_arg(name: str) -> Optional[bool]:
    raw = request.args.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = request.args.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _filters() -> SearchFilters:
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
# Pages
# --------------------------------------------------------------------------- #
@engine_web.get("/")
def home():
    from engine import backend as es_index

    config = get_config()
    document_count, index_status = 0, "offline"
    try:
        if es_index.index_exists(config):
            document_count = es_index.count(config)
            index_status = "online"
    except Exception as exc:
        _failure_reason(exc)
        index_status = "offline"
    return render_template(
        "engine/home.html",
        active="home",
        sources=_sources_info(),
        examples=_EXAMPLES,
        document_count=document_count,
        index_status=index_status,
        vectors=vectors_label(capabilities(probe=index_status == "online")),
    )


@engine_web.get("/search")
def search():
    query = request.args.get("q", "").strip()
    mode = request.args.get("mode", "hybrid")
    if mode not in ("hybrid", "bm25", "semantic"):
        mode = "hybrid"
    page = max(1, _int_arg("page", 1) or 1)
    per_page = min(50, max(5, _int_arg("per_page", 20) or 20))

    error = None
    try:
        results = _svc().search(
            query, filters=_filters(), mode=mode, page=page, per_page=per_page
        )
    except Exception as exc:
        # An unreachable index is an outage, reported as one (HTTP 503 and a
        # notice), never as "no results".
        error = _failure_reason(exc)
        results = SearchResults(
            query=query,
            mode=mode,
            total=0,
            hits=[],
            facets={},
            page=page,
            per_page=per_page,
        )
    caps = capabilities(results, probe=error is None)
    modes = mode_labels(caps)

    # Build facet groups with toggle URLs.
    facet_groups = []
    for gkey in ("source", "kind", "categories", "language"):
        param = _GROUP_PARAM[gkey]
        active_vals = set(request.args.getlist(param))
        buckets = [
            {
                "label": str(b["value"]),
                "count": b["count"],
                "active": str(b["value"]) in active_vals,
                "url": _toggle_multi(param, str(b["value"])),
            }
            for b in results.facets.get(gkey, [])
        ]
        facet_groups.append(
            {"key": gkey, "label": _GROUP_LABEL[gkey], "buckets": buckets}
        )

    for param, label in _BOOL_PARAMS:
        active = request.args.get(param) in ("true", "1", "on")
        buckets_raw = results.facets.get(param, [])
        true_count = next(
            (
                b["count"]
                for b in buckets_raw
                if b["value"] in (True, 1, "true")
            ),
            0,
        )
        facet_groups.append(
            {
                "key": param,
                "label": label,
                "buckets": [
                    {
                        "label": label,
                        "count": true_count,
                        "active": active,
                        "url": _toggle_bool(param),
                    }
                ],
            }
        )

    active_filters = []
    for param, _ in _MULTI_PARAMS:
        for v in request.args.getlist(param):
            active_filters.append({"label": v, "url": _toggle_multi(param, v)})
    for param, label in _BOOL_PARAMS:
        if request.args.get(param) in ("true", "1", "on"):
            active_filters.append({"label": label, "url": _toggle_bool(param)})

    total_pages = (
        max(1, math.ceil(results.total / per_page)) if results.total else 1
    )
    prev_url = _page_url(page - 1) if page > 1 else None
    next_url = (
        _page_url(page + 1) if (page < total_pages and results.hits) else None
    )

    page_html = render_template(
        "engine/search.html",
        active="search",
        query=query,
        mode=mode,
        results=results,
        hits=hit_views(results),
        error=error,
        retry_url=_url(_pairs()),
        retrieval_status=retrieval_status(results.retrieval)
        if query and not error
        else "",
        modes=modes,
        facet_groups=facet_groups,
        active_filters=active_filters,
        mode_urls={
            "hybrid": _mode_url("hybrid"),
            "bm25": _mode_url("bm25"),
            "semantic": _mode_url("semantic"),
        },
        prev_url=prev_url,
        next_url=next_url,
    )
    return page_html, (503 if error else 200)


@engine_web.get("/document/<path:doc_id>")
def document(doc_id: str):
    from engine import backend as es_index

    doc = es_index.get_document(doc_id)
    if doc is None:
        return (
            render_template(
                "engine/document.html",
                active="search",
                document=None,
                related=[],
                not_found=True,
                doc_id=doc_id,
            ),
            404,
        )
    related_error = None
    try:
        related = _svc().related(doc_id, size=8)
    except Exception as exc:
        # Say so: an outage must not read as "no related documents".
        related, related_error = [], _failure_reason(exc)
    return render_template(
        "engine/document.html",
        active="search",
        document=doc,
        related=related,
        related_error=related_error,
        citation=citations.citation(doc),
        query="",
    )


@engine_web.get("/compare")
def compare():
    id_a = request.args.get("a", "").strip()
    id_b = request.args.get("b", "").strip()
    comparison, error = None, None
    if id_a and id_b:
        from engine import backend as es_index
        from engine.summarize import compare_documents

        doc_a = es_index.get_document(id_a)
        doc_b = es_index.get_document(id_b)
        if doc_a is None or doc_b is None:
            missing = [
                i for i, d in ((id_a, doc_a), (id_b, doc_b)) if d is None
            ]
            error = f"Document(s) not found: {', '.join(missing)}"
        else:
            comparison = compare_documents(doc_a, doc_b)
    return render_template(
        "engine/compare.html",
        active="search",
        id_a=id_a,
        id_b=id_b,
        comparison=comparison,
        error=error,
    )


# --------------------------------------------------------------------------- #
# Collections (server-rendered)
# --------------------------------------------------------------------------- #
def _owner() -> str:
    return request.values.get("owner") or "anonymous"


@engine_web.get("/collections")
def collections():
    owner = _owner()
    colls, store_error = [], None
    try:
        from engine.collections import get_store

        store = get_store()
        summaries = store.list_collections(owner)
        # Expand each with its bookmarks for inline display.
        colls = [
            store.get_collection(c["id"], owner=owner) or c for c in summaries
        ]
    except Exception as exc:
        store_error = str(exc)
    return render_template(
        "engine/collections.html",
        active="collections",
        owner=owner,
        collections=colls,
        store_error=store_error,
    )


@engine_web.post("/collections/create")
def create_collection():
    owner = _owner()
    name = request.form.get("name", "").strip()
    if name:
        try:
            from engine.collections import get_store

            get_store().create_collection(
                owner=owner,
                name=name,
                description=request.form.get("description", ""),
            )
        except Exception:
            pass
    return redirect(url_for("engine_web.collections", owner=owner))


@engine_web.post("/collections/<int:collection_id>/delete")
def delete_collection(collection_id: int):
    owner = _owner()
    try:
        from engine.collections import get_store

        get_store().delete_collection(collection_id, owner=owner)
    except Exception:
        pass
    return redirect(url_for("engine_web.collections", owner=owner))


@engine_web.get("/about")
def about():
    return render_template(
        "engine/about.html", active="about", sources=_sources_info()
    )


_DECK_DIR = Path(__file__).resolve().parents[2] / "docs" / "deck"


@engine_web.get("/deck/", defaults={"filename": "index.html"})
@engine_web.get("/deck/<path:filename>")
def deck(filename: str):
    """Serve the project deck (docs/deck) from the platform itself.

    The deck is fully self-contained (fonts included), so it works on
    air-gapped deployments; /deck redirects to /deck/ so the page's
    relative asset URLs resolve.
    """
    return send_from_directory(_DECK_DIR, filename)


_BLUEPRINT_DIR = Path(__file__).resolve().parents[2] / "docs" / "blueprint"


@engine_web.get("/blueprint/", defaults={"filename": "index.html"})
@engine_web.get("/blueprint/<path:filename>")
def blueprint(filename: str):
    """Serve the blueprint animation (docs/blueprint) from the platform.

    Same contract as the deck: one self-contained page with vendored fonts
    and no outbound requests, so an air-gapped deployment can still show how
    its own retrieval pipeline works.
    """
    return send_from_directory(_BLUEPRINT_DIR, filename)
