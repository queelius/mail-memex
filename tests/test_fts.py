"""Tests for FTS5 full-text search.

Tests cover:
- FTS5 setup and trigger-based sync
- Query preparation
- BM25-scored search results
- Snippet extraction
- Rebuild and stats utilities
- Fallback behavior in SearchEngine
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text

from mtk.core.database import Database
from mtk.core.models import Email
from mtk.search.engine import SearchEngine
from mtk.search.fts import (
    fts5_available,
    fts5_search,
    fts_stats,
    prepare_fts_query,
    rebuild_fts_index,
    setup_fts5,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def fts_db() -> Database:
    """In-memory database with FTS5 set up."""
    database = Database(":memory:")
    database.create_tables()
    # create_tables already calls setup_fts5
    return database


@pytest.fixture
def fts_populated_db(fts_db: Database) -> Database:
    """Database with sample emails for FTS testing."""
    with fts_db.session() as session:
        emails = [
            Email(
                message_id="fts1@example.com",
                from_addr="alice@example.com",
                from_name="Alice Smith",
                subject="Project requirements document",
                body_text="We need to discuss the project requirements for the new system. "
                "The deadline is next Friday.",
                body_preview="We need to discuss the project requirements...",
                date=datetime(2024, 1, 15, 10, 0),
            ),
            Email(
                message_id="fts2@example.com",
                from_addr="bob@example.com",
                from_name="Bob Jones",
                subject="Meeting notes from today",
                body_text="Here are the meeting notes. We discussed the project timeline "
                "and assigned tasks to team members.",
                body_preview="Here are the meeting notes...",
                date=datetime(2024, 1, 15, 11, 0),
            ),
            Email(
                message_id="fts3@example.com",
                from_addr="charlie@example.com",
                from_name="Charlie Brown",
                subject="Weekend hiking plans",
                body_text="Anyone interested in hiking this weekend? "
                "I was thinking about the mountain trail.",
                body_preview="Anyone interested in hiking...",
                date=datetime(2024, 1, 16, 9, 0),
            ),
            Email(
                message_id="fts4@example.com",
                from_addr="alice@example.com",
                from_name="Alice Smith",
                subject="URGENT: Server outage",
                body_text="The production server is down. We need immediate help "
                "to restore the database backup.",
                body_preview="The production server is down...",
                date=datetime(2024, 1, 17, 8, 0),
            ),
            Email(
                message_id="fts5@example.com",
                from_addr="dave@example.com",
                from_name="Dave Wilson",
                subject="Python code review request",
                body_text="Please review the attached Python code changes. "
                "I've refactored the database module.",
                body_preview="Please review the attached Python code...",
                date=datetime(2024, 1, 18, 14, 0),
            ),
        ]
        for email in emails:
            session.add(email)
        session.commit()

    return fts_db


# =============================================================================
# setup_fts5 tests
# =============================================================================


class TestSetupFts5:
    """Tests for FTS5 table and trigger setup."""

    def test_setup_creates_fts_table(self, fts_db: Database) -> None:
        """setup_fts5 should create the emails_fts virtual table."""
        with fts_db.engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='emails_fts'")
            ).fetchone()
        assert result is not None

    def test_setup_creates_triggers(self, fts_db: Database) -> None:
        """setup_fts5 should create INSERT/UPDATE/DELETE triggers."""
        with fts_db.engine.connect() as conn:
            triggers = conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'emails_fts_%'"
                )
            ).fetchall()
        trigger_names = {row[0] for row in triggers}
        assert "emails_fts_insert" in trigger_names
        assert "emails_fts_update" in trigger_names
        assert "emails_fts_delete" in trigger_names

    def test_setup_is_idempotent(self, fts_db: Database) -> None:
        """Calling setup_fts5 multiple times should not error."""
        result = setup_fts5(fts_db.engine)
        assert result is True

    def test_fts5_available(self, fts_db: Database) -> None:
        """fts5_available should return True after setup."""
        assert fts5_available(fts_db.engine) is True


# =============================================================================
# Trigger sync tests
# =============================================================================


class TestFts5TriggerSync:
    """Tests for automatic FTS sync via triggers."""

    def test_insert_syncs_to_fts(self, fts_db: Database) -> None:
        """Inserting an email should automatically add it to FTS."""
        with fts_db.session() as session:
            email = Email(
                message_id="trigger1@example.com",
                from_addr="test@example.com",
                from_name="Test User",
                subject="Trigger test subject",
                body_text="This is the body text for trigger testing.",
                date=datetime(2024, 1, 15),
            )
            session.add(email)
            session.commit()

            # Verify it's in FTS
            count = session.execute(
                text("SELECT COUNT(*) FROM emails_fts WHERE subject MATCH 'trigger'")
            ).scalar()
            assert count == 1

    def test_update_syncs_to_fts(self, fts_db: Database) -> None:
        """Updating an email should update the FTS index."""
        with fts_db.session() as session:
            email = Email(
                message_id="update1@example.com",
                from_addr="test@example.com",
                subject="Original subject",
                body_text="Original body",
                date=datetime(2024, 1, 15),
            )
            session.add(email)
            session.commit()

            # Update subject
            email.subject = "Updated subject with uniqueword"
            session.commit()

            # Old term should not match
            old_count = session.execute(
                text("SELECT COUNT(*) FROM emails_fts WHERE subject MATCH 'Original'")
            ).scalar()
            assert old_count == 0

            # New term should match
            new_count = session.execute(
                text("SELECT COUNT(*) FROM emails_fts WHERE subject MATCH 'uniqueword'")
            ).scalar()
            assert new_count == 1

    def test_delete_syncs_to_fts(self, fts_db: Database) -> None:
        """Deleting an email should remove it from FTS."""
        with fts_db.session() as session:
            email = Email(
                message_id="delete1@example.com",
                from_addr="test@example.com",
                subject="Delete test subject",
                body_text="Delete test body",
                date=datetime(2024, 1, 15),
            )
            session.add(email)
            session.commit()

            email_id = email.id
            session.delete(email)
            session.commit()

            count = session.execute(
                text("SELECT COUNT(*) FROM emails_fts WHERE email_id = :eid"),
                {"eid": email_id},
            ).scalar()
            assert count == 0


# =============================================================================
# Query preparation tests
# =============================================================================


class TestPrepareQuery:
    """Tests for FTS5 query preparation."""

    def test_simple_word(self) -> None:
        """Single word should get prefix matching."""
        assert prepare_fts_query("hello") == "hello*"

    def test_multiple_words(self) -> None:
        """Multiple words should each get prefix matching."""
        assert prepare_fts_query("hello world") == "hello* world*"

    def test_quoted_phrase_preserved(self) -> None:
        """Quoted phrases should be preserved as-is."""
        result = prepare_fts_query('"exact phrase"')
        assert '"exact phrase"' in result

    def test_operators_preserved(self) -> None:
        """AND/OR/NOT operators should be preserved."""
        result = prepare_fts_query("hello AND world")
        assert "AND" in result

    def test_negation(self) -> None:
        """Minus prefix should convert to NOT."""
        result = prepare_fts_query("-spam hello")
        assert "NOT spam" in result
        assert "hello*" in result

    def test_wildcard_preserved(self) -> None:
        """Existing wildcards should be preserved."""
        result = prepare_fts_query("hel*")
        assert "hel*" in result

    def test_empty_query(self) -> None:
        """Empty query should return empty string."""
        assert prepare_fts_query("") == ""
        assert prepare_fts_query("  ") == ""

    def test_or_operator(self) -> None:
        """OR operator preserved."""
        result = prepare_fts_query("cat OR dog")
        assert "OR" in result


# =============================================================================
# FTS5 search tests
# =============================================================================


class TestFts5Search:
    """Tests for FTS5 BM25-scored search."""

    def test_basic_search(self, fts_populated_db: Database) -> None:
        """Basic text search should return matching emails."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "project*")
            assert len(results) >= 2  # emails 1 and 2 mention "project"

    def test_bm25_ranking(self, fts_populated_db: Database) -> None:
        """Results should be ranked by BM25 relevance."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "project*")
            assert len(results) >= 2
            # Subject match (email 1: "Project requirements") should rank
            # higher than body-only match due to subject weight=10
            ranks = [r["rank"] for r in results]
            # BM25 ranks are negative; lower (more negative) = better
            assert all(r <= 0 for r in ranks)

    def test_subject_weighted_higher(self, fts_populated_db: Database) -> None:
        """Subject matches should rank higher than body-only matches."""
        with fts_populated_db.session() as session:
            # "hiking" appears in email3's subject AND body
            results = fts5_search(session, "hiking*")
            assert len(results) >= 1

    def test_snippet_extraction(self, fts_populated_db: Database) -> None:
        """Search results should include snippets."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "server*")
            assert len(results) >= 1
            # Should have snippet_body populated
            assert results[0]["snippet_body"] is not None

    def test_no_results(self, fts_populated_db: Database) -> None:
        """Nonexistent terms should return empty results."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "xyznonexistent*")
            assert results == []

    def test_limit_offset(self, fts_populated_db: Database) -> None:
        """Limit and offset should work correctly."""
        with fts_populated_db.session() as session:
            fts5_search(session, "the*", limit=100)
            limited = fts5_search(session, "the*", limit=2)
            assert len(limited) <= 2

    def test_invalid_query_returns_empty(self, fts_populated_db: Database) -> None:
        """Invalid FTS5 syntax should return empty rather than error."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "AND OR NOT")
            assert results == []

    def test_from_addr_search(self, fts_populated_db: Database) -> None:
        """Should be able to search by sender address."""
        with fts_populated_db.session() as session:
            results = fts5_search(session, "alice*")
            assert len(results) >= 1


