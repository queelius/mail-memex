"""TDD Tests for email importer modules.

These tests define the expected behavior of the import system:
- BaseImporter: Abstract base class and ImportStats
- MboxImporter: Unix mbox format with Gmail support
- EmlImporter: Individual EML files
- GmailTakeoutImporter: Gmail Takeout exports
"""

from pathlib import Path

import pytest

from mtk.importers.base import ImportStats
from mtk.importers.eml import EmlImporter, GmailTakeoutImporter
from mtk.importers.mbox import MboxImporter
from mtk.importers.parser import ParsedEmail


class TestImportStats:
    """Tests for ImportStats dataclass."""

    def test_default_values(self) -> None:
        """Should initialize with zero counts."""
        stats = ImportStats()

        assert stats.total_found == 0
        assert stats.imported == 0
        assert stats.skipped_duplicate == 0
        assert stats.skipped_error == 0
        assert stats.errors == []

    def test_success_rate_all_imported(self) -> None:
        """100% success rate when all imported."""
        stats = ImportStats(total_found=10, imported=10)
        assert stats.success_rate == 100.0

    def test_success_rate_partial(self) -> None:
        """Partial success rate calculation."""
        stats = ImportStats(total_found=10, imported=5)
        assert stats.success_rate == 50.0

    def test_success_rate_none_imported(self) -> None:
        """0% success rate when none imported."""
        stats = ImportStats(total_found=10, imported=0)
        assert stats.success_rate == 0.0

    def test_success_rate_empty(self) -> None:
        """100% success rate when nothing to import."""
        stats = ImportStats(total_found=0, imported=0)
        assert stats.success_rate == 100.0

    def test_errors_list_initialization(self) -> None:
        """Errors list should default to empty list."""
        stats1 = ImportStats()
        stats2 = ImportStats()
        # Ensure separate instances
        stats1.errors.append(("path", "error"))
        assert len(stats2.errors) == 0


class TestBaseImporter:
    """Tests for BaseImporter abstract base class."""

    def test_source_path_converted_to_path(self, sample_eml_dir: Path) -> None:
        """Source path string should be converted to Path."""
        importer = EmlImporter(str(sample_eml_dir))
        assert isinstance(importer.source_path, Path)

    def test_nonexistent_path_raises(self, tmp_dir: Path) -> None:
        """Should raise FileNotFoundError for nonexistent path."""
        with pytest.raises(FileNotFoundError, match="Source not found"):
            EmlImporter(tmp_dir / "nonexistent")

    def test_import_all_yields_parsed_emails(self, sample_eml_dir: Path) -> None:
        """import_all should yield (ParsedEmail, None) tuples."""
        importer = EmlImporter(sample_eml_dir)
        results = list(importer.import_all())

        assert len(results) > 0
        for email, error in results:
            if email is not None:
                assert isinstance(email, ParsedEmail)
                assert error is None

    def test_import_all_handles_errors(self, tmp_dir: Path) -> None:
        """import_all should yield (None, error_message) for failures."""
        # Create directory with invalid email
        eml_dir = tmp_dir / "bad_eml"
        eml_dir.mkdir()
        (eml_dir / "bad_email.eml").write_bytes(b"")

        importer = EmlImporter(eml_dir)
        results = list(importer.import_all())

        # Should have one result (bad file) - may or may not error
        assert len(results) == 1


