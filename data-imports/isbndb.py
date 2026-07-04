"""
ISBNdb data importer for Anna's Archive.

Imports ISBNdb book metadata.
"""

from pathlib import Path
from typing import Dict, List, Optional, Iterator
import json

from .base import BaseImporter, ImportRegistry, logger


@ImportRegistry.register("isbndb")
class IsbnDbImporter(BaseImporter):
    """
    Importer for ISBNdb data.

    Expected input:
    - isbndb_2022_09.jsonl (from pilimi.org)

    Tables created:
    - isbndb_isbns: ISBN metadata
    """

    source_name = "isbndb"
    table_name = "isbndb_isbns"
    batch_size = 500

    @property
    def required_files(self) -> List[str]:
        return ["isbndb.jsonl"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """Transform ISBNdb record."""
        # Extract ISBN-13 and ISBN-10
        isbn13 = raw_record.get('isbn13', '')
        isbn10 = raw_record.get('isbn10', '')

        # Build JSON with metadata
        metadata = {
            'title': raw_record.get('title', ''),
            'title_long': raw_record.get('title_long', ''),
            'authors': raw_record.get('authors', []),
            'publisher': raw_record.get('publisher', ''),
            'year': raw_record.get('year', ''),
            'language': raw_record.get('language', ''),
            'isbn10': isbn10,
            'isbn13': isbn13,
            'pages': raw_record.get('pages', ''),
            'weight': raw_record.get('weight', ''),
            'dimensions': raw_record.get('dimensions', ''),
            'price': raw_record.get('price', ''),
            'image': raw_record.get('image', ''),
            'link': raw_record.get('link', ''),
        }

        transformed = {
            'isbn13': isbn13,
            'isbn10': isbn10,
            'json': json.dumps(metadata),
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('isbn13') or record.get('isbn10'))

    def create_tables(self):
        """Create ISBNdb tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `isbndb_isbns` (
            `isbn13` CHAR(13) CHARACTER SET utf8mb3 COLLATE utf8mb3_bin NOT NULL,
            `isbn10` CHAR(10) CHARACTER SET utf8mb3 COLLATE utf8mb3_bin NOT NULL,
            `json` LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
            PRIMARY KEY (`isbn13`, `isbn10`),
            KEY `isbn10` (`isbn10`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("ISBNdb tables created")


# CLI command to import ISBNdb
def import_isbndb(jsonl_file: str = "isbndb.jsonl.gz"):
    """
    Import ISBNdb JSONL file.

    Usage:
        # Decompress first
        gzip -d isbndb.jsonl.gz

        # Then import
        ./run flask cli import_isbndb isbndb.jsonl

    Args:
        jsonl_file: Path to ISBNdb JSONL file
    """
    importer = IsbnDbImporter()
    importer.create_tables()

    progress = importer.import_files([Path(jsonl_file)])
    print(f"Import complete: {progress.summary()}")


# Cleanup SQL (for reference)
CLEANUP_SQL = """
CREATE TABLE `isbndb_isbns` (
    `isbn13` CHAR(13) CHARACTER SET utf8mb3 COLLATE utf8mb3_bin NOT NULL,
    `isbn10` CHAR(10) CHARACTER SET utf8mb3 COLLATE utf8mb3_bin NOT NULL,
    `json` LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`json`)),
    PRIMARY KEY (`isbn13`, `isbn10`),
    KEY `isbn10` (`isbn10`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""