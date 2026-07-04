"""
Data import CLI commands for Anna's Archive.

Provides CLI commands for importing data from various sources:
- Z-Library
- Library Genesis RS
- Library Genesis LI
- Open Library
- ISBNdb

Usage:
    # Import all data sources
    ./run flask cli import_all

    # Import specific source
    ./run flask cli import_zlib /path/to/zlib.sql
    ./run flask cli import_libgen_rs /path/to/libgen.sql
    ./run flask cli import_libgen_li /path/to/libgen_new.sql
    ./run flask cli import_open_library /path/to/ol_dump_latest.txt
    ./run flask cli import_isbndb /path/to/isbndb.jsonl

    # Show import status
    ./run flask cli import_status

    # Resume interrupted import
    ./run flask cli import_resume zlib
"""

import os
import sys
from pathlib import Path
from typing import Optional, List

import click
from flask import Blueprint
from sqlalchemy import text

from config import settings
from .base import BaseImporter, ImportRegistry, logger
from . import zlib
from . import libgen_rs
from . import libgen_li
from . import open_library
from . import isbndb


cli = Blueprint("imports", __name__)


# =============================================================================
# CLI Commands
# =============================================================================

@imports.cli.command("import_all")
@click.option("--skip-existing/--no-skip-existing", default=True,
              help="Skip records that already exist")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_all(skip_existing: bool = True, dry_run: bool = False):
    """
    Import all data sources in order.

    This runs the full import pipeline for:
    - Z-Library
    - Library Genesis RS (non-fiction)
    - Library Genesis RS Fiction
    - Library Genesis LI
    - Open Library
    - ISBNdb
    """
    click.echo("=" * 60)
    click.echo("Starting full data import")
    click.echo("=" * 60)

    results = {}

    # Import each source
    sources = [
        ("zlib", zlib.ZLibImporter),
        ("libgen_rs", libgen_rs.LibGenRsImporter),
        ("libgenrs_fiction", libgen_rs.LibGenRsFictionImporter),
        ("libgen_li", libgen_li.LibGenLiImporter),
        ("open_library", open_library.OpenLibraryImporter),
        ("isbndb", isbndb.IsbnDbImporter),
    ]

    for source_name, importer_class in sources:
        click.echo(f"\n>>> Importing {source_name}...")
        try:
            importer = importer_class(resume=False, dry_run=dry_run)

            # Create tables if needed
            if hasattr(importer, 'create_tables'):
                click.echo(f"  Creating tables for {source_name}...")
                importer.create_tables()

            # TODO: Import actual files (paths should come from config or args)
            click.echo(f"  Skipping actual import (no files specified)")
            click.echo(f"  Use individual import commands to import specific files")

            results[source_name] = "skipped (no files)"

        except Exception as e:
            click.echo(f"  ERROR: {e}")
            results[source_name] = f"failed: {e}"
            logger.exception(f"Import failed for {source_name}")

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("Import Summary")
    click.echo("=" * 60)
    for source, status in results.items():
        click.echo(f"  {source}: {status}")


@imports.cli.command("import_zlib")
@click.argument("sql_file", type=click.Path(exists=True))
@click.option("--resume/--no-resume", default=False,
              help="Resume from checkpoint")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_zlib(sql_file: str, resume: bool = False, dry_run: bool = False):
    """Import Z-Library data from SQL dump."""
    click.echo(f"Importing Z-Library from: {sql_file}")

    importer = zlib.ZLibImporter(resume=resume, dry_run=dry_run)

    # Create tables
    importer.create_tables()

    # Import file
    progress = importer.import_files([Path(sql_file)])

    click.echo(f"Import complete: {progress.summary()}")


@imports.cli.command("import_libgen_rs")
@click.argument("sql_file", type=click.Path(exists=True))
@click.option("--fiction/--no-fiction", default=False,
              help="Import fiction instead of non-fiction")
@click.option("--resume/--no-resume", default=False,
              help="Resume from checkpoint")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_libgen_rs(sql_file: str, fiction: bool = False,
                     resume: bool = False, dry_run: bool = False):
    """Import Library Genesis RS data from SQL dump."""
    if fiction:
        click.echo(f"Importing LibGen RS Fiction from: {sql_file}")
        importer = libgen_rs.LibGenRsFictionImporter(resume=resume, dry_run=dry_run)
    else:
        click.echo(f"Importing LibGen RS from: {sql_file}")
        importer = libgen_rs.LibGenRsImporter(resume=resume, dry_run=dry_run)

    # Create tables
    importer.create_tables()

    # Import file
    progress = importer.import_files([Path(sql_file)])

    click.echo(f"Import complete: {progress.summary()}")


@imports.cli.command("import_libgen_li")
@click.argument("sql_file", type=click.Path(exists=True))
@click.option("--resume/--no-resume", default=False,
              help="Resume from checkpoint")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_libgen_li(sql_file: str, resume: bool = False, dry_run: bool = False):
    """Import Library Genesis LI data from SQL dump."""
    click.echo(f"Importing LibGen LI from: {sql_file}")

    importer = libgen_li.LibGenLiImporter(resume=resume, dry_run=dry_run)

    # Create tables
    importer.create_tables()

    # Import file
    progress = importer.import_files([Path(sql_file)])

    click.echo(f"Import complete: {progress.summary()}")


