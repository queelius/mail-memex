"""Pull sync: fetch new/changed messages from IMAP server."""

from __future__ import annotations

import email as email_lib
import email.utils as email_utils
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from mail_memex.core.models import Email, ImapSyncState, Tag
from mail_memex.core.recipients import parse_header
from mail_memex.importers.parser import clean_message_id


def _normalize_references(raw: str | None) -> str | None:
    """Split a References header into tokens and re-join space-separated
    without angle brackets — matching what the file/mbox importers store."""
    if not raw:
        return None
    parts = [clean_message_id(tok) for tok in raw.split()]
    cleaned = [p for p in parts if p]
    return " ".join(cleaned) if cleaned else None

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mail_memex.imap.account import ImapAccountConfig
    from mail_memex.imap.mapping import TagMapper


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
            # No new UIDs, but still refresh message_count in case prior
            # pulls or soft-deletes changed the live count.
            state.message_count = self._count_emails_in_folder(folder)
            state.last_sync = datetime.now(UTC)
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

        # Update sync state. message_count is the CUMULATIVE count of live
        # emails we have for this (account, folder), derived from the
        # authoritative emails table — not incremented per-pull. This
        # survives UIDVALIDITY resets, re-imports, and soft-deletes.
        if uids:
            state.last_uid = max(uids)
        state.message_count = self._count_emails_in_folder(folder)
        state.last_sync = datetime.now(UTC)

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
        header_bytes = data.get(b"BODY[HEADER]", b"")
        header_text = (
            header_bytes.decode("utf-8", errors="replace")
            if isinstance(header_bytes, bytes)
            else str(header_bytes)
        )
        msg = email_lib.message_from_string(header_text)

        message_id = clean_message_id(msg.get("Message-ID"))
        if not message_id:
            message_id = f"imap-{self.account.name}-{folder}-{uid}"

        to_header = msg.get("To", "")
        cc_header = msg.get("Cc", "")
        bcc_header = msg.get("Bcc", "")
        to_addrs, cc_addrs, bcc_addrs = (
            self._join_addrs(h) for h in (to_header, cc_header, bcc_header)
        )

        existing = self.session.execute(
            select(Email).where(Email.message_id == message_id)
        ).scalar()

        if existing:
            existing.imap_uid = uid
            existing.imap_account = self.account.name
            existing.imap_folder = folder
            existing.to_addrs = to_addrs
            existing.cc_addrs = cc_addrs
            existing.bcc_addrs = bcc_addrs
            # Keep normalized recipients in sync with the refreshed CSVs.
            existing.recipients.clear()
            existing.recipients.extend(parse_header(to_header, "to"))
            existing.recipients.extend(parse_header(cc_header, "cc"))
            existing.recipients.extend(parse_header(bcc_header, "bcc"))
            target = existing
            result.updated_tags += 1
        else:
            text_bytes = data.get(b"BODY[TEXT]", b"")
            body_text = (
                text_bytes.decode("utf-8", errors="replace")
                if isinstance(text_bytes, bytes)
                else (str(text_bytes) if text_bytes else "")
            )

            from_name, from_addr = email_lib.utils.parseaddr(msg.get("From", ""))

            try:
                date_tuple = email_lib.utils.parsedate_to_datetime(msg.get("Date", ""))
            except Exception:
                date_tuple = datetime.now(UTC)

            target = Email(
                message_id=message_id,
                from_addr=from_addr or "unknown@unknown",
                from_name=from_name or None,
                subject=msg.get("Subject"),
                date=date_tuple,
                in_reply_to=clean_message_id(msg.get("In-Reply-To")),
                # Clean each token so the stored form matches what the
                # file/mbox importers produce: space-joined, no angle
                # brackets. Threading relies on this invariant.
                references=_normalize_references(msg.get("References")),
                body_text=body_text,
                body_preview=body_text[:500] if body_text else None,
                imap_uid=uid,
                imap_account=self.account.name,
                imap_folder=folder,
                to_addrs=to_addrs,
                cc_addrs=cc_addrs,
                bcc_addrs=bcc_addrs,
            )
            target.recipients.extend(parse_header(to_header, "to"))
            target.recipients.extend(parse_header(cc_header, "cc"))
            target.recipients.extend(parse_header(bcc_header, "bcc"))
            self.session.add(target)
            self.session.flush()
            result.new_emails += 1

        self._apply_tags(target, self._tags_from_fetch(data))

    @staticmethod
    def _join_addrs(header: str) -> str | None:
        """Extract addresses from a To/Cc/Bcc header and join with commas."""
        return ",".join(addr for _, addr in email_utils.getaddresses([header]) if addr) or None

    def _tags_from_fetch(self, data: dict[bytes, Any]) -> set[str]:
        """Derive mail-memex tags from an IMAP FETCH response (flags + Gmail labels)."""
        flags = data.get(b"FLAGS", ())
        flag_strs = [f.decode() if isinstance(f, bytes) else str(f) for f in flags]
        labels = None
        if self.account.provider == "gmail":
            from mail_memex.imap.gmail import GmailExtensions

            labels = GmailExtensions.extract_labels(data)
        return self.tag_mapper.imap_to_tags(flag_strs, labels)

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

    def _count_emails_in_folder(self, folder: str) -> int:
        """Count live (non-archived) emails for this account+folder. Single
        source of truth for ImapSyncState.message_count — incremented
        counters drift across pulls, UIDVALIDITY resets, and soft-deletes."""
        count = self.session.execute(
            select(func.count(Email.id)).where(
                Email.imap_account == self.account.name,
                Email.imap_folder == folder,
                Email.archived_at.is_(None),
            )
        ).scalar()
        return count or 0

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
