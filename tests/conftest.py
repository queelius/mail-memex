"""Pytest fixtures and mocks for mtk tests.

This module provides:
- Database fixtures (in-memory and file-based)
- Sample email data fixtures
- Mock fixtures for external dependencies
- Factory functions for creating test data
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from mtk.core.database import Database
from mtk.core.models import (
    Attachment,
    Email,
    Tag,
    Thread,
)

# =============================================================================
# Directory and Path Fixtures
# =============================================================================


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """Create a temporary directory for test files."""
    with TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def config_dir(tmp_dir: Path) -> Path:
    """Create a mock config directory."""
    config = tmp_dir / ".config" / "mtk"
    config.mkdir(parents=True)
    return config


@pytest.fixture
def data_dir(tmp_dir: Path) -> Path:
    """Create a mock data directory."""
    data = tmp_dir / ".local" / "share" / "mtk"
    data.mkdir(parents=True)
    return data


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def db() -> Iterator[Database]:
    """Create an in-memory database for testing."""
    database = Database(":memory:")
    database.create_tables()
    yield database
    database.close()


@pytest.fixture
def db_file(tmp_dir: Path) -> Iterator[Database]:
    """Create a file-based database for testing."""
    db_path = tmp_dir / "test.db"
    database = Database(db_path)
    database.create_tables()
    yield database
    database.close()


@pytest.fixture
def session(db: Database) -> Iterator[Session]:
    """Get a database session from in-memory database.

    Note: Tests that intentionally cause IntegrityError or other
    errors should catch the exception - the fixture will handle rollback.
    """
    sess = db.session_factory()
    try:
        yield sess
    finally:
        # Always rollback any pending transaction before closing
        # This handles tests that intentionally cause IntegrityError
        with contextlib.suppress(Exception):
            sess.rollback()
        sess.close()


# =============================================================================
# Email Content Fixtures
# =============================================================================


@pytest.fixture
def sample_email_bytes() -> bytes:
    """Sample email in RFC 2822 format."""
    return b"""From: John Doe <john@example.com>
To: Jane Smith <jane@example.com>
Cc: Bob Wilson <bob@example.com>
Subject: Test Email
Date: Mon, 15 Jan 2024 10:30:00 -0500
Message-ID: <test123@example.com>
In-Reply-To: <previous@example.com>
References: <ref1@example.com> <ref2@example.com>

This is a test email body.

It has multiple lines and some content.

Best regards,
John
"""


@pytest.fixture
def sample_email_html_bytes() -> bytes:
    """Sample multipart email with HTML."""
    return b"""From: John Doe <john@example.com>
To: Jane Smith <jane@example.com>
Subject: HTML Email
Date: Mon, 15 Jan 2024 11:00:00 -0500
Message-ID: <html123@example.com>
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset="utf-8"

Plain text version of the email.

--boundary123
Content-Type: text/html; charset="utf-8"

<html>
<body>
<h1>HTML version</h1>
<p>This is the <strong>HTML</strong> body.</p>
</body>
</html>

--boundary123--
"""


@pytest.fixture
def sample_email_with_attachment() -> bytes:
    """Sample email with attachment."""
    return b"""From: sender@example.com
To: recipient@example.com
Subject: Email with attachment
Date: Mon, 15 Jan 2024 12:00:00 -0500
Message-ID: <attachment123@example.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="mixed_boundary"

--mixed_boundary
Content-Type: text/plain; charset="utf-8"

Please find the attachment.

--mixed_boundary
Content-Type: application/pdf; name="document.pdf"
Content-Disposition: attachment; filename="document.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQKJeLjz9MKMyAwIG9iago8PC9UeXBlIC9QYWdlCi9QYXJlbnQgMSAwIFI=

--mixed_boundary--
"""


# =============================================================================
# Mbox Fixtures
# =============================================================================


@pytest.fixture
def sample_mbox(tmp_dir: Path) -> Path:
    """Create a sample mbox file with multiple messages."""
    mbox_path = tmp_dir / "test.mbox"
    content = b"""From alice@example.com Mon Jan 15 10:00:00 2024
