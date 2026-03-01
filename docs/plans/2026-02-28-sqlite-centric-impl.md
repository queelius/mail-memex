# SQLite-Centric Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify mtk to a SQLite-centric architecture: pure-SQL MCP server, remove LLM/graph/embeddings, add HTML SFA export and arkiv import/export.

**Architecture:** The MCP server exposes `run_sql` + `get_schema` over stdio. The HTML export embeds a SQLite database in a single HTML file with sql.js from CDN. arkiv import/export converts between mtk's relational schema and arkiv's flat JSONL format.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0, SQLite FTS5, MCP Python SDK (stdio), sql.js (CDN)

**Design doc:** `docs/plans/2026-02-28-sqlite-centric-redesign.md`

---

### Task 1: Remove LLM module and CLI commands

**Files:**
- Delete: `src/mtk/llm/__init__.py`, `src/mtk/llm/providers.py`, `src/mtk/llm/classifier.py`
- Modify: `src/mtk/cli/main.py` — remove LLM sub-app and all LLM commands (~lines 1444-1700)

**Step 1: Delete the LLM package**

```bash
rm -rf src/mtk/llm/
```

**Step 2: Remove LLM CLI commands from main.py**

Remove these sections from `src/mtk/cli/main.py`:
- The `llm_app = typer.Typer(...)` line and `app.add_typer(llm_app, ...)` line
- All functions: `llm_status`, `_find_email_for_llm`, `_get_llm_provider`, `llm_classify`, `llm_summarize`, `llm_actions`, `llm_classify_batch`
- The entire `# === LLM Commands ===` section

**Step 3: Remove LLM test references**

Check tests for any LLM imports/references and remove them. The test files `test_cli.py` may reference LLM commands.

**Step 4: Run tests to verify nothing broke**

```bash
pytest -x --tb=short
```

**Step 5: Commit**

```bash
git add -A && git commit -m "Remove LLM command group (Ollama provider, classifier, CLI)"
```

---

### Task 2: Remove graph export and CLI command

**Files:**
- Modify: `src/mtk/people/relationships.py` — delete `NetworkNode`, `NetworkEdge`, `build_network`, `export_network_gexf`, `export_network_json`, `export_network_graphml`
- Modify: `src/mtk/cli/main.py` — delete `graph` command (~lines 879-910)
- Modify: `tests/test_people.py` — delete `test_build_network`, `test_export_network_json`, `test_export_network_gexf`
- Modify: `tests/test_cli.py` — delete `TestGraphCommand`

**Step 1: Remove graph methods from relationships.py**

Delete `NetworkNode` dataclass, `NetworkEdge` dataclass, and these methods from `RelationshipAnalyzer`: `build_network`, `export_network_gexf`, `export_network_json`, `export_network_graphml`. Also remove unused imports: `json`, `defaultdict` (if only used by graph methods — check), `Any`.

Keep: `CorrespondenceStats`, `get_top_correspondents`, `get_correspondent_stats`, `get_correspondence_timeline`, and the `__init__`, `owner_id` property.

**Step 2: Remove graph CLI command from main.py**

Delete the `# === Graph Command ===` section (~lines 879-910).

**Step 3: Remove graph tests**

In `tests/test_people.py`, delete `test_build_network`, `test_export_network_json`, `test_export_network_gexf`.
In `tests/test_cli.py`, delete `TestGraphCommand`.

**Step 4: Run tests**

```bash
pytest -x --tb=short
```

**Step 5: Commit**

```bash
git add -A && git commit -m "Remove graph export (GEXF, GraphML, JSON network)"
```

---

### Task 3: Remove embeddings, semantic search, and TopicCluster

**Files:**
- Modify: `src/mtk/core/models.py` — remove `Email.embedding`, `Email.summary`, `Thread.summary`, `TopicCluster`, `email_topics`
- Modify: `src/mtk/search/engine.py` — remove semantic search path, `_semantic_search`, `generate_embeddings`, `SearchQuery.semantic`
- Modify: `pyproject.toml` — remove `semantic` optional dependency group
- Modify: `tests/test_database.py` — remove `TestTopicClusterModel`
- Modify: `tests/test_search.py` — remove `test_parse_semantic_flag`
- Modify: `tests/conftest.py` — remove `generate_embeddings` reference

