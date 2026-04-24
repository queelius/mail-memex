"""Tests for the arkiv importer (mail_memex.importers.arkiv).

Covers bundle format auto-detection, email round-trip, UUID-stable
marginalia round-trip, and idempotent re-imports.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from mail_memex.core.database import Database
from mail_memex.core.marginalia import create_marginalia
from mail_memex.core.models import Email, Marginalia
from mail_memex.export.arkiv_export import ArkivExporter
from mail_memex.importers.arkiv import (
    _is_mail_memex_arkiv_record,
    _parse_timestamp,
    detect,
    import_arkiv,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def src_db(tmp_path) -> Database:
    """A populated source DB with emails and a marginalia entry."""
    db = Database(tmp_path / "src.db")
    db.create_tables()
    with db.session() as session:
        e1 = Email(
            message_id="msg1@test",
            from_addr="alice@example.com",
            from_name="Alice",
            subject="hello",
            body_text="hello body",
            date=datetime.now(UTC).replace(tzinfo=None),
        )
        e2 = Email(
            message_id="msg2@test",
            from_addr="bob@example.com",
            subject="reply to hello",
            in_reply_to="msg1@test",
            body_text="reply body",
            date=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(e1)
        session.add(e2)
        session.flush()
        create_marginalia(
            session,
            target_uris=["mail-memex://email/msg1@test"],
            content="follow up on the proposal",
            category="todo",
        )
        session.commit()
    return db


@pytest.fixture
def fresh_db(tmp_path) -> Database:
    db = Database(tmp_path / "fresh.db")
    db.create_tables()
    return db


# ---------------------------------------------------------------------------
# _parse_timestamp / _is_mail_memex_arkiv_record
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_timestamp_iso(self):
        ts = _parse_timestamp("2026-04-23T12:34:56")
        assert isinstance(ts, datetime)
        assert ts.hour == 12

    def test_parse_timestamp_fractional(self):
        ts = _parse_timestamp("2026-04-23T12:34:56.123456")
        assert ts is not None
        assert ts.microsecond == 123456

    def test_parse_timestamp_empty(self):
        assert _parse_timestamp(None) is None
        assert _parse_timestamp("") is None

    def test_is_record_accepts_email(self):
        assert _is_mail_memex_arkiv_record(
            {"kind": "email", "uri": "mail-memex://email/foo@bar"}
        )

    def test_is_record_accepts_marginalia(self):
        assert _is_mail_memex_arkiv_record(
            {"kind": "marginalia", "uri": "mail-memex://marginalia/uu"}
        )

    def test_is_record_rejects_unknown_kind(self):
        assert not _is_mail_memex_arkiv_record({"kind": "photo"})

    def test_is_record_rejects_non_dict(self):
        assert not _is_mail_memex_arkiv_record("foo")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# detect(): every bundle shape
# ---------------------------------------------------------------------------


class TestDetect:
    def test_detect_directory(self, src_db, tmp_path):
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        assert detect(out) is True

    def test_detect_zip(self, src_db, tmp_path):
        out = tmp_path / "bundle.zip"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        assert detect(out) is True

    def test_detect_tar_gz(self, src_db, tmp_path):
        out = tmp_path / "bundle.tar.gz"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        assert detect(out) is True

    def test_detect_tgz(self, src_db, tmp_path):
        out = tmp_path / "bundle.tgz"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        assert detect(out) is True

    def test_detect_bare_jsonl(self, src_db, tmp_path):
        dir_out = tmp_path / "d"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(dir_out).export(emails, session=session)
        bare = tmp_path / "records.jsonl"
        bare.write_bytes((dir_out / "records.jsonl").read_bytes())
        assert detect(bare) is True

    def test_detect_bare_jsonl_gz(self, src_db, tmp_path):
        dir_out = tmp_path / "d"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(dir_out).export(emails, session=session)
        bare_gz = tmp_path / "records.jsonl.gz"
        with gzip.open(bare_gz, "wb") as f:
            f.write((dir_out / "records.jsonl").read_bytes())
        assert detect(bare_gz) is True

    def test_detect_rejects_missing_path(self, tmp_path):
        assert detect(tmp_path / "does-not-exist") is False

    def test_detect_rejects_non_jsonl_file(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        assert detect(txt) is False

    def test_detect_rejects_foreign_arkiv(self, tmp_path):
        foreign = tmp_path / "foreign.jsonl"
        foreign.write_text(
            json.dumps(
                {
                    "kind": "photo",
                    "uri": "photo-memex://photo/abc",
                }
            )
            + "\n"
        )
        assert detect(foreign) is False

    def test_detect_rejects_empty_directory(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert detect(empty) is False


# ---------------------------------------------------------------------------
# import_arkiv(): round-trip
# ---------------------------------------------------------------------------


class TestImportRoundTrip:
    def test_import_directory_reconstructs_emails(self, src_db, fresh_db, tmp_path):
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        stats = import_arkiv(fresh_db, out)
        assert stats["emails_added"] == 2
        assert stats["emails_seen"] == 2
        assert stats["marginalia_added"] == 1

        with fresh_db.session() as session:
            ids = {
                e.message_id
                for e in session.execute(select(Email)).scalars()
            }
        assert ids == {"msg1@test", "msg2@test"}

    def test_import_zip(self, src_db, fresh_db, tmp_path):
        out = tmp_path / "bundle.zip"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        stats = import_arkiv(fresh_db, out)
        assert stats["emails_added"] == 2

    def test_import_tar_gz(self, src_db, fresh_db, tmp_path):
        out = tmp_path / "bundle.tar.gz"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)
        stats = import_arkiv(fresh_db, out)
        assert stats["emails_added"] == 2

    def test_import_bare_jsonl_gz(self, src_db, fresh_db, tmp_path):
        """SPA round-trip format: bare .jsonl.gz."""
        dir_out = tmp_path / "d"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(dir_out).export(emails, session=session)
        bare_gz = tmp_path / "records.jsonl.gz"
        with gzip.open(bare_gz, "wb") as f:
            f.write((dir_out / "records.jsonl").read_bytes())

        stats = import_arkiv(fresh_db, bare_gz)
        assert stats["emails_added"] == 2
        assert stats["marginalia_added"] == 1

    def test_marginalia_uuid_preserved_on_import(
        self, src_db, fresh_db, tmp_path
    ):
        """Round-trip preserves UUIDs so repeated imports remain idempotent."""
        out = tmp_path / "bundle"
        with src_db.session() as session:
            src_uuids = {
                m.uuid
                for m in session.execute(select(Marginalia)).scalars()
            }
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        import_arkiv(fresh_db, out)

        with fresh_db.session() as session:
            dst_uuids = {
                m.uuid
                for m in session.execute(select(Marginalia)).scalars()
            }
        assert src_uuids == dst_uuids

    def test_marginalia_target_uris_preserved(
        self, src_db, fresh_db, tmp_path
    ):
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        import_arkiv(fresh_db, out)

        with fresh_db.session() as session:
            imported = session.execute(select(Marginalia)).scalar_one()
            targets = [t.target_uri for t in imported.targets]
        assert targets == ["mail-memex://email/msg1@test"]

    def test_re_import_is_idempotent(self, src_db, fresh_db, tmp_path):
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        first = import_arkiv(fresh_db, out)
        second = import_arkiv(fresh_db, out)

        assert first["emails_added"] == 2
        assert second["emails_added"] == 0
        assert second["emails_skipped_existing"] == 2
        assert second["marginalia_skipped_existing"] == 1

        with fresh_db.session() as session:
            assert (
                len(list(session.execute(select(Email)).scalars())) == 2
            )
            assert (
                len(list(session.execute(select(Marginalia)).scalars())) == 1
            )

    def test_merge_flag_accepted(self, src_db, fresh_db, tmp_path):
        """--merge is accepted and behaves the same as default today."""
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        stats = import_arkiv(fresh_db, out, merge=True)
        assert stats["emails_added"] == 2

    def test_email_metadata_preserved(self, src_db, fresh_db, tmp_path):
        out = tmp_path / "bundle"
        with src_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            ArkivExporter(out).export(emails, session=session)

        import_arkiv(fresh_db, out)

        with fresh_db.session() as session:
            e2 = session.execute(
                select(Email).where(Email.message_id == "msg2@test")
            ).scalar_one()
            assert e2.in_reply_to == "msg1@test"
            assert e2.from_addr == "bob@example.com"
            assert e2.subject == "reply to hello"
            assert e2.body_text == "reply body"
