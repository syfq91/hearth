"""add subtitle_mode to users table

Revision ID: 0012_user_subtitle_mode
Revises: 0011_user_subtitle_language
Create Date: 2026-11-16 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_user_subtitle_mode"
down_revision: str | None = "0011_user_subtitle_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "subtitle_mode",
                sa.String(length=16),
                nullable=False,
                server_default="smart",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("subtitle_mode")
