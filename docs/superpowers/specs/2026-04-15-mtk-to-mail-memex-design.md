# mtk to mail-memex: Rename and Contract Compliance

**Date**: 2026-04-15
**Scope**: Package rename, MCP modernization, soft delete, marginalia, static HTML SPA
**Status**: Approved design

---

## 1. Package Rename

### Naming

| Aspect | Old | New |
|--------|-----|-----|
| PyPI package | `mtk` | `mail-memex` |
| Python import | `mtk` | `mail_memex` |
| CLI entrypoint | `mtk` | `mail-memex` |
| MCP server name | `Server("mtk")` | `FastMCP("mail-memex")` |
| URI prefix | `mtk://` | `mail-memex://` |
| Config dir | `~/.config/mtk/` | `~/.config/mail-memex/` |
| Data dir | `~/.local/share/mtk/` | `~/.local/share/mail-memex/` |
| Database file | `mtk.db` | `mail-memex.db` |
| Env var | `MTK_DATABASE_PATH` | `MAIL_MEMEX_DATABASE_PATH` |

### Directory Structure

`src/mtk/` becomes `src/mail_memex/` with the same internal layout:

```
src/mail_memex/
  __init__.py
  core/          (models, database, config)
  importers/     (base, parser, mbox, eml, gmail)
  search/        (engine, fts)
  export/        (json, mbox, markdown, html, arkiv)
  mcp/           (server, __init__, __main__)
  imap/          (auth, connection, pull, gmail, mapping, account)
  cli/           (main, imap_cli)
```

### pyproject.toml

```toml
[project]
name = "mail-memex"

[project.scripts]
mail-memex = "mail_memex.cli.main:app"

[tool.hatch.build.targets.wheel]
packages = ["src/mail_memex"]
```

### No backward compatibility

- No `mtk` CLI alias.
- No fallback to old config/data paths.
- Old `mtk` package remains on PyPI, unsupported.
- Clean break. No tech debt.

### Migration

A standalone `scripts/migrate-from-mtk.py` (not installed with the package) that:

1. Copies `~/.config/mtk/config.yaml` to `~/.config/mail-memex/config.yaml`
2. Copies `~/.local/share/mtk/mtk.db` to `~/.local/share/mail-memex/mail-memex.db`
3. Updates `db_path` in the new config file
4. Prints what it did

Not part of the package. Not auto-discovered. Users run it manually if needed. Will be removed from the repo eventually.

---

## 2. MCP Modernization

### SDK Upgrade

Replace `mcp.server.Server` with `FastMCP("mail-memex")` to match btk, ptk, ebk.

### Contract Tools

Three tools required by the memex architecture contract:

**`execute_sql(sql: str, readonly: bool = True) -> str`**
- Replaces the old `run_sql`. Same behavior: DDL always blocked, writes blocked when `readonly=True`.
- Returns JSON array of row objects for SELECT, `{"affected_rows": N}` for writes, `{"error": "..."}` on failure.

**`get_schema() -> str`**
- Unchanged behavior. Returns JSON with table DDL, column metadata, human-readable descriptions, query tips.
- Updated to document new tables (marginalia, marginalia_targets) and `archived_at` convention.

**`get_record(kind: str, id: str) -> str`**
- Resolves cross-archive URIs: `mail-memex://email/{id}` and `mail-memex://thread/{id}` and `mail-memex://marginalia/{uuid}`.
- `kind` is one of: `email`, `thread`, `marginalia`.
- `id` is the `message_id` for emails, `thread_id` for threads, `uuid` for marginalia.
- Returns the full record as JSON (including `archived_at` if soft-deleted), or `{"error": "NOT_FOUND"}` for truly missing records. Soft-deleted records are still resolvable so trail steps don't break.

### Domain Tools

**`search_emails(query: str, limit: int = 50) -> str`**
- Wraps the existing SearchEngine with Gmail-like query operators (from:, to:, subject:, after:, before:, tag:, has:attachment, thread:).
- Returns JSON array of results with scores and highlights.

**`add_marginalia(target_uris: list[str], content: str, category: str | None, color: str | None, pinned: bool = False) -> str`**
- Create a marginalia record attached to one or more `mail-memex://` URIs.
- Generates a UUID. Returns the created record as JSON.

**`list_marginalia(target_uri: str | None, include_archived: bool = False, limit: int = 50) -> str`**
- List marginalia, optionally filtered by target URI.
- Excludes soft-deleted records by default.

**`get_marginalia(uuid: str) -> str`**
- Fetch a single marginalia record by UUID, including its target URIs.

**`update_marginalia(uuid: str, content: str | None, category: str | None, color: str | None, pinned: bool | None) -> str`**
- Update fields on existing marginalia. Only provided fields are changed.

**`delete_marginalia(uuid: str, hard: bool = False) -> str`**
- Soft delete by default (sets `archived_at`). Hard delete if `hard=True`.

**`restore_marginalia(uuid: str) -> str`**
- Clear `archived_at` on a soft-deleted marginalia record.

### Entry Points

- `python -m mail_memex.mcp` (module entry point)
- `mail-memex mcp` (CLI command)
- Both use stdio transport.

---

## 3. Schema Changes

### `archived_at` on Existing Tables

Add to `Email` and `Thread` models:

```python
archived_at: Mapped[datetime | None] = mapped_column(default=None)
```

- Default queries filter `WHERE archived_at IS NULL`.
- SearchEngine, CLI commands, and MCP tools all respect this filter.
- Tags, attachments, and imap_sync_state do NOT get `archived_at` (they are not independently addressable records with their own URIs).

