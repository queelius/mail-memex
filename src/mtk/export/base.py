"""Base exporter interface and common utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mtk.core.models import Email
    from mtk.core.privacy import PrivacyFilter, PrivacyReport


@dataclass
class ExportResult:
    """Result of an export operation."""

    format: str
    output_path: str
    emails_exported: int = 0
    emails_excluded: int = 0
    emails_redacted: int = 0
    attachments_exported: int = 0
    errors: list[str] = field(default_factory=list)
    privacy_report: PrivacyReport | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        result = {
            "format": self.format,
            "output_path": self.output_path,
            "emails_exported": self.emails_exported,
            "emails_excluded": self.emails_excluded,
            "emails_redacted": self.emails_redacted,
            "attachments_exported": self.attachments_exported,
        }
        if self.errors:
            result["errors"] = self.errors
        if self.privacy_report:
            result["privacy"] = {
                "excluded": self.privacy_report.excluded_count,
                "redacted": self.privacy_report.redacted_count,
                "exclusion_reasons": self.privacy_report.exclusion_reasons,
            }
        return result


class Exporter(ABC):
    """Base class for email exporters."""

    format_name: str = "base"

    def __init__(
        self,
        output_path: Path,
        privacy_filter: PrivacyFilter | None = None,
        include_attachments: bool = False,
    ) -> None:
        self.output_path = output_path
        self.privacy_filter = privacy_filter
        self.include_attachments = include_attachments

    @abstractmethod
    def export(self, emails: list[Email]) -> ExportResult:
        """Export emails to the target format.

        Args:
            emails: List of Email objects to export.

        Returns:
            ExportResult with statistics.
        """
        pass

    def _apply_privacy(
        self, emails: list[Email]
    ) -> tuple[list[dict], PrivacyReport | None]:
        """Apply privacy filtering if configured.

        Returns:
            Tuple of (filtered email dicts, privacy report or None).
        """
        if self.privacy_filter:
            return self.privacy_filter.filter_emails(emails)

        # No privacy filter - convert to dicts without filtering
        result = []
        for email in emails:
            result.append(
                {
                    "message_id": email.message_id,
                    "from_addr": email.from_addr,
                    "from_name": email.from_name,
                    "subject": email.subject,
                    "date": email.date,
                    "in_reply_to": email.in_reply_to,
                    "references": email.references,
                    "body_text": email.body_text,
                    "body_html": email.body_html,
                    "body_preview": email.body_preview,
                    "thread_id": email.thread_id,
                    "tags": [t.name for t in email.tags]
                    if hasattr(email, "tags") and email.tags
                    else [],
                    "attachments": [
                        {
                            "filename": a.filename,
                            "content_type": a.content_type,
                            "size": a.size,
                        }
                        for a in email.attachments
                    ]
                    if hasattr(email, "attachments") and email.attachments
                    else [],
                }
            )
        return result, None

    def _format_date(self, dt: datetime | None) -> str:
        """Format datetime for export."""
        if dt is None:
            return ""
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z") or dt.isoformat()