From: alice@example.com
To: bob@example.com
Subject: First email
Date: Mon, 15 Jan 2024 10:00:00 -0500
Message-ID: <mbox1@example.com>

First email body content.

This is a multi-line body.

From bob@example.com Mon Jan 15 11:00:00 2024
From: bob@example.com
To: alice@example.com
Subject: Second email
Date: Mon, 15 Jan 2024 11:00:00 -0500
Message-ID: <mbox2@example.com>
In-Reply-To: <mbox1@example.com>

Second email body - a reply.

From charlie@example.com Mon Jan 15 12:00:00 2024
From: charlie@example.com
To: alice@example.com, bob@example.com
Subject: Third email
Date: Mon, 15 Jan 2024 12:00:00 -0500
Message-ID: <mbox3@example.com>

Third email to multiple recipients.

"""
    mbox_path.write_bytes(content)
    return mbox_path


@pytest.fixture
def sample_gmail_mbox(tmp_dir: Path) -> Path:
    """Create a sample Gmail Takeout mbox file."""
    mbox_path = tmp_dir / "All mail Including Spam and Trash.mbox"
    content = b"""From test@gmail.com Mon Jan 15 10:00:00 2024
From: test@gmail.com
To: friend@example.com
Subject: Gmail message
Date: Mon, 15 Jan 2024 10:00:00 -0500
Message-ID: <gmail1@mail.gmail.com>
X-Gmail-Labels: Inbox,Important,Starred
X-Gmail-Thread-Id: thread123

Gmail message with labels.

"""
    mbox_path.write_bytes(content)
    return mbox_path


# =============================================================================
# EML Fixtures
# =============================================================================


@pytest.fixture
def sample_eml_dir(tmp_dir: Path) -> Path:
    """Create a directory with EML files."""
    eml_dir = tmp_dir / "emails"
    eml_dir.mkdir()

    for i in range(5):
        eml = eml_dir / f"email{i}.eml"
        content = f"""From: sender{i}@example.com
To: recipient{i}@example.com
Subject: Email number {i}
Date: Mon, {15 + i} Jan 2024 10:00:00 -0500
Message-ID: <eml{i}@example.com>

Body of email number {i}.
""".encode()
        eml.write_bytes(content)

    # Add nested directory
    nested = eml_dir / "subfolder"
    nested.mkdir()
    nested_eml = nested / "nested.eml"
    nested_eml.write_bytes(b"""From: nested@example.com
Message-ID: <nested@example.com>

