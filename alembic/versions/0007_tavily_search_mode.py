"""allow the 'tavily' search mode on runs

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set(allowed: str) -> None:
    op.execute("ALTER TABLE runs DROP CONSTRAINT ck_runs_search_mode")
    op.execute(
        f"ALTER TABLE runs ADD CONSTRAINT ck_runs_search_mode CHECK (search_mode IN ({allowed}))"
    )


def upgrade() -> None:
    _set("'off', 'grounded', 'tavily'")


def downgrade() -> None:
    # Runs made with Tavily have no place in the old schema, so they are removed first.
    op.execute("DELETE FROM runs WHERE search_mode = 'tavily'")
    _set("'off', 'grounded'")
