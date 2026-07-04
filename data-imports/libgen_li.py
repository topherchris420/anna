"""
Library Genesis ".li-fork" data importer for Anna's Archive.

Imports LibGen LI (new fork) data from MySQL dumps.
"""

from pathlib import Path
from typing import Dict, List, Optional, Iterator

from .base import BaseImporter, ImportRegistry, logger


@ImportRegistry.register("libgen_li")
class LibGenLiImporter(BaseImporter):
    """
    Importer for Library Generation LI (new fork) data.

    Tables created:
    - libgenli_files: Main files table
    - libgenli_files_add_descr: Additional descriptions
    - libgenli_editions: Editions table
    - libgenli_editions_to_files: Many-to-many relation
    - libgenli_editions_add_descr: Edition descriptions
    - libgenli_publishers: Publishers
    - libgenli_series: Series
    - libgenli_series_add_descr: Series descriptions
    - libgenli_elem_descr: Element descriptions
    """

    source_name = "libgen_li"
    table_name = "libgenli_files"
    batch_size = 500

    @property
    def required_files(self) -> List[str]:
        return ["libgen_new.sql"]

    def _transform_record(self, raw_record: Dict) -> Optional[Dict]:
        """Transform LibGen LI file record."""
        transformed = {
            'f_id': raw_record.get('f_id'),
            'md5': raw_record.get('md5', ''),
            'sha1': raw_record.get('sha1', ''),
            ' crc32': raw_record.get('crc32', ''),
            'file_name': raw_record.get('file_name', ''),
            'file_size': raw_record.get('file_size', 0),
            'extension': raw_record.get('extension', ''),
            'librarian_email': raw_record.get('librarian_email', ''),
            'added_date': raw_record.get('added_date', ''),
            'last_modified': raw_record.get('last_modified', ''),
            'status': raw_record.get('status', ''),
            'libgen_topic': raw_record.get('libgen_topic', ''),
        }
        return transformed

    def _validate_record(self, record: Dict) -> bool:
        return bool(record.get('f_id') and record.get('md5'))

    def create_tables(self):
        """Create LibGen LI tables."""
        create_sql = """
        CREATE TABLE IF NOT EXISTS `libgenli_files` (
            `f_id` INT NOT NULL AUTO_INCREMENT,
            `md5` CHAR(32) DEFAULT '',
            `sha1` CHAR(40) DEFAULT '',
            `crc32` CHAR(8) DEFAULT '',
            `file_name` VARCHAR(500) DEFAULT '',
            `file_size` BIGINT DEFAULT 0,
            `extension` VARCHAR(20) DEFAULT '',
            `librarian_email` VARCHAR(255) DEFAULT '',
            `added_date` DATETIME DEFAULT NULL,
            `last_modified` DATETIME DEFAULT NULL,
            `status` VARCHAR(50) DEFAULT '',
            `libgen_topic` VARCHAR(100) DEFAULT '',
            PRIMARY KEY (`f_id`),
            KEY `md5` (`md5`),
            KEY `file_name` (`file_name`(200))
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_files_add_descr` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `f_id` INT DEFAULT NULL,
            `key` INT DEFAULT NULL,
            `value` LONGTEXT,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `f_id` (`f_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_editions` (
            `e_id` INT NOT NULL AUTO_INCREMENT,
            `title` VARCHAR(500) DEFAULT '',
            `subtitle` VARCHAR(255) DEFAULT '',
            `authors` VARCHAR(500) DEFAULT '',
            `year` VARCHAR(50) DEFAULT '',
            `month` VARCHAR(50) DEFAULT '',
            `date_added` DATETIME DEFAULT NULL,
            `last_modified` DATETIME DEFAULT NULL,
            `language` VARCHAR(50) DEFAULT '',
            `volume` VARCHAR(50) DEFAULT '',
            `edition` VARCHAR(50) DEFAULT '',
            `pages` VARCHAR(50) DEFAULT '',
            `description` LONGTEXT,
            `s_id` INT DEFAULT NULL,
            `issue` VARCHAR(50) DEFAULT '',
            PRIMARY KEY (`e_id`),
            KEY `title` (`title`(200))
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_editions_to_files` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `e_id` INT DEFAULT NULL,
            `f_id` INT DEFAULT NULL,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `e_id` (`e_id`),
            KEY `f_id` (`f_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_editions_add_descr` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `e_id` INT DEFAULT NULL,
            `key` INT DEFAULT NULL,
            `value` LONGTEXT,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `e_id` (`e_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_publishers` (
            `p_id` INT NOT NULL AUTO_INCREMENT,
            `name` VARCHAR(255) DEFAULT '',
            `abbr` VARCHAR(50) DEFAULT '',
            PRIMARY KEY (`p_id`),
            KEY `name` (`name`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_series` (
            `s_id` INT NOT NULL AUTO_INCREMENT,
            `name` VARCHAR(255) DEFAULT '',
            `description` LONGTEXT,
            PRIMARY KEY (`s_id`),
            KEY `name` (`name`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_series_add_descr` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `s_id` INT DEFAULT NULL,
            `key` INT DEFAULT NULL,
            `value` LONGTEXT,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `s_id` (`s_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        CREATE TABLE IF NOT EXISTS `libgenli_elem_descr` (
            `id` INT NOT NULL AUTO_INCREMENT,
            `f_id` INT DEFAULT NULL,
            `key` INT DEFAULT NULL,
            `value` LONGTEXT,
            `time` DATETIME DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `f_id` (`f_id`)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """

        with self.engine.connect() as conn:
            conn.execute(text(create_sql))
            conn.commit()

        logger.info("LibGen LI tables created")