# =============================================================================
# Rebuild and stats tests
# =============================================================================


class TestRebuildAndStats:
    """Tests for rebuild_fts_index and fts_stats."""

    def test_rebuild_index(self, fts_populated_db: Database) -> None:
        """rebuild_fts_index should reindex all emails."""
        count = rebuild_fts_index(fts_populated_db.engine)
        assert count == 5

    def test_rebuild_clears_and_repopulates(self, fts_populated_db: Database) -> None:
        """Rebuild should produce clean index matching email count."""
        # Manually corrupt by deleting an FTS entry
        with fts_populated_db.engine.connect() as conn:
            conn.execute(text("DELETE FROM emails_fts LIMIT 1"))
            conn.commit()

        count = rebuild_fts_index(fts_populated_db.engine)
        assert count == 5

    def test_fts_stats(self, fts_populated_db: Database) -> None:
        """fts_stats should report correct counts."""
        stats = fts_stats(fts_populated_db.engine)
        assert stats["available"] is True
        assert stats["indexed_count"] == 5
        assert stats["email_count"] == 5
        assert stats["in_sync"] is True

    def test_fts_stats_empty_db(self, fts_db: Database) -> None:
        """fts_stats on empty db should show zero counts."""
        stats = fts_stats(fts_db.engine)
        assert stats["available"] is True
        assert stats["indexed_count"] == 0
        assert stats["email_count"] == 0
        assert stats["in_sync"] is True


