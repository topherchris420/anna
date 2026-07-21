"""LLM-agent search contract: request parsing, filter resolution, serialization.

``POST /api/v1/agent/search`` is a deliberately small surface over the hybrid
search engine, designed for LLM tool calling (e.g. the ``james_library`` Rust
agent runtime): a strict four-field request, a flat citation-ready response
that fits comfortably in a model's context window, and relevance scores
normalized to 0–1 so agents can threshold on them.

Everything here is a pure function of its inputs — the Flask layer in
:mod:`allthethings.engine_api.views` stays a thin adapter and the whole
contract is unit-testable without a request context or a live backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from engine.documents import DocumentKind
from engine.search import SearchFilters, SearchHit, SearchResults

# Caps chosen for LLM context economy: a full default response (5 results of
# ~1200-char chunks plus metadata) stays around 2K tokens.
DEFAULT_LIMIT = 5
MAX_LIMIT = 25
CHUNK_MAX_CHARS = 1200

_EM_TAG_RE = re.compile(r"</?em>")

_KNOWN_KINDS = frozenset(kind.value for kind in DocumentKind)

# Explicit facet prefixes accepted in ``domain_filter`` ("source:arxiv").
_DOMAIN_PREFIXES = {
    "source": "sources",
    "kind": "kinds",
    "category": "categories",
    "topic": "categories",
}


# --------------------------------------------------------------------------- #
# Request contract
# --------------------------------------------------------------------------- #
@dataclass
class AgentSearchRequest:
    query: str
    domain_filter: Optional[str] = None
    limit: int = DEFAULT_LIMIT
    min_score: float = 0.0


def _int_field(
    value: Any, name: str, default: int, lo: int, hi: int
) -> Tuple[Optional[int], Optional[str]]:
    if value is None:
        return default, None
    if isinstance(value, bool):
        return None, f"'{name}' must be an integer"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return None, f"'{name}' must be an integer"
    if not isinstance(value, int):
        return None, f"'{name}' must be an integer"
    return max(lo, min(value, hi)), None


def _float_field(
    value: Any, name: str, default: float, lo: float, hi: float
) -> Tuple[Optional[float], Optional[str]]:
    if value is None:
        return default, None
    if isinstance(value, bool):
        return None, f"'{name}' must be a number"
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None, f"'{name}' must be a number"
    if not isinstance(value, (int, float)) or value != value:  # NaN-safe
        return None, f"'{name}' must be a number"
    return max(lo, min(float(value), hi)), None


def parse_agent_search_request(
    body: Any,
) -> Tuple[Optional[AgentSearchRequest], Optional[str]]:
    """Validate a JSON request body into an :class:`AgentSearchRequest`.

    Returns ``(request, None)`` on success or ``(None, error_message)``.
    Out-of-range numbers are clamped rather than rejected (agents retry
    badly); wrong types are errors, since they signal a broken tool binding
    rather than a bad guess. Unknown keys are ignored for forward
    compatibility.
    """
    if not isinstance(body, dict):
        return None, "request body must be a JSON object"

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return None, "'query' is required and must be a non-empty string"

    domain = body.get("domain_filter")
    if domain is not None and not isinstance(domain, str):
        return None, "'domain_filter' must be a string"
    domain = (domain or "").strip() or None

    limit, err = _int_field(
        body.get("limit"), "limit", DEFAULT_LIMIT, 1, MAX_LIMIT
    )
    if err is not None:
        return None, err
    min_score, err = _float_field(
        body.get("min_score"), "min_score", 0.0, 0.0, 1.0
    )
    if err is not None:
        return None, err

    return (
        AgentSearchRequest(
            query=query.strip(),
            domain_filter=domain,
            limit=limit,
            min_score=min_score,
        ),
        None,
    )


# --------------------------------------------------------------------------- #
# domain_filter -> SearchFilters
# --------------------------------------------------------------------------- #
def _registered_sources() -> frozenset:
    """Names of the registered ingestion sources, best-effort.

    Importing the registry pulls in every source plugin module; if that fails
    (trimmed deployment), bare source names simply fall through to the
    category facet instead of erroring.
    """
    try:
        from engine.ingest import plugin_names

        return frozenset(name.lower() for name in plugin_names())
    except Exception:
        return frozenset()


def resolve_domain_filter(domain_filter: Optional[str]) -> SearchFilters:
    """Map the single ``domain_filter`` string onto the faceted filters.

    Resolution order (first match wins):

    1. An explicit ``source:`` / ``kind:`` / ``category:`` (alias ``topic:``)
       prefix pins the facet directly, e.g. ``source:arxiv``.
    2. A bare value equal to a document kind (``paper``, ``report``, ...)
       filters by kind.
    3. A bare value equal to a registered ingestion source (``arxiv``,
       ``github``, ...) filters by source.
    4. Anything else filters by category (e.g. arXiv's ``cs.RO``). Unknown
       values match nothing rather than erroring, so agents can probe.
    """
    if not domain_filter:
        return SearchFilters()
    value = domain_filter.strip()

    prefix, _, rest = value.partition(":")
    facet = _DOMAIN_PREFIXES.get(prefix.strip().lower())
    if facet is not None and rest.strip():
        rest = rest.strip()
        if facet == "sources":
            return SearchFilters(sources=[rest.lower()])
        if facet == "kinds":
            return SearchFilters(kinds=[rest.lower()])
        return SearchFilters(categories=[rest])

    lowered = value.lower()
    if lowered in _KNOWN_KINDS:
        return SearchFilters(kinds=[lowered])
    if lowered in _registered_sources():
        return SearchFilters(sources=[lowered])
    return SearchFilters(categories=[value])


# --------------------------------------------------------------------------- #
# Response contract
# --------------------------------------------------------------------------- #
def normalize_relevance(score: float, ceiling: float) -> float:
    """Map a fused score onto 0–1 given the result set's score ceiling."""
    if ceiling <= 0:
        return 0.0
    return round(min(score / ceiling, 1.0), 4)


def _clean_fragment(fragment: str) -> str:
    """Strip highlight markup and collapse whitespace for LLM consumption."""
    return " ".join(_EM_TAG_RE.sub("", fragment).split())


def build_content_chunk(hit: SearchHit) -> str:
    """Query-relevant text for the LLM.

    Prefers the retriever's highlight fragments (the passages that actually
    matched), falling back to the abstract and then the head of the body.
    Whitespace is collapsed and the result is capped at ``CHUNK_MAX_CHARS``
    on a word boundary.
    """
    fragments = [_clean_fragment(f) for f in hit.highlights]
    text = " … ".join(f for f in fragments if f)
    if not text:
        doc = hit.document
        text = " ".join((doc.abstract or doc.body or "").split())
    if len(text) <= CHUNK_MAX_CHARS:
        return text
    cut = text.rfind(" ", 0, CHUNK_MAX_CHARS)
    return text[: cut if cut > 0 else CHUNK_MAX_CHARS].rstrip() + " …"


def hit_to_agent_result(hit: SearchHit, score_ceiling: float) -> Dict[str, Any]:
    doc = hit.document
    return {
        "id": doc.id,
        "title": doc.title,
        "authors": doc.authors,
        "content_chunk": build_content_chunk(hit),
        "source_url": doc.url or doc.pdf_url,
        "relevance_score": normalize_relevance(hit.score, score_ceiling),
        # Additive provenance fields — optional for clients, cheap in context.
        "source": doc.source,
        "kind": doc.kind,
        "published": doc.published,
    }


def results_to_agent_dict(
    results: SearchResults, min_score: float = 0.0
) -> Dict[str, Any]:
    """Shape :class:`SearchResults` for an LLM tool call.

    Hits arrive best-first, so ``min_score`` prunes the low-relevance tail.
    ``total_results`` counts what is returned (post-filter), not the
    corpus-wide match count — the agent contract promises
    ``total_results == len(results)``.
    """
    items: List[Dict[str, Any]] = []
    for hit in results.hits:
        item = hit_to_agent_result(hit, results.score_ceiling)
        if item["relevance_score"] >= min_score:
            items.append(item)
    return {
        "status": "success",
        "query": results.query,
        "total_results": len(items),
        "results": items,
    }


def agent_error_body(message: str) -> Dict[str, Any]:
    """Error responses keep the success shape so one client struct fits both."""
    return {
        "status": "error",
        "error": message,
        "total_results": 0,
        "results": [],
    }
