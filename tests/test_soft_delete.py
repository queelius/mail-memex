"""TDD Tests for soft delete (archived_at) on Email and Thread models.

The memex ecosystem convention: every record table carries an
archived_at TIMESTAMP NULL column. Default queries should filter
WHERE archived_at IS NULL. This module verifies the column exists,
defaults to None, and can be set to a datetime.
"""

from datetime import UTC, datetime

from mail_memex.core.models import Email, Thread


class TestEmailSoftDelete:
    """Tests for Email.archived_at column."""

    def test_email_archived_at_default_none(self, session) -> None:
        """New email should have archived_at=None by default."""
        email = Email(
            message_id="soft-del-1@example.com",
            from_addr="sender@example.com",
            date=datetime(2024, 1, 15, 10, 0, 0),
        )
        session.add(email)
        session.commit()

        result = session.get(Email, email.id)
        assert result.archived_at is None

    def test_email_soft_delete(self, session) -> None:
        """Setting archived_at to a datetime should persist."""
        email = Email(
            message_id="soft-del-2@example.com",
            from_addr="sender@example.com",
            date=datetime(2024, 1, 15, 10, 0, 0),
        )
        session.add(email)
        session.commit()

        now = datetime.now(UTC)
        email.archived_at = now
        session.commit()

        result = session.get(Email, email.id)
        assert result.archived_at is not None


class TestThreadSoftDelete:
    """Tests for Thread.archived_at column."""

    def test_thread_archived_at_default_none(self, session) -> None:
        """New thread should have archived_at=None by default."""
        thread = Thread(thread_id="soft-del-thread-1")
        session.add(thread)
        session.commit()

        result = session.get(Thread, thread.id)
        assert result.archived_at is None