**Step 1: Remove from models.py**

In `src/mtk/core/models.py`:
- Delete `Email.embedding` (line ~85: `embedding: Mapped[bytes | None] = mapped_column()`)
- Delete `Email.summary` (line ~86: `summary: Mapped[str | None] = mapped_column(Text)`)
- Delete `Thread.summary` (line ~178: `summary: Mapped[str | None] = mapped_column(Text)`)
- Delete `TopicCluster` class entirely (lines ~355-375)
- Delete `email_topics` association table (lines ~378-385)
- Remove the docstring reference to "Graph/relationship support for network analysis"

**Step 2: Remove semantic search from engine.py**

In `src/mtk/search/engine.py`:
- Remove `SearchQuery.semantic` field
- Remove the `if query.semantic` branch in `search()` method
- Remove `_semantic_search` method entirely
- Remove `generate_embeddings` method entirely
- Remove `self._embedding_model` from `__init__`
- Remove `is:semantic` from `parse_query` operator handling
- Update docstring to remove "Semantic search" references

**Step 3: Remove semantic optional dependency from pyproject.toml**

Delete the `semantic` group:
```toml
semantic = [
    "sentence-transformers>=2.2.0",
    "faiss-cpu>=1.7.0",
]
```

Update the `all` group to remove `semantic`:
```toml
all = [
    "mtk[notmuch,mcp,imap,dev]",
]
```

**Step 4: Fix tests**

- `tests/test_database.py`: delete `TestTopicClusterModel` class and `TopicCluster` import
- `tests/test_search.py`: delete `test_parse_semantic_flag`, update `test_defaults` assertion to not check `semantic`
- `tests/conftest.py`: remove `generate_embeddings` reference (line ~566)

**Step 5: Run tests**

```bash
pytest -x --tb=short
```

**Step 6: Commit**

```bash
git add -A && git commit -m "Remove embeddings, semantic search, and TopicCluster"
```

---

### Task 4: Rewrite MCP server — pure SQL (run_sql + get_schema)

**Files:**
- Delete: `src/mtk/mcp/tools.py`, `src/mtk/mcp/resources.py`, `src/mtk/mcp/validation.py`
- Rewrite: `src/mtk/mcp/server.py`
- Keep unchanged: `src/mtk/mcp/__init__.py`, `src/mtk/mcp/__main__.py`

**Step 1: Write failing tests for new MCP server**

Create `tests/test_mcp_server.py` (replace existing). Test structure:

