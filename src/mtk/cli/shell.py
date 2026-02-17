"""Interactive shell mode for mtk.

Provides a REPL interface for exploring and managing emails.
"""

from __future__ import annotations

import cmd
import contextlib
import shlex
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mtk.cli.main import format_date

if TYPE_CHECKING:
    from mtk.core.database import Database
    from mtk.core.models import Email

console = Console()


class MtkShell(cmd.Cmd):
    """Interactive shell for mtk email archive.

    Provides navigation, search, tagging, and display commands.
    """

    intro = """
╔════════════════════════════════════════════════════════════╗
║  mtk interactive shell                                     ║
║  Type 'help' for commands, 'quit' to exit                  ║
╚════════════════════════════════════════════════════════════╝
"""
    prompt = "mtk> "

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.current_list: list[Email] = []
        self.current_email: Email | None = None
        self.current_index: int = 0

    def emptyline(self) -> bool:
        """Do nothing on empty line."""
        return False

    def default(self, line: str) -> bool:
        """Handle unknown commands."""
        console.print(f"[red]Unknown command: {line}[/red]")
        console.print("Type 'help' for available commands")
        return False

    # === Navigation Commands ===

    def do_inbox(self, arg: str) -> None:
        """Show recent emails. Usage: inbox [limit]"""
        from sqlalchemy import select

        from mtk.core.models import Email

        limit = 20
        if arg:
            try:
                limit = int(arg)
            except ValueError:
                console.print("[red]Invalid limit[/red]")
                return

        with self.db.session() as session:
            stmt = select(Email).order_by(Email.date.desc()).limit(limit)
            self.current_list = list(session.execute(stmt).scalars())

            if not self.current_list:
                console.print("[yellow]No emails found[/yellow]")
                return

            self._display_list()

    def do_search(self, arg: str) -> None:
        """Search emails. Usage: search <query>"""
        if not arg:
            console.print("[red]Usage: search <query>[/red]")
            return

        from mtk.search import SearchEngine

        with self.db.session() as session:
            engine = SearchEngine(session)
            results = engine.search(arg, limit=50)

            self.current_list = [r.email for r in results]

            if not self.current_list:
                console.print("[yellow]No results found[/yellow]")
                return

            self._display_list(show_score=True, scores=[r.score for r in results])

    def do_show(self, arg: str) -> None:
        """Show email by index or message ID. Usage: show <index|id>"""
        if not arg:
            if self.current_email:
                self._display_email(self.current_email)
            else:
                console.print("[red]Usage: show <index|id>[/red]")
            return

        email = self._get_email(arg)
        if email:
            self.current_email = email
            self._display_email(email)

    def do_thread(self, arg: str) -> None:
        """Show thread for current or specified email. Usage: thread [index|id]"""
        from sqlalchemy import select

        from mtk.core.models import Email

        email = self._get_email(arg) if arg else self.current_email

        if not email:
            console.print("[red]No email selected. Use: thread <index|id>[/red]")
            return

        if not email.thread_id:
            console.print("[yellow]Email is not part of a thread[/yellow]")
            return

        with self.db.session() as session:
            stmt = select(Email).where(Email.thread_id == email.thread_id).order_by(Email.date)
            thread_emails = list(session.execute(stmt).scalars())

            console.print(
                Panel.fit(
                    f"[bold]Thread:[/bold] {email.subject or 'No subject'}\n"
                    f"[bold]Messages:[/bold] {len(thread_emails)}",
                    title="Thread",
                )
            )

            for i, e in enumerate(thread_emails, 1):
                marker = "→ " if e.message_id == email.message_id else "  "
                console.print(
                    f"{marker}[dim]{i}.[/dim] {format_date(e.date)} "
                    f"[cyan]{e.from_name or e.from_addr}[/cyan]"
                )

    def do_next(self, arg: str) -> None:
        """Go to next email in current list."""
        if not self.current_list:
            console.print("[yellow]No email list. Use 'inbox' or 'search' first.[/yellow]")
            return

        self.current_index = min(self.current_index + 1, len(self.current_list) - 1)
        self.current_email = self.current_list[self.current_index]
        self._display_email(self.current_email)

    def do_prev(self, arg: str) -> None:
        """Go to previous email in current list."""
        if not self.current_list:
            console.print("[yellow]No email list. Use 'inbox' or 'search' first.[/yellow]")
            return

        self.current_index = max(self.current_index - 1, 0)
        self.current_email = self.current_list[self.current_index]
        self._display_email(self.current_email)

    # === Tagging Commands ===

    def do_tag(self, arg: str) -> None:
        """Add/remove tags. Usage: tag +add -remove"""
        if not self.current_email:
            console.print("[red]No email selected[/red]")
            return

        if not arg:
            tags = [t.name for t in self.current_email.tags] if self.current_email.tags else []
            console.print(f"Tags: {', '.join(tags) or '(none)'}")
            return

        from sqlalchemy import select

        from mtk.core.models import Tag

        parts = shlex.split(arg)
        add_tags = [p[1:] for p in parts if p.startswith("+")]
        remove_tags = [p[1:] for p in parts if p.startswith("-")]

        with self.db.session() as session:
            # Re-attach email to session
            from mtk.core.models import Email

            email = session.execute(
                select(Email).where(Email.message_id == self.current_email.message_id)
            ).scalar()

            if not email:
                console.print("[red]Email not found[/red]")
                return

            for tag_name in add_tags:
                tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                if not tag:
                    tag = Tag(name=tag_name, source="mtk")
                    session.add(tag)
                if tag not in email.tags:
                    email.tags.append(tag)

            for tag_name in remove_tags:
                tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
                if tag and tag in email.tags:
                    email.tags.remove(tag)

            session.commit()

            tags = [t.name for t in email.tags]
            console.print(f"[green]Tags updated:[/green] {', '.join(tags) or '(none)'}")

    def do_tags(self, arg: str) -> None:
        """List all tags. Usage: tags"""
        from sqlalchemy import func, select

        from mtk.core.models import Tag, email_tags

        with self.db.session() as session:
            stmt = (
                select(Tag.name, func.count(email_tags.c.email_id).label("count"))
                .outerjoin(email_tags, Tag.id == email_tags.c.tag_id)
                .group_by(Tag.id)
                .order_by(func.count(email_tags.c.email_id).desc())
            )
            results = session.execute(stmt).all()

            if not results:
                console.print("[yellow]No tags found[/yellow]")
                return

            table = Table(title="Tags")
            table.add_column("Tag", width=30)
            table.add_column("Count", justify="right", width=8)

            for name, count in results:
                table.add_row(name, str(count))

            console.print(table)

    # === Info Commands ===

    def do_stats(self, arg: str) -> None:
        """Show archive statistics."""
        from sqlalchemy import func, select

        from mtk.core.models import Email, Person, Tag, Thread

        with self.db.session() as session:
            email_count = session.execute(select(func.count(Email.id))).scalar() or 0
            person_count = session.execute(select(func.count(Person.id))).scalar() or 0
            thread_count = session.execute(select(func.count(Thread.id))).scalar() or 0
            tag_count = session.execute(select(func.count(Tag.id))).scalar() or 0

            console.print(
                Panel.fit(
                    f"""[bold]Archive Statistics[/bold]

📧 Emails:  {email_count:,}
👥 People:  {person_count:,}
💬 Threads: {thread_count:,}
🏷️  Tags:    {tag_count:,}""",
                    title="mtk",
                )
            )

    def do_people(self, arg: str) -> None:
        """List top correspondents. Usage: people [limit]"""
        from mtk.people import RelationshipAnalyzer

        limit = 10
        if arg:
            with contextlib.suppress(ValueError):
                limit = int(arg)

        with self.db.session() as session:
            analyzer = RelationshipAnalyzer(session)
            stats = analyzer.get_top_correspondents(limit=limit)

            if not stats:
                console.print("[yellow]No correspondents found[/yellow]")
                return

            table = Table(title="Top Correspondents")
            table.add_column("Name", width=25)
            table.add_column("Email", width=30)
            table.add_column("Emails", justify="right", width=8)

            for s in stats:
                table.add_row(s.person_name[:24], s.primary_email[:29], str(s.total_emails))

            console.print(table)

    # === Exit Commands ===

    def do_quit(self, arg: str) -> bool:
        """Exit the shell."""
        console.print("[dim]Goodbye![/dim]")
        return True

    do_exit = do_quit
    do_q = do_quit

    def do_EOF(self, arg: str) -> bool:  # noqa: N802
        """Handle Ctrl+D."""
        console.print()
        return self.do_quit(arg)

    # === Helper Methods ===

    def _get_email(self, arg: str) -> Email | None:
        """Get email by index or message ID."""
        from sqlalchemy import select

        from mtk.core.models import Email

        # Try as index
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(self.current_list):
                self.current_index = idx
                return self.current_list[idx]
            console.print(f"[red]Index out of range (1-{len(self.current_list)})[/red]")
            return None

        # Try as message ID
        with self.db.session() as session:
            email = session.execute(select(Email).where(Email.message_id.contains(arg))).scalar()

            if email:
                return email

            console.print(f"[red]Email not found: {arg}[/red]")
            return None

    def _display_list(self, show_score: bool = False, scores: list[float] | None = None) -> None:
        """Display current email list."""
        table = Table(title=f"Emails ({len(self.current_list)})")
        table.add_column("#", style="dim", width=3)
        table.add_column("Date", style="cyan", width=16)
        table.add_column("From", width=25)
        table.add_column("Subject")
        if show_score:
            table.add_column("Score", justify="right", width=6)

        for i, email in enumerate(self.current_list, 1):
            row = [
                str(i),
                format_date(email.date),
                (email.from_name or email.from_addr or "")[:24],
                (email.subject or "(no subject)")[:50],
            ]
            if show_score and scores:
                row.append(f"{scores[i - 1]:.2f}")
            table.add_row(*row)

        console.print(table)
        console.print("[dim]Use 'show <#>' to view an email[/dim]")

    def _display_email(self, email: Email) -> None:
        """Display a single email."""
        tags = [t.name for t in email.tags] if email.tags else []

        console.print(
            Panel.fit(
                f"""[bold]From:[/bold] {email.from_name or ""} <{email.from_addr}>
[bold]Date:[/bold] {format_date(email.date)}
[bold]Subject:[/bold] {email.subject or "(no subject)"}
[bold]Thread:[/bold] {email.thread_id or "N/A"}
[bold]Tags:[/bold] {", ".join(tags) or "(none)"}""",
                title="Email",
            )
        )

        console.print()
        console.print(email.body_text or "(no content)")


def run_shell(db: Database) -> None:
    """Run the interactive shell."""
    shell = MtkShell(db)
    shell.cmdloop()
