"""FTS5 full-text search for email archive.

Manages FTS5 virtual table, triggers for automatic sync,
query preparation, and BM25-scored search with snippet extraction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session


# BM25 column weights: subject=10, body=1, from_addr=5, from_name=5
_BM25_WEIGHTS = "10.0, 1.0, 5.0, 5.0"

# Shared INSERT expression used by triggers and the one-shot backfill.
_FTS_INSERT_NEW = (
    "INSERT INTO emails_fts(email_id, subject, body_text, from_addr, from_name) "
    "VALUES (NEW.id, COALESCE(NEW.subject, ''), COALESCE(NEW.body_text, ''), "
    "COALESCE(NEW.from_addr, ''), COALESCE(NEW.from_name, ''))"
)
_FTS_BACKFILL_SELECT = (
    "INSERT INTO emails_fts(email_id, subject, body_text, from_addr, from_name) "
    "SELECT id, COALESCE(subject, ''), COALESCE(body_text, ''), "
    "COALESCE(from_addr, ''), COALESCE(from_name, '') FROM emails"
)

# FTS5 table and trigger DDL
_CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    email_id UNINDEXED,
    subject,
    body_text,
    from_addr,
    from_name,
    tokenize='porter unicode61'
)
"""

_CREATE_TRIGGER_INSERT = f"""
CREATE TRIGGER IF NOT EXISTS emails_fts_insert
AFTER INSERT ON emails
BEGIN
    {_FTS_INSERT_NEW};
END
"""

# Scoped to the four indexed columns: updating archived_at, imap_uid,
# thread_id, etc. must NOT rebuild the FTS entry. Column scope is matched
# by name, so this also protects against future column additions that are
# not part of the search surface.
_CREATE_TRIGGER_UPDATE = f"""
CREATE TRIGGER IF NOT EXISTS emails_fts_update
AFTER UPDATE OF subject, body_text, from_addr, from_name ON emails
BEGIN
    DELETE FROM emails_fts WHERE email_id = OLD.id;
    {_FTS_INSERT_NEW};
END
"""

_CREATE_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS emails_fts_delete
AFTER DELETE ON emails
BEGIN
    DELETE FROM emails_fts WHERE email_id = OLD.id;
