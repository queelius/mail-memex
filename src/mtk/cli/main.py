"""Main CLI entry point for mtk.

Designed for both human use and Claude Code integration.
Rich output for readability, JSON output for programmatic use.

Usage:
    mtk inbox                    Show recent emails
    mtk show <id>                Show email with full context
    mtk thread <id>              Show full thread conversation
    mtk reply <id>               Prepare context for reply
    mtk search QUERY             Search emails
    mtk people                   List/manage correspondents
    mtk import                   Import emails
    mtk shell                    Interactive shell mode
"""

from __future__ import annotations

import json as json_lib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from mtk import __version__
from mtk.core.config import MtkConfig
from mtk.core.database import Database

app = typer.Typer(
    name="mtk",
    help="Mail Toolkit - Personal email archive management",
    no_args_is_help=True,
)
console = Console()


def get_db() -> Database:
    """Get the database, loading config as needed."""
    config = MtkConfig.load()
    if not config.db_path:
        config.db_path = config.default_data_dir() / "mtk.db"
    return Database(config.db_path)


def format_date(dt: datetime | None) -> str:
    """Format datetime for display."""
    if not dt:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_email_summary(email, show_body: bool = False) -> str:
    """Format email for display."""
    lines = [
        f"**From:** {email.from_name or ''} <{email.from_addr}>",
        f"**Date:** {format_date(email.date)}",
        f"**Subject:** {email.subject or '(no subject)'}",
    ]
    if email.in_reply_to:
        lines.append(f"**In-Reply-To:** {email.in_reply_to}")

    if show_body and email.body_text:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(email.body_text)

    return "\n".join(lines)


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", help="Show version and exit"),
) -> None:
    """mtk - Mail Toolkit for personal email archive management."""
    if version:
        console.print(f"mtk version {__version__}")
        raise typer.Exit()


