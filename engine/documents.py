"""Unified document schema shared by every ingestion source.

All source plugins normalize their raw records into a :class:`Document`. The
same schema is what gets indexed into Elasticsearch and what the search layer
returns, giving the whole platform one consistent shape regardless of whether a
record came from arXiv, GitHub, NASA NTRS, or a vendor documentation crawler.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class DocumentKind(str, Enum):
    """Coarse content type, used for faceting and result rendering."""

    PAPER = "paper"  # arXiv, IEEE, journal articles
    REPORT = "report"  # NASA NTRS, DOE OSTI, NIST publications
    STANDARD = "standard"  # NIST standards, IEEE standards
    REPOSITORY = "repository"  # GitHub repos
    CODE = "code"  # individual source files
    DOCUMENTATION = "documentation"  # vendor / kernel docs
    DATASHEET = "datasheet"  # STM32 / vendor datasheets
    LIBRARY = "library"  # a shadow library / access point (ShadowLibraries)
    OTHER = "other"


# Fields that carry equations (LaTeX / MathML) get flagged so equation search
# can boost or filter on them.
_EQUATION_RE = re.compile(r"(\$[^$]+\$|\\begin\{(equation|align|math)\})")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Document:
    """A single indexable engineering-knowledge record.

    The ``id`` should be globally unique and stable across re-ingestion so that
    re-indexing updates rather than duplicates. Source plugins usually derive it
    from ``source`` + the source-native identifier (see :meth:`make_id`).
    """

    id: str
    source: str
    kind: str  # a DocumentKind value
    title: str

    # Body / retrieval text.
    abstract: str = ""
    body: str = ""

    # Provenance and linking (citation-first answers depend on these).
    url: str = ""
    pdf_url: str = ""
    authors: List[str] = field(default_factory=list)
    published: Optional[str] = None  # ISO-8601 date
    updated: Optional[str] = None  # ISO-8601 date
    version: Optional[str] = None  # for version-aware documentation

    # Faceting / filtering.
    categories: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    language: Optional[str] = None  # natural or programming language
    identifiers: Dict[str, str] = field(
        default_factory=dict
    )  # doi, isbn, arxiv_id...

    # Engineering-specific signals.
    has_equations: bool = False
    has_code: bool = False
    equations: List[str] = field(default_factory=list)

    # Ranking hints.
    popularity: float = 0.0  # stars, citations, downloads (normalized upstream)

    # Dense vector (populated by the embedding stage before indexing).
    embedding: Optional[List[float]] = None

    # Free-form source-specific payload, retained but not required for search.
    extra: Dict[str, Any] = field(default_factory=dict)

    indexed_at: str = field(default_factory=_utcnow_iso)

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def make_id(source: str, native_id: str) -> str:
        """Deterministic, collision-resistant document id."""
        native = (native_id or "").strip()
        digest = hashlib.sha1(f"{source}:{native}".encode("utf-8")).hexdigest()[
            :16
        ]
        return f"{source}:{digest}"

    def __post_init__(self) -> None:
        # Normalize kind to its plain string value so serialization, templates,
        # and Elasticsearch all see a clean "paper"/"code"/... rather than the
        # enum's repr (which changed to "DocumentKind.X" in Python 3.11).
        if isinstance(self.kind, DocumentKind):
            self.kind = self.kind.value
        else:
            self.kind = str(self.kind)
        # Auto-detect equations / code if the caller did not set the flags.
        haystack = f"{self.abstract}\n{self.body}"
        if not self.has_equations and _EQUATION_RE.search(haystack):
            self.has_equations = True
        if not self.has_code and self.kind in (
            DocumentKind.CODE.value,
            DocumentKind.REPOSITORY.value,
        ):
            self.has_code = True

    # ------------------------------------------------------------------ #
    # Search text
    # ------------------------------------------------------------------ #
    def search_text(self) -> str:
        """The concatenated text used for BM25 and for embedding."""
        parts = [
            self.title,
            " ".join(self.authors),
            self.abstract,
            self.body,
            " ".join(self.categories),
            " ".join(self.tags),
        ]
        return "\n".join(p for p in parts if p).strip()

    def embedding_text(self) -> str:
        """Text handed to the embedding model.

        Titles and abstracts carry most of the semantic signal for retrieval,
        so we prioritize them and cap body length to keep vectors focused and
        embedding fast.
        """
        body_excerpt = (self.body or "")[:2000]
        parts = [self.title, self.abstract, body_excerpt]
        return "\n".join(p for p in parts if p).strip()

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    def to_index_doc(self, include_embedding: bool = True) -> Dict[str, Any]:
        """Shape the record for the Elasticsearch ``_source``."""
        doc = asdict(self)
        doc[
            "kind"
        ] = self.kind  # already normalized to a string in __post_init__
        doc["search_text"] = self.search_text()
        if not include_embedding:
            doc.pop("embedding", None)
        elif doc.get("embedding") is None:
            doc.pop("embedding", None)
        return doc

    @classmethod
    def from_source(cls, hit: Dict[str, Any]) -> "Document":
        """Reconstruct a Document from an Elasticsearch ``_source`` payload."""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        payload = {k: v for k, v in hit.items() if k in known}
        payload.setdefault("id", hit.get("_id", ""))
        return cls(**payload)
