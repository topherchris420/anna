"""Source-plugin base class and registry.

A source plugin implements two responsibilities:

1. :meth:`SourcePlugin.fetch` — talk to the upstream API / crawl the docs and
   yield raw, source-native records (dicts).
2. :meth:`SourcePlugin.normalize` — map one raw record to a unified
   :class:`~engine.documents.Document` (or return ``None`` to skip it).

The pipeline handles everything else (embedding, indexing, batching), so a
plugin never needs to know about Elasticsearch or vectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, List, Optional

from engine.config import EngineConfig, get_config
from engine.documents import Document, DocumentKind

# name -> plugin class
_REGISTRY: Dict[str, type] = {}


def register(cls: type) -> type:
    """Class decorator that adds a :class:`SourcePlugin` to the registry."""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"{cls.__name__} must define a non-empty `name`")
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        raise ValueError(f"Duplicate source plugin name: {name!r}")
    _REGISTRY[name] = cls
    return cls


def get_plugin(name: str, config: Optional[EngineConfig] = None) -> "SourcePlugin":
    """Instantiate a registered plugin by name."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown source {name!r}. Available: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[name](config=config)


def all_plugins(config: Optional[EngineConfig] = None) -> List["SourcePlugin"]:
    return [cls(config=config) for cls in _REGISTRY.values()]


def plugin_names() -> List[str]:
    return sorted(_REGISTRY.keys())


class SourcePlugin(ABC):
    """Abstract base for every knowledge source."""

    #: Unique, URL-safe identifier (also the ``source`` value on documents).
    name: str = ""
    #: Human-friendly label for the UI.
    display_name: str = ""
    #: Default document kind for records from this source.
    default_kind: DocumentKind = DocumentKind.OTHER
    #: One-line description shown in source listings.
    description: str = ""
    #: Whether this source supports a free-text query in :meth:`fetch`.
    supports_query: bool = True

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or get_config()

    # ------------------------------------------------------------------ #
    @abstractmethod
    def fetch(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Dict[str, Any]]:
        """Yield raw, source-native records."""
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: Dict[str, Any]) -> Optional[Document]:
        """Convert one raw record into a :class:`Document` (or ``None``)."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    def documents(
        self, *, query: Optional[str] = None, limit: int = 100, **kwargs: Any
    ) -> Iterator[Document]:
        """Fetch + normalize, yielding up to ``limit`` valid documents."""
        produced = 0
        for raw in self.fetch(query=query, limit=limit, **kwargs):
            try:
                doc = self.normalize(raw)
            except Exception:
                doc = None
            if doc is None:
                continue
            yield doc
            produced += 1
            if produced >= limit:
                break

    def make_document(self, native_id: str, **fields: Any) -> Document:
        """Helper to build a Document with this source's identity applied."""
        fields.setdefault("kind", self.default_kind)
        return Document(
            id=Document.make_id(self.name, native_id),
            source=self.name,
            **fields,
        )

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "description": self.description,
            "default_kind": str(self.default_kind),
            "supports_query": self.supports_query,
        }
