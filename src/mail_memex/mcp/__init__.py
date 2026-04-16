"""MCP server for mail-memex."""

from __future__ import annotations


def run_server() -> None:
    """Run the MCP server on stdio transport."""
    from mail_memex.mcp.server import create_server

    mcp = create_server()
    mcp.run(transport="stdio")
