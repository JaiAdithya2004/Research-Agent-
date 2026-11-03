"""rename the review action 'accept' to 'approve'

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _swap(old: str, new: str, allowed: str) -> None:
    # reviews are append-only (a trigger forbids UPDATE), so the trigger is paused for this
    # one-off data migration and switched straight back on.
    op.execute("ALTER TABLE reviews DISABLE TRIGGER reviews_append_only")
    op.execute("ALTER TABLE reviews DROP CONSTRAINT ck_reviews_action")
    op.execute(f"UPDATE reviews SET action = '{new}' WHERE action = '{old}'")
    op.execute(
        f"ALTER TABLE reviews ADD CONSTRAINT ck_reviews_action CHECK (action IN ({allowed}))"
    )
    op.execute("ALTER TABLE reviews ENABLE TRIGGER reviews_append_only")


def upgrade() -> None:
    _swap("accept", "approve", "'approve', 'correct', 'reject'")


def downgrade() -> None:
    _swap("approve", "accept", "'accept', 'correct', 'reject'")
