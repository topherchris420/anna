"""PostgreSQL document store (schema + bulk upsert), pgvector-backed.

Owns the ``engineering_docs`` table: a ``tsvector`` column for full-text search
and a ``vector(dims)`` column (pgvector) for dense-vector kNN. Everything is
plain SQL over psycopg2 — no ORM — because ``tsvector`` and ``vector`` are not
standard SQLAlchemy types.

Connections are opened per operation and closed afterwards, which keeps the
store robust against dropped/idle connections on free-tier databases.
"""

from __future__ import annotations

import datetime
import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine.config import EngineConfig, get_config
from engine.documents import Document

# Column order used by both the INSERT and the row tuples (embedding last).
_COLUMNS = [
    "id", "source", "kind", "title", "abstract", "body", "url", "pdf_url",
    "authors", "published", "published_date", "updated", "version",
    "categories", "tags", "language", "identifiers", "has_equations",
    "has_code", "popularity", "equations", "extra", "embedding",
]
# Columns read back when reconstructing a Document (no embedding / tsvector).
_SELECT_COLUMNS = [c for c in _COLUMNS if c not in ("embedding", "published_date")]


def psycopg2_dsn(url: str) -> str:
    """Convert a SQLAlchemy-style URL into a libpq DSN psycopg2 accepts."""
    return (
        url.replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres://", "postgresql://")
    )


def parse_date(value: Any) -> Optional[datetime.date]:
    """Best-effort parse of a document date (ISO, ``YYYY-MM`` or ``YYYY``)."""
    if not value:
        return None
    match = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(value).strip())
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return datetime.date(year, 1, 1)


def vector_literal(vec: Optional[List[float]]) -> Optional[str]:
    """Format a vector as the pgvector text literal ``[a,b,c]`` (or None)."""
    if not vec:
        return None
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def document_row(doc: Document) -> Tuple[Any, ...]:
    """Build the INSERT tuple for a document (matches ``_COLUMNS``)."""
    from psycopg2.extras import Json

    return (
        doc.id,
        doc.source,
        doc.kind,
        doc.title,
        doc.abstract,
        doc.body,
        doc.url,
        doc.pdf_url,
        list(doc.authors or []),
        doc.published,
        parse_date(doc.published),
        doc.updated,
        doc.version,
        list(doc.categories or []),
        list(doc.tags or []),
        doc.language,
        Json(doc.identifiers or {}),
        bool(doc.has_equations),
        bool(doc.has_code),
        float(doc.popularity or 0.0),
        list(doc.equations or []),
        Json(doc.extra or {}),
        vector_literal(doc.embedding),
    )


def row_to_document(row: Dict[str, Any]) -> Document:
    """Reconstruct a :class:`Document` from a ``RealDictCursor`` row."""
    data = dict(row)
    data["id"] = data.get("id", "")
    indexed = data.get("indexed_at")
    if isinstance(indexed, (datetime.datetime, datetime.date)):
        data["indexed_at"] = indexed.isoformat()
    # psycopg2 returns TEXT[] as lists and jsonb as dicts already.
    return Document.from_source(data)


