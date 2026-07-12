"""Ingestion pipeline: fetch → normalize → embed → index.

Drives a :class:`~engine.ingest.base.SourcePlugin` end to end, batching
embedding and Elasticsearch bulk-indexing for throughput. Used by the CLI
commands and background workers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional

from engine import index as es_index
from engine.config import EngineConfig, get_config
from engine.documents import Document
from engine.embeddings import Embedder, get_embedder
from engine.ingest.base import SourcePlugin, get_plugin


@dataclass
class IngestionStats:
    source: str
    fetched: int = 0
    embedded: int = 0
    indexed: int = 0
    errors: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "fetched": self.fetched,
            "embedded": self.embedded,
            "indexed": self.indexed,
            "errors": self.errors[:20],
            "error_count": len(self.errors),
            "elapsed_s": round(self.elapsed_s, 2),
        }


ProgressFn = Callable[[IngestionStats], None]


class IngestionPipeline:
    def __init__(
        self,
        config: Optional[EngineConfig] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.config = config or get_config()
        self.embedder = embedder or get_embedder()

    # ------------------------------------------------------------------ #
    def run(
        self,
        source: str,
        *,
        query: Optional[str] = None,
        limit: int = 100,
        batch_size: int = 64,
        embed: bool = True,
        do_index: bool = True,
        ensure_index: bool = True,
        progress: Optional[ProgressFn] = None,
        **fetch_kwargs: Any,
    ) -> IngestionStats:
        """Ingest up to ``limit`` documents from ``source``."""
        plugin: SourcePlugin = get_plugin(source, config=self.config)
        stats = IngestionStats(source=plugin.name)
        started = time.time()

        if do_index and ensure_index:
            es_index.create_index(self.config)

        batch: List[Document] = []
        stream = plugin.documents(query=query, limit=limit, **fetch_kwargs)
        for doc in stream:
            stats.fetched += 1
            batch.append(doc)
            if len(batch) >= batch_size:
                self._flush(batch, stats, embed, do_index)
                batch = []
                if progress:
                    progress(stats)
        if batch:
            self._flush(batch, stats, embed, do_index)
            if progress:
                progress(stats)

        stats.elapsed_s = time.time() - started
        return stats

    # ------------------------------------------------------------------ #
    def index_documents(
        self,
        documents: Iterable[Document],
        *,
        source: str = "custom",
        batch_size: int = 64,
        embed: bool = True,
        ensure_index: bool = True,
    ) -> IngestionStats:
        """Embed + index an arbitrary stream of pre-built documents."""
        stats = IngestionStats(source=source)
        started = time.time()
        if ensure_index:
            es_index.create_index(self.config)
        batch: List[Document] = []
        for doc in documents:
            stats.fetched += 1
            batch.append(doc)
            if len(batch) >= batch_size:
                self._flush(batch, stats, embed, True)
                batch = []
        if batch:
            self._flush(batch, stats, embed, True)
        stats.elapsed_s = time.time() - started
        return stats

    # ------------------------------------------------------------------ #
    def _flush(
        self,
        batch: List[Document],
        stats: IngestionStats,
        embed: bool,
        do_index: bool,
    ) -> None:
        if embed:
            try:
                vectors = self.embedder.encode_batch(
                    [d.embedding_text() for d in batch]
                )
                for doc, vec in zip(batch, vectors):
                    doc.embedding = vec
                stats.embedded += len(batch)
            except Exception as exc:  # embedding must not abort ingestion
                stats.errors.append(f"embed: {exc}")

        if do_index:
            try:
                success, errors = es_index.bulk_index(batch, self.config)
                stats.indexed += success
                for err in errors:
                    stats.errors.append(f"index: {err}")
            except Exception as exc:
                stats.errors.append(f"index: {exc}")
