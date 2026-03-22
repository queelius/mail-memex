"""Export emails to arkiv JSONL format.

arkiv is the universal personal data format in the longecho ecosystem.
Each email becomes one JSONL record with mimetype, content, uri, timestamp, and metadata.
Denormalizes relational data (tags, attachments) into each record's metadata.

See: https://github.com/queelius/arkiv
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from mtk.export.base import ExportResult

if TYPE_CHECKING:
    from mtk.core.models import Email


class ArkivExporter:
    """Export emails to arkiv universal record format (JSONL)."""

    def __init__(
        self,
        output_path: Path,
        include_body: bool = True,
    ) -> None:
        self.output_path = Path(output_path)
        self.include_body = include_body

    def _email_to_record(self, email: Email) -> dict:
        """Convert an Email to an arkiv record.

        Denormalizes tags and attachment info into metadata.
        """
        record: dict = {
            "mimetype": "message/rfc822",
            "uri": f"mtk://email/{email.message_id}",
        }

        if email.date:
            record["timestamp"] = email.date.isoformat()

        if self.include_body and email.body_text:
            record["content"] = email.body_text

        metadata: dict = {
            "message_id": email.message_id,
            "from_addr": email.from_addr,
            "subject": email.subject,
        }

        if email.from_name:
            metadata["from_name"] = email.from_name
        if email.thread_id:
            metadata["thread_id"] = email.thread_id
        if email.in_reply_to:
            metadata["in_reply_to"] = email.in_reply_to
        if email.tags:
            metadata["tags"] = sorted(t.name for t in email.tags)
        if email.attachments:
            metadata["has_attachments"] = True
            metadata["attachment_count"] = len(email.attachments)
            metadata["attachments"] = [
                {
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                }
                for a in email.attachments
            ]

        record["metadata"] = metadata
        return record

    def export(self, emails: list[Email]) -> ExportResult:
        """Export emails to JSONL file."""
        exported = 0

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.output_path, "w") as f:
            for email in emails:
                record = self._email_to_record(email)
                f.write(json.dumps(record, default=str) + "\n")
                exported += 1

        # Generate schema.yaml alongside the JSONL
        self._write_schema(exported)

        return ExportResult(
            format="arkiv",
            output_path=str(self.output_path),
            emails_exported=exported,
        )

    def _write_schema(self, record_count: int) -> None:
        """Write arkiv schema.yaml describing metadata keys."""
        schema_path = self.output_path.parent / "schema.yaml"

        # Build schema following arkiv spec: type, count, description, values/example
        metadata_keys = {
            "message_id": {
                "type": "string",
                "count": record_count,
                "description": "RFC 2822 Message-ID header",
            },
            "from_addr": {
                "type": "string",
                "count": record_count,
                "description": "Sender email address",
            },
            "subject": {
                "type": "string",
                "count": record_count,
                "description": "Email subject line",
            },
            "from_name": {
                "type": "string",
                "description": "Sender display name",
            },
            "thread_id": {
                "type": "string",
                "description": "Thread/conversation identifier",
            },
            "in_reply_to": {
                "type": "string",
                "description": "Message-ID of the email being replied to",
            },
            "tags": {
                "type": "array",
                "description": "Email tags/labels (e.g. inbox, important, sent)",
            },
            "has_attachments": {
                "type": "boolean",
                "description": "Whether the email has file attachments",
                "values": [True, False],
            },
            "attachment_count": {
                "type": "number",
                "description": "Number of file attachments",
            },
            "attachments": {
                "type": "array",
                "description": "Attachment metadata (filename, content_type, size)",
            },
        }

        collection_name = self.output_path.stem
        schema = {
            collection_name: {
                "record_count": record_count,
                "metadata_keys": metadata_keys,
            }
        }
        with open(schema_path, "w") as f:
            yaml.dump(schema, f, default_flow_style=False, sort_keys=False)