class PgStore:
    """Manages the ``engineering_docs`` table and bulk upserts."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or get_config()
        self.table = self.config.index_name
        self.dims = self.config.embedding_dims
        self._lock = threading.Lock()
        # Whether the pgvector extension / ``embedding`` column is available.
        # ``None`` until first probed; cached for the process lifetime.
        self._vector_enabled: Optional[bool] = None

    # ------------------------------------------------------------------ #
    @contextmanager
    def connect(self):
        import psycopg2  # lazy import

        conn = psycopg2.connect(psycopg2_dsn(self.config.database_url))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    def create_index(self, recreate: bool = False) -> None:
        """Ensure the pgvector extension, table, and indexes exist.

        pgvector is optional: on hosts where the ``vector`` extension cannot be
        created (e.g. some managed/free Postgres plans that don't ship it), the
        table is built **without** the ``embedding`` column and the engine
        degrades to full-text-only retrieval. This keeps ``index-init`` — and
        therefore container boot — from crash-looping on those plans.
        """
        with self.connect() as conn, conn.cursor() as cur:
            vector_ok = self._ensure_vector_extension(cur)
            if recreate:
                cur.execute(f"DROP TABLE IF EXISTS {self.table};")
            cur.execute(self._create_table_sql(vector_ok))
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_fts "
                f"ON {self.table} USING GIN (search_vector);"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_source "
                f"ON {self.table} (source);"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_kind "
                f"ON {self.table} (kind);"
            )
        self._vector_enabled = vector_ok

    def reset_index(self) -> None:
        self.create_index(recreate=True)

    @staticmethod
    def _ensure_vector_extension(cur) -> bool:
        """Try to enable pgvector inside a savepoint; report availability.

        Running ``CREATE EXTENSION`` in a savepoint means a failure (missing
        extension, insufficient privileges) can be rolled back without
        aborting the outer transaction that goes on to create the table.
        """
        cur.execute("SAVEPOINT vector_ext;")
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        except Exception:  # noqa: BLE001 — extension unavailable / no perms
            cur.execute("ROLLBACK TO SAVEPOINT vector_ext;")
            cur.execute("RELEASE SAVEPOINT vector_ext;")
            return False
        cur.execute("RELEASE SAVEPOINT vector_ext;")
        return True

    def _create_table_sql(self, vector_ok: bool) -> str:
        # Only emit the pgvector column when the extension is available.
        embedding_col = (
            f"embedding     vector({self.dims})," if vector_ok else ""
        )
        return f"""
        CREATE TABLE IF NOT EXISTS {self.table} (
            id            TEXT PRIMARY KEY,
            source        TEXT,
            kind          TEXT,
            title         TEXT,
            abstract      TEXT,
            body          TEXT,
            url           TEXT,
            pdf_url       TEXT,
            authors       TEXT[]  DEFAULT '{{}}',
            published     TEXT,
            published_date DATE,
            updated       TEXT,
            version       TEXT,
            categories    TEXT[]  DEFAULT '{{}}',
            tags          TEXT[]  DEFAULT '{{}}',
            language      TEXT,
            identifiers   JSONB   DEFAULT '{{}}'::jsonb,
            has_equations BOOLEAN DEFAULT FALSE,
            has_code      BOOLEAN DEFAULT FALSE,
            popularity    REAL    DEFAULT 0,
            equations     TEXT[]  DEFAULT '{{}}',
            extra         JSONB   DEFAULT '{{}}'::jsonb,
            indexed_at    TIMESTAMPTZ DEFAULT now(),
            {embedding_col}
            search_vector tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(title, '')),   'A') ||
                setweight(to_tsvector('english', coalesce(abstract, '')),'B') ||
                setweight(to_tsvector('english', coalesce(body, '')),    'C') ||
                setweight(to_tsvector('english',
                    coalesce(array_to_string(authors, ' '), '')),        'B')
            ) STORED
        );
        """

    # ------------------------------------------------------------------ #
    def has_vector(self) -> bool:
        """Whether the table has a pgvector ``embedding`` column (cached)."""
        if self._vector_enabled is None:
            self._vector_enabled = self._detect_vector()
        return self._vector_enabled

    def _detect_vector(self) -> bool:
        """Ground-truth probe: does the table actually have ``embedding``?"""
        try:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = %s AND column_name = 'embedding'",
                    (self.table,),
                )
                return cur.fetchone() is not None
        except Exception:  # noqa: BLE001 — DB unreachable; assume no vector
            return False

    # ------------------------------------------------------------------ #
    def bulk_index(
        self, documents: Iterable[Document], batch_size: int = 100
    ) -> Tuple[int, List[Any]]:
        """Upsert documents. Returns ``(success_count, errors)``."""
        from psycopg2.extras import execute_values

        rows = [document_row(d) for d in documents]
        if not rows:
            return 0, []

        # When pgvector is unavailable the table has no ``embedding`` column, so
        # drop it (it is the last element of both _COLUMNS and each row tuple).
        vector_ok = self.has_vector()
        if vector_ok:
            columns = _COLUMNS
            # Only the embedding needs an explicit cast to vector.
            template = (
                "(" + ",".join(["%s"] * (len(columns) - 1) + ["%s::vector"]) + ")"
            )
        else:
            columns = _COLUMNS[:-1]
            rows = [row[:-1] for row in rows]
            template = None

        cols = ", ".join(columns)
        updates = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in columns if c != "id"
        )
        sql = (
            f"INSERT INTO {self.table} ({cols}) VALUES %s "
            f"ON CONFLICT (id) DO UPDATE SET {updates}, indexed_at=now()"
        )

        errors: List[Any] = []
        success = 0
        with self.connect() as conn, conn.cursor() as cur:
            for start in range(0, len(rows), batch_size):
                chunk = rows[start : start + batch_size]
                try:
                    execute_values(cur, sql, chunk, template=template, page_size=batch_size)
                    success += len(chunk)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    errors.append(str(exc))
        return success, errors

    # ------------------------------------------------------------------ #
    def index_exists(self) -> bool:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (self.table,))
            return cur.fetchone()[0] is not None

    def count(self) -> int:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (self.table,))
            if cur.fetchone()[0] is None:
                return 0
            cur.execute(f"SELECT count(*) FROM {self.table}")
            return int(cur.fetchone()[0])

    def get_document(self, doc_id: str) -> Optional[Document]:
        from psycopg2.extras import RealDictCursor

        cols = ", ".join(_SELECT_COLUMNS)
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT {cols} FROM {self.table} WHERE id = %s", (doc_id,))
            row = cur.fetchone()
        return row_to_document(row) if row else None


_stores: Dict[str, PgStore] = {}
_stores_lock = threading.Lock()


def get_store(config: Optional[EngineConfig] = None) -> PgStore:
    config = config or get_config()
    key = config.database_url + "::" + config.index_name
    if key not in _stores:
        with _stores_lock:
            if key not in _stores:
                _stores[key] = PgStore(config)
    return _stores[key]
