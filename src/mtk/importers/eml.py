"""EML file importer.

EML is a standard format for individual email messages, commonly used
for email exports and as attachments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from mtk.importers.base import BaseImporter
from mtk.importers.parser import EmailParser, ParsedEmail


class EmlImporter(BaseImporter):
    """Import emails from EML files.

    Can import a single EML file or a directory of EML files.
    """

    def __init__(
        self,
        source_path: Path | str,
        *,
        recursive: bool = True,
        extensions: tuple[str, ...] = (".eml", ".email", ".msg"),
    ) -> None:
        """Initialize the EML importer.

        Args:
            source_path: Path to an EML file or directory of EML files.
            recursive: Whether to search subdirectories.
            extensions: File extensions to treat as EML files.
        """
        super().__init__(source_path)
        self.recursive = recursive
        self.extensions = extensions
        self.parser = EmailParser()

    @property
    def format_name(self) -> str:
        return "EML"

    def discover(self) -> Iterator[Path]:
        """Discover all EML files.

        Yields:
            Paths to EML files.
        """
        if self.source_path.is_file():
            if self._is_eml_file(self.source_path):
                yield self.source_path
        else:
            # Directory - find all EML files
            pattern = "**/*" if self.recursive else "*"
            for path in self.source_path.glob(pattern):
                if path.is_file() and self._is_eml_file(path):
                    yield path

    def _is_eml_file(self, path: Path) -> bool:
        """Check if a file is an EML file by extension."""
        return path.suffix.lower() in self.extensions

    def parse(self, path: Path) -> ParsedEmail:
        """Parse a single EML file."""
        return self.parser.parse_file(path)


class GmailTakeoutImporter(BaseImporter):
    """Import emails from Gmail Takeout export.

    Gmail Takeout exports mail as MBOX files, possibly in a nested structure:
        Takeout/
            Mail/
                All mail Including Spam and Trash.mbox
                Inbox.mbox
                Sent.mbox
                ...
    """

    def __init__(self, source_path: Path | str) -> None:
        """Initialize Gmail Takeout importer.

        Args:
            source_path: Path to the Takeout directory or a specific mbox file.
        """
        super().__init__(source_path)
        self.parser = EmailParser()

    @property
    def format_name(self) -> str:
        return "Gmail Takeout"

    def _find_mbox_files(self) -> Iterator[Path]:
        """Find all mbox files in the Takeout export."""
        if self.source_path.is_file():
            if self.source_path.suffix.lower() == ".mbox":
                yield self.source_path
        else:
            # Look for Mail directory
            mail_dir = self.source_path / "Mail"
            if not mail_dir.exists():
                mail_dir = self.source_path

            for mbox_file in mail_dir.glob("**/*.mbox"):
                yield mbox_file

    def discover(self) -> Iterator[Path]:
        """Discover all email messages in the Takeout.

        Yields pseudo-paths in format: path/to/file.mbox#index
        """
        import mailbox

        for mbox_path in self._find_mbox_files():
            mbox = mailbox.mbox(mbox_path)
            try:
                for i in range(len(mbox)):
                    yield Path(f"{mbox_path}#{i}")
            finally:
                mbox.close()

    def parse(self, path: Path) -> ParsedEmail:
        """Parse a message from a Takeout mbox."""
        import mailbox

        path_str = str(path)
        if "#" not in path_str:
            raise ValueError(f"Invalid pseudo-path: {path}")

        file_path, index_str = path_str.rsplit("#", 1)
        index = int(index_str)

        mbox = mailbox.mbox(file_path)
        try:
            msg = mbox[index]
            if msg is None:
                raise ValueError(f"Message {index} not found")

            parsed = self.parser.parse_bytes(bytes(msg))
            parsed.file_path = Path(file_path)
            parsed.raw_headers["X-Mbox-Index"] = str(index)

            # Extract Gmail-specific headers
            for header in ["X-Gmail-Labels", "X-Gmail-Thread-Id"]:
                value = msg.get(header)
                if value:
                    parsed.raw_headers[header] = value

            return parsed
        finally:
            mbox.close()

    def import_all(self) -> Iterator[tuple[ParsedEmail | None, str | None]]:
        """Import all messages from all mbox files in Takeout."""
        import mailbox

        for mbox_path in self._find_mbox_files():
            # Extract label from filename (e.g., "Inbox.mbox" -> "Inbox")
            label = mbox_path.stem

            mbox = mailbox.mbox(mbox_path)
            try:
                for i, msg in enumerate(mbox):
                    try:
                        parsed = self.parser.parse_bytes(bytes(msg))
                        parsed.file_path = mbox_path
                        parsed.raw_headers["X-Mbox-Index"] = str(i)
                        parsed.raw_headers["X-Gmail-Folder"] = label

                        for header in ["X-Gmail-Labels", "X-Gmail-Thread-Id"]:
                            value = msg.get(header)
                            if value:
                                parsed.raw_headers[header] = value

                        yield parsed, None
                    except Exception as e:
                        yield None, f"{mbox_path}#{i}: {e}"
            finally:
                mbox.close()
