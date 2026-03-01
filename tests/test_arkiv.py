"""Tests for arkiv JSONL export."""

import json
from pathlib import Path

import yaml
from sqlalchemy import select

from mtk.core.database import Database
from mtk.core.models import Email
from mtk.export.arkiv_export import ArkivExporter


class TestArkivExporter:
    """Tests for arkiv export functionality."""

    def test_export_creates_jsonl(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            exporter = ArkivExporter(output)
            result = exporter.export(emails)
        assert output.exists()
        assert result.emails_exported == 5

    def test_each_line_is_valid_json(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            assert isinstance(record, dict)

    def test_record_has_arkiv_fields(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert record["mimetype"] == "message/rfc822"
        assert "timestamp" in record
        assert "metadata" in record
        assert "uri" in record
        assert record["uri"].startswith("mtk://email/")

    def test_metadata_has_required_fields(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        meta = record["metadata"]
        assert "message_id" in meta
        assert "from_addr" in meta
        assert "subject" in meta

    def test_content_included_by_default(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert "content" in record

    def test_no_body_option(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output, include_body=False).export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert "content" not in record

    def test_tags_denormalized(self, populated_db: Database, tmp_path: Path) -> None:
        """Tags should be denormalized into each record's metadata."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        # Find a record that has tags
        found_tags = False
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            if "tags" in record.get("metadata", {}):
                assert isinstance(record["metadata"]["tags"], list)
                found_tags = True
                break
        assert found_tags, "Expected at least one record with tags"

    def test_tags_sorted(self, populated_db: Database, tmp_path: Path) -> None:
        """Tags should be sorted alphabetically."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            tags = record.get("metadata", {}).get("tags")
            if tags:
                assert tags == sorted(tags)

    def test_attachments_denormalized(self, populated_db: Database, tmp_path: Path) -> None:
        """Attachments should be denormalized into metadata."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        found_attachment = False
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            meta = record.get("metadata", {})
            if meta.get("has_attachments"):
                assert meta["attachment_count"] > 0
                assert isinstance(meta["attachments"], list)
                att = meta["attachments"][0]
                assert "filename" in att
                assert "content_type" in att
                assert "size" in att
                found_attachment = True
                break
        assert found_attachment, "Expected at least one record with attachments"

    def test_thread_id_in_metadata(self, populated_db: Database, tmp_path: Path) -> None:
        """Emails with thread_id should include it in metadata."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        found_thread = False
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            if "thread_id" in record.get("metadata", {}):
                found_thread = True
                break
        assert found_thread, "Expected at least one record with thread_id"

    def test_in_reply_to_in_metadata(self, populated_db: Database, tmp_path: Path) -> None:
        """Emails with in_reply_to should include it in metadata."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        found_reply = False
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            if "in_reply_to" in record.get("metadata", {}):
                found_reply = True
                break
        assert found_reply, "Expected at least one record with in_reply_to"

    def test_generates_schema_yaml(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        schema_path = tmp_path / "schema.yaml"
        assert schema_path.exists()

    def test_schema_has_correct_structure(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        schema = yaml.safe_load((tmp_path / "schema.yaml").read_text())
        assert "emails" in schema  # collection name from stem
        assert "record_count" in schema["emails"]
        assert schema["emails"]["record_count"] == 5
        assert "metadata_keys" in schema["emails"]
        keys = schema["emails"]["metadata_keys"]
        assert "message_id" in keys
        assert "from_addr" in keys
        assert "subject" in keys

    def test_schema_metadata_key_has_type(self, populated_db: Database, tmp_path: Path) -> None:
        """Each metadata key in schema should have a type field."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        schema = yaml.safe_load((tmp_path / "schema.yaml").read_text())
        for key, spec in schema["emails"]["metadata_keys"].items():
            assert "type" in spec, f"metadata key '{key}' missing 'type'"

    def test_schema_collection_name_from_stem(self, populated_db: Database, tmp_path: Path) -> None:
        """Schema collection name should match the JSONL filename stem."""
        output = tmp_path / "my_archive.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        schema = yaml.safe_load((tmp_path / "schema.yaml").read_text())
        assert "my_archive" in schema

    def test_export_result_format(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            result = ArkivExporter(output).export(emails)
        assert result.format == "arkiv"
        assert result.output_path == str(output)

    def test_export_result_to_dict(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            result = ArkivExporter(output).export(emails)
        d = result.to_dict()
        assert d["format"] == "arkiv"
        assert d["emails_exported"] == 5

    def test_empty_export(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "empty.jsonl"
        result = ArkivExporter(output).export([])
        assert result.emails_exported == 0
        assert output.exists()
        assert output.read_text() == ""

    def test_empty_export_schema(self, populated_db: Database, tmp_path: Path) -> None:
        """Empty export should still produce a schema with record_count 0."""
        output = tmp_path / "empty.jsonl"
        ArkivExporter(output).export([])
        schema = yaml.safe_load((tmp_path / "schema.yaml").read_text())
        assert schema["empty"]["record_count"] == 0

    def test_creates_parent_directories(self, populated_db: Database, tmp_path: Path) -> None:
        output = tmp_path / "deep" / "nested" / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        assert output.exists()

    def test_uri_contains_message_id(self, populated_db: Database, tmp_path: Path) -> None:
        """URI should contain the email's message_id."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        for line in output.read_text().strip().split("\n"):
            record = json.loads(line)
            msg_id = record["metadata"]["message_id"]
            assert record["uri"] == f"mtk://email/{msg_id}"

    def test_from_name_in_metadata(self, populated_db: Database, tmp_path: Path) -> None:
        """Emails with from_name should include it in metadata."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(output).export(emails)
        line = output.read_text().strip().split("\n")[0]
        record = json.loads(line)
        assert "from_name" in record["metadata"]

    def test_record_count_matches_lines(self, populated_db: Database, tmp_path: Path) -> None:
        """Number of JSONL lines should match the export count."""
        output = tmp_path / "emails.jsonl"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            result = ArkivExporter(output).export(emails)
        lines = output.read_text().strip().split("\n")
        assert len(lines) == result.emails_exported