### New: `marginalia` Table

```sql
CREATE TABLE marginalia (
    id          INTEGER PRIMARY KEY,
    uuid        VARCHAR(36) UNIQUE NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(100),
    color       VARCHAR(20),
    pinned      BOOLEAN DEFAULT FALSE,
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL,
    archived_at DATETIME
);
```

### New: `marginalia_targets` Join Table

```sql
CREATE TABLE marginalia_targets (
    marginalia_id  INTEGER NOT NULL REFERENCES marginalia(id) ON DELETE CASCADE,
    target_uri     TEXT NOT NULL,
    PRIMARY KEY (marginalia_id, target_uri)
);
```

- URIs are plain strings, no FK enforcement.
- A marginalia record survives even if its target email/thread is deleted (orphan survival per the ecosystem contract).
- Marginalia URI: `mail-memex://marginalia/{uuid}`

### FTS5

No schema change to `emails_fts`. The `archived_at` filter happens at the query layer: join FTS results against `emails WHERE archived_at IS NULL`.

---

## 4. Static HTML SPA Generator

### Output

Single `.html` file with embedded base64-encoded SQLite database. Uses sql.js (WASM) from CDN for client-side querying.

### Embedded Database Schema

A simplified export schema (not the full ORM):

```sql
CREATE TABLE emails (
    id INTEGER PRIMARY KEY,
    message_id TEXT,
    from_addr TEXT,
    from_name TEXT,
    to_addrs TEXT,
    subject TEXT,
    date TEXT,
    body_text TEXT,
    body_preview TEXT,
    thread_id TEXT,
    tags_json TEXT    -- denormalized JSON array of tag names
);

CREATE TABLE threads (
    thread_id TEXT PRIMARY KEY,
    subject TEXT,
    email_count INTEGER,
    first_date TEXT,
    last_date TEXT
);

-- FTS5 rebuilt in the embedded DB
CREATE VIRTUAL TABLE emails_fts USING fts5(
    email_id UNINDEXED, subject, body_text, from_addr, from_name,
    tokenize='porter unicode61'
);
```

Tags are denormalized into `tags_json` on each email. Attachments, IMAP state, marginalia, and archived records are excluded.

### UI Features

- Inbox-style list view (date, from, subject, preview)
- Thread grouping toggle
- Search bar wired to FTS5 via sql.js
- Tag filter sidebar
- Email detail view (body text, metadata)
- Sort by date or relevance

### CLI

`mail-memex export html output.html [--query "..."]`

The `--query` flag filters which emails to include using the standard search operators.

### Implementation

A builder module (`export/html_builder.py`) creates the in-memory SQLite export DB, and a template module (`export/html_template.py`) generates the HTML+CSS+JS. Following btk's pattern.

---

## 5. CLI Surface

### Top-level Commands

- `mail-memex init [--db PATH] [--force]`
- `mail-memex search <query> [--limit N] [--json]`
- `mail-memex mcp [--transport stdio]`

### Sub-command Groups

- `mail-memex import {mbox,eml,gmail}`
- `mail-memex export {json,mbox,markdown,html,arkiv}`
- `mail-memex tag {add,remove,list,batch}`
- `mail-memex rebuild {index,threads}`
- `mail-memex imap {accounts,sync,folders,test}`

### Conventions

- `--json` flag on all commands for programmatic output.
- No marginalia CLI commands. Marginalia is MCP-only.
- No migration command. Migration is a standalone script.

---

## 6. Files Changed

### Renamed/Moved

- `src/mtk/` -> `src/mail_memex/` (entire package tree)
- All internal imports: `from mtk.` -> `from mail_memex.`

### Modified

- `pyproject.toml`: package name, scripts, wheel target, version bump
- `CLAUDE.md`: full rewrite for new names
- `README.md`: update all references
- `src/mail_memex/core/models.py`: add `archived_at` to Email and Thread, add Marginalia and MarginaliaTarget models
- `src/mail_memex/core/config.py`: new default paths (`~/.config/mail-memex/`, `~/.local/share/mail-memex/`)
- `src/mail_memex/mcp/server.py`: rewrite with FastMCP, rename to `execute_sql`, add `get_record`, `search_emails`, and marginalia tools
- `src/mail_memex/search/engine.py`: respect `archived_at IS NULL` filter
- `src/mail_memex/search/fts.py`: filter archived emails when joining FTS results
- `src/mail_memex/export/arkiv_export.py`: URI scheme `mail-memex://email/...`
- `src/mail_memex/export/html_builder.py`: new, builds embedded SQLite export DB
- `src/mail_memex/export/html_template.py`: new, generates single-file HTML SPA
- `src/mail_memex/export/html_export.py`: rewrite to use builder+template pattern
- All test files: update imports, add tests for marginalia, soft delete, new MCP tools

### New Files

- `src/mail_memex/core/marginalia.py`: Marginalia CRUD operations (used by MCP tools)
- `src/mail_memex/export/html_builder.py`: builds in-memory SQLite export database
- `src/mail_memex/export/html_template.py`: HTML+CSS+JS template for SPA
- `scripts/migrate-from-mtk.py`: standalone migration script
- `tests/test_marginalia.py`: marginalia model and CRUD tests
- `tests/test_soft_delete.py`: archived_at filtering tests

---

## 7. What This Design Does NOT Include

- No embedding computation (federation layer responsibility).
- No cross-archive trails (meta-memex responsibility).
- No `ask_email` LLM tool (optional contract item, deferred).
- No CLI simplification (user considering but not ready).
- No auto-discovery for exporters (YAGNI for five stable formats).
