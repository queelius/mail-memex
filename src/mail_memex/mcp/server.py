"""MCP server for mail-memex using FastMCP.

Contract tools: execute_sql, get_schema, get_record.
Domain tools: search_emails, marginalia CRUD (7 tools).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import text

from mail_memex.core.config import MailMemexConfig
from mail_memex.core.database import Database

# ---------------------------------------------------------------------------
# Table descriptions (human-readable, for LLM context)
# ---------------------------------------------------------------------------

TABLE_DESCRIPTIONS: dict[str, str] = {
    "emails": (
        "Email messages with headers, content, and metadata. "
        "to_addrs, cc_addrs, bcc_addrs are comma-separated address strings. "
        "metadata_json stores flexible JSON extras (e.g. Gmail labels) queryable via json_extract(metadata_json, '$.key'). "
        "archived_at is NULL for live records; set to a timestamp for soft-deleted records."
    ),
    "threads": (
        "Email threads/conversations grouping related emails by thread_id. "
        "archived_at is NULL for live records; set to a timestamp for soft-deleted records."
    ),
    "tags": "Tags applied to emails. source='mail-memex' for locally created tags, 'imap' for IMAP-synced tags.",
    "email_tags": "Association table linking emails to tags (many-to-many join via email_id and tag_id).",
    "attachments": "Email attachment metadata (filename, content_type, size). Content is not stored — retrieve from original file.",
    "imap_sync_state": "IMAP sync state per account/folder for incremental sync (last_uid, uid_validity, highest_modseq).",
    "emails_fts": (
        "FTS5 full-text search index on emails (subject, body_text, from_addr, from_name). "
        "Query with: SELECT * FROM emails_fts WHERE emails_fts MATCH 'search terms'. "
        'Supports prefix search (proj*), phrase search ("exact phrase"), and boolean operators (AND, OR, NOT).'
    ),
    "marginalia": (
        "Free-form notes attached to email/thread records via URIs. "
        "Use marginalia tools (create/list/get/update/delete/restore) rather than raw SQL. "
        "archived_at is NULL for live records; set to a timestamp for soft-deleted records."
    ),
    "marginalia_targets": (
        "Join table linking marginalia to target URIs (mail-memex://email/<id>, etc.). "
        "Managed automatically by marginalia tools."
    ),
}

QUERY_TIPS: list[str] = [
    "FTS5 full-text search: SELECT rowid, * FROM emails_fts WHERE emails_fts MATCH 'project report'",
    "FTS5 prefix and phrase: MATCH 'proj*' for prefix, MATCH '\"exact phrase\"' for phrases, MATCH 'a AND b' for boolean",
    "Tag join: SELECT e.* FROM emails e JOIN email_tags et ON e.id = et.email_id JOIN tags t ON et.tag_id = t.id WHERE t.name = 'inbox'",
    "Thread conversation: SELECT * FROM emails WHERE thread_id = '<thread-id-here>' ORDER BY date",
    "Date filtering: SELECT * FROM emails WHERE date >= '2024-01-01' AND date < '2024-02-01'",
    "Count by sender: SELECT from_addr, COUNT(*) AS cnt FROM emails GROUP BY from_addr ORDER BY cnt DESC LIMIT 20",
    "Recipient search (to/cc/bcc): SELECT * FROM emails WHERE to_addrs LIKE '%alice@example.com%'",
    "JSON metadata query: SELECT * FROM emails WHERE json_extract(metadata_json, '$.source') = 'gmail'",
    "Soft-deleted records: WHERE archived_at IS NULL (default filters) or WHERE archived_at IS NOT NULL (show deleted)",
    "Marginalia: use marginalia tools (create/list/get/update/delete/restore) rather than raw SQL",
]

# ---------------------------------------------------------------------------
# SQL safety via sqlite3 authorizer callback
#
# sqlite3's authorizer runs at statement-prepare time against the parsed AST,
# so string literals, comments, and case variations cannot defeat it — unlike
# regex against raw SQL. The callback receives a numeric action code (one of
# the SQLITE_* constants below) and returns SQLITE_OK / SQLITE_DENY.
# ---------------------------------------------------------------------------


def _codes(*names: str) -> frozenset[int]:
    return frozenset(
        code for code in (getattr(sqlite3, n, None) for n in names) if code is not None
    )


_DDL_ACTIONS = _codes(
    "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE",
    "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE",
    "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW",
    "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE",
    "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
    "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW",
    "SQLITE_ALTER_TABLE", "SQLITE_REINDEX",
    "SQLITE_ATTACH", "SQLITE_DETACH",
)

_WRITE_ACTIONS = _codes("SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE")

# PRAGMAs that are always blocked — writable_schema allows editing
# sqlite_master and bypassing every higher-level safety check (triggers,
# constraints, schema); journal_mode switching can corrupt an in-use
# WAL database if another connection holds locks.
_BLOCKED_PRAGMAS = frozenset({"writable_schema", "journal_mode"})


class _AuthContext:
    """Per-call authorizer state: remembers why the last statement was denied."""

    def __init__(self, readonly: bool) -> None:
        self.readonly = readonly
        self.reason: str | None = None

    def __call__(
        self, action: int, arg1: Any, arg2: Any, db_name: Any, trigger: Any
    ) -> int:
        if action in _DDL_ACTIONS:
            self.reason = (
                "DDL statements (DROP/ALTER/CREATE/ATTACH/DETACH/REINDEX) "
                "are not allowed"
            )
            return sqlite3.SQLITE_DENY
        if (
            action == sqlite3.SQLITE_PRAGMA
            and isinstance(arg1, str)
            and arg1.lower() in _BLOCKED_PRAGMAS
        ):
            # arg1 is the pragma name (e.g. 'writable_schema'); arg2 is its
            # value when setting (None when reading). Block both read and
            # write for names on the danger list.
            self.reason = f"PRAGMA {arg1} is blocked — bypasses schema safety."
            return sqlite3.SQLITE_DENY
        if self.readonly and action in _WRITE_ACTIONS:
            self.reason = (
                "Write statements (INSERT/UPDATE/DELETE) are blocked in "
                "readonly mode. Set readonly=false to allow."
            )
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


# ---------------------------------------------------------------------------
# Tool implementations (module-level, testable independently)
# ---------------------------------------------------------------------------


def get_schema_impl(session: Any) -> str:
    """Return full database schema as a JSON string.

    Reads sqlite_master for DDL, PRAGMA table_info for columns on regular
    tables, and includes human-readable descriptions and query tips.
    """
    conn = session.connection()
    raw = conn.connection.driver_connection

    tables: dict[str, Any] = {}

    cursor = raw.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    for row_type, name, ddl in cursor.fetchall():
        entry: dict[str, Any] = {
            "type": row_type,
            "ddl": ddl,
            "description": TABLE_DESCRIPTIONS.get(name, ""),
        }

        if row_type == "table" and not (ddl and "VIRTUAL TABLE" in ddl.upper()):
            col_cursor = raw.execute(f"PRAGMA table_info('{name}')")
            columns = []
            for col_row in col_cursor.fetchall():
                columns.append(
                    {
                        "name": col_row[1],
                        "type": col_row[2],
                        "notnull": bool(col_row[3]),
                        "default": col_row[4],
                        "pk": bool(col_row[5]),
                    }
                )
            entry["columns"] = columns

        tables[name] = entry

    result = {
        "tables": tables,
        "tips": QUERY_TIPS,
    }
    return json.dumps(result)


def execute_sql_impl(session: Any, sql: str, readonly: bool = True) -> str:
    """Execute SQL and return results as a JSON string.

    For SELECT/PRAGMA: returns a JSON array of row objects.
    For writes (when readonly=False): returns {"affected_rows": N}.
    On error: returns {"error": "message"}.

    DDL and (in readonly mode) writes are blocked by sqlite3's authorizer
    callback — statement-level enforcement that cannot be defeated by
    string literals, comments, or case variations.
    """
    raw = session.connection().connection.driver_connection
    ctx = _AuthContext(readonly=readonly)
    raw.set_authorizer(ctx)
    try:
        result = session.execute(text(sql))
        if result.returns_rows:
            rows = [dict(row._mapping) for row in result]
            return json.dumps(rows, default=str)
        return json.dumps({"affected_rows": result.rowcount})
    except Exception as e:
        if ctx.reason is not None:
            return json.dumps({"error": ctx.reason})
        return json.dumps({"error": str(e)})
    finally:
        raw.set_authorizer(None)


_URI_SCHEME = "mail-memex"


def _parse_uri(uri: str) -> tuple[str, str]:
    """Parse a mail-memex://<kind>/<id>[#fragment] URI into (kind, id).

    Fragments are stripped — they address positions within a record
    (e.g. '#part=2', '#msg-45-67') and are opaque to the resolution
    layer per the *-memex URI scheme (see memex/docs/uri-scheme.md).

    Raises ValueError for malformed input or a non-mail-memex scheme.
    """
    sep = "://"
    idx = uri.find(sep)
    if idx < 0 or uri[:idx] != _URI_SCHEME:
        raise ValueError(
            f"URI must begin with '{_URI_SCHEME}://'; got: {uri!r}"
        )
    rest = uri[idx + len(sep):]
    frag = rest.find("#")
    if frag >= 0:
        rest = rest[:frag]
    slash = rest.find("/")
    if slash < 0:
        raise ValueError(f"URI missing '/<id>' portion: {uri!r}")
    kind = rest[:slash]
    record_id = rest[slash + 1:]
    if not kind or not record_id:
        raise ValueError(
            f"URI must be {_URI_SCHEME}://<kind>/<id>; got: {uri!r}"
        )
    return kind, record_id


def get_record_impl(session: Any, uri: str) -> str:
    """Resolve a mail-memex:// URI and return the record as JSON.

    Accepted URIs:
      mail-memex://email/<message_id>
      mail-memex://thread/<thread_id>
      mail-memex://marginalia/<uuid>

    Position fragments (e.g. '#part=2') are stripped before lookup —
    the underlying record is returned regardless of the fragment, since
    fragments address positions within a record, not separate records.

    Returns archived (soft-deleted) records too, so cross-archive
    references (trails, marginalia) stay resolvable after soft-deletion.
    """
    from mail_memex.core.marginalia import get_marginalia
    from mail_memex.core.models import Email, Thread

    try:
        kind, record_id = _parse_uri(uri)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    def _iso(dt: Any) -> Any:
        return dt.isoformat() if dt else None

    if kind == "email":
        email = session.query(Email).filter_by(message_id=record_id).first()
        record = (
            None
            if email is None
            else {
                "message_id": email.message_id,
                "from_addr": email.from_addr,
                "from_name": email.from_name,
                "to_addrs": email.to_addrs,
                "subject": email.subject,
                "date": _iso(email.date),
                "body_preview": email.body_preview,
                "thread_id": email.thread_id,
                "archived_at": _iso(email.archived_at),
            }
        )
    elif kind == "thread":
        thread = session.query(Thread).filter_by(thread_id=record_id).first()
        record = (
            None
            if thread is None
            else {
                "thread_id": thread.thread_id,
                "subject": thread.subject,
                "email_count": thread.email_count,
                "first_date": _iso(thread.first_date),
                "last_date": _iso(thread.last_date),
                "archived_at": _iso(thread.archived_at),
            }
        )
    elif kind == "marginalia":
        record = get_marginalia(session, record_id)
    else:
        return json.dumps(
            {"error": f"Unknown kind: {kind}. Valid: email, thread, marginalia"}
        )

    if record is None:
        return json.dumps({"error": "NOT_FOUND"})
    return json.dumps(record, default=str)


def search_emails_impl(session: Any, query: str, limit: int = 50) -> str:
    """Search emails using the SearchEngine and return JSON results.

    Wraps SearchEngine.search() and serializes results to a list of
    email summary dicts.
    """
    from mail_memex.search.engine import SearchEngine

    engine = SearchEngine(session)
    results = engine.search(query, limit=limit)

    output = []
    for sr in results:
        e = sr.email
        output.append(
            {
                "message_id": e.message_id,
                "from_addr": e.from_addr,
                "from_name": e.from_name,
                "subject": e.subject,
                "date": e.date.isoformat() if e.date else None,
                "to_addrs": e.to_addrs,
                "body_preview": e.body_preview,
                "thread_id": e.thread_id,
                "score": sr.score,
                "match_type": sr.match_type,
            }
        )

    return json.dumps(output, default=str)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _get_db_path() -> Path:
    """Resolve database path from environment or config."""
    env_path = os.environ.get("MAIL_MEMEX_DATABASE_PATH")
    if env_path:
        return Path(env_path)

    config = MailMemexConfig.load()
    if config.db_path:
        return config.db_path

    return MailMemexConfig.default_data_dir() / "mail-memex.db"


def create_server() -> FastMCP:
    """Create and configure the FastMCP server with all tools."""
    mcp = FastMCP(
        "mail-memex",
        instructions=(
            "mail-memex is a personal email archive with full-text search. "
            "Use get_schema to discover tables, execute_sql for SQL queries, "
            "get_record to resolve email/thread/marginalia URIs, and "
            "search_emails for Gmail-like query syntax."
        ),
    )

    db_path = _get_db_path()
    db = Database(db_path)
    db.create_tables()

    # ----- Contract tools -----

    @mcp.tool(
        name="get_schema",
        description="Get the full database schema as JSON, including table DDL, column details, descriptions, and query tips.",
    )
    def get_schema_tool() -> str:
        with db.session() as session:
            return get_schema_impl(session)

    @mcp.tool(
        name="execute_sql",
        description="Execute a SQL query against the mail-memex email archive database. Returns JSON array of row objects for SELECT, or affected_rows for writes.",
    )
    def execute_sql_tool(sql: str, readonly: bool = True) -> str:
        with db.session() as session:
            return execute_sql_impl(session, sql, readonly=readonly)

    @mcp.tool(
        name="get_record",
        description=(
            "Resolve a mail-memex:// URI to its record as JSON. Accepts "
            "mail-memex://email/<message_id>, mail-memex://thread/<thread_id>, "
            "and mail-memex://marginalia/<uuid>. Position fragments (e.g. "
            "'#part=2') are ignored for lookup. Returns soft-deleted records "
            "too so cross-archive references stay resolvable."
        ),
    )
    def get_record_tool(uri: str) -> str:
        with db.session() as session:
            return get_record_impl(session, uri)

    # ----- Domain tools -----

    @mcp.tool(
        name="search_emails",
        description=(
            "Search emails using Gmail-like query syntax. "
            "Operators: from:, to:, subject:, after:YYYY-MM-DD, before:YYYY-MM-DD, "
            "tag:, has:attachment, thread:. Free text searches subject and body."
        ),
    )
    def search_emails_tool(query: str, limit: int = 50) -> str:
        with db.session() as session:
            return search_emails_impl(session, query, limit=limit)

    # ----- Marginalia tools -----

    from mail_memex.core import marginalia as mg

    def _marginalia_op(fn, *args, mutates: bool, **kwargs) -> str:
        """Run a marginalia CRUD function, commit if mutating, JSON-encode result."""
        with db.session() as session:
            result = fn(session, *args, **kwargs)
            if result is None:
                return json.dumps({"error": "NOT_FOUND"})
            if mutates:
                session.commit()
            return json.dumps(result, default=str)

    @mcp.tool(
        name="create_marginalia",
        description="Create a new marginalia note attached to one or more target URIs (e.g. mail-memex://email/<message_id>).",
    )
    def create_marginalia_tool(
        target_uris: list[str],
        content: str,
        category: str | None = None,
        color: str | None = None,
        pinned: bool = False,
    ) -> str:
        return _marginalia_op(
            mg.create_marginalia,
            target_uris=target_uris,
            content=content,
            category=category,
            color=color,
            pinned=pinned,
            mutates=True,
        )

    @mcp.tool(
        name="list_marginalia",
        description="List marginalia notes, optionally filtered by target URI. Returns newest first.",
    )
    def list_marginalia_tool(
        target_uri: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> str:
        return _marginalia_op(
            mg.list_marginalia,
            target_uri=target_uri,
            include_archived=include_archived,
            limit=limit,
            mutates=False,
        )

    @mcp.tool(
        name="get_marginalia",
        description="Fetch a single marginalia note by its UUID.",
    )
    def get_marginalia_tool(uuid: str) -> str:
        return _marginalia_op(mg.get_marginalia, uuid, mutates=False)

    @mcp.tool(
        name="update_marginalia",
        description="Update fields on an existing marginalia note. Only provided fields are changed.",
    )
    def update_marginalia_tool(
        uuid: str,
        content: str | None = None,
        category: str | None = None,
        color: str | None = None,
        pinned: bool | None = None,
    ) -> str:
        return _marginalia_op(
            mg.update_marginalia,
            uuid=uuid,
            content=content,
            category=category,
            color=color,
            pinned=pinned,
            mutates=True,
        )

    @mcp.tool(
        name="delete_marginalia",
        description="Delete a marginalia note. Soft delete by default (sets archived_at). Pass hard=true to permanently remove.",
    )
    def delete_marginalia_tool(uuid: str, hard: bool = False) -> str:
        return _marginalia_op(mg.delete_marginalia, uuid=uuid, hard=hard, mutates=True)

    @mcp.tool(
        name="restore_marginalia",
        description="Undo a soft delete on a marginalia note by clearing archived_at.",
    )
    def restore_marginalia_tool(uuid: str) -> str:
        return _marginalia_op(mg.restore_marginalia, uuid=uuid, mutates=True)

    return mcp
