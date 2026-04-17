# mail-memex

Personal email archive with SQLite+FTS5 full-text search and an MCP server for LLM access.

Part of the [`*-memex` ecosystem](../memex) of co-located personal archives: `llm-memex` (AI conversations), `bookmark-memex`, `photo-memex`, `book-memex`, `hugo-memex`, and `health-memex`.

## What It Does

- **Ingests** email from Gmail Takeout, mbox files, `.eml` files, or IMAP (Gmail OAuth2 or any IMAP server).
- **Indexes** every message with SQLite FTS5 for ranked full-text search (Porter stemming, BM25 weighting).
- **Rebuilds threads** from `In-Reply-To` / `References` headers.
- **Exposes** the archive as an MCP server so LLMs can query it with SQL or Gmail-style operators.
- **Exports** to JSON, mbox, markdown, a single-file HTML SPA (embedded SQLite via sql.js), or arkiv JSONL for cross-archive ingestion.
- **Marginalia:** attach free-form notes to any email, thread, or marginalia itself, addressable by durable URIs that survive re-imports.
- **Soft-deletes:** archived emails and threads stay queryable but are filtered from default results, so MCP trails and marginalia references don't break.

mail-memex is **not an email client**. It does not send or reply to mail. It's a long-term archive and an LLM-queryable surface on top of your mail history.

## Install

```bash
pip install -e ".[mcp,imap,imap-oauth]"
```

Optional extras: `mcp` (MCP server), `imap` (IMAP pull with keyring), `imap-oauth` (Gmail OAuth2).

## Quick Start

```bash
# 1. Initialize
mail-memex init

# 2. Import from Gmail Takeout, mbox, or eml
mail-memex import gmail ~/takeout/"All mail Including Spam and Trash.mbox"
mail-memex import mbox archive.mbox
mail-memex import eml ~/Mail/

# 3. Search
mail-memex search "project proposal"
mail-memex search "from:alice after:2024-01-01 has:attachment"

# 4. Start MCP server (LLM-facing)
mail-memex mcp
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `mail-memex init` | Create the database |
| `mail-memex import {mbox,eml,gmail}` | Import from a source |
| `mail-memex search QUERY` | Search (FTS5 ranked, with Gmail-style operators) |
| `mail-memex tag {add,remove,list,batch}` | Manage tags |
| `mail-memex rebuild {index,threads}` | Rebuild FTS index or threads |
| `mail-memex export {json,mbox,markdown,html,arkiv}` | Export the archive |
| `mail-memex imap {accounts,sync,folders,test}` | IMAP incremental sync |
| `mail-memex mcp` | Start the stdio MCP server |

All commands accept `--json` for machine-readable output.

### Search Operators

Gmail-style query language:

```
from:alice              # sender address contains "alice"
to:bob@example.com      # any recipient field contains "bob@..."
subject:proposal        # subject contains "proposal"
after:2024-01-01        # date on or after
before:2024-12-31       # date on or before
tag:work                # has the tag "work"
-tag:archive            # does NOT have the tag "archive"
has:attachment          # has at least one attachment
thread:<thread-id>      # in a specific thread
```

Free-text terms are matched against subject and body via FTS5.

## MCP Server

The MCP server is the primary interface for LLM access. Configure it in `~/.claude.json` (or any MCP client config):

```json
{
  "mcpServers": {
    "mail-memex": {
      "type": "stdio",
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "mail_memex.mcp"]
    }
  }
}
```

### Exposed Tools

**Contract tools** (shared across the `*-memex` ecosystem):

| Tool | Purpose |
|------|---------|
| `get_schema` | Return DDL, column metadata, descriptions, and query tips |
| `execute_sql(sql, readonly=true)` | Run SQL. DDL always blocked; writes blocked by default. |
| `get_record(kind, record_id)` | Resolve a `mail-memex://` URI. `kind` is one of `email`, `thread`, `marginalia`. Returns soft-deleted records too, so trail steps don't break. |

**Domain tools:**

| Tool | Purpose |
|------|---------|
| `search_emails(query, limit)` | Gmail-style search, BM25 ranked |
| `create_marginalia(target_uris, content, category?, color?, pinned?)` | Attach a note to one or more URIs |
| `list_marginalia(target_uri?, include_archived?, limit?)` | List notes |
| `get_marginalia(uuid)` | Fetch a note by UUID |
| `update_marginalia(uuid, ...)` | Update fields |
| `delete_marginalia(uuid, hard?)` | Soft delete by default; `hard=true` for permanent removal |
| `restore_marginalia(uuid)` | Undo a soft delete |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│     Sources: mbox, eml, Gmail Takeout, IMAP (incl. OAuth2)  │
└────────────────────────────┬────────────────────────────────┘
                             ▼
              ┌──────────────────────────────┐
              │   Importers (idempotent      │
              │   dedup by Message-ID)       │
              └──────────────┬───────────────┘
                             ▼