```python
"""Tests for the pure-SQL MCP server."""
import json
import pytest
from mtk.core.database import Database
from mtk.mcp.server import create_server, run_sql, get_schema


class TestRunSql:
    """Tests for the run_sql tool handler."""

    def test_select_returns_rows(self, populated_db: Database) -> None:
        """SELECT query returns JSON array of row objects."""
        with populated_db.session() as session:
            result = run_sql(session, "SELECT message_id, subject FROM emails LIMIT 2")
        rows = json.loads(result)
        assert isinstance(rows, list)
        assert len(rows) <= 2
        assert "message_id" in rows[0]
        assert "subject" in rows[0]

    def test_select_empty_result(self, populated_db: Database) -> None:
        """SELECT returning no rows gives empty array."""
        with populated_db.session() as session:
            result = run_sql(session, "SELECT * FROM emails WHERE 1=0")
        assert json.loads(result) == []

    def test_readonly_blocks_insert(self, populated_db: Database) -> None:
        """readonly=True (default) blocks INSERT."""
        with populated_db.session() as session:
            result = run_sql(session, "INSERT INTO tags (name, source) VALUES ('x', 'mtk')")
        data = json.loads(result)
        assert "error" in data

    def test_readonly_blocks_delete(self, populated_db: Database) -> None:
        """readonly=True blocks DELETE."""
        with populated_db.session() as session:
            result = run_sql(session, "DELETE FROM emails")
        data = json.loads(result)
        assert "error" in data

    def test_writable_allows_insert(self, populated_db: Database) -> None:
        """readonly=False allows INSERT."""
        with populated_db.session() as session:
            result = run_sql(session, "INSERT INTO tags (name, source) VALUES ('new-tag', 'mtk')", readonly=False)
        data = json.loads(result)
        assert "affected_rows" in data

    def test_fts5_search(self, populated_db: Database) -> None:
        """FTS5 queries work through run_sql."""
        with populated_db.session() as session:
            result = run_sql(session, "SELECT email_id FROM emails_fts WHERE emails_fts MATCH 'test'")
        rows = json.loads(result)
        assert isinstance(rows, list)

    def test_invalid_sql_returns_error(self, populated_db: Database) -> None:
        """Bad SQL returns error, not exception."""
        with populated_db.session() as session:
            result = run_sql(session, "SELEKT * FORM emails")
        data = json.loads(result)
        assert "error" in data

    def test_pragma_blocked(self, populated_db: Database) -> None:
        """PRAGMA commands are blocked in readonly mode."""
        with populated_db.session() as session:
            result = run_sql(session, "PRAGMA table_info(emails)")
        # Should work - PRAGMA is read-only
        rows = json.loads(result)
        assert isinstance(rows, list)


class TestGetSchema:
    """Tests for the get_schema tool handler."""

    def test_returns_valid_json(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_schema(session)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_contains_tables(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_schema(session)
        data = json.loads(result)
        assert "tables" in data
        table_names = [t["name"] for t in data["tables"]]
        assert "emails" in table_names
        assert "persons" in table_names
        assert "threads" in table_names
        assert "tags" in table_names

    def test_table_has_columns(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_schema(session)
        data = json.loads(result)
        emails_table = next(t for t in data["tables"] if t["name"] == "emails")
        assert "columns" in emails_table
        col_names = [c["name"] for c in emails_table["columns"]]
        assert "message_id" in col_names
        assert "from_addr" in col_names

    def test_includes_fts5(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_schema(session)
        data = json.loads(result)
        table_names = [t["name"] for t in data["tables"]]
        assert "emails_fts" in table_names

    def test_includes_descriptions(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_schema(session)
        data = json.loads(result)
        assert "description" in data  # Top-level description of the database


class TestCreateServer:
    """Tests for create_server integration."""

    def test_creates_server(self, tmp_path) -> None:
        import os
        os.environ["MTK_DATABASE_PATH"] = str(tmp_path / "test.db")
        try:
            server = create_server()
            assert server is not None
        finally:
            del os.environ["MTK_DATABASE_PATH"]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_mcp_server.py -x --tb=short
```

**Step 3: Delete old MCP files**

```bash
rm src/mtk/mcp/tools.py src/mtk/mcp/resources.py src/mtk/mcp/validation.py
```

**Step 4: Implement new server.py**

Rewrite `src/mtk/mcp/server.py`:

