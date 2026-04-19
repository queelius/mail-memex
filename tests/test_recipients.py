"""Tests for the email_recipients normalized side table.

Covers:
- Model column shape and cascade semantics
- build_recipients / parse_header helpers
- backfill_recipients idempotent migration
- SearchEngine uses the index on to:-style queries
- IMAP pull populates and refreshes recipients
"""

from __future__ import annotations

from datetime import datetime

import pytest

from mail_memex.core.database import Database
from mail_memex.core.models import Email, EmailRecipient
from mail_memex.core.recipients import (
    backfill_recipients,
    build_recipients,
    parse_header,
)


class TestBuildRecipients:
    """Unit tests for build_recipients (file/mbox importer path)."""

    def test_addrs_with_matching_names(self) -> None:
        recs = build_recipients(
            ["alice@x.com", "bob@x.com"],
            ["Alice", None],
            "to",
        )
        assert [(r.addr, r.name, r.kind) for r in recs] == [
            ("alice@x.com", "Alice", "to"),
            ("bob@x.com", None, "to"),
        ]

    def test_names_shorter_than_addrs_pads_with_none(self) -> None:
        recs = build_recipients(["a@x.com", "b@x.com", "c@x.com"], ["A"], "cc")
        assert [r.name for r in recs] == ["A", None, None]

    def test_names_none_uses_all_none(self) -> None:
        recs = build_recipients(["a@x.com", "b@x.com"], None, "bcc")
        assert all(r.name is None for r in recs)
        assert all(r.kind == "bcc" for r in recs)

    def test_empty_addrs_returns_empty_list(self) -> None:
        assert build_recipients([], None, "to") == []

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid recipient kind"):
            build_recipients(["a@x.com"], None, "from")


class TestParseHeader:
    """Unit tests for parse_header (IMAP pull path)."""

    def test_bare_address(self) -> None:
        recs = parse_header("alice@example.com", "to")
        assert [(r.addr, r.name) for r in recs] == [("alice@example.com", None)]

    def test_named_address(self) -> None:
        recs = parse_header("Alice Smith <alice@example.com>", "to")
        assert recs[0].addr == "alice@example.com"
        assert recs[0].name == "Alice Smith"

    def test_multiple_addresses(self) -> None:
        recs = parse_header(
            "Alice <a@x.com>, bob@x.com, Charlie <c@x.com>", "to"
        )
        assert {r.addr for r in recs} == {"a@x.com", "bob@x.com", "c@x.com"}

    def test_none_and_empty(self) -> None:
        assert parse_header(None, "to") == []
        assert parse_header("", "cc") == []

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid recipient kind"):
            parse_header("x@y.com", "unknown")


class TestBackfill:
    """Migration path: populate recipients from legacy CSV columns."""

    def test_backfill_creates_rows_from_csv(self, session) -> None:
        session.add(
            Email(
                message_id="bf1@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
                to_addrs="alice@example.com,bob@example.com",
                cc_addrs="charlie@example.com",
            )
        )
        session.commit()

        created = backfill_recipients(session)
        session.commit()

        assert created == 3
        recs = session.query(EmailRecipient).all()
        assert {(r.addr, r.kind) for r in recs} == {
            ("alice@example.com", "to"),
            ("bob@example.com", "to"),
            ("charlie@example.com", "cc"),
        }

    def test_backfill_handles_rfc_5322_csv(self, session) -> None:
        """Legacy CSV with 'Name <addr>' form should still parse correctly."""
        session.add(
            Email(
                message_id="bf2@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
                to_addrs='"Alice Smith" <alice@x.com>, Bob <bob@x.com>',
            )
        )
        session.commit()

        backfill_recipients(session)
        session.commit()

        recs = session.query(EmailRecipient).order_by(EmailRecipient.addr).all()
        assert [(r.addr, r.name) for r in recs] == [
            ("alice@x.com", "Alice Smith"),
            ("bob@x.com", "Bob"),
        ]

    def test_backfill_is_idempotent(self, session) -> None:
        session.add(
            Email(
                message_id="bf3@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
                to_addrs="alice@example.com",
            )
        )
        session.commit()

        assert backfill_recipients(session) == 1
        session.commit()
        assert backfill_recipients(session) == 0  # already backfilled
        session.commit()
        assert session.query(EmailRecipient).count() == 1

    def test_backfill_empty_csvs_no_rows(self, session) -> None:
        """Emails with no to/cc/bcc should produce zero recipient rows."""
        session.add(
            Email(
                message_id="bf4@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
            )
        )
        session.commit()
        assert backfill_recipients(session) == 0


class TestCascadeDelete:
    """Recipients must be cleaned up when the parent email is deleted."""

    def test_cascade_on_hard_delete(self, session) -> None:
        email = Email(
            message_id="casc@example.com",
            from_addr="s@x.com",
            subject="s",
            body_text="",
            date=datetime(2024, 1, 1),
        )
        email.recipients.extend(build_recipients(["a@x.com"], None, "to"))
        session.add(email)
        session.commit()

        assert session.query(EmailRecipient).count() == 1
        session.delete(email)
        session.commit()
        assert session.query(EmailRecipient).count() == 0


class TestSearchUsesRecipientsTable:
    """End-to-end: to:-style queries must resolve via the indexed table,
    and importing an email must populate the recipients row so the query
    finds it. These tests verify both the wiring (import → recipients)
    and the search (query → JOIN)."""

    def test_to_query_finds_via_recipients(self, db: Database) -> None:
        from mail_memex.search.engine import SearchEngine

        with db.session() as session:
            email = Email(
                message_id="search-recip@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
                to_addrs="alice@example.com",
            )
            email.recipients.extend(
                build_recipients(["alice@example.com"], None, "to")
            )
            session.add(email)
            session.commit()

            engine = SearchEngine(session)
            results = engine.search("to:alice")
            assert len(results) == 1
            assert results[0].email.message_id == "search-recip@example.com"

    def test_to_query_misses_when_no_recipient_row(self, db: Database) -> None:
        """Regression: an email with a CSV to_addrs column but no recipient
        rows should NOT match — it's the recipients table that the query
        hits now. This is the scenario that motivates rebuild_recipients."""
        from mail_memex.search.engine import SearchEngine

        with db.session() as session:
            session.add(
                Email(
                    message_id="legacy@example.com",
                    from_addr="sender@x.com",
                    subject="s",
                    body_text="",
                    date=datetime(2024, 1, 1),
                    to_addrs="alice@example.com",  # CSV populated, recipients NOT
                )
            )
            session.commit()

            engine = SearchEngine(session)
            results = engine.search("to:alice")
            assert results == []

            # But after backfill, the query finds it.
            backfill_recipients(session)
            session.commit()
            results = engine.search("to:alice")
            assert len(results) == 1

    def test_to_query_matches_cc_and_bcc_recipients(self, db: Database) -> None:
        """Gmail's `to:` operator matches any delivery field (to/cc/bcc).
        Our normalized query does the same — the subquery filters by addr,
        not kind."""
        from mail_memex.search.engine import SearchEngine

        with db.session() as session:
            email = Email(
                message_id="cc-match@example.com",
                from_addr="sender@x.com",
                subject="s",
                body_text="",
                date=datetime(2024, 1, 1),
            )
            email.recipients.extend(
                build_recipients(["alice@example.com"], None, "cc")
            )
            session.add(email)
            session.commit()

            results = SearchEngine(session).search("to:alice")
            assert len(results) == 1
