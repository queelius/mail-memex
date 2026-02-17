"""Person resolution - merge multiple email addresses into unified persons.

Email archives often contain the same person under multiple addresses:
- work@company.com and personal@gmail.com
- old@provider.com that changed to new@provider.com
- name variations: john.smith@x.com and jsmith@x.com

This module provides heuristics and user controls for merging these
into unified Person records.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mtk.core.models import Email, Person, PersonEmail


@dataclass
class MergeCandidate:
    """A potential merge between two persons."""

    person1_id: int
    person2_id: int
    confidence: float  # 0.0 to 1.0
    reason: str
    emails1: list[str] = field(default_factory=list)
    emails2: list[str] = field(default_factory=list)


class PersonResolver:
    """Resolve email addresses to Person records.

    Handles:
    - Creating new persons from unknown addresses
    - Merging addresses when they appear together
    - Suggesting merges based on heuristics
    - User-controlled manual merges
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        # Cache of email -> person_id
        self._cache: dict[str, int] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Load email->person mapping into cache."""
        result = self.session.execute(select(PersonEmail.email, PersonEmail.person_id))
        for email, person_id in result:
            self._cache[email.lower()] = person_id

    def resolve(self, email: str, name: str | None = None) -> Person:
        """Resolve an email address to a Person.

        If the address is known, returns the existing Person.
        Otherwise creates a new Person.

        Args:
            email: The email address.
            name: Optional display name from the email header.

        Returns:
            The resolved Person record.
        """
        email_lower = email.lower()

        # Check cache first
        if email_lower in self._cache:
            person_id = self._cache[email_lower]
            person = self.session.get(Person, person_id)
            if person:
                # Update name if we have a better one
                if name and (not person.name or person.name == email_lower):
                    person.name = name
                return person

        # Create new person
        person = Person(
            name=name or self._extract_name_from_email(email_lower),
            primary_email=email_lower,
        )
        self.session.add(person)
        self.session.flush()  # Get the ID

        # Create email mapping
        person_email = PersonEmail(email=email_lower, person_id=person.id, is_primary=True)
        self.session.add(person_email)

        # Update cache
        self._cache[email_lower] = person.id

        return person

    def add_email_to_person(self, person: Person, email: str) -> None:
        """Add an additional email address to a person.

        Args:
            person: The Person to add the email to.
            email: The email address to add.
        """
        email_lower = email.lower()

        # Check if already exists
        if email_lower in self._cache:
            existing_person_id = self._cache[email_lower]
            if existing_person_id == person.id:
                return  # Already associated
            # Email belongs to different person - merge needed
            raise ValueError(
                f"Email {email} already belongs to person {existing_person_id}. "
                "Use merge_persons() instead."
            )

        # Add the mapping
        person_email = PersonEmail(email=email_lower, person_id=person.id, is_primary=False)
        self.session.add(person_email)
        self._cache[email_lower] = person.id

    def merge_persons(self, keep: Person, merge: Person) -> Person:
        """Merge two Person records into one.

        All emails from `merge` are transferred to `keep`, and `merge` is deleted.

        Args:
            keep: The Person to keep.
            merge: The Person to merge into keep.

        Returns:
            The kept Person.
        """
        if keep.id == merge.id:
            return keep

        # Transfer all emails
        for person_email in merge.email_addresses:
            person_email.person_id = keep.id
            person_email.is_primary = False
            self._cache[person_email.email] = keep.id

        # Update sent_emails foreign keys
        for email in merge.sent_emails:
            email.sender_id = keep.id

        # Update statistics
        keep.email_count += merge.email_count
        if merge.first_contact and (
            not keep.first_contact or merge.first_contact < keep.first_contact
        ):
            keep.first_contact = merge.first_contact
        if merge.last_contact and (not keep.last_contact or merge.last_contact > keep.last_contact):
            keep.last_contact = merge.last_contact

        # Keep the better name (longer, not email-based)
        if (
            merge.name
            and len(merge.name) > len(keep.name or "")
            and "@" not in merge.name
            and "@" in (keep.name or "")
        ):
            keep.name = merge.name

        # Merge notes
        if merge.notes:
            keep.notes = f"{keep.notes or ''}\n\n{merge.notes}".strip()

        # Delete the merged person
        self.session.delete(merge)

        return keep

    def find_merge_candidates(self, min_confidence: float = 0.5) -> list[MergeCandidate]:
        """Find potential person merges using heuristics.

        Heuristics used:
        - Same domain with similar local parts
        - Names that match across different emails
        - Emails that always appear together in conversations

        Args:
            min_confidence: Minimum confidence score (0-1) for suggestions.

        Returns:
            List of MergeCandidate suggestions.
        """
        candidates = []

        # Get all persons with their emails
        persons = self.session.execute(select(Person).where(Person.email_count > 0)).scalars().all()

        person_emails: dict[int, list[str]] = defaultdict(list)
        for person in persons:
            for pe in person.email_addresses:
                person_emails[person.id].append(pe.email)

        # Check each pair
        person_list = list(persons)
        for i, p1 in enumerate(person_list):
            for p2 in person_list[i + 1 :]:
                confidence, reason = self._check_merge(
                    p1, p2, person_emails[p1.id], person_emails[p2.id]
                )
                if confidence >= min_confidence:
                    candidates.append(
                        MergeCandidate(
                            person1_id=p1.id,
                            person2_id=p2.id,
                            confidence=confidence,
                            reason=reason,
                            emails1=person_emails[p1.id],
                            emails2=person_emails[p2.id],
                        )
                    )

        # Sort by confidence
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates

    def _check_merge(
        self,
        p1: Person,
        p2: Person,
        emails1: list[str],
        emails2: list[str],
    ) -> tuple[float, str]:
        """Check if two persons might be the same.

        Returns (confidence, reason).
        """
        # Same name (exact match)
        if p1.name and p2.name and p1.name.lower() == p2.name.lower():
            return 0.8, "Identical names"

        # Similar names (fuzzy)
        if p1.name and p2.name:
            name_sim = self._name_similarity(p1.name, p2.name)
            if name_sim > 0.8:
                return 0.7, f"Similar names ({name_sim:.0%})"

        # Same domain with similar local parts
        for e1 in emails1:
            for e2 in emails2:
                local1, domain1 = e1.split("@") if "@" in e1 else (e1, "")
                local2, domain2 = e2.split("@") if "@" in e2 else (e2, "")

                if domain1 and domain1 == domain2:
                    local_sim = self._local_part_similarity(local1, local2)
                    if local_sim > 0.7:
                        return 0.6, f"Same domain, similar addresses ({local_sim:.0%})"

        return 0.0, ""

    def _name_similarity(self, name1: str, name2: str) -> float:
        """Compute similarity between two names."""
        # Normalize
        n1 = name1.lower().split()
        n2 = name2.lower().split()

        if not n1 or not n2:
            return 0.0

        # Check for common parts
        common = set(n1) & set(n2)
        total = set(n1) | set(n2)

        return len(common) / len(total) if total else 0.0

    def _local_part_similarity(self, local1: str, local2: str) -> float:
        """Compute similarity between email local parts."""
        # Normalize (remove dots, underscores)
        l1 = re.sub(r"[._-]", "", local1.lower())
        l2 = re.sub(r"[._-]", "", local2.lower())

        if l1 == l2:
            return 1.0

        # Check if one contains the other
        if l1 in l2 or l2 in l1:
            return 0.8

        # Simple character overlap
        common = set(l1) & set(l2)
        total = set(l1) | set(l2)
        return len(common) / len(total) if total else 0.0

    def _extract_name_from_email(self, email: str) -> str:
        """Extract a readable name from an email address."""
        if "@" not in email:
            return email

        local = email.split("@")[0]
        # Replace separators with spaces
        name = re.sub(r"[._-]+", " ", local)
        # Title case
        return name.title()

    def update_person_stats(self, person: Person) -> None:
        """Update statistics for a person from their emails."""
        # Get all emails from this person
        result = self.session.execute(
            select(func.count(Email.id), func.min(Email.date), func.max(Email.date)).where(
                Email.sender_id == person.id
            )
        ).one()

        person.email_count = result[0] or 0
        person.first_contact = result[1]
        person.last_contact = result[2]

    def update_all_stats(self) -> None:
        """Update statistics for all persons."""
        persons = self.session.execute(select(Person)).scalars().all()
        for person in persons:
            self.update_person_stats(person)
