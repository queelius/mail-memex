"""Tests for MCP server tool handlers, server setup, and resource handlers.

Tests the tool handler functions directly (without MCP transport),
using the populated_db fixture for realistic data.  Also tests the
server.py registration/dispatch and resources.py handler functions.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from mtk.core.database import Database
from mtk.mcp.resources import (
    RESOURCE_HANDLERS,
    read_email_resource,
    read_person_resource,
    read_stats_resource,
    read_thread_resource,
)
from mtk.mcp.tools import (
    TOOL_HANDLERS,
    get_correspondence_timeline,
    get_inbox,
    get_reply_context,
    get_stats,
    list_people,
    list_tags,
    search_emails,
    show_email,
    show_person,
    show_thread,
    tag_batch,
    tag_email,
)
from mtk.mcp.validation import (
    optional_bool,
    optional_int,
    optional_list,
    optional_str,
    require_str,
)

# server.py imports from the `mcp` package at module level; guard so
# the rest of this file's tests still run when the mcp extra is missing.
try:
    from mtk.mcp.server import TOOL_DEFINITIONS

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False
    TOOL_DEFINITIONS = []  # type: ignore[assignment]

_requires_mcp = pytest.mark.skipif(not _HAS_MCP, reason="mcp package not installed")


def _parse_result(result: list[dict]) -> dict | list | str:
    """Parse the text content from a tool result."""
    assert len(result) >= 1
    text = result[0]["text"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# =============================================================================
# Validation tests
# =============================================================================


class TestValidation:
    """Tests for input validation helpers."""

    def test_require_str_present(self) -> None:
        assert require_str({"key": "value"}, "key") == "value"

    def test_require_str_missing(self) -> None:
        with pytest.raises(ValueError, match="Missing required"):
            require_str({}, "key")

    def test_require_str_empty(self) -> None:
        with pytest.raises(ValueError, match="Missing required"):
            require_str({"key": ""}, "key")

    def test_optional_str(self) -> None:
        assert optional_str({"key": "val"}, "key") == "val"
        assert optional_str({}, "key") is None
        assert optional_str({}, "key", "default") == "default"

    def test_optional_int(self) -> None:
        assert optional_int({"key": 42}, "key") == 42
        assert optional_int({}, "key", 10) == 10

    def test_optional_bool(self) -> None:
        assert optional_bool({"key": True}, "key") is True
        assert optional_bool({}, "key") is False

    def test_optional_list(self) -> None:
        assert optional_list({"key": ["a", "b"]}, "key") == ["a", "b"]
        assert optional_list({}, "key") == []
        assert optional_list({"key": "single"}, "key") == ["single"]


# =============================================================================
# Tool handler tests
# =============================================================================


class TestSearchEmails:
    """Tests for search_emails tool."""

    def test_basic_search(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = search_emails(session, {"query": "project"})
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) >= 1
            assert "message_id" in data[0]

    def test_search_with_limit(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = search_emails(session, {"query": "project", "limit": 1})
            data = _parse_result(result)
            assert len(data) <= 1

    def test_search_no_results(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = search_emails(session, {"query": "xyznonexistent"})
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) == 0

    def test_search_requires_query(self, populated_db: Database) -> None:
        with populated_db.session() as session, pytest.raises(ValueError):
            search_emails(session, {})


class TestGetInbox:
    """Tests for get_inbox tool."""

    def test_inbox_returns_emails(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_inbox(session, {})
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) >= 1

    def test_inbox_with_limit(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_inbox(session, {"limit": 2})
            data = _parse_result(result)
            assert len(data) <= 2

    def test_inbox_with_since(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_inbox(session, {"since": "2024-01-17"})
            data = _parse_result(result)
            # Should only include emails on or after Jan 17
            assert isinstance(data, list)


class TestGetStats:
    """Tests for get_stats tool."""

    def test_stats_returns_counts(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_stats(session, {})
            data = _parse_result(result)
            assert data["emails"] == 5
            assert data["people"] == 3
            assert data["threads"] == 2
            assert data["tags"] == 4


class TestShowEmail:
    """Tests for show_email tool."""

    def test_show_by_exact_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_email(session, {"message_id": "email1@example.com"})
            data = _parse_result(result)
            assert data["message_id"] == "email1@example.com"
            assert data["subject"] == "Project Discussion"
            assert "body_text" in data

    def test_show_by_partial_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_email(session, {"message_id": "email1@"})
            data = _parse_result(result)
            assert data["message_id"] == "email1@example.com"

    def test_show_not_found(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_email(session, {"message_id": "nonexistent@nowhere.com"})
            text = result[0]["text"]
            assert "not found" in text.lower()


class TestShowThread:
    """Tests for show_thread tool."""

    def test_show_by_thread_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_thread(session, {"thread_id": "thread-001"})
            data = _parse_result(result)
            assert data["thread_id"] == "thread-001"
            assert data["message_count"] == 3
            assert len(data["messages"]) == 3

    def test_show_thread_not_found(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_thread(session, {"thread_id": "nonexistent"})
            text = result[0]["text"]
            assert "not found" in text.lower()


class TestGetReplyContext:
    """Tests for get_reply_context tool."""

    def test_reply_context(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_reply_context(session, {"message_id": "email2@example.com"})
            data = _parse_result(result)
            assert "replying_to" in data
            assert "suggested_headers" in data
            assert data["suggested_headers"]["to"] == "bob@example.com"

    def test_reply_context_adds_re(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = get_reply_context(session, {"message_id": "email1@example.com"})
            data = _parse_result(result)
            assert data["suggested_headers"]["subject"].startswith("Re:")


class TestTagEmail:
    """Tests for tag_email tool."""

    def test_add_tag(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = tag_email(
                session,
                {"message_id": "email3@example.com", "add": ["followup"]},
            )
            data = _parse_result(result)
            assert "followup" in data["tags"]

    def test_remove_tag(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = tag_email(
                session,
                {"message_id": "email1@example.com", "remove": ["important"]},
            )
            data = _parse_result(result)
            assert "important" not in data["tags"]

    def test_tag_not_found(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = tag_email(
                session,
                {"message_id": "nonexistent@nowhere.com", "add": ["test"]},
            )
            text = result[0]["text"]
            assert "not found" in text.lower()


class TestTagBatch:
    """Tests for tag_batch tool."""

    def test_batch_tag(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = tag_batch(
                session,
                {"query": "from:alice", "add": ["alice-mail"]},
            )
            data = _parse_result(result)
            assert data["matched"] >= 1
            assert data["modified"] >= 1


class TestListTags:
    """Tests for list_tags tool."""

    def test_list_tags(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = list_tags(session, {})
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) >= 1
            assert "name" in data[0]
            assert "count" in data[0]


class TestListPeople:
    """Tests for list_people tool."""

    def test_list_people(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = list_people(session, {})
            data = _parse_result(result)
            assert isinstance(data, list)
            assert len(data) >= 1
            assert "name" in data[0]


class TestShowPerson:
    """Tests for show_person tool."""

    def test_show_person(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            # First get a person ID
            people = list_people(session, {"limit": 1})
            people_data = _parse_result(people)
            person_id = people_data[0]["id"]

            result = show_person(session, {"person_id": person_id})
            data = _parse_result(result)
            assert "name" in data
            assert "email" in data
            assert "email_count" in data

    def test_show_person_not_found(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = show_person(session, {"person_id": 99999})
            text = result[0]["text"]
            assert "not found" in text.lower()


class TestGetCorrespondenceTimeline:
    """Tests for get_correspondence_timeline tool."""

    def test_timeline(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            people = list_people(session, {"limit": 1})
            people_data = _parse_result(people)
            person_id = people_data[0]["id"]

            result = get_correspondence_timeline(
                session, {"person_id": person_id, "granularity": "month"}
            )
            data = _parse_result(result)
            assert isinstance(data, dict)


# =============================================================================
# Dispatch registry tests
# =============================================================================


class TestToolRegistry:
    """Tests for tool dispatch registry."""

    def test_all_tools_registered(self) -> None:
        """All 13 tool handlers should be in the registry."""
        assert len(TOOL_HANDLERS) == 13

    def test_all_handlers_callable(self) -> None:
        """All handlers should be callable."""
        for name, handler in TOOL_HANDLERS.items():
            assert callable(handler), f"{name} is not callable"

    def test_handler_names_match(self) -> None:
        """Handler names should match expected tool names."""
        expected = {
            "search_emails",
            "get_inbox",
            "get_stats",
            "show_email",
            "show_thread",
            "get_reply_context",
            "tag_email",
            "tag_batch",
            "list_tags",
            "list_people",
            "show_person",
            "get_correspondence_timeline",
            "notmuch_sync",
        }
        assert set(TOOL_HANDLERS.keys()) == expected


# =============================================================================
# server.py tests — tool/resource registration, dispatch, error handling
# =============================================================================


@_requires_mcp
class TestToolDefinitions:
    """Tests for TOOL_DEFINITIONS in server.py."""

    def test_tool_definitions_is_nonempty_list(self) -> None:
        assert isinstance(TOOL_DEFINITIONS, list)
        assert len(TOOL_DEFINITIONS) >= 1

    def test_each_definition_has_required_keys(self) -> None:
        for td in TOOL_DEFINITIONS:
            assert "name" in td, f"Tool definition missing 'name': {td}"
            assert "description" in td, f"Tool {td.get('name')} missing 'description'"
            assert "inputSchema" in td, f"Tool {td.get('name')} missing 'inputSchema'"

    def test_each_input_schema_has_type_object(self) -> None:
        for td in TOOL_DEFINITIONS:
            schema = td["inputSchema"]
            assert schema.get("type") == "object", (
                f"Tool {td['name']} inputSchema type is not 'object'"
            )
            assert "properties" in schema, f"Tool {td['name']} inputSchema missing 'properties'"

    def test_tool_names_match_handlers(self) -> None:
        """Every defined tool should have a corresponding handler."""
        definition_names = {td["name"] for td in TOOL_DEFINITIONS}
        handler_names = set(TOOL_HANDLERS.keys())
        assert definition_names == handler_names

    def test_specific_tool_names_present(self) -> None:
        names = {td["name"] for td in TOOL_DEFINITIONS}
        for expected in [
            "search_emails",
            "get_inbox",
            "get_stats",
            "show_email",
            "show_thread",
            "get_reply_context",
            "tag_email",
            "tag_batch",
            "list_tags",
            "list_people",
            "show_person",
            "get_correspondence_timeline",
            "notmuch_sync",
        ]:
            assert expected in names, f"Missing tool definition: {expected}"

    def test_search_emails_schema_requires_query(self) -> None:
        td = next(t for t in TOOL_DEFINITIONS if t["name"] == "search_emails")
        assert "required" in td["inputSchema"]
        assert "query" in td["inputSchema"]["required"]

    def test_show_email_schema_requires_message_id(self) -> None:
        td = next(t for t in TOOL_DEFINITIONS if t["name"] == "show_email")
        assert "message_id" in td["inputSchema"]["required"]

    def test_tag_email_schema_has_add_remove_arrays(self) -> None:
        td = next(t for t in TOOL_DEFINITIONS if t["name"] == "tag_email")
        props = td["inputSchema"]["properties"]
        assert props["add"]["type"] == "array"
        assert props["remove"]["type"] == "array"

    def test_get_stats_schema_has_no_required(self) -> None:
        td = next(t for t in TOOL_DEFINITIONS if t["name"] == "get_stats")
        assert "required" not in td["inputSchema"]


@_requires_mcp
class TestCreateServer:
    """Tests for create_server() — registration and dispatch.

    Uses a capturing mock for the MCP Server class to intercept
    decorator registrations, then calls the captured handlers directly.
    """

    @pytest.fixture(autouse=True)
    def _setup_mocks(self, populated_db: Database, tmp_path) -> None:
        """Store fixtures for use in tests."""
        self.populated_db = populated_db
        self.tmp_path = tmp_path

    def _create_server_and_capture_handlers(self):
        """Create the MCP server, capturing all decorator-registered handlers.

        Returns (server, handlers_dict) where handlers_dict maps decorator
        names (e.g. "list_tools", "call_tool") to the registered async functions.
        """
        captured = {}

        class CapturingServer:
            """Mock Server that captures decorator registrations."""

            def __init__(self, name):
                self.name = name

            def _make_decorator(self, key):
                def decorator():
                    def wrapper(fn):
                        captured[key] = fn
                        return fn

                    return wrapper

                return decorator

            def list_tools(self):
                def wrapper(fn):
                    captured["list_tools"] = fn
                    return fn

                return wrapper

            def call_tool(self):
                def wrapper(fn):
                    captured["call_tool"] = fn
                    return fn

                return wrapper

            def list_resources(self):
                def wrapper(fn):
                    captured["list_resources"] = fn
                    return fn

                return wrapper

            def list_resource_templates(self):
                def wrapper(fn):
                    captured["list_resource_templates"] = fn
                    return fn

                return wrapper

            def read_resource(self):
                def wrapper(fn):
                    captured["read_resource"] = fn
                    return fn

                return wrapper

        with (
            patch("mtk.mcp.server.Server", CapturingServer),
            patch("mtk.mcp.server._get_db_path", return_value=":memory:"),
            patch("mtk.mcp.server.Database", return_value=self.populated_db),
            patch("mtk.mcp.server._get_privacy_filter", return_value=None),
        ):
            from mtk.mcp.server import create_server

            server = create_server()

        return server, captured

    # -- Registration tests ---------------------------------------------------

    def test_create_server_returns_instance(self) -> None:
        server, _ = self._create_server_and_capture_handlers()
        assert server.name == "mtk"

    def test_all_handlers_registered(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        for key in [
            "list_tools",
            "call_tool",
            "list_resources",
            "list_resource_templates",
            "read_resource",
        ]:
            assert key in captured, f"Handler '{key}' was not registered"

    # -- list_tools -----------------------------------------------------------

    def test_list_tools_returns_all_definitions(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        tools = asyncio.run(captured["list_tools"]())
        assert len(tools) == len(TOOL_DEFINITIONS)
        tool_names = {t.name for t in tools}
        for td in TOOL_DEFINITIONS:
            assert td["name"] in tool_names

    def test_list_tools_returns_tool_objects(self) -> None:
        from mcp.types import Tool

        _, captured = self._create_server_and_capture_handlers()
        tools = asyncio.run(captured["list_tools"]())
        for t in tools:
            assert isinstance(t, Tool)

    # -- list_resources -------------------------------------------------------

    def test_list_resources_returns_stats(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        resources = asyncio.run(captured["list_resources"]())
        assert len(resources) == 1
        assert str(resources[0].uri) == "mtk://stats"
        assert resources[0].mimeType == "application/json"

    # -- list_resource_templates ----------------------------------------------

    def test_list_resource_templates_returns_three(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        templates = asyncio.run(captured["list_resource_templates"]())
        assert len(templates) == 3
        template_names = {t.name for t in templates}
        assert "Email" in template_names
        assert "Thread" in template_names
        assert "Person" in template_names

    def test_resource_template_uri_patterns(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        templates = asyncio.run(captured["list_resource_templates"]())
        uri_templates = {t.uriTemplate for t in templates}
        assert "mtk://email/{message_id}" in uri_templates
        assert "mtk://thread/{thread_id}" in uri_templates
        assert "mtk://person/{person_id}" in uri_templates

    # -- call_tool dispatch ---------------------------------------------------

    def test_call_tool_dispatches_get_stats(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["call_tool"]("get_stats", {}))
        assert len(result) >= 1
        data = json.loads(result[0].text)
        assert data["emails"] == 5
        assert data["people"] == 3

    def test_call_tool_dispatches_search_emails(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["call_tool"]("search_emails", {"query": "project"}))
        data = json.loads(result[0].text)
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_call_tool_unknown_returns_error_text(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["call_tool"]("nonexistent_tool", {}))
        assert len(result) == 1
        assert "Unknown tool: nonexistent_tool" in result[0].text

    def test_call_tool_none_arguments_treated_as_empty(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["call_tool"]("get_stats", None))
        data = json.loads(result[0].text)
        assert "emails" in data

    # -- read_resource dispatch -----------------------------------------------

    def test_read_resource_stats(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://stats"))
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["emails"] == 5

    def test_read_resource_email(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://email/email1@example.com"))
        data = json.loads(result[0].text)
        assert data["message_id"] == "email1@example.com"
        assert data["subject"] == "Project Discussion"

    def test_read_resource_thread(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://thread/thread-001"))
        data = json.loads(result[0].text)
        assert data["thread_id"] == "thread-001"
        assert data["message_count"] == 3

    def test_read_resource_person(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://person/1"))
        data = json.loads(result[0].text)
        assert data["name"] == "Alice Smith"

    def test_read_resource_unknown_type(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://unknown_type/123"))
        data = json.loads(result[0].text)
        assert "error" in data
        assert "Unknown resource type" in data["error"]

    def test_read_resource_not_found_email(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://email/nonexistent@nowhere.com"))
        data = json.loads(result[0].text)
        assert "error" in data
        assert "not found" in data["error"]

    def test_read_resource_not_found_person(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        result = asyncio.run(captured["read_resource"]("mtk://person/99999"))
        data = json.loads(result[0].text)
        assert "error" in data
        assert "not found" in data["error"]

    def test_read_resource_uri_preserved_in_response(self) -> None:
        _, captured = self._create_server_and_capture_handlers()
        uri = "mtk://stats"
        result = asyncio.run(captured["read_resource"](uri))
        assert str(result[0].uri) == uri


# =============================================================================
# resources.py tests — resource handler functions
# =============================================================================


class TestResourceHandlerRegistry:
    """Tests for the RESOURCE_HANDLERS dispatch dict."""

    def test_resource_handlers_has_expected_keys(self) -> None:
        assert set(RESOURCE_HANDLERS.keys()) == {"email", "thread", "person"}

    def test_all_handlers_callable(self) -> None:
        for name, handler in RESOURCE_HANDLERS.items():
            assert callable(handler), f"Resource handler '{name}' is not callable"

    def test_handlers_point_to_correct_functions(self) -> None:
        assert RESOURCE_HANDLERS["email"] is read_email_resource
        assert RESOURCE_HANDLERS["thread"] is read_thread_resource
        assert RESOURCE_HANDLERS["person"] is read_person_resource


class TestReadEmailResource:
    """Tests for read_email_resource."""

    def test_exact_message_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "email1@example.com")
            assert result is not None
            data = json.loads(result)
            assert data["message_id"] == "email1@example.com"
            assert data["from_addr"] == "alice@example.com"
            assert data["from_name"] == "Alice Smith"
            assert data["subject"] == "Project Discussion"
            assert data["thread_id"] == "thread-001"
            assert "body_text" in data
            assert data["date"] is not None

    def test_partial_message_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "email2@")
            assert result is not None
            data = json.loads(result)
            assert data["message_id"] == "email2@example.com"

    def test_not_found_returns_none(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "nonexistent@nowhere.com")
            assert result is None

    def test_tags_included(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "email1@example.com")
            data = json.loads(result)
            assert isinstance(data["tags"], list)
            assert "important" in data["tags"]
            assert "work" in data["tags"]

    def test_email_without_tags(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "email2@example.com")
            data = json.loads(result)
            assert data["tags"] == []

    def test_date_is_iso_format(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_email_resource(session, "email1@example.com")
            data = json.loads(result)
            # Should be parseable ISO format
            assert "2024-01-15" in data["date"]


class TestReadThreadResource:
    """Tests for read_thread_resource."""

    def test_existing_thread(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_thread_resource(session, "thread-001")
            assert result is not None
            data = json.loads(result)
            assert data["thread_id"] == "thread-001"
            assert data["message_count"] == 3
            assert len(data["messages"]) == 3

    def test_thread_messages_ordered_by_date(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_thread_resource(session, "thread-001")
            data = json.loads(result)
            messages = data["messages"]
            dates = [m["date"] for m in messages]
            assert dates == sorted(dates)

    def test_thread_message_fields(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_thread_resource(session, "thread-001")
            data = json.loads(result)
            msg = data["messages"][0]
            assert "message_id" in msg
            assert "from_addr" in msg
            assert "date" in msg
            assert "subject" in msg
            assert "body_text" in msg

    def test_thread_not_found_returns_none(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_thread_resource(session, "nonexistent-thread")
            assert result is None

    def test_second_thread(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_thread_resource(session, "thread-002")
            assert result is not None
            data = json.loads(result)
            assert data["thread_id"] == "thread-002"
            assert data["message_count"] == 1  # only email4 has thread-002


class TestReadPersonResource:
    """Tests for read_person_resource."""

    def test_existing_person_by_id(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            # Alice is the first person added, so id=1
            result = read_person_resource(session, "1")
            assert result is not None
            data = json.loads(result)
            assert data["name"] == "Alice Smith"
            assert data["primary_email"] == "alice@example.com"
            assert data["relationship_type"] == "colleague"
            assert data["email_count"] == 10
            assert data["first_contact"] is not None
            assert data["last_contact"] is not None

    def test_second_person(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_person_resource(session, "2")
            assert result is not None
            data = json.loads(result)
            assert data["name"] == "Bob Jones"
            assert data["primary_email"] == "bob@example.com"

    def test_person_not_found_returns_none(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_person_resource(session, "99999")
            assert result is None

    def test_invalid_id_returns_none(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_person_resource(session, "not-a-number")
            assert result is None

    def test_person_dates_iso_format(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_person_resource(session, "1")
            data = json.loads(result)
            assert "2023-01-01" in data["first_contact"]
            assert "2024-01-15" in data["last_contact"]

    def test_person_without_dates(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            # Charlie (id=3) has no first_contact / last_contact set
            result = read_person_resource(session, "3")
            data = json.loads(result)
            assert data["name"] == "Charlie Brown"
            assert data["first_contact"] is None
            assert data["last_contact"] is None


class TestReadStatsResource:
    """Tests for read_stats_resource."""

    def test_stats_returns_valid_json(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            result = read_stats_resource(session)
            assert isinstance(result, str)
            data = json.loads(result)
            assert isinstance(data, dict)

    def test_stats_counts(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            data = json.loads(read_stats_resource(session))
            assert data["emails"] == 5
            assert data["people"] == 3
            assert data["threads"] == 2
            assert data["tags"] == 4
            assert data["attachments"] == 1

    def test_stats_date_range(self, populated_db: Database) -> None:
        with populated_db.session() as session:
            data = json.loads(read_stats_resource(session))
            assert data["date_from"] is not None
            assert data["date_to"] is not None
            # Earliest email is 2024-01-15, latest is 2024-01-17
            assert "2024-01-15" in data["date_from"]
            assert "2024-01-17" in data["date_to"]

    def test_stats_on_empty_db(self, db: Database) -> None:
        with db.session() as session:
            data = json.loads(read_stats_resource(session))
            assert data["emails"] == 0
            assert data["people"] == 0
            assert data["threads"] == 0
            assert data["tags"] == 0
            assert data["attachments"] == 0
            assert data["date_from"] is None
            assert data["date_to"] is None