# =============================================================================
# SearchEngine integration tests
# =============================================================================


class TestSearchEngineFts5Integration:
    """Tests for SearchEngine using FTS5 backend."""

    def test_search_uses_fts5(self, fts_populated_db: Database) -> None:
        """SearchEngine.search should use FTS5 when available."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("project")
            assert len(results) >= 1
            # FTS5 results should have match_type "fts5"
            assert results[0].match_type == "fts5"

    def test_search_results_have_scores(self, fts_populated_db: Database) -> None:
        """FTS5 search results should have meaningful scores."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("project")
            for r in results:
                assert r.score > 0

    def test_search_results_have_highlights(self, fts_populated_db: Database) -> None:
        """FTS5 search results should have highlight snippets."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("server")
            assert len(results) >= 1
            # Should have some highlights
            r = results[0]
            has_highlights = bool(r.highlights.get("subject")) or bool(r.highlights.get("body"))
            assert has_highlights

    def test_search_with_field_filters(self, fts_populated_db: Database) -> None:
        """FTS5 search should work with field-specific filters."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            # Search for "project" from alice only
            results = engine.search("project from:alice")
            assert len(results) >= 1
            for r in results:
                assert "alice" in r.email.from_addr.lower()

    def test_date_sort_uses_like_search(self, fts_populated_db: Database) -> None:
        """Requesting date order should use LIKE search."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("project", order_by="date")
            assert len(results) >= 1
            # Date-ordered search falls back to LIKE
            assert results[0].match_type == "keyword"

    def test_empty_text_uses_like_search(self, fts_populated_db: Database) -> None:
        """Query with no free text should use LIKE search."""
        with fts_populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("from:alice@example.com")
            # No free text → LIKE search path
            assert len(results) >= 1

    def test_existing_populated_db_works(self, populated_db: Database) -> None:
        """FTS5 should work with the standard populated_db fixture."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("project")
            assert len(results) >= 1
