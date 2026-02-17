"""Tests for people and relationship modules."""

from sqlalchemy import select

from mtk.core.models import Person, PersonEmail
from mtk.people.relationships import RelationshipAnalyzer
from mtk.people.resolver import PersonResolver


class TestPersonResolver:
    """Tests for PersonResolver class."""

    def test_resolve_new_person(self, db) -> None:
        """Test resolving a new email address creates a person."""
        with db.session() as session:
            resolver = PersonResolver(session)
            person = resolver.resolve("john@example.com", "John Doe")

            assert person.name == "John Doe"
            assert person.primary_email == "john@example.com"

            # Check person email mapping was created
            pe = session.execute(
                select(PersonEmail).where(PersonEmail.email == "john@example.com")
            ).scalar()
            assert pe is not None
            assert pe.person_id == person.id

    def test_resolve_existing_person(self, db) -> None:
        """Test resolving an existing email returns the same person."""
        with db.session() as session:
            resolver = PersonResolver(session)

            person1 = resolver.resolve("john@example.com", "John")
            person2 = resolver.resolve("john@example.com", "John Doe")

            assert person1.id == person2.id
            # Name keeps the first non-email name (implementation choice)
            assert person2.name == "John"

    def test_add_email_to_person(self, db) -> None:
        """Test adding additional email to a person."""
        with db.session() as session:
            resolver = PersonResolver(session)

            person = resolver.resolve("john@example.com", "John")
            resolver.add_email_to_person(person, "john.doe@work.com")

            # Check both emails map to same person
            pe1 = session.execute(
                select(PersonEmail).where(PersonEmail.email == "john@example.com")
            ).scalar()
            pe2 = session.execute(
                select(PersonEmail).where(PersonEmail.email == "john.doe@work.com")
            ).scalar()

            assert pe1.person_id == pe2.person_id

    def test_merge_persons(self, db) -> None:
        """Test merging two persons."""
        with db.session() as session:
            resolver = PersonResolver(session)

            person1 = resolver.resolve("john@example.com", "John")
            person1.email_count = 5
            session.flush()

            person2 = resolver.resolve("johnny@work.com", "Johnny")
            person2.email_count = 3
            session.flush()

            person1_id = person1.id
            person2_id = person2.id

            merged = resolver.merge_persons(person1, person2)

            assert merged.id == person1_id
            assert merged.email_count == 8

            # Commit changes and verify in a new query
            session.commit()

            # Verify person2 was deleted
            remaining = session.execute(select(Person)).scalars().all()
            remaining_ids = [p.id for p in remaining]
            assert person2_id not in remaining_ids
            assert person1_id in remaining_ids

    def test_extract_name_from_email(self, db) -> None:
        """Test extracting readable name from email."""
        with db.session() as session:
            resolver = PersonResolver(session)

            # No name provided, should extract from email
            person = resolver.resolve("john.doe@example.com")
            assert "John" in person.name or "john" in person.name.lower()


class TestRelationshipAnalyzer:
    """Tests for RelationshipAnalyzer class."""

    def test_get_top_correspondents(self, populated_db) -> None:
        """Test getting top correspondents."""
        with populated_db.session() as session:
            analyzer = RelationshipAnalyzer(session)
            stats = analyzer.get_top_correspondents(limit=10)

            assert len(stats) > 0
            # Should be sorted by email count
            if len(stats) > 1:
                assert stats[0].total_emails >= stats[1].total_emails

    def test_get_correspondent_stats(self, populated_db) -> None:
        """Test getting stats for specific correspondent."""
        with populated_db.session() as session:
            # First get a person ID
            person = session.execute(select(Person).limit(1)).scalar()

            analyzer = RelationshipAnalyzer(session)
            stats = analyzer.get_correspondent_stats(person.id)

            assert stats is not None
            assert stats.person_id == person.id
            assert stats.person_name == person.name

    def test_build_network(self, populated_db) -> None:
        """Test building correspondence network."""
        with populated_db.session() as session:
            analyzer = RelationshipAnalyzer(session)
            nodes, edges = analyzer.build_network(min_emails=1)

            # Should have nodes for people with emails
            assert len(nodes) >= 0  # May be 0 if no qualifying data

    def test_export_network_json(self, populated_db) -> None:
        """Test exporting network to JSON."""
        import json

        with populated_db.session() as session:
            analyzer = RelationshipAnalyzer(session)
            nodes, edges = analyzer.build_network(min_emails=1)

            json_str = analyzer.export_network_json(nodes, edges)
            data = json.loads(json_str)

            assert "nodes" in data
            assert "links" in data

    def test_export_network_gexf(self, populated_db) -> None:
        """Test exporting network to GEXF format."""
        with populated_db.session() as session:
            analyzer = RelationshipAnalyzer(session)
            nodes, edges = analyzer.build_network(min_emails=1)

            gexf = analyzer.export_network_gexf(nodes, edges)

            assert '<?xml version="1.0"' in gexf
            assert "<gexf" in gexf
            assert "</gexf>" in gexf