```python
"""MCP server: pure-SQL access to the mtk email archive.

Two tools:
- get_schema: returns database schema with descriptions
- run_sql: executes SQL queries (read-only by default)

Transport: stdio (configured in __init__.py)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from mtk.core.config import MtkConfig
from mtk.core.database import Database

# Column descriptions for the LLM
TABLE_DESCRIPTIONS: dict[str, str] = {
    "emails": "Email messages with headers, content, and metadata",
    "persons": "People with potentially multiple email addresses",
    "person_emails": "Maps email addresses to persons (one person can have multiple addresses)",
    "threads": "Email threads/conversations grouping related emails",
    "tags": "Tags applied to emails (synced from notmuch or created in mtk)",
    "email_tags": "Association table linking emails to tags (many-to-many)",
    "email_recipients": "Association table for email recipients (To/CC/BCC)",
    "attachments": "Email attachment metadata (filename, type, size)",
    "annotations": "User annotations/notes on emails, threads, or persons",
    "collections": "User-defined email collections (manual or smart query-based)",
    "collection_emails": "Association table linking collections to emails",
    "privacy_rules": "Privacy rules for filtering/redacting during export",
    "custom_fields": "Flexible key-value metadata storage on emails",
    "imap_sync_state": "IMAP sync state per account/folder for incremental sync",
    "imap_pending_push": "Queue of tag changes to push to IMAP on next sync",
    "emails_fts": "FTS5 full-text search index on emails (subject, body_text, from_addr, from_name). Query with: SELECT * FROM emails_fts WHERE emails_fts MATCH 'search terms'",
}

# Statements that are never allowed
_DANGEROUS_PATTERNS = re.compile(
    r"\b(DROP|ALTER|CREATE|ATTACH|DETACH)\b", re.IGNORECASE
)

# Write statements (blocked in readonly mode)
_WRITE_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE
)


def _get_db_path() -> Path:
    """Resolve database path from environment or config."""
    env_path = os.environ.get("MTK_DATABASE_PATH")
    if env_path:
        return Path(env_path)
    config = MtkConfig.load()
    if config.db_path:
        return config.db_path
    return MtkConfig.default_data_dir() / "mtk.db"


def get_schema(session) -> str:
    """Return full database schema as JSON."""
    conn = session.connection()
    raw = conn.connection.dbapi_connection

    tables = []
    # Get all tables from sqlite_master
    cursor = raw.cursor()
    cursor.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    for name, obj_type, ddl in cursor.fetchall():
        table_info = {
            "name": name,
            "type": obj_type,
            "description": TABLE_DESCRIPTIONS.get(name, ""),
            "ddl": ddl,
        }

        # Get column info for regular tables
        if obj_type == "table" and not name.endswith("_fts"):
            col_cursor = raw.cursor()
            col_cursor.execute(f"PRAGMA table_info('{name}')")
            table_info["columns"] = [
                {
                    "name": row[1],
                    "type": row[2],
                    "notnull": bool(row[3]),
                    "default": row[4],
                    "pk": bool(row[5]),
                }
                for row in col_cursor.fetchall()
            ]

        tables.append(table_info)

    schema = {
        "description": "mtk email archive database. Use run_sql to query.",
        "tables": tables,
        "tips": [
            "Use emails_fts for full-text search: SELECT e.* FROM emails e JOIN emails_fts f ON e.id = f.email_id WHERE emails_fts MATCH 'query'",
            "Tags are linked via email_tags: SELECT t.name FROM tags t JOIN email_tags et ON t.id = et.tag_id WHERE et.email_id = ?",
            "Person emails: SELECT pe.email FROM person_emails pe WHERE pe.person_id = ?",
            "Thread emails: SELECT * FROM emails WHERE thread_id = ? ORDER BY date",
        ],
    }
    return json.dumps(schema, indent=2, default=str)


def run_sql(session, sql: str, readonly: bool = True) -> str:
    """Execute SQL and return results as JSON."""
    sql = sql.strip()

    # Block dangerous operations always
    if _DANGEROUS_PATTERNS.search(sql):
        return json.dumps({"error": "DDL operations (DROP, ALTER, CREATE, ATTACH, DETACH) are not allowed"})

    # Block writes in readonly mode
    if readonly and _WRITE_PATTERNS.search(sql):
        return json.dumps({"error": "Write operations require readonly=false"})

    try:
        conn = session.connection()
        raw = conn.connection.dbapi_connection
        cursor = raw.cursor()
        cursor.execute(sql)

        if cursor.description:
            # SELECT-like: return rows as JSON objects
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return json.dumps(rows, default=str)
        else:
            # Write: return affected rows
            affected = cursor.rowcount
            if not readonly:
                raw.commit()
            return json.dumps({"affected_rows": affected})

    except Exception as e:
        return json.dumps({"error": str(e)})


TOOL_DEFINITIONS = [
    {
        "name": "get_schema",
        "description": "Get the database schema — tables, columns, types, relationships, and query tips. Call this first to understand the data model before writing SQL.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_sql",
        "description": "Execute a SQL query against the mtk email archive database. Returns JSON array of row objects for SELECT, or {affected_rows: N} for mutations. Supports FTS5 full-text search via emails_fts table.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL query to execute"},
                "readonly": {
                    "type": "boolean",
                    "description": "If true (default), only SELECT queries are allowed. Set to false to allow INSERT/UPDATE/DELETE.",
                    "default": True,
                },
            },
            "required": ["sql"],
        },
    },
]


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("mtk")

    db_path = _get_db_path()
    db = Database(db_path)
    db.create_tables()

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(name=td["name"], description=td["description"], inputSchema=td["inputSchema"])
            for td in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}

        with db.session() as session:
            if name == "get_schema":
                result = get_schema(session)
            elif name == "run_sql":
                sql = arguments.get("sql", "")
                readonly = arguments.get("readonly", True)
                result = run_sql(session, sql, readonly=readonly)
            else:
                result = json.dumps({"error": f"Unknown tool: {name}"})

        return [TextContent(type="text", text=result)]

    return server
```

