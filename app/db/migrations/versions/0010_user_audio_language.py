"""add preferred_audio_language to users table

Revision ID: 0010_user_audio_language
Revises: 0009_state_and_progress_indexes
Create Date: 2026-11-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_user_audio_language"
down_revision: str | None = "0009_state_and_progress_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("preferred_audio_language", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("preferred_audio_language")
