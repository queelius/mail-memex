"""Tests for IMAP sync module.

Tests cover:
- Tag mapping (IMAP flags to mail-memex tags, Gmail labels to mail-memex tags)
- Auth manager
- Sync state management
- Pull sync with mock IMAPClient
- Auth manager (keyring-based credential storage)
- IMAP connection management
- Pull sync (incremental fetch from IMAP server)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from mail_memex.core.database import Database
from mail_memex.core.models import Email, ImapSyncState, Tag
from mail_memex.imap.account import ImapAccountConfig
from mail_memex.imap.gmail import GmailExtensions
from mail_memex.imap.mapping import TagMapper

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def imap_account() -> ImapAccountConfig:
    """Test IMAP account config."""
    return ImapAccountConfig(
        name="test",
        host="imap.example.com",
        port=993,
        username="user@example.com",
        use_ssl=True,
        provider="generic",
        folders=["INBOX", "Sent"],
    )


@pytest.fixture
def gmail_account() -> ImapAccountConfig:
    """Test Gmail account config."""
    return ImapAccountConfig(
        name="gmail",
        host="imap.gmail.com",
        port=993,
        username="user@gmail.com",
        use_ssl=True,
        provider="gmail",
        folders=["INBOX"],
        oauth2=True,
    )


@pytest.fixture
def imap_db() -> Database:
    """Database with IMAP models."""
    database = Database(":memory:")
    database.create_tables()
    return database


@pytest.fixture
def imap_populated_db(imap_db: Database) -> Database:
    """Database with IMAP-tracked emails."""
    with imap_db.session() as session:
        # Create emails with IMAP tracking
        email1 = Email(
            message_id="imap1@example.com",
            from_addr="sender@example.com",
            from_name="Sender",
            subject="IMAP Email 1",
            body_text="Body of IMAP email 1",
            date=datetime(2024, 1, 15, 10, 0),
            imap_uid=100,
            imap_account="test",
            imap_folder="INBOX",
        )
        email2 = Email(
            message_id="imap2@example.com",
            from_addr="sender2@example.com",
            subject="IMAP Email 2",
            body_text="Body of IMAP email 2",
            date=datetime(2024, 1, 15, 11, 0),
            imap_uid=101,
            imap_account="test",
            imap_folder="INBOX",
        )
        email3 = Email(
            message_id="local@example.com",
            from_addr="local@example.com",
            subject="Local Email (not IMAP)",
            body_text="This email is not from IMAP",
            date=datetime(2024, 1, 15, 12, 0),
        )
        session.add_all([email1, email2, email3])

        # Create sync state
        state = ImapSyncState(
            account_name="test",
            folder="INBOX",
            uid_validity=12345,
            last_uid=101,
            last_sync=datetime(2024, 1, 15, 12, 0),
        )
        session.add(state)

        # Create some tags
        read_tag = Tag(name="read", source="imap")
        flagged_tag = Tag(name="flagged", source="imap")
        session.add_all([read_tag, flagged_tag])
        session.flush()

        email1.tags.append(read_tag)
        email1.tags.append(flagged_tag)

        session.commit()

    return imap_db


# =============================================================================
# Tag Mapping Tests
# =============================================================================


class TestTagMapper:
    """Tests for IMAP to mail-memex tag mapping."""

    def test_imap_flags_to_tags(self) -> None:
        """Standard IMAP flags should map to mail-memex tags."""
        mapper = TagMapper()
        tags = mapper.imap_to_tags(["\\Seen", "\\Flagged"])
        assert "read" in tags
        assert "flagged" in tags

    def test_unknown_flag_ignored(self) -> None:
        """Unknown flags should be ignored."""
        mapper = TagMapper()
        tags = mapper.imap_to_tags(["\\Seen", "\\CustomFlag"])
        assert "read" in tags
        assert len(tags) == 1

    def test_gmail_labels_to_tags(self) -> None:
        """Gmail labels should map to mail-memex tags."""
        mapper = TagMapper(is_gmail=True)
        tags = mapper.imap_to_tags(["\\Seen"], ["\\Inbox", "\\Starred", "CATEGORY_SOCIAL"])
        assert "read" in tags
        assert "inbox" in tags
        assert "starred" in tags
        assert "social" in tags

    def test_gmail_custom_labels_passthrough(self) -> None:
        """Unknown Gmail labels should pass through as lowercase."""
        mapper = TagMapper(is_gmail=True)
        tags = mapper.imap_to_tags([], ["MyCustomLabel"])
        assert "mycustomlabel" in tags

    def test_bytes_flags(self) -> None:
        """Should handle bytes flags (as returned by imapclient)."""
        mapper = TagMapper()
        tags = mapper.imap_to_tags([b"\\Seen", b"\\Flagged"])
        assert "read" in tags
        assert "flagged" in tags

    def test_custom_mappings(self) -> None:
        """Custom mappings should work alongside standard ones."""
        mapper = TagMapper(custom_mappings={"$Junk": "spam"})
        tags = mapper.imap_to_tags(["\\Seen", "$Junk"])
        assert "read" in tags
        assert "spam" in tags


# =============================================================================
# Gmail Extensions Tests
# =============================================================================


class TestGmailExtensions:
    """Tests for Gmail-specific IMAP extensions."""

    def test_extract_labels(self) -> None:
        labels = GmailExtensions.extract_labels(
            {
                b"X-GM-LABELS": (b"\\Inbox", b"\\Important", b"MyLabel"),
            }
        )
        assert labels == ["\\Inbox", "\\Important", "MyLabel"]

    def test_extract_labels_empty(self) -> None:
        labels = GmailExtensions.extract_labels({})
        assert labels == []

    def test_extract_thread_id(self) -> None:
        thrid = GmailExtensions.extract_thread_id({b"X-GM-THRID": 123456789})
        assert thrid == "123456789"

    def test_extract_thread_id_missing(self) -> None:
        thrid = GmailExtensions.extract_thread_id({})
        assert thrid is None

    def test_build_fetch_items(self) -> None:
        items = GmailExtensions.build_fetch_items(gmail_extensions=True)
        assert "X-GM-LABELS" in items
        assert "X-GM-THRID" in items

    def test_build_fetch_items_no_gmail(self) -> None:
        items = GmailExtensions.build_fetch_items(gmail_extensions=False)
        assert "X-GM-LABELS" not in items


# =============================================================================
# Account Tests
# =============================================================================


class TestImapAccountConfig:
    """Tests for IMAP account config."""

    def test_is_gmail(self, gmail_account: ImapAccountConfig) -> None:
        assert gmail_account.is_gmail is True

    def test_not_gmail(self, imap_account: ImapAccountConfig) -> None:
        assert imap_account.is_gmail is False

    def test_keyring_service(self, imap_account: ImapAccountConfig) -> None:
        assert imap_account.keyring_service == "mail-memex-imap-test"


# =============================================================================
# Sync State Tests
# =============================================================================


class TestSyncState:
    """Tests for IMAP sync state management."""

    def test_sync_state_created(self, imap_populated_db: Database) -> None:
        """Sync state should be created correctly."""
        with imap_populated_db.session() as session:
            state = session.execute(
                select(ImapSyncState).where(
                    ImapSyncState.account_name == "test",
                    ImapSyncState.folder == "INBOX",
                )
            ).scalar()
            assert state is not None
            assert state.uid_validity == 12345
            assert state.last_uid == 101

    def test_imap_tracked_email(self, imap_populated_db: Database) -> None:
        """Emails should have IMAP tracking fields."""
        with imap_populated_db.session() as session:
            email = session.execute(
                select(Email).where(Email.message_id == "imap1@example.com")
            ).scalar()
            assert email is not None
            assert email.imap_uid == 100
            assert email.imap_account == "test"
            assert email.imap_folder == "INBOX"

    def test_non_imap_email(self, imap_populated_db: Database) -> None:
        """Non-IMAP emails should have null tracking fields."""
        with imap_populated_db.session() as session:
            email = session.execute(
                select(Email).where(Email.message_id == "local@example.com")
            ).scalar()
            assert email is not None
            assert email.imap_uid is None
            assert email.imap_account is None


# =============================================================================
# CLI Command Tests
# =============================================================================


class TestImapCLI:
    """Tests for IMAP CLI commands."""

    def test_imap_help(self) -> None:
        """imap subcommand should have help."""
        from typer.testing import CliRunner

        from mail_memex.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["imap", "--help"])
        assert result.exit_code == 0
        assert "imap" in result.output.lower()

    def test_imap_accounts_help(self) -> None:
        """imap accounts should have help."""
        from typer.testing import CliRunner

        from mail_memex.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["imap", "accounts", "--help"])
        assert result.exit_code == 0


# =============================================================================
# Auth Manager Tests
# =============================================================================


class TestAuthManager:
    """Tests for IMAP AuthManager (keyring-based credential storage)."""

    def test_get_password_returns_stored_value(self, imap_account: ImapAccountConfig) -> None:
        """get_password should retrieve password from keyring."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "s3cret"

        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            from mail_memex.imap.auth import AuthManager

            mgr = AuthManager()
            # Force the keyring module reference
            mgr._keyring = mock_keyring

            result = mgr.get_password(imap_account)
            assert result == "s3cret"
            mock_keyring.get_password.assert_called_once_with(
                imap_account.keyring_service, imap_account.username
            )

    def test_get_password_returns_none_when_not_found(
        self, imap_account: ImapAccountConfig
    ) -> None:
        """get_password should return None when no password is stored."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None

        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = mock_keyring

        result = mgr.get_password(imap_account)
        assert result is None

    def test_get_password_returns_none_when_keyring_unavailable(
        self, imap_account: ImapAccountConfig
    ) -> None:
        """get_password should return None when keyring is not available."""
        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = None

        result = mgr.get_password(imap_account)
        assert result is None

    def test_store_password(self, imap_account: ImapAccountConfig) -> None:
        """store_password should save password in keyring."""
        mock_keyring = MagicMock()

        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = mock_keyring

        mgr.store_password(imap_account, "newpassword")
        mock_keyring.set_password.assert_called_once_with(
            imap_account.keyring_service, imap_account.username, "newpassword"
        )

    def test_store_password_raises_when_keyring_unavailable(
        self, imap_account: ImapAccountConfig
    ) -> None:
        """store_password should raise RuntimeError when keyring is not available."""
        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = None

        with pytest.raises(RuntimeError, match="keyring not available"):
            mgr.store_password(imap_account, "password")

    def test_delete_password(self, imap_account: ImapAccountConfig) -> None:
        """delete_password should remove password from keyring."""
        mock_keyring = MagicMock()

        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = mock_keyring

        mgr.delete_password(imap_account)
        mock_keyring.delete_password.assert_called_once_with(
            imap_account.keyring_service, imap_account.username
        )

    def test_delete_password_suppresses_errors(self, imap_account: ImapAccountConfig) -> None:
        """delete_password should suppress exceptions from keyring."""
        mock_keyring = MagicMock()
        mock_keyring.delete_password.side_effect = Exception("keyring error")

        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = mock_keyring

        # Should not raise
        mgr.delete_password(imap_account)

    def test_delete_password_noop_when_keyring_unavailable(
        self, imap_account: ImapAccountConfig
    ) -> None:
        """delete_password should do nothing when keyring is not available."""
        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = None

        # Should not raise
        mgr.delete_password(imap_account)

    def test_available_property_true(self) -> None:
        """available should return True when keyring is loaded."""
        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = MagicMock()

        assert mgr.available is True

    def test_available_property_false(self) -> None:
        """available should return False when keyring is None."""
        from mail_memex.imap.auth import AuthManager

        mgr = AuthManager()
        mgr._keyring = None

        assert mgr.available is False


class TestGmailOAuth2:
    """Tests for Gmail OAuth2 token management."""

    def test_get_access_token_with_refresh_token(self, gmail_account: ImapAccountConfig) -> None:
        """get_access_token should use refresh token to get access token."""
        from mail_memex.imap.auth import GmailOAuth2

        mock_creds = MagicMock()
        mock_creds.token = "access_token_123"

        with (
            patch.object(GmailOAuth2, "__init__", lambda self, account: None),
            patch("mail_memex.imap.auth.AuthManager"),
            patch("google.oauth2.credentials.Credentials", return_value=mock_creds),
            patch("google.auth.transport.requests.Request"),
        ):
            oauth = GmailOAuth2.__new__(GmailOAuth2)
            oauth.account = gmail_account
            mock_auth_mgr = MagicMock()
            mock_auth_mgr.get_password.return_value = "refresh_token_xyz"
            oauth._auth_manager = mock_auth_mgr

            result = oauth.get_access_token()
            assert result == "access_token_123"
            mock_creds.refresh.assert_called_once()

    def test_get_access_token_returns_none_without_refresh_token(
        self, gmail_account: ImapAccountConfig
    ) -> None:
        """get_access_token should return None if no refresh token is stored."""
        from mail_memex.imap.auth import GmailOAuth2

        oauth = GmailOAuth2.__new__(GmailOAuth2)
        oauth.account = gmail_account
        mock_auth_mgr = MagicMock()
        mock_auth_mgr.get_password.return_value = None
        oauth._auth_manager = mock_auth_mgr

        result = oauth.get_access_token()
        assert result is None

    def test_get_access_token_returns_none_on_error(self, gmail_account: ImapAccountConfig) -> None:
        """get_access_token should return None when refresh fails."""
        from mail_memex.imap.auth import GmailOAuth2

        mock_creds = MagicMock()
        mock_creds.refresh.side_effect = Exception("Network error")

        with (
            patch("google.oauth2.credentials.Credentials", return_value=mock_creds),
            patch("google.auth.transport.requests.Request"),
        ):
            oauth = GmailOAuth2.__new__(GmailOAuth2)
            oauth.account = gmail_account
            mock_auth_mgr = MagicMock()
            mock_auth_mgr.get_password.return_value = "refresh_token_xyz"
            oauth._auth_manager = mock_auth_mgr

            # Patch the env methods
            oauth._get_client_id = lambda: "client_id"
            oauth._get_client_secret = lambda: "client_secret"

            result = oauth.get_access_token()
            assert result is None


# =============================================================================
# IMAP Connection Tests
# =============================================================================


class TestImapConnection:
    """Tests for IMAP connection management."""

    def test_ssl_connection_login(self, imap_account: ImapAccountConfig) -> None:
        """Should create SSL connection and use password login for generic account."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(imap_account, "mypassword", max_retries=1)

        # Mock the import inside __enter__
        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            client = conn.__enter__()

        assert client is mock_client
        mock_cls.assert_called_once_with(
            imap_account.host,
            port=imap_account.port,
            ssl=imap_account.use_ssl,
        )
        mock_client.login.assert_called_once_with(imap_account.username, "mypassword")
        mock_client.oauth2_login.assert_not_called()

    def test_oauth2_connection_login(self, gmail_account: ImapAccountConfig) -> None:
        """Should use oauth2_login for OAuth2 accounts."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1", b"X-GM-EXT-1"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(gmail_account, "oauth2_token", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            client = conn.__enter__()

        assert client is mock_client
        mock_client.oauth2_login.assert_called_once_with(gmail_account.username, "oauth2_token")
        mock_client.login.assert_not_called()

    def test_non_ssl_connection(self) -> None:
        """Should create non-SSL connection when use_ssl is False."""
        from mail_memex.imap.connection import ImapConnection

        account = ImapAccountConfig(
            name="plaintext",
            host="imap.local",
            port=143,
            username="user@local",
            use_ssl=False,
        )

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(account, "password", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()

        mock_cls.assert_called_once_with("imap.local", port=143, ssl=False)

    def test_exit_logs_out_and_cleans_up(self, imap_account: ImapAccountConfig) -> None:
        """__exit__ should logout and set client to None."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(imap_account, "password", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()
            conn.__exit__(None, None, None)

        mock_client.logout.assert_called_once()
        assert conn._client is None

    def test_exit_suppresses_logout_errors(self, imap_account: ImapAccountConfig) -> None:
        """__exit__ should suppress errors during logout."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1"]
        mock_client.logout.side_effect = Exception("Connection reset")
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(imap_account, "password", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()
            # Should not raise
            conn.__exit__(None, None, None)

        assert conn._client is None

    def test_retry_on_connection_failure(self, imap_account: ImapAccountConfig) -> None:
        """Should retry connection on failure up to max_retries."""
        from mail_memex.imap.connection import ImapConnection

        mock_client_good = MagicMock()
        mock_client_good.capabilities.return_value = [b"IMAP4rev1"]

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return mock_client_good

        mock_cls = MagicMock(side_effect=side_effect)

        conn = ImapConnection(imap_account, "password", max_retries=3, retry_delay=0.0)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}), patch("time.sleep"):
            client = conn.__enter__()

        assert client is mock_client_good
        assert call_count == 3

    def test_connection_error_after_max_retries(self, imap_account: ImapAccountConfig) -> None:
        """Should raise ConnectionError after exhausting retries."""
        from mail_memex.imap.connection import ImapConnection

        mock_cls = MagicMock(side_effect=ConnectionError("Connection refused"))

        conn = ImapConnection(imap_account, "password", max_retries=2, retry_delay=0.0)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with (
            patch.dict("sys.modules", {"imapclient": fake_imapclient}),
            patch("time.sleep"),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            conn.__enter__()

    def test_imapclient_import_error(self, imap_account: ImapAccountConfig) -> None:
        """Should raise ImportError when imapclient is not installed."""
        from mail_memex.imap.connection import ImapConnection

        conn = ImapConnection(imap_account, "password")

        with (
            patch.dict("sys.modules", {"imapclient": None}),
            pytest.raises(ImportError, match="imapclient"),
        ):
            conn.__enter__()

    def test_detect_capabilities_condstore(self, imap_account: ImapAccountConfig) -> None:
        """Should detect CONDSTORE capability."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1", b"CONDSTORE", b"IDLE"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(imap_account, "password", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()

        assert conn.capabilities.condstore is True
        assert conn.capabilities.idle is True
        assert "CONDSTORE" in conn.capabilities.raw_capabilities

    def test_detect_capabilities_gmail_extensions(self, gmail_account: ImapAccountConfig) -> None:
        """Should detect Gmail extensions in capabilities."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1", b"X-GM-EXT-1"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(gmail_account, "token", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()

        assert conn.capabilities.gmail_extensions is True

    def test_detect_capabilities_compress(self, imap_account: ImapAccountConfig) -> None:
        """Should detect COMPRESS=DEFLATE capability."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1", b"COMPRESS=DEFLATE"]
        mock_cls = MagicMock(return_value=mock_client)

        conn = ImapConnection(imap_account, "password", max_retries=1)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn.__enter__()

        assert conn.capabilities.compress is True

    def test_context_manager_usage(self, imap_account: ImapAccountConfig) -> None:
        """Should work properly as a context manager."""
        from mail_memex.imap.connection import ImapConnection

        mock_client = MagicMock()
        mock_client.capabilities.return_value = [b"IMAP4rev1"]
        mock_cls = MagicMock(return_value=mock_client)

        import types

        fake_imapclient = types.ModuleType("imapclient")
        fake_imapclient.IMAPClient = mock_cls  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"imapclient": fake_imapclient}):
            conn = ImapConnection(imap_account, "password", max_retries=1)
            with conn as client:
                assert client is mock_client

        mock_client.logout.assert_called_once()


