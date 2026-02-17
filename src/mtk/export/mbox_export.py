"""Mbox export for mtk."""

from __future__ import annotations

import email.utils
from datetime import datetime
from email.message import EmailMessage
from mailbox import mbox
from typing import Any

from mtk.export.base import Exporter, ExportResult


class MboxExporter(Exporter):
    """Export emails to mbox format.

    Standard Unix mbox format, compatible with most email clients.
    Includes X-Mtk-Tags header for tag preservation.
    """

    format_name = "mbox"

    def export(self, emails: list[Any]) -> ExportResult:
        """Export emails to mbox file."""
        filtered_emails, privacy_report = self._apply_privacy(emails)

        result = ExportResult(
            format=self.format_name,
            output_path=str(self.output_path),
            emails_exported=0,
            emails_excluded=len(emails) - len(filtered_emails),
            privacy_report=privacy_report,
        )

        # Create mbox
        try:
            box = mbox(str(self.output_path))
            box.lock()

            for email_data in filtered_emails:
                try:
                    msg = self._create_message(email_data)
                    box.add(msg)
                    result.emails_exported += 1
                except Exception as e:
                    result.errors.append(
                        f"Error exporting {email_data.get('message_id', 'unknown')}: {e}"
                    )

            box.unlock()
            box.close()

        except Exception as e:
            result.errors.append(f"Error creating mbox: {e}")

        return result

    def _create_message(self, email_data: dict[str, Any]) -> EmailMessage:
        """Create EmailMessage from email data."""
        msg = EmailMessage()

        # Headers
        msg["Message-ID"] = f"<{email_data.get('message_id', '')}>"
        msg["Subject"] = email_data.get("subject", "")

        # Format From header
        from_name = email_data.get("from_name", "")
        from_addr = email_data.get("from_addr", "")
        if from_name:
            msg["From"] = email.utils.formataddr((from_name, from_addr))
        else:
            msg["From"] = from_addr

        # Date
        date = email_data.get("date")
        if isinstance(date, datetime):
            msg["Date"] = email.utils.format_datetime(date)
        elif isinstance(date, str):
            msg["Date"] = date

        # References
        if email_data.get("in_reply_to"):
            msg["In-Reply-To"] = email_data["in_reply_to"]
        if email_data.get("references"):
            msg["References"] = email_data["references"]

        # Custom mtk headers
        tags = email_data.get("tags", [])
        if tags:
            msg["X-Mtk-Tags"] = ", ".join(tags)

        thread_id = email_data.get("thread_id")
        if thread_id:
            msg["X-Mtk-Thread-ID"] = thread_id

        # Body
        body_text = email_data.get("body_text", "")
        body_html = email_data.get("body_html", "")

        if body_html and body_text:
            # Multipart message
            msg.make_mixed()
            msg.add_alternative(body_text, subtype="plain")
            msg.add_alternative(body_html, subtype="html")
        elif body_html:
            msg.set_content(body_html, subtype="html")
        else:
            msg.set_content(body_text or "")

        return msg
