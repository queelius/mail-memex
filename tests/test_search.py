"""Tests for search engine."""

from datetime import datetime

from mtk.search.engine import SearchEngine


class TestSearchQuery:
    """Tests for SearchQuery parsing."""

    def test_parse_simple_query(self) -> None:
        """Test parsing a simple text query."""
        engine = SearchEngine(None)
        query = engine.parse_query("hello world")

        assert query.text == "hello world"

    def test_parse_from_operator(self) -> None:
        """Test parsing from: operator."""
        engine = SearchEngine(None)
        query = engine.parse_query("from:john@example.com project update")

        assert query.from_addr == "john@example.com"
        assert query.text == "project update"

    def test_parse_date_operators(self) -> None:
        """Test parsing date operators."""
        engine = SearchEngine(None)
        query = engine.parse_query("after:2024-01-01 before:2024-12-31 report")

        assert query.date_from == datetime(2024, 1, 1)
        assert query.date_to == datetime(2024, 12, 31)
        assert query.text == "report"

    def test_parse_tag_operators(self) -> None:
        """Test parsing tag operators."""
        engine = SearchEngine(None)
        query = engine.parse_query("tag:important -tag:spam")

        assert "important" in query.has_tags
        # Note: -tag: is parsed separately
        assert query.text is None or "spam" not in query.text

    def test_parse_multiple_operators(self) -> None:
        """Test parsing multiple operators together."""
        engine = SearchEngine(None)
        query = engine.parse_query("from:alice subject:meeting after:2024-01-01 project discussion")

        assert query.from_addr == "alice"
        assert query.subject == "meeting"
        assert query.date_from == datetime(2024, 1, 1)
        assert query.text == "project discussion"


class TestSearchEngine:
    """Tests for SearchEngine class."""

    def test_keyword_search(self, populated_db) -> None:
        """Test basic keyword search."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("Project")

            assert len(results) > 0
            # Should find emails with "Project" in subject or body
            subjects = [r.email.subject for r in results]
            assert any("Project" in s for s in subjects if s)

    def test_search_by_from(self, populated_db) -> None:
        """Test searching by sender."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("from:alice")

            assert len(results) > 0
            for r in results:
                assert "alice" in r.email.from_addr.lower()

    def test_search_by_to(self, populated_db) -> None:
        """Test that to: operator filters by to_addrs column."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            # email1 has to_addrs="bob@example.com" (set in populated_db fixture)
            results = engine.search("to:bob@example.com")

            assert len(results) > 0
            for r in results:
                assert r.email.to_addrs is not None
                assert "bob@example.com" in r.email.to_addrs

    def test_parse_to_operator(self) -> None:
        """Test that to: operator is parsed into to_addr field."""
        engine = SearchEngine(None)
        query = engine.parse_query("to:bob@example.com project")

        assert query.to_addr == "bob@example.com"
        assert query.text == "project"

    def test_search_date_range(self, populated_db) -> None:
        """Test searching by date range."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("after:2024-01-15 before:2024-01-16")

            assert len(results) > 0
            for r in results:
                assert r.email.date.date() >= datetime(2024, 1, 15).date()
                assert r.email.date.date() <= datetime(2024, 1, 16).date()

    def test_search_no_results(self, populated_db) -> None:
        """Test search with no matching results."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("xyznonexistent12345")

            assert len(results) == 0

    def test_search_limit(self, populated_db) -> None:
        """Test search result limiting."""
        with populated_db.session() as session:
            engine = SearchEngine(session)
            results = engine.search("*", limit=1)

            assert len(results) <= 1
