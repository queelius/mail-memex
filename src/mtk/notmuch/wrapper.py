"""Wrapper around notmuch Python bindings.

This module provides a clean interface to notmuch functionality
while isolating the dependency to allow testing without notmuch.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Try to import notmuch2, but allow graceful fallback
try:
    import notmuch2

    NOTMUCH_AVAILABLE = True
except ImportError:
    NOTMUCH_AVAILABLE = False


class NotmuchError(Exception):
    """Error from notmuch operations."""

    pass


@dataclass
class NotmuchMessage:
    """A message as returned by notmuch."""

    message_id: str
    thread_id: str
    filename: Path
    date: datetime
    from_header: str
    to_header: str
    cc_header: str
    subject: str
    tags: list[str]
    in_reply_to: str | None
    references: list[str]

    @property
    def from_addr(self) -> str:
        """Extract email address from From header."""
        return _extract_email(self.from_header)

    @property
    def from_name(self) -> str | None:
        """Extract name from From header."""
        return _extract_name(self.from_header)

    @property
    def to_addrs(self) -> list[str]:
        """Extract email addresses from To header."""
        return _extract_emails(self.to_header)

    @property
    def cc_addrs(self) -> list[str]:
        """Extract email addresses from CC header."""
        return _extract_emails(self.cc_header)


@dataclass
class NotmuchThread:
    """A thread as returned by notmuch."""

    thread_id: str
    subject: str
    authors: list[str]
    tags: list[str]
    total_messages: int
    matched_messages: int
    newest_date: datetime
    oldest_date: datetime


class NotmuchWrapper:
    """Wrapper around notmuch database operations.

    This class provides a clean interface to notmuch and can be
    mocked for testing without requiring notmuch to be installed.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the notmuch wrapper.

        Args:
            db_path: Path to the notmuch database directory.
                    If None, uses the default location from notmuch config.
        """
        if not NOTMUCH_AVAILABLE:
            raise NotmuchError(
                "notmuch2 Python bindings not available. Install with: pip install notmuch2"
            )

        self.db_path = Path(db_path) if db_path else None
        self._db = None

    def _get_db(self, mode: str = "ro"):  # type: ignore[no-untyped-def]
        """Get a database connection.

        Args:
            mode: Database mode - "ro" for read-only, "rw" for read-write.
        """
        if self.db_path:
            return notmuch2.Database(path=self.db_path, mode=mode)
        return notmuch2.Database(mode=mode)

    def search_messages(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> Iterator[NotmuchMessage]:
        """Search for messages matching a query.

        Args:
            query: notmuch search query string.
            limit: Maximum number of messages to return.

        Yields:
            NotmuchMessage objects for each matching message.
        """
        with self._get_db() as db:
            messages = db.messages(query)
            for count, msg in enumerate(messages):
                if limit and count >= limit:
                    break

                yield self._convert_message(msg)

    def search_threads(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> Iterator[NotmuchThread]:
        """Search for threads matching a query.

        Args:
            query: notmuch search query string.
            limit: Maximum number of threads to return.

        Yields:
            NotmuchThread objects for each matching thread.
        """
        with self._get_db() as db:
            threads = db.threads(query)
            for count, thread in enumerate(threads):
                if limit and count >= limit:
                    break

                yield self._convert_thread(thread)

    def get_message(self, message_id: str) -> NotmuchMessage | None:
        """Get a specific message by Message-ID.

        Args:
            message_id: The Message-ID header value.

        Returns:
            NotmuchMessage or None if not found.
        """
        # notmuch expects the Message-ID without angle brackets
        clean_id = message_id.strip("<>")
        query = f"id:{clean_id}"

        with self._get_db() as db:
            messages = list(db.messages(query))
            if not messages:
                return None
            return self._convert_message(messages[0])

    def get_thread(self, thread_id: str) -> NotmuchThread | None:
        """Get a specific thread by thread ID.

        Args:
            thread_id: The notmuch thread ID.

        Returns:
            NotmuchThread or None if not found.
        """
        query = f"thread:{thread_id}"

        with self._get_db() as db:
            threads = list(db.threads(query))
            if not threads:
                return None
            return self._convert_thread(threads[0])

    def get_thread_messages(self, thread_id: str) -> list[NotmuchMessage]:
        """Get all messages in a thread.

        Args:
            thread_id: The notmuch thread ID.

        Returns:
            List of NotmuchMessage objects, ordered by date.
        """
        query = f"thread:{thread_id}"
        messages = list(self.search_messages(query))
        messages.sort(key=lambda m: m.date)
        return messages

    def get_all_tags(self) -> list[str]:
        """Get all tags in the database.

        Returns:
            List of tag names.
        """
        with self._get_db() as db:
            return list(db.tags)

    def add_tag(self, message_id: str, tag: str) -> None:
        """Add a tag to a message.

        Args:
            message_id: The Message-ID header value.
            tag: The tag to add.
        """
        clean_id = message_id.strip("<>")
        query = f"id:{clean_id}"

        with self._get_db(mode="rw") as db:
            for msg in db.messages(query):
                msg.tags.add(tag)

    def remove_tag(self, message_id: str, tag: str) -> None:
        """Remove a tag from a message.

        Args:
            message_id: The Message-ID header value.
            tag: The tag to remove.
        """
        clean_id = message_id.strip("<>")
        query = f"id:{clean_id}"

        with self._get_db(mode="rw") as db:
            for msg in db.messages(query):
                msg.tags.discard(tag)

    def count_messages(self, query: str = "*") -> int:
        """Count messages matching a query.

        Args:
            query: notmuch search query string.

        Returns:
            Number of matching messages.
        """
        with self._get_db() as db:
            return int(db.count_messages(query))

    def _convert_message(self, msg) -> NotmuchMessage:  # type: ignore[no-untyped-def]
        """Convert a notmuch message to our dataclass."""
        # Get the first filename (messages can have multiple)
        filenames = list(msg.filenames())
        filename = Path(filenames[0]) if filenames else Path("")

        # Parse references header
        refs_header = msg.header("References") or ""
        references = [r.strip() for r in refs_header.split() if r.strip()]

        return NotmuchMessage(
            message_id=msg.messageid,
            thread_id=msg.threadid,
            filename=filename,
            date=datetime.fromtimestamp(msg.date),
            from_header=msg.header("From") or "",
            to_header=msg.header("To") or "",
            cc_header=msg.header("Cc") or "",
            subject=msg.header("Subject") or "",
            tags=list(msg.tags),
            in_reply_to=msg.header("In-Reply-To"),
            references=references,
        )

    def _convert_thread(self, thread) -> NotmuchThread:  # type: ignore[no-untyped-def]
        """Convert a notmuch thread to our dataclass."""
        return NotmuchThread(
            thread_id=thread.threadid,
            subject=thread.subject or "",
            authors=list(thread.authors),
            tags=list(thread.tags),
            total_messages=thread.total,
            matched_messages=thread.matched,
            newest_date=datetime.fromtimestamp(thread.newest),
            oldest_date=datetime.fromtimestamp(thread.oldest),
        )


def _extract_email(header: str) -> str:
    """Extract email address from a header like 'Name <email@example.com>'."""
    import re

    if not header:
        return ""

    # Try to extract from angle brackets
    match = re.search(r"<([^>]+)>", header)
    if match:
        return match.group(1).lower()

    # Otherwise assume the whole thing is an email
    return header.strip().lower()


def _extract_name(header: str) -> str | None:
    """Extract name from a header like 'Name <email@example.com>'."""
    import re

    if not header:
        return None

    # Try to extract name before angle brackets
    match = re.match(r"^([^<]+)<", header)
    if match:
        name = match.group(1).strip().strip('"')
        return name if name else None

    return None


def _extract_emails(header: str) -> list[str]:
    """Extract multiple email addresses from a header."""
    if not header:
        return []

    # Split by comma and extract each
    parts = header.split(",")
    return [_extract_email(p) for p in parts if _extract_email(p)]
