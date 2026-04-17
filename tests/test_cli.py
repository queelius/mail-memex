"""TDD Tests for CLI commands.

These tests verify the CLI interface works correctly:
- Command invocation and exit codes
- Output format (--json flag)
- Error handling
- Help text
"""

from datetime import datetime
from unittest.mock import patch

from typer.testing import CliRunner

from mail_memex.cli.main import app

runner = CliRunner()


class TestCLIHelp:
    """Tests for CLI help and basic invocation."""

    def test_app_has_help(self) -> None:
        """CLI should display help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "mail-memex" in result.output.lower() or "mail memex" in result.output.lower()

    def test_version_flag(self) -> None:
        """CLI should display version."""
        result = runner.invoke(app, ["--version"])
        # Should have version info or show help
        assert result.exit_code == 0 or "--help" in result.output


class TestInitCommand:
    """Tests for the init command."""

    def test_init_help(self) -> None:
        """init command should have help."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "init" in result.output.lower()

    def test_init_creates_fresh_db(self, tmp_path, isolated_mail_memex_config) -> None:
        """init should create a new database file at the given path."""
        db_path = tmp_path / "new.db"
        assert not db_path.exists()

        result = runner.invoke(app, ["init", "--db", str(db_path)])

        assert result.exit_code == 0
        assert db_path.exists()

    def test_init_refuses_when_db_exists(self, tmp_path, isolated_mail_memex_config) -> None:
        """init without --force should refuse to overwrite existing DB."""
        db_path = tmp_path / "existing.db"
        # Create the DB first
        runner.invoke(app, ["init", "--db", str(db_path)])
        assert db_path.exists()

        # Second init without --force should fail
        result = runner.invoke(app, ["init", "--db", str(db_path)])

        assert result.exit_code != 0
        assert "already exists" in result.output.lower()

    def test_init_force_drops_existing_data(self, tmp_path, isolated_mail_memex_config) -> None:
        """init --force must actually reinitialize: existing data is dropped."""
        import sqlite3

        db_path = tmp_path / "forceful.db"
        # Create the DB and insert a marker row
        runner.invoke(app, ["init", "--db", str(db_path)])
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO emails "
            "(message_id, from_addr, date, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "marker@example.com",
                "test@example.com",
                "2024-01-01 00:00:00",
                "2024-01-01 00:00:00",
                "2024-01-01 00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        # Verify row is present
        conn = sqlite3.connect(db_path)
        (n_before,) = conn.execute("SELECT COUNT(*) FROM emails").fetchone()
        conn.close()
        assert n_before == 1, "precondition: row should be in DB before re-init"

        # Force re-init
        result = runner.invoke(app, ["init", "--db", str(db_path), "--force"])
        assert result.exit_code == 0, f"init --force failed: {result.output}"

        # The marker row must be gone (DB was truly reinitialized)
        conn = sqlite3.connect(db_path)
        (n_after,) = conn.execute("SELECT COUNT(*) FROM emails").fetchone()
        conn.close()
        assert n_after == 0, "init --force must drop existing data"


class TestImportMetadata:
    """Tests that import populates the metadata_json column from raw_headers."""

    def test_mbox_import_preserves_gmail_labels(
        self, tmp_path, isolated_mail_memex_config, sample_gmail_mbox
    ) -> None:
        """Gmail mbox import should preserve X-Gmail-Labels in metadata_json."""
        import json as json_lib
        import sqlite3

        db_path = tmp_path / "gmail_import.db"
        runner.invoke(app, ["init", "--db", str(db_path)])

        # Point the CLI at this test DB via a patched config loader
        from mail_memex.core.config import MailMemexConfig

        def _load_with_db(*args, **kwargs):
            cfg = MailMemexConfig()
            cfg.db_path = db_path
            return cfg

        with patch.object(MailMemexConfig, "load", _load_with_db):
            result = runner.invoke(app, ["import", "mbox", str(sample_gmail_mbox), "--json"])

        assert result.exit_code == 0, f"import failed: {result.output}"

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT metadata_json FROM emails WHERE message_id LIKE '%gmail1%'"
        ).fetchone()
        conn.close()

        assert row is not None, "imported email not found in DB"
        assert row[0] is not None, "metadata_json should be populated from raw_headers"

        meta = json_lib.loads(row[0])
        assert "X-Gmail-Labels" in meta, f"X-Gmail-Labels missing from metadata: {meta!r}"
        assert "Important" in meta["X-Gmail-Labels"]


