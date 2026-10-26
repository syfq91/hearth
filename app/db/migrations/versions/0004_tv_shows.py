"""tv shows schema: series, seasons, and episodes

Revision ID: 0004_tv_shows
Revises: 0003_media_catalog
Create Date: 2026-10-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_tv_shows"
down_revision: str | None = "0003_media_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "series",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("original_title", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("first_air_date", sa.String(length=10), nullable=True),
        sa.Column("network", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_series_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_series")),
    )

    op.create_table(
        "seasons",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.String(length=36), nullable=False),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("air_date", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_seasons_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.item_id"],
            name=op.f("fk_seasons_series_id_series"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_seasons")),
        sa.UniqueConstraint("series_id", "season_number", name=op.f("uq_seasons_series_number")),
    )
    with op.batch_alter_table("seasons", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_seasons_series_id"), ["series_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_seasons_season_number"), ["season_number"], unique=False
        )

    op.create_table(
        "episodes",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("series_id", sa.String(length=36), nullable=False),
        sa.Column("season_id", sa.String(length=36), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("air_date", sa.String(length=10), nullable=True),
        sa.Column("absolute_number", sa.Integer(), nullable=True),
        sa.Column("file_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_episodes_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["series.item_id"],
            name=op.f("fk_episodes_series_id_series"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.item_id"],
            name=op.f("fk_episodes_season_id_seasons"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["media_files.id"],
            name=op.f("fk_episodes_file_id_media_files"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_episodes")),
    )
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_episodes_series_id"), ["series_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_episodes_season_id"), ["season_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_episodes_season_number"), ["season_number"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_episodes_episode_number"), ["episode_number"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_episodes_file_id"), ["file_id"], unique=False)
        batch_op.create_index(
            "ix_episodes_series_season_episode",
            ["series_id", "season_number", "episode_number"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.drop_index("ix_episodes_series_season_episode")
        batch_op.drop_index(batch_op.f("ix_episodes_file_id"))
        batch_op.drop_index(batch_op.f("ix_episodes_episode_number"))
        batch_op.drop_index(batch_op.f("ix_episodes_season_number"))
        batch_op.drop_index(batch_op.f("ix_episodes_season_id"))
        batch_op.drop_index(batch_op.f("ix_episodes_series_id"))
    op.drop_table("episodes")

    with op.batch_alter_table("seasons", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_seasons_season_number"))
        batch_op.drop_index(batch_op.f("ix_seasons_series_id"))
    op.drop_table("seasons")

    op.drop_table("series")
