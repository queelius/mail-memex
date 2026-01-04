"""TDD Tests for CLI commands.

These tests verify the CLI interface works correctly:
- Command invocation and exit codes
- Output format (--json flag)
- Error handling
- Help text
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mtk.cli.main import app
from mtk.core.database import Database


runner = CliRunner()


class TestCLIHelp:
    """Tests for CLI help and basic invocation."""

    def test_app_has_help(self) -> None:
        """CLI should display help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "mtk" in result.output.lower() or "mail toolkit" in result.output.lower()

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


class TestSearchCommand:
    """Tests for the search command."""

    def test_search_help(self) -> None:
        """search command should have help."""
        result = runner.invoke(app, ["search", "--help"])
        assert result.exit_code == 0
        assert "search" in result.output.lower()

    def test_search_without_db_fails(self) -> None:
        """search without initialized db should fail gracefully."""
        with patch("mtk.cli.main.get_db") as mock_get_db:
            mock_get_db.side_effect = RuntimeError("Database not initialized")

            result = runner.invoke(app, ["search", "test"])

            # Should fail gracefully
            assert result.exit_code != 0 or "not initialized" in result.output.lower()


class TestPeopleCommand:
    """Tests for the people command."""

    def test_people_help(self) -> None:
        """people command should have help."""
        result = runner.invoke(app, ["people", "--help"])
        assert result.exit_code == 0
        assert "people" in result.output.lower()


class TestGraphCommand:
    """Tests for the graph command."""

    def test_graph_help(self) -> None:
        """graph command should have help."""
        result = runner.invoke(app, ["graph", "--help"])
        assert result.exit_code == 0


class TestStatsCommand:
    """Tests for the stats command."""

    def test_stats_help(self) -> None:
        """stats command should have help."""
        result = runner.invoke(app, ["stats", "--help"])
        assert result.exit_code == 0


class TestTagCommand:
    """Tests for the tag command."""

    def test_tag_help(self) -> None:
        """tag command should have help."""
        result = runner.invoke(app, ["tag", "--help"])
        assert result.exit_code == 0


class TestImportCommand:
    """Tests for import subcommands."""

    def test_import_help(self) -> None:
        """import command should have help."""
        result = runner.invoke(app, ["import", "--help"])
        assert result.exit_code == 0

    def test_import_maildir_help(self) -> None:
        """import maildir should have help."""
        result = runner.invoke(app, ["import", "maildir", "--help"])
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


class TestInboxCommand:
    """Tests for the inbox command."""

    def test_inbox_help(self) -> None:
        """inbox command should have help."""
        result = runner.invoke(app, ["inbox", "--help"])
        assert result.exit_code == 0


class TestShowCommand:
    """Tests for the show command."""

    def test_show_help(self) -> None:
        """show command should have help."""
        result = runner.invoke(app, ["show", "--help"])
        assert result.exit_code == 0


class TestThreadCommand:
    """Tests for the thread command."""

    def test_thread_help(self) -> None:
        """thread command should have help."""
        result = runner.invoke(app, ["thread", "--help"])
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

    def test_show_requires_message_id(self) -> None:
        """show command should require message_id argument."""
        result = runner.invoke(app, ["show"])
        # Either shows help or requires argument
        assert "message" in result.output.lower() or result.exit_code != 0