┌────────────────────────────────────────────────────────────┐
│                     SQLite + FTS5                           │
│                                                             │
│  emails, threads, tags, attachments, imap_sync_state,       │
│  marginalia, marginalia_targets, emails_fts (Porter+BM25)  │
│                                                             │
│  Soft delete via archived_at on emails, threads, marginalia│
└──────┬────────────────────────────────────┬─────────────────┘
       │                                    │
       ▼                                    ▼
┌──────────────────────┐         ┌─────────────────────────┐
│  MCP Server          │         │   Exporters             │
│  (FastMCP, stdio)    │         │                         │
│                      │         │   JSON, mbox, markdown, │
│  execute_sql         │         │   HTML SPA (sql.js),    │
│  get_schema          │         │   arkiv JSONL+schema    │
│  get_record          │         │                         │
│  search_emails       │         │   Cross-archive URIs:   │
│  marginalia CRUD     │         │   mail-memex://...      │
└──────────────────────┘         └─────────────────────────┘
```

### URI Scheme

Records are addressable by URI, making cross-archive references durable:

```
mail-memex://email/<message_id>
mail-memex://thread/<thread_id>
mail-memex://marginalia/<uuid>
```

A trail in `meta-memex` (or a note anywhere else) can reference these as plain strings. The archive resolves them via `get_record`.

### Database ID Scheme

- **`message_id`** (RFC 2822 Message-ID header) is the durable, external identifier used for deduplication and URIs. If a message arrives without one, mail-memex generates `generated-{sha256[:32]}@mail-memex.local` deterministically.
- **`id`** (auto-increment integer) is the internal primary key used for FK relationships and FTS5 joins. Do not expose this outside the database.

### FTS5 Details

- Tokenizer: `porter unicode61` (English stemming + Unicode segmentation).
- BM25 column weights: `subject=10.0, body_text=1.0, from_addr=5.0, from_name=5.0`.
- Triggers keep `emails_fts` in sync with `emails` automatically on INSERT/UPDATE/DELETE.
- Falls back to SQL `LIKE` matching if FTS5 is unavailable.

### Thread Reconstruction

Threads are rebuilt from `In-Reply-To` headers (only). The algorithm:

1. Find emails with `thread_id IS NULL` that have `in_reply_to`.
2. Look up the parent by `message_id`.
3. If the parent has a thread, join it. Otherwise, create `thread-{parent.message_id}` and assign both.
4. Loop until no new threads are created (handles deep chains).

Runs automatically after every import, and can be re-run with `mail-memex rebuild threads`.

## Paths

- **Config:** `~/.config/mail-memex/config.yaml`
- **Database:** `~/.local/share/mail-memex/mail-memex.db`
- **Env var:** `MAIL_MEMEX_DATABASE_PATH` overrides the database path.

## Data Model

Core tables (all carry `archived_at TIMESTAMP NULL` where applicable):

- **emails**: headers (to/cc/bcc as comma-separated strings), body text/html, preview, thread_id, raw headers in `metadata_json` for custom field queries.
- **threads**: thread_id, subject, first/last date, email count.
- **tags**: name (unique), source (`mail-memex` or `imap`).
- **attachments**: filename, content type, size. Content is not stored (retrieve from the source file).
- **marginalia**: uuid, content, category, color, pinned. Free-form notes.
- **marginalia_targets**: many-to-many join from marginalia to target URIs (strings, no FK).
- **imap_sync_state**: per-account, per-folder UIDVALIDITY and last_uid for incremental sync.
- **emails_fts**: FTS5 virtual table.

## Design Principles

- **Contract compliance.** Satisfies the `*-memex` archive contract: SQLite+FTS5, MCP server with `execute_sql`/`get_schema`/`get_record`, arkiv export, soft delete, marginalia, durable URIs.
- **Thin admin CLI.** Use the CLI for import, export, and housekeeping. Use the MCP server for interactive query. Marginalia is MCP-only.
- **No embeddings here.** Archives stay narrow. The federation layer (`meta-memex`, soon to be renamed to `memex`) computes embeddings and maintains cross-archive trails.
- **Re-importable.** Dedup by Message-ID, so running the same import twice is a no-op.

## Status

v0.6.0 (alpha). Active development. The archive shape is stable but the CLI surface may still evolve.

## License

MIT
