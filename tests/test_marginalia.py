"""TDD Tests for Marginalia and MarginaliaTarget ORM models.

Marginalia are free-form notes that can be attached to any record
via URIs. They support the memex ecosystem contract:
- UUID-based durable IDs
- Soft delete via archived_at
- Multi-target attachment via MarginaliaTarget join table
- Cascade delete from Marginalia to MarginaliaTarget
"""

from datetime import UTC, datetime

from sqlalchemy import select

from mail_memex.core.models import Marginalia, MarginaliaTarget


class TestMarginaliaModel:
    """Tests for the Marginalia ORM model."""

    def test_create_marginalia(self, session) -> None:
        """Create a marginalia with UUID, content, and verify defaults."""
        note = Marginalia(content="This thread is important for the Q1 review.")
        session.add(note)
        session.commit()

        result = session.get(Marginalia, note.id)
        assert result.content == "This thread is important for the Q1 review."
        assert result.uuid is not None
        assert len(result.uuid) == 32  # hex UUID without dashes
        assert result.pinned is False
        assert result.category is None
        assert result.color is None
        assert result.created_at is not None
        assert result.updated_at is not None
        assert result.archived_at is None

    def test_marginalia_with_targets(self, session) -> None:
        """Marginalia can be attached to multiple target URIs."""
        note = Marginalia(content="Cross-referencing these two emails.")
        note.targets = [
            MarginaliaTarget(target_uri="mail-memex://email/abc123@example.com"),
            MarginaliaTarget(target_uri="mail-memex://thread/thread-001"),
        ]
        session.add(note)
        session.commit()

        result = session.get(Marginalia, note.id)
        assert len(result.targets) == 2
        uris = {t.target_uri for t in result.targets}
        assert "mail-memex://email/abc123@example.com" in uris
        assert "mail-memex://thread/thread-001" in uris

    def test_marginalia_soft_delete(self, session) -> None:
        """Setting archived_at should persist (soft delete)."""
        note = Marginalia(content="Temporary note.")
        session.add(note)
        session.commit()

        now = datetime.now(UTC)
        note.archived_at = now
        session.commit()

        result = session.get(Marginalia, note.id)
        assert result.archived_at is not None

    def test_marginalia_cascade_delete_targets(self, session) -> None:
        """Deleting a marginalia should cascade-delete its targets."""
        note = Marginalia(content="Note with targets.")
        note.targets = [
            MarginaliaTarget(target_uri="mail-memex://email/del1@example.com"),
            MarginaliaTarget(target_uri="mail-memex://email/del2@example.com"),
        ]
        session.add(note)
        session.commit()

        note_id = note.id
        session.delete(note)
        session.commit()

        # Targets should be gone
        remaining = (
            session.execute(
                select(MarginaliaTarget).where(
                    MarginaliaTarget.marginalia_id == note_id
                )
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 0

    def test_marginalia_category_and_color(self, session) -> None:
        """Optional category and color fields should persist."""
        note = Marginalia(
            content="Flagged for follow-up.",
            category="follow-up",
            color="#ff6600",
        )
        session.add(note)
        session.commit()

        result = session.get(Marginalia, note.id)
        assert result.category == "follow-up"
        assert result.color == "#ff6600"

    def test_marginalia_uuid_unique(self, session) -> None:
        """Each marginalia should get a unique UUID."""
        note1 = Marginalia(content="First note.")
        note2 = Marginalia(content="Second note.")
        session.add_all([note1, note2])
        session.commit()

        assert note1.uuid != note2.uuid


class TestMarginaliaTargetModel:
    """Tests for the MarginaliaTarget ORM model."""

    def test_target_back_populates_marginalia(self, session) -> None:
        """MarginaliaTarget.marginalia should back-populate to the parent."""
        note = Marginalia(content="Parent note.")
        target = MarginaliaTarget(target_uri="mail-memex://email/back-pop@example.com")
        note.targets.append(target)
        session.add(note)
        session.commit()

        result = (
            session.execute(
                select(MarginaliaTarget).where(
                    MarginaliaTarget.target_uri
                    == "mail-memex://email/back-pop@example.com"
                )
            )
            .scalars()
            .first()
        )
        assert result.marginalia is not None
        assert result.marginalia.content == "Parent note."
