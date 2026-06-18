"""Tests for HTML SPA export.

Shape: single self-contained .html file with inlined sql-wasm.js,
base64-encoded sql-wasm.wasm, and gzipped+base64 SQLite DB. All user
data is rendered via DOM textContent; no CDN, no fetch, no FTS5.
"""

from __future__ import annotations

import base64
import gzip
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy import select

from mail_memex.core.database import Database
from mail_memex.core.models import Email
from mail_memex.export.html_export import HtmlExporter


def _extract_db_from_html(html: str) -> bytes:
    """Pull the gzipped+base64 DB out of the SPA and decompress it."""
    match = re.search(
        r'<script id="bm-db-b64" type="application/base64">\s*'
        r'([A-Za-z0-9+/=\s]+?)\s*</script>',
        html,
    )
    assert match is not None, "bm-db-b64 script not found in HTML"
    gz = base64.b64decode("".join(match.group(1).split()))
    return gzip.decompress(gz)


class TestHtmlExporter:
    def test_html_export_creates_file(self, populated_db: Database, tmp_dir: Path) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            exporter = HtmlExporter(output)
            result = exporter.export(emails)
        assert output.exists()
        assert result.emails_exported == 5
        assert result.format == "html"

    def test_html_inlines_vendored_sqljs_not_cdn(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        html = output.read_text()
        # The sql.js API object name must be present.
        assert "initSqlJs" in html
        # No CDN fetch URLs.
        for smell in ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com"):
            assert smell not in html, f"unexpected CDN reference {smell!r}"

    def test_html_embeds_wasm_as_base64(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        html = output.read_text()
        m = re.search(
            r'<script id="bm-wasm-b64" type="application/base64">\s*'
            r'([A-Za-z0-9+/=\s]+?)\s*</script>',
            html,
        )
        assert m is not None, "bm-wasm-b64 script not found"
        blob = base64.b64decode("".join(m.group(1).split()))
        # Wasm magic-number header.
        assert blob[:4] == b"\x00asm"
        # sql-wasm.wasm is ~650 KB; base64 expansion ≈ 870 KB.
        assert len(blob) > 100_000

    def test_html_embeds_gzipped_db_as_base64(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        html = output.read_text()
        m = re.search(
            r'<script id="bm-db-b64" type="application/base64">\s*'
            r'([A-Za-z0-9+/=\s]+?)\s*</script>',
            html,
        )
        assert m is not None, "bm-db-b64 script not found"
        gz = base64.b64decode("".join(m.group(1).split()))
        # gzip magic header.
        assert gz[:2] == b"\x1f\x8b"
        # Decompresses to valid SQLite.
        raw = gzip.decompress(gz)
        assert raw[:16].startswith(b"SQLite format 3")

    def test_html_contains_no_fts5_tables(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        """Vendored sql.js lacks FTS5; shipped DB must not contain FTS5 shadows."""
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        db_bytes = _extract_db_from_html(output.read_text())
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            f.write(db_bytes)
            tmp_db = f.name
        try:
            conn = sqlite3.connect(tmp_db)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            names = {row[0] for row in cursor}
            conn.close()
        finally:
            os.unlink(tmp_db)
        assert "emails_fts" not in names

    def test_html_uses_hash_routing(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        html = output.read_text()
        assert "#/email/" in html
        assert "#/thread/" in html
        assert "#/search/" in html
        assert "#/tag/" in html
        assert "hashchange" in html

    def test_email_deep_links_route_on_message_id(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        """MM-9: deep links must key on the durable message_id, not the
        internal autoincrement row id (which changes across re-imports)."""
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        html = output.read_text()
        # The email detail loader resolves by message_id, and rows route on it.
        assert "WHERE message_id = ?" in html
        assert "r.message_id" in html
        # The old int-id parse is gone.
        assert "parseInt(decodeURIComponent(parts[1])" not in html

    def test_output_is_valid_html(self, populated_db: Database, tmp_dir: Path) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        content = output.read_text()
        assert content.startswith("<!DOCTYPE html>")
        assert "</html>" in content

    def test_export_result_to_dict(self, populated_db: Database, tmp_dir: Path) -> None:
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            result = HtmlExporter(output).export(emails)
        d = result.to_dict()
        assert d["format"] == "html"
        assert d["emails_exported"] == 5
        assert d["output_path"] == str(output)

    def test_contains_ui_elements(self, populated_db: Database, tmp_dir: Path) -> None:
        """The new SPA shell has: search input, stats badge, list/detail panes,
        theme toggle button, and the brand wordmark."""
        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        content = output.read_text()
        assert 'id="search"' in content
        assert 'id="stats"' in content
        assert 'id="list-pane"' in content
        assert 'id="detail-pane"' in content
        assert 'id="theme-toggle"' in content
        assert 'id="brand"' in content

    def test_creates_parent_directories(self, populated_db: Database, tmp_dir: Path) -> None:
        output = tmp_dir / "sub" / "dir" / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            result = HtmlExporter(output).export(emails)
        assert output.exists()
        assert result.emails_exported == 5

    def test_export_empty_emails(self, tmp_dir: Path) -> None:
        """Export works with an empty list of emails."""
        output = tmp_dir / "empty.html"
        exporter = HtmlExporter(output)
        result = exporter.export([])
        assert output.exists()
        assert result.emails_exported == 0
        assert result.format == "html"

    def test_export_preserves_tags_as_json(
        self, populated_db: Database, tmp_dir: Path
    ) -> None:
        """Tags are stored as JSON arrays in the export DB (sql.js SPA uses them)."""
        import json

        output = tmp_dir / "archive.html"
        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            HtmlExporter(output).export(emails)
        db_bytes = _extract_db_from_html(output.read_text())

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            f.write(db_bytes)
            tmp_db = f.name
        try:
            conn = sqlite3.connect(tmp_db)
            cursor = conn.execute(
                "SELECT tags_json FROM emails WHERE tags_json IS NOT NULL"
            )
            rows = cursor.fetchall()
            conn.close()
            assert len(rows) > 0
            for (tags_json,) in rows:
                tags = json.loads(tags_json)
                assert isinstance(tags, list)
        finally:
            os.unlink(tmp_db)


class TestHtmlBuilder:
    def test_build_export_db_returns_bytes(self, populated_db: Database) -> None:
        from mail_memex.export.html_builder import build_export_db

        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            db_bytes = build_export_db(emails)
        assert isinstance(db_bytes, bytes)
        assert db_bytes[:16].startswith(b"SQLite format 3")

    def test_build_export_db_has_correct_email_count(self, populated_db: Database) -> None:
        import os
        import sqlite3
        import tempfile

        from mail_memex.export.html_builder import build_export_db

        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            db_bytes = build_export_db(emails)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            f.write(db_bytes)
            tmp_db = f.name

        try:
            conn = sqlite3.connect(tmp_db)
            count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
            conn.close()
            assert count == 5
        finally:
            os.unlink(tmp_db)

    def test_build_export_db_has_threads(self, populated_db: Database) -> None:
        import os
        import sqlite3
        import tempfile

        from mail_memex.export.html_builder import build_export_db

        with populated_db.session() as session:
            emails = list(session.execute(select(Email)).scalars())
            db_bytes = build_export_db(emails)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            f.write(db_bytes)
            tmp_db = f.name

        try:
            conn = sqlite3.connect(tmp_db)
            count = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            conn.close()
            assert count > 0
        finally:
            os.unlink(tmp_db)

    def test_build_export_db_empty(self) -> None:
        from mail_memex.export.html_builder import build_export_db

        db_bytes = build_export_db([])
        assert isinstance(db_bytes, bytes)
        assert db_bytes[:16].startswith(b"SQLite format 3")
