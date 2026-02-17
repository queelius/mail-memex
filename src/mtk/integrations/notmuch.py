"""notmuch integration for mtk.

Provides bidirectional tag synchronization between mtk and notmuch databases.
Requires the notmuch2 Python package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mtk.core.models import Tag


@dataclass
class NotmuchSyncResult:
    """Result of a sync operation."""

    operation: str  # pull, push, sync
    emails_processed: int = 0
    tags_added: int = 0
    tags_removed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        result = {
            "operation": self.operation,
            "emails_processed": self.emails_processed,
            "tags_added": self.tags_added,
            "tags_removed": self.tags_removed,
        }
        if self.errors:
            result["errors"] = self.errors
        return result


class NotmuchSync:
    """Synchronize tags between mtk and notmuch.

    Usage:
        sync = NotmuchSync(session, notmuch_db_path)
        result = sync.pull()  # Import tags from notmuch
        result = sync.push()  # Export tags to notmuch
        result = sync.sync()  # Bidirectional merge
    """

    # Tags to skip during sync (notmuch internal tags)
    SKIP_TAGS = {"new", "signed", "encrypted", "passed", "replied", "attachment"}

    def __init__(
        self,
        session: Session,
        notmuch_path: Path | None = None,
        tag_prefix: str = "",
    ) -> None:
        """Initialize notmuch sync.

        Args:
            session: SQLAlchemy session for mtk database.
            notmuch_path: Path to notmuch database (default: ~/.mail).
            tag_prefix: Prefix to add to tags synced from notmuch.
        """
        self.session = session
        self.notmuch_path = notmuch_path or Path.home() / ".mail"
        self.tag_prefix = tag_prefix
        self._db = None

    def _get_notmuch_db(self, mode: str = "ro") -> Any:
        """Get notmuch database connection.

        Args:
            mode: "ro" for read-only, "rw" for read-write.
        """
        try:
            import notmuch2
        except ImportError:
            raise ImportError(
                "notmuch2 package required. Install with: pip install notmuch2"
            ) from None

        if mode == "rw":
            return notmuch2.Database(str(self.notmuch_path), mode=notmuch2.Database.MODE.READ_WRITE)
        return notmuch2.Database(str(self.notmuch_path))

    def status(self) -> dict[str, Any]:
        """Get sync status.

        Returns:
            Dictionary with notmuch database info and sync statistics.
        """
        from sqlalchemy import func, select

        from mtk.core.models import Email, Tag

        result = {
            "notmuch_path": str(self.notmuch_path),
            "notmuch_available": False,
            "mtk_emails": 0,
            "mtk_tags": 0,
        }

        # Get mtk stats
        result["mtk_emails"] = self.session.execute(select(func.count(Email.id))).scalar() or 0
        result["mtk_tags"] = self.session.execute(select(func.count(Tag.id))).scalar() or 0

        # Try to connect to notmuch
        try:
            db = self._get_notmuch_db()
            result["notmuch_available"] = True
            result["notmuch_revision"] = db.revision().rev

            # Count emails with common message IDs
            mtk_ids = set(self.session.execute(select(Email.message_id)).scalars().all())

            common_count = 0
            for msg in db.messages("*"):
                if msg.messageid in mtk_ids:
                    common_count += 1

            result["common_emails"] = common_count
            db.close()

        except Exception as e:
            result["error"] = str(e)

        return result

    def pull(self, overwrite: bool = False) -> NotmuchSyncResult:
        """Import tags from notmuch to mtk.

        Args:
            overwrite: If True, replace mtk tags with notmuch tags.
                      If False, merge tags (add new ones).

        Returns:
            NotmuchSyncResult with statistics.
        """
        from sqlalchemy import select

        from mtk.core.models import Email, Tag

        result = NotmuchSyncResult(operation="pull")

        try:
            nm_db = self._get_notmuch_db()
        except Exception as e:
            result.errors.append(str(e))
            return result

        # Get all mtk emails by message_id
        mtk_emails = {e.message_id: e for e in self.session.execute(select(Email)).scalars()}

        try:
            for nm_msg in nm_db.messages("*"):
                msg_id = nm_msg.messageid
                if msg_id not in mtk_emails:
                    continue

                email = mtk_emails[msg_id]
                result.emails_processed += 1

                # Get notmuch tags (filter internal ones)
                nm_tags = {t for t in nm_msg.tags if t not in self.SKIP_TAGS}

                if overwrite:
                    # Remove all existing tags
                    for existing_tag in list(email.tags):
                        if existing_tag.source == "notmuch":
                            email.tags.remove(existing_tag)
                            result.tags_removed += 1

                # Add notmuch tags
                for tag_name in nm_tags:
                    prefixed_name = f"{self.tag_prefix}{tag_name}"

                    # Find or create tag
                    tag: Tag | None = self.session.execute(
                        select(Tag).where(Tag.name == prefixed_name)
                    ).scalar()

                    if not tag:
                        tag = Tag(name=prefixed_name, source="notmuch")
                        self.session.add(tag)
                        self.session.flush()

                    if tag not in email.tags:
                        email.tags.append(tag)
                        result.tags_added += 1

            self.session.commit()

        except Exception as e:
            result.errors.append(str(e))
            self.session.rollback()
        finally:
            nm_db.close()

        return result

    def push(self) -> NotmuchSyncResult:
        """Export tags from mtk to notmuch.

        Returns:
            NotmuchSyncResult with statistics.
        """
        from sqlalchemy import select

        from mtk.core.models import Email

        result = NotmuchSyncResult(operation="push")

        try:
            nm_db = self._get_notmuch_db(mode="rw")
        except Exception as e:
            result.errors.append(str(e))
            return result

        try:
            # Get emails with tags
            emails = self.session.execute(select(Email)).scalars()

            for email in emails:
                if not email.tags:
                    continue

                # Find in notmuch
                try:
                    nm_msg = nm_db.find(email.message_id)
                    if not nm_msg:
                        continue

                    result.emails_processed += 1

                    # Get current notmuch tags
                    current_tags = set(nm_msg.tags)

                    # Add mtk tags
                    for tag in email.tags:
                        tag_name = tag.name
                        # Remove prefix if it was added during pull
                        if self.tag_prefix and tag_name.startswith(self.tag_prefix):
                            tag_name = tag_name[len(self.tag_prefix) :]

                        if tag_name not in current_tags and tag_name not in self.SKIP_TAGS:
                            nm_msg.tags.add(tag_name)
                            result.tags_added += 1

                except Exception as e:
                    result.errors.append(f"{email.message_id}: {e}")

            nm_db.close()

        except Exception as e:
            result.errors.append(str(e))

        return result

    def sync(self, strategy: str = "merge") -> NotmuchSyncResult:
        """Bidirectional sync between mtk and notmuch.

        Args:
            strategy: "merge" (combine tags) or "notmuch-wins" or "mtk-wins".

        Returns:
            NotmuchSyncResult with combined statistics.
        """
        result = NotmuchSyncResult(operation="sync")

        if strategy == "merge":
            # Pull first, then push
            pull_result = self.pull(overwrite=False)
            push_result = self.push()

            result.emails_processed = max(
                pull_result.emails_processed, push_result.emails_processed
            )
            result.tags_added = pull_result.tags_added + push_result.tags_added
            result.tags_removed = pull_result.tags_removed
            result.errors = pull_result.errors + push_result.errors

        elif strategy == "notmuch-wins":
            result = self.pull(overwrite=True)
            result.operation = "sync"

        elif strategy == "mtk-wins":
            # For mtk-wins, we'd need to remove notmuch tags not in mtk
            # This is destructive, so just do a push for now
            result = self.push()
            result.operation = "sync"

        return result

    def import_emails(self, query: str = "*") -> NotmuchSyncResult:
        """Import emails from notmuch database.

        This imports email metadata and content from notmuch into mtk.

        Args:
            query: notmuch query to filter emails (default: all).

        Returns:
            NotmuchSyncResult with import statistics.
        """
        from sqlalchemy import select

        from mtk.core.models import Email, Tag
        from mtk.importers.parser import EmailParser
        from mtk.people.resolver import PersonResolver

        result = NotmuchSyncResult(operation="import")

        try:
            nm_db = self._get_notmuch_db()
        except Exception as e:
            result.errors.append(str(e))
            return result

        resolver = PersonResolver(self.session)
        parser = EmailParser()

        try:
            for nm_msg in nm_db.messages(query):
                msg_id = nm_msg.messageid

                # Skip if already in mtk
                existing = self.session.query(Email).filter_by(message_id=msg_id).first()
                if existing:
                    continue

                try:
                    # Get file path and parse
                    filenames = list(nm_msg.filenames())
                    if not filenames:
                        continue

                    filepath = Path(filenames[0])
                    if not filepath.exists():
                        continue

                    parsed = parser.parse_file(filepath)

                    # Create email
                    email = Email(
                        message_id=parsed.message_id,
                        from_addr=parsed.from_addr,
                        from_name=parsed.from_name,
                        subject=parsed.subject,
                        date=parsed.date,
                        in_reply_to=parsed.in_reply_to,
                        references=" ".join(parsed.references) if parsed.references else None,
                        body_text=parsed.body_text,
                        body_html=parsed.body_html,
                        body_preview=parsed.body_preview,
                        file_path=str(filepath),
                    )

                    if parsed.from_addr:
                        sender = resolver.resolve(parsed.from_addr, parsed.from_name)
                        email.sender_id = sender.id

                    self.session.add(email)
                    result.emails_processed += 1

                    # Import tags
                    for tag_name in nm_msg.tags:
                        if tag_name in self.SKIP_TAGS:
                            continue

                        tag = self.session.execute(select(Tag).where(Tag.name == tag_name)).scalar()

                        if not tag:
                            tag = Tag(name=tag_name, source="notmuch")
                            self.session.add(tag)
                            self.session.flush()

                        email.tags.append(tag)
                        result.tags_added += 1

                    if result.emails_processed % 100 == 0:
                        self.session.commit()

                except Exception as e:
                    result.errors.append(f"{msg_id}: {e}")

            self.session.commit()

        except Exception as e:
            result.errors.append(str(e))
            self.session.rollback()
        finally:
            nm_db.close()

        return result
