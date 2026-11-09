"""state and progress composite indexes for home shelves

Revision ID: 0009_state_and_progress_indexes
Revises: 0008_collections_and_content_rating
Create Date: 2026-11-09 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_state_and_progress_indexes"
down_revision: str | None = "0008_collections_and_content_rating"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_item_state", schema=None) as batch_op:
        batch_op.create_index(
            "ix_user_item_state_continue",
            ["user_id", "played", "last_played_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_user_item_state_favorite",
            ["user_id", "favorite", "updated_at"],
            unique=False,
        )

    with op.batch_alter_table("book_progress", schema=None) as batch_op:
        batch_op.create_index(
            "ix_book_progress_continue",
            ["user_id", "completed", "updated_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("book_progress", schema=None) as batch_op:
        batch_op.drop_index("ix_book_progress_continue")

    with op.batch_alter_table("user_item_state", schema=None) as batch_op:
        batch_op.drop_index("ix_user_item_state_favorite")
        batch_op.drop_index("ix_user_item_state_continue")
