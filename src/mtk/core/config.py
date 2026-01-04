"""Configuration management for mtk."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class MtkConfig:
    """mtk configuration."""

    # Paths
    maildir: Path | None = None
    notmuch_config: Path | None = None
    db_path: Path | None = None

    # Behavior
    auto_sync: bool = True
    generate_embeddings: bool = False  # Requires semantic extras
    generate_summaries: bool = False  # Requires LLM

    # Privacy defaults
    default_export_allowed: bool = True

    @classmethod
    def default_config_dir(cls) -> Path:
        """Get the default config directory (~/.config/mtk)."""
        return Path.home() / ".config" / "mtk"

    @classmethod
    def default_data_dir(cls) -> Path:
        """Get the default data directory (~/.local/share/mtk)."""
        return Path.home() / ".local" / "share" / "mtk"

    @classmethod
    def load(cls, config_path: Path | None = None) -> MtkConfig:
        """Load configuration from file.

        Args:
            config_path: Path to config file. If None, uses default location.

        Returns:
            Loaded configuration, or defaults if file doesn't exist.
        """
        if config_path is None:
            config_path = cls.default_config_dir() / "config.yaml"

        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MtkConfig:
        """Create config from dictionary."""
        config = cls()

        if data.get("maildir"):
            config.maildir = Path(data["maildir"]).expanduser()
        if data.get("notmuch_config"):
            config.notmuch_config = Path(data["notmuch_config"]).expanduser()
        if data.get("db_path"):
            config.db_path = Path(data["db_path"]).expanduser()

        config.auto_sync = data.get("auto_sync", True)
        config.generate_embeddings = data.get("generate_embeddings", False)
        config.generate_summaries = data.get("generate_summaries", False)
        config.default_export_allowed = data.get("default_export_allowed", True)

        return config

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for serialization."""
        return {
            "maildir": str(self.maildir) if self.maildir else None,
            "notmuch_config": str(self.notmuch_config) if self.notmuch_config else None,
            "db_path": str(self.db_path) if self.db_path else None,
            "auto_sync": self.auto_sync,
            "generate_embeddings": self.generate_embeddings,
            "generate_summaries": self.generate_summaries,
            "default_export_allowed": self.default_export_allowed,
        }

    def save(self, config_path: Path | None = None) -> None:
        """Save configuration to file.

        Args:
            config_path: Path to save to. If None, uses default location.
        """
        if config_path is None:
            config_path = self.default_config_dir() / "config.yaml"

        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False)

    def ensure_dirs(self) -> None:
        """Ensure all required directories exist."""
        self.default_config_dir().mkdir(parents=True, exist_ok=True)
        self.default_data_dir().mkdir(parents=True, exist_ok=True)

        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class PrivacyConfig:
    """Privacy rules configuration."""

    exclude_addresses: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    redact_patterns: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: Path | None = None) -> PrivacyConfig:
        """Load privacy config from file."""
        if config_path is None:
            config_path = MtkConfig.default_config_dir() / "privacy.yaml"

        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrivacyConfig:
        """Create privacy config from dictionary."""
        exclude = data.get("exclude", {})
        redact = data.get("redact", {})

        return cls(
            exclude_addresses=exclude.get("addresses", []),
            exclude_tags=exclude.get("tags", []),
            exclude_patterns=exclude.get("patterns", []),
            redact_patterns=redact.get("patterns", []),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "exclude": {
                "addresses": self.exclude_addresses,
                "tags": self.exclude_tags,
                "patterns": self.exclude_patterns,
            },
            "redact": {
                "patterns": self.redact_patterns,
            },
        }

    def save(self, config_path: Path | None = None) -> None:
        """Save privacy config to file."""
        if config_path is None:
            config_path = MtkConfig.default_config_dir() / "privacy.yaml"

        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w") as f:
            yaml.safe_dump(self.to_dict(), f, default_flow_style=False)
