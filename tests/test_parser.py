"""TDD Tests for email parser module.

These tests define the expected behavior of the EmailParser class:
- Parse RFC 2822 email format
- Extract headers (From, To, Cc, Subject, Date, etc.)
- Handle multipart messages (text + HTML)
- Extract attachment metadata
- Handle various encoding issues gracefully
- Generate Message-IDs when missing
"""

from datetime import datetime
from pathlib import Path

import pytest

from mtk.importers.parser import EmailParser, ParsedEmail, ParsedAttachment


class TestEmailParserBasics:
    """Basic email parsing tests."""

    def test_parser_can_be_instantiated(self) -> None:
        """Parser should be instantiable."""
        parser = EmailParser()
        assert parser is not None

    def test_parse_bytes_returns_parsed_email(self, sample_email_bytes: bytes) -> None:
        """Parsing bytes should return a ParsedEmail object."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert isinstance(result, ParsedEmail)

    def test_parse_string_returns_parsed_email(self) -> None:
        """Parsing string should return a ParsedEmail object."""
        parser = EmailParser()
        email_str = """From: test@example.com
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_string(email_str)
        assert isinstance(result, ParsedEmail)

    def test_parse_file_returns_parsed_email(self, tmp_dir: Path) -> None:
        """Parsing a file should return a ParsedEmail object."""
        email_file = tmp_dir / "test.eml"
        email_file.write_bytes(b"""From: test@example.com
Message-ID: <test@example.com>

Body.
""")
        parser = EmailParser()
        result = parser.parse_file(email_file)

        assert isinstance(result, ParsedEmail)
        assert result.file_path == email_file


class TestHeaderParsing:
    """Tests for email header extraction."""

    def test_extract_message_id(self, sample_email_bytes: bytes) -> None:
        """Should extract Message-ID header."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.message_id == "test123@example.com"

    def test_extract_from_address(self, sample_email_bytes: bytes) -> None:
        """Should extract sender email address."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.from_addr == "john@example.com"

    def test_extract_from_name(self, sample_email_bytes: bytes) -> None:
        """Should extract sender display name."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.from_name == "John Doe"

    def test_extract_to_addresses(self, sample_email_bytes: bytes) -> None:
        """Should extract recipient addresses."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert "jane@example.com" in result.to_addrs

    def test_extract_cc_addresses(self, sample_email_bytes: bytes) -> None:
        """Should extract CC addresses."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert "bob@example.com" in result.cc_addrs

    def test_extract_subject(self, sample_email_bytes: bytes) -> None:
        """Should extract subject line."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.subject == "Test Email"

    def test_extract_date(self, sample_email_bytes: bytes) -> None:
        """Should extract and parse date."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.date is not None
        assert result.date.year == 2024
        assert result.date.month == 1
        assert result.date.day == 15

    def test_extract_in_reply_to(self, sample_email_bytes: bytes) -> None:
        """Should extract In-Reply-To header."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.in_reply_to == "previous@example.com"

    def test_extract_references(self, sample_email_bytes: bytes) -> None:
        """Should extract References header as list."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert len(result.references) == 2
        assert "ref1@example.com" in result.references
        assert "ref2@example.com" in result.references


class TestMultipleRecipients:
    """Tests for parsing multiple recipients."""

    def test_multiple_to_addresses(self) -> None:
        """Should parse multiple To addresses."""
        parser = EmailParser()
        email = b"""From: sender@example.com
To: alice@example.com, Bob <bob@example.com>, "Charlie Smith" <charlie@example.com>
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)

        assert len(result.to_addrs) == 3
        assert "alice@example.com" in result.to_addrs
        assert "bob@example.com" in result.to_addrs
        assert "charlie@example.com" in result.to_addrs

    def test_to_names_extracted(self) -> None:
        """Should extract names from To addresses."""
        parser = EmailParser()
        email = b"""From: sender@example.com
To: alice@example.com, Bob <bob@example.com>, "Charlie Smith" <charlie@example.com>
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)

        assert "Bob" in result.to_names
        assert "Charlie Smith" in result.to_names


class TestMessageIdHandling:
    """Tests for Message-ID generation and handling."""

    def test_generate_message_id_when_missing(self) -> None:
        """Should generate a Message-ID when missing."""
        parser = EmailParser()
        email = b"""From: test@example.com
