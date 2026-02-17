"""Authentication management for IMAP accounts.

Credentials stored in system keyring (never in config files).
"""

from __future__ import annotations

import contextlib
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mtk.imap.account import ImapAccountConfig


class AuthManager:
    """Manage IMAP credentials via system keyring."""

    def __init__(self) -> None:
        try:
            import keyring as _keyring

            self._keyring: ModuleType | None = _keyring
        except ImportError:
            self._keyring = None

    @property
    def available(self) -> bool:
        """Check if keyring is available."""
        return self._keyring is not None

    def store_password(self, account: ImapAccountConfig, password: str) -> None:
        """Store password in system keyring."""
        if not self._keyring:
            raise RuntimeError("keyring not available. Install with: pip install mtk[imap]")
        self._keyring.set_password(account.keyring_service, account.username, password)

    def get_password(self, account: ImapAccountConfig) -> str | None:
        """Retrieve password from system keyring."""
        if not self._keyring:
            return None
        result: str | None = self._keyring.get_password(account.keyring_service, account.username)
        return result

    def delete_password(self, account: ImapAccountConfig) -> None:
        """Remove password from system keyring."""
        if not self._keyring:
            return
        with contextlib.suppress(Exception):
            self._keyring.delete_password(account.keyring_service, account.username)


class GmailOAuth2:
    """Gmail OAuth2 token management.

    Uses google-auth-oauthlib for the OAuth2 flow.
    Stores refresh token in keyring.
    """

    SCOPES = ["https://mail.google.com/"]

    def __init__(self, account: ImapAccountConfig) -> None:
        self.account = account
        self._auth_manager = AuthManager()

    def get_access_token(self) -> str | None:
        """Get a valid OAuth2 access token.

        Returns None if no refresh token is stored.
        """
        refresh_token = self._auth_manager.get_password(self.account)
        if not refresh_token:
            return None

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            creds = Credentials(  # type: ignore[no-untyped-call]
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self._get_client_id(),
                client_secret=self._get_client_secret(),
            )
            creds.refresh(Request())  # type: ignore[no-untyped-call]
            return creds.token
        except Exception:
            return None

    def authorize(self, client_id: str, client_secret: str) -> str:
        """Run OAuth2 authorization flow.

        Returns the refresh token (also stored in keyring).
        """
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            raise ImportError(
                "Gmail OAuth2 requires google-auth-oauthlib. "
                "Install with: pip install mtk[imap-oauth]"
            ) from None

        flow = InstalledAppFlow.from_client_config(
            {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            self.SCOPES,
        )
        creds = flow.run_local_server(port=0)

        # Store refresh token in keyring
        if creds.refresh_token:
            self._auth_manager.store_password(self.account, creds.refresh_token)

        return creds.refresh_token or ""

    def _get_client_id(self) -> str:
        """Get OAuth2 client ID from environment."""
        import os

        return os.environ.get("MTK_GMAIL_CLIENT_ID", "")

    def _get_client_secret(self) -> str:
        """Get OAuth2 client secret from environment."""
        import os

        return os.environ.get("MTK_GMAIL_CLIENT_SECRET", "")
