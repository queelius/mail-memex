"""IMAP CLI sub-app for mtk.

Commands for managing IMAP accounts and sync operations.
"""

from __future__ import annotations

import json as json_lib

import typer
from rich.console import Console
from rich.table import Table

from mtk.core.config import ImapAccountConfig, MtkConfig

imap_app = typer.Typer(help="IMAP email sync")
console = Console()


def _get_account(name: str) -> ImapAccountConfig:
    """Get an IMAP account by name from config."""
    config = MtkConfig.load()
    account = config.imap_accounts.get(name)
    if not account:
        console.print(f"[red]Account not found: {name}[/red]")
        console.print(f"Available accounts: {', '.join(config.imap_accounts.keys()) or '(none)'}")
        raise typer.Exit(1)
    return account


def _get_password(account: ImapAccountConfig) -> str:
    """Get password for account from keyring or prompt."""
    from mtk.imap.auth import AuthManager

    auth = AuthManager()
    password = auth.get_password(account)
    if not password:
        console.print(f"[yellow]No password stored for {account.name}[/yellow]")
        console.print("Run 'mtk imap auth' first, or enter password now:")
        password = typer.prompt("Password", hide_input=True)
    return password


@imap_app.command("add")
def imap_add(
    name: str = typer.Argument(..., help="Account name (e.g., 'work', 'personal')"),
    host: str = typer.Option(..., "--host", "-h", help="IMAP server host"),
    username: str = typer.Option(..., "--username", "-u", help="Username/email"),
    port: int = typer.Option(993, "--port", "-p", help="IMAP port"),
    provider: str = typer.Option("generic", "--provider", help="Provider: generic, gmail"),
    no_ssl: bool = typer.Option(False, "--no-ssl", help="Disable SSL"),
    folders: str | None = typer.Option("INBOX", "--folders", "-f", help="Comma-separated folders"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Add an IMAP account."""
    config = MtkConfig.load()

    folder_list = [f.strip() for f in (folders or "INBOX").split(",")]

    account = ImapAccountConfig(
        name=name,
        host=host,
        port=port,
        username=username,
        use_ssl=not no_ssl,
        provider=provider,
        folders=folder_list,
        oauth2=False,
    )

    config.imap_accounts[name] = account
    config.save()

    if json:
        print(json_lib.dumps({"added": name, "host": host, "username": username}, indent=2))
    else:
        console.print(f"[green]Added IMAP account: {name}[/green]")
        console.print(f"  Host: {host}:{port}")
        console.print(f"  Username: {username}")
        console.print(f"  Folders: {', '.join(folder_list)}")
        console.print("\n[dim]Run 'mtk imap auth {name}' to store credentials[/dim]")


@imap_app.command("auth")
def imap_auth(
    name: str = typer.Argument(..., help="Account name"),
    oauth2: bool = typer.Option(False, "--oauth2", help="Use Gmail OAuth2 flow"),
) -> None:
    """Store credentials for an IMAP account."""
    account = _get_account(name)

    if oauth2 or account.oauth2:
        from mtk.imap.auth import GmailOAuth2

        gmail_auth = GmailOAuth2(account)
        client_id = typer.prompt("Gmail OAuth2 Client ID")
        client_secret = typer.prompt("Gmail OAuth2 Client Secret", hide_input=True)
        token = gmail_auth.authorize(client_id, client_secret)
        if token:
            console.print(f"[green]OAuth2 credentials stored for {name}[/green]")
        else:
            console.print("[red]OAuth2 authorization failed[/red]")
            raise typer.Exit(1)
    else:
        from mtk.imap.auth import AuthManager

        password = typer.prompt("Password", hide_input=True)
        auth = AuthManager()
        auth.store_password(account, password)
        console.print(f"[green]Password stored for {name}[/green]")


@imap_app.command("remove")
def imap_remove(
    name: str = typer.Argument(..., help="Account name to remove"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Remove an IMAP account."""
    config = MtkConfig.load()

    if name not in config.imap_accounts:
        if json:
            print(json_lib.dumps({"error": f"Account not found: {name}"}, indent=2))
        else:
            console.print(f"[red]Account not found: {name}[/red]")
        raise typer.Exit(1)

    # Remove stored credentials
    account = config.imap_accounts[name]
    try:
        from mtk.imap.auth import AuthManager

        AuthManager().delete_password(account)
    except Exception:
        pass

    del config.imap_accounts[name]
    config.save()

    if json:
        print(json_lib.dumps({"removed": name}, indent=2))
    else:
        console.print(f"[green]Removed IMAP account: {name}[/green]")


@imap_app.command("accounts")
def imap_accounts(
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List configured IMAP accounts."""
    config = MtkConfig.load()

    if json:
        data = [
            {
                "name": name,
                "host": acct.host,
                "username": acct.username,
                "provider": acct.provider,
                "folders": acct.folders,
            }
            for name, acct in config.imap_accounts.items()
        ]
        print(json_lib.dumps(data, indent=2))
        return

    if not config.imap_accounts:
        console.print("[yellow]No IMAP accounts configured[/yellow]")
        console.print("[dim]Run 'mtk imap add' to add an account[/dim]")
        return

    table = Table(title="IMAP Accounts")
    table.add_column("Name", width=15)
    table.add_column("Host", width=25)
    table.add_column("Username", width=25)
    table.add_column("Provider", width=10)
    table.add_column("Folders", width=20)

    for name, acct in config.imap_accounts.items():
        table.add_row(
            name,
            f"{acct.host}:{acct.port}",
            acct.username,
            acct.provider,
            ", ".join(acct.folders),
        )

    console.print(table)


@imap_app.command("status")
def imap_status(
    name: str = typer.Argument(..., help="Account name"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Show sync status for an IMAP account."""
    from mtk.cli.main import get_db
    from mtk.imap import ImapSync

    account = _get_account(name)
    db = get_db()

    with db.session() as session:
        sync = ImapSync(session, account, "")  # No password needed for status
        status = sync.status()

    if json:
        print(json_lib.dumps(status, indent=2))
    else:
        console.print(f"[bold]IMAP Sync Status: {name}[/bold]")
        console.print(f"  Host: {status['host']}")

        if status["folders"]:
            for f in status["folders"]:
                console.print(f"\n  [cyan]{f['folder']}[/cyan]")
                console.print(f"    Last UID: {f['last_uid']}")
                console.print(f"    Messages: {f['message_count']}")
                console.print(f"    Last sync: {f['last_sync'] or 'Never'}")
        else:
            console.print("  [yellow]No sync history (run 'mtk imap sync' first)[/yellow]")


@imap_app.command("sync")
def imap_sync(
    name: str = typer.Argument(..., help="Account name"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Pull new emails from IMAP server."""
    from mtk.cli.main import get_db
    from mtk.imap import ImapSync

    account = _get_account(name)
    password = _get_password(account)
    db = get_db()

    with db.session() as session:
        sync = ImapSync(session, account, password)
        result = sync.sync()
        if json:
            print(json_lib.dumps(result.to_dict(), indent=2))
        else:
            for r in result.pull_results:
                console.print(
                    f"[green]{r.folder}: {r.new_emails} new, {r.updated_tags} updated[/green]"
                )
                if r.errors:
                    for err in r.errors:
                        console.print(f"  [yellow]Error: {err}[/yellow]")


@imap_app.command("folders")
def imap_folders(
    name: str = typer.Argument(..., help="Account name"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """List folders on IMAP server."""
    from mtk.imap.connection import ImapConnection

    account = _get_account(name)
    password = _get_password(account)

    with ImapConnection(account, password) as client:
        folders = client.list_folders()

    folder_names = []
    for _flags, _delimiter, name_bytes in folders:
        folder_name = name_bytes.decode() if isinstance(name_bytes, bytes) else str(name_bytes)
        folder_names.append(folder_name)

    if json:
        print(json_lib.dumps(folder_names, indent=2))
    else:
        console.print(f"[bold]Folders ({len(folder_names)}):[/bold]")
        for f in sorted(folder_names):
            console.print(f"  {f}")


@imap_app.command("test")
def imap_test(
    name: str = typer.Argument(..., help="Account name"),
    json: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
) -> None:
    """Test IMAP connection."""
    from mtk.imap.connection import ImapConnection

    account = _get_account(name)
    password = _get_password(account)

    try:
        with ImapConnection(account, password) as client:
            select_info = client.select_folder("INBOX", readonly=True)
            message_count = select_info.get(b"EXISTS", 0)
            success = True
            error = None
    except Exception as e:
        success = False
        message_count = 0
        error = str(e)

    if json:
        print(
            json_lib.dumps(
                {
                    "account": name,
                    "success": success,
                    "message_count": message_count,
                    "error": error,
                },
                indent=2,
            )
        )
    else:
        if success:
            console.print(f"[green]Connected to {account.host} successfully[/green]")
            console.print(f"  INBOX messages: {message_count}")
        else:
            console.print(f"[red]Connection failed: {error}[/red]")
