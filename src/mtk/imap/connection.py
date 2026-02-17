"""IMAP connection management with retry support."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType

    from mtk.imap.account import ImapAccountConfig


@dataclass
class ServerCapabilities:
    """Detected IMAP server capabilities."""

    condstore: bool = False
    idle: bool = False
    compress: bool = False
    gmail_extensions: bool = False
    raw_capabilities: list[str] = field(default_factory=list)


class ImapConnection:
    """Context manager for IMAP connections with retry.

    Usage:
        with ImapConnection(account_config, password) as client:
            client.select_folder("INBOX")
            ...
    """

    def __init__(
        self,
        account: ImapAccountConfig,
        password: str,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        self.account = account
        self.password = password
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client: Any = None
        self.capabilities = ServerCapabilities()

    def __enter__(self) -> Any:
        try:
            from imapclient import IMAPClient
        except ImportError:
            raise ImportError(
                "IMAP support requires imapclient. Install with: pip install mtk[imap]"
            ) from None

        last_error = None
        for attempt in range(self.max_retries):
            try:
                self._client = IMAPClient(
                    self.account.host,
                    port=self.account.port,
                    ssl=self.account.use_ssl,
                )
                if self.account.oauth2:
                    # OAuth2 authentication
                    self._client.oauth2_login(self.account.username, self.password)
                else:
                    self._client.login(self.account.username, self.password)

                self._detect_capabilities()
                return self._client
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        raise ConnectionError(
            f"Failed to connect to {self.account.host} after {self.max_retries} attempts: {last_error}"
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client:
            with contextlib.suppress(Exception):
                self._client.logout()
            self._client = None

    def _detect_capabilities(self) -> None:
        """Detect server capabilities after login."""
        if not self._client:
            return

        try:
            caps = self._client.capabilities()
            cap_strs = [c.decode() if isinstance(c, bytes) else str(c) for c in caps]
            self.capabilities.raw_capabilities = cap_strs

            cap_upper = [c.upper() for c in cap_strs]
            self.capabilities.condstore = "CONDSTORE" in cap_upper
            self.capabilities.idle = "IDLE" in cap_upper
            self.capabilities.compress = "COMPRESS=DEFLATE" in cap_upper

            # Gmail extensions
            self.capabilities.gmail_extensions = any(c.startswith("X-GM-") for c in cap_upper)
        except Exception:
            pass
