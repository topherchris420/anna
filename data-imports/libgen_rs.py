"""
Library Genesis ".rs-fork" data importer for Anna's Archive.

Imports LibGen RS (non-fiction) and LibGen RS Fiction data.
"""

from pathlib import Path
from typing import Dict, List, Optional, Iterator
import re

from .base import BaseImporter, ImportRegistry, logger


@ImportRegistry.register("libgen_rs")
class LibGenRsImporter(BaseImporter):
    """
    Importer for Library Genesis RS (non-fiction) data.

    Tables created:
    - libgenrs_updated: Main non-fiction table
    - libgenrs_description: Descriptions
    - libgenrs_hashes: File hashes
    - libgenrs_topics: Topics/categories
    """

    source_name = "libgenrs"
    table_name = "libgenrs_updated"
    batch_size = 500

    @property
    def required_files(self) -> List[str]:
        return ["libgen.sql"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """Transform LibGen RS record."""
        transformed = {
            'ID': raw_record.get('ID'),
            'Title': raw_record.get('Title', ''),
            'Authors': raw_record.get('Authors', ''),
            'Series': raw_record.get('Series', ''),
            'Publisher': raw_record.get('Publisher', ''),
            'Year': raw_record.get('Year', ''),
            'Language': raw_record.get('Language', ''),
            'Pages': raw_record.get('Pages', ''),
            'ISBN': raw_record.get('ISBN', ''),
            'MD5': raw_record.get('MD5', ''),
            'Size': raw_record.get('Size', ''),
            'Extension': raw_record.get('Extension', ''),
            'TimeAdded': raw_record.get('TimeAdded', ''),
            'TimeModified': raw_record.get('TimeModified', ''),
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        """Validate LibGen RS record."""
        return bool(record.get('ID') and record.get('Title'))

    def create_tables(self):
        """Create LibGen RS tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `libgenrs_updated` (
            `ID` INT NOT NULL AUTO_INCREMENT,
            `Title` VARCHAR(500) DEFAULT '',
            `Series` VARCHAR(255) DEFAULT '',
            `Authors` VARCHAR(500) DEFAULT '',
            `Publisher` VARCHAR(255) DEFAULT '',
            `Year` VARCHAR(50) DEFAULT '',
            `Language` VARCHAR(50) DEFAULT '',
            `Pages` VARCHAR(50) DEFAULT '',
            `ISBN` VARCHAR(50) DEFAULT '',
            `MD5` CHAR(32) DEFAULT '',
            `Size` VARCHAR(50) DEFAULT '',
            `Extension` VARCHAR(20) DEFAULT '',
            `TimeAdded` DATETIME DEFAULT NULL,
            `TimeModified` DATETIME DEFAULT NULL,
            PRIMARY KEY (`ID`),
            KEY `MD5` (`MD5`),
            KEY `Title` (`Title`(200)),
            KEY `Authors` (`Authors`(200))
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenrs_description` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `book_id` INT DEFAULT NULL,
            `description` LONGTEXT,
            PRIMARY KEY (`id`),
            KEY `book_id` (`book_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenrs_hashes` (
            `MD5` CHAR(32) NOT NULL,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`MD5`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenrs_topics` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `book_id` INT DEFAULT NULL,
            `topic` VARCHAR(255) DEFAULT '',
            PRIMARY KEY (`id`),
            KEY `book_id` (`book_id`),
            KEY `topic` (`topic`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("LibGen RS tables created")


@ImportRegistry.register("libgenrs_fiction")
class LibGenRsFictionImporter(BaseImporter):
    """
    Importer for Library Genesis RS Fiction data.
    """

    source_name = "libgenrs_fiction"
    table_name = "libgenrs_fiction"
    batch_size = 500

    @property
    def required_files(self) -> List[str]:
        return ["fiction.sql"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """Transform LibGen RS Fiction record."""
        transformed = {
            'id': raw_record.get('id'),
            'title': raw_record.get('title', ''),
            'authors': raw_record.get('authors', ''),
            'series': raw_record.get('series', ''),
            'publisher': raw_record.get('publisher', ''),
            'year': raw_record.get('year', ''),
            'language': raw_record.get('language', ''),
            'md5': raw_record.get('md5', ''),
            'filesize': raw_record.get('filesize', 0),
            'extension': raw_record.get('extension', ''),
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('id') and record.get('title'))

    def create_tables(self):
        """Create LibGen RS Fiction tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `libgenrs_fiction` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `title` VARCHAR(500) DEFAULT '',
            `authors` VARCHAR(500) DEFAULT '',
            `series` VARCHAR(255) DEFAULT '',
            `publisher` VARCHAR(255) DEFAULT '',
            `year` VARCHAR(50) DEFAULT '',
            `language` VARCHAR(50) DEFAULT '',
            `md5` CHAR(32) DEFAULT '',
            `filesize` BIGINT DEFAULT 0,
            `extension` VARCHAR(20) DEFAULT '',
            PRIMARY KEY (`id`),
            KEY `md5` (`md5`),
            KEY `title` (`title`(200))
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenrs_fiction_description` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `book_id` INT DEFAULT NULL,
            `description` LONGTEXT,
            PRIMARY KEY (`id`),
            KEY `book_id` (`book_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenrs_fiction_hashes` (
            `MD5` CHAR(32) NOT NULL,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`MD5`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("LibGen RS Fiction tables created")


def create_sqlalchemy_tables():
    """Create all LibGen RS tables using SQLAlchemy."""
    from sqlalchemy import text
    from config import settings
    from .base import BaseImporter

    importer = LibGenRsImporter()
    importer.create_tables()

    importer_fiction = LibGenRsFictionImporter()
    importer_fiction.create_tables()


# Cleanup SQL commands (for reference)
CLEANUP_SQL = """
-- Drop triggers
DROP TRIGGER IF EXISTS libgen_description_update_all;
DROP TRIGGER IF EXISTS libgen_updated_update_all;

-- Rename tables
ALTER TABLE updated RENAME libgenrs_updated;
ALTER TABLE description RENAME libgenrs_description;
ALTER TABLE hashes RENAME libgenrs_hashes;
ALTER TABLE topics RENAME libgenrs_topics;

ALTER TABLE fiction RENAME libgenrs_fiction;
ALTER TABLE fiction_description RENAME libgenrs_fiction_description;
ALTER TABLE fiction_hashes RENAME libgenrs_fiction_hashes;

-- Add primary keys
ALTER TABLE libgenrs_hashes ADD PRIMARY KEY(md5);

-- Drop unnecessary indexes
SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION';
ALTER TABLE libgenrs_description DROP INDEX `time`;
ALTER TABLE libgenrs_hashes DROP INDEX `MD5`;
ALTER TABLE libgenrs_updated DROP INDEX `Generic`, DROP INDEX `VisibleTimeAdded`,
    DROP INDEX `TimeAdded`, DROP INDEX `Topic`, DROP INDEX `VisibleID`,
    DROP INDEX `VisibleTimeLastModified`, DROP INDEX `TimeLastModifiedID`,
    DROP INDEX `DOI_INDEX`, DROP INDEX `Identifier`, DROP INDEX `Language`,
    DROP INDEX `Title`, DROP INDEX `Author`, DROP INDEX `Language_FTS`,
    DROP INDEX `Extension`, DROP INDEX `Publisher`, DROP INDEX `Series`,
    DROP INDEX `Year`, DROP INDEX `Title1`, DROP INDEX `Tags`,
    DROP INDEX `Identifierfulltext`;
ALTER TABLE libgenrs_fiction DROP INDEX `Language`, DROP INDEX `TITLE`,
    DROP INDEX `Authors`, DROP INDEX `Series`, DROP INDEX `Title+Authors+Series`,
    DROP INDEX `Identifier`;
"""