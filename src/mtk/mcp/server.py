"""MCP server setup, tool/resource registration, and dispatch.

Exposes mtk as a full-access MCP tool server for Claude Code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server import Server
from mcp.types import (
    Resource,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
)

from mtk.core.config import MtkConfig
from mtk.core.database import Database
from mtk.mcp.resources import (
    RESOURCE_HANDLERS,
    read_stats_resource,
)
from mtk.mcp.tools import TOOL_HANDLERS


def _get_db_path() -> Path:
    """Resolve database path from environment or config."""
    env_path = os.environ.get("MTK_DATABASE_PATH")
    if env_path:
        return Path(env_path)

    config = MtkConfig.load()
    if config.db_path:
        return config.db_path

    return MtkConfig.default_data_dir() / "mtk.db"


def _get_privacy_filter():
    """Load privacy filter (None if disabled via env)."""
    if os.environ.get("MTK_MCP_SKIP_PRIVACY", "").strip() == "1":
        return None

    from mtk.core.config import PrivacyConfig
    from mtk.core.privacy import PrivacyFilter

    privacy_config = PrivacyConfig.load()
    return PrivacyFilter(privacy_config)


# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema for input)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_emails",
        "description": "Search emails using query string. Supports operators: from:, to:, subject:, after:YYYY-MM-DD, before:YYYY-MM-DD, tag:, has:attachment",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_inbox",
        "description": "Get recent emails (inbox view)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20)",
                    "default": 20,
                },
                "since": {"type": "string", "description": "Show emails since date (YYYY-MM-DD)"},
            },
        },
    },
    {
        "name": "get_stats",
        "description": "Get email archive statistics (counts, date range)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "show_email",
        "description": "Show a single email with full content by message ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID (full or partial)"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "show_thread",
        "description": "Show full thread conversation by thread ID or message ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Thread ID or message ID"},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_reply_context",
        "description": "Get context for composing a reply (original email, thread history, suggested headers)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID to reply to"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "tag_email",
        "description": "Add or remove tags from a single email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Message ID"},
                "add": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to add",
                },
                "remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to remove",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "tag_batch",
        "description": "Add or remove tags from all emails matching a search query",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to match emails"},
                "add": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to add",
                },
                "remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags to remove",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_tags",
        "description": "List all tags in the archive with email counts",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_people",
        "description": "List top correspondents by email count",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "show_person",
        "description": "Show detailed info for a specific person",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "integer", "description": "Person ID"},
            },
            "required": ["person_id"],
        },
    },
    {
        "name": "get_correspondence_timeline",
        "description": "Get email count over time for a correspondent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "integer", "description": "Person ID"},
                "granularity": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                    "description": "Time granularity (default: month)",
                    "default": "month",
                },
            },
            "required": ["person_id"],
        },
    },
    {
        "name": "notmuch_sync",
        "description": "Run notmuch sync operations (status, pull, push, sync)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "pull", "push", "sync"],
                    "description": "Sync action to perform (default: status)",
                    "default": "status",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["merge", "notmuch-wins", "mtk-wins"],
                    "description": "Sync strategy (for sync action)",
                    "default": "merge",
                },
            },
        },
    },
]


def create_server() -> Server:
    """Create and configure the MCP server."""
    server = Server("mtk")

    db_path = _get_db_path()
    db = Database(db_path)
    db.create_tables()
    # Privacy filter loaded here for future content filtering
    _get_privacy_filter()

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name=td["name"],
                description=td["description"],
                inputSchema=td["inputSchema"],
            )
            for td in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

        with db.session() as session:
            raw_results = handler(session, arguments)

        # Convert raw dicts to TextContent
        return [
            TextContent(type="text", text=r["text"]) if isinstance(r, dict) else r
            for r in raw_results
        ]

    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return [
            Resource(
                uri="mtk://stats",
                name="Archive Statistics",
                description="Email archive statistics",
                mimeType="application/json",
            ),
        ]

    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="mtk://email/{message_id}",
                name="Email",
                description="A single email by message ID",
                mimeType="application/json",
            ),
            ResourceTemplate(
                uriTemplate="mtk://thread/{thread_id}",
                name="Thread",
                description="A thread conversation",
                mimeType="application/json",
            ),
            ResourceTemplate(
                uriTemplate="mtk://person/{person_id}",
                name="Person",
                description="A person/correspondent",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def handle_read_resource(uri: str) -> list[TextResourceContents]:
        parsed = urlparse(str(uri))
        # URI format: mtk://resource_type/id
        resource_type = parsed.netloc
        resource_id = parsed.path.lstrip("/")

        with db.session() as session:
            if resource_type == "stats":
                content = read_stats_resource(session)
            else:
                handler = RESOURCE_HANDLERS.get(resource_type)
                if not handler:
                    content = f'{{"error": "Unknown resource type: {resource_type}"}}'
                else:
                    content = handler(session, resource_id)
                    if content is None:
                        content = f'{{"error": "{resource_type} not found: {resource_id}"}}'

        return [
            TextResourceContents(
                uri=str(uri),
                mimeType="application/json",
                text=content,
            )
        ]

    return server
