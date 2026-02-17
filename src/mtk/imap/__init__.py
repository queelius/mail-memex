"""IMAP sync for mtk — bidirectional Gmail/IMAP sync.

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
    from mtk.imap.push import PushResult


@dataclass
class SyncResult:
    """Combined result of pull + push sync."""

    account: str = ""
    pull_results: list[PullResult] = field(default_factory=list)
    push_result: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "pull_results": [r.to_dict() for r in self.pull_results],
            "push_result": self.push_result,
            "errors": self.errors,
        }


class ImapSync:
    """Orchestrator for IMAP sync operations.

    Coordinates pull and push sync for a single account.
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
        """Full bidirectional sync: pull then push."""
        result = SyncResult(account=self.account.name)

        # Pull first
        pull_results = self.pull_only()
        result.pull_results = pull_results

        # Then push
        push_result = self.push_only()
        result.push_result = push_result.to_dict()

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

    def push_only(self) -> PushResult:
        """Push pending tag changes to IMAP server."""
        from mtk.imap.connection import ImapConnection
        from mtk.imap.mapping import TagMapper
        from mtk.imap.push import PushSync

        tag_mapper = TagMapper(is_gmail=self.account.provider == "gmail")
        push_sync = PushSync(self.session, self.account, tag_mapper)
        result: PushResult | None = None

        with ImapConnection(self.account, self.password) as client:
            result = push_sync.push(client)

        assert result is not None
        return result

    def status(self) -> dict[str, Any]:
        """Get sync status for all folders."""
        from sqlalchemy import select

        from mtk.core.models import ImapPendingPush, ImapSyncState

        states = list(
            self.session.execute(
                select(ImapSyncState).where(ImapSyncState.account_name == self.account.name)
            ).scalars()
        )

        pending_count = self.session.execute(
            select(ImapPendingPush).where(ImapPendingPush.account_name == self.account.name)
        ).scalars()

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
            "pending_push": sum(1 for _ in pending_count),
        }
