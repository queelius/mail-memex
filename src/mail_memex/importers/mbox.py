"""mbox format importer.

mbox is a traditional Unix mailbox format where all messages are
concatenated in a single file, separated by "From " lines.
"""

from __future__ import annotations

import mailbox
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mail_memex.importers.base import BaseImporter
from mail_memex.importers.parser import EmailParser, ParsedEmail


class MboxImporter(BaseImporter):
    """Import emails from mbox format.

    Supports standard mbox files as well as Gmail Takeout MBOX exports.
    """

    def __init__(self, source_path: Path | str) -> None:
        """Initialize the mbox importer.

        Args:
            source_path: Path to the mbox file.
        """
        super().__init__(source_path)
        self.parser = EmailParser()

        if not self.source_path.is_file():
            raise ValueError(f"Not a file: {self.source_path}")

    @property
    def format_name(self) -> str:
        return "mbox"

    def discover(self) -> Iterator[Path]:
        """For mbox, we yield the file path for each message index.

        Note: We use a different approach - iterate via mailbox module.
        """
        # mbox doesn't have individual files, so we yield the source
        # with an index appended for tracking
        mbox = mailbox.mbox(self.source_path)
        try:
            for i in range(len(mbox)):
                # Create a pseudo-path for tracking
                yield Path(f"{self.source_path}#{i}")
        finally:
            mbox.close()

    def _parse_mbox_message(self, msg: Any, file_path: Path, index: int) -> ParsedEmail:
        """Parse a mailbox.mboxMessage and annotate with index + Gmail labels."""
        parsed = self.parser.parse_bytes(bytes(msg))
        parsed.file_path = file_path
        parsed.raw_headers["X-Mbox-Index"] = str(index)
        gmail_labels = msg.get("X-Gmail-Labels")
        if gmail_labels:
            parsed.raw_headers["X-Gmail-Labels"] = gmail_labels
        return parsed

    def parse(self, path: Path) -> ParsedEmail:
        """Parse a message from the mbox.

        The path format is: /path/to/file.mbox#index
        """
        path_str = str(path)
        if "#" not in path_str:
            raise ValueError(f"Invalid mbox pseudo-path: {path}")
        file_path, index_str = path_str.rsplit("#", 1)
        index = int(index_str)

        mbox = mailbox.mbox(file_path)
        try:
            msg = mbox[index]  # type: ignore[index]
            if msg is None:
                raise ValueError(f"Message {index} not found in mbox")
            return self._parse_mbox_message(msg, Path(file_path), index)
        finally:
            mbox.close()

    def import_all(self) -> Iterator[tuple[ParsedEmail | None, str | None]]:
        """Import all messages from the mbox file.

        Overridden for efficiency - opens mbox once instead of per-message.
        """
        mbox = mailbox.mbox(self.source_path)
        try:
            for i, msg in enumerate(mbox):
                try:
                    yield self._parse_mbox_message(msg, self.source_path, i), None
                except Exception as e:
                    yield None, f"{self.source_path}#{i}: {e}"
        finally:
            mbox.close()