@imports.cli.command("import_open_library")
@click.argument("dump_file", type=click.Path(exists=True))
@click.option("--build-index/--no-build-index", default=False,
              help="Build ISBN index after import")
@click.option("--resume/--no-resume", default=False,
              help="Resume from checkpoint")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_open_library(dump_file: str, build_index: bool = False,
                       resume: bool = False, dry_run: bool = False):
    """Import Open Library dump."""
    click.echo(f"Importing Open Library from: {dump_file}")

    importer = open_library.OpenLibraryImporter(resume=resume, dry_run=dry_run)

    # Create tables
    importer.create_tables()

    # Import file
    progress = importer.import_files([Path(dump_file)])

    click.echo(f"Import complete: {progress.summary()}")

    # Build ISBN index if requested
    if build_index:
        click.echo("Building ISBN index...")
        importer.build_isbn_index()
        click.echo("ISBN index complete")


@imports.cli.command("import_isbndb")
@click.argument("jsonl_file", type=click.Path(exists=True))
@click.option("--resume/--no-resume", default=False,
              help="Resume from checkpoint")
@click.option("--dry-run/--no-dry-run", default=False,
              help="Don't actually write to database")
def import_isbndb(jsonl_file: str, resume: bool = False, dry_run: bool = False):
    """Import ISBNdb JSONL file."""
    click.echo(f"Importing ISBNdb from: {jsonl_file}")

    importer = isbndb.IsbnDbImporter(resume=resume, dry_run=dry_run)

    # Create tables
    importer.create_tables()

    # Import file
    progress = importer.import_files([Path(jsonl_file)])

    click.echo(f"Import complete: {progress.summary()}")


@imports.cli.command("import_resume")
@click.argument("source")
def import_resume(source: str):
    """Resume an interrupted import for the given source."""
    importer = ImportRegistry.create(source, resume=True)
    if not importer:
        click.echo(f"Unknown source: {source}")
        click.echo(f"Available sources: {', '.join(ImportRegistry.list_importers())}")
        sys.exit(1)

    click.echo(f"Resuming {source} import...")

    # Get file path from user
    click.echo("Enter the file path to continue importing:")

    # For now, just show status
    progress = importer.progress
    click.echo(f"Last checkpoint: {progress.last_checkpoint}")
    click.echo(f"Records processed: {progress.processed}")
    click.echo(f"Successful: {progress.successful}")
    click.echo(f"Failed: {progress.failed}")


@imports.cli.command("import_status")
def import_status():
    """Show import status for all sources."""
    from .base import ImportProgress

    click.echo("=" * 60)
    click.echo("Import Status")
    click.echo("=" * 60)

    sources = ImportRegistry.list_importers()

    if not sources:
        click.echo("No importers registered")
        return

    for source in sources:
        importer = ImportRegistry.create(source)
        if importer:
            progress = importer.progress
            checkpoint_file = Path(f".checkpoint_{source}.json")

            click.echo(f"\n{source}:")
            if checkpoint_file.exists():
                progress.load_checkpoint()
                click.echo(f"  Status: INCOMPLETE")
                click.echo(f"  Last checkpoint: {progress.last_checkpoint}")
                click.echo(f"  Processed: {progress.processed}")
                click.echo(f"  Successful: {progress.successful}")
                click.echo(f"  Failed: {progress.failed}")
            else:
                click.echo(f"  Status: NOT STARTED or COMPLETED")


@imports.cli.command("list_importers")
def list_importers():
    """List all available importers."""
    click.echo("Available importers:")
    for name in ImportRegistry.list_importers():
        click.echo(f"  - {name}")


# =============================================================================
# Utility Functions
# =============================================================================

def get_source_info(source: str) -> dict:
    """Get information about a data source."""
    importer = ImportRegistry.create(source)
    if not importer:
        return {}

    return {
        'name': importer.source_name,
        'table': importer.table_name,
        'batch_size': importer.batch_size,
        'required_files': importer.required_files,
    }


def validate_import_environment() -> List[str]:
    """
    Validate the import environment.

    Returns:
        List of warnings/errors
    """
    issues = []

    # Check database connection
    try:
        from sqlalchemy import create_engine
        engine = create_engine(settings.SQLALCHEMY_DATABASE_URI)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        issues.append(f"Database connection failed: {e}")

    # Check data directory
    data_dir = Path("data-imports")
    if not data_dir.exists():
        issues.append(f"Data directory not found: {data_dir}")

    # Check required packages
    required = ['tqdm', 'sqlalchemy', 'pymysql']
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            issues.append(f"Missing required package: {pkg}")

    return issues


# Register this module as a Flask CLI command group
# This is done in the parent allthethings/cli/views.py