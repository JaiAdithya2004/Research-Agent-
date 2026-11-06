"""let DELETE /audits/{id} remove an audit's reviews

Revision ID: 0008
Revises: 0007

Reviews are append-only: nothing may edit or delete them. The one exception is deleting the whole
audit they belong to. The delete operation sets a flag that lives only for its own transaction
(set_config('app.audit_purge', 'on', true)); with the flag, the reviews trigger lets DELETE
through. UPDATE stays forbidden always, and every other DELETE stays forbidden.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reviews_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('app.audit_purge', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER reviews_append_only ON reviews")
    op.execute(
        "CREATE TRIGGER reviews_append_only BEFORE UPDATE OR DELETE ON reviews "
        "FOR EACH ROW EXECUTE FUNCTION reviews_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER reviews_append_only ON reviews")
    op.execute(
        "CREATE TRIGGER reviews_append_only BEFORE UPDATE OR DELETE ON reviews "
        "FOR EACH ROW EXECUTE FUNCTION forbid_update_delete()"
    )
    op.execute("DROP FUNCTION reviews_guard()")
