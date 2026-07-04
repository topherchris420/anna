# Data Imports for Anna's Archive

This module provides automated data import scripts for various data sources.

## Available Data Sources

- **Z-Library** (`zlib`): Book metadata from Z-Library
- **Library Genesis RS** (`libgen_rs`): Non-fiction from LibGen RS fork
- **Library Genesis RS Fiction** (`libgenrs_fiction`): Fiction from LibGen RS fork
- **Library Genesis LI** (`libgen_li`): New LibGen LI fork
- **Open Library** (`open_library`): Open Library dumps
- **ISBNdb** (`isbndb`): ISBN metadata

## Quick Start

### List Available Importers

```bash
./run flask cli list_importers
```

### Check Import Status

```bash
./run flask cli import_status
```

### Import All Sources

```bash
./run flask cli import_all
```

### Import Individual Sources

```bash
# Z-Library
./run flask cli import_zlib /path/to/pilimi-zlib2-index.sql

# LibGen RS (non-fiction)
./run flask cli import_libgen_rs /path/to/libgen.sql

# LibGen RS Fiction
./run flask cli import_libgen_rs /path/to/fiction.sql --fiction

# LibGen LI
./run flask cli import_libgen_li /path/to/libgen_new.sql

# Open Library
./run flask cli import_open_library /path/to/ol_dump_latest.txt

# ISBNdb
./run flask cli import_isbndb /path/to/isbndb.jsonl
```

## Resume Interrupted Import

```bash
./run flask cli import_resume <source_name>
```

## Dry Run Mode

Test imports without writing to database:

```bash
./run flask cli import_zlib /path/to/file.sql --dry-run
```

## Using Make

```bash
# Import all sources
make import-all

# Import specific source
make import-zlib

# Check status
make import-status
```

## Data Source Details

### Z-Library

1. Get `pilimi-zlib2-index-2022-08-24-fixed.sql` from pilimi.org
2. Run:
   ```bash
   ./run flask cli import_zlib pilimi-zlib2-index-2022-08-24-fixed.sql
   ```

### Library Genesis RS

1. Get `libgen.sql` and `fiction.sql` from http://libgen.rs/dbdumps/
2. Run:
   ```bash
   ./run flask cli import_libgen_rs libgen.sql
   ./run flask cli import_libgen_rs fiction.sql --fiction
   ```

### Library Genesis LI

1. Get MyISAM tables from https://libgen.li/dirlist.php?dir=dbdumps
2. Run:
   ```bash
   ./run flask cli import_libgen_li libgen_new.sql
   ```

### Open Library

1. Download: `wget https://openlibrary.org/data/ol_dump_latest.txt.gz`
2. Decompress: `gzip -d ol_dump_latest.txt.gz`
3. Run:
   ```bash
   ./run flask cli import_open_library ol_dump_latest.txt --build-index
   ```

### ISBNdb

1. Get `isbndb_2022_09.jsonl.gz` from pilimi.org
2. Decompress: `gzip -d isbndb.jsonl.gz`
3. Run:
   ```bash
   ./run flask cli import_isbndb isbndb.jsonl
   ```

## Building Derived Data

After importing, build the computed tables and search indexes:

```bash
./run flask cli mysql_build_computed_all_md5s
./run flask cli elastic_reset_md5_dicts
./run flask cli elastic_build_md5_dicts
```

## Progress Tracking

The importer creates checkpoint files for resume capability:

- `.checkpoint_<source>.json` - Checkpoint data
- `imports.log` - Import log

To clear checkpoints and start fresh:

```bash
rm .checkpoint_*.json
```

## Architecture

The import system uses:
- `base.py`: Base importer class with:
  - Progress tracking (`ImportProgress`)
  - Error handling with retry logic
  - Batch processing
  - Checkpoint/resume support
  - Logging
- Individual importer modules for each source
- Registry pattern for importer discovery
- Click-based CLI commands

## Development

### Adding New Importers

1. Create new module in `data-imports/`
2. Import `BaseImporter` and `ImportRegistry`
3. Implement importer class:
   ```python
   @ImportRegistry.register("my_source")
   class MyImporter(BaseImporter):
       source_name = "my_source"
       table_name = "my_table"
       
       @property
       def required_files(self):
           return ["my_file.sql"]
       
       def _transform_record(self, raw_record):
           return transformed_record
   ```
4. Add CLI command in `__init__.py`

### Running Tests

```bash
# All tests
make test

# Import tests only
./run pytest test/test_imports.py

# With coverage
make test-coverage
```

## Troubleshooting

### Memory Issues

If you hit memory limits, reduce batch size:
```python
class MyImporter(BaseImporter):
    batch_size = 100  # Reduced from 500
```

### Slow Imports

- Use `--dry-run` to test first
- Check checkpoint file for resume point
- Adjust thread/process settings in CLI

### Database Errors

- Ensure database is running: `./run mysql allthethings`
- Check table schema matches importer expectations
- Use `--resume` to recover from interruptions