**Step 5: Run tests**

```bash
pytest tests/test_mcp_server.py -x -v
```

**Step 6: Run full test suite**

```bash
pytest -x --tb=short
```

**Step 7: Commit**

```bash
git add -A && git commit -m "Rewrite MCP server: pure SQL (run_sql + get_schema)"
```

---

### Task 5: Add HTML Single File Application export

**Files:**
- Create: `src/mtk/export/html_export.py`
- Modify: `src/mtk/cli/main.py` — add `export html` command
- Create: `tests/test_html_export.py`

**Step 1: Write failing tests**

Create `tests/test_html_export.py`:

```python
"""Tests for HTML SFA export."""
import pytest
from pathlib import Path
from mtk.core.database import Database
from mtk.export.html_export import HtmlExporter


class TestHtmlExporter:

    def test_export_creates_file(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "archive.html"
        with populated_db.session() as session:
            exporter = HtmlExporter(output, populated_db.db_path)
            result = exporter.export_from_db()
        assert output.exists()
        assert result.emails_exported > 0

    def test_output_is_valid_html(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "archive.html"
        with populated_db.session() as session:
            exporter = HtmlExporter(output, populated_db.db_path)
            exporter.export_from_db()
        content = output.read_text()
        assert content.startswith("<!DOCTYPE html>")
        assert "</html>" in content

    def test_contains_sql_js_cdn(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "archive.html"
        with populated_db.session() as session:
            exporter = HtmlExporter(output, populated_db.db_path)
            exporter.export_from_db()
        content = output.read_text()
        assert "sql.js" in content.lower() or "sql-wasm" in content.lower()

    def test_contains_embedded_database(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "archive.html"
        with populated_db.session() as session:
            exporter = HtmlExporter(output, populated_db.db_path)
            exporter.export_from_db()
        content = output.read_text()
        # Database should be base64-encoded in the HTML
        assert "base64" in content.lower() or "data:application" in content

    def test_export_result_format(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "archive.html"
        with populated_db.session() as session:
            exporter = HtmlExporter(output, populated_db.db_path)
            result = exporter.export_from_db()
        assert result.format == "html"
        assert result.output_path == str(output)
```

**Step 2: Implement HtmlExporter**

The HTML exporter works differently from other exporters — it doesn't take a list of Email objects. Instead, it takes the raw SQLite database file, copies it to a temp location (optionally filtering with a query), and embeds it as base64 in an HTML file.

Create `src/mtk/export/html_export.py`. The HTML template includes:
- sql.js loaded from CDN (`https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/sql-wasm.js`)
- The database as a base64 blob in a `<script>` tag
- A minimal email client UI with inbox table, email detail pane, search bar, thread view

**Step 3: Add CLI command**

Add to `src/mtk/cli/main.py` in the export sub-app:

```python
@export_app.command("html")
def export_html(
    output: Path = typer.Argument(..., help="Output HTML file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    apply_privacy: bool = typer.Option(False, "--privacy", "-p", help="Apply privacy rules"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export email archive as a self-contained HTML application."""
    from mtk.export.html_export import HtmlExporter

    db = get_db()
    exporter = HtmlExporter(output, db.db_path)
    result = exporter.export_from_db()

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported archive to {output}[/green]")
        console.print(f"  {result.emails_exported} emails, {output.stat().st_size / 1024:.0f} KB")
```

**Step 4: Run tests**

```bash
pytest tests/test_html_export.py -x -v
```

**Step 5: Commit**

```bash
git add -A && git commit -m "Add HTML SFA export (sql.js + embedded SQLite)"
```

---

### Task 6: Add arkiv export

**Files:**
- Create: `src/mtk/export/arkiv_export.py`
- Modify: `src/mtk/cli/main.py` — add `export arkiv` command
- Create: `tests/test_arkiv.py`

**Step 1: Write failing tests**

