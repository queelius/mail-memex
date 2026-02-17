"""Email import handlers for various formats."""

from mtk.importers.base import BaseImporter, ImportStats
from mtk.importers.eml import EmlImporter, GmailTakeoutImporter
from mtk.importers.maildir import MaildirImporter
from mtk.importers.mbox import MboxImporter
from mtk.importers.parser import EmailParser, ParsedAttachment, ParsedEmail

__all__ = [
    "EmailParser",
    "ParsedEmail",
    "ParsedAttachment",
    "BaseImporter",
    "ImportStats",
    "MaildirImporter",
    "MboxImporter",
    "EmlImporter",
    "GmailTakeoutImporter",
]
