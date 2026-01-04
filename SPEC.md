# mtk (Mail Toolkit) Specification

**Version**: 0.2.0-draft
**License**: MIT/Apache-2.0 (permissive, dual-licensed)
**Status**: Open source project, accepting contributions

---

## Vision

mtk is a personal email archive management toolkit for power users who want deep control over their email history. It provides backup/preservation, semantic search, relationship mapping, privacy controls, and LLM-powered analysis—all from the command line, designed for integration with AI assistants like Claude Code.

**mtk is NOT an email client.** It does not send emails. Email sending is a solved problem (mutt, neomutt, aerc, himalaya, your existing client). mtk focuses on what's unique: **archive intelligence + LLM integration**.

---

## Design Principles

1. **Unix Philosophy**: Do one thing well, composable with other tools
2. **Data Ownership**: User owns their data, portable formats, no lock-in
3. **Progressive Disclosure**: Simple defaults, power features opt-in
4. **Convention over Configuration**: Sensible defaults, minimal required config
5. **CLI-Native**: Terminal is the primary interface, optimized for AI assistant integration

---

## Architecture

```
Email Sources (Maildir/mbox/EML/Gmail Takeout)
              │
              ▼
       ┌──────────────┐
       │ Email Parser │──────┐
       └──────────────┘      │
              │              ▼
              │      ┌───────────────────┐
              │      │ Attachment Extractor │
              │      │ (PDF, Office, text)  │
              │      └───────────────────┘
              ▼
       ┌──────────────┐     ┌─────────────────┐
       │   Importer   │────▶│ Person Resolver │
       └──────────────┘     └─────────────────┘
              │
              ▼
       ┌──────────────────────────────────────┐
       │         SQLite Database              │
       │  • Emails       • Attachments        │
       │  • People       • Hierarchical Tags  │
       │  • Threads      • Embeddings         │
       └──────────────────────────────────────┘
              │
     ┌────────┴────────┬─────────────┐
     ▼                 ▼             ▼
┌──────────┐    ┌───────────┐  ┌──────────┐
│  Search  │    │  Privacy  │  │   LLM    │
│  Engine  │    │  Filter   │  │ Classify │
└──────────┘    └───────────┘  └──────────┘
     │                 │             │
     └────────┬────────┴─────────────┘
              ▼
       ┌──────────────┐     ┌─────────────────┐
       │     CLI      │     │    Exporters    │
       │  (primary)   │     │ (JSON/mbox/md)  │
       └──────────────┘     └─────────────────┘
              │
              ▼
       ┌──────────────┐
       │  MCP Server  │
       │  (read-only) │
       └──────────────┘
```

---

## Core Decisions

### Identity & Person Resolution

- **Model**: Separate identities with aliases relationship
- Each unique email address is a distinct identity
- Identities can be linked via explicit `alias` relationship
- No automatic merging across domains
- Manual linking: `mtk people link <id1> <id2>`

### Privacy

- **Timing**: Export-time only (preserve originals in DB)
- Privacy rules filter/redact only when exporting
- Original content always preserved for user access
- Configurable in `~/.config/mtk/privacy.yaml`

### Search

- **Ranking**: Keyword primary, semantic fills gaps
- Exact keyword matches always rank highest
- Semantic search supplements when keyword results are sparse
- Explicit `--semantic` flag for semantic-only mode

### Multi-Account Support

- **Model**: Unified database with hierarchical tag namespacing
- Single SQLite database for all accounts
- Accounts distinguished via hierarchical tags: `account/work`, `account/personal`
- Query syntax: `tag:account/work from:alice`

### Hierarchical Tags

- **Format**: Slash-delimited paths stored flat
- Examples: `account/work`, `project/mtk`, `type/newsletter`
- Parsed as hierarchy for UI/queries, stored as flat strings
- Supports arbitrary depth: `account/work/team/engineering`

### Threading

- **Algorithm**: Strict header-based with manual linking
- Uses In-Reply-To and References headers only
- No heuristic matching by subject/time
- Manual repair: `mtk thread link <email1> <email2>`

### Scale Target

- **Capacity**: 500K - 5M emails (researcher scale)
- Complex queries acceptable up to 5 seconds
- Overnight indexing acceptable for initial import
- Performance strategy: slow import, fast query (pre-compute at import)

### Source of Truth

- **Primary**: mtk database
- **Secondary**: notmuch (for compatibility with other tools)
- Tags push from mtk to notmuch, not vice versa
- notmuch integration optional

---

## Data Model

