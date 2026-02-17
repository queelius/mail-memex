"""Database session management and initialization."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from mtk.core.models import Base

if TYPE_CHECKING:
    from sqlalchemy import Engine


class Database:
    """Manages SQLite database connections for mtk shadow database."""

    def __init__(self, db_path: Path | str) -> None:
        """Initialize database connection.

        Args:
            db_path: Path to the SQLite database file.
                     Use ":memory:" for in-memory database (testing).
        """
        self.db_path = Path(db_path) if db_path != ":memory:" else db_path
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        """Get or create the database engine."""
        if self._engine is None:
            if self.db_path == ":memory:":
                url = "sqlite:///:memory:"
            else:
                url = f"sqlite:///{self.db_path}"

            self._engine = create_engine(url, echo=False)

            # Enable foreign key constraints for SQLite
            @event.listens_for(self._engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        return self._engine

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Get or create the session factory."""
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=self.engine)
        return self._session_factory

    def create_tables(self) -> None:
        """Create all database tables."""
        Base.metadata.create_all(self.engine)
        # Set up FTS5 full-text search index and triggers
        from mtk.search.fts import setup_fts5

        setup_fts5(self.engine)

    def drop_tables(self) -> None:
        """Drop all database tables."""
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Context manager for database sessions.

        Automatically commits on success, rolls back on exception.

        Example:
            with db.session() as session:
                session.add(email)
                session.commit()
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        """Close the database connection."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None


# Global database instance (set during init)
_db: Database | None = None


def get_db() -> Database:
    """Get the global database instance."""
    if _db is None:
        raise RuntimeError("Database not initialized. Run 'mtk init' first.")
    return _db


def init_db(db_path: Path | str) -> Database:
    """Initialize the global database instance."""
    global _db
    _db = Database(db_path)
    _db.create_tables()
    return _db


def close_db() -> None:
    """Close the global database instance."""
    global _db
    if _db is not None:
        _db.close()
        _db = None