class TestSearchCommand:
    """Tests for the search command."""

    def test_search_help(self) -> None:
        """search command should have help."""
        result = runner.invoke(app, ["search", "--help"])
        assert result.exit_code == 0
        assert "search" in result.output.lower()

    def test_search_without_db_fails(self) -> None:
        """search without initialized db should fail gracefully."""
        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            mock_get_db.side_effect = RuntimeError("Database not initialized")

            result = runner.invoke(app, ["search", "test"])

            # Should fail gracefully
            assert result.exit_code != 0 or "not initialized" in result.output.lower()


class TestTagCommand:
    """Tests for the tag command group (sub-app)."""

    # --- Help / discovery ---

    def test_tag_help(self) -> None:
        """tag command group should display help listing subcommands."""
        result = runner.invoke(app, ["tag", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "remove" in result.output
        assert "list" in result.output
        assert "batch" in result.output

    def test_tag_add_help(self) -> None:
        """tag add should display help."""
        result = runner.invoke(app, ["tag", "add", "--help"])
        assert result.exit_code == 0
        assert "message" in result.output.lower()

    def test_tag_remove_help(self) -> None:
        """tag remove should display help."""
        result = runner.invoke(app, ["tag", "remove", "--help"])
        assert result.exit_code == 0
        assert "message" in result.output.lower()

    def test_tag_list_help(self) -> None:
        """tag list should display help."""
        result = runner.invoke(app, ["tag", "list", "--help"])
        assert result.exit_code == 0

    def test_tag_batch_help(self) -> None:
        """tag batch should display help."""
        result = runner.invoke(app, ["tag", "batch", "--help"])
        assert result.exit_code == 0

    # --- tag add ---

    def test_tag_add_email_not_found(self) -> None:
        """tag add should fail gracefully when email not found."""
        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database

            db = Database(":memory:")
            db.create_tables()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "add", "nonexistent@id", "work"])
            assert result.exit_code != 0

    def test_tag_add_email_not_found_json(self) -> None:
        """tag add --json should output error JSON when email not found."""
        import json

        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database

            db = Database(":memory:")
            db.create_tables()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "add", "--json", "nonexistent@id", "work"])
            assert result.exit_code != 0
            data = json.loads(result.output)
            assert "error" in data

    def test_tag_add_success(self) -> None:
        """tag add should add tags to an email."""
        import json

        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database
            from mail_memex.core.models import Email

            db = Database(":memory:")
            db.create_tables()
            with db.session() as session:
                session.add(
                    Email(
                        message_id="test@example.com",
                        from_addr="a@b.com",
                        date=datetime(2024, 1, 15),
                    )
                )
                session.commit()
            mock_get_db.return_value = db

            result = runner.invoke(
                app, ["tag", "add", "--json", "test@example.com", "work", "important"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["message_id"] == "test@example.com"
            assert "work" in data["tags"]
            assert "important" in data["tags"]

    def test_tag_add_requires_tags(self) -> None:
        """tag add should require at least one tag argument."""
        result = runner.invoke(app, ["tag", "add", "test@example.com"])
        assert result.exit_code != 0

    # --- tag remove ---

    def test_tag_remove_email_not_found(self) -> None:
        """tag remove should fail gracefully when email not found."""
        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database

            db = Database(":memory:")
            db.create_tables()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "remove", "nonexistent@id", "work"])
            assert result.exit_code != 0

    def test_tag_remove_success(self) -> None:
        """tag remove should remove tags from an email."""
        import json

        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database
            from mail_memex.core.models import Email, Tag

            db = Database(":memory:")
            db.create_tables()
            with db.session() as session:
                email = Email(
                    message_id="test@example.com", from_addr="a@b.com", date=datetime(2024, 1, 15)
                )
                tag = Tag(name="work", source="mail-memex")
                session.add(email)
                session.add(tag)
                session.flush()
                email.tags.append(tag)
                session.commit()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "remove", "--json", "test@example.com", "work"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["message_id"] == "test@example.com"
            assert "work" not in data["tags"]

    # --- tag list ---

    def test_tag_list_empty(self) -> None:
        """tag list should handle empty tag list."""
        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database

            db = Database(":memory:")
            db.create_tables()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "list"])
            assert result.exit_code == 0

    def test_tag_list_json(self) -> None:
        """tag list --json should output JSON array of tags with counts."""
        import json

        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database
            from mail_memex.core.models import Email, Tag

            db = Database(":memory:")
            db.create_tables()
            with db.session() as session:
                email = Email(
                    message_id="test@example.com", from_addr="a@b.com", date=datetime(2024, 1, 15)
                )
                tag = Tag(name="work", source="mail-memex")
                session.add(email)
                session.add(tag)
                session.flush()
                email.tags.append(tag)
                session.commit()
            mock_get_db.return_value = db

            result = runner.invoke(app, ["tag", "list", "--json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert any(t["name"] == "work" and t["count"] == 1 for t in data)

    # --- tag batch ---

    def test_tag_batch_requires_query(self) -> None:
        """tag batch should require a query argument."""
        result = runner.invoke(app, ["tag", "batch"])
        assert result.exit_code != 0

    def test_tag_batch_no_matches(self) -> None:
        """tag batch should handle no matching emails."""
        import json

        with patch("mail_memex.cli.main.get_db") as mock_get_db:
            from mail_memex.core.database import Database

            db = Database(":memory:")
            db.create_tables()
            mock_get_db.return_value = db

            result = runner.invoke(
                app, ["tag", "batch", "--json", "from:nobody@x.com", "--add", "work"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["matched"] == 0

    # --- Old commands are gone ---

    def test_old_list_tags_removed(self) -> None:
        """The old 'list-tags' top-level command should no longer exist."""
        result = runner.invoke(app, ["list-tags"])
        assert result.exit_code != 0

    def test_old_tag_batch_removed(self) -> None:
        """The old 'tag-batch' top-level command should no longer exist."""
        result = runner.invoke(app, ["tag-batch", "query"])
        assert result.exit_code != 0


class TestRebuildCommand:
    """Tests for the rebuild command group."""

    def test_rebuild_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output
        assert "threads" in result.output

    def test_rebuild_index_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "index", "--help"])
        assert result.exit_code == 0

    def test_rebuild_threads_help(self) -> None:
        result = runner.invoke(app, ["rebuild", "threads", "--help"])
        assert result.exit_code == 0

    def test_old_rebuild_index_removed(self) -> None:
        result = runner.invoke(app, ["rebuild-index", "--help"])
        assert result.exit_code != 0

    def test_old_rebuild_threads_removed(self) -> None:
        result = runner.invoke(app, ["rebuild-threads", "--help"])
        assert result.exit_code != 0


class TestImportCommand:
    """Tests for import subcommands."""

    def test_import_help(self) -> None:
        """import command should have help."""
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0

    def test_import_mbox_help(self) -> None:
        """import mbox should have help."""
        result = runner.invoke(app, ["import", "mbox", "--help"])
        assert result.exit_code == 0

    def test_import_eml_help(self) -> None:
        """import eml should have help."""
        result = runner.invoke(app, ["import", "eml", "--help"])
        assert result.exit_code == 0

    def test_import_gmail_help(self) -> None:
        """import gmail should have help."""
        result = runner.invoke(app, ["import", "gmail", "--help"])
        assert result.exit_code == 0


class TestCLIErrorHandling:
    """Tests for CLI error handling."""

    def test_unknown_command_fails(self) -> None:
        """Unknown commands should fail."""
        result = runner.invoke(app, ["nonexistent_command"])
        assert result.exit_code != 0

    def test_search_requires_query(self) -> None:
        """search command should require query argument."""
        result = runner.invoke(app, ["search"])
        # Either shows help or requires argument
        assert "query" in result.output.lower() or result.exit_code != 0