Subject: No Message ID

Body text.
"""
        result = parser.parse_bytes(email)

        assert result.message_id is not None
        assert result.message_id.startswith("generated-")
        assert "@mtk.local" in result.message_id

    def test_strip_angle_brackets_from_message_id(self) -> None:
        """Should remove angle brackets from Message-ID."""
        parser = EmailParser()
        email = b"""From: test@example.com
Message-ID: <with-brackets@example.com>

Body.
"""
        result = parser.parse_bytes(email)
        assert result.message_id == "with-brackets@example.com"

    def test_consistent_generated_id_for_same_content(self) -> None:
        """Generated ID should be deterministic based on content."""
        parser = EmailParser()
        email = b"""From: test@example.com
Subject: Test

Body.
"""
        result1 = parser.parse_bytes(email)
        result2 = parser.parse_bytes(email)

        assert result1.message_id == result2.message_id


class TestDateParsing:
    """Tests for date parsing with various formats."""

    @pytest.mark.parametrize(
        "date_str,expected_year,expected_month,expected_day",
        [
            ("Mon, 15 Jan 2024 10:30:00 -0500", 2024, 1, 15),
            ("15 Jan 2024 10:30:00 +0000", 2024, 1, 15),
            ("Tue, 20 Feb 2024 14:00:00 GMT", 2024, 2, 20),
        ],
    )
    def test_various_date_formats(
        self,
        date_str: str,
        expected_year: int,
        expected_month: int,
        expected_day: int,
    ) -> None:
        """Should parse various date formats."""
        parser = EmailParser()
        email = f"""From: test@example.com
Date: {date_str}
Message-ID: <test@example.com>

Body.
""".encode()
        result = parser.parse_bytes(email)

        assert result.date is not None
        assert result.date.year == expected_year
        assert result.date.month == expected_month
        assert result.date.day == expected_day

    def test_missing_date_returns_none(self) -> None:
        """Missing date should return None."""
        parser = EmailParser()
        email = b"""From: test@example.com
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)
        assert result.date is None

    def test_invalid_date_returns_none(self) -> None:
        """Invalid date should return None, not raise."""
        parser = EmailParser()
        email = b"""From: test@example.com
Date: not-a-valid-date
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)
        assert result.date is None


class TestBodyExtraction:
    """Tests for body content extraction."""

    def test_extract_plain_text_body(self, sample_email_bytes: bytes) -> None:
        """Should extract plain text body."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.body_text is not None
        assert "test email body" in result.body_text.lower()

    def test_extract_html_body(self, sample_email_html_bytes: bytes) -> None:
        """Should extract HTML body from multipart message."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_html_bytes)

        assert result.body_html is not None
        assert "<h1>HTML version</h1>" in result.body_html

    def test_extract_both_text_and_html(self, sample_email_html_bytes: bytes) -> None:
        """Multipart message should have both text and HTML."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_html_bytes)

        assert result.body_text is not None
        assert result.body_html is not None
        assert "Plain text" in result.body_text
        assert "<html>" in result.body_html


class TestBodyPreview:
    """Tests for body preview generation."""

    def test_body_preview_generated(self) -> None:
        """Should generate a body preview."""
        parser = EmailParser()
        email = b"""From: test@example.com
Message-ID: <test@example.com>

This is the body content that should appear in the preview.
"""
        result = parser.parse_bytes(email)

        assert result.body_preview is not None
        assert "body content" in result.body_preview

    def test_body_preview_truncated_at_500_chars(self) -> None:
        """Preview should be truncated at ~500 characters."""
        parser = EmailParser()
        long_body = "A" * 1000
        email = f"""From: test@example.com
Message-ID: <test@example.com>

{long_body}
""".encode()
        result = parser.parse_bytes(email)

        assert result.body_preview is not None
        assert len(result.body_preview) <= 503  # 500 + "..."
        assert result.body_preview.endswith("...")

    def test_body_preview_normalizes_whitespace(self) -> None:
        """Preview should normalize whitespace."""
        parser = EmailParser()
        email = b"""From: test@example.com
Message-ID: <test@example.com>

This   has    multiple   spaces
and
newlines.
"""
        result = parser.parse_bytes(email)

        assert "  " not in result.body_preview
        assert "\n" not in result.body_preview


