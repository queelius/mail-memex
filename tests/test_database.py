"""TDD Tests for database module and ORM models.

These tests define the expected behavior of:
- Database class: connection management, sessions, transactions
- ORM models: Email, Person, Thread, Tag, Attachment, Annotation, Collection, etc.
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
    Annotation,
    Attachment,
    Collection,
    CustomField,
    Email,
    Person,
    PersonEmail,
    PrivacyRule,
    Tag,
    Thread,
    TopicCluster,
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
        assert result.export_allowed is True
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


class TestPersonModel:
    """Tests for Person ORM model."""

    def test_create_person(self, session) -> None:
        """Test creating a person."""
        person = Person(
            name="Jane Smith",
            primary_email="jane@example.com",
            relationship_type="colleague",
        )
        session.add(person)
        session.commit()

        result = session.get(Person, person.id)
        assert result.name == "Jane Smith"
        assert result.primary_email == "jane@example.com"
        assert result.relationship_type == "colleague"

    def test_person_with_multiple_emails(self, session) -> None:
        """Test person with multiple email addresses."""
        person = Person(
            name="Jane Smith",
            primary_email="jane@example.com",
        )
        session.add(person)
        session.flush()

        pe1 = PersonEmail(email="jane@example.com", person_id=person.id, is_primary=True)
        pe2 = PersonEmail(email="jane.smith@work.com", person_id=person.id, is_primary=False)
        session.add_all([pe1, pe2])
        session.commit()

        result = session.get(Person, person.id)
        assert len(result.email_addresses) == 2

    def test_person_email_unique(self, session) -> None:
        """PersonEmail should have unique email constraint."""
        person = Person(name="Test", primary_email="test@example.com")
        session.add(person)
        session.flush()

        pe1 = PersonEmail(email="shared@example.com", person_id=person.id)
        session.add(pe1)
        session.commit()

        pe2 = PersonEmail(email="shared@example.com", person_id=person.id)
        session.add(pe2)

        with pytest.raises(IntegrityError):
            session.commit()

    def test_person_defaults(self, session) -> None:
        """Test person default values."""
        person = Person(name="Test", primary_email="test@example.com")
        session.add(person)
        session.commit()

        result = session.get(Person, person.id)
        assert result.email_count == 0
        assert result.created_at is not None


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
        """Tag should default to 'notmuch' source."""
        tag = Tag(name="test")
        session.add(tag)
        session.commit()

        result = session.get(Tag, tag.id)
        assert result.source == "notmuch"

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


class TestAnnotationModel:
    """Tests for Annotation ORM model."""

    def test_create_annotation(self, session) -> None:
        """Test creating annotations."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.flush()

        annotation = Annotation(
            email_id=email.id,
            annotation_type="note",
            content="Important context here",
        )
        session.add(annotation)
        session.commit()

        result = session.execute(select(Annotation).where(Annotation.email_id == email.id)).scalar()
        assert result.content == "Important context here"
        assert result.annotation_type == "note"

    def test_annotation_types(self, session) -> None:
        """Test different annotation types."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.flush()

        for ann_type in ["note", "highlight", "link", "summary"]:
            ann = Annotation(
                email_id=email.id,
                annotation_type=ann_type,
                content=f"Content for {ann_type}",
            )
            session.add(ann)

        session.commit()

        result = (
            session.execute(select(Annotation).where(Annotation.email_id == email.id))
            .scalars()
            .all()
        )
        assert len(result) == 4


class TestCollectionModel:
    """Tests for Collection ORM model."""

    def test_create_collection(self, session) -> None:
        """Test creating collections."""
        collection = Collection(
            name="Project X",
            description="Emails related to Project X",
            collection_type="manual",
        )
        session.add(collection)
        session.commit()

        result = session.get(Collection, collection.id)
        assert result.name == "Project X"
        assert result.collection_type == "manual"

    def test_collection_emails(self, session) -> None:
        """Test collection-email relationship."""
        collection = Collection(name="Test Collection")
        session.add(collection)
        session.flush()

        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.flush()

        collection.emails.append(email)
        session.commit()

        result = session.get(Collection, collection.id)
        assert len(result.emails) == 1

    def test_collection_unique_name(self, session) -> None:
        """Collection name should be unique."""
        c1 = Collection(name="unique-collection")
        session.add(c1)
        session.commit()

        c2 = Collection(name="unique-collection")
        session.add(c2)

        with pytest.raises(IntegrityError):
            session.commit()


class TestCustomFieldModel:
    """Tests for CustomField ORM model."""

    def test_create_custom_field(self, session) -> None:
        """Test custom fields."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.flush()

        field = CustomField(
            email_id=email.id,
            field_name="project",
            field_type="text",
            field_value="Alpha",
        )
        session.add(field)
        session.commit()

        result = session.execute(
            select(CustomField).where(CustomField.email_id == email.id)
        ).scalar()
        assert result.field_name == "project"
        assert result.field_value == "Alpha"

    def test_custom_field_unique_per_email(self, session) -> None:
        """Same field name cannot be used twice per email."""
        email = Email(
            message_id="test@example.com",
            from_addr="sender@example.com",
            date=datetime.now(),
        )
        session.add(email)
        session.flush()

        f1 = CustomField(email_id=email.id, field_name="priority", field_value="high")
        session.add(f1)
        session.commit()

        f2 = CustomField(email_id=email.id, field_name="priority", field_value="low")
        session.add(f2)

        with pytest.raises(IntegrityError):
            session.commit()


class TestPrivacyRuleModel:
    """Tests for PrivacyRule ORM model."""

    def test_create_privacy_rule(self, session) -> None:
        """Test creating privacy rules."""
        rule = PrivacyRule(
            rule_type="exclude",
            target_type="address",
            pattern="secret@company.com",
            enabled=True,
        )
        session.add(rule)
        session.commit()

        result = session.get(PrivacyRule, rule.id)
        assert result.rule_type == "exclude"
        assert result.target_type == "address"
        assert result.pattern == "secret@company.com"

    def test_redact_rule(self, session) -> None:
        """Test redact rule with replacement."""
        rule = PrivacyRule(
            rule_type="redact",
            target_type="pattern",
            pattern=r"\d{3}-\d{2}-\d{4}",
            replacement="[REDACTED SSN]",
        )
        session.add(rule)
        session.commit()

        result = session.get(PrivacyRule, rule.id)
        assert result.replacement == "[REDACTED SSN]"

    def test_privacy_rule_unique_constraint(self, session) -> None:
        """Same rule type/target/pattern should be unique."""
        r1 = PrivacyRule(
            rule_type="exclude",
            target_type="address",
            pattern="test@example.com",
        )
        session.add(r1)
        session.commit()

        r2 = PrivacyRule(
            rule_type="exclude",
            target_type="address",
            pattern="test@example.com",
        )
        session.add(r2)

        with pytest.raises(IntegrityError):
            session.commit()


class TestTopicClusterModel:
    """Tests for TopicCluster ORM model."""

    def test_create_topic_cluster(self, session) -> None:
        """Test creating topic clusters."""
        cluster = TopicCluster(
            name="Machine Learning",
            description="Emails about ML topics",
            keywords='["ml", "deep learning", "neural networks"]',
            email_count=25,
        )
        session.add(cluster)
        session.commit()

        result = session.get(TopicCluster, cluster.id)
        assert result.name == "Machine Learning"
        assert result.email_count == 25
        assert "ml" in result.keywords
