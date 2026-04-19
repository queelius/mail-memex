"""Tests for the MCP server (FastMCP with contract + domain tools).

Tests the *_impl functions directly using lightweight fixtures.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch

# =============================================================================
# Fixture
# =============================================================================
import pytest

from mail_memex.core.database import Database
from mail_memex.core.models import Email, Thread


@pytest.fixture
def mcp_db(db: Database) -> Database:
    """Database with minimal data for MCP tool tests."""
    with db.session() as session:
        email = Email(
            message_id="mcp-test@example.com",
            from_addr="alice@example.com",
            from_name="Alice",
            subject="MCP Test Email",
            body_text="This is a test for the MCP server.",
            date=datetime(2024, 6, 15, 10, 0),
            to_addrs="bob@example.com",
        )
        thread = Thread(
            thread_id="thread-mcp-test",
            subject="MCP Thread",
            email_count=1,
            first_date=datetime(2024, 6, 15),
            last_date=datetime(2024, 6, 15),
        )
        session.add_all([email, thread])
        session.flush()
        email.thread_id = "thread-mcp-test"
        session.commit()
    return db


# =============================================================================
# TestExecuteSQL
# =============================================================================


class TestExecuteSQL:
    """Tests for execute_sql_impl (renamed from run_sql)."""

    def test_select_returns_rows(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(execute_sql_impl(session, "SELECT * FROM emails"))
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0]["message_id"] == "mcp-test@example.com"

    def test_select_empty_result(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(session, "SELECT * FROM emails WHERE message_id = 'nope'")
            )
            assert result == []

    def test_readonly_blocks_insert(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session, "INSERT INTO tags (name, source) VALUES ('x', 'mail-memex')"
                )
            )
            assert "error" in result

    def test_readonly_blocks_delete(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(execute_sql_impl(session, "DELETE FROM emails WHERE id = 1"))
            assert "error" in result

    def test_readonly_blocks_update(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session, "UPDATE emails SET subject = 'hacked' WHERE id = 1"
                )
            )
            assert "error" in result

    def test_writable_allows_insert(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session,
                    "INSERT INTO tags (name, source) VALUES ('newtag', 'mail-memex')",
                    readonly=False,
                )
            )
            assert "affected_rows" in result
            assert result["affected_rows"] == 1

    def test_ddl_always_blocked(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            for stmt in [
                "DROP TABLE emails",
                "ALTER TABLE emails ADD COLUMN foo TEXT",
                "CREATE TABLE evil (id INTEGER)",
            ]:
                result = json.loads(execute_sql_impl(session, stmt, readonly=False))
                assert "error" in result, f"DDL not blocked: {stmt}"

    def test_invalid_sql_returns_error(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(execute_sql_impl(session, "SELECTT * FROMM nowhere"))
            assert "error" in result

    def test_pragma_allowed(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(execute_sql_impl(session, "PRAGMA table_info('emails')"))
            assert isinstance(result, list)
            assert len(result) > 0

    def test_ddl_keyword_in_string_literal_not_blocked(self, mcp_db: Database) -> None:
        """Regression: a string value containing 'DROP TABLE' or 'CREATE' is data,
        not DDL, and must reach the database."""
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session,
                    "INSERT INTO tags (name, source) VALUES ('DROP TABLE lore', 'mail-memex')",
                    readonly=False,
                )
            )
            assert "affected_rows" in result, result

    def test_ddl_keyword_in_comment_not_blocked(self, mcp_db: Database) -> None:
        """Regression: a comment mentioning 'CREATE' must not block a SELECT."""
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session,
                    "SELECT message_id FROM emails /* could CREATE an index here */",
                )
            )
            assert isinstance(result, list), result

    def test_write_leaves_session_usable(self, mcp_db: Database) -> None:
        """Regression: execute_sql writes must not corrupt SQLAlchemy session state.
        After the write, the same session must still be usable for ORM queries."""
        from mail_memex.core.models import Email
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(
                    session,
                    "INSERT INTO tags (name, source) VALUES ('post-op', 'mail-memex')",
                    readonly=False,
                )
            )
            assert result.get("affected_rows") == 1
            # ORM query on the same session must still work
            emails = session.query(Email).all()
            assert len(emails) == 1

    def test_mixed_case_ddl_blocked(self, mcp_db: Database) -> None:
        """Authorizer is parser-based, so case variations are naturally handled."""
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(
                execute_sql_impl(session, "cReAtE TaBlE evil (id INTEGER)", readonly=False)
            )
            assert "error" in result

    def test_writable_schema_pragma_blocked(self, mcp_db: Database) -> None:
        """PRAGMA writable_schema is a full schema-escape hatch — it lets
        you UPDATE sqlite_master and rewrite any table's definition,
        bypassing triggers, constraints, and column scope. Must be denied
        even though plain DDL is already blocked separately."""
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            for stmt in (
                "PRAGMA writable_schema = 1",
                "PRAGMA writable_schema = ON",
                "PRAGMA WRITABLE_SCHEMA = 1",  # case insensitive
                "PRAGMA writable_schema",  # read also blocked for safety
            ):
                result = json.loads(execute_sql_impl(session, stmt, readonly=False))
                assert "error" in result, f"Not blocked: {stmt}"

    def test_safe_pragmas_still_work(self, mcp_db: Database) -> None:
        """Read-only introspection PRAGMAs must still function — they're
        how LLMs explore the schema via MCP."""
        from mail_memex.mcp.server import execute_sql_impl

        with mcp_db.session() as session:
            result = json.loads(execute_sql_impl(session, "PRAGMA foreign_keys"))
            assert isinstance(result, list)
            # table_info already covered elsewhere; spot-check another one
            result = json.loads(execute_sql_impl(session, "PRAGMA database_list"))
            assert isinstance(result, list)


# =============================================================================
# TestGetSchema
# =============================================================================


class TestGetSchema:
    """Tests for get_schema_impl."""

    def test_returns_valid_json(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            assert isinstance(result, dict)
            assert "tables" in result
            assert "tips" in result

    def test_contains_core_tables(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            tables = result["tables"]
            for name in ["emails", "threads", "tags", "marginalia"]:
                assert name in tables, f"Missing table: {name}"

    def test_table_has_columns(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            emails = result["tables"]["emails"]
            assert "columns" in emails
            col_names = [c["name"] for c in emails["columns"]]
            assert "message_id" in col_names
            assert "archived_at" in col_names

    def test_includes_fts5(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            assert "emails_fts" in result["tables"]

    def test_includes_descriptions(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            emails = result["tables"]["emails"]
            assert emails["description"] != ""

    def test_includes_marginalia_description(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            marginalia = result["tables"]["marginalia"]
            assert marginalia["description"] != ""

    def test_includes_tips(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_schema_impl

        with mcp_db.session() as session:
            result = json.loads(get_schema_impl(session))
            assert isinstance(result["tips"], list)
            assert len(result["tips"]) > 0


# =============================================================================
# TestGetRecord
# =============================================================================


class TestGetRecord:
    """Tests for get_record_impl — now takes a mail-memex:// URI per the
    federation contract (see meta-memex/docs/uri-scheme.md)."""

    def test_email_by_uri(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://email/mcp-test@example.com")
            )
            assert result["message_id"] == "mcp-test@example.com"
            assert result["from_addr"] == "alice@example.com"
            assert result["from_name"] == "Alice"
            assert result["subject"] == "MCP Test Email"
            assert result["to_addrs"] == "bob@example.com"
            assert result["thread_id"] == "thread-mcp-test"

    def test_thread_by_uri(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://thread/thread-mcp-test")
            )
            assert result["thread_id"] == "thread-mcp-test"
            assert result["subject"] == "MCP Thread"
            assert result["email_count"] == 1

    def test_email_not_found(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://email/nonexistent@example.com")
            )
            assert result == {"error": "NOT_FOUND"}

    def test_thread_not_found(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://thread/no-such-thread")
            )
            assert result == {"error": "NOT_FOUND"}

    def test_unknown_kind(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(get_record_impl(session, "mail-memex://widget/abc"))
            assert "error" in result
            assert "Unknown kind" in result["error"]
            assert "widget" in result["error"]

    def test_returns_archived_email(self, mcp_db: Database) -> None:
        """get_record must return archived (soft-deleted) records too."""
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            email = session.query(Email).filter_by(message_id="mcp-test@example.com").one()
            email.archived_at = datetime(2024, 7, 1)
            session.commit()

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://email/mcp-test@example.com")
            )
            assert result["message_id"] == "mcp-test@example.com"
            assert result["archived_at"] is not None

    def test_marginalia_by_uri(self, mcp_db: Database) -> None:
        """get_record with kind=marginalia delegates to marginalia module."""
        from mail_memex.core.marginalia import create_marginalia
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            created = create_marginalia(
                session,
                target_uris=["mail-memex://email/mcp-test@example.com"],
                content="A note",
            )
            session.commit()
            uuid = created["uuid"]

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, f"mail-memex://marginalia/{uuid}")
            )
            assert result["uuid"] == uuid
            assert result["content"] == "A note"

    def test_marginalia_not_found(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, f"mail-memex://marginalia/{'0' * 32}")
            )
            assert result == {"error": "NOT_FOUND"}

    def test_fragment_is_stripped_for_lookup(self, mcp_db: Database) -> None:
        """Position fragments address positions within a record, not separate
        records. The underlying email must resolve regardless of fragment."""
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(
                    session, "mail-memex://email/mcp-test@example.com#part=2"
                )
            )
            assert result["message_id"] == "mcp-test@example.com"

    def test_non_mail_memex_scheme_rejected(self, mcp_db: Database) -> None:
        """URIs from other archives (llm-memex://, book-memex://) must be
        rejected — federation-layer code is responsible for routing them."""
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "llm-memex://conversation/abc")
            )
            assert "error" in result
            assert "mail-memex://" in result["error"]

    def test_malformed_uri_rejected(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            for bad in (
                "mail-memex://",  # no kind or id
                "mail-memex://email",  # no id
                "mail-memex:///abc",  # empty kind
                "not a uri",
                "",
            ):
                result = json.loads(get_record_impl(session, bad))
                assert "error" in result, f"{bad!r} should be rejected"

    def test_id_containing_slash_preserved(self, mcp_db: Database) -> None:
        """Message-IDs can technically contain '/' — the id portion of the
        URI is everything after the first '/', including further slashes."""
        from mail_memex.core.models import Email
        from mail_memex.mcp.server import get_record_impl

        with mcp_db.session() as session:
            session.add(
                Email(
                    message_id="weird/slash@example.com",
                    from_addr="x@example.com",
                    subject="s",
                    body_text="",
                    date=datetime(2024, 1, 1),
                )
            )
            session.commit()

        with mcp_db.session() as session:
            result = json.loads(
                get_record_impl(session, "mail-memex://email/weird/slash@example.com")
            )
            assert result["message_id"] == "weird/slash@example.com"


# =============================================================================
# TestSearchEmails
# =============================================================================


class TestSearchEmails:
    """Tests for search_emails_impl."""

    def test_keyword_search(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import search_emails_impl

        with mcp_db.session() as session:
            result = json.loads(search_emails_impl(session, "MCP"))
            assert isinstance(result, list)
            assert len(result) >= 1
            assert result[0]["message_id"] == "mcp-test@example.com"

    def test_no_results(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import search_emails_impl

        with mcp_db.session() as session:
            result = json.loads(search_emails_impl(session, "xyzzy_no_match_999"))
            assert result == []

    def test_respects_limit(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import search_emails_impl

        with mcp_db.session() as session:
            result = json.loads(search_emails_impl(session, "test", limit=1))
            assert len(result) <= 1

    def test_from_operator(self, mcp_db: Database) -> None:
        from mail_memex.mcp.server import search_emails_impl

        with mcp_db.session() as session:
            result = json.loads(search_emails_impl(session, "from:alice"))
            assert len(result) >= 1
            assert all(
                "alice" in r.get("from_addr", "").lower() for r in result
            )


# =============================================================================
# TestCreateServer
# =============================================================================


class TestCreateServer:
    """Tests for create_server() factory."""

    def test_creates_server(self, tmp_path) -> None:
        from mail_memex.mcp.server import create_server

        db_path = tmp_path / "test.db"
        with patch.dict(os.environ, {"MAIL_MEMEX_DATABASE_PATH": str(db_path)}):
            mcp = create_server()
        assert mcp is not None
        assert mcp.name == "mail-memex"
