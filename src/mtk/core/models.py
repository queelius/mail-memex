"""SQLAlchemy ORM models for mtk database.

Core models for email archival, annotation, and relationship tracking:
- Email, Thread, Person, Attachment - Core email data
- Tag, Annotation, Collection - Organization and metadata
- PrivacyRule - Export controls
- Graph/relationship support for network analysis
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Index, Integer, Float, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


# Association table for email recipients (To/CC/BCC)
email_recipients = Table(
    "email_recipients",
    Base.metadata,
    Column("email_id", Integer, ForeignKey("emails.id"), primary_key=True),
    Column("person_id", Integer, ForeignKey("persons.id"), primary_key=True),
    Column("recipient_type", String(10)),  # "to", "cc", "bcc"
)

# Association table for email tags
email_tags = Table(
    "email_tags",
    Base.metadata,
    Column("email_id", Integer, ForeignKey("emails.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Email(Base):
    """An email message with its metadata and content."""

    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("threads.thread_id"), index=True
    )

    # Headers
    from_addr: Mapped[str] = mapped_column(String(255), index=True)
    from_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(1000))
    date: Mapped[datetime] = mapped_column(index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    references: Mapped[str | None] = mapped_column(Text)

    # Content
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(String(500))

    # File reference (path relative to maildir)
    file_path: Mapped[str | None] = mapped_column(String(1000))

    # mtk enhancements
    embedding: Mapped[bytes | None] = mapped_column()
    summary: Mapped[str | None] = mapped_column(Text)

    # Privacy
    export_allowed: Mapped[bool] = mapped_column(default=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    thread: Mapped[Thread | None] = relationship(back_populates="emails")
    sender: Mapped[Person | None] = relationship(
        foreign_keys="Email.sender_id", back_populates="sent_emails"
    )
    sender_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"))
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=email_tags, back_populates="emails")

    __table_args__ = (Index("ix_emails_date_thread", "date", "thread_id"),)

    def __repr__(self) -> str:
        return f"<Email {self.message_id[:30]}... from={self.from_addr}>"


class Person(Base):
    """A person with potentially multiple email addresses."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    primary_email: Mapped[str | None] = mapped_column(String(255), index=True)
    relationship_type: Mapped[str | None] = mapped_column(
        String(50)
    )  # "family", "friend", "colleague", etc.
    notes: Mapped[str | None] = mapped_column(Text)

    # Statistics (denormalized for performance)
    email_count: Mapped[int] = mapped_column(default=0)
    first_contact: Mapped[datetime | None] = mapped_column()
    last_contact: Mapped[datetime | None] = mapped_column()

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    email_addresses: Mapped[list[PersonEmail]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    sent_emails: Mapped[list[Email]] = relationship(
        foreign_keys="Email.sender_id", back_populates="sender"
    )

    def __repr__(self) -> str:
        return f"<Person {self.name} ({self.primary_email})>"


class PersonEmail(Base):
    """Maps email addresses to persons (one person can have multiple addresses)."""

    __tablename__ = "person_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    is_primary: Mapped[bool] = mapped_column(default=False)

    # Relationships
    person: Mapped[Person] = relationship(back_populates="email_addresses")

    def __repr__(self) -> str:
        return f"<PersonEmail {self.email} -> {self.person_id}>"


class Thread(Base):
    """An email thread/conversation."""

    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    subject: Mapped[str | None] = mapped_column(String(1000))

    # Statistics
    email_count: Mapped[int] = mapped_column(default=0)
    first_date: Mapped[datetime | None] = mapped_column()
    last_date: Mapped[datetime | None] = mapped_column()

    # mtk enhancements
    summary: Mapped[str | None] = mapped_column(Text)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    emails: Mapped[list[Email]] = relationship(back_populates="thread")

    def __repr__(self) -> str:
        return f"<Thread {self.thread_id[:20]}... ({self.email_count} emails)>"


class Tag(Base):
    """A tag that can be applied to emails (synced from notmuch)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="notmuch")  # "notmuch" or "mtk"

    # Relationships
    emails: Mapped[list[Email]] = relationship(secondary=email_tags, back_populates="tags")

    def __repr__(self) -> str:
        return f"<Tag {self.name}>"


class Attachment(Base):
    """An email attachment."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)
    filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int | None] = mapped_column()
    content_id: Mapped[str | None] = mapped_column(String(255))

    # We don't store attachment content, just metadata
    # Content can be retrieved from the original email file

    # Relationships
    email: Mapped[Email] = relationship(back_populates="attachments")

    def __repr__(self) -> str:
        return f"<Attachment {self.filename} ({self.content_type})>"


