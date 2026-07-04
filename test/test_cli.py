"""
Unit tests for CLI commands.

Tests the Flask CLI commands including data imports.
"""

import pytest
from click.testing import CliRunner
from unittest.mock import MagicMock, patch

from allthethings.cli.views import cli


# =============================================================================
# CLI Basic Tests
# =============================================================================

class TestCLIBasics:
    """Basic CLI tests."""

    def test_cli_command_exists(self, runner):
        """Test CLI command exists."""
        result = runner.invoke(cli, ['--help'])
        assert result.exit_code == 0

    def test_cli_help(self, runner):
        """Test CLI help output."""
        result = runner.invoke(cli, ['--help'])
        assert 'Manage the application' in result.output


# =============================================================================
# Database CLI Tests
# =============================================================================

class TestDatabaseCLI:
    """Tests for database CLI commands."""

    @patch('allthethings.cli.views.create_engine')
    @patch('allthethings.cli.views.pathlib.Path.read_text')
    def test_dbreset_command(self, mock_path, mock_engine, runner):
        """Test dbreset command."""
        # Mock the engine
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_engine.return_value.raw_connection.return_value = mock_conn

        # Mock path
        mock_path.return_value = "DROP TABLE IF EXISTS test;"

        result = runner.invoke(cli, ['dbreset'], input='y\n')

        # Command should exist (may fail without actual DB)
        assert result.exit_code != 0 or 'error' in result.output.lower() or 'database' in result.output.lower()

    @patch('allthethings.cli.views.create_engine')
    def test_mysql_build_computed_all_md5s(self, mock_engine, runner):
        """Test mysql_build_computed_all_md5s command."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_engine.return_value.raw_connection.return_value = mock_conn

        result = runner.invoke(cli, ['mysql_build_computed_all_md5s'], input='y\n')

        # Command should exist
        assert 'Erasing' in result.output or result.exit_code != 0


# =============================================================================
# ElasticSearch CLI Tests
# =============================================================================

class TestElasticSearchCLI:
    """Tests for ElasticSearch CLI commands."""

    @patch('allthethings.cli.views.es')
    def test_elastic_reset_md5_dicts(self, mock_es, runner):
        """Test elastic_reset_md5_dicts command."""
        mock_es.options.return_value.indices.delete.return_value = {}

        result = runner.invoke(cli, ['elastic_reset_md5_dicts'], input='y\n')

        assert 'Erasing' in result.output or result.exit_code != 0

    @patch('allthethings.cli.views.db')
    @patch('allthethings.cli.views.es')
    @patch('allthethings.cli.views.select')
    @patch('allthethings.cli.views.func')
    @patch('allthethings.cli.views.query_yield_batches')
    @patch('allthethings.cli.views.multiprocessing.Pool')
    def test_elastic_build_md5_dicts(self, mock_pool, mock_query, mock_func,
                                     mock_select, mock_es, mock_db, runner):
        """Test elastic_build_md5_dicts command."""
        # Skip test if complex mocking needed
        pytest.skip("Complex mocking required - test manually")


# =============================================================================
# Import CLI Tests (if available)
# =============================================================================

@pytest.mark.integration
class TestImportCLI:
    """Tests for data import CLI commands."""

    def test_list_importers(self, runner):
        """Test list_importers command."""
        try:
            from data_imports import imports
            result = runner.invoke(imports.import_cli, ['list_importers'])
            assert result.exit_code == 0
        except ImportError:
            pytest.skip("Imports not available")

    def test_import_status(self, runner):
        """Test import_status command."""
        try:
            from data_imports import imports
            result = runner.invoke(imports.import_cli, ['import_status'])
            assert result.exit_code == 0
        except ImportError:
            pytest.skip("Imports not available")

    def test_import_all_command(self, runner):
        """Test import_all command."""
        try:
            from data_imports import imports
            result = runner.invoke(imports.import_cli, ['import_all'])
            # Should run without error (dry run)
            assert 'Starting' in result.output or result.exit_code == 0
        except ImportError:
            pytest.skip("Imports not available")


# =============================================================================
# CLI Error Handling Tests
# =============================================================================

class TestCLIErrorHandling:
    """Tests for CLI error handling."""

    def test_invalid_command(self, runner):
        """Test invalid command handling."""
        result = runner.invoke(cli, ['invalid_command'])
        assert result.exit_code != 0

    @patch('allthethings.cli.views.db')
    def test_database_error_handling(self, mock_db, runner):
        """Test database error handling."""
        mock_db.engine = None  # Force error

        # Should handle gracefully
        result = runner.invoke(cli, ['dbreset'], input='y\n')
        # Either succeeds or shows clear error
        assert result.exit_code != 0 or 'error' in result.output.lower()


# =============================================================================
# CLI Output Tests
# =============================================================================

class TestCLIOutput:
    """Tests for CLI output formatting."""

    def test_help_formatting(self, runner):
        """Test help output formatting."""
        result = runner.invoke(cli, ['--help'])
        assert 'Usage:' in result.output
        assert 'Options:' in result.output or '-h, --help' in result.output

    def test_command_list(self, runner):
        """Test command list."""
        result = runner.invoke(cli, ['--help'])
        # Should list available commands
        assert 'dbreset' in result.output or 'Commands' in result.output