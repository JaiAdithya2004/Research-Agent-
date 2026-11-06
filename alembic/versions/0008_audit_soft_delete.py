"""soft delete for audits

Revision ID: 0008
Revises: 0007

An audit a user deletes is hidden everywhere, but its rows stay: raw answers, grounding metadata and
reviews are never destroyed (the database forbids it on purpose).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audits", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("audits", "deleted_at")