class PrivacyRule(Base):
    """Privacy rule for filtering/redacting emails during export."""

    __tablename__ = "privacy_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(20))  # "exclude", "redact"
    target_type: Mapped[str] = mapped_column(String(20))  # "address", "tag", "pattern"
    pattern: Mapped[str] = mapped_column(String(500))
    replacement: Mapped[str | None] = mapped_column(String(200))  # For redaction rules
    enabled: Mapped[bool] = mapped_column(default=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("rule_type", "target_type", "pattern"),)

    def __repr__(self) -> str:
        return f"<PrivacyRule {self.rule_type}:{self.target_type}={self.pattern}>"


# Association table for collection emails
collection_emails = Table(
    "collection_emails",
    Base.metadata,
    Column("collection_id", Integer, ForeignKey("collections.id"), primary_key=True),
    Column("email_id", Integer, ForeignKey("emails.id"), primary_key=True),
)


class Annotation(Base):
    """A user annotation/note on an email, thread, or person."""

    __tablename__ = "annotations"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Target (one of these will be set)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"), index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("persons.id"), index=True)

    # Annotation content
    annotation_type: Mapped[str] = mapped_column(
        String(50), default="note"
    )  # "note", "highlight", "link", "summary"
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text)  # JSON for flexible metadata

    # For highlights/selections
    selection_start: Mapped[int | None] = mapped_column()
    selection_end: Mapped[int | None] = mapped_column()

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        target = f"email={self.email_id}" if self.email_id else ""
        target = target or (f"thread={self.thread_id}" if self.thread_id else "")
        target = target or (f"person={self.person_id}" if self.person_id else "unknown")
        return f"<Annotation {self.annotation_type} on {target}>"


class Collection(Base):
    """A user-defined collection of emails (like a smart folder)."""

    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Collection type
    collection_type: Mapped[str] = mapped_column(
        String(50), default="manual"
    )  # "manual", "smart", "import"

    # For smart collections: the query that defines membership
    query: Mapped[str | None] = mapped_column(Text)

    # Metadata
    color: Mapped[str | None] = mapped_column(String(20))  # For UI display
    icon: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    emails: Mapped[list[Email]] = relationship(secondary=collection_emails)

    def __repr__(self) -> str:
        return f"<Collection {self.name} ({self.collection_type})>"


class CustomField(Base):
    """Custom metadata field for flexible key-value storage on emails."""

    __tablename__ = "custom_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), index=True)

    # Field definition
    field_name: Mapped[str] = mapped_column(String(100), index=True)
    field_type: Mapped[str] = mapped_column(
        String(20), default="text"
    )  # "text", "number", "date", "boolean", "json"
    field_value: Mapped[str] = mapped_column(Text)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("email_id", "field_name"),
        Index("ix_custom_fields_name_value", "field_name", "field_value"),
    )

    def __repr__(self) -> str:
        return f"<CustomField {self.field_name}={self.field_value[:30]}>"


class TopicCluster(Base):
    """A topic cluster discovered through analysis (e.g., LDA, embeddings)."""

    __tablename__ = "topic_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

    # Cluster metadata
    keywords: Mapped[str | None] = mapped_column(Text)  # JSON array of keywords
    embedding: Mapped[bytes | None] = mapped_column()  # Cluster centroid embedding

    # Statistics
    email_count: Mapped[int] = mapped_column(default=0)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<TopicCluster {self.name} ({self.email_count} emails)>"


# Association table for email-topic membership
email_topics = Table(
    "email_topics",
    Base.metadata,
    Column("email_id", Integer, ForeignKey("emails.id"), primary_key=True),
    Column("topic_id", Integer, ForeignKey("topic_clusters.id"), primary_key=True),
    Column("score", Float, default=1.0),  # Membership score/weight
)
