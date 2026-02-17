"""Base classes for email importers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from mtk.importers.parser import ParsedEmail


@dataclass
class ImportStats:
    """Statistics from an import operation."""

    total_found: int = 0
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_error: int = 0
    errors: list[tuple[str, str]] | None = None  # (path, error message)

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    @property
    def success_rate(self) -> float:
        """Percentage of successfully imported emails."""
        if self.total_found == 0:
            return 100.0
        return (self.imported / self.total_found) * 100


class BaseImporter(ABC):
    """Base class for email importers.

    Subclasses implement source-specific logic for discovering
    and reading email files.
    """

    def __init__(self, source_path: Path | str) -> None:
        """Initialize the importer.

        Args:
            source_path: Path to the email source (directory or file).
        """
        self.source_path = Path(source_path)
        if not self.source_path.exists():
            raise FileNotFoundError(f"Source not found: {self.source_path}")

    @abstractmethod
    def discover(self) -> Iterator[Path]:
        """Discover all email files in the source.

        Yields:
            Paths to individual email files.
        """
        pass

    @abstractmethod
    def parse(self, path: Path) -> ParsedEmail:
        """Parse a single email file.

        Args:
            path: Path to the email file.

        Returns:
            ParsedEmail with extracted data.

        Raises:
            ValueError: If the file cannot be parsed.
        """
        pass

    def import_all(self) -> Iterator[tuple[ParsedEmail | None, str | None]]:
        """Import all emails from the source.

        Yields:
            Tuples of (ParsedEmail, None) on success or (None, error_message) on failure.
        """
        for path in self.discover():
            try:
                parsed = self.parse(path)
                yield parsed, None
            except Exception as e:
                yield None, f"{path}: {e}"

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Human-readable name of this format."""
        pass