Nested email.
""")

    return eml_dir


# =============================================================================
# Populated Database Fixtures
# =============================================================================


@pytest.fixture
def populated_db(db: Database) -> Database:
    """Database with comprehensive sample data."""
    with db.session() as session:
        # Create thread
        thread1 = Thread(
            thread_id="thread-001",
            subject="Project Discussion",
            email_count=3,
            first_date=datetime(2024, 1, 15, 10, 0),
            last_date=datetime(2024, 1, 15, 12, 0),
        )
        thread2 = Thread(
            thread_id="thread-002",
            subject="Weekend Plans",
            email_count=2,
        )
        session.add_all([thread1, thread2])
        session.flush()

        # Create emails
        emails_data = [
            {
                "message_id": "email1@example.com",
                "thread_id": "thread-001",
                "from_addr": "alice@example.com",
                "from_name": "Alice Smith",
                "subject": "Project Discussion",
                "date": datetime(2024, 1, 15, 10, 0, 0),
                "body_text": "Let's discuss the new project requirements.\n\nI think we should focus on the MVP first.",
                "body_preview": "Let's discuss the new project requirements. I think we should focus on the MVP first.",
                "to_addrs": "bob@example.com",
            },
            {
                "message_id": "email2@example.com",
                "thread_id": "thread-001",
                "from_addr": "bob@example.com",
                "from_name": "Bob Jones",
                "subject": "Re: Project Discussion",
                "date": datetime(2024, 1, 15, 11, 0, 0),
                "body_text": "I agree. The MVP should include core features only.",
                "body_preview": "I agree. The MVP should include core features only.",
                "in_reply_to": "email1@example.com",
            },
            {
                "message_id": "email3@example.com",
                "thread_id": "thread-001",
                "from_addr": "alice@example.com",
                "from_name": "Alice Smith",
                "subject": "Re: Project Discussion",
                "date": datetime(2024, 1, 15, 12, 0, 0),
                "body_text": "Great, let's schedule a meeting tomorrow.",
                "body_preview": "Great, let's schedule a meeting tomorrow.",
                "in_reply_to": "email2@example.com",
            },
            {
                "message_id": "email4@example.com",
                "thread_id": "thread-002",
                "from_addr": "charlie@example.com",
                "from_name": "Charlie Brown",
                "subject": "Weekend Plans",
                "date": datetime(2024, 1, 16, 9, 0, 0),
                "body_text": "Anyone free this weekend for a hike?",
                "body_preview": "Anyone free this weekend for a hike?",
            },
            {
                "message_id": "email5@example.com",
                "from_addr": "alice@example.com",
                "from_name": "Alice Smith",
                "subject": "URGENT: Server down",
                "date": datetime(2024, 1, 17, 8, 0, 0),
                "body_text": "The production server is down. Need immediate help!",
                "body_preview": "The production server is down. Need immediate help!",
            },
        ]

        for data in emails_data:
            email = Email(**data)
            session.add(email)

        # Create tags
        tags = [
            Tag(name="important", source="mtk"),
            Tag(name="work", source="mtk"),
            Tag(name="personal", source="mtk"),
            Tag(name="urgent", source="mtk"),
        ]
        session.add_all(tags)
        session.flush()

        # Associate tags with emails
        email1 = session.query(Email).filter_by(message_id="email1@example.com").first()
        email5 = session.query(Email).filter_by(message_id="email5@example.com").first()
        important_tag = session.query(Tag).filter_by(name="important").first()
        urgent_tag = session.query(Tag).filter_by(name="urgent").first()
        work_tag = session.query(Tag).filter_by(name="work").first()

        email1.tags.append(important_tag)
        email1.tags.append(work_tag)
        email5.tags.append(urgent_tag)
        email5.tags.append(important_tag)

        # Create attachment
        attachment = Attachment(
            email_id=email1.id,
            filename="requirements.pdf",
            content_type="application/pdf",
            size=102400,
        )
        session.add(attachment)

        session.commit()

    return db


# =============================================================================
# Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_config(tmp_dir: Path):
    """Mock MtkConfig to use temporary directories."""
    with patch("mtk.core.config.MtkConfig") as mock_config_cls:
        config = MagicMock()
        config.default_config_dir.return_value = tmp_dir / ".config" / "mtk"
        config.default_data_dir.return_value = tmp_dir / ".local" / "share" / "mtk"
        config.db_path = tmp_dir / ".local" / "share" / "mtk" / "mtk.db"
        mock_config_cls.load.return_value = config
        mock_config_cls.return_value = config
        yield config


# =============================================================================
# Factory Fixtures
# =============================================================================


class EmailFactory:
    """Factory for creating test Email objects."""

    def __init__(self, session: Session):
        self.session = session
        self._counter = 0

    def create(
        self,
        *,
        message_id: str | None = None,
        from_addr: str = "sender@example.com",
        from_name: str | None = "Sender",
        subject: str = "Test Subject",
        body_text: str = "Test body content",
        date: datetime | None = None,
        **kwargs,
    ) -> Email:
        """Create and save an Email."""
        self._counter += 1
        if message_id is None:
            message_id = f"generated{self._counter}@example.com"
        if date is None:
            date = datetime.now() - timedelta(hours=self._counter)

        email = Email(
            message_id=message_id,
            from_addr=from_addr,
            from_name=from_name,
            subject=subject,
            body_text=body_text,
            body_preview=body_text[:100] if body_text else None,
            date=date,
            **kwargs,
        )
        self.session.add(email)
        self.session.flush()
        return email


@pytest.fixture
def email_factory(session: Session) -> EmailFactory:
    """Get an EmailFactory instance."""
    return EmailFactory(session)