### Email
| Field | Type | Description |
|-------|------|-------------|
| message_id | string (unique) | RFC 2822 Message-ID |
| account | string | Account namespace (from import path/config) |
| thread_id | string (FK) | Links to Thread |
| from_addr | string | Sender email |
| from_name | string | Sender display name |
| subject | string | Email subject |
| date | datetime | Send date (stored as UTC) |
| in_reply_to | string | Parent message ID |
| references | string | Thread reference chain |
| body_text | text | Plain text content |
| body_html | text | HTML content (preserved) |
| body_markdown | text | HTML converted to Markdown |
| body_preview | string | First 500 chars |
| embedding | bytes | Vector for semantic search |
| file_path | string | Source file location |
| sender_id | int (FK) | Links to Person |

### Person
| Field | Type | Description |
|-------|------|-------------|
| name | string | Display name |
| primary_email | string | Canonical email address |
| relationship_type | string | family/friend/colleague/etc |
| email_count | int | Total emails |
| first_contact | datetime | First email date |
| last_contact | datetime | Most recent email |
| notes | text | User notes |

### PersonAlias
| Field | Type | Description |
|-------|------|-------------|
| person_id | int (FK) | Primary person |
| alias_email | string | Alternative email address |
| alias_name | string | Name used with this address |

### Thread
| Field | Type | Description |
|-------|------|-------------|
| thread_id | string (unique) | Thread identifier |
| subject | string | Thread subject |
| email_count | int | Messages in thread |
| first_date | datetime | Thread start |
| last_date | datetime | Most recent message |

### Tag (Hierarchical)
| Field | Type | Description |
|-------|------|-------------|
| name | string (unique) | Full path: `account/work` |
| source | string | "mtk", "notmuch", "llm", "import" |

### Attachment
| Field | Type | Description |
|-------|------|-------------|
| email_id | int (FK) | Parent email |
| filename | string | File name |
| content_type | string | MIME type |
| size | int | Size in bytes |
| content_id | string | For inline images |
| content_hash | string | SHA256 for dedup |
| extracted_text | text | Full-text content (if extractable) |
| storage_path | string | Path to extracted content |

---

## CLI Commands

### Core Operations
```bash
mtk init [path]              # Initialize database, optionally import
mtk inbox                    # Show recent emails
mtk show <id>                # Display email with full context
mtk thread <id>              # Show conversation thread
mtk reply <id>               # Prepare reply context for external tools
mtk search <query>           # Search emails
mtk stats                    # Archive statistics
mtk shell                    # Interactive REPL mode
mtk backup                   # Create timestamped database backup
```

Note: `mtk reply` outputs context (headers, thread history) for external email clients or LLM composition - it does NOT send emails.

### Import (`mtk import`)
```bash
mtk import maildir <path> [--account NAME]
mtk import mbox <path> [--account NAME]
mtk import eml <path> [--account NAME]
mtk import gmail <path> [--account NAME]
```

All imports:
- Support `--json` for structured output
- Auto-detect format when ambiguous
- Skip corrupt emails, log to error file
- Support `--account` to set account namespace tag

### Export (`mtk export`)
```bash
mtk export json <output> [--query Q] [--privacy]
mtk export mbox <output> [--query Q] [--privacy]
mtk export markdown <output> [--query Q] [--privacy] [--threads]
```

### Tagging
```bash
mtk tag <id> --add account/work --add project/mtk
mtk tag <id> --remove type/newsletter
mtk tag-batch <query> --add TAG [--dry-run]
mtk list-tags [--tree]       # Show hierarchy
```

### People (`mtk people`)
```bash
mtk people list [--limit N]
mtk people show <id>
mtk people link <id1> <id2>  # Create alias relationship
mtk people unlink <id1> <id2>
mtk graph [--format gexf|json|graphml]
```

### Privacy (`mtk privacy`)
```bash
mtk privacy check [--query Q]  # Preview what would be filtered
mtk privacy rules              # Show active rules
```

### LLM (`mtk llm`)
```bash
mtk llm status                 # Check Ollama availability
mtk llm classify <id> [--categories C1,C2,C3]
mtk llm summarize <id>
mtk llm actions <id>           # Extract action items
mtk llm classify-batch <query> [--apply-tags] [--fail-fast]
```

Default behavior: skip failures, continue batch
With `--fail-fast`: stop on first error

### notmuch (`mtk notmuch`)
```bash
mtk notmuch status
mtk notmuch push              # Push mtk tags to notmuch
mtk notmuch import [--query]  # Import emails from notmuch
```

mtk is primary, notmuch is backup sync target.

### Attachments (`mtk attachments`)
```bash
mtk attachments list <email-id>
mtk attachments extract <email-id> [--output DIR]
mtk attachments search <query>  # Search extracted text
mtk attachments index           # Re-index all attachments
```