class TestMboxImporter:
    """Tests for mbox format importer."""

    def test_format_name(self, sample_mbox: Path) -> None:
        """Format name should be 'mbox'."""
        importer = MboxImporter(sample_mbox)
        assert importer.format_name == "mbox"

    def test_not_file_raises(self, tmp_dir: Path) -> None:
        """Should raise ValueError if path is not a file."""
        with pytest.raises(ValueError, match="Not a file"):
            MboxImporter(tmp_dir)

    def test_discover_yields_pseudo_paths(self, sample_mbox: Path) -> None:
        """Discover should yield pseudo-paths with indices."""
        importer = MboxImporter(sample_mbox)
        paths = list(importer.discover())

        assert len(paths) == 3  # 3 messages in sample
        for i, path in enumerate(paths):
            assert str(path).endswith(f"#{i}")

    def test_parse_by_index(self, sample_mbox: Path) -> None:
        """Should parse message by pseudo-path index."""
        importer = MboxImporter(sample_mbox)
        pseudo_path = Path(f"{sample_mbox}#0")

        result = importer.parse(pseudo_path)

        assert isinstance(result, ParsedEmail)
        assert result.from_addr == "alice@example.com"
        assert result.subject == "First email"

    def test_parse_stores_mbox_index(self, sample_mbox: Path) -> None:
        """Parsed email should have X-Mbox-Index header."""
        importer = MboxImporter(sample_mbox)
        pseudo_path = Path(f"{sample_mbox}#1")

        result = importer.parse(pseudo_path)

        assert result.raw_headers.get("X-Mbox-Index") == "1"

    def test_invalid_pseudo_path_raises(self, sample_mbox: Path) -> None:
        """Should raise for invalid pseudo-path format."""
        importer = MboxImporter(sample_mbox)

        with pytest.raises(ValueError, match="Invalid mbox pseudo-path"):
            importer.parse(sample_mbox)  # No #index suffix

    def test_import_all_efficiency(self, sample_mbox: Path) -> None:
        """import_all should yield all messages."""
        importer = MboxImporter(sample_mbox)
        results = list(importer.import_all())

        assert len(results) == 3
        subjects = [r[0].subject for r in results if r[0]]
        assert "First email" in subjects
        assert "Second email" in subjects
        assert "Third email" in subjects

    def test_gmail_labels_extracted(self, sample_gmail_mbox: Path) -> None:
        """Should extract X-Gmail-Labels header."""
        importer = MboxImporter(sample_gmail_mbox)
        results = list(importer.import_all())

        assert len(results) == 1
        email, error = results[0]
        assert email is not None
        assert "X-Gmail-Labels" in email.raw_headers
        assert "Inbox" in email.raw_headers["X-Gmail-Labels"]

    def test_mbox_index_header(self, sample_mbox: Path) -> None:
        """Test that mbox index is stored in raw headers."""
        importer = MboxImporter(sample_mbox)
        results = list(importer.import_all())

        for parsed, _ in results:
            if parsed:
                assert "X-Mbox-Index" in parsed.raw_headers


