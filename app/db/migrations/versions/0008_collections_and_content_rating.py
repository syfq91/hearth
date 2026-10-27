"""collections and media item content rating

Revision ID: 0008_collections_and_content_rating
Revises: 0007_books
Create Date: 2026-10-27 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "0008_collections_and_content_rating"
down_revision: str | None = "0007_books"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("poster_path", sa.String(length=512), nullable=True),
        sa.Column("tmdb_id", sa.Integer(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collections")),
        sa.UniqueConstraint("tmdb_id", name=op.f("uq_collections_tmdb_id")),
    )
    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_collections_name"), ["name"], unique=False)

    op.create_table(
        "collection_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("media_item_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_collection_items_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_collection_items_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_items")),
        sa.UniqueConstraint(
            "collection_id", "media_item_id", name=op.f("uq_collection_items_collection_item")
        ),
    )
    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_collection_items_collection_id"), ["collection_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_collection_items_media_item_id"), ["media_item_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_collection_items_position"), ["collection_id", "position"], unique=False
        )

    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("content_rating", sa.String(length=16), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_media_items_content_rating"), ["content_rating"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_media_items_content_rating"))
        batch_op.drop_column("content_rating")

    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_collection_items_position"))
        batch_op.drop_index(batch_op.f("ix_collection_items_media_item_id"))
        batch_op.drop_index(batch_op.f("ix_collection_items_collection_id"))

    op.drop_table("collection_items")

    with op.batch_alter_table("collections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_collections_name"))

    op.drop_table("collections")
