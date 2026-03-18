"""SQLAlchemy ORM models for mtk database.

Core models for email archival and relationship tracking:
- Email, Thread, Attachment - Core email data
- Tag - Organization and tagging
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


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
    to_addrs: Mapped[str | None] = mapped_column(Text)
    cc_addrs: Mapped[str | None] = mapped_column(Text)
    bcc_addrs: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    references: Mapped[str | None] = mapped_column(Text)

    # Content
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(String(500))

    # File reference (path relative to maildir)
    file_path: Mapped[str | None] = mapped_column(String(1000))

    # IMAP tracking
    imap_uid: Mapped[int | None] = mapped_column()
    imap_account: Mapped[str | None] = mapped_column(String(100))
    imap_folder: Mapped[str | None] = mapped_column(String(255))

    # Flexible JSON metadata (e.g. {"source": "gmail", "labels": ["inbox"]})
    metadata_json: Mapped[str | None] = mapped_column(Text)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    thread: Mapped[Thread | None] = relationship(back_populates="emails")
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=email_tags, back_populates="emails")

    __table_args__ = (Index("ix_emails_date_thread", "date", "thread_id"),)

    def __repr__(self) -> str:
        return f"<Email {self.message_id[:30]}... from={self.from_addr}>"


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

    # Metadata
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

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


class ImapSyncState(Base):
    """Tracks IMAP sync state per account/folder for incremental sync."""

    __tablename__ = "imap_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_name: Mapped[str] = mapped_column(String(100), index=True)
    folder: Mapped[str] = mapped_column(String(255))

    # IMAP state for incremental sync
    uid_validity: Mapped[int | None] = mapped_column()
    last_uid: Mapped[int] = mapped_column(default=0)
    highest_modseq: Mapped[int | None] = mapped_column()

    # Statistics
    message_count: Mapped[int] = mapped_column(default=0)
    last_sync: Mapped[datetime | None] = mapped_column()

    __table_args__ = (UniqueConstraint("account_name", "folder"),)

    def __repr__(self) -> str:
        return f"<ImapSyncState {self.account_name}/{self.folder} uid={self.last_uid}>"


