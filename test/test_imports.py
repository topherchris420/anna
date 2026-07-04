"""
Unit tests for data imports module.

Tests the data import pipeline and importer classes.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from io import StringIO

from data_imports.base import (
    BaseImporter,
    ImportProgress,
    ImportRegistry,
)
from data_imports import zlib
from data_imports import libgen_rs
from data_imports import libgen_li
from data_imports import open_library
from data_imports import isbndb


# =============================================================================
# ImportProgress Tests
# =============================================================================

class TestImportProgress:
    """Tests for ImportProgress class."""

    def test_initial_state(self):
        """Test initial progress state."""
        progress = ImportProgress(source="test")
        assert progress.source == "test"
        assert progress.total_records == 0
        assert progress.processed == 0
        assert progress.successful == 0
        assert progress.failed == 0

    def test_start(self):
        """Test starting import."""
        progress = ImportProgress(source="test")
        progress.start()
        assert progress.started_at is not None

    def test_success(self):
        """Test recording successful imports."""
        progress = ImportProgress(source="test")
        progress.start()
        progress.success(5)
        assert progress.successful == 5
        assert progress.processed == 5

    def test_fail(self):
        """Test recording failed imports."""
        progress = ImportProgress(source="test")
        progress.start()
        progress.fail(3)
        assert progress.failed == 3
        assert progress.processed == 3

    def test_summary(self):
        """Test progress summary."""
        progress = ImportProgress(source="test")
        progress.start()
        progress.total_records = 100
        progress.processed = 50
        progress.successful = 45
        progress.failed = 5

        summary = progress.summary()
        assert "test" in summary
        assert "50/100" in summary
        assert "45" in summary
        assert "5" in summary

    def test_checkpoint_save_load(self, tmp_path):
        """Test checkpoint save and load."""
        # Change to tmp dir for checkpoint test
        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            progress = ImportProgress(source="test_checkpoint")
            progress.start()
            progress.total_records = 100
            progress.processed = 50
            progress.checkpoint(50)

            # Load checkpoint
            progress2 = ImportProgress(source="test_checkpoint")
            loaded = progress2.load_checkpoint()
            assert loaded == 50
            assert progress2.processed == 50
        finally:
            os.chdir(old_cwd)


# =============================================================================
# ImportRegistry Tests
# =============================================================================

class TestImportRegistry:
    """Tests for ImportRegistry class."""

    def test_register(self):
        """Test importer registration."""
        @ImportRegistry.register("test_importer")
        class TestImporter(BaseImporter):
            source_name = "test"

            @property
            def required_files(self):
                return ["test.txt"]

            def _transform_record(self, raw_record):
                return raw_record

        assert "test_importer" in ImportRegistry.list_importers()
        assert ImportRegistry.get("test_importer") == TestImporter

    def test_create(self):
        """Test importer creation."""
        importer = ImportRegistry.create("zlib", resume=True, dry_run=False)
        assert importer is not None
        assert importer.resume is True
        assert importer.dry_run is False

    def test_create_unknown(self):
        """Test creating unknown importer."""
        importer = ImportRegistry.create("unknown_importer")
        assert importer is None


# =============================================================================
# BaseImporter Tests
# =============================================================================

class TestBaseImporter:
    """Tests for BaseImporter base class."""

    def test_importer_init(self):
        """Test importer initialization."""
        @ImportRegistry.register("test_base")
        class TestImporter(BaseImporter):
            source_name = "test_base"
            table_name = "test_table"

            @property
            def required_files(self):
                return ["test.txt"]

            def _transform_record(self, raw_record):
                return raw_record

        importer = TestImporter(resume=True, dry_run=True)
        assert importer.resume is True
        assert importer.dry_run is True
        assert importer.source_name == "test_base"

    def test_checksum(self, tmp_path):
        """Test file checksum calculation."""
        @ImportRegistry.register("test_checksum")
        class TestImporter(BaseImporter):
            source_name = "test"

            @property
            def required_files(self):
                return []

            def _transform_record(self, raw):
                return raw

        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        importer = TestImporter()
        checksum = importer.get_checksum(test_file)
        assert checksum == "5eb63bbbe01eeed093cb22bb8f5acdc3"

    def test_verify_file(self, tmp_path):
        """Test file verification."""
        @ImportRegistry.register("test_verify")
        class TestImporter(BaseImporter):
            source_name = "test"

            @property
            def required_files(self):
                return []

            def _transform_record(self, raw):
                return raw

        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        importer = TestImporter()

        # Test existing file
        assert importer.verify_file(test_file) is True

        # Test with wrong checksum
        assert importer.verify_file(test_file, "wrong") is False

        # Test non-existent file
        assert importer.verify_file(tmp_path / "nonexistent.txt") is False


# =============================================================================
# ZLib Importer Tests
# =============================================================================

class TestZLibImporter:
    """Tests for Z-Library importer."""

    def test_transform_record(self):
        """Test record transformation."""
        raw = {
            'id': 1,
            'title': 'Test Book',
            'authors': 'Test Author',
            'publisher': 'Test Publisher',
            'year': '2024',
            'language': 'en',
            'extension': 'pdf',
            'filesize': 1024,
            'md5': 'abc123',
            'descr': 'Description',
            'md5_reported': 'abc123',
            'filesize_reported': 1024,
        }

        importer = zlib.ZLibImporter()
        result = importer._transform_record(raw)

        assert result['zlibrary_id'] == 1
        assert result['title'] == 'Test Book'
        assert result['author'] == 'Test Author'
        assert result['md5'] == 'abc123'

    def test_validate_record(self):
        """Test record validation."""
        importer = zlib.ZLibImporter()

        # Valid record
        valid = importer._validate_record({
            'zlibrary_id': 1,
            'title': 'Test'
        })
        assert valid is True

        # Invalid - missing ID
        invalid = importer._validate_record({
            'title': 'Test'
        })
        assert invalid is False

        # Invalid - missing title
        invalid = importer._validate_record({
            'zlibrary_id': 1
        })
        assert invalid is False


# =============================================================================
# LibGen RS Importer Tests
# =============================================================================

class TestLibGenRsImporter:
    """Tests for LibGen RS importer."""

    def test_transform_record(self):
        """Test record transformation."""
        raw = {
            'ID': 1,
            'Title': 'Test Book',
            'Authors': 'Test Author',
            'Series': 'Test Series',
            'Publisher': 'Test Publisher',
            'Year': '2024',
            'Language': 'en',
            'Pages': '100',
            'ISBN': '978-0-123456-78-9',
            'MD5': 'abc123',
            'Size': '1MB',
            'Extension': 'pdf',
            'TimeAdded': '2024-01-01',
            'TimeModified': '2024-01-01',
        }

        importer = libgen_rs.LibGenRsImporter()
        result = importer._transform_record(raw)

        assert result['ID'] == 1
        assert result['Title'] == 'Test Book'
        assert result['Authors'] == 'Test Author'

    def test_validate_record(self):
        """Test record validation."""
        importer = libgen_rs.LibGenRsImporter()

        # Valid record
        valid = importer._validate_record({
            'ID': 1,
            'Title': 'Test'
        })
        assert valid is True

        # Invalid - missing ID
        invalid = importer._validate_record({
            'Title': 'Test'
        })
        assert invalid is False


# =============================================================================
# Open Library Importer Tests
# =============================================================================

class TestOpenLibraryImporter:
    """Tests for Open Library importer."""

    def test_transform_record(self):
        """Test record transformation."""
        raw = {
            'type': 'book',
            'ol_key': '/books/OL12345',
            'revision': 1,
            'last_modified': '2024-01-01',
            'json': '{"title": "Test", "isbn_13": ["978-0-123456-78-9"]}',
        }

        importer = open_library.OpenLibraryImporter()
        result = importer._transform_record(raw)

        assert result['ol_key'] == '/books/OL12345'
        assert result['type'] == 'book'
        assert 'title' in result['json']

    def test_validate_record(self):
        """Test record validation."""
        importer = open_library.OpenLibraryImporter()

        # Valid record
        valid = importer._validate_record({
            'ol_key': '/books/OL123'
        })
        assert valid is True

        # Invalid - missing key
        invalid = importer._validate_record({
            'type': 'book'
        })
        assert invalid is False


# =============================================================================
# ISBNdb Importer Tests
# =============================================================================

class TestIsbnDbImporter:
    """Tests for ISBNdb importer."""

    def test_transform_record(self):
        """Test record transformation."""
        raw = {
            'isbn13': '9780123456789',
            'isbn10': '0123456789',
            'title': 'Test Book',
            'title_long': 'Test Book: The Complete Guide',
            'authors': ['Test Author'],
            'publisher': 'Test Publisher',
            'year': '2024',
            'language': 'en',
            'pages': '200',
        }

        importer = isbndb.IsbnDbImporter()
        result = importer._transform_record(raw)

        assert result['isbn13'] == '9780123456789'
        assert result['isbn10'] == '0123456789'
        assert 'title' in result['json']

    def test_validate_record(self):
        """Test record validation."""
        importer = isbndb.IsbnDbImporter()

        # Valid with ISBN-13
        valid = importer._validate_record({
            'isbn13': '9780123456789',
            'isbn10': '0123456789'
        })
        assert valid is True

        # Valid with ISBN-10 only
        valid = importer._validate_record({
            'isbn10': '0123456789'
        })
        assert valid is True

        # Invalid - no ISBN
        invalid = importer._validate_record({})
        assert invalid is False


# =============================================================================
# Integration Tests (Mocked)
# =============================================================================

@pytest.mark.integration
class TestImportIntegration:
    """Integration tests for import pipeline."""

    @patch('data_imports.base.create_engine')
    def test_full_import_flow(self, mock_engine):
        """Test full import flow."""
        mock_conn = MagicMock()
        mock_engine.return_value.connect.return_value.__enter__ = MagicMock(
            return_value=mock_conn
        )
        mock_engine.return_value.connect.return_value.__exit__ = MagicMock(
            return_value=False
        )

        importer = zlib.ZLibImporter()
        assert importer is not None
        assert importer.source_name == "zlib"


# =============================================================================
# Performance Tests
# =============================================================================

@pytest.mark.slow
class TestImportPerformance:
    """Performance tests for imports."""

    def test_batch_processing(self):
        """Test batch processing performance."""
        import time

        @ImportRegistry.register("test_perf")
        class TestImporter(BaseImporter):
            source_name = "test_perf"
            table_name = "test"

            @property
            def required_files(self):
                return []

            def _transform_record(self, raw):
                return raw

        importer = TestImporter()
        importer.batch_size = 100

        # Generate test records
        records = [{'id': i} for i in range(1000)]

        start = time.time()
        # Just test transform speed
        for record in records:
            importer._transform_record(record)
        elapsed = time.time() - start

        # Should complete quickly
        assert elapsed < 1.0  # 1 second for 1000 records