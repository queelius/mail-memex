"""TDD Tests for database module and ORM models.

These tests define the expected behavior of:
- Database class: connection management, sessions, transactions
- ORM models: Email, Thread, Tag, Attachment, Annotation, Collection, etc.
- Relationships: foreign keys, many-to-many, cascade behavior
- Constraints: unique, indexes, defaults
"""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mtk.core.database import Database, close_db, get_db, init_db
from mtk.core.models import (
    Attachment,
    Email,
    Tag,
    Thread,
)


class TestDatabaseLifecycle:
    """Tests for Database lifecycle operations."""

    def test_create_database_file(self, tmp_dir: Path) -> None:
        """Test creating a new database file."""
        db_path = tmp_dir / "test.db"
        db = Database(db_path)
        db.create_tables()

        assert db_path.exists()
        db.close()

    def test_memory_database(self) -> None:
        """Test creating an in-memory database."""
        db = Database(":memory:")
        db.create_tables()

        with db.session() as session:
            email = Email(
                message_id="test@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session.add(email)
            session.commit()

            result = session.execute(select(Email)).scalar()
            assert result.message_id == "test@example.com"

        db.close()

    def test_db_path_conversion(self, tmp_dir: Path) -> None:
        """Test that string path is converted to Path."""
        db_path_str = str(tmp_dir / "test.db")
        db = Database(db_path_str)

        # Memory database stays as string
        assert db.db_path == Path(db_path_str)
        db.close()

    def test_memory_db_path_stays_string(self) -> None:
        """Test that :memory: stays as string."""
        db = Database(":memory:")
        assert db.db_path == ":memory:"
        db.close()

    def test_engine_created_on_first_access(self) -> None:
        """Engine should be lazily created."""
        db = Database(":memory:")
        assert db._engine is None

        # Access engine property
        _ = db.engine
        assert db._engine is not None

        db.close()

    def test_close_disposes_engine(self) -> None:
        """Close should dispose engine and clear factory."""
        db = Database(":memory:")
        db.create_tables()

        _ = db.engine
        _ = db.session_factory

        db.close()
        assert db._engine is None
        assert db._session_factory is None

    def test_drop_tables(self, tmp_dir: Path) -> None:
        """Test dropping all tables."""
        db_path = tmp_dir / "test.db"
        db = Database(db_path)
        db.create_tables()

        with db.session() as session:
            email = Email(
                message_id="test@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session.add(email)
            session.commit()

        db.drop_tables()
        db.create_tables()

        # Table should be empty after drop/recreate
        with db.session() as session:
            result = session.execute(select(Email)).scalars().all()
            assert len(result) == 0

        db.close()


class TestDatabaseSession:
    """Tests for database session management."""

    def test_session_auto_commit(self, db: Database) -> None:
        """Session should auto-commit on success."""
        with db.session() as session:
            email = Email(
                message_id="test@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session.add(email)
            # No explicit commit

        # Should be persisted
        with db.session() as session:
            result = session.execute(select(Email)).scalar()
            assert result.message_id == "test@example.com"

    def test_session_rollback_on_error(self, db: Database) -> None:
        """Session should rollback on exception."""
        try:
            with db.session() as session:
                email = Email(
                    message_id="test@example.com",
                    from_addr="sender@example.com",
                    date=datetime.now(),
                )
                session.add(email)
                raise ValueError("Test error")
        except ValueError:
            pass

        # Email should not be persisted
        with db.session() as session:
            result = session.execute(select(Email)).scalars().all()
            assert len(result) == 0

    def test_multiple_sessions(self, db: Database) -> None:
        """Multiple sessions should work correctly."""
        with db.session() as session1:
            email1 = Email(
                message_id="email1@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session1.add(email1)

        with db.session() as session2:
            email2 = Email(
                message_id="email2@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session2.add(email2)

        with db.session() as session:
            result = session.execute(select(Email)).scalars().all()
            assert len(result) == 2


class TestGlobalDatabase:
    """Tests for global database functions."""

    def test_get_db_without_init_raises(self) -> None:
        """get_db should raise if not initialized."""
        close_db()  # Ensure clean state

        with pytest.raises(RuntimeError, match="Database not initialized"):
            get_db()

    def test_init_db_creates_tables(self, tmp_dir: Path) -> None:
        """init_db should create tables."""
        close_db()  # Ensure clean state

        db_path = tmp_dir / "global.db"
        db = init_db(db_path)

        assert db_path.exists()

        # Should be able to use the database
        with db.session() as session:
            email = Email(
                message_id="test@example.com",
                from_addr="sender@example.com",
                date=datetime.now(),
            )
            session.add(email)

        close_db()

    def test_get_db_after_init(self, tmp_dir: Path) -> None:
        """get_db should return database after init."""
        close_db()

        db_path = tmp_dir / "global.db"
        init_db(db_path)

        db = get_db()
        assert db is not None

        close_db()


class TestEmailModel:
    """Tests for Email ORM model."""

    def test_create_email(self, session) -> None:
        """Test creating an email record."""
        email = Email(
            message_id="test123@example.com",
            from_addr="john@example.com",
            from_name="John Doe",
            subject="Test Subject",
            date=datetime(2024, 1, 15, 10, 0, 0),
            body_text="Test body content",
        )
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert result.message_id == "test123@example.com"
        assert result.from_addr == "john@example.com"
        assert result.subject == "Test Subject"

    def test_message_id_unique_constraint(self, session) -> None:
        """Message ID should be unique."""
        email1 = Email(
            message_id="duplicate@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email1)
        session.commit()

        email2 = Email(
            message_id="duplicate@example.com",
            from_addr="other@example.com",
            date=datetime.now(),
        )
        session.add(email2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_email_defaults(self, session) -> None:
        """Test email default values."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert result.created_at is not None
        assert result.updated_at is not None

    def test_email_repr(self) -> None:
        """Test email string representation."""
        email = Email(
            message_id="test123456789@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        repr_str = repr(email)
        assert "test123456789" in repr_str
        assert "sender@example.com" in repr_str


class TestThreadModel:
    """Tests for Thread ORM model."""

    def test_email_thread_relationship(self, session) -> None:
        """Test email-thread relationship."""
        thread = Thread(thread_id="thread-001", subject="Test Thread")
        session.add(thread)
        session.flush()

        email1 = Email(
            message_id="email1@example.com",
            thread_id="thread-001",
            from_addr="alice@example.com",
            date=datetime(2024, 1, 15, 10, 0, 0),
        )
        email2 = Email(
            message_id="email2@example.com",
            thread_id="thread-001",
            from_addr="bob@example.com",
            date=datetime(2024, 1, 15, 11, 0, 0),
        )
        session.add_all([email1, email2])
        session.commit()

        result = session.get(Thread, thread.id)
        assert len(result.emails) == 2

    def test_thread_id_unique(self, session) -> None:
        """Thread ID should be unique."""
        thread1 = Thread(thread_id="unique-thread")
        session.add(thread1)
        session.commit()

        thread2 = Thread(thread_id="unique-thread")
        session.add(thread2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_thread_defaults(self, session) -> None:
        """Test thread default values."""
        thread = Thread(thread_id="test-thread")
        session.add(thread)
        session.commit()

        result = session.get(Thread, thread.id)
        assert result.email_count == 0


class TestTagModel:
    """Tests for Tag ORM model."""

    def test_email_tags(self, session) -> None:
        """Test email-tag many-to-many relationship."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        tag1 = Tag(name="important")
        tag2 = Tag(name="personal")
        session.add_all([email, tag1, tag2])
        session.flush()

        email.tags.append(tag1)
        email.tags.append(tag2)
        session.commit()

        result = session.get(Email, email.id)
        assert len(result.tags) == 2
        assert {t.name for t in result.tags} == {"important", "personal"}

    def test_tag_unique_name(self, session) -> None:
        """Tag name should be unique."""
        tag1 = Tag(name="unique")
        session.add(tag1)
        session.commit()

        tag2 = Tag(name="unique")
        session.add(tag2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_tag_default_source(self, session) -> None:
        """Tag should default to 'mtk' source."""
        tag = Tag(name="test")
        session.add(tag)
        session.commit()

        result = session.get(Tag, tag.id)
        assert result.source == "mtk"

    def test_tag_reverse_relationship(self, session) -> None:
        """Tag should have reverse relationship to emails."""
        email1 = Email(
            message_id="email1@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        email2 = Email(
            message_id="email2@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        tag = Tag(name="shared-tag")
        session.add_all([email1, email2, tag])
        session.flush()

        email1.tags.append(tag)
        email2.tags.append(tag)
        session.commit()

        result = session.get(Tag, tag.id)
        assert len(result.emails) == 2


class TestAttachmentModel:
    """Tests for Attachment ORM model."""

    def test_email_attachments(self, session) -> None:
        """Test email-attachment relationship."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        att = Attachment(
            filename="document.pdf",
            content_type="application/pdf",
            size=1024,
        )
        email.attachments.append(att)
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "document.pdf"

    def test_attachment_cascade_delete(self, session) -> None:
        """Attachments should be deleted when email is deleted."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        att = Attachment(filename="test.pdf", content_type="application/pdf")
        email.attachments.append(att)
        session.add(email)
        session.commit()

        email_id = email.id

        session.delete(email)
        session.commit()

        # Attachment should also be gone
        result = (
            session.execute(select(Attachment).where(Attachment.email_id == email_id))
            .scalars()
            .all()
        )
        assert len(result) == 0


class TestMetadataJson:
    """Tests for Email.metadata_json field."""

    def test_metadata_json(self, session) -> None:
        """Test storing and retrieving JSON metadata on an email."""
        import json

        meta = {"source": "gmail", "labels": ["inbox"]}
        email = Email(
            message_id="meta@test.com",
            from_addr="a@b.com",
            date=datetime(2024, 1, 1),
            metadata_json=json.dumps(meta),
        )
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert json.loads(result.metadata_json)["source"] == "gmail"
        assert json.loads(result.metadata_json)["labels"] == ["inbox"]

    def test_metadata_json_defaults_none(self, session) -> None:
        """metadata_json should be None when not set."""
        email = Email(
            message_id="nometa@test.com",
            from_addr="a@b.com",
            date=datetime(2024, 1, 1),
        )
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert result.metadata_json is None