class TestEmlImporter:
    """Tests for EML file importer."""

    def test_format_name(self, sample_eml_dir: Path) -> None:
        """Format name should be 'EML'."""
        importer = EmlImporter(sample_eml_dir)
        assert importer.format_name == "EML"

    def test_discover_single_file(self, tmp_dir: Path) -> None:
        """Should handle single EML file."""
        eml_file = tmp_dir / "single.eml"
        eml_file.write_bytes(b"From: test@example.com\nMessage-ID: <x>\n\nBody")

        importer = EmlImporter(eml_file)
        paths = list(importer.discover())

        assert len(paths) == 1
        assert paths[0] == eml_file

    def test_discover_directory_recursive(self, sample_eml_dir: Path) -> None:
        """Should find EML files recursively by default."""
        importer = EmlImporter(sample_eml_dir, recursive=True)
        paths = list(importer.discover())

        # 5 in main + 1 in subfolder = 6
        assert len(paths) == 6

    def test_discover_directory_non_recursive(self, sample_eml_dir: Path) -> None:
        """Should not recurse when disabled."""
        importer = EmlImporter(sample_eml_dir, recursive=False)
        paths = list(importer.discover())

        # Only 5 in main directory
        assert len(paths) == 5

    def test_custom_extensions(self, tmp_dir: Path) -> None:
        """Should respect custom file extensions."""
        (tmp_dir / "email.eml").write_bytes(b"From: a@x.com\nMessage-ID: <1>\n\nBody")
        (tmp_dir / "email.txt").write_bytes(b"From: b@x.com\nMessage-ID: <2>\n\nBody")
        (tmp_dir / "email.custom").write_bytes(b"From: c@x.com\nMessage-ID: <3>\n\nBody")

        importer = EmlImporter(tmp_dir, extensions=(".custom",))
        paths = list(importer.discover())

        assert len(paths) == 1
        assert paths[0].suffix == ".custom"

    def test_parse_returns_parsed_email(self, sample_eml_dir: Path) -> None:
        """Parsing should return ParsedEmail."""
        importer = EmlImporter(sample_eml_dir)
        paths = list(importer.discover())
        eml_path = [p for p in paths if "email0" in p.name][0]

        result = importer.parse(eml_path)

        assert isinstance(result, ParsedEmail)
        assert result.from_addr == "sender0@example.com"

    def test_file_path_set_on_parsed(self, sample_eml_dir: Path) -> None:
        """Parsed email should have file_path set."""
        importer = EmlImporter(sample_eml_dir)
        paths = list(importer.discover())
        eml_path = paths[0]

        result = importer.parse(eml_path)

        assert result.file_path == eml_path

    def test_msg_extension_supported(self, tmp_dir: Path) -> None:
        """Should support .msg extension by default."""
        msg_file = tmp_dir / "test.msg"
        msg_file.write_bytes(b"From: test@x.com\nMessage-ID: <1>\n\nBody")

        importer = EmlImporter(tmp_dir)
        paths = list(importer.discover())

        assert len(paths) == 1

    def test_import_single_file(self, tmp_dir: Path) -> None:
        """Test importing a single EML file."""
        eml_path = tmp_dir / "test.eml"
        eml_path.write_bytes(b"""From: test@example.com
To: recipient@example.com
Subject: Test EML
Message-ID: <eml-test@example.com>

This is a test email.
""")

        importer = EmlImporter(eml_path)
        results = list(importer.import_all())

        assert len(results) == 1
        parsed, error = results[0]
        assert error is None
        assert parsed.message_id == "eml-test@example.com"

    def test_import_directory(self, tmp_dir: Path) -> None:
        """Test importing directory of EML files."""
        # Create multiple EML files
        for i in range(3):
            eml = tmp_dir / f"email{i}.eml"
            eml.write_bytes(
                f"""From: sender{i}@example.com
Message-ID: <email{i}@example.com>

Body {i}.
""".encode()
            )

        importer = EmlImporter(tmp_dir)
        results = list(importer.import_all())

        successful = [r for r, e in results if r is not None]
        assert len(successful) == 3

    def test_recursive_import(self, tmp_dir: Path) -> None:
        """Test recursive directory import."""
        # Create subdirectory
        subdir = tmp_dir / "subdir"
        subdir.mkdir()

        (tmp_dir / "root.eml").write_bytes(
            b"From: root@example.com\nMessage-ID: <root@example.com>\n\nRoot."
        )
        (subdir / "nested.eml").write_bytes(
            b"From: nested@example.com\nMessage-ID: <nested@example.com>\n\nNested."
        )

        # With recursion
        importer = EmlImporter(tmp_dir, recursive=True)
        results = list(importer.import_all())
        assert len([r for r, _ in results if r]) == 2

        # Without recursion
        importer = EmlImporter(tmp_dir, recursive=False)
        results = list(importer.import_all())
        assert len([r for r, _ in results if r]) == 1


