"""Configuration management for mail-memex."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mail_memex.imap.account import ImapAccountConfig


@dataclass
class MailMemexConfig:
    """mail-memex configuration."""

    # Paths
    db_path: Path | None = None

    # IMAP accounts
    imap_accounts: dict[str, ImapAccountConfig] = field(default_factory=dict)

    @classmethod
    def default_config_dir(cls) -> Path:
        """Get the default config directory (~/.config/mail-memex)."""
        return Path.home() / ".config" / "mail-memex"

    @classmethod
    def default_data_dir(cls) -> Path:
        """Get the default data directory (~/.local/share/mail-memex)."""
        return Path.home() / ".local" / "share" / "mail-memex"

    @classmethod
    def load(cls, config_path: Path | None = None) -> MailMemexConfig:
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
    def from_dict(cls, data: dict[str, Any]) -> MailMemexConfig:
        """Create config from dictionary."""
        config = cls()

        if data.get("db_path"):
            config.db_path = Path(data["db_path"]).expanduser()

        # IMAP accounts
        for name, acct_data in data.get("imap_accounts", {}).items():
            config.imap_accounts[name] = ImapAccountConfig.from_dict(name, acct_data)

        return config

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for serialization."""
        data: dict[str, Any] = {
            "db_path": str(self.db_path) if self.db_path else None,
        }
        if self.imap_accounts:
            data["imap_accounts"] = {
                name: acct.to_dict() for name, acct in self.imap_accounts.items()
            }
        return data

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