Supported extraction:
- PDF (pdftotext or pdfminer)
- Office documents (docx, xlsx, pptx via python-docx, openpyxl)
- Plain text, Markdown, code files
- Best-effort on others, graceful fallback

---

## Search Query Syntax

Gmail-like operators:
```
from:alice@example.com       # Sender
to:bob@example.com           # Recipient
subject:quarterly            # Subject contains
body:important               # Body contains
after:2024-01-01             # Date range
before:2024-12-31
has:attachment               # Has attachments
attachment:pdf               # Attachment type/name
tag:account/work             # Hierarchical tag
-tag:type/spam               # Excludes tag
thread:id                    # Specific thread
account:work                 # Shorthand for tag:account/work
```

Free text searches subject, body, and attachment text.

---

## Configuration

### ~/.config/mtk/config.yaml
```yaml
# Database
db_path: ~/.local/share/mtk/mtk.db

# Email sources (optional, for 'mtk import' without path)
accounts:
  personal:
    maildir: ~/mail/personal
    default: true
  work:
    maildir: ~/mail/work

# Features
generate_embeddings: false   # Requires sentence-transformers
attachment_indexing: true    # Extract and index attachment text

# notmuch (optional)
notmuch_config: ~/.notmuch-config

# Logging
log_level: info              # debug, info, warn, error
log_format: json             # json or text
log_file: ~/.local/share/mtk/mtk.log

# Display
timezone: local              # Display timezone (storage always UTC)
```

### ~/.config/mtk/privacy.yaml
```yaml
exclude:
  addresses:
    - hr@company.com
    - legal@company.com
  tags:
    - private/confidential
  patterns:
    - "CONFIDENTIAL"
    - "attorney-client"

redact:
  patterns:
    - pattern: '\b\d{3}-\d{2}-\d{4}\b'
      replacement: '[SSN REDACTED]'
    - pattern: 'secret-project-\w+'
      replacement: '[PROJECT REDACTED]'
```

---

## Claude Code Integration

### JSON API
All commands support `--json` for structured output:
```bash
mtk stats --json
mtk inbox --json --limit 50
mtk search "from:alice" --json
mtk show <id> --json
```

### Streaming Output
Large results support JSONL streaming:
```bash
mtk search "tag:work" --jsonl  # One JSON object per line
```

### Batch Input
Bulk operations accept JSON input:
```bash
echo '{"ids": ["id1", "id2"], "add_tags": ["reviewed"]}' | mtk tag-batch --stdin
```

### Query Builder
Structured query alternative to string parsing:
```bash
mtk search --from alice --after 2024-01-01 --has-attachment --json
```

### Exit Codes
```
0 = Success
1 = Not found (email, person, etc.)
2 = Invalid input (bad query, missing required arg)
3 = Operation failed (DB error, network error)
4 = Partial success (some items in batch failed)
```

### Thread Context for External Composers
`mtk thread <id> --json` provides context that external email clients or LLM tools can consume:
```json
{
  "thread_id": "...",
  "subject": "Q1 Planning",
  "participants": ["alice@example.com", "bob@example.com"],
  "messages": [
    {
      "id": "...",
      "from": "alice@example.com",
      "date": "2024-01-15T10:00:00Z",
      "body": "..."
    }
  ],
  "latest_message": {
    "id": "...",
    "from": "alice@example.com",
    "subject": "Re: Q1 Planning",
    "in_reply_to": "<msg-id>",
    "references": "<ref-chain>"
  }
}
```

This enables external tools to compose replies with full thread context.

---

## MCP Server Integration

When enabled, mtk exposes read-only MCP resources:

### Resources
- `mtk://emails/{id}` - Individual email
- `mtk://threads/{id}` - Thread with all messages
- `mtk://people/{id}` - Person with correspondence stats
- `mtk://search?q={query}` - Search results

### Tools
- `mtk_search(query, limit)` - Search emails
- `mtk_show(id)` - Get email details
- `mtk_thread(id)` - Get thread
- `mtk_stats()` - Archive statistics

No write operations via MCP for safety (tagging allowed only via CLI).

---

## Integration with Email Clients

mtk is archive-only by design. For sending emails, use an existing client:

### Established CLI Clients
- **mutt/neomutt** - Classic, scriptable, highly configurable
- **aerc** - Modern terminal client with Vi-like keybindings
- **himalaya** - Rust-based, good CLI ergonomics

### LLM-Native Email Sending (Gap Analysis)

There may be an opportunity for a lightweight email CLI specifically designed for Claude Code integration. Current clients predate LLM assistants and weren't designed for:

- **Structured JSON I/O** - Most use curses TUI or expect stdin/stdout piping
- **MCP integration** - No existing client exposes an MCP interface
- **Context handoff** - No standard way to pass conversation context to compose

A hypothetical `llm-mail` or similar could:
```bash
# Compose with context from mtk
mtk thread abc123 --json | llm-mail compose --context

# Send via Claude Code with full audit trail
llm-mail send --to bob@example.com --subject "Re: Project" --body-file reply.md --json

# MCP for Claude Code
llm-mail mcp-server  # Exposes compose/send tools
```

This is explicitly **out of scope for mtk** but could be a complementary project. mtk would provide the archive intelligence; a separate tool would handle composition and sending with LLM-native ergonomics.

---

## Attachment Handling

### Extraction Pipeline
1. On import, detect extractable attachments
2. Extract text content:
   - PDF: pdftotext or pdfminer.six
   - DOCX: python-docx
   - XLSX: openpyxl (cell contents)
   - PPTX: python-pptx (slide text)
   - TXT/MD/code: direct read
3. Store extracted text in `extracted_text` field
4. Optionally store file content in `~/.local/share/mtk/attachments/`
5. Index extracted text for full-text search

### Search Integration
```bash
mtk search "attachment:quarterly report"  # Searches attachment text
mtk search "attachment:*.pdf budget"      # Filter by type + content
```

---

## Performance Considerations

### Indexing Strategy
- Pre-compute at import time (slow import, fast query)
- SQLite with WAL mode for concurrent access
- Indexes on: message_id, date, thread_id, from_addr, account
- FTS5 on: subject, body_markdown, attachment extracted_text

### Large Archive Handling
- Pagination: all list commands support `--limit` and `--offset`
- Streaming: `--jsonl` for memory-efficient large exports
- Background indexing: `mtk attachments index --background`

### Embedding Generation
- Lazy: only generate when semantic search requested
- Batch: process N emails at a time (configurable)
- Model: all-MiniLM-L6-v2 (384 dimensions, fast)

---

## Error Handling

### Logging
- Structured JSON logs to `~/.local/share/mtk/mtk.log`
- Configurable level: debug, info, warn, error
- Log rotation: 10MB max, keep 5 files

### Import Errors
- Skip malformed emails, continue import
- Log errors to `~/.local/share/mtk/import_errors.log`
- Summary at end: "Imported 1000, skipped 3 with errors"

### LLM Errors
- Default: skip failed items, continue batch
- With `--fail-fast`: stop on first error
- Always report partial results

---

## Security

### Data Protection
- All data local, no cloud sync
- Privacy rules for controlled export
- No telemetry or analytics

---

## Migration & Versioning

### Schema Migrations
- Manual SQL scripts for breaking changes
- Documented in CHANGELOG.md
- `mtk migrate` to apply pending migrations

### Backup
```bash
mtk backup                    # Creates timestamped copy
mtk backup --output path.db   # Custom location
mtk restore <backup-file>     # Restore from backup
```

---

## Testing Requirements

- pytest with >80% coverage (CI enforced)
- Integration tests against sample email corpora
- Unit tests for parsing edge cases
- Type checking with mypy (strict mode)

---

## Roadmap

### Phase 1 (Current)
- [x] Import (Maildir, mbox, EML, Gmail)
- [x] Basic search (keyword)
- [x] Threading
- [x] Tagging
- [x] People management
- [x] Export (JSON, mbox, Markdown)
- [x] Privacy filtering
- [x] LLM classification (Ollama)
- [x] notmuch integration
- [x] Interactive shell

### Phase 2 (Next)
- [ ] Attachment extraction and indexing (PRIORITY)
- [ ] Hierarchical tags
- [ ] Account namespacing
- [ ] Structured logging

### Phase 3 (Future)
- [ ] MCP server
- [ ] Semantic search with embeddings
- [ ] Web UI (optional)
- [ ] Performance optimization for 1M+ emails

---

## Dependencies

### Required
- Python 3.11+
- typer, rich (CLI)
- sqlalchemy 2.0+ (ORM)
- pyyaml (config)

### Optional
```bash
pip install mtk[semantic]     # sentence-transformers, faiss-cpu
pip install mtk[notmuch]      # notmuch2
pip install mtk[attachments]  # pdfminer.six, python-docx, openpyxl
pip install mtk[llm]          # httpx (for Ollama)
pip install mtk[all]          # Everything
```

---

## File Locations

```
~/.config/mtk/
  config.yaml          # Main configuration
  privacy.yaml         # Privacy rules

~/.local/share/mtk/
  mtk.db               # SQLite database
  mtk.log              # Structured logs
  import_errors.log    # Import error log
  attachments/         # Extracted attachment content
  backups/             # Database backups
```
