"""JSON export for mtk."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from mtk.export.base import Exporter, ExportResult


class JsonExporter(Exporter):
    """Export emails to JSON format.

    Output structure matches the mtk JSON API for consistency.
    """

    format_name = "json"

    def __init__(self, *args, pretty: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pretty = pretty

    def export(self, emails: list) -> ExportResult:
        """Export emails to JSON file."""
        filtered_emails, privacy_report = self._apply_privacy(emails)

        result = ExportResult(
            format=self.format_name,
            output_path=str(self.output_path),
            emails_exported=len(filtered_emails),
            emails_excluded=len(emails) - len(filtered_emails),
            privacy_report=privacy_report,
        )

        # Build export data
        export_data = {
            "exported_at": datetime.now().isoformat(),
            "total_emails": len(filtered_emails),
            "emails": [],
        }

        for email_data in filtered_emails:
            # Convert datetime to ISO format
            date = email_data.get("date")
            if isinstance(date, datetime):
                email_data["date"] = date.isoformat()

            export_data["emails"].append(email_data)

        # Write to file
        try:
            with open(self.output_path, "w", encoding="utf-8") as f:
                if self.pretty:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)
                else:
                    json.dump(export_data, f, ensure_ascii=False)
        except Exception as e:
            result.errors.append(str(e))

        return result
