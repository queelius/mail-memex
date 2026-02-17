"""MCP tool handlers for mtk.

Each handler takes arguments dict and returns list of TextContent.
Designed for independent testability without MCP transport layer.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from mtk.core.models import (
    Email,
    Person,
    Tag,
    Thread,
    email_tags,
)
from mtk.mcp.validation import (
    optional_int,
    optional_list,
    optional_str,
    require_str,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _text(content: str) -> dict:
    """Create a text content dict (converted to TextContent by server layer)."""
    return {"type": "text", "text": content}


def _json_text(data: Any) -> dict:
    """Create a text content dict from JSON-serializable data."""
    return _text(json.dumps(data, indent=2, default=str))


def _email_to_dict(email: Email) -> dict:
    """Convert Email ORM to serializable dict."""
    return {
        "message_id": email.message_id,
        "from_addr": email.from_addr,
        "from_name": email.from_name,
        "subject": email.subject,
        "date": email.date.isoformat() if email.date else None,
        "body_preview": email.body_preview,
        "thread_id": email.thread_id,
        "tags": [t.name for t in email.tags] if email.tags else [],
    }


def _email_full_dict(email: Email) -> dict:
    """Convert Email ORM to full serializable dict with body."""
    data = _email_to_dict(email)
    data["body_text"] = email.body_text
    data["in_reply_to"] = email.in_reply_to
    data["references"] = email.references
    data["attachments"] = (
        [
            {"filename": a.filename, "type": a.content_type, "size": a.size}
            for a in email.attachments
        ]
        if email.attachments
        else []
    )
    return data


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def search_emails(session: Session, arguments: dict) -> list[dict]:
    """Search emails using query string with operators."""
    from mtk.search.engine import SearchEngine

    query = require_str(arguments, "query")
    limit = optional_int(arguments, "limit", 20)

    engine = SearchEngine(session)
    results = engine.search(query, limit=limit)

    data = [
        {
            **_email_to_dict(r.email),
            "score": r.score,
            "match_type": r.match_type,
        }
        for r in results
    ]
    return [_json_text(data)]


def get_inbox(session: Session, arguments: dict) -> list[dict]:
    """Get recent emails (inbox view)."""
    limit = optional_int(arguments, "limit", 20)
    since = optional_str(arguments, "since")

    stmt = select(Email).order_by(Email.date.desc()).limit(limit)
    if since:
        try:
            since_date = datetime.strptime(since, "%Y-%m-%d")
            stmt = stmt.where(Email.date >= since_date)
        except ValueError:
            pass

    emails = list(session.execute(stmt).scalars())
    data = [_email_to_dict(e) for e in emails]
    return [_json_text(data)]


def get_stats(session: Session, arguments: dict) -> list[dict]:
    """Get archive statistics."""
    from mtk.core.models import Attachment as Att

    email_count = session.execute(select(func.count(Email.id))).scalar() or 0
    person_count = session.execute(select(func.count(Person.id))).scalar() or 0
    thread_count = session.execute(select(func.count(Thread.id))).scalar() or 0
    tag_count = session.execute(select(func.count(Tag.id))).scalar() or 0
    attachment_count = session.execute(select(func.count(Att.id))).scalar() or 0

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
    return [_json_text(data)]


def show_email(session: Session, arguments: dict) -> list[dict]:
    """Show a single email by message_id."""
    message_id = require_str(arguments, "message_id")

    email = session.execute(select(Email).where(Email.message_id == message_id)).scalar()

    if not email:
        email = session.execute(
            select(Email).where(Email.message_id.contains(message_id)).limit(1)
        ).scalar()

    if not email:
        return [_text(f"Email not found: {message_id}")]

    return [_json_text(_email_full_dict(email))]


def show_thread(session: Session, arguments: dict) -> list[dict]:
    """Show full thread conversation."""
    thread_id = require_str(arguments, "thread_id")

    # Try as thread_id first, then as message_id
    emails = list(
        session.execute(
            select(Email).where(Email.thread_id == thread_id).order_by(Email.date)
        ).scalars()
    )

    if not emails:
        email = session.execute(select(Email).where(Email.message_id == thread_id)).scalar()
        if email and email.thread_id:
            emails = list(
                session.execute(
                    select(Email).where(Email.thread_id == email.thread_id).order_by(Email.date)
                ).scalars()
            )

    if not emails:
        return [_text(f"Thread not found: {thread_id}")]

    data = {
        "thread_id": emails[0].thread_id,
        "message_count": len(emails),
        "messages": [_email_full_dict(e) for e in emails],
    }
    return [_json_text(data)]


def get_reply_context(session: Session, arguments: dict) -> list[dict]:
    """Get context for composing a reply."""
    message_id = require_str(arguments, "message_id")

    email = session.execute(
        select(Email).where(Email.message_id.contains(message_id)).limit(1)
    ).scalar()

    if not email:
        return [_text(f"Email not found: {message_id}")]

    # Thread history
    thread_history = []
    if email.thread_id:
        thread_history = list(
            session.execute(
                select(Email)
                .where(Email.thread_id == email.thread_id)
                .where(Email.date < email.date)
                .order_by(Email.date.desc())
                .limit(5)
            ).scalars()
        )
        thread_history.reverse()

    subject = email.subject or ""
    if not subject.startswith("Re:"):
        subject = f"Re: {subject}"

    data = {
        "replying_to": _email_full_dict(email),
        "suggested_headers": {
            "to": email.from_addr,
            "subject": subject,
            "in_reply_to": email.message_id,
            "references": f"{email.references or ''} {email.message_id}".strip(),
        },
        "thread_history": [
            {
                "from": e.from_addr,
                "from_name": e.from_name,
                "date": e.date.isoformat() if e.date else None,
                "body": e.body_text,
            }
            for e in thread_history
        ],
    }
    return [_json_text(data)]


def tag_email(session: Session, arguments: dict) -> list[dict]:
    """Add or remove tags from a single email."""
    message_id = require_str(arguments, "message_id")
    add_tags = optional_list(arguments, "add")
    remove_tags = optional_list(arguments, "remove")

    email = session.execute(select(Email).where(Email.message_id.contains(message_id))).scalar()

    if not email:
        return [_text(f"Email not found: {message_id}")]

    for tag_name in add_tags:
        tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
        if not tag:
            tag = Tag(name=tag_name, source="mtk")
            session.add(tag)
            session.flush()
        if tag not in email.tags:
            email.tags.append(tag)

    for tag_name in remove_tags:
        tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
        if tag and tag in email.tags:
            email.tags.remove(tag)

    session.commit()

    return [
        _json_text(
            {
                "message_id": email.message_id,
                "tags": [t.name for t in email.tags],
            }
        )
    ]


def tag_batch(session: Session, arguments: dict) -> list[dict]:
    """Add or remove tags from multiple emails matching a query."""
    from mtk.search.engine import SearchEngine

    query = require_str(arguments, "query")
    add_tags = optional_list(arguments, "add")
    remove_tags = optional_list(arguments, "remove")

    engine = SearchEngine(session)
    results = engine.search(query, limit=1000)

    if not results:
        return [_json_text({"matched": 0, "modified": 0})]

    modified = 0
    for r in results:
        changed = False
        for tag_name in add_tags:
            tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
            if not tag:
                tag = Tag(name=tag_name, source="mtk")
                session.add(tag)
                session.flush()
            if tag not in r.email.tags:
                r.email.tags.append(tag)
                changed = True

        for tag_name in remove_tags:
            tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar()
            if tag and tag in r.email.tags:
                r.email.tags.remove(tag)
                changed = True

        if changed:
            modified += 1

    session.commit()

    return [
        _json_text(
            {
                "matched": len(results),
                "modified": modified,
                "add_tags": add_tags,
                "remove_tags": remove_tags,
            }
        )
    ]


def list_tags(session: Session, arguments: dict) -> list[dict]:
    """List all tags with email counts."""
    stmt = (
        select(Tag.name, func.count(email_tags.c.email_id).label("count"))
        .outerjoin(email_tags, Tag.id == email_tags.c.tag_id)
        .group_by(Tag.id)
        .order_by(func.count(email_tags.c.email_id).desc())
    )
    rows = session.execute(stmt).all()
    data = [{"name": name, "count": count} for name, count in rows]
    return [_json_text(data)]


def list_people(session: Session, arguments: dict) -> list[dict]:
    """List top correspondents."""
    from mtk.people.relationships import RelationshipAnalyzer

    limit = optional_int(arguments, "limit", 20)
    analyzer = RelationshipAnalyzer(session)
    stats = analyzer.get_top_correspondents(limit=limit)

    data = [
        {
            "id": s.person_id,
            "name": s.person_name,
            "email": s.primary_email,
            "email_count": s.total_emails,
        }
        for s in stats
    ]
    return [_json_text(data)]


def show_person(session: Session, arguments: dict) -> list[dict]:
    """Show details for a specific person."""
    from mtk.people.relationships import RelationshipAnalyzer

    person_id = optional_int(arguments, "person_id", 0)
    if not person_id:
        return [_text("Missing required argument: person_id")]

    analyzer = RelationshipAnalyzer(session)
    stats = analyzer.get_correspondent_stats(person_id)

    if not stats:
        return [_text(f"Person not found: {person_id}")]

    data = {
        "id": stats.person_id,
        "name": stats.person_name,
        "email": stats.primary_email,
        "email_count": stats.total_emails,
        "first_email": stats.first_email.isoformat() if stats.first_email else None,
        "last_email": stats.last_email.isoformat() if stats.last_email else None,
        "thread_count": stats.thread_count,
        "relationship_type": stats.relationship_type,
    }
    return [_json_text(data)]


def get_correspondence_timeline(session: Session, arguments: dict) -> list[dict]:
    """Get email count over time for a correspondent."""
    from mtk.people.relationships import RelationshipAnalyzer

    person_id = optional_int(arguments, "person_id", 0)
    granularity = optional_str(arguments, "granularity", "month") or "month"

    if not person_id:
        return [_text("Missing required argument: person_id")]

    analyzer = RelationshipAnalyzer(session)
    timeline = analyzer.get_correspondence_timeline(person_id, granularity=granularity)

    return [_json_text(timeline)]


def notmuch_sync(session: Session, arguments: dict) -> list[dict]:
    """Run notmuch sync operation."""
    try:
        from mtk.integrations import NotmuchSync
    except ImportError:
        return [_text("notmuch integration not available. Install with: pip install mtk[notmuch]")]

    action = optional_str(arguments, "action", "status") or "status"

    sync = NotmuchSync(session)

    if action == "status":
        status = sync.status()
        return [_json_text(status)]
    elif action == "pull":
        result = sync.pull()
        return [_json_text(result.to_dict())]
    elif action == "push":
        result = sync.push()
        return [_json_text(result.to_dict())]
    elif action == "sync":
        strategy = optional_str(arguments, "strategy", "merge") or "merge"
        result = sync.sync(strategy=strategy)
        return [_json_text(result.to_dict())]
    else:
        return [_text(f"Unknown action: {action}. Use: status, pull, push, sync")]


# ---------------------------------------------------------------------------
# Tool dispatch registry
# ---------------------------------------------------------------------------

TOOL_HANDLERS: dict[str, Any] = {
    "search_emails": search_emails,
    "get_inbox": get_inbox,
    "get_stats": get_stats,
    "show_email": show_email,
    "show_thread": show_thread,
    "get_reply_context": get_reply_context,
    "tag_email": tag_email,
    "tag_batch": tag_batch,
    "list_tags": list_tags,
    "list_people": list_people,
    "show_person": show_person,
    "get_correspondence_timeline": get_correspondence_timeline,
    "notmuch_sync": notmuch_sync,
}