END
"""


def setup_fts5(engine: Engine) -> bool:
    """Set up FTS5 virtual table and sync triggers.

    Idempotent — safe to call multiple times. On first run with existing
    data, populates the FTS index from existing emails.

    Args:
        engine: SQLAlchemy engine connected to the database.

    Returns:
        True if FTS5 was set up successfully, False if FTS5 is unavailable.
    """
    with engine.connect() as conn:
        # Check if FTS5 is available
        try:
            conn.execute(text("SELECT fts5()"))
        except Exception:
            # fts5() with no args raises an error, but that means FTS5 exists.
            # If the *extension* is missing, we get a different error about
            # "no such function". Distinguish by trying to create a temp table.
            try:
                conn.execute(
                    text("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(test_col)")
                )
                conn.execute(text("DROP TABLE IF EXISTS _fts5_check"))
            except Exception:
                return False

        # Create FTS table
        conn.execute(text(_CREATE_FTS_TABLE))

        # Drop the UPDATE trigger before (re)creating: older schemas had an
        # unscoped AFTER UPDATE trigger that rebuilt FTS on every column
        # change. IF NOT EXISTS would preserve that old definition, so we
        # explicitly drop-and-recreate to apply the column scope on existing
        # databases. INSERT/DELETE triggers are unchanged and stay idempotent.
        conn.execute(text("DROP TRIGGER IF EXISTS emails_fts_update"))

        conn.execute(text(_CREATE_TRIGGER_INSERT))
        conn.execute(text(_CREATE_TRIGGER_UPDATE))
        conn.execute(text(_CREATE_TRIGGER_DELETE))

        # Populate from existing emails if FTS table is empty
        fts_count = conn.execute(text("SELECT COUNT(*) FROM emails_fts")).scalar()
        email_count = conn.execute(text("SELECT COUNT(*) FROM emails")).scalar()

        if fts_count == 0 and email_count is not None and email_count > 0:
            conn.execute(text(_FTS_BACKFILL_SELECT))

        conn.commit()

    return True


def fts5_available(engine: Engine) -> bool:
    """Check if FTS5 is available and the emails_fts table exists.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        True if FTS5 search can be used.
    """
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT COUNT(*) FROM emails_fts"))
            return True
        except Exception:
            return False


def prepare_fts_query(query_text: str) -> str:
    """Prepare a user query string for FTS5.

    Adds prefix matching (word*), preserves quoted phrases and
    AND/OR/NOT operators.

    Args:
        query_text: Raw user query text.

    Returns:
        FTS5-compatible query string.
    """
    if not query_text or not query_text.strip():
        return ""

    query_text = query_text.strip()

    # If the query already contains FTS5 operators, pass through
    # (user knows what they're doing)
    fts_operators = {"AND", "OR", "NOT", "NEAR"}

    # Extract quoted phrases first
    parts = []
    remaining = query_text
    for match in re.finditer(r'"[^"]*"', query_text):
        parts.append(("quoted", match.group()))

    # Remove quoted parts from remaining
    remaining = re.sub(r'"[^"]*"', " __QUOTED__ ", remaining)

    tokens = remaining.split()
    result_tokens = []
    quoted_idx = 0

    for token in tokens:
        if token == "__QUOTED__":
            if quoted_idx < len(parts):
                result_tokens.append(parts[quoted_idx][1])
                quoted_idx += 1
        elif token.upper() in fts_operators:
            result_tokens.append(token.upper())
        elif token.startswith("-"):
            # Negation: -word -> NOT word
            word = token[1:]
            if word:
                result_tokens.append(f"NOT {word}")
        elif "*" in token:
            # Already has wildcard
            result_tokens.append(token)
        else:
            # Add prefix matching for regular words
            result_tokens.append(f"{token}*")

    return " ".join(result_tokens)


def fts5_search(
    session: Session,
    query_text: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Execute an FTS5 search with BM25 ranking and snippet extraction.

    Args:
        session: SQLAlchemy session.
        query_text: Prepared FTS5 query (output of prepare_fts_query).
        limit: Maximum results.
        offset: Number of results to skip.

    Returns:
        List of dicts with keys: email_id, rank, snippet_subject,
        snippet_body, snippet_from.
    """
    if not query_text:
        return []

    # BM25 returns negative scores (lower = better match).
    # snippet() extracts context around matches.
    sql = text(
        "SELECT "
        "  email_id, "
        f"  bm25(emails_fts, {_BM25_WEIGHTS}) AS rank, "
        "  snippet(emails_fts, 1, '<b>', '</b>', '...', 32) AS snippet_subject, "
        "  snippet(emails_fts, 2, '<b>', '</b>', '...', 64) AS snippet_body, "
        "  snippet(emails_fts, 3, '<b>', '</b>', '...', 16) AS snippet_from "
        "FROM emails_fts "
        "WHERE emails_fts MATCH :query "
        "ORDER BY rank "
        "LIMIT :limit OFFSET :offset"
    )

    try:
        rows = session.execute(
            sql, {"query": query_text, "limit": limit, "offset": offset}
        ).fetchall()
    except Exception:
        # Query syntax error or other FTS5 issue — return empty
        return []

    results = []
    for row in rows:
        results.append(
            {
                "email_id": row[0],
                "rank": row[1],
                "snippet_subject": row[2],
                "snippet_body": row[3],
                "snippet_from": row[4],
            }
        )

    return results


def rebuild_fts_index(engine: Engine) -> int:
    """Rebuild the FTS5 index from scratch.

    Drops and recreates all FTS data. Use after bulk operations
    or if the index becomes corrupted.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        Number of emails indexed.
    """
    with engine.connect() as conn:
        # Clear existing FTS data
        try:
            conn.execute(text("DELETE FROM emails_fts"))
        except Exception:
            # Table might not exist yet
            setup_fts5(engine)
            return rebuild_fts_index(engine)

        # Re-populate from emails table
        conn.execute(text(_FTS_BACKFILL_SELECT))

        count = conn.execute(text("SELECT COUNT(*) FROM emails_fts")).scalar() or 0
        conn.commit()

    return count


def fts_stats(engine: Engine) -> dict[str, Any]:
    """Get FTS5 index statistics.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        Dict with keys: available, indexed_count, email_count, in_sync.
    """
    available = fts5_available(engine)
    if not available:
        return {
            "available": False,
            "indexed_count": 0,
            "email_count": 0,
            "in_sync": False,
        }

    with engine.connect() as conn:
        indexed = conn.execute(text("SELECT COUNT(*) FROM emails_fts")).scalar() or 0
        total = conn.execute(text("SELECT COUNT(*) FROM emails")).scalar() or 0

    return {
        "available": True,
        "indexed_count": indexed,
        "email_count": total,
        "in_sync": indexed == total,
    }
