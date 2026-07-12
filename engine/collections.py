"""User collections and bookmarks (PostgreSQL, SQLite fallback).

A lightweight persistence layer for saving documents into named collections.
It uses its own SQLAlchemy engine (default ``ENGINE_DATABASE_URL``, falling back
to a local SQLite file) so it is fully decoupled from the legacy MariaDB
reflection used by the book-search app.

SQLAlchemy is imported lazily inside :func:`_models` so that importing this
module never fails in environments without the ORM installed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from engine.config import EngineConfig, get_config

_MODELS: Optional[SimpleNamespace] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _models() -> SimpleNamespace:
    """Build (once) and return the ORM models namespace."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS

    from sqlalchemy import (
        Boolean,
        Column,
        DateTime,
        ForeignKey,
        Integer,
        String,
        Text,
        UniqueConstraint,
    )
    from sqlalchemy.orm import declarative_base, relationship

    Base = declarative_base()

    class Collection(Base):
        __tablename__ = "engine_collections"

        id = Column(Integer, primary_key=True)
        owner = Column(String(255), nullable=False, index=True)
        name = Column(String(255), nullable=False)
        description = Column(Text, default="")
        is_public = Column(Boolean, default=False)
        created_at = Column(DateTime, default=_utcnow)
        updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

        bookmarks = relationship(
            "Bookmark",
            back_populates="collection",
            cascade="all, delete-orphan",
            lazy="selectin",
        )

        def to_dict(self, with_bookmarks: bool = False) -> Dict[str, Any]:
            data = {
                "id": self.id,
                "owner": self.owner,
                "name": self.name,
                "description": self.description or "",
                "is_public": bool(self.is_public),
                "created_at": self.created_at.isoformat()
                if self.created_at
                else None,
                "updated_at": self.updated_at.isoformat()
                if self.updated_at
                else None,
                "bookmark_count": len(self.bookmarks),
            }
            if with_bookmarks:
                data["bookmarks"] = [b.to_dict() for b in self.bookmarks]
            return data

    class Bookmark(Base):
        __tablename__ = "engine_bookmarks"
        __table_args__ = (
            UniqueConstraint(
                "collection_id", "document_id", name="uq_collection_document"
            ),
        )

        id = Column(Integer, primary_key=True)
        collection_id = Column(
            Integer,
            ForeignKey("engine_collections.id"),
            nullable=False,
            index=True,
        )
        document_id = Column(String(255), nullable=False, index=True)
        title = Column(String(1024), default="")
        url = Column(String(2048), default="")
        source = Column(String(64), default="")
        note = Column(Text, default="")
        created_at = Column(DateTime, default=_utcnow)

        collection = relationship("Collection", back_populates="bookmarks")

        def to_dict(self) -> Dict[str, Any]:
            return {
                "id": self.id,
                "collection_id": self.collection_id,
                "document_id": self.document_id,
                "title": self.title or "",
                "url": self.url or "",
                "source": self.source or "",
                "note": self.note or "",
                "created_at": self.created_at.isoformat()
                if self.created_at
                else None,
            }

    _MODELS = SimpleNamespace(
        Base=Base, Collection=Collection, Bookmark=Bookmark
    )
    return _MODELS


class CollectionStore:
    """CRUD service for collections and bookmarks."""

    def __init__(
        self,
        database_url: Optional[str] = None,
        config: Optional[EngineConfig] = None,
    ) -> None:
        self.config = config or get_config()
        self.database_url = database_url or self.config.database_url
        self._engine = None
        self._Session = None

    def _ensure(self) -> None:
        if self._engine is not None:
            return
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        connect_args = (
            {"check_same_thread": False}
            if self.database_url.startswith("sqlite")
            else {}
        )
        self._engine = create_engine(
            self.database_url, connect_args=connect_args, future=True
        )
        _models().Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine, future=True)

    def init_db(self) -> None:
        self._ensure()

    @contextmanager
    def session(self):
        self._ensure()
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #
    def create_collection(
        self,
        owner: str,
        name: str,
        description: str = "",
        is_public: bool = False,
    ) -> Dict[str, Any]:
        m = _models()
        with self.session() as s:
            coll = m.Collection(
                owner=owner,
                name=name,
                description=description,
                is_public=is_public,
            )
            s.add(coll)
            s.flush()
            return coll.to_dict()

    def list_collections(self, owner: str) -> List[Dict[str, Any]]:
        m = _models()
        with self.session() as s:
            rows = (
                s.query(m.Collection)
                .filter_by(owner=owner)
                .order_by(m.Collection.updated_at.desc())
                .all()
            )
            return [c.to_dict() for c in rows]

    def get_collection(
        self, collection_id: int, owner: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        m = _models()
        with self.session() as s:
            coll = s.get(m.Collection, collection_id)
            if coll is None:
                return None
            if owner is not None and coll.owner != owner and not coll.is_public:
                return None
            return coll.to_dict(with_bookmarks=True)

    def delete_collection(self, collection_id: int, owner: str) -> bool:
        m = _models()
        with self.session() as s:
            coll = s.get(m.Collection, collection_id)
            if coll is None or coll.owner != owner:
                return False
            s.delete(coll)
            return True

    # ------------------------------------------------------------------ #
    # Bookmarks
    # ------------------------------------------------------------------ #
    def add_bookmark(
        self,
        collection_id: int,
        document_id: str,
        *,
        owner: Optional[str] = None,
        title: str = "",
        url: str = "",
        source: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        m = _models()
        with self.session() as s:
            coll = s.get(m.Collection, collection_id)
            if coll is None or (owner is not None and coll.owner != owner):
                return None
            existing = (
                s.query(m.Bookmark)
                .filter_by(collection_id=collection_id, document_id=document_id)
                .one_or_none()
            )
            if existing is not None:
                existing.note = note or existing.note
                s.flush()
                return existing.to_dict()
            bm = m.Bookmark(
                collection_id=collection_id,
                document_id=document_id,
                title=title,
                url=url,
                source=source,
                note=note,
            )
            s.add(bm)
            s.flush()
            return bm.to_dict()

    def remove_bookmark(
        self, collection_id: int, document_id: str, owner: Optional[str] = None
    ) -> bool:
        m = _models()
        with self.session() as s:
            coll = s.get(m.Collection, collection_id)
            if coll is None or (owner is not None and coll.owner != owner):
                return False
            bm = (
                s.query(m.Bookmark)
                .filter_by(collection_id=collection_id, document_id=document_id)
                .one_or_none()
            )
            if bm is None:
                return False
            s.delete(bm)
            return True

    def list_bookmarks(self, collection_id: int) -> List[Dict[str, Any]]:
        m = _models()
        with self.session() as s:
            rows = (
                s.query(m.Bookmark)
                .filter_by(collection_id=collection_id)
                .order_by(m.Bookmark.created_at.desc())
                .all()
            )
            return [b.to_dict() for b in rows]


_default_store: Optional[CollectionStore] = None


def get_store() -> CollectionStore:
    """Return a process-wide shared collection store."""
    global _default_store
    if _default_store is None:
        _default_store = CollectionStore()
    return _default_store
