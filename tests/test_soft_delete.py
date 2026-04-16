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


def test_search_excludes_archived_emails(session) -> None:
    """SearchEngine should not return archived emails."""
    from mail_memex.search.engine import SearchEngine

    active = Email(
        message_id="active@example.com",
        from_addr="a@b.com",
        subject="Active email about projects",
        body_text="This email is active and searchable.",
        date=datetime(2024, 1, 1),
    )
    archived = Email(
        message_id="archived@example.com",
        from_addr="a@b.com",
        subject="Archived email about projects",
        body_text="This email is archived and hidden.",
        date=datetime(2024, 1, 2),
        archived_at=datetime.now(UTC),
    )
    session.add_all([active, archived])
    session.commit()

    engine = SearchEngine(session)
    results = engine.search("projects")
    message_ids = [r.email.message_id for r in results]

    assert "active@example.com" in message_ids
    assert "archived@example.com" not in message_ids
