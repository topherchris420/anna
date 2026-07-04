"""
Z-Library data importer for Anna's Archive.

Imports Z-Library book data from SQL dumps.
"""

import re
import io
from pathlib import Path
from typing import Dict, List, Optional, Iterator
import pymysql

from .base import BaseImporter, ImportRegistry, logger


@ImportRegistry.register("zlib")
class ZLibImporter(BaseImporter):
    """
    Importer for Z-Library data.

    Expected input:
    - SQL dump file from pilimi.org (e.g., pilimi-zlib2-index-2022-08-24-fixed.sql)

    Tables created:
    - zlib_book: Main books table
    - zlib_isbn: ISBN references
    - zlib_ipfs: IPFS references
    """

    source_name = "zlib"
    table_name = "zlib_book"
    batch_size = 500

    @property
    def required_files(self) -> List[str]:
        return ["pilimi-zlib2-index.sql"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """
        Transform raw Z-Library record.

        Args:
            raw_record: Raw record from SQL

        Returns:
            Transformed record
        """
        # Map SQL fields to our schema
        transformed = {
            'zlibrary_id': raw_record.get('id'),
            'title': raw_record.get('title', ''),
            'author': raw_record.get('authors', ''),
            'publisher': raw_record.get('publisher', ''),
            'year': raw_record.get('year', ''),
            'language': raw_record.get('language', ''),
            'extension': raw_record.get('extension', ''),
            'filesize': raw_record.get('filesize', 0),
            'md5': raw_record.get('md5', ''),
            'md5_reported': raw_record.get('md5_reported', ''),
            'filesize_reported': raw_record.get('filesize_reported', 0),
            'description': raw_record.get('descr', ''),
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        """Validate Z-Library record."""
        return bool(record.get('zlibrary_id') and record.get('title'))

    def _import_sql_file(self, filepath: Path) -> Iterator[Dict]:
        """
        Import SQL dump file.

        Args:
            filepath: Path to SQL file

        Yields:
            Records from SQL
        """
        # Read SQL file and execute statements
        sql_content = filepath.read_text(encoding='utf-8')

        # Split into individual INSERT statements
        # This is a simplified parser - for production, use mysql CLI
        pattern = r"INSERT INTO `?books`? .*? VALUES (.*?);"
        matches = re.findall(pattern, sql_content, re.DOTALL | re.IGNORECASE)

        for match in matches:
            # Parse the VALUES
            values = self._parse_sql_values(match)
            if values:
                yield self._sql_values_to_record(values)

    def _parse_sql_values(self, values_str: str) -> Optional[List]:
        """Parse SQL VALUES string into list of values."""
        # Simple parser for SQL VALUES
        values_str = values_str.strip().strip('()')

        # Handle quoted strings
        result = []
        current = ""
        in_string = False
        escape_next = False

        for char in values_str:
            if escape_next:
                current += char
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == "'" and not in_string:
                in_string = True
                continue
            elif char == "'" and in_string:
                in_string = False
                continue

            if char == ',' and not in_string:
                result.append(current.strip().strip("'"))
                current = ""
            else:
                current += char

        if current:
            result.append(current.strip().strip("'"))

        return result if result else None

    def _sql_values_to_record(self, values: List) -> Dict:
        """Convert SQL VALUES list to record dict."""
        # Map based on Z-Library schema
        fields = [
            'id', 'title', 'authors', 'publisher', 'year',
            'language', 'extension', 'filesize', 'md5',
            'descr', 'md5_reported', 'filesize_reported'
        ]

        record = {}
        for i, field in enumerate(fields):
            if i < len(values):
                record[field] = values[i]

        return record

    def import_from_sql(self, sql_file: Path) -> Iterator[Dict]:
        """
        Import from SQL dump file.

        Args:
            sql_file: Path to SQL dump

        Yields:
            Records from dump
        """
        logger.info(f"Importing SQL file: {sql_file}")

        # For large SQL files, use streaming parser
        with open(sql_file, 'r', encoding='utf-8', errors='ignore') as f:
            buffer = ""
            in_insert = False

            for line in f:
                buffer += line

                if 'INSERT INTO `books`' in line or 'INSERT INTO books' in line:
                    in_insert = True

                if in_insert and ';' in line:
                    # Extract and process the INSERT statement
                    yield from self._process_sql_buffer(buffer)
                    buffer = ""
                    in_insert = False

    def _process_sql_buffer(self, buffer: str) -> Iterator[Dict]:
        """Process SQL buffer containing INSERT statements."""
        # Find all VALUES blocks
        import re
        pattern = r"VALUES\s*(.*?);"
        matches = re.findall(pattern, buffer, re.DOTALL | re.IGNORECASE)

        for match in matches:
            # Split by ),(
            records = re.split(r'\),\(', match)
            for i, record_str in enumerate(records):
                record_str = record_str.strip().strip('()')
                values = self._parse_sql_values(record_str)
                if values:
                    yield self._sql_values_to_record(values)

    def create_tables(self):
        """Create Z-Library tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `zlib_book` (
            `zlibrary_id` INT NOT NULL AUTO_INCREMENT,
            `title` VARCHAR(500) DEFAULT '',
            `authors` VARCHAR(500) DEFAULT '',
            `publisher` VARCHAR(255) DEFAULT '',
            `year` VARCHAR(50) DEFAULT '',
            `language` VARCHAR(50) DEFAULT '',
            `extension` VARCHAR(20) DEFAULT '',
            `filesize` BIGINT DEFAULT 0,
            `md5` CHAR(32) DEFAULT '',
            `md5_reported` CHAR(32) DEFAULT '',
            `filesize_reported` BIGINT DEFAULT 0,
            `descr` LONGTEXT,
            PRIMARY KEY (`zlibrary_id`),
            KEY `md5` (`md5`),
            KEY `md5_reported` (`md5_reported`),
            KEY `title` (`title`(200))
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `zlib_isbn` (
            `zlibrary_id` INT NOT NULL,
            `isbn` VARCHAR(20) DEFAULT '',
            KEY `zlibrary_id` (`zlibrary_id`),
            KEY `isbn` (`isbn`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `zlib_ipfs` (
            `zlibrary_id` INT NOT NULL,
            `ipfs_cid` CHAR(62) NOT NULL,
            PRIMARY KEY (`zlibrary_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("Z-Library tables created")


@ImportRegistry.register("zlib_isbn")
class ZLibIsbnImporter(BaseImporter):
    """Importer for Z-Library ISBN data."""

    source_name = "zlib_isbn"
    table_name = "zlib_isbn"
    batch_size = 1000

    @property
    def required_files(self) -> List[str]:
        return ["zlib_isbn.csv"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        return {
            'zlibrary_id': raw_record.get('zlibrary_id'),
            'isbn': raw_record.get('isbn', '')
        }

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('zlibrary_id') and record.get('isbn'))


@ImportRegistry.register("zlib_ipfs")
class ZLibIpfsImporter(BaseImporter):
    """Importer for Z-Library IPFS data."""

    source_name = "zlib_ipfs"
    table_name = "zlib_ipfs"
    batch_size = 1000

    @property
    def required_files(self) -> List[str]:
        return ["zlib_ipfs.csv"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        return {
            'zlibrary_id': raw_record.get('zlibrary_id'),
            'ipfs_cid': raw_record.get('ipfs_cid', '')
        }

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('zlibrary_id') and record.get('ipfs_cid'))