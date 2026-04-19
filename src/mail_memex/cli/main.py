"""Main CLI entry point for mail-memex.

Designed for both human use and Claude Code integration.
Rich output for readability, JSON output for programmatic use.
"""

from __future__ import annotations

import json as json_lib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from mail_memex import __version__
from mail_memex.core.config import MailMemexConfig
from mail_memex.core.database import Database

app = typer.Typer(
    name="mail-memex",
    help="Mail Memex - Personal email archive management",
    no_args_is_help=True,
)
console = Console()


def get_db() -> Database:
    """Get the database, loading config as needed."""
    config = MailMemexConfig.load()
    if not config.db_path:
        config.db_path = config.default_data_dir() / "mail-memex.db"
    return Database(config.db_path)


def format_date(dt: datetime | None) -> str:
    """Format datetime for display."""
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


def _resolve_email(session, message_id: str):
    """Look up email by message_id substring."""
    from sqlalchemy import select

    from mail_memex.core.models import Email

    return session.execute(select(Email).where(Email.message_id.contains(message_id))).scalar()


def _ensure_tag(session, tag_name: str):
    """Get or create a Tag by name."""
    from sqlalchemy import select

    from mail_memex.core.models import Tag

    tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
    if not tag:
        tag = Tag(name=tag_name, source="mail-memex")
        session.add(tag)
        session.flush()
    return tag


def _require_email_or_exit(session, message_id: str, json: bool):
    """Resolve an email or print a not-found message and exit 1."""
    email = _resolve_email(session, message_id)
    if email:
        return email
    if json:
        print(json_lib.dumps({"error": f"Email not found: {message_id}"}, indent=2))
    else:
        console.print(f"[red]Email not found: {message_id}[/red]")
    raise typer.Exit(1)


def _print_current_tags(email, json: bool) -> None:
    """Print the email's current tags in JSON or Rich console format."""
    current_tags = [t.name for t in email.tags]
    if json:
        print(
            json_lib.dumps(
                {"message_id": email.message_id, "tags": current_tags},
                indent=2,
            )
        )
    else:
        console.print(f"Current tags: {', '.join(current_tags) or '(none)'}")


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", help="Show version and exit"),
) -> None:
    """mail-memex - Personal email archive management."""
    if version:
        console.print(f"mail-memex version {__version__}")
        raise typer.Exit()