# === Inbox Command ===
@app.command()
def inbox(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum emails to show"),
    since: str | None = typer.Option(
        None, "--since", "-s", help="Show emails since date (YYYY-MM-DD)"
    ),
    unread: bool = typer.Option(False, "--unread", "-u", help="Show only unread/unprocessed"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show recent emails (inbox view).

    Perfect for quick overview of what needs attention.
    """
    from sqlalchemy import desc, select

    from mtk.core.models import Email

    db = get_db()
    with db.session() as session:
        stmt = select(Email).order_by(desc(Email.date)).limit(limit)

        if since:
            try:
                since_date = datetime.strptime(since, "%Y-%m-%d")
                stmt = stmt.where(Email.date >= since_date)
            except ValueError:
                console.print(f"[red]Invalid date format: {since}[/red]")
                raise typer.Exit(1) from None

        emails = list(session.execute(stmt).scalars())

        if json:
            data = [
                {
                    "id": e.message_id,
                    "from": e.from_addr,
                    "from_name": e.from_name,
                    "subject": e.subject,
                    "date": e.date.isoformat() if e.date else None,
                    "preview": e.body_preview,
                    "thread_id": e.thread_id,
                }
                for e in emails
            ]
            print(json_lib.dumps(data, indent=2))
            return

        if not emails:
            console.print("[yellow]No emails found[/yellow]")
            return

        table = Table(title=f"Recent Emails ({len(emails)})")
        table.add_column("#", style="dim", width=3)
        table.add_column("Date", style="cyan", width=16)
        table.add_column("From", width=25)
        table.add_column("Subject")

        for i, email in enumerate(emails, 1):
            table.add_row(
                str(i),
                format_date(email.date),
                f"{email.from_name or email.from_addr}"[:24],
                (email.subject or "(no subject)")[:60],
            )

        console.print(table)
        console.print("\n[dim]Use 'mtk show <message-id>' to view full email[/dim]")


# === Show Command ===
@app.command()
def show(
    message_id: str = typer.Argument(..., help="Message ID or partial match"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Show raw headers"),
    context: int = typer.Option(0, "--context", "-c", help="Show N surrounding thread messages"),
) -> None:
    """Show a single email with full content.

    Displays headers, body, and optionally thread context.
    """
    from sqlalchemy import select

    from mtk.core.models import Email

    db = get_db()
    with db.session() as session:
        # Find email (exact or partial match)
        stmt = select(Email).where(Email.message_id == message_id)
        email = session.execute(stmt).scalar()

        if not email:
            # Try partial match
            stmt = select(Email).where(Email.message_id.contains(message_id)).limit(1)
            email = session.execute(stmt).scalar()

        if not email:
            console.print(f"[red]Email not found: {message_id}[/red]")
            raise typer.Exit(1)

        if json:
            data = {
                "id": email.message_id,
                "thread_id": email.thread_id,
                "from": email.from_addr,
                "from_name": email.from_name,
                "subject": email.subject,
                "date": email.date.isoformat() if email.date else None,
                "in_reply_to": email.in_reply_to,
                "body_text": email.body_text,
                "body_html": email.body_html,
                "attachments": [
                    {"filename": a.filename, "type": a.content_type, "size": a.size}
                    for a in email.attachments
                ],
            }
            print(json_lib.dumps(data, indent=2))
            return

        # Display email
        console.print(
            Panel.fit(
                f"""[bold]From:[/bold] {email.from_name or ""} <{email.from_addr}>
[bold]Date:[/bold] {format_date(email.date)}
[bold]Subject:[/bold] {email.subject or "(no subject)"}
[bold]Message-ID:[/bold] {email.message_id}
[bold]Thread-ID:[/bold] {email.thread_id or "N/A"}
[bold]In-Reply-To:[/bold] {email.in_reply_to or "N/A"}""",
                title="Email Headers",
            )
        )

        # Attachments
        if email.attachments:
            console.print("\n[bold]Attachments:[/bold]")
            for att in email.attachments:
                size_kb = (att.size or 0) / 1024
                console.print(f"  📎 {att.filename} ({att.content_type}, {size_kb:.1f} KB)")

        # Body
        console.print("\n[bold]Body:[/bold]")
        console.print(Panel(email.body_text or "(no content)", border_style="dim"))

        # Thread context
        if context > 0 and email.thread_id:
            stmt = (
                select(Email)
                .where(Email.thread_id == email.thread_id)
                .where(Email.id != email.id)
                .order_by(Email.date)
                .limit(context)
            )
            related = list(session.execute(stmt).scalars())
            if related:
                console.print(f"\n[bold]Thread Context ({len(related)} related):[/bold]")
                for r in related:
                    console.print(f"  • {format_date(r.date)} - {r.from_addr}: {r.subject}")


# === Thread Command ===
@app.command()
def thread(
    thread_id: str = typer.Argument(..., help="Thread ID or message ID"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show full thread conversation.

    Great for understanding the context of a conversation.
    """
    from sqlalchemy import select

    from mtk.core.models import Email
    from mtk.core.models import Thread as ThreadModel

    db = get_db()
    with db.session() as session:
        # Find thread (could be thread ID or message ID)
        thread_obj = session.execute(
            select(ThreadModel).where(ThreadModel.thread_id == thread_id)
        ).scalar()

        if thread_obj:
            tid = thread_obj.thread_id
        else:
            # Try to find by message ID
            email = session.execute(select(Email).where(Email.message_id == thread_id)).scalar()
            if email and email.thread_id:
                tid = email.thread_id
            else:
                console.print(f"[red]Thread not found: {thread_id}[/red]")
                raise typer.Exit(1)

        # Get all emails in thread
        stmt = select(Email).where(Email.thread_id == tid).order_by(Email.date)
        emails = list(session.execute(stmt).scalars())

        if not emails:
            console.print("[yellow]No emails in thread[/yellow]")
            return

        if json:
            data = {
                "thread_id": tid,
                "message_count": len(emails),
                "messages": [
                    {
                        "id": e.message_id,
                        "from": e.from_addr,
                        "from_name": e.from_name,
                        "date": e.date.isoformat() if e.date else None,
                        "subject": e.subject,
                        "body": e.body_text,
                    }
                    for e in emails
                ],
            }
            print(json_lib.dumps(data, indent=2))
            return

        # Display thread
        console.print(
            Panel.fit(
                f"[bold]Thread:[/bold] {emails[0].subject or 'No subject'}\n"
                f"[bold]Messages:[/bold] {len(emails)}\n"
                f"[bold]Participants:[/bold] {', '.join({e.from_addr for e in emails})}",
                title="Thread Summary",
            )
        )

        for i, email in enumerate(emails, 1):
            console.print(f"\n[bold cyan]--- Message {i}/{len(emails)} ---[/bold cyan]")
            console.print(f"[bold]From:[/bold] {email.from_name or email.from_addr}")
            console.print(f"[bold]Date:[/bold] {format_date(email.date)}")
            console.print()
            console.print(email.body_text or "(no content)")


# === Reply Command ===
@app.command()
def reply(
    message_id: str = typer.Argument(..., help="Message ID to reply to"),
    include_thread: bool = typer.Option(
        True, "--thread/--no-thread", help="Include thread context"
    ),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Prepare context for composing a reply.

    Shows the email being replied to, thread history, and suggests
    reply headers. Perfect for Claude Code to help draft responses.
    """
    from sqlalchemy import select

    from mtk.core.models import Email

    db = get_db()
    with db.session() as session:
        # Find email
        stmt = select(Email).where(Email.message_id == message_id)
        email = session.execute(stmt).scalar()

        if not email:
            stmt = select(Email).where(Email.message_id.contains(message_id)).limit(1)
            email = session.execute(stmt).scalar()

        if not email:
            console.print(f"[red]Email not found: {message_id}[/red]")
            raise typer.Exit(1)

        # Get thread history if requested
        thread_history = []
        if include_thread and email.thread_id:
            stmt = (
                select(Email)
                .where(Email.thread_id == email.thread_id)
                .where(Email.date < email.date)
                .order_by(Email.date.desc())
                .limit(5)
            )
            thread_history = list(session.execute(stmt).scalars())
            thread_history.reverse()  # Oldest first

        if json:
            data = {
                "replying_to": {
                    "id": email.message_id,
                    "from": email.from_addr,
                    "from_name": email.from_name,
                    "date": email.date.isoformat() if email.date else None,
                    "subject": email.subject,
                    "body": email.body_text,
                },
                "suggested_headers": {
                    "to": email.from_addr,
                    "subject": f"Re: {email.subject}"
                    if email.subject and not email.subject.startswith("Re:")
                    else email.subject,
                    "in_reply_to": email.message_id,
                    "references": f"{email.references or ''} {email.message_id}".strip(),
                },
                "thread_history": [
                    {
                        "from": e.from_addr,
                        "from_name": e.from_name,
                        "date": e.date.isoformat() if e.date else None,
                        "body": e.body_text,
                    }
                    for e in thread_history
                ],
            }
            print(json_lib.dumps(data, indent=2))
            return

        # Display reply context
        console.print("[bold blue]📧 REPLY CONTEXT[/bold blue]\n")

        # Original message
        console.print(
            Panel.fit(
                f"""[bold]Replying to:[/bold] {email.from_name or email.from_addr}
[bold]Original Subject:[/bold] {email.subject}
[bold]Sent:[/bold] {format_date(email.date)}""",
                title="Original Message",
            )
        )

        console.print("\n[bold]Original Body:[/bold]")
        console.print(Panel(email.body_text or "(no content)", border_style="dim"))

        # Thread history
        if thread_history:
            console.print("\n[bold]Previous Messages in Thread:[/bold]")
            for h in thread_history:
                console.print(f"\n[dim]{format_date(h.date)} - {h.from_addr}:[/dim]")
                preview = (h.body_text or "")[:200]
                if len(h.body_text or "") > 200:
                    preview += "..."
                console.print(f"  {preview}")

        # Suggested headers
        console.print("\n[bold green]Suggested Reply Headers:[/bold green]")
        console.print(f"  To: {email.from_addr}")
        subject = email.subject or ""
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        console.print(f"  Subject: {subject}")
        console.print(f"  In-Reply-To: {email.message_id}")


# === Init Command ===
@app.command()
def init(
    path: Path | None = typer.Argument(
        None, help="Path to email source (Maildir, mbox, or directory)"
    ),
    db_path: Path | None = typer.Option(
        None, "--db", "-d", help="Database path (default: ~/.local/share/mtk/mtk.db)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Reinitialize if already exists"),
) -> None:
    """Initialize mtk database and optionally import emails."""
    config = MtkConfig.load()
    config.ensure_dirs()

    if db_path:
        config.db_path = db_path
    elif not config.db_path:
        config.db_path = config.default_data_dir() / "mtk.db"

    if config.db_path.exists() and not force:
        console.print(f"[yellow]Database already exists at {config.db_path}[/yellow]")
        console.print("Use --force to reinitialize")
        raise typer.Exit(1)

    db = Database(config.db_path)
    db.create_tables()
    config.save()

    console.print(f"[green]Initialized mtk database at {config.db_path}[/green]")

    if path:
        _run_import(path, db)


# === Import Commands ===
import_app = typer.Typer(help="Import emails from various sources")
app.add_typer(import_app, name="import")


@import_app.command("maildir")
def import_maildir(
    path: Path = typer.Argument(..., help="Path to Maildir directory"),
    include_subfolders: bool = typer.Option(True, "--subfolders/--no-subfolders"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from Maildir format."""
    from mtk.importers import MaildirImporter

    db = get_db()
    importer = MaildirImporter(path, include_subfolders=include_subfolders)
    _run_import_with_importer(importer, db, json_output=json)


@import_app.command("mbox")
def import_mbox(
    path: Path = typer.Argument(..., help="Path to mbox file"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from mbox format."""
    from mtk.importers import MboxImporter

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
    from mtk.importers import EmlImporter

    db = get_db()
    importer = EmlImporter(path, recursive=recursive)
    _run_import_with_importer(importer, db, json_output=json)


@import_app.command("gmail")
def import_gmail(
    path: Path = typer.Argument(..., help="Path to Gmail Takeout"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from Gmail Takeout export."""
    from mtk.importers import GmailTakeoutImporter

    db = get_db()
    importer = GmailTakeoutImporter(path)
    _run_import_with_importer(importer, db, json_output=json)


def _run_import(path: Path, db: Database) -> None:
    """Auto-detect format and import."""
    from mtk.importers import EmlImporter, MaildirImporter, MboxImporter

    if path.is_file():
        if path.suffix.lower() == ".mbox":  # noqa: SIM108
            importer = MboxImporter(path)
        else:
            importer = EmlImporter(path)
    elif (path / "cur").exists() and (path / "new").exists():
        importer = MaildirImporter(path)
    else:
        importer = EmlImporter(path)

    _run_import_with_importer(importer, db)


def _build_threads(session) -> int:
    """Build conversation threads from email references.

    Groups emails into threads based on In-Reply-To and References headers.
    Returns the number of threads created/updated.
    """
    from sqlalchemy import select

    from mtk.core.models import Email, Thread

    threads_created = 0
    processed = True

    # Keep processing until no more changes (handles multi-level threads)
    while processed:
        processed = False

        # Get emails without thread_id that have In-Reply-To
        emails_needing_threads = (
            session.execute(
                select(Email)
                .where(Email.thread_id.is_(None), Email.in_reply_to.isnot(None))
                .order_by(Email.date)  # Process oldest first
            )
            .scalars()
            .all()
        )

        for email in emails_needing_threads:
            # Find the parent email by In-Reply-To
            parent_msg_id = email.in_reply_to.strip("<>") if email.in_reply_to else None
            if not parent_msg_id:
                continue

            parent = session.execute(
                select(Email).where(Email.message_id == parent_msg_id)
            ).scalar()

            if parent and parent.thread_id:
                # Parent already has a thread, join it
                email.thread_id = parent.thread_id
                # Update thread stats
                thread = session.execute(
                    select(Thread).where(Thread.thread_id == parent.thread_id)
                ).scalar()
                if thread:
                    thread.email_count += 1
                    if email.date and (not thread.last_date or email.date > thread.last_date):
                        thread.last_date = email.date
                session.flush()
                processed = True
            elif parent:
                # Parent exists but no thread yet - create one
                thread_id = f"thread-{parent.message_id}"
                thread = Thread(
                    thread_id=thread_id,
                    subject=parent.subject,
                    email_count=2,
                    first_date=parent.date,
                    last_date=email.date
                    if email.date and parent.date and email.date > parent.date
                    else parent.date,
                )
                session.add(thread)
                parent.thread_id = thread_id
                email.thread_id = thread_id
                session.flush()  # Flush so subsequent queries see this
                threads_created += 1
                processed = True
            # If parent not found, skip for now (might be imported later)

    session.commit()
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
    from mtk.core.models import Attachment, Email
    from mtk.people.resolver import PersonResolver

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
            resolver = PersonResolver(session)

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
                    date=parsed.date or datetime.now(),
                    in_reply_to=parsed.in_reply_to,
                    references=" ".join(parsed.references) if parsed.references else None,
                    body_text=parsed.body_text,
                    body_html=parsed.body_html,
                    body_preview=parsed.body_preview,
                    file_path=str(parsed.file_path) if parsed.file_path else None,
                )

                if parsed.from_addr:
                    sender = resolver.resolve(parsed.from_addr, parsed.from_name)
                    email.sender_id = sender.id

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
    semantic: bool = typer.Option(False, "--semantic", "-s"),
    json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Search emails in the archive.

    Operators: from:, to:, subject:, after:, before:, tag:, has:attachment
    """
    from mtk.search import SearchEngine

    if semantic:
        query = f"is:semantic {query}"

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


# === People Commands ===
people_app = typer.Typer(help="Manage correspondents")
app.add_typer(people_app, name="people")


@people_app.command("list")
def people_list(
    limit: int = typer.Option(20, "--limit", "-n"),
    json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List top correspondents."""
    from mtk.people import RelationshipAnalyzer

    db = get_db()
    with db.session() as session:
        analyzer = RelationshipAnalyzer(session)
        stats = analyzer.get_top_correspondents(limit=limit)

        if json:
            data = [
                {
                    "id": s.person_id,
                    "name": s.person_name,
                    "email": s.primary_email,
                    "email_count": s.total_emails,
                }
                for s in stats
            ]
            print(json_lib.dumps(data, indent=2))
            return

        table = Table(title=f"Top Correspondents ({len(stats)})")
        table.add_column("ID", style="dim", width=4)
        table.add_column("Name", width=25)
        table.add_column("Email", width=30)
        table.add_column("Emails", justify="right", width=8)

        for s in stats:
            table.add_row(
                str(s.person_id),
                s.person_name[:24],
                s.primary_email[:29],
                str(s.total_emails),
            )

        console.print(table)


@people_app.command("show")
def people_show(
    person_id: int = typer.Argument(..., help="Person ID"),
    json: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show details for a specific person."""
    from mtk.people import RelationshipAnalyzer

    db = get_db()
    with db.session() as session:
        analyzer = RelationshipAnalyzer(session)
        stats = analyzer.get_correspondent_stats(person_id)

        if not stats:
            console.print(f"[red]Person not found: {person_id}[/red]")
            raise typer.Exit(1)

        if json:
            data = {
                "id": stats.person_id,
                "name": stats.person_name,
                "email": stats.primary_email,
                "email_count": stats.total_emails,
                "first_email": stats.first_email.isoformat() if stats.first_email else None,
                "last_email": stats.last_email.isoformat() if stats.last_email else None,
                "thread_count": stats.thread_count,
                "relationship_type": stats.relationship_type,
            }
            print(json_lib.dumps(data, indent=2))
            return

        console.print(
            Panel.fit(
                f"""[bold]Name:[/bold] {stats.person_name}
[bold]Email:[/bold] {stats.primary_email}
[bold]Type:[/bold] {stats.relationship_type or "Unknown"}
[bold]Total Emails:[/bold] {stats.total_emails}
[bold]Threads:[/bold] {stats.thread_count}
[bold]First Contact:[/bold] {format_date(stats.first_email)}
[bold]Last Contact:[/bold] {format_date(stats.last_email)}""",
                title="Person Details",
            )
        )



# === Stats Command ===
@app.command()
def stats(json: bool = typer.Option(False, "--json", "-j")) -> None:
    """Show archive statistics."""
    from sqlalchemy import func, select

    from mtk.core.models import Attachment, Email, Person, Tag, Thread

    db = get_db()
    with db.session() as session:
        email_count = session.execute(select(func.count(Email.id))).scalar() or 0
        person_count = session.execute(select(func.count(Person.id))).scalar() or 0
        thread_count = session.execute(select(func.count(Thread.id))).scalar() or 0
        tag_count = session.execute(select(func.count(Tag.id))).scalar() or 0
        attachment_count = session.execute(select(func.count(Attachment.id))).scalar() or 0

        date_result = session.execute(select(func.min(Email.date), func.max(Email.date))).one()

    # FTS5 stats (outside session context — uses engine directly)
    from mtk.search import fts_stats as get_fts_stats

    fts_info = get_fts_stats(db.engine)

    if json:
        data = {
            "emails": email_count,
            "people": person_count,
            "threads": thread_count,
            "tags": tag_count,
            "attachments": attachment_count,
            "date_from": date_result[0].isoformat() if date_result[0] else None,
            "date_to": date_result[1].isoformat() if date_result[1] else None,
            "fts5": fts_info,
        }
        print(json_lib.dumps(data, indent=2))
        return

    fts_status = (
        "[green]Active[/green]" if fts_info["available"] else "[yellow]Unavailable[/yellow]"
    )
    fts_line = f"\nFTS5 Search: {fts_status}"
    if fts_info["available"]:
        fts_line += f" ({fts_info['indexed_count']:,} indexed)"
        if not fts_info["in_sync"]:
            fts_line += " [yellow](out of sync)[/yellow]"

    panel = Panel.fit(
        f"""[bold]Email Archive Statistics[/bold]

Emails:      {email_count:,}
People:      {person_count:,}
Threads:     {thread_count:,}
Tags:        {tag_count:,}
Attachments: {attachment_count:,}

Date Range:  {date_result[0] or "N/A"} to {date_result[1] or "N/A"}
{fts_line}""",
        title="mtk",
    )
    console.print(panel)


# === Rebuild Index Command ===
@app.command("rebuild-index")
def rebuild_index(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Rebuild FTS5 full-text search index.

    Recreates the search index from all emails in the database.
    Run this after bulk imports or if search results seem stale.
    """
    from mtk.search import fts_stats, rebuild_fts_index

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


# === Threads Command ===
@app.command("rebuild-threads")
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


# === Embeddings Command ===
@app.command()
def embeddings(
    batch_size: int = typer.Option(100, "--batch", "-b"),
    model: str = typer.Option("all-MiniLM-L6-v2", "--model", "-m"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Generate embeddings for semantic search."""
    from mtk.search import SearchEngine

    db = get_db()
    with db.session() as session:
        engine = SearchEngine(session)
        if not json:
            console.print("[blue]Generating embeddings...[/blue]")
        count = engine.generate_embeddings(batch_size=batch_size, model_name=model)
        if json:
            print(json_lib.dumps({"generated": count, "model": model}, indent=2))
        else:
            console.print(f"[green]Generated embeddings for {count} emails[/green]")


# === Tag Command ===
@app.command()
def tag(
    message_id: str = typer.Argument(..., help="Message ID"),
    add: list[str] | None = typer.Option(None, "--add", "-a", help="Tags to add"),
    remove: list[str] | None = typer.Option(None, "--remove", "-r", help="Tags to remove"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add or remove tags from an email."""
    from sqlalchemy import select

    from mtk.core.models import Email, Tag

    db = get_db()
    with db.session() as session:
        email = session.execute(select(Email).where(Email.message_id.contains(message_id))).scalar()

        if not email:
            if json:
                print(json_lib.dumps({"error": f"Email not found: {message_id}"}, indent=2))
            else:
                console.print(f"[red]Email not found: {message_id}[/red]")
            raise typer.Exit(1)

        if add:
            for tag_name in add:
                existing_tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                if not existing_tag:
                    existing_tag = Tag(name=tag_name, source="mtk")
                    session.add(existing_tag)
                if existing_tag not in email.tags:
                    email.tags.append(existing_tag)
            if not json:
                console.print(f"[green]Added tags: {', '.join(add)}[/green]")

        if remove:
            for tag_name in remove:
                existing_tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                if existing_tag and existing_tag in email.tags:
                    email.tags.remove(existing_tag)
            if not json:
                console.print(f"[yellow]Removed tags: {', '.join(remove)}[/yellow]")

        # Queue tag changes for IMAP push if email is IMAP-tracked
        if email.imap_account:
            from mtk.imap.push import queue_tag_change

            for tag_name in add or []:
                queue_tag_change(session, email.id, email.imap_account, "add", tag_name)
            for tag_name in remove or []:
                queue_tag_change(session, email.id, email.imap_account, "remove", tag_name)

        session.commit()

        current_tags = [t.name for t in email.tags]
        if json:
            print(
                json_lib.dumps(
                    {
                        "message_id": email.message_id,
                        "tags": current_tags,
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"Current tags: {', '.join(current_tags) or '(none)'}")


# === Privacy Commands ===
privacy_app = typer.Typer(help="Privacy and redaction tools")
app.add_typer(privacy_app, name="privacy")


@privacy_app.command("check")
def privacy_check(
    query: str | None = typer.Argument(None, help="Search query to filter emails"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Preview what privacy rules would exclude or redact.

    Shows statistics about what would be filtered without making changes.
    """
    from sqlalchemy import select

    from mtk.core.config import PrivacyConfig
    from mtk.core.models import Email
    from mtk.core.privacy import PrivacyFilter
    from mtk.search import SearchEngine

    privacy_config = PrivacyConfig.load()
    pfilter = PrivacyFilter(privacy_config)

    db = get_db()
    with db.session() as session:
        if query:
            engine = SearchEngine(session)
            results = engine.search(query, limit=10000)
            emails = [r.email for r in results]
        else:
            emails = list(session.execute(select(Email)).scalars())

        report = pfilter.preview(emails)

    if json:
        print(
            json_lib.dumps(
                {
                    "total_emails": report.total_emails,
                    "excluded": report.excluded_count,
                    "redacted": report.redacted_count,
                    "exclusion_reasons": report.exclusion_reasons,
                    "redaction_patterns": report.redaction_patterns_applied,
                },
                indent=2,
            )
        )
    else:
        console.print(
            Panel.fit(
                f"""[bold]Privacy Filter Preview[/bold]

Total emails checked: {report.total_emails}
Would exclude: {report.excluded_count}
Would redact: {report.redacted_count}""",
                title="Privacy Check",
            )
        )

        if report.exclusion_reasons:
            console.print("\n[bold]Exclusion reasons:[/bold]")
            for reason, count in report.exclusion_reasons.items():
                console.print(f"  {reason}: {count}")

        if report.redaction_patterns_applied:
            console.print("\n[bold]Redaction patterns matched:[/bold]")
            for pattern, count in report.redaction_patterns_applied.items():
                console.print(f"  {pattern[:40]}: {count} matches")


# === Export Commands ===
export_app = typer.Typer(help="Export emails to various formats")
app.add_typer(export_app, name="export")


def _prepare_export(session, query: str | None, apply_privacy: bool) -> tuple[list, object]:
    """Shared setup for export commands: build privacy filter and fetch emails.

    Args:
        session: SQLAlchemy session.
        query: Optional search query to filter emails.
        apply_privacy: Whether to create a privacy filter.

    Returns:
        Tuple of (emails list, privacy filter or None).
    """
    from sqlalchemy import select

    from mtk.core.config import PrivacyConfig
    from mtk.core.models import Email
    from mtk.core.privacy import PrivacyFilter
    from mtk.search import SearchEngine

    privacy_filter = None
    if apply_privacy:
        privacy_config = PrivacyConfig.load()
        privacy_filter = PrivacyFilter(privacy_config)

    if query:
        engine = SearchEngine(session)
        results = engine.search(query, limit=100000)
        emails = [r.email for r in results]
    else:
        emails = list(session.execute(select(Email)).scalars())

    return emails, privacy_filter


@export_app.command("json")
def export_json(
    output: Path = typer.Argument(..., help="Output file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    apply_privacy: bool = typer.Option(False, "--privacy", "-p", help="Apply privacy rules"),
    pretty: bool = typer.Option(True, "--pretty/--compact", help="Pretty print JSON"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to JSON format."""
    from mtk.export import JsonExporter

    db = get_db()
    with db.session() as session:
        emails, privacy_filter = _prepare_export(session, query, apply_privacy)
        exporter = JsonExporter(output, privacy_filter=privacy_filter, pretty=pretty)
        result = exporter.export(emails)

    if json_output:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")
        if result.emails_excluded:
            console.print(f"[yellow]Excluded {result.emails_excluded} emails (privacy)[/yellow]")


@export_app.command("mbox")
def export_mbox(
    output: Path = typer.Argument(..., help="Output file path"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    apply_privacy: bool = typer.Option(False, "--privacy", "-p", help="Apply privacy rules"),
    json: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to mbox format."""
    from mtk.export import MboxExporter

    db = get_db()
    with db.session() as session:
        emails, privacy_filter = _prepare_export(session, query, apply_privacy)
        exporter = MboxExporter(output, privacy_filter=privacy_filter)
        result = exporter.export(emails)

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}[/green]")
        if result.emails_excluded:
            console.print(f"[yellow]Excluded {result.emails_excluded} emails (privacy)[/yellow]")


@export_app.command("markdown")
def export_markdown(
    output: Path = typer.Argument(..., help="Output directory"),
    query: str | None = typer.Option(None, "--query", "-q", help="Search query to filter"),
    apply_privacy: bool = typer.Option(False, "--privacy", "-p", help="Apply privacy rules"),
    threads: bool = typer.Option(False, "--threads", "-t", help="Group by thread"),
    json: bool = typer.Option(False, "--json", "-j", help="Output result as JSON"),
) -> None:
    """Export emails to Markdown files."""
    from mtk.export import MarkdownExporter

    db = get_db()
    with db.session() as session:
        emails, privacy_filter = _prepare_export(session, query, apply_privacy)
        exporter = MarkdownExporter(output, privacy_filter=privacy_filter, group_by_thread=threads)
        result = exporter.export(emails)

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Exported {result.emails_exported} emails to {output}/[/green]")
        if result.emails_excluded:
            console.print(f"[yellow]Excluded {result.emails_excluded} emails (privacy)[/yellow]")


# === List Tags Command ===
@app.command("list-tags")
def list_tags(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List all tags in the archive."""
    from sqlalchemy import func, select

    from mtk.core.models import Tag, email_tags

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


# === Batch Tag Command ===
@app.command("tag-batch")
def tag_batch(
    query: str = typer.Argument(..., help="Search query to match emails"),
    add: list[str] | None = typer.Option(None, "--add", "-a", help="Tags to add"),
    remove: list[str] | None = typer.Option(None, "--remove", "-r", help="Tags to remove"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be changed"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add or remove tags from multiple emails matching a query.

    Example: mtk tag-batch "from:alice@example.com" --add work --add important
    """
    from sqlalchemy import select

    from mtk.core.models import Tag
    from mtk.search import SearchEngine

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
                    existing_tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                    if not existing_tag:
                        existing_tag = Tag(name=tag_name, source="mtk")
                        session.add(existing_tag)
                        session.flush()
                    if existing_tag not in email.tags:
                        email.tags.append(existing_tag)
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


# === Notmuch Commands ===
notmuch_app = typer.Typer(help="notmuch integration")
app.add_typer(notmuch_app, name="notmuch")


@notmuch_app.command("status")
def notmuch_status(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show notmuch sync status."""
    from mtk.integrations import NotmuchSync

    db = get_db()
    with db.session() as session:
        sync = NotmuchSync(session)
        status = sync.status()

    if json:
        print(json_lib.dumps(status, indent=2))
    else:
        available = "[green]Yes[/green]" if status.get("notmuch_available") else "[red]No[/red]"
        console.print(
            Panel.fit(
                f"""[bold]notmuch Integration Status[/bold]

Database path: {status.get("notmuch_path")}
Available: {available}
mtk emails: {status.get("mtk_emails", 0):,}
mtk tags: {status.get("mtk_tags", 0):,}
Common emails: {status.get("common_emails", "N/A")}""",
                title="notmuch",
            )
        )
        if status.get("error"):
            console.print(f"[red]Error: {status['error']}[/red]")


@notmuch_app.command("pull")
def notmuch_pull(
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace tags instead of merge"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import tags from notmuch to mtk."""
    from mtk.integrations import NotmuchSync

    db = get_db()
    with db.session() as session:
        sync = NotmuchSync(session)
        result = sync.pull(overwrite=overwrite)

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Processed {result.emails_processed} emails[/green]")
        console.print(f"  Tags added: {result.tags_added}")
        if result.tags_removed:
            console.print(f"  Tags removed: {result.tags_removed}")
        if result.errors:
            console.print(f"[yellow]Errors: {len(result.errors)}[/yellow]")


@notmuch_app.command("push")
def notmuch_push(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Export tags from mtk to notmuch."""
    from mtk.integrations import NotmuchSync

    db = get_db()
    with db.session() as session:
        sync = NotmuchSync(session)
        result = sync.push()

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Processed {result.emails_processed} emails[/green]")
        console.print(f"  Tags added: {result.tags_added}")
        if result.errors:
            console.print(f"[yellow]Errors: {len(result.errors)}[/yellow]")


@notmuch_app.command("sync")
def notmuch_sync(
    strategy: str = typer.Option("merge", "--strategy", "-s", help="merge, notmuch-wins, mtk-wins"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Bidirectional tag sync with notmuch."""
    from mtk.integrations import NotmuchSync

    db = get_db()
    with db.session() as session:
        sync = NotmuchSync(session)
        result = sync.sync(strategy=strategy)

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Synced {result.emails_processed} emails[/green]")
        console.print(f"  Tags added: {result.tags_added}")
        console.print(f"  Tags removed: {result.tags_removed}")
        if result.errors:
            console.print(f"[yellow]Errors: {len(result.errors)}[/yellow]")


@notmuch_app.command("import")
def notmuch_import_cmd(
    query: str = typer.Option("*", "--query", "-q", help="notmuch query to filter"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Import emails from notmuch database."""
    from mtk.integrations import NotmuchSync

    db = get_db()
    with db.session() as session:
        sync = NotmuchSync(session)
        result = sync.import_emails(query=query)

    if json:
        print(json_lib.dumps(result.to_dict(), indent=2))
    else:
        console.print(f"[green]Imported {result.emails_processed} emails[/green]")
        console.print(f"  Tags imported: {result.tags_added}")
        if result.errors:
            console.print(f"[yellow]Errors: {len(result.errors)}[/yellow]")


# === IMAP Commands ===
from mtk.cli.imap_cli import imap_app  # noqa: E402

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
        from mtk.mcp import run_server
    except ImportError:
        console.print("[red]MCP support requires the mcp package.[/red]")
        console.print("Install with: pip install mtk[mcp]")
        raise typer.Exit(1) from None

    if transport != "stdio":
        console.print(f"[red]Unsupported transport: {transport}[/red]")
        console.print("Currently only 'stdio' is supported.")
        raise typer.Exit(1)

    run_server()


# === Shell Command ===
@app.command()
def shell() -> None:
    """Start interactive shell mode.

    Provides a REPL interface for exploring and managing emails.
    Commands: inbox, search, show, thread, tag, next, prev, stats, quit
    """
    from mtk.cli.shell import run_shell

    db = get_db()
    run_shell(db)


if __name__ == "__main__":
    app()
