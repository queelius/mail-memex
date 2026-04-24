# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mail-memex is a personal email archive with full-text search and SQL/MCP access. It stores email from mbox files, eml files, and IMAP accounts into a SQLite database with FTS5 indexing. LLM interaction happens via an MCP server built on FastMCP, exposing contract tools (execute_sql, get_schema, get_record) and domain tools (search_emails, marginalia CRUD).

Part of the *-memex personal archive ecosystem alongside llm-memex (AI conversations), bookmark-memex (bookmarks), photo-memex (photos), book-memex (ebooks), and hugo-memex (static site content). The federation layer is `memex/` (design phase).

## Development Commands

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run specific test file
pytest tests/test_search.py

# Run a single test by node ID
pytest tests/test_search.py::test_search_from_operator
pytest tests/test_marginalia.py -k "orphan_survival"

# Run with coverage
pytest --cov=src/mail_memex --cov-report=term-missing

# Type checking
mypy src/mail_memex

# Linting
ruff check src/mail_memex tests
ruff format src/mail_memex tests
```

## Paths and Configuration

- Config file: `~/.config/mail-memex/config.yaml`
- Database: `~/.local/share/mail-memex/mail-memex.db`
- Env var override: `MAIL_MEMEX_DATABASE_PATH` sets the database path at runtime
- CLI entrypoint: `mail-memex` (no `mtk` alias)

## Architecture

### Core Layer (`src/mail_memex/core/`)
- `models.py` - SQLAlchemy ORM models: Email, Thread, Tag, Attachment, ImapSyncState, Marginalia, MarginaliaTarget
- `database.py` - Database session management. SQLite with WAL mode and foreign keys enabled.
- `config.py` - MailMemexConfig for YAML-based configuration with IMAP account support
- `marginalia.py` - Marginalia CRUD operations (create, list, get, update, soft-delete, restore, purge)

### Importers (`src/mail_memex/importers/`)
- `base.py` - BaseImporter abstract class with `discover()`, `parse()`, `import_all()` methods
- `parser.py` - EmailParser for RFC 2822 parsing, returns ParsedEmail dataclass
- `mbox.py`, `eml.py` - Format-specific importers (MboxImporter, EmlImporter)

Import is idempotent: message_id is the dedup key. Re-importing an already-present email updates fields but does not create a duplicate row. The integer `id` column is used for FK relationships internally; message_id is the durable external identifier used in URIs.

### Search (`src/mail_memex/search/`)
- `engine.py` - SearchEngine with FTS5 full-text search and Gmail-like query operators
- `fts.py` - FTS5 virtual table setup, triggers for automatic sync, BM25-scored queries

FTS5 setup details:
- Tokenizer: `porter unicode61` (stemming for English text)
- BM25 column weights: subject=10.0, body_text=1.0, from_addr=5.0, from_name=5.0
- Triggers on INSERT, UPDATE, DELETE keep `emails_fts` in sync with `emails`
- Fallback: if FTS5 is unavailable, SearchEngine falls back to LIKE-based keyword search

Thread reconstruction algorithm: emails are grouped by In-Reply-To and References headers into thread chains. When a thread_id is absent, the importer assigns one by walking the References chain to find the root message_id, then using that as the thread_id string.

Supported query operators: `from:`, `to:`, `subject:`, `after:`, `before:`, `tag:`, `-tag:`, `has:attachment`, `thread:`. Bare terms search subject and body via FTS5.

### Export (`src/mail_memex/export/`)
- `base.py` - Exporter (ABC), ExportResult, and `_email_to_dict` helper
- `json_export.py`, `mbox_export.py`, `markdown_export.py` - Format-specific exporters
- `html_export.py` - HtmlExporter: generates a self-contained HTML Single File Application with the SQLite database embedded (sql.js loaded from CDN). No server required; open in any browser.
- `arkiv_export.py` - ArkivExporter: exports to arkiv JSONL format with schema.yaml generation

### MCP Server (`src/mail_memex/mcp/`)
- `server.py` - FastMCP-based server with contract tools and domain tools
- `__init__.py` - stdio transport entry point
- `__main__.py` - `python -m mail_memex.mcp` entry point

MCP tools:
- `execute_sql(sql, readonly=True)` - Execute SQL against the database. DDL always blocked. Writes require `readonly=False`.
- `get_schema()` - Returns full database schema as JSON with table descriptions, column info, and query tips.
- `get_record(uri)` - Resolve a `mail-memex://` URI and return the record as JSON.
- `search_emails(query, limit=20)` - Search emails using Gmail-like query operators. Returns ranked results.
- `marginalia_create(target_uris, content, kind)` - Attach a note to one or more record URIs.
- `marginalia_list(target_uri)` - List all live marginalia for a URI.
- `marginalia_get(uuid)` - Get a single marginalia record by UUID.
- `marginalia_update(uuid, content)` - Update marginalia content.
- `marginalia_delete(uuid)` - Soft-delete marginalia (sets archived_at).
- `marginalia_restore(uuid)` - Restore soft-deleted marginalia.
- `marginalia_purge(uuid)` - Hard-delete marginalia (irreversible).

