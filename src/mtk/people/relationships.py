"""Relationship analysis for email correspondence.

Analyzes communication patterns to understand:
- Who you correspond with most
- Temporal patterns (frequency over time)
- Topics discussed with each person
- Network structure (who connects to whom)
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterator

from sqlalchemy import func, select, and_, or_
from sqlalchemy.orm import Session

from mtk.core.models import Email, Person, PersonEmail, Thread


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


@dataclass
class NetworkEdge:
    """An edge in the correspondence network."""

    source_id: int
    target_id: int
    weight: int  # Number of emails exchanged
    emails: list[str] = field(default_factory=list)  # Sample message IDs


@dataclass
class NetworkNode:
    """A node in the correspondence network."""

    person_id: int
    name: str
    email: str
    email_count: int
    degree: int = 0  # Number of connections
    in_degree: int = 0
    out_degree: int = 0


class RelationshipAnalyzer:
    """Analyze relationships and build correspondence network."""

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
            select(func.count(func.distinct(Email.thread_id))).where(
                Email.sender_id == person_id
            )
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
        emails = self.session.execute(
            select(Email.date).where(Email.sender_id == person_id).order_by(Email.date)
        ).scalars().all()

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

    def build_network(
        self,
        min_emails: int = 2,
        since: datetime | None = None,
    ) -> tuple[list[NetworkNode], list[NetworkEdge]]:
        """Build a correspondence network graph.

        Nodes are people, edges represent email exchanges.

        Args:
            min_emails: Minimum emails to include in network.
            since: Only include emails after this date.

        Returns:
            Tuple of (nodes, edges).
        """
        # Get all email exchanges (sender -> recipients via threads)
        edge_counts: dict[tuple[int, int], int] = defaultdict(int)
        node_info: dict[int, dict] = {}

        # Get all persons with their email counts
        persons_query = select(Person).where(Person.email_count >= min_emails)
        for person in self.session.execute(persons_query).scalars():
            node_info[person.id] = {
                "name": person.name,
                "email": person.primary_email or "",
                "email_count": person.email_count,
            }

        # Build edges from thread participation
        # Two people are connected if they appear in the same thread
        threads_query = select(Thread.id, Thread.thread_id)
        for thread_row in self.session.execute(threads_query):
            thread_id = thread_row.thread_id

            # Get all participants in this thread
            participants = self.session.execute(
                select(func.distinct(Email.sender_id))
                .where(Email.thread_id == thread_id)
                .where(Email.sender_id.isnot(None))
            ).scalars().all()

            # Add edges between all pairs
            participants_list = list(participants)
            for i, p1 in enumerate(participants_list):
                for p2 in participants_list[i + 1 :]:
                    if p1 in node_info and p2 in node_info:
                        # Use sorted tuple for undirected edges
                        edge_key = tuple(sorted([p1, p2]))
                        edge_counts[edge_key] += 1

        # Build node and edge lists
        nodes = []
        for person_id, info in node_info.items():
            nodes.append(
                NetworkNode(
                    person_id=person_id,
                    name=info["name"],
                    email=info["email"],
                    email_count=info["email_count"],
                )
            )

        edges = []
        for (source, target), weight in edge_counts.items():
            if weight >= min_emails:
                edges.append(NetworkEdge(source_id=source, target_id=target, weight=weight))

        # Compute degrees
        degree_count: dict[int, int] = defaultdict(int)
        for edge in edges:
            degree_count[edge.source_id] += 1
            degree_count[edge.target_id] += 1

        for node in nodes:
            node.degree = degree_count.get(node.person_id, 0)

        return nodes, edges

    def export_network_gexf(
        self,
        nodes: list[NetworkNode],
        edges: list[NetworkEdge],
    ) -> str:
        """Export network to GEXF format (for Gephi).

        Args:
            nodes: Network nodes.
            edges: Network edges.

        Returns:
            GEXF XML string.
        """
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<gexf xmlns="http://gexf.net/1.3" version="1.3">',
            '  <meta lastmodifieddate="' + datetime.now().isoformat() + '">',
            "    <creator>mtk</creator>",
            "    <description>Email correspondence network</description>",
            "  </meta>",
            '  <graph mode="static" defaultedgetype="undirected">',
            "    <attributes class=\"node\">",
            '      <attribute id="0" title="email" type="string"/>',
            '      <attribute id="1" title="email_count" type="integer"/>',
            "    </attributes>",
            "    <nodes>",
        ]

        for node in nodes:
            name_escaped = node.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            email_escaped = node.email.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.extend([
                f'      <node id="{node.person_id}" label="{name_escaped}">',
                "        <attvalues>",
                f'          <attvalue for="0" value="{email_escaped}"/>',
                f'          <attvalue for="1" value="{node.email_count}"/>',
                "        </attvalues>",
                "      </node>",
            ])

        lines.append("    </nodes>")
        lines.append("    <edges>")

        for i, edge in enumerate(edges):
            lines.append(
                f'      <edge id="{i}" source="{edge.source_id}" '
                f'target="{edge.target_id}" weight="{edge.weight}"/>'
            )

        lines.extend([
            "    </edges>",
            "  </graph>",
            "</gexf>",
        ])

        return "\n".join(lines)

    def export_network_json(
        self,
        nodes: list[NetworkNode],
        edges: list[NetworkEdge],
    ) -> str:
        """Export network to JSON format (for D3.js, etc.).

        Args:
            nodes: Network nodes.
            edges: Network edges.

        Returns:
            JSON string.
        """
        data = {
            "nodes": [
                {
                    "id": node.person_id,
                    "name": node.name,
                    "email": node.email,
                    "email_count": node.email_count,
                    "degree": node.degree,
                }
                for node in nodes
            ],
            "links": [
                {
                    "source": edge.source_id,
                    "target": edge.target_id,
                    "weight": edge.weight,
                }
                for edge in edges
            ],
        }
        return json.dumps(data, indent=2)

    def export_network_graphml(
        self,
        nodes: list[NetworkNode],
        edges: list[NetworkEdge],
    ) -> str:
        """Export network to GraphML format.

        Args:
            nodes: Network nodes.
            edges: Network edges.

        Returns:
            GraphML XML string.
        """
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
            '  <key id="name" for="node" attr.name="name" attr.type="string"/>',
            '  <key id="email" for="node" attr.name="email" attr.type="string"/>',
            '  <key id="email_count" for="node" attr.name="email_count" attr.type="int"/>',
            '  <key id="weight" for="edge" attr.name="weight" attr.type="int"/>',
            '  <graph id="correspondence" edgedefault="undirected">',
        ]

        for node in nodes:
            name_escaped = node.name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            email_escaped = node.email.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.extend([
                f'    <node id="{node.person_id}">',
                f'      <data key="name">{name_escaped}</data>',
                f'      <data key="email">{email_escaped}</data>',
                f'      <data key="email_count">{node.email_count}</data>',
                "    </node>",
            ])

        for i, edge in enumerate(edges):
            lines.extend([
                f'    <edge id="e{i}" source="{edge.source_id}" target="{edge.target_id}">',
                f'      <data key="weight">{edge.weight}</data>',
                "    </edge>",
            ])

        lines.extend([
            "  </graph>",
            "</graphml>",
        ])

        return "\n".join(lines)
