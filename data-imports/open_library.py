"""
Open Library data importer for Anna's Archive.

Imports Open Library data dumps.
"""

from pathlib import Path
from typing import Dict, List, Optional, Iterator
import json
import re

from .base import BaseImporter, ImportRegistry, logger


@ImportRegistry.register("open_library")
class OpenLibraryImporter(BaseImporter):
    """
    Importer for Open Library data.

    Expected input:
    - ol_dump_latest.txt (from https://openlibrary.org/data/ol_dump_latest.txt.gz)

    Tables created:
    - ol_base: Raw JSON data
    - ol_isbn13: ISBN index (optional, built after import)
    """

    source_name = "open_library"
    table_name = "ol_base"
    batch_size = 1000

    @property
    def required_files(self) -> List[str]:
        return ["ol_dump_latest.txt"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """Transform Open Library record."""
        # Open Library dump format: tab-separated with JSON in last field
        # Format: type\tol_key\trevision\tlast_modified\tjson

        try:
            json_data = json.loads(raw_record.get('json', '{}'))
        except json.JSONDecodeError:
            json_data = {}

        transformed = {
            'type': raw_record.get('type', ''),
            'ol_key': raw_record.get('ol_key', ''),
            'revision': raw_record.get('revision', 0),
            'last_modified': raw_record.get('last_modified', ''),
            'json': json.dumps(json_data),  # Store as JSON string
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('ol_key'))

    def _import_file(self, filepath: Path, **kwargs) -> Iterator[Dict]:
        """
        Import Open Library dump file.

        Format: tab-separated with JSON in last field
        """
        logger.info(f"Importing Open Library dump: {filepath}")

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                # Parse tab-separated fields
                parts = line.split('\t')
                if len(parts) < 5:
                    logger.warning(f"Skipping line {line_num}: not enough fields")
                    continue

                # Clean null bytes from JSON
                json_str = parts[4].replace('\\u0000', '')

                yield {
                    'type': parts[0],
                    'ol_key': parts[1],
                    'revision': int(parts[2]) if parts[2].isdigit() else 0,
                    'last_modified': parts[3],
                    'json': json_str
                }

    def create_tables(self):
        """Create Open Library tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `ol_base` (
            `type` CHAR(40) CHARACTER SET utf8 COLLATE utf8_bin NOT NULL,
            `ol_key` CHAR(250) CHARACTER SET utf8 COLLATE utf8_bin NOT NULL,
            `revision` INTEGER NOT NULL,
            `last_modified` DATETIME NOT NULL,
            `json` JSON NOT NULL,
            PRIMARY KEY (`ol_key`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        -- ISBN index table (optional, built after main import)
        CREATE TABLE IF NOT EXISTS `ol_isbn13` (
            `isbn` CHAR(13) NOT NULL,
            `ol_key` CHAR(250) NOT NULL,
            PRIMARY KEY (`isbn`, `ol_key`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("Open Library tables created")

    def build_isbn_index(self):
        """Build ISBN index from imported data."""
        logger.info("Building ISBN index...")

        build_sql = """
        SET SESSION myisam_sort_buffer_size = 75*1024*1024*1024;

        CREATE TABLE ol_isbn13_new ENGINE=MyISAM IGNORE
        SELECT x.isbn AS isbn, b.ol_key
        FROM ol_base b
        CROSS JOIN JSON_TABLE(b.json, '$.isbn_13[*]' COLUMNS (isbn CHAR(13) PATH '$')) x
        WHERE b.ol_key LIKE '/books/OL%';

        RENAME TABLE ol_isbn13 TO ol_isbn13_old;
        RENAME TABLE ol_isbn13_new TO ol_isbn13;
        DROP TABLE IF EXISTS ol_isbn13_old;
        """

        with self.engine.connect() as conn:
            conn.execute(text(build_sql))
            conn.commit()

        logger.info("ISBN index built")


# CLI command to import Open Library
def import_open_library(dump_file: str = "ol_dump_latest.txt"):
    """
    Import Open Library dump.

    Usage:
        ./run flask cli import_open_library /path/to/ol_dump_latest.txt

    Args:
        dump_file: Path to Open Library dump file
    """
    importer = OpenLibraryImporter()
    importer.create_tables()

    progress = importer.import_files([Path(dump_file)])
    print(f"Import complete: {progress.summary()}")

    # Optionally build ISBN index
    # importer.build_isbn_index()


# Cleanup SQL (for reference)
CLEANUP_SQL = """
-- Optimize table
SET SESSION myisam_sort_buffer_size = 75*1024*1024*1024;

-- Add primary key
ALTER TABLE ol_base ADD PRIMARY KEY(ol_key);

-- Create ISBN index (~37 mins)
CREATE TABLE ol_isbn13 (PRIMARY KEY(isbn, ol_key)) ENGINE=MyISAM IGNORE
SELECT x.isbn AS isbn, ol_key
FROM ol_base b
CROSS JOIN JSON_TABLE(b.json, '$.isbn_13[*]' COLUMNS (isbn CHAR(13) PATH '$')) x
WHERE ol_key LIKE '/books/OL%';
"""