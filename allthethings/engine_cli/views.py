"""Flask CLI commands for the Engineering Intelligence platform.

Exposed under ``flask engine …``::

    ./run flask engine index-init            # create the ES hybrid index
    ./run flask engine index-reset           # drop + recreate the index
    ./run flask engine status                # index + document counts
    ./run flask engine sources               # list registered sources
    ./run flask engine ingest arxiv -q "cat:eess.SY" -n 200
    ./run flask engine ingest github -q "topic:rtos stars:>1000" -n 100
    ./run flask engine collections-init      # create the collections tables
    ./run flask engine demo                  # index a few offline sample docs
"""

from __future__ import annotations

import json

import click
from flask import Blueprint

engine_cli = Blueprint("engine_cli", __name__, cli_group="engine")


@engine_cli.cli.command("index-init")
def index_init():
    """Create the engineering-docs Elasticsearch index if absent."""
    from engine import backend as es_index

    es_index.create_index()
    click.echo(f"Index ready: {es_index.get_config().index_name}")


@engine_cli.cli.command("index-reset")
def index_reset():
    """Drop and recreate the engineering-docs index."""
    from engine import backend as es_index

    es_index.reset_index()
    click.echo(f"Index reset: {es_index.get_config().index_name}")


@engine_cli.cli.command("status")
def status():
    """Show index existence and document count."""
    from engine import backend as es_index

    config = es_index.get_config()
    try:
        exists = es_index.index_exists(config)
        total = es_index.count(config) if exists else 0
        click.echo(
            json.dumps(
                {
                    "index": config.index_name,
                    "exists": exists,
                    "documents": total,
                },
                indent=2,
            )
        )
    except Exception as exc:
        click.echo(f"Elasticsearch unavailable: {exc}", err=True)


@engine_cli.cli.command("sources")
def sources():
    """List all registered ingestion sources."""
    from engine.ingest import all_plugins

    for plugin in all_plugins():
        info = plugin.info()
        click.echo(f"{info['name']:<14} {info['display_name']}")
        click.echo(f"{'':<14} {info['description']}")


@engine_cli.cli.command("ingest")
@click.argument("source")
@click.option(
    "--query", "-q", default=None, help="Source-specific query string."
)
@click.option(
    "--limit", "-n", default=100, show_default=True, help="Max documents."
)
@click.option("--batch-size", "-b", default=64, show_default=True)
@click.option("--no-embed", is_flag=True, help="Skip embedding generation.")
@click.option(
    "--dry-run", is_flag=True, help="Fetch + normalize only; do not index."
)
@click.option(
    "--code", is_flag=True, help="(github) ingest code files instead of repos."
)
def ingest(source, query, limit, batch_size, no_embed, dry_run, code):
    """Ingest documents from SOURCE (e.g. arxiv, github, nasa, doe, ...)."""
    from engine.ingest import IngestionPipeline, plugin_names

    if source not in plugin_names():
        raise click.BadParameter(
            f"Unknown source {source!r}. Available: {', '.join(plugin_names())}"
        )

    pipeline = IngestionPipeline()
    fetch_kwargs = {}
    if code:
        fetch_kwargs["code"] = True

    def _progress(stats):
        click.echo(
            f"  fetched={stats.fetched} embedded={stats.embedded} indexed={stats.indexed}"
        )

    stats = pipeline.run(
        source,
        query=query,
        limit=limit,
        batch_size=batch_size,
        embed=not no_embed,
        do_index=not dry_run,
        progress=_progress,
        **fetch_kwargs,
    )
    click.echo(json.dumps(stats.as_dict(), indent=2))


@engine_cli.cli.command("collections-init")
def collections_init():
    """Create the collections/bookmarks database tables."""
    from engine.collections import get_store

    get_store().init_db()
    click.echo("Collections tables ready.")


@engine_cli.cli.command("demo")
def demo():
    """Index a handful of offline sample documents (no network required).

    Useful for trying the UI/search without running an ingestion crawl.
    """
    from engine.documents import Document, DocumentKind
    from engine.ingest import IngestionPipeline

    samples = [
        Document(
            id=Document.make_id("arxiv", "demo-1"),
            source="arxiv",
            kind=DocumentKind.PAPER,
            title="Model Predictive Control of Quadrotor UAVs",
            abstract=(
                "We present a real-time model predictive control (MPC) scheme for "
                "quadrotor trajectory tracking, solving a constrained QP at 100 Hz."
            ),
            authors=["A. Researcher", "B. Engineer"],
            published="2023-05-01",
            categories=["eess.SY"],
            url="https://arxiv.org/abs/demo-1",
            language="en",
        ),
        Document(
            id=Document.make_id("github", "demo/rtos"),
            source="github",
            kind=DocumentKind.REPOSITORY,
            title="demo/tiny-rtos",
            abstract="A minimal preemptive real-time operating system for ARM Cortex-M.",
            tags=["rtos", "cortex-m", "embedded"],
            language="C",
            popularity=1200,
            url="https://github.com/demo/tiny-rtos",
        ),
        Document(
            id=Document.make_id("espressif", "demo-dma"),
            source="espressif",
            kind=DocumentKind.DOCUMENTATION,
            title="ESP32 DMA and Circular Buffers",
            abstract=(
                "The ESP32 DMA engine supports circular (ping-pong) buffers for "
                "continuous ADC and I2S data acquisition without CPU intervention."
            ),
            version="v5.1",
            tags=["esp32", "dma"],
            language="en",
            url="https://docs.espressif.com/projects/esp-idf/en/v5.1/esp32/",
        ),
    ]
    stats = IngestionPipeline().index_documents(samples, source="demo")
    click.echo(json.dumps(stats.as_dict(), indent=2))
    click.echo("Try: http://localhost:8000/search?q=circular+buffer+dma")
