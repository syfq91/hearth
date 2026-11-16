"""add auto_play_next to users table

Revision ID: 0013_user_auto_play_next
Revises: 0012_user_subtitle_mode
Create Date: 2026-11-16 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_user_auto_play_next"
down_revision: str | None = "0012_user_subtitle_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_play_next",
                sa.String(length=16),
                nullable=False,
                server_default="prompt",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("auto_play_next")