class TestGmailTakeoutImporter:
    """Tests for Gmail Takeout export importer."""

    def test_format_name(self, sample_gmail_mbox: Path) -> None:
        """Format name should be 'Gmail Takeout'."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        assert importer.format_name == "Gmail Takeout"

    def test_single_mbox_file(self, sample_gmail_mbox: Path) -> None:
        """Should import from a single mbox file."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        results = list(importer.import_all())

        assert len(results) == 1
        email, error = results[0]
        assert email is not None
        assert error is None

    def test_discover_yields_pseudo_paths(self, sample_gmail_mbox: Path) -> None:
        """Should yield pseudo-paths like mbox importer."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        paths = list(importer.discover())

        assert len(paths) == 1
        assert "#0" in str(paths[0])

    def test_gmail_labels_extracted(self, sample_gmail_mbox: Path) -> None:
        """Should extract Gmail labels."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        results = list(importer.import_all())

        email, _ = results[0]
        labels = email.raw_headers.get("X-Gmail-Labels", "")
        assert "Inbox" in labels
        assert "Important" in labels
        assert "Starred" in labels

    def test_gmail_thread_id_extracted(self, sample_gmail_mbox: Path) -> None:
        """Should extract Gmail thread ID."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        results = list(importer.import_all())

        email, _ = results[0]
        assert email.raw_headers.get("X-Gmail-Thread-Id") == "thread123"

    def test_folder_label_from_filename(self, sample_gmail_mbox: Path) -> None:
        """Should extract folder label from mbox filename."""
        importer = GmailTakeoutImporter(sample_gmail_mbox)
        results = list(importer.import_all())

        email, _ = results[0]
        # Filename is "All mail Including Spam and Trash.mbox"
        assert "X-Gmail-Folder" in email.raw_headers

    def test_takeout_directory_structure(self, tmp_dir: Path) -> None:
        """Should find mbox files in Takeout/Mail/ directory."""
        # Create Takeout directory structure
        mail_dir = tmp_dir / "Takeout" / "Mail"
        mail_dir.mkdir(parents=True)

        inbox_mbox = mail_dir / "Inbox.mbox"
        inbox_mbox.write_bytes(b"""From test@gmail.com Mon Jan 15 10:00:00 2024
From: test@gmail.com
Message-ID: <inbox1@gmail.com>

Inbox message.

""")

        sent_mbox = mail_dir / "Sent.mbox"
        sent_mbox.write_bytes(b"""From test@gmail.com Mon Jan 15 11:00:00 2024
From: test@gmail.com
Message-ID: <sent1@gmail.com>

Sent message.

""")

        importer = GmailTakeoutImporter(tmp_dir / "Takeout")
        results = list(importer.import_all())

        assert len(results) == 2

        # Check folder labels
        folders = [r[0].raw_headers.get("X-Gmail-Folder") for r in results if r[0]]
        assert "Inbox" in folders
        assert "Sent" in folders


class TestImportAllErrorHandling:
    """Tests for error handling in import_all."""

    def test_continues_after_error(self, tmp_dir: Path) -> None:
        """Should continue importing after individual file errors."""
        eml_dir = tmp_dir / "mixed"
        eml_dir.mkdir()

        # Good email
        (eml_dir / "good.eml").write_bytes(b"From: good@x.com\nMessage-ID: <1>\n\nBody")
        # Bad email (empty)
        (eml_dir / "bad.eml").write_bytes(b"")
        # Another good email
        (eml_dir / "good2.eml").write_bytes(b"From: good2@x.com\nMessage-ID: <2>\n\nBody")

        importer = EmlImporter(eml_dir)
        results = list(importer.import_all())

        # Should have 3 results
        assert len(results) == 3

        # Count successes and failures
        successes = sum(1 for email, error in results if email is not None)
        _failures = sum(1 for email, error in results if error is not None)

        # At least 2 should succeed
        assert successes >= 2

    def test_error_message_includes_path(self, tmp_dir: Path) -> None:
        """Error messages should include the problematic file path."""
        eml_dir = tmp_dir / "errors"
        eml_dir.mkdir()

        # Create invalid email
        bad_path = eml_dir / "invalid.eml"
        bad_path.write_bytes(b"")

        importer = EmlImporter(eml_dir)
        results = list(importer.import_all())

        # Find the error result
        errors = [(email, error) for email, error in results if error is not None]

        if errors:
            _, error_msg = errors[0]
            assert "invalid.eml" in str(error_msg) or str(bad_path) in str(error_msg)