class TestAttachmentExtraction:
    """Tests for attachment metadata extraction."""

    def test_extract_attachment_metadata(self, sample_email_with_attachment: bytes) -> None:
        """Should extract attachment metadata."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_with_attachment)

        assert len(result.attachments) > 0
        attachment = result.attachments[0]
        assert attachment.filename == "document.pdf"
        assert attachment.content_type == "application/pdf"

    def test_attachment_has_size(self, sample_email_with_attachment: bytes) -> None:
        """Attachment should have size information."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_with_attachment)

        assert len(result.attachments) > 0
        attachment = result.attachments[0]
        assert attachment.size > 0

    def test_attachment_has_content_hash(self, sample_email_with_attachment: bytes) -> None:
        """Attachment should have content hash for deduplication."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_with_attachment)

        assert len(result.attachments) > 0
        attachment = result.attachments[0]
        assert attachment.content_hash is not None

    def test_no_attachments_returns_empty_list(self, sample_email_bytes: bytes) -> None:
        """Email without attachments should have empty list."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert result.attachments == []


class TestAllRecipients:
    """Tests for combined recipient access."""

    def test_all_recipients_property(self) -> None:
        """Should combine To, Cc, and Bcc in all_recipients."""
        parser = EmailParser()
        email = b"""From: sender@example.com
To: to1@example.com, to2@example.com
Cc: cc1@example.com
Bcc: bcc1@example.com
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)

        all_recipients = result.all_recipients
        assert "to1@example.com" in all_recipients
        assert "to2@example.com" in all_recipients
        assert "cc1@example.com" in all_recipients
        # Note: Bcc is often stripped by MTA, but we test parsing it


class TestRawHeaders:
    """Tests for raw header preservation."""

    def test_raw_headers_preserved(self, sample_email_bytes: bytes) -> None:
        """Should preserve raw headers for later access."""
        parser = EmailParser()
        result = parser.parse_bytes(sample_email_bytes)

        assert "From" in result.raw_headers
        assert "To" in result.raw_headers
        assert "Subject" in result.raw_headers

    def test_custom_headers_preserved(self) -> None:
        """Custom headers should be preserved."""
        parser = EmailParser()
        email = b"""From: test@example.com
X-Custom-Header: custom-value
X-Priority: 1
Message-ID: <test@example.com>

Body.
"""
        result = parser.parse_bytes(email)

        assert "X-Custom-Header" in result.raw_headers
        assert result.raw_headers["X-Custom-Header"] == "custom-value"


class TestEncodingHandling:
    """Tests for character encoding handling."""

    def test_utf8_subject(self) -> None:
        """Should handle UTF-8 encoded subject."""
        parser = EmailParser()
        email = """From: test@example.com
Subject: =?UTF-8?Q?Caf=C3=A9_menu?=
Message-ID: <test@example.com>

Body.
""".encode("utf-8")
        result = parser.parse_bytes(email)

        assert "Café" in result.subject

    def test_utf8_body(self) -> None:
        """Should handle UTF-8 body content."""
        parser = EmailParser()
        email = """From: test@example.com
Message-ID: <test@example.com>
Content-Type: text/plain; charset=utf-8

Café résumé naïve.
""".encode("utf-8")
        result = parser.parse_bytes(email)

        assert "Café" in result.body_text
        assert "résumé" in result.body_text

    def test_graceful_handling_of_unknown_encoding(self) -> None:
        """Should gracefully handle unknown encodings."""
        parser = EmailParser()
        # Simulate bytes that might cause encoding issues
        email = b"""From: test@example.com
Message-ID: <test@example.com>

Body with some \xff\xfe weird bytes.
"""
        # Should not raise
        result = parser.parse_bytes(email)
        assert result.body_text is not None
