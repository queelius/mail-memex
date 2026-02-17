"""Push sync: send tag changes back to IMAP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from mtk.core.models import Email, ImapPendingPush

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mtk.imap.account import ImapAccountConfig
    from mtk.imap.mapping import TagMapper


@dataclass
class PushResult:
    """Result of a push sync operation."""

    account: str = ""
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "errors": self.errors,
        }


def queue_tag_change(
    session: Session,
    email_id: int,
    account_name: str,
    action: str,
    tag_name: str,
) -> None:
    """Queue a tag change for push to IMAP on next sync.

    Args:
        session: Database session.
        email_id: ID of the email.
        account_name: IMAP account name.
        action: "add" or "remove".
        tag_name: Tag name to add or remove.
    """
    pending = ImapPendingPush(
        email_id=email_id,
        account_name=account_name,
        action=action,
        tag_name=tag_name,
    )
    session.add(pending)


class PushSync:
    """Process pending tag changes and push to IMAP server.

    Reads from ImapPendingPush queue and applies changes
    to the IMAP server using flag/label operations.
    """

    def __init__(
        self,
        session: Session,
        account: ImapAccountConfig,
        tag_mapper: TagMapper,
    ) -> None:
        self.session = session
        self.account = account
        self.tag_mapper = tag_mapper

    def push(self, client: Any) -> PushResult:
        """Process all pending push items for this account.

        Args:
            client: Connected IMAPClient instance.

        Returns:
            PushResult with sync statistics.
        """
        result = PushResult(account=self.account.name)

        # Get pending items for this account
        pending_items = list(
            self.session.execute(
                select(ImapPendingPush).where(ImapPendingPush.account_name == self.account.name)
            ).scalars()
        )

        if not pending_items:
            return result

        # Group by email for efficiency
        by_email: dict[int, list[ImapPendingPush]] = {}
        for item in pending_items:
            by_email.setdefault(item.email_id, []).append(item)

        for email_id, items in by_email.items():
            result.processed += len(items)

            # Get the email to find IMAP UID and folder
            email_obj = self.session.get(Email, email_id)
            if not email_obj or not email_obj.imap_uid or not email_obj.imap_folder:
                for item in items:
                    result.errors.append(f"Email {email_id} not tracked by IMAP, skipping")
                    self.session.delete(item)
                    result.failed += len(items)
                continue

            try:
                client.select_folder(email_obj.imap_folder)
            except Exception as e:
                for item in items:
                    result.errors.append(f"Cannot select folder {email_obj.imap_folder}: {e}")
                    self.session.delete(item)
                result.failed += len(items)
                continue

            # Process add and remove operations
            add_tags = set()
            remove_tags = set()
            for item in items:
                if item.action == "add":
                    add_tags.add(item.tag_name)
                elif item.action == "remove":
                    remove_tags.add(item.tag_name)

            try:
                # Convert tags to IMAP flags
                if add_tags:
                    flags_to_add = self.tag_mapper.tags_to_imap_flags(add_tags)
                    if flags_to_add:
                        client.add_flags([email_obj.imap_uid], flags_to_add)

                    # Gmail labels
                    if self.account.provider == "gmail":
                        labels_to_add = self.tag_mapper.tags_to_gmail_labels(add_tags)
                        if labels_to_add:
                            client.add_gmail_labels([email_obj.imap_uid], labels_to_add)

                if remove_tags:
                    flags_to_remove = self.tag_mapper.tags_to_imap_flags(remove_tags)
                    if flags_to_remove:
                        client.remove_flags([email_obj.imap_uid], flags_to_remove)

                    if self.account.provider == "gmail":
                        labels_to_remove = self.tag_mapper.tags_to_gmail_labels(remove_tags)
                        if labels_to_remove:
                            client.remove_gmail_labels([email_obj.imap_uid], labels_to_remove)

                result.succeeded += len(items)
            except Exception as e:
                result.errors.append(f"Failed to push changes for UID {email_obj.imap_uid}: {e}")
                result.failed += len(items)

            # Remove processed items regardless of success/failure
            for item in items:
                self.session.delete(item)

        self.session.commit()
        return result
