"""
Test configuration and fixtures for Anna's Archive.

Provides:
- Application fixtures
- Database fixtures with rollback support
- ElasticSearch fixtures
- Test data factories
"""

import pytest
import os
import json
from typing import Generator, Any, Dict
from unittest.mock import MagicMock, patch

from config import settings
from allthethings.app import create_app
from allthethings.extensions import db as _db


# =============================================================================
# Application Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def app():
    """
    Setup Flask test app (session scope - created once).

    Returns:
        Flask application instance
    """
    db_uri = f"{settings.SQLALCHEMY_DATABASE_URI}_test"
    params = {
        "DEBUG": False,
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": db_uri,
        "ELASTICSEARCH_HOST": "localhost:9200",
    }

    _app = create_app(settings_override=params)

    # Push app context
    ctx = _app.app_context()
    ctx.push()

    yield _app

    ctx.pop()


@pytest.fixture(scope="session")
def _db(app):
    """
    Setup database (session scope - created once per test session).

    Returns:
        SQLAlchemy database instance
    """
    # Create all tables
    _db.create_all()

    yield _db

    # Drop all tables after tests
    _db.drop_all()


@pytest.fixture(scope="function")
def db(_db):
    """
    Reset database for each test (function scope).

    Provides transaction rollback for fast tests.

    Returns:
        SQLAlchemy session
    """
    # Start nested transaction
    connection = _db.engine.connect()
    transaction = connection.begin()

    # Bind session to connection
    options = {"bind": connection, "binds": {}}
    session = _db.create_scoped_session(options=options)

    # Replace db.session
    _db.session = session

    yield _db

    # Rollback transaction
    transaction.rollback()
    connection.close()
    session.remove()


@pytest.fixture(scope="function")
def session(db):
    """
    Provide database session with automatic rollback.

    Each test gets a clean session that rolls back after test.

    Returns:
        SQLAlchemy session
    """
    # Begin nested transaction
    db.session.begin_nested()

    yield db.session

    # Rollback to savepoint
    db.session.rollback()


@pytest.fixture(scope="function")
def client(app):
    """
    Provide Flask test client.

    Returns:
        Flask test client
    """
    return app.test_client()


@pytest.fixture(scope="function")
def runner(app):
    """
    Provide Flask CLI test runner.

    Returns:
        Flask CLI test runner
    """
    return app.test_cli_runner()


# =============================================================================
# Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_es():
    """
    Mock ElasticSearch client.

    Returns:
        Mock ElasticSearch client
    """
    with patch('allthethings.extensions.es') as mock:
        # Setup common mock responses
        mock.search.return_value = {
            'hits': {'total': {'value': 0}, 'hits': []}
        }
        mock.index.return_value = {'_id': 'test', 'result': 'created'}
        mock.delete.return_value = {'result': 'deleted'}
        yield mock


@pytest.fixture
def mock_db():
    """
    Mock database operations.

    Returns:
        Mock database
    """
    with patch('allthethings.extensions.db') as mock:
        mock.session.query.return_value.all.return_value = []
        mock.session.query.return_value.filter.return_value.first.return_value = None
        yield mock


# =============================================================================
# Test Data Factories
# =============================================================================

class Factory:
    """Test data factory base class."""

    @staticmethod
    def create(cls, **kwargs):
        """Create a test object."""
        return cls(**kwargs)


class BookFactory(Factory):
    """Factory for book test data."""

    @staticmethod
    def create(
        title: str = "Test Book",
        author: str = "Test Author",
        year: str = "2024",
        language: str = "en",
        extension: str = "pdf",
        filesize: int = 1024000,
        md5: str = "d41d8cd98f00b204e9800998ecf8427e",
        **kwargs
    ) -> Dict:
        """Create test book data."""
        return {
            'title': title,
            'author': author,
            'year': year,
            'language': language,
            'extension': extension,
            'filesize': filesize,
            'md5': md5,
            **kwargs
        }


class SearchQueryFactory(Factory):
    """Factory for search query test data."""

    @staticmethod
    def create(
        query: str = "test",
        filters: Dict = None,
        page: int = 1,
        per_page: int = 20,
        **kwargs
    ) -> Dict:
        """Create test search query."""
        return {
            'query': query,
            'filters': filters or {},
            'page': page,
            'per_page': per_page,
            **kwargs
        }


# Register factories
factories = {
    'book': BookFactory,
    'search_query': SearchQueryFactory,
}


@pytest.fixture
def factory():
    """Provide test data factory."""
    return factories


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_books():
    """Provide sample book data for tests."""
    return [
        {
            'zlibrary_id': 1,
            'title': 'The Great Gatsby',
            'author': 'F. Scott Fitzgerald',
            'year': '1925',
            'language': 'en',
            'extension': 'pdf',
            'filesize': 1024000,
            'md5': 'abc123',
        },
        {
            'zlibrary_id': 2,
            'title': '1984',
            'author': 'George Orwell',
            'year': '1949',
            'language': 'en',
            'extension': 'epub',
            'filesize': 512000,
            'md5': 'def456',
        },
        {
            'zlibrary_id': 3,
            'title': 'To Kill a Mockingbird',
            'author': 'Harper Lee',
            'year': '1960',
            'language': 'en',
            'extension': 'pdf',
            'filesize': 768000,
            'md5': 'ghi789',
        },
    ]


@pytest.fixture
def sample_search_results():
    """Provide sample search results for tests."""
    return {
        'total': 3,
        'page': 1,
        'per_page': 20,
        'results': [
            {
                'id': 1,
                'title': 'The Great Gatsby',
                'author': 'F. Scott Fitzgerald',
                'year': '1925',
                'extension': 'pdf',
            },
            {
                'id': 2,
                'title': '1984',
                'author': 'George Orwell',
                'year': '1949',
                'extension': 'epub',
            },
        ]
    }


# =============================================================================
# Cleanup Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def cleanup_files():
    """Cleanup test files after each test."""
    yield
    # Cleanup any test files
    import shutil
    test_dirs = ['htmlcov', '.coverage']
    for d in test_dirs:
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
            except Exception:
                pass


# =============================================================================
# Environment Fixtures
# =============================================================================

@pytest.fixture
def test_env(monkeypatch):
    """Set test environment variables."""
    monkeypatch.setenv('FLASK_ENV', 'testing')
    monkeypatch.setenv('DATABASE_URL', 'mysql+pymysql://test:test@localhost/test')
    return os.environ