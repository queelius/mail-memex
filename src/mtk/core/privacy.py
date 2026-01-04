"""Privacy filtering for email export and display.

Implements exclusion rules and redaction patterns from privacy.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mtk.core.models import Email

from mtk.core.config import PrivacyConfig


@dataclass
class ExclusionResult:
    """Result of checking if an email should be excluded."""

    excluded: bool
    reason: str = ""


@dataclass
class RedactionResult:
    """Result of applying redactions to text."""

    original: str
    redacted: str
    redaction_count: int = 0


@dataclass
class PrivacyReport:
    """Summary of privacy filtering applied to a set of emails."""

    total_emails: int = 0
    excluded_count: int = 0
    redacted_count: int = 0
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    redaction_patterns_applied: dict[str, int] = field(default_factory=dict)


class PrivacyFilter:
    """Apply privacy rules to emails for safe export.

    Usage:
        config = PrivacyConfig.load()
        filter = PrivacyFilter(config)

        # Check single email
        result = filter.should_exclude(email)
        if result.excluded:
            print(f"Skipping: {result.reason}")

        # Redact email content
        safe_email = filter.redact_email(email)

        # Preview what would happen
        report = filter.preview(emails)
    """

    def __init__(self, config: PrivacyConfig) -> None:
        self.config = config
        self._compiled_exclude_patterns: list[re.Pattern] = []
        self._compiled_redact_patterns: list[tuple[re.Pattern, str]] = []
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for performance."""
        # Exclusion patterns
        for pattern in self.config.exclude_patterns:
            try:
                self._compiled_exclude_patterns.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error:
                pass  # Skip invalid patterns

        # Redaction patterns
        for rule in self.config.redact_patterns:
            pattern = rule.get("pattern", "")
            replacement = rule.get("replacement", "[REDACTED]")
            try:
                self._compiled_redact_patterns.append(
                    (re.compile(pattern), replacement)
                )
            except re.error:
                pass  # Skip invalid patterns

    def should_exclude(self, email: Email) -> ExclusionResult:
        """Check if an email should be excluded from export.

        Args:
            email: Email to check.

        Returns:
            ExclusionResult with excluded flag and reason.
        """
        # Check sender address
        if email.from_addr:
            for addr in self.config.exclude_addresses:
                if addr.lower() in email.from_addr.lower():
                    return ExclusionResult(
                        excluded=True, reason=f"address:{addr}"
                    )

        # Check tags
        if hasattr(email, "tags") and email.tags:
            for tag in email.tags:
                if tag.name in self.config.exclude_tags:
                    return ExclusionResult(
                        excluded=True, reason=f"tag:{tag.name}"
                    )

        # Check content patterns
        content = f"{email.subject or ''} {email.body_text or ''}"
        for i, pattern in enumerate(self._compiled_exclude_patterns):
            if pattern.search(content):
                return ExclusionResult(
                    excluded=True,
                    reason=f"pattern:{self.config.exclude_patterns[i]}",
                )

        return ExclusionResult(excluded=False)

    def redact_text(self, text: str) -> RedactionResult:
        """Apply redaction patterns to text.

        Args:
            text: Text to redact.

        Returns:
            RedactionResult with original, redacted text, and count.
        """
        if not text:
            return RedactionResult(original="", redacted="", redaction_count=0)

        result = text
        total_count = 0

        for pattern, replacement in self._compiled_redact_patterns:
            new_result, count = pattern.subn(replacement, result)
            total_count += count
            result = new_result

        return RedactionResult(
            original=text, redacted=result, redaction_count=total_count
        )

    def redact_email(self, email: Email) -> dict:
        """Create a redacted copy of email data.

        Args:
            email: Email to redact.

        Returns:
            Dictionary with redacted email data (not an Email object
            to avoid modifying the database).
        """
        subject_result = self.redact_text(email.subject or "")
        body_result = self.redact_text(email.body_text or "")
        html_result = self.redact_text(email.body_html or "")

        return {
            "message_id": email.message_id,
            "from_addr": email.from_addr,
            "from_name": email.from_name,
            "subject": subject_result.redacted,
            "date": email.date,
            "in_reply_to": email.in_reply_to,
            "references": email.references,
            "body_text": body_result.redacted,
            "body_html": html_result.redacted,
            "body_preview": self.redact_text(email.body_preview or "").redacted,
            "thread_id": email.thread_id,
            "tags": [t.name for t in email.tags] if hasattr(email, "tags") else [],
            "attachments": [
                {
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                }
                for a in email.attachments
            ]
            if hasattr(email, "attachments")
            else [],
            "_redactions": {
                "subject": subject_result.redaction_count,
                "body": body_result.redaction_count,
            },
        }

    def preview(self, emails: list[Email]) -> PrivacyReport:
        """Preview what privacy filtering would do to a set of emails.

        Args:
            emails: List of emails to check.

        Returns:
            PrivacyReport with statistics.
        """
        report = PrivacyReport(total_emails=len(emails))

        for email in emails:
            # Check exclusion
            result = self.should_exclude(email)
            if result.excluded:
                report.excluded_count += 1
                report.exclusion_reasons[result.reason] = (
                    report.exclusion_reasons.get(result.reason, 0) + 1
                )
                continue

            # Check redactions
            subject_result = self.redact_text(email.subject or "")
            body_result = self.redact_text(email.body_text or "")

            if subject_result.redaction_count > 0 or body_result.redaction_count > 0:
                report.redacted_count += 1

            # Track which patterns matched
            for pattern, _ in self._compiled_redact_patterns:
                pattern_str = pattern.pattern
                matches = len(pattern.findall(email.subject or "")) + len(
                    pattern.findall(email.body_text or "")
                )
                if matches > 0:
                    report.redaction_patterns_applied[pattern_str] = (
                        report.redaction_patterns_applied.get(pattern_str, 0)
                        + matches
                    )

        return report

    def filter_emails(
        self, emails: list[Email], apply_redaction: bool = True
    ) -> tuple[list[dict], PrivacyReport]:
        """Filter and optionally redact a list of emails.

        Args:
            emails: Emails to filter.
            apply_redaction: Whether to apply redaction patterns.

        Returns:
            Tuple of (filtered email dicts, privacy report).
        """
        report = PrivacyReport(total_emails=len(emails))
        result = []

        for email in emails:
            # Check exclusion
            exclusion = self.should_exclude(email)
            if exclusion.excluded:
                report.excluded_count += 1
                report.exclusion_reasons[exclusion.reason] = (
                    report.exclusion_reasons.get(exclusion.reason, 0) + 1
                )
                continue

            if apply_redaction:
                email_data = self.redact_email(email)
                if (
                    email_data["_redactions"]["subject"] > 0
                    or email_data["_redactions"]["body"] > 0
                ):
                    report.redacted_count += 1
                del email_data["_redactions"]  # Don't include in output
            else:
                email_data = {
                    "message_id": email.message_id,
                    "from_addr": email.from_addr,
                    "from_name": email.from_name,
                    "subject": email.subject,
                    "date": email.date,
                    "in_reply_to": email.in_reply_to,
                    "references": email.references,
                    "body_text": email.body_text,
                    "body_html": email.body_html,
                    "body_preview": email.body_preview,
                    "thread_id": email.thread_id,
                    "tags": [t.name for t in email.tags]
                    if hasattr(email, "tags")
                    else [],
                    "attachments": [
                        {
                            "filename": a.filename,
                            "content_type": a.content_type,
                            "size": a.size,
                        }
                        for a in email.attachments
                    ]
                    if hasattr(email, "attachments")
                    else [],
                }

            result.append(email_data)

        return result, report
