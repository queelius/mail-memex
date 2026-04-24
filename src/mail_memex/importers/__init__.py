"""Email import handlers for various formats."""

from mail_memex.importers.arkiv import detect as detect_arkiv
from mail_memex.importers.arkiv import import_arkiv
from mail_memex.importers.base import BaseImporter, ImportStats
from mail_memex.importers.eml import EmlImporter, GmailTakeoutImporter
from mail_memex.importers.mbox import MboxImporter
from mail_memex.importers.parser import EmailParser, ParsedAttachment, ParsedEmail

__all__ = [
    "EmailParser",
    "ParsedEmail",
    "ParsedAttachment",
    "BaseImporter",
    "ImportStats",
    "MboxImporter",
    "EmlImporter",
    "GmailTakeoutImporter",
    "import_arkiv",
    "detect_arkiv",
]
