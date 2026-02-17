"""Input validation helpers for MCP tool arguments."""

from __future__ import annotations

from typing import Any


def require_str(args: dict[str, Any], key: str) -> str:
    """Extract a required string argument."""
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required argument: {key}")
    return value


def optional_str(args: dict[str, Any], key: str, default: str | None = None) -> str | None:
    """Extract an optional string argument."""
    value = args.get(key)
    if value is None:
        return default
    return str(value)


def optional_int(args: dict[str, Any], key: str, default: int = 0) -> int:
    """Extract an optional integer argument."""
    value = args.get(key)
    if value is None:
        return default
    return int(value)


def optional_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    """Extract an optional boolean argument."""
    value = args.get(key)
    if value is None:
        return default
    return bool(value)


def optional_list(args: dict[str, Any], key: str) -> list[str]:
    """Extract an optional list-of-strings argument."""
    value = args.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]
