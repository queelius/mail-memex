"""IMAP sync for mtk — pull-only Gmail/IMAP sync.

Usage:
    from mtk.imap import ImapSync
    sync = ImapSync(session, account_config, password)
    result = sync.sync()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mtk.imap.account import ImapAccountConfig
    from mtk.imap.pull import PullResult


@dataclass
class SyncResult:
    """Result of a pull sync."""

    account: str = ""
    pull_results: list[PullResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "pull_results": [r.to_dict() for r in self.pull_results],
            "errors": self.errors,
        }


class ImapSync:
    """Orchestrator for IMAP sync operations.

    Coordinates pull sync for a single account.
    """

    def __init__(
        self,
        session: Session,
        account: ImapAccountConfig,
        password: str,
    ) -> None:
        self.session = session
        self.account = account
        self.password = password

    def sync(self) -> SyncResult:
        """Pull new messages from IMAP."""
        result = SyncResult(account=self.account.name)
        result.pull_results = self.pull_only()
        return result

    def pull_only(self) -> list[PullResult]:
        """Pull new messages from all configured folders."""
        from mtk.imap.connection import ImapConnection
        from mtk.imap.mapping import TagMapper
        from mtk.imap.pull import PullSync

        tag_mapper = TagMapper(is_gmail=self.account.provider == "gmail")
        pull_sync = PullSync(self.session, self.account, tag_mapper)

        results = []
        with ImapConnection(self.account, self.password) as client:
            for folder in self.account.folders:
                result = pull_sync.pull_folder(client, folder)
                results.append(result)

        return results

    def status(self) -> dict[str, Any]:
        """Get sync status for all folders."""
        from sqlalchemy import select

        from mtk.core.models import ImapSyncState

        states = list(
            self.session.execute(
                select(ImapSyncState).where(ImapSyncState.account_name == self.account.name)
            ).scalars()
        )

        return {
            "account": self.account.name,
            "host": self.account.host,
            "folders": [
                {
                    "folder": s.folder,
                    "last_uid": s.last_uid,
                    "uid_validity": s.uid_validity,
                    "message_count": s.message_count,
                    "last_sync": s.last_sync.isoformat() if s.last_sync else None,
                }
                for s in states
            ],
        }
