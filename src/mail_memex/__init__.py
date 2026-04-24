"""mail-memex - Personal email archive.

Personal email archive with full-text search and SQL/MCP access.
Part of the memex personal archive ecosystem.
"""

# Read the installed-package version at runtime. This is robust to pytest
# configurations where a ``tests/mail_memex/`` shadow package could hide
# the real one — importlib.metadata always consults dist-info.
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("mail-memex")
except PackageNotFoundError:
    __version__ = "unknown"