# =============================================================================
# Server Capabilities Tests
# =============================================================================


class TestServerCapabilities:
    """Tests for the ServerCapabilities dataclass."""

    def test_defaults(self) -> None:
        """All capabilities should default to False/empty."""
        from mail_memex.imap.connection import ServerCapabilities

        caps = ServerCapabilities()
        assert caps.condstore is False
        assert caps.idle is False
        assert caps.compress is False
        assert caps.gmail_extensions is False
        assert caps.raw_capabilities == []


# =============================================================================
# Pull Sync Tests
# =============================================================================


class TestPullResult:
    """Tests for PullResult dataclass."""

    def test_default_values(self) -> None:
        """PullResult should have sensible defaults."""
        from mail_memex.imap.pull import PullResult

        result = PullResult()
        assert result.account == ""
        assert result.folder == ""
        assert result.fetched == 0
        assert result.new_emails == 0
        assert result.updated_tags == 0
        assert result.errors == []
        assert result.uid_validity_reset is False

    def test_to_dict(self) -> None:
        """to_dict should return all fields."""
        from mail_memex.imap.pull import PullResult

        result = PullResult(
            account="test",
            folder="INBOX",
            fetched=5,
            new_emails=3,
            updated_tags=2,
            errors=["error1"],
            uid_validity_reset=True,
        )
        d = result.to_dict()
        assert d["account"] == "test"
        assert d["folder"] == "INBOX"
        assert d["fetched"] == 5
        assert d["new_emails"] == 3
        assert d["updated_tags"] == 2
        assert d["errors"] == ["error1"]
        assert d["uid_validity_reset"] is True