# Import mapping for tables
TABLE_MAPPING = {
    'files': 'libgenli_files',
    'files_add_descr': 'libgenli_files_add_descr',
    'editions': 'libgenli_editions',
    'editions_to_files': 'libgenli_editions_to_files',
    'editions_add_descr': 'libgenli_editions_add_descr',
    'publishers': 'libgenli_publishers',
    'series': 'libgenli_series',
    'series_add_descr': 'libgenli_series_add_descr',
    'elem_descr': 'libgenli_elem_descr',
}


# Cleanup SQL (for reference)
CLEANUP_SQL = """
-- Drop triggers (list from data-imports/README.md)
DROP TRIGGER IF EXISTS authors_before_ins_tr;
DROP TRIGGER IF EXISTS authors_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS authors_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS authors_add_descr_before_del_tr1;
DROP TRIGGER IF EXISTS editions_before_ins_tr1;
DROP TRIGGER IF EXISTS editions_before_upd_tr1;
DROP TRIGGER IF EXISTS editions_before_del_tr1;
DROP TRIGGER IF EXISTS editions_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS editions_add_descr_after_ins_tr;
DROP TRIGGER IF EXISTS editions_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS editions_add_descr_after_upd_tr;
DROP TRIGGER IF EXISTS editions_add_descr_before_del_tr;
DROP TRIGGER IF EXISTS editions_add_descr_after_del_tr;
DROP TRIGGER IF EXISTS editions_to_files_before_ins_tr;
DROP TRIGGER IF EXISTS editions_to_files_before_upd_tr;
DROP TRIGGER IF EXISTS editions_to_files_before_del_tr;
DROP TRIGGER IF EXISTS files_before_ins_tr;
DROP TRIGGER IF EXISTS files_before_upd_tr;
DROP TRIGGER IF EXISTS files_before_del_tr;
DROP TRIGGER IF EXISTS files_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS files_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS files_add_descr_before_del_tr1;
DROP TRIGGER IF EXISTS publisher_before_ins_tr;
DROP TRIGGER IF EXISTS publisher_before_upd_tr;
DROP TRIGGER IF EXISTS publisher_before_del_tr;
DROP TRIGGER IF EXISTS publisher_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS publisher_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS publisher_add_descr_before_del_tr;
DROP TRIGGER IF EXISTS series_before_ins_tr;
DROP TRIGGER IF EXISTS series_before_upd_tr;
DROP TRIGGER IF EXISTS series_before_del_tr;
DROP TRIGGER IF EXISTS series_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS series_add_descr_after_ins_tr;
DROP TRIGGER IF EXISTS series_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS series_add_descr_after_upd_tr;
DROP TRIGGER IF EXISTS series_add_descr_before_del_tr;
DROP TRIGGER IF EXISTS series_add_descr_after_del_tr;
DROP TRIGGER IF EXISTS works_before_ins_tr;
DROP TRIGGER IF EXISTS works_before_upd_tr;
DROP TRIGGER IF EXISTS works_before_del_tr;
DROP TRIGGER IF EXISTS works_add_descr_before_ins_tr;
DROP TRIGGER IF EXISTS works_add_descr_before_upd_tr;
DROP TRIGGER IF EXISTS works_add_descr_before_del_tr;
DROP TRIGGER IF EXISTS works_to_editions_before_ins_tr;
DROP TRIGGER IF EXISTS works_to_editions_before_upd_tr;
DROP TRIGGER IF EXISTS works_to_editions_before_del_tr;

-- Rename tables
ALTER TABLE libgen_new.elem_descr RENAME allthethings.libgenli_elem_descr;
ALTER TABLE libgen_new.files RENAME allthethings.libgenli_files;
ALTER TABLE libgen_new.editions RENAME allthethings.libgenli_editions;
ALTER TABLE libgen_new.editions_to_files RENAME allthethings.libgenli_editions_to_files;
ALTER TABLE libgen_new.editions_add_descr RENAME allthethings.libgenli_editions_add_descr;
ALTER TABLE libgen_new.files_add_descr RENAME allthethings.libgenli_files_add_descr;
ALTER TABLE libgen_new.series RENAME allthethings.libgenli_series;
ALTER TABLE libgen_new.series_add_descr RENAME allthethings.libgenli_series_add_descr;
ALTER TABLE libgen_new.publishers RENAME allthethings.libgenli_publishers;

-- Drop unnecessary indexes
SET SESSION sql_mode = 'NO_ENGINE_SUBSTITUTION';
ALTER TABLE libgenli_editions DROP INDEX `YEAR`, DROP INDEX `N_YEAR`, DROP INDEX `MONTH`,
    DROP INDEX `MONTH_END`, DROP INDEX `VISIBLE`, DROP INDEX `LG_TOP`, DROP INDEX `TYPE`,
    DROP INDEX `COMMENT`, DROP INDEX `S_ID`, DROP INDEX `DOI`, DROP INDEX `ISSUE`,
    DROP INDEX `DAY`, DROP INDEX `TIME`, DROP INDEX `TIMELM`;
ALTER TABLE libgenli_editions_add_descr DROP INDEX `TIME`, DROP INDEX `VAL3`,
    DROP INDEX `VAL`, DROP INDEX `VAL2`, DROP INDEX `VAL1`, DROP INDEX `VAL_ID`,
    DROP INDEX `VAL_UNIQ`, DROP INDEX `KEY`;
ALTER TABLE libgenli_editions_to_files DROP INDEX `TIME`, DROP INDEX `FID`;
ALTER TABLE libgenli_elem_descr DROP INDEX `key`;
ALTER TABLE libgenli_files DROP INDEX `md5_2`, DROP INDEX `MAGZID`, DROP INDEX `COMICSID`,
    DROP INDEX `LGTOPIC`, DROP INDEX `FICID`, DROP INDEX `FICTRID`, DROP INDEX `SMID`,
    DROP INDEX `STDID`, DROP INDEX `LGID`, DROP INDEX `FSIZE`, DROP INDEX `SMPATH`,
    DROP INDEX `TIME`, DROP INDEX `TIMELM`;
ALTER TABLE libgenli_files_add_descr DROP INDEX `TIME`, DROP INDEX `VAL`, DROP INDEX `KEY`;
ALTER TABLE libgenli_publishers DROP INDEX `TIME`, DROP INDEX `COM`, DROP INDEX `FULLTEXT`;
ALTER TABLE libgenli_series DROP INDEX `LG_TOP`, DROP INDEX `TIME`, DROP INDEX `TYPE`,
    DROP INDEX `VISIBLE`, DROP INDEX `COMMENT`, DROP INDEX `VAL_FULLTEXT`;
ALTER TABLE libgenli_series_add_descr DROP INDEX `TIME`, DROP INDEX `VAL`, DROP INDEX `VAL1`,
    DROP INDEX `VAL2`, DROP INDEX `VAL3`;
"""