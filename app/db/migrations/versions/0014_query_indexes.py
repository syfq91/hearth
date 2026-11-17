"""indexes for job/audit ordering and library-scoped media sorting

Revision ID: 0014_query_indexes
Revises: 0013_user_auto_play_next
Create Date: 2026-11-17 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_query_indexes"
down_revision: str | None = "0013_user_auto_play_next"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_updated_at", ["updated_at"], unique=False)

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index("ix_audit_log_created_at", ["created_at"], unique=False)

    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.create_index("ix_media_items_library_year", ["library_id", "year"], unique=False)
        batch_op.create_index(
            "ix_media_items_library_rating", ["library_id", "rating"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.drop_index("ix_media_items_library_rating")
        batch_op.drop_index("ix_media_items_library_year")

    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_created_at")

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_updated_at")