class TestPullSync:
    """Tests for IMAP pull sync."""

    def test_get_sync_state_creates_new(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_get_sync_state should create new state for unknown folder."""
        from mail_memex.imap.pull import PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            state = pull._get_sync_state("INBOX")
            assert state is not None
            assert state.account_name == "test"
            assert state.folder == "INBOX"
            assert state.last_uid == 0
            assert state.uid_validity is None

    def test_count_emails_in_folder_filters_account_and_folder(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_count_emails_in_folder must count only emails matching the
        account and folder — not other IMAP accounts, other folders, or
        non-IMAP (local) emails."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            pull = PullSync(session, imap_account, TagMapper())
            # imap_populated_db has two test/INBOX emails and one local email.
            assert pull._count_emails_in_folder("INBOX") == 2
            # Wrong folder: zero.
            assert pull._count_emails_in_folder("Sent") == 0

    def test_count_emails_in_folder_excludes_archived(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """Soft-deleted emails must not be counted — they're invisible by
        convention, and including them would make 'Messages: N' lie to
        the user about how many live messages they have."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            imap1 = session.execute(
                select(Email).where(Email.message_id == "imap1@example.com")
            ).scalar()
            imap1.archived_at = datetime(2024, 2, 1)
            session.commit()

        with imap_populated_db.session() as session:
            pull = PullSync(session, imap_account, TagMapper())
            assert pull._count_emails_in_folder("INBOX") == 1

    def test_message_count_is_cumulative_not_last_pull(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """Regression: state.message_count = result.new_emails clobbered
        the cumulative total on every pull — after a second pull that
        found only 1 new email, 'Messages: 1' would have been reported
        even when 50+ were actually stored. Cumulative count is derived
        from the emails table.

        We verify this by running pull_folder's post-pull state update
        against a pre-populated folder: even with zero new UIDs, the
        count must reflect the live total (2 in the fixture)."""
        from unittest.mock import MagicMock

        from mail_memex.imap.pull import PullSync

        # Mock IMAP client that reports no new UIDs — forces the
        # "if not uids" early-return branch.
        client = MagicMock()
        client.select_folder.return_value = {b"UIDVALIDITY": 12345}
        client.search.return_value = []

        with imap_populated_db.session() as session:
            pull = PullSync(session, imap_account, TagMapper())
            pull.pull_folder(client, "INBOX")

        # After the pull with zero new emails, message_count reflects
        # the LIVE total (2), not result.new_emails (0).
        with imap_populated_db.session() as session:
            state = session.execute(
                select(ImapSyncState).where(
                    ImapSyncState.account_name == "test",
                    ImapSyncState.folder == "INBOX",
                )
            ).scalar()
            assert state.message_count == 2

    def test_get_sync_state_returns_existing(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_get_sync_state should return existing state for known folder."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            state = pull._get_sync_state("INBOX")
            assert state.uid_validity == 12345
            assert state.last_uid == 101

    def test_process_message_creates_new_email(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should create a new Email for unknown message_id."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            header = (
                b"From: Alice <alice@example.com>\r\n"
                b"Subject: Test Email\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
                b"Message-ID: <newmsg@example.com>\r\n"
            )
            body = b"Hello, this is a test message."

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": body,
                b"FLAGS": (b"\\Seen",),
            }

            pull._process_message(uid=200, data=data, folder="INBOX", result=result)
            session.flush()

            assert result.new_emails == 1

            email = session.execute(
                select(Email).where(Email.message_id == "newmsg@example.com")
            ).scalar()
            assert email is not None
            assert email.from_addr == "alice@example.com"
            assert email.from_name == "Alice"
            assert email.subject == "Test Email"
            assert email.imap_uid == 200
            assert email.imap_account == "test"
            assert email.imap_folder == "INBOX"
            assert "Hello, this is a test message." in email.body_text

    def test_process_message_updates_existing_email(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should update IMAP tracking for existing email."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            header = (
                b"From: sender@example.com\r\n"
                b"Subject: IMAP Email 1\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
                b"Message-ID: <imap1@example.com>\r\n"
            )

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": b"Body text",
                b"FLAGS": (b"\\Seen", b"\\Answered"),
            }

            pull._process_message(uid=500, data=data, folder="INBOX", result=result)
            session.flush()

            assert result.updated_tags == 1
            assert result.new_emails == 0

            email = session.execute(
                select(Email).where(Email.message_id == "imap1@example.com")
            ).scalar()
            assert email.imap_uid == 500

    def test_process_message_generates_message_id_when_missing(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should generate a message_id if header is missing."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            # Header without Message-ID
            header = (
                b"From: sender@example.com\r\n"
                b"Subject: No ID Email\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": b"Body content",
                b"FLAGS": (),
            }

            pull._process_message(uid=300, data=data, folder="INBOX", result=result)
            session.flush()

            assert result.new_emails == 1

            # Generated message_id should contain the uid
            email = session.execute(
                select(Email).where(Email.message_id == "imap-test-INBOX-300")
            ).scalar()
            assert email is not None

    def test_process_message_applies_tags_from_flags(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should apply mail-memex tags based on IMAP flags."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            header = (
                b"From: sender@example.com\r\n"
                b"Message-ID: <tagged@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": b"Content",
                b"FLAGS": (b"\\Seen", b"\\Flagged"),
            }

            pull._process_message(uid=400, data=data, folder="INBOX", result=result)
            session.flush()

            email = session.execute(
                select(Email).where(Email.message_id == "tagged@example.com")
            ).scalar()
            tag_names = {t.name for t in email.tags}
            assert "read" in tag_names
            assert "flagged" in tag_names

    def test_pull_folder_no_new_messages(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle no new messages gracefully."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = []

            result = pull.pull_folder(mock_client, "INBOX")

            assert result.fetched == 0
            assert result.new_emails == 0
            assert result.errors == []

    def test_pull_folder_fetches_new_messages(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should fetch and process new messages."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = [102, 103]

            header102 = (
                b"From: new1@example.com\r\n"
                b"Subject: New Email 1\r\n"
                b"Date: Mon, 15 Jan 2024 13:00:00 -0500\r\n"
                b"Message-ID: <new102@example.com>\r\n"
            )
            header103 = (
                b"From: new2@example.com\r\n"
                b"Subject: New Email 2\r\n"
                b"Date: Mon, 15 Jan 2024 14:00:00 -0500\r\n"
                b"Message-ID: <new103@example.com>\r\n"
            )

            mock_client.fetch.return_value = {
                102: {
                    b"BODY[HEADER]": header102,
                    b"BODY[TEXT]": b"Body of new email 1",
                    b"FLAGS": (b"\\Seen",),
                },
                103: {
                    b"BODY[HEADER]": header103,
                    b"BODY[TEXT]": b"Body of new email 2",
                    b"FLAGS": (),
                },
            }

            result = pull.pull_folder(mock_client, "INBOX")

            assert result.fetched == 2
            assert result.new_emails == 2
            assert result.errors == []

            # Verify emails were created
            e102 = session.execute(
                select(Email).where(Email.message_id == "new102@example.com")
            ).scalar()
            assert e102 is not None
            assert e102.from_addr == "new1@example.com"

            e103 = session.execute(
                select(Email).where(Email.message_id == "new103@example.com")
            ).scalar()
            assert e103 is not None

            # Verify sync state was updated
            state = session.execute(
                select(ImapSyncState).where(
                    ImapSyncState.account_name == "test",
                    ImapSyncState.folder == "INBOX",
                )
            ).scalar()
            assert state.last_uid == 103

    def test_pull_folder_uid_validity_reset(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle UIDVALIDITY change by resetting sync state."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            # Different UIDVALIDITY from stored value (12345 -> 99999)
            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 99999}
            mock_client.search.return_value = []

            result = pull.pull_folder(mock_client, "INBOX")

            assert result.uid_validity_reset is True

            # Verify IMAP tracking was cleared for existing emails
            e1 = session.execute(
                select(Email).where(Email.message_id == "imap1@example.com")
            ).scalar()
            assert e1.imap_uid is None
            assert e1.imap_folder is None

            e2 = session.execute(
                select(Email).where(Email.message_id == "imap2@example.com")
            ).scalar()
            assert e2.imap_uid is None
            assert e2.imap_folder is None

            # Sync state should be updated
            state = session.execute(
                select(ImapSyncState).where(
                    ImapSyncState.account_name == "test",
                    ImapSyncState.folder == "INBOX",
                )
            ).scalar()
            assert state.uid_validity == 99999

    def test_pull_folder_select_folder_error(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle folder selection errors gracefully."""
        from mail_memex.imap.pull import PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.side_effect = Exception("Folder not found")

            result = pull.pull_folder(mock_client, "NONEXISTENT")

            assert len(result.errors) == 1
            assert "Failed to select folder" in result.errors[0]

    def test_pull_folder_search_error(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle search errors gracefully."""
        from mail_memex.imap.pull import PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.side_effect = Exception("Search failed")

            result = pull.pull_folder(mock_client, "INBOX")

            assert len(result.errors) == 1
            assert "Search failed" in result.errors[0]

    def test_pull_folder_fetch_batch_error(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle fetch errors for individual batches."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = [102]
            mock_client.fetch.side_effect = Exception("Fetch error")

            result = pull.pull_folder(mock_client, "INBOX")

            assert result.fetched == 1
            assert len(result.errors) == 1
            assert "Fetch failed" in result.errors[0]

    def test_pull_folder_process_message_error(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should handle individual message processing errors."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = [102]

            mock_client.fetch.return_value = {
                102: {
                    b"BODY[HEADER]": b"Message-ID: <err@test>\r\nFrom: a@b\r\n",
                    b"BODY[TEXT]": b"body",
                    b"FLAGS": (),
                },
            }

            # Patch _process_message to raise, simulating an internal error
            with patch.object(pull, "_process_message", side_effect=RuntimeError("boom")):
                result = pull.pull_folder(mock_client, "INBOX")

            assert result.fetched == 1
            assert len(result.errors) >= 1
            assert "Failed to process UID 102" in result.errors[0]

    def test_pull_folder_updates_highestmodseq(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should update HIGHESTMODSEQ when server provides it."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {
                b"UIDVALIDITY": 12345,
                b"HIGHESTMODSEQ": 9876,
            }

            header = (
                b"From: sender@example.com\r\n"
                b"Message-ID: <modseq@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )
            mock_client.search.return_value = [102]
            mock_client.fetch.return_value = {
                102: {
                    b"BODY[HEADER]": header,
                    b"BODY[TEXT]": b"body",
                    b"FLAGS": (),
                },
            }

            pull.pull_folder(mock_client, "INBOX")

            state = session.execute(
                select(ImapSyncState).where(
                    ImapSyncState.account_name == "test",
                    ImapSyncState.folder == "INBOX",
                )
            ).scalar()
            assert state.highest_modseq == 9876

    def test_pull_folder_first_sync(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should use 'ALL' search on first sync (no prior state)."""
        from mail_memex.imap.pull import PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = [1, 2]

            header1 = (
                b"From: a@example.com\r\n"
                b"Message-ID: <first1@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )
            header2 = (
                b"From: b@example.com\r\n"
                b"Message-ID: <first2@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 11:00:00 -0500\r\n"
            )

            mock_client.fetch.return_value = {
                1: {
                    b"BODY[HEADER]": header1,
                    b"BODY[TEXT]": b"Body 1",
                    b"FLAGS": (),
                },
                2: {
                    b"BODY[HEADER]": header2,
                    b"BODY[TEXT]": b"Body 2",
                    b"FLAGS": (b"\\Seen",),
                },
            }

            result = pull.pull_folder(mock_client, "INBOX")

            # On first sync, search should use "ALL"
            mock_client.search.assert_called_once_with("ALL")
            assert result.fetched == 2
            assert result.new_emails == 2

    def test_pull_folder_incremental_sync(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """pull_folder should use UID range search for incremental sync."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            mock_client = MagicMock()
            mock_client.select_folder.return_value = {b"UIDVALIDITY": 12345}
            mock_client.search.return_value = []

            pull.pull_folder(mock_client, "INBOX")

            # Should search for UIDs > last_uid (101)
            mock_client.search.assert_called_once_with("UID 102:*")

    def test_apply_tags_creates_new_tags(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_apply_tags should create Tag objects that don't exist yet."""
        from mail_memex.imap.pull import PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            email = Email(
                message_id="tagtest@example.com",
                from_addr="test@example.com",
                subject="Tag test",
                body_text="body",
                date=datetime(2024, 1, 15),
            )
            session.add(email)
            session.flush()

            pull._apply_tags(email, {"newtag1", "newtag2"})
            session.flush()

            tag_names = {t.name for t in email.tags}
            assert "newtag1" in tag_names
            assert "newtag2" in tag_names

            # Tags should be created in DB with source="imap"
            tag = session.execute(select(Tag).where(Tag.name == "newtag1")).scalar()
            assert tag is not None
            assert tag.source == "imap"

    def test_apply_tags_reuses_existing_tags(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_apply_tags should reuse existing Tag objects."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            # Count existing "read" tags before
            existing_read = session.execute(select(Tag).where(Tag.name == "read")).scalar()
            assert existing_read is not None
            existing_id = existing_read.id

            email = Email(
                message_id="reusetag@example.com",
                from_addr="test@example.com",
                subject="Reuse tag test",
                body_text="body",
                date=datetime(2024, 1, 15),
            )
            session.add(email)
            session.flush()

            pull._apply_tags(email, {"read"})
            session.flush()

            # Should have reused the existing tag
            reused_tag = session.execute(select(Tag).where(Tag.name == "read")).scalar()
            assert reused_tag.id == existing_id

    def test_clear_folder_state(
        self, imap_populated_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_clear_folder_state should clear IMAP tracking for emails in folder."""
        from mail_memex.imap.pull import PullSync

        with imap_populated_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)

            pull._clear_folder_state("INBOX")
            session.flush()

            # IMAP-tracked emails should have tracking cleared
            e1 = session.execute(
                select(Email).where(Email.message_id == "imap1@example.com")
            ).scalar()
            assert e1.imap_uid is None
            assert e1.imap_folder is None

            e2 = session.execute(
                select(Email).where(Email.message_id == "imap2@example.com")
            ).scalar()
            assert e2.imap_uid is None
            assert e2.imap_folder is None

            # Non-IMAP email should be unaffected
            local = session.execute(
                select(Email).where(Email.message_id == "local@example.com")
            ).scalar()
            assert local.imap_uid is None  # Was already None

    def test_process_message_handles_body_preview(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should set body_preview to first 500 chars of body."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            long_body = b"A" * 1000

            header = (
                b"From: sender@example.com\r\n"
                b"Message-ID: <preview@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": long_body,
                b"FLAGS": (),
            }

            pull._process_message(uid=500, data=data, folder="INBOX", result=result)
            session.flush()

            email = session.execute(
                select(Email).where(Email.message_id == "preview@example.com")
            ).scalar()
            assert email.body_preview is not None
            assert len(email.body_preview) == 500

    def test_process_message_parses_in_reply_to(
        self, imap_db: Database, imap_account: ImapAccountConfig
    ) -> None:
        """_process_message should correctly parse In-Reply-To header."""
        from mail_memex.imap.pull import PullResult, PullSync

        with imap_db.session() as session:
            mapper = TagMapper()
            pull = PullSync(session, imap_account, mapper)
            result = PullResult(account="test", folder="INBOX")

            header = (
                b"From: sender@example.com\r\n"
                b"Message-ID: <reply@example.com>\r\n"
                b"In-Reply-To: <original@example.com>\r\n"
                b"References: <ref1@example.com> <ref2@example.com>\r\n"
                b"Date: Mon, 15 Jan 2024 10:00:00 -0500\r\n"
            )

            data = {
                b"BODY[HEADER]": header,
                b"BODY[TEXT]": b"Reply body",
                b"FLAGS": (),
            }

            pull._process_message(uid=600, data=data, folder="INBOX", result=result)
            session.flush()

            email = session.execute(
                select(Email).where(Email.message_id == "reply@example.com")
            ).scalar()
            assert email.in_reply_to == "original@example.com"
            assert email.references is not None
