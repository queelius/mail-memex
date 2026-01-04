"""Maildir format importer.

Maildir is a standard email storage format where each message is stored
in a separate file. The directory structure is:
    maildir/
        cur/    - Read messages
        new/    - Unread messages
        tmp/    - Temporary files (during delivery)
        .folder/ - Subfolders (optional, nested Maildirs)
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mtk.importers.base import BaseImporter
from mtk.importers.parser import EmailParser, ParsedEmail


class MaildirImporter(BaseImporter):
    """Import emails from Maildir format."""

    def __init__(
        self,
        source_path: Path | str,
        *,
        include_subfolders: bool = True,
    ) -> None:
        """Initialize the Maildir importer.

        Args:
            source_path: Path to the Maildir root directory.
            include_subfolders: Whether to import from .folder subdirectories.
        """
        super().__init__(source_path)
        self.include_subfolders = include_subfolders
        self.parser = EmailParser()

        # Validate it looks like a Maildir
        if not self._is_maildir(self.source_path):
            raise ValueError(
                f"Not a valid Maildir: {self.source_path} "
                "(missing cur, new, or tmp directories)"
            )

    @property
    def format_name(self) -> str:
        return "Maildir"

    def _is_maildir(self, path: Path) -> bool:
        """Check if a path looks like a Maildir."""
        # A Maildir must have cur and new (tmp is optional for our purposes)
        return (path / "cur").is_dir() and (path / "new").is_dir()

    def _find_maildirs(self) -> Iterator[Path]:
        """Find all Maildir directories to import from."""
        yield self.source_path

        if self.include_subfolders:
            # Look for .folder subdirectories (standard Maildir++ format)
            for item in self.source_path.iterdir():
                if item.name.startswith(".") and item.is_dir():
                    if self._is_maildir(item):
                        yield item

    def discover(self) -> Iterator[Path]:
        """Discover all email files in the Maildir.

        Yields:
            Paths to individual email files in cur/ and new/ directories.
        """
        for maildir in self._find_maildirs():
            # Import from cur (read messages)
            cur_dir = maildir / "cur"
            if cur_dir.exists():
                for email_file in cur_dir.iterdir():
                    if email_file.is_file():
                        yield email_file

            # Import from new (unread messages)
            new_dir = maildir / "new"
            if new_dir.exists():
                for email_file in new_dir.iterdir():
                    if email_file.is_file():
                        yield email_file

    def parse(self, path: Path) -> ParsedEmail:
        """Parse a single Maildir email file.

        Also extracts Maildir flags from the filename.
        """
        parsed = self.parser.parse_file(path)

        # Extract Maildir flags from filename if present
        # Format: unique_id:2,FLAGS (e.g., "1234567890.M123456:2,RS")
        flags = self._extract_flags(path.name)
        parsed.raw_headers["X-Maildir-Flags"] = flags

        return parsed

    def _extract_flags(self, filename: str) -> str:
        """Extract Maildir flags from filename.

        Maildir flags (after :2,):
            P - Passed (forwarded)
            R - Replied
            S - Seen (read)
            T - Trashed
            D - Draft
            F - Flagged (starred)
        """
        if ":2," in filename:
            return filename.split(":2,")[1]
        return ""
