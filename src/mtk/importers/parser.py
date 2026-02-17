"""Email parsing utilities using Python's email module.

This module provides robust parsing of email files/bytes into a clean
dataclass representation, handling encoding issues, multipart messages,
and attachment extraction.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import BinaryIO


@dataclass
class ParsedAttachment:
    """A parsed email attachment."""

    filename: str | None
    content_type: str
    size: int
    content_id: str | None
    # We store content separately or reference by hash
    content_hash: str | None = None


@dataclass
class ParsedEmail:
    """A parsed email with all extracted data."""

    # Identifiers
    message_id: str
    file_path: Path | None = None

    # Headers
    from_addr: str = ""
    from_name: str | None = None
    to_addrs: list[str] = field(default_factory=list)
    to_names: list[str | None] = field(default_factory=list)
    cc_addrs: list[str] = field(default_factory=list)
    cc_names: list[str | None] = field(default_factory=list)
    bcc_addrs: list[str] = field(default_factory=list)
    subject: str | None = None
    date: datetime | None = None

    # Threading
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)

    # Content
    body_text: str | None = None
    body_html: str | None = None

    # Attachments (metadata only)
    attachments: list[ParsedAttachment] = field(default_factory=list)

    # Raw headers for preservation
    raw_headers: dict[str, str] = field(default_factory=dict)

    @property
    def body_preview(self) -> str | None:
        """Generate a preview of the email body (first 500 chars)."""
        text = self.body_text or ""
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 500:
            return text[:497] + "..."
        return text if text else None

    @property
    def all_recipients(self) -> list[str]:
        """All recipient addresses (to + cc + bcc)."""
        return self.to_addrs + self.cc_addrs + self.bcc_addrs


class EmailParser:
    """Parser for email files and bytes.

    Handles various encodings, multipart messages, and attachment extraction.
    Uses Python's email.policy.default for modern parsing behavior.
    """

    def __init__(self) -> None:
        self.policy = email.policy.default

    def parse_file(self, path: Path) -> ParsedEmail:
        """Parse an email file.

        Args:
            path: Path to the email file.

        Returns:
            ParsedEmail with extracted data.

        Raises:
            ValueError: If the file cannot be parsed as email.
        """
        with open(path, "rb") as f:
            parsed = self.parse_bytes(f.read())
            parsed.file_path = path
            return parsed

    def parse_bytes(self, data: bytes) -> ParsedEmail:
        """Parse email from bytes.

        Args:
            data: Raw email bytes.

        Returns:
            ParsedEmail with extracted data.
        """
        msg = email.message_from_bytes(data, policy=self.policy)
        return self._parse_message(msg)

    def parse_string(self, data: str) -> ParsedEmail:
        """Parse email from string.

        Args:
            data: Email as string.

        Returns:
            ParsedEmail with extracted data.
        """
        msg = email.message_from_string(data, policy=self.policy)
        return self._parse_message(msg)

    def parse_file_handle(self, fp: BinaryIO) -> ParsedEmail:
        """Parse email from file handle.

        Args:
            fp: Binary file handle.

        Returns:
            ParsedEmail with extracted data.
        """
        msg = email.message_from_binary_file(fp, policy=self.policy)
        return self._parse_message(msg)

    def _parse_message(self, msg: EmailMessage) -> ParsedEmail:
        """Parse an EmailMessage object into our dataclass."""
        # Extract Message-ID (generate if missing)
        message_id = self._get_message_id(msg)

        # Parse From header
        from_addr, from_name = self._parse_address(msg.get("From", ""))

        # Parse To/CC/BCC
        to_addrs, to_names = self._parse_address_list(msg.get("To", ""))
        cc_addrs, cc_names = self._parse_address_list(msg.get("Cc", ""))
        bcc_addrs, bcc_names = self._parse_address_list(msg.get("Bcc", ""))

        # Parse date
        date = self._parse_date(msg.get("Date"))

        # Parse threading headers
        in_reply_to = self._clean_message_id(msg.get("In-Reply-To"))
        references = self._parse_references(msg.get("References", ""))

        # Extract body content
        body_text, body_html = self._extract_body(msg)

        # Extract attachment metadata
        attachments = self._extract_attachments(msg)

        # Preserve raw headers
        raw_headers = {k: str(v) for k, v in msg.items()}

        return ParsedEmail(
            message_id=message_id,
            from_addr=from_addr,
            from_name=from_name,
            to_addrs=to_addrs,
            to_names=to_names,
            cc_addrs=cc_addrs,
            cc_names=cc_names,
            bcc_addrs=bcc_addrs,
            subject=msg.get("Subject"),
            date=date,
            in_reply_to=in_reply_to,
            references=references,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            raw_headers=raw_headers,
        )

    def _get_message_id(self, msg: EmailMessage) -> str:
        """Get or generate a Message-ID."""
        msg_id = msg.get("Message-ID", "")
        if msg_id:
            return self._clean_message_id(msg_id) or msg_id

        # Generate a hash-based ID if missing
        content = msg.as_bytes()
        hash_id = hashlib.sha256(content).hexdigest()[:32]
        return f"generated-{hash_id}@mtk.local"

    def _clean_message_id(self, msg_id: str | None) -> str | None:
        """Clean a Message-ID, removing angle brackets."""
        if not msg_id:
            return None
        # Remove angle brackets and whitespace
        cleaned = msg_id.strip().strip("<>").strip()
        return cleaned if cleaned else None

    def _parse_address(self, header: str) -> tuple[str, str | None]:
        """Parse a single address header into (email, name)."""
        if not header:
            return "", None

        # Use email.utils for robust parsing
        name_str, addr = email.utils.parseaddr(header)
        addr = addr.lower() if addr else ""
        name_out: str | None = name_str.strip() if name_str else None
        return addr, name_out

    def _parse_address_list(self, header: str) -> tuple[list[str], list[str | None]]:
        """Parse an address list header into ([emails], [names])."""
        if not header:
            return [], []

        # Parse all addresses
        addresses = email.utils.getaddresses([header])
        emails = []
        names = []
        for name, addr in addresses:
            if addr:  # Skip empty addresses
                emails.append(addr.lower())
                names.append(name.strip() if name else None)
        return emails, names

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse a date header into datetime."""
        if not date_str:
            return None

        try:
            # email.utils.parsedate_to_datetime handles most formats
            return email.utils.parsedate_to_datetime(date_str)
        except (ValueError, TypeError):
            pass

        # Try some fallback patterns
        fallback_patterns = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y %H:%M:%S %z",
            "%Y-%m-%d %H:%M:%S",
        ]
        for pattern in fallback_patterns:
            try:
                return datetime.strptime(date_str.strip(), pattern)
            except ValueError:
                continue

        return None

    def _parse_references(self, header: str) -> list[str]:
        """Parse References header into list of Message-IDs."""
        if not header:
            return []

        # References are space-separated Message-IDs
        refs = []
        for part in header.split():
            cleaned = self._clean_message_id(part)
            if cleaned:
                refs.append(cleaned)
        return refs

    def _extract_body(self, msg: EmailMessage) -> tuple[str | None, str | None]:
        """Extract text and HTML body from message."""
        body_text = None
        body_html = None

        if msg.is_multipart():
            # Walk through all parts
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get_content_disposition()

                # Skip attachments
                if disposition == "attachment":
                    continue

                if content_type == "text/plain" and body_text is None:
                    body_text = self._get_text_content(part)
                elif content_type == "text/html" and body_html is None:
                    body_html = self._get_text_content(part)
        else:
            content_type = msg.get_content_type()
            if content_type == "text/plain":
                body_text = self._get_text_content(msg)
            elif content_type == "text/html":
                body_html = self._get_text_content(msg)

        return body_text, body_html

    def _get_text_content(self, part: EmailMessage) -> str | None:
        """Extract text content from a message part."""
        try:
            content = part.get_content()
            if isinstance(content, str):
                return content
            if isinstance(content, bytes):
                # Try to decode
                charset = part.get_content_charset() or "utf-8"
                try:
                    return content.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    return content.decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def _extract_attachments(self, msg: EmailMessage) -> list[ParsedAttachment]:
        """Extract attachment metadata from message."""
        attachments = []

        for part in msg.walk():
            disposition = part.get_content_disposition()
            if disposition != "attachment":
                # Also check for inline attachments with filenames
                filename = part.get_filename()
                if not filename:
                    continue

            content_type = part.get_content_type()
            filename = part.get_filename()
            content_id = part.get("Content-ID")

            # Get content size
            try:
                raw_content = part.get_payload(decode=True)
                content_bytes: bytes | None = (
                    raw_content if isinstance(raw_content, bytes) else None
                )
                size = len(content_bytes) if content_bytes else 0
                # Generate content hash for deduplication
                content_hash = hashlib.sha256(content_bytes).hexdigest() if content_bytes else None
            except Exception:
                size = 0
                content_hash = None

            attachments.append(
                ParsedAttachment(
                    filename=filename,
                    content_type=content_type,
                    size=size,
                    content_id=content_id,
                    content_hash=content_hash,
                )
            )

        return attachments
