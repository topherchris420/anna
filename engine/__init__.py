"""
Vers3Dynamics Engineering Intelligence — core search engine.

This package layers a modern, AI-powered engineering-knowledge search engine
on top of the existing Elasticsearch + Flask architecture. It is intentionally
decoupled from the legacy ``allthethings`` book search so the two can coexist:

- ``engine.config``      Environment-driven configuration.
- ``engine.documents``   Unified ``Document`` schema shared across all sources.
- ``engine.embeddings``  Local sentence-embedding generation (lazy + fallback).
- ``engine.index``       Elasticsearch hybrid index management (BM25 + kNN).
- ``engine.search``      Hybrid retrieval (BM25 + vector) with RRF fusion.
- ``engine.summarize``   Citation-first AI summaries (optional local LLM).
- ``engine.collections`` User collections and bookmarks (PostgreSQL).
- ``engine.ingest``      Modular, plugin-based ingestion pipeline.

Every heavy dependency (torch, sentence-transformers, pdf parsers, HTTP
clients) is imported lazily so that importing :mod:`engine` never fails and the
legacy application keeps booting even when the ML stack is not installed.
"""

from engine.documents import Document, DocumentKind
from engine.config import EngineConfig, get_config

__all__ = [
    "Document",
    "DocumentKind",
    "EngineConfig",
    "get_config",
    "__version__",
]

__version__ = "0.1.0"
