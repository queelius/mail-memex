"""MCP resource handlers for mtk.

Resources expose email data via mtk:// URI scheme.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from mtk.core.models import Email, Person, Thread

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def read_email_resource(session: Session, email_id: str) -> str | None:
    """Read a single email resource.

    URI: mtk://email/{message_id}
    """
    email = session.execute(select(Email).where(Email.message_id == email_id)).scalar()

    if not email:
        email = session.execute(
            select(Email).where(Email.message_id.contains(email_id)).limit(1)
        ).scalar()

    if not email:
        return None

    data = {
        "message_id": email.message_id,
        "from_addr": email.from_addr,
        "from_name": email.from_name,
        "subject": email.subject,
        "date": email.date.isoformat() if email.date else None,
        "body_text": email.body_text,
        "thread_id": email.thread_id,
        "tags": [t.name for t in email.tags] if email.tags else [],
    }
    return json.dumps(data, indent=2, default=str)


def read_thread_resource(session: Session, thread_id: str) -> str | None:
    """Read a thread resource.

    URI: mtk://thread/{thread_id}
    """
    emails = list(
        session.execute(
            select(Email).where(Email.thread_id == thread_id).order_by(Email.date)
        ).scalars()
    )

    if not emails:
        return None

    data = {
        "thread_id": thread_id,
        "message_count": len(emails),
        "messages": [
            {
                "message_id": e.message_id,
                "from_addr": e.from_addr,
                "date": e.date.isoformat() if e.date else None,
                "subject": e.subject,
                "body_text": e.body_text,
            }
            for e in emails
        ],
    }
    return json.dumps(data, indent=2, default=str)


def read_person_resource(session: Session, person_id: str) -> str | None:
    """Read a person resource.

    URI: mtk://person/{person_id}
    """
    try:
        pid = int(person_id)
    except ValueError:
        return None

    person = session.get(Person, pid)
    if not person:
        return None

    data = {
        "id": person.id,
        "name": person.name,
        "primary_email": person.primary_email,
        "relationship_type": person.relationship_type,
        "email_count": person.email_count,
        "first_contact": person.first_contact.isoformat() if person.first_contact else None,
        "last_contact": person.last_contact.isoformat() if person.last_contact else None,
    }
    return json.dumps(data, indent=2, default=str)


def read_stats_resource(session: Session) -> str:
    """Read archive stats resource.

    URI: mtk://stats
    """
    from mtk.core.models import Attachment, Tag

    email_count = session.execute(select(func.count(Email.id))).scalar() or 0
    person_count = session.execute(select(func.count(Person.id))).scalar() or 0
    thread_count = session.execute(select(func.count(Thread.id))).scalar() or 0
    tag_count = session.execute(select(func.count(Tag.id))).scalar() or 0
    attachment_count = session.execute(select(func.count(Attachment.id))).scalar() or 0

    date_result = session.execute(select(func.min(Email.date), func.max(Email.date))).one()

    data = {
        "emails": email_count,
        "people": person_count,
        "threads": thread_count,
        "tags": tag_count,
        "attachments": attachment_count,
        "date_from": date_result[0].isoformat() if date_result[0] else None,
        "date_to": date_result[1].isoformat() if date_result[1] else None,
    }
    return json.dumps(data, indent=2, default=str)


# Resource dispatch by URI prefix
RESOURCE_HANDLERS = {
    "email": read_email_resource,
    "thread": read_thread_resource,
    "person": read_person_resource,
}
