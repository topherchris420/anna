"""Background indexing tasks (Celery).

These run on the existing Celery worker so ingestion crawls don't block web
requests. They are declared with ``shared_task`` to avoid importing the Flask
app's Celery instance at module load. Register them by adding ``engine.tasks``
to ``CELERY_CONFIG['include']`` (already done in ``config/settings.py``).

Enqueue from anywhere::

    from engine.tasks import ingest_source
    ingest_source.delay("arxiv", query="cat:eess.SY", limit=500)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from celery import shared_task
except Exception:  # pragma: no cover - celery optional at import time

    def shared_task(*dargs, **dkwargs):  # type: ignore
        def wrap(fn):
            return fn

        return (
            wrap if not (len(dargs) == 1 and callable(dargs[0])) else dargs[0]
        )


@shared_task(name="engine.ingest_source", bind=False)
def ingest_source(
    source: str,
    query: Optional[str] = None,
    limit: int = 200,
    batch_size: int = 64,
    **fetch_kwargs: Any,
) -> Dict[str, Any]:
    """Ingest a source in the background; returns ingestion stats as a dict."""
    from engine.ingest import IngestionPipeline

    stats = IngestionPipeline().run(
        source,
        query=query,
        limit=limit,
        batch_size=batch_size,
        **fetch_kwargs,
    )
    return stats.as_dict()


@shared_task(name="engine.reindex_all", bind=False)
def reindex_all(limit_per_source: int = 100) -> Dict[str, Any]:
    """Refresh the index by re-ingesting a slice of every registered source."""
    from engine.ingest import IngestionPipeline, plugin_names

    pipeline = IngestionPipeline()
    summary: Dict[str, Any] = {}
    for name in plugin_names():
        try:
            summary[name] = pipeline.run(name, limit=limit_per_source).as_dict()
        except Exception as exc:
            summary[name] = {"error": str(exc)}
    return summary
