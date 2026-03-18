"""MCP server with pure-SQL interface: run_sql + get_schema.

Exposes the mtk SQLite database via two tools, letting LLMs query
and (optionally) mutate the archive using plain SQL.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from mtk.core.config import MtkConfig
from mtk.core.database import Database

# ---------------------------------------------------------------------------
# Table descriptions (human-readable, for LLM context)
# ---------------------------------------------------------------------------

TABLE_DESCRIPTIONS: dict[str, str] = {
    "emails": "Email messages with headers, content, and metadata (metadata_json for flexible extras)",
    "threads": "Email threads/conversations grouping related emails",
    "tags": "Tags applied to emails (synced from notmuch or created in mtk)",
    "email_tags": "Association table linking emails to tags (many-to-many)",
    "attachments": "Email attachment metadata (filename, type, size)",
    "imap_sync_state": "IMAP sync state per account/folder for incremental sync",
    "emails_fts": (
        "FTS5 full-text search index on emails (subject, body_text, from_addr, from_name). "
        "Query with: SELECT * FROM emails_fts WHERE emails_fts MATCH 'search terms'"
    ),
}

QUERY_TIPS: list[str] = [
    "Use emails_fts for full-text search: SELECT * FROM emails_fts WHERE emails_fts MATCH 'term'",
    "Join emails to tags via email_tags: SELECT e.* FROM emails e JOIN email_tags et ON e.id = et.email_id JOIN tags t ON et.tag_id = t.id WHERE t.name = 'inbox'",
    "Thread conversation: SELECT * FROM emails WHERE thread_id = ? ORDER BY date",
    "Date filtering: SELECT * FROM emails WHERE date >= '2024-01-01' AND date < '2024-02-01'",
    "FTS5 supports prefix queries: MATCH 'proj*' and phrase queries: MATCH '\"exact phrase\"'",
    "Count by sender: SELECT from_addr, COUNT(*) as cnt FROM emails GROUP BY from_addr ORDER BY cnt DESC",
]

# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema for MCP input validation)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": "Get the full database schema as JSON, including table DDL, column details, descriptions, and query tips.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "run_sql",
        "description": "Execute a SQL query against the mtk email archive database. Returns JSON array of row objects for SELECT, or affected_rows for writes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SQL query to execute",
                },
                "readonly": {
                    "type": "boolean",
                    "description": "If true (default), block INSERT/UPDATE/DELETE/REPLACE. DDL is always blocked.",
                    "default": True,
                },
            },
            "required": ["sql"],
        },
    },
]

# ---------------------------------------------------------------------------
# Regex patterns for SQL safety
# ---------------------------------------------------------------------------

_DDL_PATTERN = re.compile(r"\b(DROP|ALTER|CREATE|ATTACH|DETACH)\b", re.IGNORECASE)
_WRITE_PATTERN = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Tool implementations (module-level, testable independently)
# ---------------------------------------------------------------------------


def get_schema(session: Any) -> str:
    """Return full database schema as a JSON string.

    Reads sqlite_master for DDL, PRAGMA table_info for columns on regular
    tables, and includes human-readable descriptions and query tips.
    """
    conn = session.connection()
    raw = conn.connection.driver_connection  # unwrap to raw dbapi connection

    tables: dict[str, Any] = {}

    # Get all tables and views from sqlite_master
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

        # Add column details for regular tables (not views, not FTS virtual tables)
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


def run_sql(session: Any, sql: str, readonly: bool = True) -> str:
    """Execute SQL and return results as a JSON string.

    For SELECT/PRAGMA: returns a JSON array of row objects.
    For writes (when readonly=False): returns {"affected_rows": N}.
    On error: returns {"error": "message"}.
    DDL (DROP/ALTER/CREATE/ATTACH/DETACH) is always blocked.
    """
    # Always block DDL
    if _DDL_PATTERN.search(sql):
        return json.dumps(
            {"error": "DDL statements (DROP/ALTER/CREATE/ATTACH/DETACH) are not allowed"}
        )

    # Block writes in readonly mode
    if readonly and _WRITE_PATTERN.search(sql):
        return json.dumps(
            {
                "error": "Write statements (INSERT/UPDATE/DELETE/REPLACE) are blocked in readonly mode. Set readonly=false to allow."
            }
        )

    try:
        conn = session.connection()
        raw = conn.connection.driver_connection  # unwrap to raw dbapi connection
        cursor = raw.execute(sql)

        # If it's a write operation (non-readonly), commit and return affected rows
        if _WRITE_PATTERN.search(sql):
            raw.commit()
            return json.dumps({"affected_rows": cursor.rowcount})

        # SELECT or PRAGMA — return rows as JSON array of dicts
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
            return json.dumps(rows, default=str)

        # Statement produced no results (e.g., empty PRAGMA)
        return json.dumps([])

    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def _get_db_path() -> Path:
    """Resolve database path from environment or config."""
    env_path = os.environ.get("MTK_DATABASE_PATH")
    if env_path:
        return Path(env_path)

    config = MtkConfig.load()
    if config.db_path:
        return config.db_path

    return MtkConfig.default_data_dir() / "mtk.db"


def create_server() -> Server:
    """Create and configure the MCP server with run_sql and get_schema tools."""
    server = Server("mtk")

    db_path = _get_db_path()
    db = Database(db_path)
    db.create_tables()

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name=td["name"],
                description=td["description"],
                inputSchema=td["inputSchema"],
            )
            for td in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}

        if name == "get_schema":
            with db.session() as session:
                result = get_schema(session)
            return [TextContent(type="text", text=result)]

        if name == "run_sql":
            sql_str = arguments.get("sql", "")
            readonly = arguments.get("readonly", True)
            with db.session() as session:
                result = run_sql(session, sql_str, readonly=readonly)
            return [TextContent(type="text", text=result)]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server
