"""books schema: authors, book_series, books, and book_progress

Revision ID: 0007_books
Revises: 0006_listenbrainz
Create Date: 2026-10-27 11:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "0007_books"
down_revision: str | None = "0006_listenbrainz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sort_name", sa.String(length=255), nullable=True),
        sa.Column("openlibrary_id", sa.String(length=32), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authors")),
    )
    with op.batch_alter_table("authors", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_authors_name"), ["name"], unique=False)
        batch_op.create_index(batch_op.f("ix_authors_sort_name"), ["sort_name"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_authors_openlibrary_id"), ["openlibrary_id"], unique=False
        )

    op.create_table(
        "book_series",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_book_series")),
    )
    with op.batch_alter_table("book_series", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_book_series_name"), ["name"], unique=False)

    op.create_table(
        "books",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("series_id", sa.String(length=36), nullable=True),
        sa.Column("series_index", sa.Float(), nullable=True),
        sa.Column("isbn", sa.String(length=32), nullable=True),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("published_date", sa.String(length=32), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["authors.id"],
            name=op.f("fk_books_author_id_authors"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_books_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["book_series.id"],
            name=op.f("fk_books_series_id_book_series"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_books")),
    )
    with op.batch_alter_table("books", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_books_author_id"), ["author_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_books_isbn"), ["isbn"], unique=False)
        batch_op.create_index(batch_op.f("ix_books_series_id"), ["series_id"], unique=False)

    op.create_table(
        "book_progress",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("book_id", sa.String(length=36), nullable=False),
        sa.Column("locator", sa.String(length=512), nullable=False),
        sa.Column("percentage", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column("completed", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "percentage >= 0.0 AND percentage <= 100.0",
            name=op.f("ck_book_progress_percentage_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["book_id"],
            ["media_items.id"],
            name=op.f("fk_book_progress_book_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_book_progress_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "book_id", name=op.f("pk_book_progress")),
    )


def downgrade() -> None:
    op.drop_table("book_progress")
    op.drop_table("books")
    op.drop_table("book_series")
    op.drop_table("authors")