Configure in `.mcp.json`:
```json
{"mcpServers": {"mail-memex": {"command": "python", "args": ["-m", "mail_memex.mcp"]}}}
```
Set `MAIL_MEMEX_DATABASE_PATH` env var to override the database location.

### IMAP Pull (`src/mail_memex/imap/`)
- `auth.py` - AuthManager for password/OAuth2 credential storage (keyring-backed)
- `connection.py` - ImapConnection context manager
- `pull.py` - Incremental IMAP fetch with UID tracking. Parses To/Cc/Bcc headers. Stores last_uid and uid_validity in imap_sync_state for resumable sync.
- `gmail.py` - Gmail-specific label mapping to tags
- `mapping.py` - IMAP flag to mail-memex tag mapping

### CLI (`src/mail_memex/cli/`)
- `main.py` - Typer app with commands: search, init, mcp
- Sub-apps: `import` (mbox, eml, gmail), `export` (json, mbox, markdown, html, arkiv), `tag` (add, remove, list, batch), `rebuild` (index, threads, recipients)
- `imap_cli.py` - IMAP sub-commands (accounts, sync, folders, test)

## Key Patterns

### Database Sessions
Use context manager for sessions (auto-commit on success, rollback on error):
```python
db = Database(path)
with db.session() as session:
    session.add(email)
    # commits automatically
```

### Soft Delete
Every record table (emails, threads, marginalia) carries `archived_at TIMESTAMP NULL`. Live records have `archived_at IS NULL`. Default queries filter on this. Soft delete sets `archived_at` to the current UTC timestamp. Hard delete is opt-in via an explicit flag or `marginalia_purge`.

This preserves URIs referenced by marginalia and cross-archive trails until the user explicitly purges.

### Marginalia
Marginalia are free-form notes attachable to any record via URI. They are MCP-only (no CLI commands). Each marginalia record has a UUID as its durable identifier. The `marginalia_targets` join table links one marginalia entry to one or more target URIs.

Marginalia survive their target being soft-deleted (orphan survival). Hard-deleting a target does not cascade to marginalia; they remain as orphaned notes queryable by UUID.

### URI Scheme
Records are addressed as `mail-memex://<kind>/<id>`:
- `mail-memex://email/<message_id>` - An email by RFC 2822 Message-ID
- `mail-memex://thread/<thread_id>` - A thread by thread_id string
- `mail-memex://marginalia/<uuid>` - A marginalia entry by UUID

Positions within a record use URI fragments (not separate kinds):
- `mail-memex://email/<message_id>#part=2` - A MIME part within an email

### JSON Output
All CLI commands support `--json` flag for programmatic use. Output is valid JSON to stdout.

### Address Storage
`to_addrs`, `cc_addrs`, and `bcc_addrs` on `emails` are comma-separated strings in a single Text column, kept for display compatibility.

For searches like `to:alice`, the search engine JOINs against the `email_recipients` side table: one row per `(email_id, addr, name, kind)` with an index on `addr` and a composite index on `(kind, addr)`. Ingestion (file importers and IMAP pull) populates this table at the same time as the CSV columns. For databases imported before this schema, run `mail-memex rebuild recipients` once to backfill from the CSV columns. The command is idempotent.

## Testing

Tests use pytest with fixtures defined in `tests/conftest.py`:
- `db` - In-memory SQLite database with schema and FTS5 initialized
- `session` - Database session from in-memory db
- `populated_db` - Database with sample data (5 emails, 2 threads, 4 tags)
- `sample_mbox`, `sample_eml_dir` - File system fixtures for import testing
- `email_factory` - Factory fixture for creating test email records with defaults
- `isolated_mail_memex_config` - Redirects MailMemexConfig default dirs to a tmp directory. Use for CLI tests that invoke `mail-memex init` or any command that would write to `~/.config/mail-memex/`.

## Optional Dependencies

- `mcp` extra: fastmcp for the stdio MCP server
- `imap` extra: imapclient + keyring for IMAP pull
- `imap-oauth` extra: google-auth-oauthlib for Gmail OAuth2

## Migration

To migrate from old mtk paths to mail-memex paths, run:
```bash
python scripts/migrate-from-mtk.py --dry-run   # preview
python scripts/migrate-from-mtk.py             # execute
```

The script copies `~/.config/mtk/config.yaml` to `~/.config/mail-memex/config.yaml` and `~/.local/share/mtk/mtk.db` to `~/.local/share/mail-memex/mail-memex.db`, including WAL/SHM sidecars. It also rewrites `db_path` references in the new config file.
