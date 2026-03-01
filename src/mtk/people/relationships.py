"""Relationship analysis for email correspondence.

Analyzes communication patterns to understand:
- Who you correspond with most
- Temporal patterns (frequency over time)
- Topics discussed with each person
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mtk.core.models import Email, Person, PersonEmail


@dataclass
class CorrespondenceStats:
    """Statistics about correspondence with a person."""

    person_id: int
    person_name: str
    primary_email: str

    # Volume
    total_emails: int = 0
    sent_count: int = 0  # Emails sent by them
    received_count: int = 0  # Emails sent to them

    # Timing
    first_email: datetime | None = None
    last_email: datetime | None = None
    avg_response_time: timedelta | None = None

    # Threads
    thread_count: int = 0
    avg_thread_length: float = 0.0

    # Derived
    relationship_type: str | None = None
    common_topics: list[str] = field(default_factory=list)


class RelationshipAnalyzer:
    """Analyze email correspondence relationships."""

    def __init__(self, session: Session, owner_email: str | None = None) -> None:
        """Initialize the analyzer.

        Args:
            session: Database session.
            owner_email: Email address of the archive owner (for sent/received).
        """
        self.session = session
        self.owner_email = owner_email.lower() if owner_email else None
        self._owner_id: int | None = None

    @property
    def owner_id(self) -> int | None:
        """Get the person ID of the owner."""
        if self._owner_id is None and self.owner_email:
            result = self.session.execute(
                select(PersonEmail.person_id).where(PersonEmail.email == self.owner_email)
            ).scalar()
            self._owner_id = result
        return self._owner_id

    def get_top_correspondents(
        self,
        limit: int = 20,
        since: datetime | None = None,
    ) -> list[CorrespondenceStats]:
        """Get the top correspondents by email count.

        Args:
            limit: Maximum number of correspondents to return.
            since: Only count emails after this date.

        Returns:
            List of CorrespondenceStats, sorted by total_emails descending.
        """
        # Query persons with email counts
        query = (
            select(
                Person.id,
                Person.name,
                Person.primary_email,
                func.count(Email.id).label("email_count"),
            )
            .join(Email, Email.sender_id == Person.id)
            .group_by(Person.id)
            .order_by(func.count(Email.id).desc())
            .limit(limit)
        )

        if since:
            query = query.where(Email.date >= since)

        results = []
        for row in self.session.execute(query):
            stats = CorrespondenceStats(
                person_id=row.id,
                person_name=row.name,
                primary_email=row.primary_email or "",
                sent_count=row.email_count,  # Emails sent by them
                total_emails=row.email_count,
            )
            results.append(stats)

        return results

    def get_correspondent_stats(self, person_id: int) -> CorrespondenceStats | None:
        """Get detailed statistics for a specific correspondent.

        Args:
            person_id: The person ID to analyze.

        Returns:
            CorrespondenceStats or None if person not found.
        """
        person = self.session.get(Person, person_id)
        if not person:
            return None

        # Get email counts and dates
        result = self.session.execute(
            select(
                func.count(Email.id),
                func.min(Email.date),
                func.max(Email.date),
            ).where(Email.sender_id == person_id)
        ).one()

        sent_count = result[0] or 0
        first_email = result[1]
        last_email = result[2]

        # Get thread count
        thread_result = self.session.execute(
            select(func.count(func.distinct(Email.thread_id))).where(Email.sender_id == person_id)
        ).scalar()

        return CorrespondenceStats(
            person_id=person_id,
            person_name=person.name,
            primary_email=person.primary_email or "",
            total_emails=sent_count,
            sent_count=sent_count,
            first_email=first_email,
            last_email=last_email,
            thread_count=thread_result or 0,
            relationship_type=person.relationship_type,
        )

    def get_correspondence_timeline(
        self,
        person_id: int,
        granularity: str = "month",
    ) -> dict[str, int]:
        """Get email count over time for a correspondent.

        Args:
            person_id: The person to analyze.
            granularity: "day", "week", "month", or "year".

        Returns:
            Dict mapping time periods to email counts.
        """
        emails = (
            self.session.execute(
                select(Email.date).where(Email.sender_id == person_id).order_by(Email.date)
            )
            .scalars()
            .all()
        )

        timeline: dict[str, int] = defaultdict(int)

        for date in emails:
            if date is None:
                continue

            if granularity == "day":
                key = date.strftime("%Y-%m-%d")
            elif granularity == "week":
                key = date.strftime("%Y-W%W")
            elif granularity == "month":
                key = date.strftime("%Y-%m")
            else:  # year
                key = date.strftime("%Y")

            timeline[key] += 1

        return dict(sorted(timeline.items()))
