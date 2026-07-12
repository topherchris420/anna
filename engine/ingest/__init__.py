"""Modular, plugin-based ingestion pipeline.

Every knowledge source is a :class:`~engine.ingest.base.SourcePlugin` that knows
how to *fetch* raw records and *normalize* them into the unified
:class:`~engine.documents.Document` schema. Plugins register themselves via the
``@register`` decorator, so adding a new source is a matter of dropping a module
into :mod:`engine.ingest.sources` — no changes to the pipeline or the app.

The :class:`~engine.ingest.pipeline.IngestionPipeline` ties fetch → normalize →
embed → index together and is what the CLI and background workers drive.
"""

# Importing the sources package triggers plugin self-registration.
from engine.ingest.base import (  # noqa: F401
    SourcePlugin,
    register,
    get_plugin,
    all_plugins,
    plugin_names,
)
from engine.ingest.pipeline import IngestionPipeline  # noqa: F401

# Register all built-in sources.
from engine.ingest import sources  # noqa: F401,E402

__all__ = [
    "SourcePlugin",
    "register",
    "get_plugin",
    "all_plugins",
    "plugin_names",
    "IngestionPipeline",
]