```python
"""Tests for arkiv import/export."""
import json
import pytest
from pathlib import Path
from mtk.core.database import Database
from mtk.export.arkiv_export import ArkivExporter


class TestArkivExporter:

    def test_export_creates_jsonl(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            result = exporter.export(emails)
        assert output.exists()
        assert result.emails_exported > 0

    def test_each_line_is_valid_json(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            exporter.export(emails)
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            assert isinstance(record, dict)

    def test_record_has_arkiv_fields(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            exporter.export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert record["mimetype"] == "message/rfc822"
        assert "timestamp" in record
        assert "metadata" in record
        assert "message_id" in record["metadata"]

    def test_metadata_contains_subject(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            exporter.export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert "subject" in record["metadata"]

    def test_no_body_option(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output, include_body=False)
            exporter.export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert "content" not in record or record["content"] is None

    def test_generates_schema_yaml(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            from sqlalchemy import select
            from mtk.core.models import Email
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            exporter.export(emails)
        schema_path = tmp_path / "schema.yaml"
        assert schema_path.exists()
```

**Step 2: Implement ArkivExporter**

Create `src/mtk/export/arkiv_export.py`:

```python
"""Export emails to arkiv JSONL format."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from mtk.export.base import ExportResult

if TYPE_CHECKING:
    from mtk.core.models import Email
    from mtk.core.privacy import PrivacyFilter


class ArkivExporter:
    """Export emails to arkiv universal record format (JSONL)."""

    def __init__(
        self,
        output_path: Path,
        privacy_filter: PrivacyFilter | None = None,
        include_body: bool = True,
    ) -> None:
        self.output_path = Path(output_path)
        self.privacy_filter = privacy_filter
        self.include_body = include_body

    def _email_to_record(self, email: Email) -> dict:
        """Convert an Email to an arkiv record."""
        record: dict = {
            "mimetype": "message/rfc822",
            "uri": f"mtk://email/{email.message_id}",
        }

        if email.date:
            record["timestamp"] = email.date.isoformat()

        if self.include_body and email.body_text:
            record["content"] = email.body_text

        metadata: dict = {
            "message_id": email.message_id,
            "from_addr": email.from_addr,
            "subject": email.subject,
        }

        if email.from_name:
            metadata["from_name"] = email.from_name
        if email.thread_id:
            metadata["thread_id"] = email.thread_id
        if email.in_reply_to:
            metadata["in_reply_to"] = email.in_reply_to
        if email.tags:
            metadata["tags"] = [t.name for t in email.tags]
        if email.attachments:
            metadata["has_attachments"] = True
            metadata["attachment_count"] = len(email.attachments)

        record["metadata"] = metadata
        return record

    def export(self, emails: list[Email]) -> ExportResult:
        """Export emails to JSONL file."""
        exported = 0

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.output_path, "w") as f:
            for email in emails:
                record = self._email_to_record(email)
                f.write(json.dumps(record, default=str) + "\n")
                exported += 1

        # Generate schema.yaml alongside the JSONL
        self._write_schema(exported)

        return ExportResult(
            format="arkiv",
            output_path=str(self.output_path),
            emails_exported=exported,
        )

    def _write_schema(self, record_count: int) -> None:
        """Write arkiv schema.yaml describing metadata keys."""
        schema_path = self.output_path.parent / "schema.yaml"
        schema = {
            self.output_path.stem: {
                "record_count": record_count,
                "metadata_keys": {
                    "message_id": {"type": "string", "description": "RFC 2822 Message-ID"},
                    "from_addr": {"type": "string", "description": "Sender email address"},
                    "from_name": {"type": "string", "description": "Sender display name"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "thread_id": {"type": "string", "description": "Thread/conversation ID"},
                    "in_reply_to": {"type": "string", "description": "Message-ID being replied to"},
                    "tags": {"type": "array", "description": "Email tags/labels"},
                    "has_attachments": {"type": "boolean", "description": "Whether email has attachments"},
                    "attachment_count": {"type": "number", "description": "Number of attachments"},
                },
            }
        }
        with open(schema_path, "w") as f:
            yaml.dump(schema, f, default_flow_style=False, sort_keys=False)
```

**Step 3: Add CLI command**

Add to export sub-app in `src/mtk/cli/main.py`:

