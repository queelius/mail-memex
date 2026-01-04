"""Markdown export for mtk."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from mtk.export.base import Exporter, ExportResult


class MarkdownExporter(Exporter):
    """Export emails to Markdown format.

    Creates human-readable Markdown files, one per email or grouped by thread.
    """

    format_name = "markdown"

    def __init__(self, *args, group_by_thread: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.group_by_thread = group_by_thread

    def export(self, emails: list) -> ExportResult:
        """Export emails to Markdown files."""
        filtered_emails, privacy_report = self._apply_privacy(emails)

        result = ExportResult(
            format=self.format_name,
            output_path=str(self.output_path),
            emails_exported=0,
            emails_excluded=len(emails) - len(filtered_emails),
            privacy_report=privacy_report,
        )

        # Ensure output directory exists
        self.output_path.mkdir(parents=True, exist_ok=True)

        if self.group_by_thread:
            result = self._export_by_thread(filtered_emails, result)
        else:
            result = self._export_individual(filtered_emails, result)

        return result

    def _export_individual(
        self, emails: list[dict], result: ExportResult
    ) -> ExportResult:
        """Export each email as a separate file."""
        for email_data in emails:
            try:
                filename = self._generate_filename(email_data)
                filepath = self.output_path / filename

                content = self._format_email(email_data)
                filepath.write_text(content, encoding="utf-8")
                result.emails_exported += 1

            except Exception as e:
                result.errors.append(
                    f"Error exporting {email_data.get('message_id', 'unknown')}: {e}"
                )

        return result

    def _export_by_thread(
        self, emails: list[dict], result: ExportResult
    ) -> ExportResult:
        """Export emails grouped by thread."""
        # Group by thread
        threads: dict[str, list[dict]] = {}
        no_thread: list[dict] = []

        for email_data in emails:
            thread_id = email_data.get("thread_id")
            if thread_id:
                if thread_id not in threads:
                    threads[thread_id] = []
                threads[thread_id].append(email_data)
            else:
                no_thread.append(email_data)

        # Export threads
        for thread_id, thread_emails in threads.items():
            try:
                # Sort by date
                thread_emails.sort(
                    key=lambda e: e.get("date") or datetime.min
                )

                # Use first email's subject for filename
                first = thread_emails[0]
                safe_subject = self._safe_filename(first.get("subject", "no-subject"))
                filename = f"thread_{safe_subject[:50]}.md"
                filepath = self.output_path / filename

                content = self._format_thread(thread_emails)
                filepath.write_text(content, encoding="utf-8")
                result.emails_exported += len(thread_emails)

            except Exception as e:
                result.errors.append(f"Error exporting thread {thread_id}: {e}")

        # Export non-threaded emails individually
        for email_data in no_thread:
            try:
                filename = self._generate_filename(email_data)
                filepath = self.output_path / filename

                content = self._format_email(email_data)
                filepath.write_text(content, encoding="utf-8")
                result.emails_exported += 1

            except Exception as e:
                result.errors.append(
                    f"Error exporting {email_data.get('message_id', 'unknown')}: {e}"
                )

        return result

    def _generate_filename(self, email_data: dict) -> str:
        """Generate a filename for an email."""
        date = email_data.get("date")
        if isinstance(date, datetime):
            date_str = date.strftime("%Y-%m-%d_%H%M")
        elif isinstance(date, str):
            date_str = date[:10].replace(":", "-")
        else:
            date_str = "unknown-date"

        subject = email_data.get("subject", "no-subject")
        safe_subject = self._safe_filename(subject)[:50]

        return f"{date_str}_{safe_subject}.md"

    def _safe_filename(self, s: str) -> str:
        """Convert string to safe filename."""
        # Remove/replace unsafe characters
        s = re.sub(r'[<>:"/\\|?*]', "", s)
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"_+", "_", s)
        return s.strip("_") or "untitled"

    def _format_email(self, email_data: dict) -> str:
        """Format a single email as Markdown."""
        lines = []

        # Title
        subject = email_data.get("subject", "(no subject)")
        lines.append(f"# {subject}")
        lines.append("")

        # Metadata
        lines.append("## Metadata")
        lines.append("")
        lines.append(f"- **From:** {email_data.get('from_name', '')} <{email_data.get('from_addr', '')}>")

        date = email_data.get("date")
        if isinstance(date, datetime):
            lines.append(f"- **Date:** {date.strftime('%Y-%m-%d %H:%M:%S')}")
        elif date:
            lines.append(f"- **Date:** {date}")

        lines.append(f"- **Message-ID:** `{email_data.get('message_id', '')}`")

        if email_data.get("thread_id"):
            lines.append(f"- **Thread-ID:** `{email_data.get('thread_id')}`")

        tags = email_data.get("tags", [])
        if tags:
            lines.append(f"- **Tags:** {', '.join(tags)}")

        lines.append("")

        # Body
        lines.append("## Content")
        lines.append("")
        body = email_data.get("body_text", "")
        if body:
            # Indent body as blockquote for readability
            for line in body.split("\n"):
                lines.append(line)
        else:
            lines.append("*(no text content)*")

        lines.append("")

        # Attachments
        attachments = email_data.get("attachments", [])
        if attachments:
            lines.append("## Attachments")
            lines.append("")
            for att in attachments:
                size_kb = (att.get("size", 0) or 0) / 1024
                lines.append(
                    f"- {att.get('filename', 'unknown')} "
                    f"({att.get('content_type', 'unknown')}, {size_kb:.1f} KB)"
                )
            lines.append("")

        return "\n".join(lines)

    def _format_thread(self, emails: list[dict]) -> str:
        """Format a thread as Markdown."""
        if not emails:
            return ""

        lines = []

        # Thread title
        subject = emails[0].get("subject", "(no subject)")
        lines.append(f"# Thread: {subject}")
        lines.append("")

        # Summary
        participants = set()
        for e in emails:
            if e.get("from_addr"):
                participants.add(e["from_addr"])

        date_range = ""
        first_date = emails[0].get("date")
        last_date = emails[-1].get("date")
        if first_date and last_date:
            if isinstance(first_date, datetime):
                first_date = first_date.strftime("%Y-%m-%d")
            if isinstance(last_date, datetime):
                last_date = last_date.strftime("%Y-%m-%d")
            date_range = f"{first_date} to {last_date}"

        lines.append(f"**Messages:** {len(emails)}")
        lines.append(f"**Participants:** {', '.join(sorted(participants))}")
        if date_range:
            lines.append(f"**Date range:** {date_range}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Each message
        for i, email_data in enumerate(emails, 1):
            lines.append(f"## Message {i}")
            lines.append("")

            from_str = email_data.get("from_name") or email_data.get("from_addr", "")
            date = email_data.get("date")
            if isinstance(date, datetime):
                date_str = date.strftime("%Y-%m-%d %H:%M")
            else:
                date_str = str(date) if date else ""

            lines.append(f"**From:** {from_str}  ")
            lines.append(f"**Date:** {date_str}")
            lines.append("")

            body = email_data.get("body_text", "")
            if body:
                lines.append(body)
            else:
                lines.append("*(no text content)*")

            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)
