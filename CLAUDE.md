# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mtk (Mail Toolkit) is a personal email archive management tool with semantic search, relationship mapping, and privacy controls. It wraps notmuch for indexing and adds a SQLite shadow database for enhanced features.

Part of the longecho personal archive ecosystem alongside ctk (conversations), btk (bookmarks), ebk (ebooks), stk (static sites), and ptk (photos).

## Development Commands

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run specific test file
pytest tests/test_search.py

# Run with coverage
pytest --cov=src/mtk --cov-report=term-missing

# Type checking
mypy src/mtk

# Linting
ruff check src/mtk tests
ruff format src/mtk tests
```

## Architecture

### Core Layer (`src/mtk/core/`)
- `models.py` - SQLAlchemy ORM models: Email, Person, PersonEmail, Thread, Tag, Attachment, Annotation, Collection, PrivacyRule, TopicCluster
- `database.py` - Database session management, uses SQLite with WAL mode and foreign keys enabled
- `config.py` - MtkConfig and PrivacyConfig for YAML-based configuration
- `privacy.py` - PrivacyFilter for exclusion/redaction rules during export

### Importers (`src/mtk/importers/`)
- `base.py` - BaseImporter abstract class with `discover()`, `parse()`, `import_all()` methods
- `parser.py` - EmailParser for RFC 2822 parsing, returns ParsedEmail dataclass
- `maildir.py`, `mbox.py`, `eml.py` - Format-specific importers (MaildirImporter, MboxImporter, EmlImporter)

### People/Relationships (`src/mtk/people/`)
- `resolver.py` - PersonResolver for merging multiple email addresses into unified Person records
- `relationships.py` - RelationshipAnalyzer for correspondence statistics and network graph generation (GEXF, JSON, GraphML export)

### Search (`src/mtk/search/`)
- `engine.py` - SearchEngine with keyword search (SQLite LIKE), semantic search (sentence-transformers embeddings), and Gmail-like query operators (from:, to:, subject:, after:, before:, tag:, has:attachment)

### Export (`src/mtk/export/`)
- `base.py` - BaseExporter and ExportResult
- `json_export.py`, `mbox_export.py`, `markdown_export.py` - Format-specific exporters with privacy filter support

### LLM Integration (`src/mtk/llm/`)
- `providers.py` - OllamaProvider for local LLM inference
- `classifier.py` - EmailClassifier for classification, summarization, action item extraction

### notmuch Integration (`src/mtk/integrations/`)
- `notmuch.py` - NotmuchSync for bidirectional tag sync and email import from notmuch database

### CLI (`src/mtk/cli/`)
- `main.py` - Typer app with commands: inbox, show, thread, reply, search, stats, init, shell
- Sub-apps: `import` (maildir, mbox, eml, gmail), `people` (list, show), `export` (json, mbox, markdown), `privacy` (check), `llm` (status, classify, summarize, actions), `notmuch` (status, pull, push, sync, import)
- `shell.py` - Interactive REPL mode

## Key Patterns

### Database Sessions
Use context manager for sessions (auto-commit on success, rollback on error):
```python
db = Database(path)
with db.session() as session:
    session.add(email)
    # commits automatically
```

### JSON Output
All CLI commands support `--json` flag for programmatic use - output valid JSON to stdout.

### Privacy Filtering
Apply privacy rules during export by passing `privacy_filter` to exporters. Privacy config is loaded from `~/.config/mtk/privacy.yaml`.

## Testing

Tests use pytest with fixtures defined in `tests/conftest.py`:
- `db` - In-memory database
- `session` - Database session from in-memory db
- `populated_db` - Database with sample data (persons, emails, threads, tags)
- `sample_maildir`, `sample_mbox`, `sample_eml_dir` - File system fixtures for import testing
- `email_factory`, `person_factory` - Factory fixtures for creating test data

## Optional Dependencies

- `notmuch` extra: notmuch2 bindings for mail indexing integration
- `semantic` extra: sentence-transformers + faiss-cpu for semantic search