# === Init Command ===
@app.command()
def init(
    db_path: Path | None = typer.Option(
        None, "--db", "-d", help="Database path (default: ~/.local/share/mail-memex/mail-memex.db)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Reinitialize if already exists"),
) -> None:
    """Initialize mail-memex database."""
    config = MailMemexConfig.load()
    config.ensure_dirs()

    if db_path:
        config.db_path = db_path
    elif not config.db_path:
        config.db_path = config.default_data_dir() / "mail-memex.db"

    if config.db_path.exists():
        if not force:
            console.print(f"[yellow]Database already exists at {config.db_path}[/yellow]")
            console.print("Use --force to reinitialize")
            raise typer.Exit(1)
        # Force reinit: drop the existing database and its WAL/SHM sidecars
        # so create_tables() starts from a clean slate. (create_all is a no-op
        # on existing tables, so without this the --force flag does nothing.)
        config.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = config.db_path.with_name(config.db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()

    db = Database(config.db_path)
    db.create_tables()
    config.save()

    console.print(f"[green]Initialized mail-memex database at {config.db_path}[/green]")


# === Import Commands ===
import_app = typer.Typer(help="Import emails from various sources")
app.add_typer(import_app, name="import")


@import_app.command("mbox")
def import_mbox(
    path: Path = typer.Argument(..., help="Path to mbox file"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from mbox format."""
    from mail_memex.importers import MboxImporter

    db = get_db()
    importer = MboxImporter(path)
    _run_import_with_importer(importer, db, json_output=json)


@import_app.command("eml")
def import_eml(
    path: Path = typer.Argument(..., help="Path to EML file or directory"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from EML files."""
    from mail_memex.importers import EmlImporter

    db = get_db()
    importer = EmlImporter(path, recursive=recursive)
    _run_import_with_importer(importer, db, json_output=json)


@import_app.command("gmail")
def import_gmail(
    path: Path = typer.Argument(..., help="Path to Gmail Takeout"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from Gmail Takeout export."""
    from mail_memex.importers import GmailTakeoutImporter

    db = get_db()
    importer = GmailTakeoutImporter(path)
    _run_import_with_importer(importer, db, json_output=json)


def _resolve_thread_root(email, session) -> str | None:
    """Walk In-Reply-To and References from an email up to its earliest
    ancestor that exists in the archive.

    Returns the root's message_id, or None if no ancestor is in the archive
    (dangling reply — will stay un-threaded until the parent is imported).

    Cycles are broken defensively by tracking visited message_ids.
    """
    from sqlalchemy import select

    from mail_memex.core.models import Email

    current = email
    visited: set[str] = set()
    while True:
        if current.message_id in visited:
            return current.message_id  # cycle — stop here
        visited.add(current.message_id)

        # Candidate parent IDs, in preference order: In-Reply-To first
        # (immediate parent), then References walking newest-to-oldest.
        # All ingestion sites route Message-IDs through clean_message_id
        # so no defensive strip is needed here.
        parent_ids: list[str] = []
        if current.in_reply_to:
            parent_ids.append(current.in_reply_to)
        for ref in reversed((current.references or "").split()):
            if ref and ref not in parent_ids:
                parent_ids.append(ref)

        next_ancestor = None
        for pid in parent_ids:
            if pid in visited:
                continue
            found = session.execute(
                select(Email).where(Email.message_id == pid)
            ).scalar()
            if found is not None:
                next_ancestor = found
                break

        if next_ancestor is None:
            return None if current is email else current.message_id
        current = next_ancestor


def _build_threads(session) -> int:
    """Build conversation threads from In-Reply-To and References headers.

    Three-pass algorithm:
      1. Find all un-threaded emails that have a parent reference. For each,
         resolve the earliest ancestor in the archive.
      2. Assign thread_id to every child + its root in a single pass.
      3. Recompute email_count/first_date/last_date for each touched thread
         from a single SQL aggregate — the authoritative source is the
         emails table, not an incremented counter.

    Emails without any parent reference, and replies whose ancestors are not
    in the archive, remain un-threaded (thread_id=NULL) by design.

    The caller's db.session() context manager commits; this function does not.

    Returns the number of Thread rows created (existing threads are updated
    in place rather than duplicated).
    """
    from collections import defaultdict

    from sqlalchemy import func, or_, select

    from mail_memex.core.models import Email, Thread

    candidates = (
        session.execute(
            select(Email).where(
                Email.thread_id.is_(None),
                or_(Email.in_reply_to.isnot(None), Email.references.isnot(None)),
            )
        )
        .scalars()
        .all()
    )

    roots: dict[str, list[Email]] = defaultdict(list)
    for email in candidates:
        root_id = _resolve_thread_root(email, session)
        if root_id is not None:
            roots[root_id].append(email)

    if not roots:
        return 0

    # Pass A: ensure a Thread row exists for each root BEFORE any email is
    # re-assigned. emails.thread_id has a FK to threads.thread_id, so the
    # parent row must be present first.
    threads_created = 0
    for root_id in roots:
        thread_id = f"thread-{root_id}"
        existing = session.execute(
            select(Thread).where(Thread.thread_id == thread_id)
        ).scalar()
        if existing is None:
            root = session.execute(
                select(Email).where(Email.message_id == root_id)
            ).scalar()
            session.add(
                Thread(
                    thread_id=thread_id,
                    subject=root.subject if root is not None else None,
                )
            )
            threads_created += 1
    session.flush()

    # Pass B: assign thread_id on every child + its root.
    for root_id, emails in roots.items():
        thread_id = f"thread-{root_id}"
        for email in emails:
            email.thread_id = thread_id
        root = session.execute(
            select(Email).where(Email.message_id == root_id)
        ).scalar()
        if root is not None and root.thread_id != thread_id:
            root.thread_id = thread_id
    session.flush()

    # Pass C: recompute stats from the authoritative emails table.
    for root_id in roots:
        thread_id = f"thread-{root_id}"
        thread = session.execute(
            select(Thread).where(Thread.thread_id == thread_id)
        ).scalar()
        if thread is None:
            continue
        count, first_date, last_date = session.execute(
            select(
                func.count(Email.id),
                func.min(Email.date),
                func.max(Email.date),
            ).where(
                Email.thread_id == thread_id,
                Email.archived_at.is_(None),
            )
        ).one()
        thread.email_count = count or 0
        thread.first_date = first_date
        thread.last_date = last_date

    return threads_created


@dataclass
class ImportResult:
    """Result of an import operation."""

    imported: int = 0
    errors: int = 0
    threads: int = 0
    source: str = ""


def _run_import_with_importer(importer, db: Database, json_output: bool = False) -> ImportResult:  # type: ignore
    """Run import with progress display."""
    from mail_memex.core.models import Attachment, Email

    result = ImportResult(source=str(importer.source_path))

    if not json_output:
        console.print(f"[blue]Importing from {importer.format_name}: {importer.source_path}[/blue]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=json_output,
    ) as progress:
        task = progress.add_task("Importing emails...", total=None)

        with db.session() as session:
            for parsed, error in importer.import_all():
                if error:
                    result.errors += 1
                    continue

                if parsed is None:
                    continue

                existing = session.query(Email).filter_by(message_id=parsed.message_id).first()
                if existing:
                    continue

                email = Email(
                    message_id=parsed.message_id,
                    from_addr=parsed.from_addr,
                    from_name=parsed.from_name,
                    subject=parsed.subject,
                    # Column is naive UTC per existing data; fall back to
                    # same shape rather than mixing naive/aware within a column.
                    date=parsed.date or datetime.now(UTC).replace(tzinfo=None),
                    in_reply_to=parsed.in_reply_to,
                    references=" ".join(parsed.references) if parsed.references else None,
                    body_text=parsed.body_text,
                    body_html=parsed.body_html,
                    body_preview=parsed.body_preview,
                    file_path=str(parsed.file_path) if parsed.file_path else None,
                )

                email.to_addrs = ",".join(parsed.to_addrs) if parsed.to_addrs else None
                email.cc_addrs = ",".join(parsed.cc_addrs) if parsed.cc_addrs else None
                email.bcc_addrs = ",".join(parsed.bcc_addrs) if parsed.bcc_addrs else None

                # Normalized recipients for indexed to:/cc:/bcc: queries.
                # CSV columns above remain for display.
                from mail_memex.core.recipients import build_recipients

                email.recipients.extend(build_recipients(parsed.to_addrs, parsed.to_names, "to"))
                email.recipients.extend(build_recipients(parsed.cc_addrs, parsed.cc_names, "cc"))
                email.recipients.extend(build_recipients(parsed.bcc_addrs, None, "bcc"))

                # Preserve all headers (Gmail labels, List-Id, etc.) as JSON.
                # Queryable via json_extract(metadata_json, '$.X-Gmail-Labels').
                if parsed.raw_headers:
                    email.metadata_json = json_lib.dumps(parsed.raw_headers)

                for att in parsed.attachments:
                    attachment = Attachment(
                        filename=att.filename,
                        content_type=att.content_type,
                        size=att.size,
                        content_id=att.content_id,
                    )
                    email.attachments.append(attachment)

                session.add(email)
                result.imported += 1

                if result.imported % 100 == 0:
                    progress.update(task, description=f"Imported {result.imported} emails...")
                    session.commit()

            session.commit()

            # Build threads after import
            result.threads = _build_threads(session)

    if json_output:
        print(
            json_lib.dumps(
                {
                    "source": result.source,
                    "imported": result.imported,
                    "errors": result.errors,
                    "threads": result.threads,
                },
                indent=2,
            )
        )
    else:
        console.print(f"[green]Imported {result.imported} emails[/green]")
        if result.threads:
            console.print(f"[blue]Built {result.threads} conversation threads[/blue]")
        if result.errors:
            console.print(f"[yellow]Skipped {result.errors} emails with errors[/yellow]")

    return result


# === Search Command ===
@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Search emails in the archive.

    Operators: from:, to:, subject:, after:, before:, tag:, has:attachment
    """
    from mail_memex.search import SearchEngine

    db = get_db()
    with db.session() as session:
        engine = SearchEngine(session)
        results = engine.search(query, limit=limit)

        if not results:
            console.print("[yellow]No results found[/yellow]")
            return

        if json:
            data = [
                {
                    "id": r.email.message_id,
                    "from": r.email.from_addr,
                    "from_name": r.email.from_name,
                    "subject": r.email.subject,
                    "date": r.email.date.isoformat() if r.email.date else None,
                    "preview": r.email.body_preview,
                    "score": r.score,
                }
                for r in results
            ]
            print(json_lib.dumps(data, indent=2))
            return

        table = Table(title=f"Search Results ({len(results)})")
        table.add_column("Date", style="dim", width=16)
        table.add_column("From", width=25)
        table.add_column("Subject")
        table.add_column("Score", justify="right", width=6)

        for r in results:
            table.add_row(
                format_date(r.email.date),
                (r.email.from_name or r.email.from_addr)[:24],
                (r.email.subject or "")[:50],
                f"{r.score:.2f}",
            )

        console.print(table)


# === Rebuild Commands ===
rebuild_app = typer.Typer(help="Rebuild indexes and threads")
app.add_typer(rebuild_app, name="rebuild")


@rebuild_app.command("index")
def rebuild_index(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Rebuild FTS5 full-text search index.

    Recreates the search index from all emails in the database.
    Run this after bulk imports or if search results seem stale.
    """
    from mail_memex.search import fts_stats, rebuild_fts_index

    db = get_db()
    db.create_tables()

    count = rebuild_fts_index(db.engine)
    stats_data = fts_stats(db.engine)

    if json:
        print(
            json_lib.dumps(
                {
                    "indexed": count,
                    "fts5_available": stats_data["available"],
                    "in_sync": stats_data["in_sync"],
                },
                indent=2,
            )
        )
    else:
        console.print(f"[green]Rebuilt FTS5 index: {count} emails indexed[/green]")
        if stats_data["in_sync"]:
            console.print("[dim]Index is in sync with email database[/dim]")


@rebuild_app.command("threads")
def rebuild_threads(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Rebuild conversation threads from email references.

    Groups emails into threads based on In-Reply-To and References headers.
    Run this after importing emails if threads weren't built automatically.
    """
    db = get_db()
    with db.session() as session:
        thread_count = _build_threads(session)

    if json:
        print(json_lib.dumps({"threads_created": thread_count}, indent=2))
    elif thread_count:
        console.print(f"[green]Built {thread_count} conversation threads[/green]")
    else:
        console.print("[yellow]No new threads to build[/yellow]")


@rebuild_app.command("recipients")
def rebuild_recipients(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Backfill the email_recipients side table from to/cc/bcc CSV columns.

    Run once after upgrading to a schema that includes email_recipients.
    Idempotent: emails that already have recipient rows are skipped.
    """
    from mail_memex.core.recipients import backfill_recipients

    db = get_db()
    with db.session() as session:
        created = backfill_recipients(session)
        session.commit()

    if json:
        print(json_lib.dumps({"recipients_created": created}, indent=2))
    elif created:
        console.print(f"[green]Created {created} recipient rows[/green]")
    else:
        console.print("[yellow]All emails already have recipient rows[/yellow]")


# === Tag Commands ===
tag_app = typer.Typer(help="Manage email tags")
app.add_typer(tag_app, name="tag")


@tag_app.command("add")
def tag_add(
    message_id: str = typer.Argument(..., help="Message ID"),
    tags: list[str] = typer.Argument(..., help="Tag names to add"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add tags to an email."""
    db = get_db()
    with db.session() as session:
        email = _require_email_or_exit(session, message_id, json)

        for tag_name in tags:
            tag = _ensure_tag(session, tag_name)
            if tag not in email.tags:
                email.tags.append(tag)
        if not json:
            console.print(f"[green]Added tags: {', '.join(tags)}[/green]")

        session.commit()
        _print_current_tags(email, json)


@tag_app.command("remove")
def tag_remove(
    message_id: str = typer.Argument(..., help="Message ID"),
    tags: list[str] = typer.Argument(..., help="Tag names to remove"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Remove tags from an email."""
    from sqlalchemy import select

    from mail_memex.core.models import Tag

    db = get_db()
    with db.session() as session:
        email = _require_email_or_exit(session, message_id, json)

        for tag_name in tags:
            existing_tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
            if existing_tag and existing_tag in email.tags:
                email.tags.remove(existing_tag)
        if not json:
            console.print(f"[yellow]Removed tags: {', '.join(tags)}[/yellow]")

        session.commit()
        _print_current_tags(email, json)


@tag_app.command("list")
def tag_list(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List all tags in the archive."""
    from sqlalchemy import func, select

    from mail_memex.core.models import Tag, email_tags

    db = get_db()
    with db.session() as session:
        # Get tags with email counts
        stmt = (
            select(Tag.name, func.count(email_tags.c.email_id).label("count"))
            .outerjoin(email_tags, Tag.id == email_tags.c.tag_id)
            .group_by(Tag.id)
            .order_by(func.count(email_tags.c.email_id).desc())
        )
        results = session.execute(stmt).all()

        if json:
            data = [{"name": name, "count": count} for name, count in results]
            print(json_lib.dumps(data, indent=2))
            return

        if not results:
            console.print("[yellow]No tags found[/yellow]")
            return

        table = Table(title=f"Tags ({len(results)})")
        table.add_column("Tag", width=30)
        table.add_column("Emails", justify="right", width=8)

        for name, count in results:
            table.add_row(name, str(count))

        console.print(table)


@tag_app.command("batch")
def tag_batch(
    query: str = typer.Argument(..., help="Search query to match emails"),
    add: list[str] | None = typer.Option(None, "--add", "-a", help="Tags to add"),
    remove: list[str] | None = typer.Option(None, "--remove", "-r", help="Tags to remove"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be changed"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add or remove tags from multiple emails matching a query.

    Example: mail-memex tag batch "from:alice@example.com" --add work --add important
    """
    from sqlalchemy import select

    from mail_memex.core.models import Tag
    from mail_memex.search import SearchEngine

    db = get_db()
    with db.session() as session:
        engine = SearchEngine(session)
        results = engine.search(query, limit=1000)

        if not results:
            if json:
                print(json_lib.dumps({"matched": 0, "modified": 0}, indent=2))
            else:
                console.print("[yellow]No emails matched the query[/yellow]")
            return

        emails = [r.email for r in results]

        if dry_run:
            if json:
                print(
                    json_lib.dumps(
                        {
                            "dry_run": True,
                            "matched": len(emails),
                            "emails": [
                                {"id": e.message_id, "subject": e.subject} for e in emails[:20]
                            ],
                            "add_tags": add or [],
                            "remove_tags": remove or [],
                        },
                        indent=2,
                    )
                )
            else:
                console.print(f"[blue]Would modify {len(emails)} emails[/blue]")
                for e in emails[:10]:
                    console.print(f"  - {e.subject or '(no subject)'}")
                if len(emails) > 10:
                    console.print(f"  ... and {len(emails) - 10} more")
            return

        modified = 0
        for email in emails:
            changed = False
            if add:
                for tag_name in add:
                    tag = _ensure_tag(session, tag_name)
                    if tag not in email.tags:
                        email.tags.append(tag)
                        changed = True

            if remove:
                for tag_name in remove:
                    existing_tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                    if existing_tag and existing_tag in email.tags:
                        email.tags.remove(existing_tag)
                        changed = True

            if changed:
                modified += 1

        session.commit()

        if json:
            print(
                json_lib.dumps(
                    {
                        "matched": len(emails),
                        "modified": modified,
                        "add_tags": add or [],
                        "remove_tags": remove or [],
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[green]Modified {modified} of {len(emails)} matched emails[/green]")


# === Export Commands ===
export_app = typer.Typer(help="Export emails to various formats")
app.add_typer(export_app, name="export")


def _prepare_export(
    session, query: str | None, *, include_archived: bool = False
) -> list:
    """Fetch emails for export, optionally filtered by a search query.

    Soft-deleted emails are excluded by default — consistent with the
    workspace convention that archived records are resolvable by URI but
    hidden from default enumeration. Pass include_archived=True to
    produce a full mirror.
    """
    from sqlalchemy import select

    from mail_memex.core.models import Email
    from mail_memex.search import SearchEngine

    if query:
        engine = SearchEngine(session)
        return [
            r.email
            for r in engine.search(
                query, limit=100000, include_archived=include_archived
            )
        ]

    stmt = select(Email)
    if not include_archived:
        stmt = stmt.where(Email.archived_at.is_(None))
    return list(session.execute(stmt).scalars())


def _run_export(exporter_factory, query: str | None, *, include_archived: bool = False):
    """Open a session, fetch emails, run the exporter, return the result."""
    db = get_db()
    with db.session() as session:
        emails = _prepare_export(session, query, include_archived=include_archived)
        return exporter_factory().export(emails)


_INCLUDE_ARCHIVED_OPT = typer.Option(
    False,
    "--include-archived",
    help="Include soft-deleted emails in the export (default: excluded).",
)


@export_app.command("json")
def export_json(
    output: Path = typer.Argument(..., help="Output file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    pretty: bool = typer.Option(True, "--pretty/--compact", help="Pretty print JSON"),
    include_archived: bool = _INCLUDE_ARCHIVED_OPT,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to JSON format."""
    from mail_memex.export import JsonExporter

    result = _run_export(
        lambda: JsonExporter(output, pretty=pretty),
        query,
        include_archived=include_archived,
    )

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")


@export_app.command("mbox")
def export_mbox(
    output: Path = typer.Argument(..., help="Output file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    include_archived: bool = _INCLUDE_ARCHIVED_OPT,
    json: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to mbox format."""
    from mail_memex.export import MboxExporter

    result = _run_export(
        lambda: MboxExporter(output), query, include_archived=include_archived
    )

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")


@export_app.command("markdown")
def export_markdown(
    output: Path = typer.Argument(..., help="Output directory"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    threads: bool = typer.Option(False, "--threads", "-t", help="Group by thread"),
    include_archived: bool = _INCLUDE_ARCHIVED_OPT,
    json: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to Markdown files."""
    from mail_memex.export import MarkdownExporter

    result = _run_export(
        lambda: MarkdownExporter(output, group_by_thread=threads),
        query,
        include_archived=include_archived,
    )

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}/[/green]")


@export_app.command("html")
def export_html(
    output: Path = typer.Argument(..., help="Output HTML file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    include_archived: bool = _INCLUDE_ARCHIVED_OPT,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export email archive as a self-contained HTML application."""
    from mail_memex.export.html_export import HtmlExporter

    result = _run_export(
        lambda: HtmlExporter(output), query, include_archived=include_archived
    )

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported archive to {output}[/green]")
        console.print(f"  {result.emails_exported} emails, {output.stat().st_size / 1024:.0f} KB")


@export_app.command("arkiv")
def export_arkiv(
    output: Path = typer.Argument(..., help="Output JSONL file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    include_body: bool = typer.Option(True, "--body/--no-body", help="Include email body text"),
    include_archived: bool = _INCLUDE_ARCHIVED_OPT,
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to arkiv JSONL format."""
    from mail_memex.export.arkiv_export import ArkivExporter

    result = _run_export(
        lambda: ArkivExporter(output, include_body=include_body),
        query,
        include_archived=include_archived,
    )

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")
        console.print(f"  Schema written to {output.parent / 'schema.yaml'}")


# === IMAP Commands ===
from mail_memex.cli.imap_cli import imap_app  # noqa: E402

app.add_typer(imap_app, name="imap")


# === MCP Command ===
@app.command()
def mcp(
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio"),
) -> None:
    """Start MCP server for Claude Code integration.

    Exposes email archive as MCP tools (search, read, tag, etc.).
    Configure in .mcp.json or run directly.
    """
    try:
        from mail_memex.mcp import run_server
    except ImportError:
        console.print("[red]MCP support requires the mcp package.[/red]")
        console.print("Install with: pip install mail-memex[mcp]")
        raise typer.Exit(1) from None

    if transport != "stdio":
        console.print(f"[red]Unsupported transport: {transport}[/red]")
        console.print("Currently only 'stdio' is supported.")
        raise typer.Exit(1)

    run_server()


if __name__ == "__main__":
    app()
