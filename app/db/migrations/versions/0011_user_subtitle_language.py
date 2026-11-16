"""add preferred_subtitle_language to users table

Revision ID: 0011_user_subtitle_language
Revises: 0010_user_audio_language
Create Date: 2026-11-16 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_subtitle_language"
down_revision: str | None = "0010_user_audio_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("preferred_subtitle_language", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("preferred_subtitle_language")