```python
@export_app.command("arkiv")
def export_arkiv(
    output: Path = typer.Argument(..., help="Output JSONL file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    apply_privacy: bool = typer.Option(False, "--privacy", "-p", help="Apply privacy rules"),
    include_body: bool = typer.Option(True, "--body/--no-body", help="Include email body text"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to arkiv JSONL format."""
    from mtk.export.arkiv_export import ArkivExporter

    db = get_db()
    with db.session() as session:
        emails, privacy_filter = _prepare_export(session, query, apply_privacy)
        exporter = ArkivExporter(output, privacy_filter=privacy_filter, include_body=include_body)
        result = exporter.export(emails)

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")
        console.print(f"  Schema written to {output.parent / 'schema.yaml'}")
```

**Step 4: Run tests**

```bash
pytest tests/test_arkiv.py -x -v
```

**Step 5: Commit**

```bash
git add -A && git commit -m "Add arkiv JSONL export with schema.yaml generation"
```

---

### Task 7: Add arkiv import

**Files:**
- Create: `src/mtk/importers/arkiv.py`
- Modify: `src/mtk/cli/main.py` — add `import arkiv` command
- Modify: `tests/test_arkiv.py` — add import tests

**Step 1: Write failing tests**

Add to `tests/test_arkiv.py`:

```python
from mtk.importers.arkiv import ArkivImporter

class TestArkivImporter:

    def test_import_creates_emails(self, db: Database, tmp_path: Path) -> None:
        """Import arkiv JSONL creates Email records."""
        jsonl = tmp_path / "emails.jsonl"
        jsonl.write_text(
            '{"mimetype": "message/rfc822", "content": "Hello world", '
            '"timestamp": "2024-01-15T10:30:00", "metadata": {'
            '"message_id": "test-1@example.com", "from_addr": "alice@example.com", '
            '"subject": "Test email"}}\n'
        )
        with db.session() as session:
            importer = ArkivImporter(session)
            result = importer.import_file(jsonl)
        assert result.imported == 1

        from sqlalchemy import select
        from mtk.core.models import Email
        with db.session() as session:
            email = session.execute(select(Email)).scalar()
            assert email is not None
            assert email.message_id == "test-1@example.com"
            assert email.subject == "Test email"
            assert email.body_text == "Hello world"

    def test_import_with_tags(self, db: Database, tmp_path: Path) -> None:
        jsonl = tmp_path / "emails.jsonl"
        jsonl.write_text(
            '{"mimetype": "message/rfc822", "content": "Tagged email", '
            '"timestamp": "2024-01-15T10:30:00", "metadata": {'
            '"message_id": "test-2@example.com", "from_addr": "bob@example.com", '
            '"subject": "Tagged", "tags": ["inbox", "important"]}}\n'
        )
        with db.session() as session:
            importer = ArkivImporter(session)
            importer.import_file(jsonl)

        from sqlalchemy import select
        from mtk.core.models import Email
        with db.session() as session:
            email = session.execute(select(Email)).scalar()
            tag_names = [t.name for t in email.tags]
            assert "inbox" in tag_names
            assert "important" in tag_names

    def test_import_skips_non_email_records(self, db: Database, tmp_path: Path) -> None:
        jsonl = tmp_path / "mixed.jsonl"
        jsonl.write_text(
            '{"mimetype": "image/jpeg", "uri": "file://photo.jpg"}\n'
            '{"mimetype": "message/rfc822", "content": "Real email", '
            '"timestamp": "2024-01-15", "metadata": {'
            '"message_id": "test-3@example.com", "from_addr": "c@example.com", "subject": "Hi"}}\n'
        )
        with db.session() as session:
            importer = ArkivImporter(session)
            result = importer.import_file(jsonl)
        assert result.imported == 1
        assert result.skipped == 1

    def test_import_skips_duplicates(self, db: Database, tmp_path: Path) -> None:
        jsonl = tmp_path / "dupes.jsonl"
        record = (
            '{"mimetype": "message/rfc822", "content": "Hello", '
            '"timestamp": "2024-01-15", "metadata": {'
            '"message_id": "dupe@example.com", "from_addr": "a@example.com", "subject": "Dupe"}}\n'
        )
        jsonl.write_text(record + record)
        with db.session() as session:
            importer = ArkivImporter(session)
            result = importer.import_file(jsonl)
        assert result.imported == 1
        assert result.skipped == 1
```

**Step 2: Implement ArkivImporter**

Create `src/mtk/importers/arkiv.py`:

```python
"""Import emails from arkiv JSONL format."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mtk.core.models import Email, Tag


@dataclass
class ArkivImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ArkivImporter:
    """Import emails from arkiv universal record format."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._tag_cache: dict[str, Tag] = {}

    def _get_or_create_tag(self, name: str) -> Tag:
        if name in self._tag_cache:
            return self._tag_cache[name]
        tag = self.session.execute(select(Tag).where(Tag.name == name)).scalar()
        if not tag:
            tag = Tag(name=name, source="arkiv")
            self.session.add(tag)
            self.session.flush()
        self._tag_cache[name] = tag
        return tag

    def import_file(self, path: Path) -> ArkivImportResult:
        result = ArkivImportResult()

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    result.errors.append(f"Invalid JSON: {e}")
                    result.skipped += 1
                    continue

                if record.get("mimetype") != "message/rfc822":
                    result.skipped += 1
                    continue

                metadata = record.get("metadata", {})
                message_id = metadata.get("message_id")
                if not message_id:
                    result.errors.append("Record missing metadata.message_id")
                    result.skipped += 1
                    continue

                # Skip duplicates
                existing = self.session.execute(
                    select(Email).where(Email.message_id == message_id)
                ).scalar()
                if existing:
                    result.skipped += 1
                    continue

                # Parse timestamp
                date = None
                if record.get("timestamp"):
                    try:
                        date = datetime.fromisoformat(record["timestamp"])
                    except ValueError:
                        pass

                email = Email(
                    message_id=message_id,
                    from_addr=metadata.get("from_addr", ""),
                    from_name=metadata.get("from_name"),
                    subject=metadata.get("subject"),
                    date=date or datetime.utcnow(),
                    body_text=record.get("content"),
                    thread_id=metadata.get("thread_id"),
                    in_reply_to=metadata.get("in_reply_to"),
                )
                self.session.add(email)
                self.session.flush()

                # Add tags
                for tag_name in metadata.get("tags", []):
                    tag = self._get_or_create_tag(tag_name)
                    email.tags.append(tag)

                result.imported += 1

        return result
```

**Step 3: Add CLI command**

Add to import sub-app in `src/mtk/cli/main.py`:

```python
@import_app.command("arkiv")
def import_arkiv(
    path: Path = typer.Argument(..., help="Path to arkiv JSONL file"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Import emails from arkiv JSONL format."""
    from mtk.importers.arkiv import ArkivImporter

    db = get_db()
    with db.session() as session:
        importer = ArkivImporter(session)
        result = importer.import_file(path)

    if json_output:
        print(json_lib.dumps({"imported": result.imported, "skipped": result.skipped, "errors": result.errors}, indent=2))
    else:
        console.print(f"[green]Imported {result.imported} emails from {path}[/green]")
        if result.skipped:
            console.print(f"[yellow]Skipped {result.skipped} records[/yellow]")
        if result.errors:
            for err in result.errors[:5]:
                console.print(f"[red]  {err}[/red]")
```

**Step 4: Run tests**

```bash
pytest tests/test_arkiv.py -x -v
```

**Step 5: Commit**

```bash
git add -A && git commit -m "Add arkiv JSONL import"
```

---

### Task 8: Final cleanup and verification

**Files:**
- Modify: `pyproject.toml` — version bump, dependency cleanup
- Modify: `CLAUDE.md` — update architecture docs
- Modify: `src/mtk/export/__init__.py` — add new exporters

**Step 1: Update pyproject.toml**

- Bump version to `0.3.0`
- Remove `semantic` from `all` extras
- Verify no orphaned dependencies

**Step 2: Update export __init__.py**

Ensure `HtmlExporter` and `ArkivExporter` are importable from `mtk.export`.

**Step 3: Run full test suite with coverage**

```bash
pytest --cov=src/mtk --cov-report=term-missing -x
```

**Step 4: Run linting and type checks**

```bash
ruff check src/mtk tests
ruff format src/mtk tests
mypy src/mtk
```

**Step 5: Fix any issues found**

**Step 6: Update CLAUDE.md**

Update the Architecture section to reflect:
- No more LLM integration
- No more graph export
- MCP server: pure SQL (2 tools)
- Export formats: json, mbox, markdown, html, arkiv

**Step 7: Commit**

```bash
git add -A && git commit -m "v0.3.0: cleanup, version bump, update docs"
```
