"""Search engine for email archive.

Provides multiple search modes:
- Keyword search (SQLite LIKE pattern matching)
- FTS5 ranked text search (when available)
- Field-specific search (from, to, subject, date ranges)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from mail_memex.core.models import Email, Tag, email_tags


@dataclass
class SearchResult:
    """A search result with relevance information."""

    email: Email
    score: float = 1.0
    match_type: str = "keyword"  # "keyword" or "fts5"
    highlights: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class SearchQuery:
    """A parsed search query with various filters."""

    # Free text (searches subject + body)
    text: str | None = None

    # Field-specific filters
    from_addr: str | None = None
    to_addr: str | None = None
    subject: str | None = None

    # Date range
    date_from: datetime | None = None
    date_to: datetime | None = None

    # Tags
    has_tags: list[str] = field(default_factory=list)
    not_tags: list[str] = field(default_factory=list)

    # Attachments
    has_attachment: bool | None = None

    # Thread
    thread_id: str | None = None


class SearchEngine:
    """Search engine for email archive.

    Supports multiple search modes and query types.
    Uses FTS5 for fast ranked text search when available,
    with LIKE fallback for databases without FTS5.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._fts5_checked = False
        self._fts5_ok = False

    def _has_fts5(self) -> bool:
        """Check if FTS5 is available for this session's database."""
        if not self._fts5_checked:
            from mail_memex.search.fts import fts5_available

            engine = self.session.get_bind()
            self._fts5_ok = fts5_available(engine)
            self._fts5_checked = True
        return self._fts5_ok

    def search(
        self,
        query: str | SearchQuery,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Literal["date", "relevance"] = "relevance",
    ) -> list[SearchResult]:
        """Search for emails matching the query.

        Args:
            query: Search query (string or SearchQuery object).
            limit: Maximum results to return.
            offset: Number of results to skip.
            order_by: Sort order - "date" (newest first) or "relevance".

        Returns:
            List of SearchResult objects.
        """
        if isinstance(query, str):
            query = self.parse_query(query)

        if query.text and self._has_fts5() and order_by == "relevance":
            return self._fts5_search(query, limit, offset)
        else:
            return self._like_search(query, limit, offset, order_by)

    def parse_query(self, query_str: str) -> SearchQuery:
        """Parse a query string into a SearchQuery object.

        Supports Gmail-like operators:
        - from:address
        - to:address
        - subject:text
        - after:YYYY-MM-DD
        - before:YYYY-MM-DD
        - has:attachment
        - tag:tagname
        - -tag:tagname (exclude)
        - thread:id
        - Remaining text is free-text search

        Args:
            query_str: The query string.

        Returns:
            Parsed SearchQuery object.
        """
        query = SearchQuery()
        remaining_parts = []

        # Tokenize while preserving quoted strings
        tokens = self._tokenize_query(query_str)

        for token in tokens:
            if ":" in token:
                operator, value = token.split(":", 1)
                operator = operator.lower()

                if operator == "from":
                    query.from_addr = value
                elif operator == "to":
                    query.to_addr = value
                elif operator == "subject":
                    query.subject = value
                elif operator == "after":
                    query.date_from = self._parse_date(value)
                elif operator == "before":
                    query.date_to = self._parse_date(value)
                elif operator == "has" and value.lower() == "attachment":
                    query.has_attachment = True
                elif operator == "tag":
                    query.has_tags.append(value)
                elif operator == "-tag":
                    query.not_tags.append(value)
                elif operator == "thread":
                    query.thread_id = value
                else:
                    # Unknown operator, treat as text
                    remaining_parts.append(token)
            else:
                remaining_parts.append(token)

        if remaining_parts:
            query.text = " ".join(remaining_parts)

        return query

    def _tokenize_query(self, query_str: str) -> list[str]:
        """Tokenize query preserving quoted strings."""
        tokens = []
        current = ""
        in_quotes = False

        for char in query_str:
            if char == '"':
                in_quotes = not in_quotes
            elif char == " " and not in_quotes:
                if current:
                    tokens.append(current)
                    current = ""
            else:
                current += char

        if current:
            tokens.append(current)

        return tokens

    def _parse_date(self, date_str: str) -> datetime | None:
        """Parse a date string."""
        formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    def _filter_conditions(self, query: SearchQuery) -> list:
        """Build SQLAlchemy field-filter conditions from a SearchQuery.

        Covers everything except free-text (which FTS5 and LIKE handle
        differently). Always filters out soft-deleted emails.
        """
        from mail_memex.core.models import Attachment

        conditions: list = [Email.archived_at.is_(None)]

        if query.from_addr:
            conditions.append(Email.from_addr.ilike(f"%{query.from_addr}%"))
        if query.to_addr:
            conditions.append(Email.to_addrs.ilike(f"%{query.to_addr}%"))
        if query.subject:
            conditions.append(Email.subject.ilike(f"%{query.subject}%"))
        if query.date_from:
            conditions.append(Email.date >= query.date_from)
        if query.date_to:
            conditions.append(Email.date <= query.date_to)
        if query.thread_id:
            conditions.append(Email.thread_id == query.thread_id)
        if query.has_attachment:
            conditions.append(Email.id.in_(select(Attachment.email_id).distinct()))
        for tag_name in query.has_tags:
            tag_subq = (
                select(email_tags.c.email_id)
                .join(Tag, Tag.id == email_tags.c.tag_id)
                .where(Tag.name == tag_name)
            )
            conditions.append(Email.id.in_(tag_subq))
        for tag_name in query.not_tags:
            tag_subq = (
                select(email_tags.c.email_id)
                .join(Tag, Tag.id == email_tags.c.tag_id)
                .where(Tag.name == tag_name)
            )
            conditions.append(Email.id.notin_(tag_subq))

        return conditions

    def _fts5_search(
        self,
        query: SearchQuery,
        limit: int,
        offset: int,
    ) -> list[SearchResult]:
        """Perform FTS5 search with BM25 ranking.

        Uses FTS5 for text matching and SQLAlchemy for field filters.
        Falls back to LIKE search if FTS5 query fails.
        """
        from mail_memex.search.fts import fts5_search, prepare_fts_query

        fts_query = prepare_fts_query(query.text or "")
        if not fts_query:
            return self._like_search(query, limit, offset, "date")

        # Get FTS5 results (may return more than needed since we filter after)
        fts_results = fts5_search(self.session, fts_query, limit=limit * 3, offset=0)
        if not fts_results:
            # FTS5 query failed or no results — fall back to LIKE
            return self._like_search(query, limit, offset, "relevance")

        fts_email_ids = [r["email_id"] for r in fts_results]
        fts_lookup = {r["email_id"]: r for r in fts_results}

        conditions = [Email.id.in_(fts_email_ids), *self._filter_conditions(query)]
        emails = self.session.execute(select(Email).where(and_(*conditions))).scalars().all()

        # Build results sorted by FTS5 rank (lower = better match)
        results = []
        for email_obj in emails:
            fts_data = fts_lookup.get(email_obj.id)
            if not fts_data:
                continue

            # Convert BM25 rank (negative, lower=better) to a 0-1 score
            raw_rank = fts_data["rank"]
            score = 1.0 / (1.0 - raw_rank) if raw_rank < 0 else 1.0

            highlights: dict[str, list[str]] = {"subject": [], "body": []}
            if fts_data.get("snippet_subject"):
                highlights["subject"].append(fts_data["snippet_subject"])
            if fts_data.get("snippet_body"):
                highlights["body"].append(fts_data["snippet_body"])

            results.append(
                SearchResult(
                    email=email_obj,
                    score=score,
                    match_type="fts5",
                    highlights=highlights,
                )
            )

        # Sort by score descending (highest relevance first)
        results.sort(key=lambda r: r.score, reverse=True)

        return results[offset : offset + limit]

    def _like_search(
        self,
        query: SearchQuery,
        limit: int,
        offset: int,
        order_by: str,
    ) -> list[SearchResult]:
        """Perform keyword-based search using SQLite LIKE (fallback)."""
        conditions = self._filter_conditions(query)

        if query.text:
            text_pattern = f"%{query.text}%"
            conditions.append(
                or_(
                    Email.subject.ilike(text_pattern),
                    Email.body_text.ilike(text_pattern),
                    Email.body_preview.ilike(text_pattern),
                )
            )

        stmt = (
            select(Email)
            .where(and_(*conditions))
            .order_by(Email.date.desc())
            .limit(limit)
            .offset(offset)
        )
        emails = self.session.execute(stmt).scalars().all()

        # Build results
        results = []
        for email in emails:
            result = SearchResult(
                email=email,
                score=1.0,
                match_type="keyword",
            )

            # Add highlights for text matches
            if query.text:
                highlights = self._extract_highlights(email, query.text)
                result.highlights = highlights

            results.append(result)

        return results

    def _extract_highlights(self, email: Email, query_text: str) -> dict[str, list[str]]:
        """Extract highlighted snippets from email matching query text."""
        highlights: dict[str, list[str]] = {"subject": [], "body": []}

        # Simple case-insensitive matching
        pattern = re.compile(re.escape(query_text), re.IGNORECASE)

        # Subject highlights
        if email.subject and pattern.search(email.subject):
            highlights["subject"].append(email.subject)

        # Body highlights (extract context around matches)
        if email.body_text:
            for match in pattern.finditer(email.body_text):
                start = max(0, match.start() - 50)
                end = min(len(email.body_text), match.end() + 50)
                snippet = email.body_text[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(email.body_text):
                    snippet = snippet + "..."
                highlights["body"].append(snippet)
                if len(highlights["body"]) >= 3:
                    break

        return highlights
