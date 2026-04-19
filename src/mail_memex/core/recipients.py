"""Helpers for populating the email_recipients side table.

The table exists so `to:alice`-style searches can use an index instead
of LIKE-scanning the CSV to_addrs/cc_addrs/bcc_addrs columns. These
helpers handle the three ingestion paths:

- File/mbox/eml importers have already parsed addresses into lists
- IMAP pull works from raw header strings
- Existing databases need a one-shot backfill from the CSV columns
"""

from __future__ import annotations

import email.utils
from typing import TYPE_CHECKING

from sqlalchemy import select

from mail_memex.core.models import Email, EmailRecipient

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


VALID_KINDS = frozenset({"to", "cc", "bcc"})


def _pair_names(addrs: list[str], names: list[str | None] | None) -> list[tuple[str, str | None]]:
    """Zip addresses with optional display names, padding names with None."""
    if not names:
        return [(a, None) for a in addrs]
    pairs: list[tuple[str, str | None]] = []
    for i, addr in enumerate(addrs):
        pairs.append((addr, names[i] if i < len(names) else None))
    return pairs


def build_recipients(
    addrs: list[str], names: list[str | None] | None, kind: str
) -> list[EmailRecipient]:
    """Build EmailRecipient rows from a pre-parsed (addrs, names) pair.

    Used by file/mbox/eml importers where the parser has already split
    the header into individual addresses.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid recipient kind: {kind!r}")
    return [
        EmailRecipient(addr=addr, name=name, kind=kind)
        for addr, name in _pair_names(addrs, names)
        if addr
    ]


def parse_header(header: str | None, kind: str) -> list[EmailRecipient]:
    """Build EmailRecipient rows from a raw header string.

    Used by IMAP pull where the raw 'To'/'Cc'/'Bcc' header text is all
    we have. Delegates to email.utils.getaddresses for RFC 5322 parsing.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid recipient kind: {kind!r}")
    if not header:
        return []
    recipients: list[EmailRecipient] = []
    for name, addr in email.utils.getaddresses([header]):
        if addr:
            recipients.append(EmailRecipient(addr=addr, name=name or None, kind=kind))
    return recipients


def backfill_recipients(session: Session) -> int:
    """Populate email_recipients for emails that don't have rows yet.

    Parses existing to_addrs/cc_addrs/bcc_addrs CSV columns and creates
    EmailRecipient rows. Idempotent: emails that already have recipients
    are skipped. Safe to re-run.

    Returns the number of new recipient rows created.
    """
    with_recipients = {
        row[0]
        for row in session.execute(select(EmailRecipient.email_id).distinct())
    }

    created = 0
    for email_obj in session.execute(select(Email)).scalars():
        if email_obj.id in with_recipients:
            continue
        for field_name, kind in (("to_addrs", "to"), ("cc_addrs", "cc"), ("bcc_addrs", "bcc")):
            value = getattr(email_obj, field_name)
            if not value:
                continue
            # Legacy CSV may be bare comma-joined addresses or full RFC 5322
            # 'Name <addr>' strings. email.utils.getaddresses handles both.
            for name, addr in email.utils.getaddresses([value]):
                if addr:
                    session.add(
                        EmailRecipient(
                            email_id=email_obj.id,
                            addr=addr,
                            name=name or None,
                            kind=kind,
                        )
                    )
                    created += 1
    return created
