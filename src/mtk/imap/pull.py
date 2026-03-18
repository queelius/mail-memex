"""Pull sync: fetch new/changed messages from IMAP server."""

from __future__ import annotations

import email as email_lib
import email.utils as email_utils
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from mtk.core.models import Email, ImapSyncState, Tag

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mtk.imap.account import ImapAccountConfig
    from mtk.imap.mapping import TagMapper


@dataclass
class PullResult:
    """Result of a pull sync operation."""

    account: str = ""
    folder: str = ""
    fetched: int = 0
    new_emails: int = 0
    updated_tags: int = 0
    errors: list[str] = field(default_factory=list)
    uid_validity_reset: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "folder": self.folder,
            "fetched": self.fetched,
            "new_emails": self.new_emails,
            "updated_tags": self.updated_tags,
            "errors": self.errors,
            "uid_validity_reset": self.uid_validity_reset,
        }


class PullSync:
    """Incremental pull sync from IMAP server.

    Uses UIDVALIDITY and last_uid to only fetch new messages.
    Handles UIDVALIDITY reset by clearing state and doing full re-sync.
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

    def pull_folder(self, client: Any, folder: str) -> PullResult:
        """Pull new messages from a single IMAP folder.

        Args:
            client: Connected IMAPClient instance.
            folder: IMAP folder name.

        Returns:
            PullResult with sync statistics.
        """
        result = PullResult(account=self.account.name, folder=folder)

        try:
            select_info = client.select_folder(folder, readonly=True)
        except Exception as e:
            result.errors.append(f"Failed to select folder {folder}: {e}")
            return result

        # Get current UIDVALIDITY
        uid_validity = select_info.get(b"UIDVALIDITY", 0)

        # Get or create sync state
        state = self._get_sync_state(folder)

        # Check UIDVALIDITY
        if state.uid_validity is not None and state.uid_validity != uid_validity:
            # UIDVALIDITY changed — full re-sync needed
            result.uid_validity_reset = True
            state.last_uid = 0
            state.highest_modseq = None
            # Remove IMAP tracking from existing emails in this folder
            self._clear_folder_state(folder)

        state.uid_validity = uid_validity

        # Fetch new messages (UID > last_uid)
        search_criteria = f"UID {state.last_uid + 1}:*" if state.last_uid > 0 else "ALL"

        try:
            uids = client.search(search_criteria)
        except Exception as e:
            result.errors.append(f"Search failed: {e}")
            return result

        # Filter out UIDs we already have
        uids = [uid for uid in uids if uid > state.last_uid]

        if not uids:
            state.last_sync = datetime.utcnow()
            self.session.commit()
            return result

        result.fetched = len(uids)

        # Fetch message data in batches
        batch_size = 50
        for i in range(0, len(uids), batch_size):
            batch_uids = uids[i : i + batch_size]
            try:
                fetch_items = ["UID", "FLAGS", "ENVELOPE", "BODY.PEEK[HEADER]", "BODY.PEEK[TEXT]"]
                if self.account.provider == "gmail":
                    fetch_items.extend(["X-GM-LABELS", "X-GM-THRID"])

                fetch_data = client.fetch(batch_uids, fetch_items)
            except Exception as e:
                result.errors.append(f"Fetch failed for UIDs {batch_uids[0]}-{batch_uids[-1]}: {e}")
                continue

            for uid, data in fetch_data.items():
                try:
                    self._process_message(uid, data, folder, result)
                except Exception as e:
                    result.errors.append(f"Failed to process UID {uid}: {e}")

        # Update sync state
        if uids:
            state.last_uid = max(uids)
        state.message_count = result.new_emails
        state.last_sync = datetime.utcnow()

        # Update HIGHESTMODSEQ if server supports CONDSTORE
        modseq = select_info.get(b"HIGHESTMODSEQ")
        if modseq:
            state.highest_modseq = modseq

        self.session.commit()
        return result

    def _process_message(
        self,
        uid: int,
        data: dict[bytes, Any],
        folder: str,
        result: PullResult,
    ) -> None:
        """Process a single fetched IMAP message."""
        # Parse headers
        header_bytes = data.get(b"BODY[HEADER]", b"")
        text_bytes = data.get(b"BODY[TEXT]", b"")

        if isinstance(header_bytes, bytes):
            header_text = header_bytes.decode("utf-8", errors="replace")
        else:
            header_text = str(header_bytes)

        msg = email_lib.message_from_string(header_text)

        message_id = msg.get("Message-ID", "").strip("<>")
        if not message_id:
            message_id = f"imap-{self.account.name}-{folder}-{uid}"

        # Check if email already exists
        existing = self.session.execute(
            select(Email).where(Email.message_id == message_id)
        ).scalar()

        if existing:
            # Update IMAP tracking
            existing.imap_uid = uid
            existing.imap_account = self.account.name
            existing.imap_folder = folder

            # Update recipient columns
            to_list = email_utils.getaddresses([msg.get("To", "")])
            cc_list = email_utils.getaddresses([msg.get("Cc", "")])
            bcc_list = email_utils.getaddresses([msg.get("Bcc", "")])
            existing.to_addrs = ",".join(addr for _, addr in to_list if addr) or None
            existing.cc_addrs = ",".join(addr for _, addr in cc_list if addr) or None
            existing.bcc_addrs = ",".join(addr for _, addr in bcc_list if addr) or None

            # Update tags from flags
            flags = data.get(b"FLAGS", ())
            flag_strs = [f.decode() if isinstance(f, bytes) else str(f) for f in flags]
            labels = None
            if self.account.provider == "gmail":
                from mtk.imap.gmail import GmailExtensions

                labels = GmailExtensions.extract_labels(data)

            new_tags = self.tag_mapper.imap_to_tags(flag_strs, labels)
            self._apply_tags(existing, new_tags)
            result.updated_tags += 1
        else:
            # Create new email
            body_text = ""
            if isinstance(text_bytes, bytes):
                body_text = text_bytes.decode("utf-8", errors="replace")
            elif text_bytes:
                body_text = str(text_bytes)

            from_header = msg.get("From", "")
            from_addr = email_lib.utils.parseaddr(from_header)[1]
            from_name = email_lib.utils.parseaddr(from_header)[0]

            date_str = msg.get("Date", "")
            try:
                date_tuple = email_lib.utils.parsedate_to_datetime(date_str)
            except Exception:
                date_tuple = datetime.utcnow()

            to_list = email_utils.getaddresses([msg.get("To", "")])
            cc_list = email_utils.getaddresses([msg.get("Cc", "")])
            bcc_list = email_utils.getaddresses([msg.get("Bcc", "")])

            new_email = Email(
                message_id=message_id,
                from_addr=from_addr or "unknown@unknown",
                from_name=from_name or None,
                subject=msg.get("Subject"),
                date=date_tuple,
                in_reply_to=msg.get("In-Reply-To", "").strip("<>") or None,
                references=msg.get("References"),
                body_text=body_text,
                body_preview=body_text[:500] if body_text else None,
                imap_uid=uid,
                imap_account=self.account.name,
                imap_folder=folder,
                to_addrs=",".join(addr for _, addr in to_list if addr) or None,
                cc_addrs=",".join(addr for _, addr in cc_list if addr) or None,
                bcc_addrs=",".join(addr for _, addr in bcc_list if addr) or None,
            )
            self.session.add(new_email)
            self.session.flush()

            # Apply tags from flags
            flags = data.get(b"FLAGS", ())
            flag_strs = [f.decode() if isinstance(f, bytes) else str(f) for f in flags]
            labels = None
            if self.account.provider == "gmail":
                from mtk.imap.gmail import GmailExtensions

                labels = GmailExtensions.extract_labels(data)

            new_tags = self.tag_mapper.imap_to_tags(flag_strs, labels)
            self._apply_tags(new_email, new_tags)
            result.new_emails += 1

    def _apply_tags(self, email_obj: Email, tag_names: set[str]) -> None:
        """Apply tags to an email, creating Tag objects as needed."""
        for tag_name in tag_names:
            tag = self.session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
            if not tag:
                tag = Tag(name=tag_name, source="imap")
                self.session.add(tag)
                self.session.flush()
            if tag not in email_obj.tags:
                email_obj.tags.append(tag)

    def _get_sync_state(self, folder: str) -> ImapSyncState:
        """Get or create sync state for a folder."""
        state = self.session.execute(
            select(ImapSyncState).where(
                ImapSyncState.account_name == self.account.name,
                ImapSyncState.folder == folder,
            )
        ).scalar()

        if not state:
            state = ImapSyncState(
                account_name=self.account.name,
                folder=folder,
            )
            self.session.add(state)
            self.session.flush()

        return state

    def _clear_folder_state(self, folder: str) -> None:
        """Clear IMAP tracking for emails in a folder (UIDVALIDITY reset)."""
        emails = (
            self.session.execute(
                select(Email).where(
                    Email.imap_account == self.account.name,
                    Email.imap_folder == folder,
                )
            )
            .scalars()
            .all()
        )

        for email_obj in emails:
            email_obj.imap_uid = None
            email_obj.imap_folder = None
