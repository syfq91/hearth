"""media catalog and full-text search

Revision ID: 0003_media_catalog
Revises: 0002_phase1_models
Create Date: 2026-10-21 16:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "0003_media_catalog"
down_revision: str | None = "0002_phase1_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_scan_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("last_scan_status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("last_scan_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("last_scan_stats", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )

    op.create_table(
        "media_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("library_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("sort_title", sa.String(length=512), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("runtime_seconds", sa.Integer(), nullable=True),
        sa.Column("added_at", UTCDateTime(), nullable=False),
        sa.Column("match_state", sa.String(length=16), nullable=False),
        sa.Column("match_attempts", sa.Integer(), nullable=False),
        sa.Column("match_error", sa.Text(), nullable=True),
        sa.Column("next_match_at", UTCDateTime(), nullable=True),
        sa.Column("matched_at", UTCDateTime(), nullable=True),
        sa.Column("provider_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "type IN ('movie', 'series', 'season', 'episode', 'artist', 'album', 'track', 'book')",
            name=op.f("ck_media_items_type"),
        ),
        sa.CheckConstraint(
            "match_state IN ('pending', 'matched', 'unmatched')",
            name=op.f("ck_media_items_match_state"),
        ),
        sa.ForeignKeyConstraint(
            ["library_id"],
            ["libraries.id"],
            name=op.f("fk_media_items_library_id_libraries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_items")),
    )
    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.create_index(
            "ix_media_items_library_title", ["library_id", "sort_title"], unique=False
        )
        batch_op.create_index(
            "ix_media_items_library_added", ["library_id", "added_at"], unique=False
        )
        batch_op.create_index(
            "ix_media_items_match_due", ["match_state", "next_match_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_media_items_library_id"), ["library_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_media_items_type"), ["type"], unique=False)
        batch_op.create_index(batch_op.f("ix_media_items_sort_title"), ["sort_title"], unique=False)
        batch_op.create_index(batch_op.f("ix_media_items_year"), ["year"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_media_items_match_state"), ["match_state"], unique=False
        )

    op.create_table(
        "movies",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("original_title", sa.String(length=512), nullable=True),
        sa.Column("edition", sa.String(length=64), nullable=True),
        sa.Column("part", sa.Integer(), nullable=True),
        sa.Column("tagline", sa.String(length=512), nullable=True),
        sa.Column("release_date", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_movies_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_movies")),
    )

    op.create_table(
        "media_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=False),
        sa.Column("partial_hash", sa.String(length=64), nullable=False),
        sa.Column("container", sa.String(length=64), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("bit_rate", sa.BigInteger(), nullable=True),
        sa.Column("streams_json", sa.JSON(), nullable=False),
        sa.Column("sidecar_subtitles", sa.JSON(), nullable=False),
        sa.Column("probe_error", sa.Text(), nullable=True),
        sa.Column("version_label", sa.String(length=64), nullable=True),
        sa.Column("missing_since", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_media_files_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_files")),
        sa.UniqueConstraint("path", name=op.f("uq_media_files_path")),
    )
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.create_index(
            "ix_media_files_identity", ["size_bytes", "partial_hash"], unique=False
        )
        batch_op.create_index(
            "ix_media_files_item_missing", ["item_id", "missing_since"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_media_files_item_id"), ["item_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_media_files_partial_hash"), ["partial_hash"], unique=False
        )

    op.create_table(
        "genres",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_genres")),
    )
    with op.batch_alter_table("genres", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_genres_name"), ["name"], unique=True)

    op.create_table(
        "item_genres",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("genre_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["genre_id"],
            ["genres.id"],
            name=op.f("fk_item_genres_genre_id_genres"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_item_genres_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "genre_id", name=op.f("pk_item_genres")),
    )

    op.create_table(
        "people",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tmdb_id", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
        sa.UniqueConstraint("tmdb_id", name=op.f("uq_people_tmdb_id")),
    )
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_people_name"), ["name"], unique=False)

    op.create_table(
        "item_people",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("person_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("character", sa.String(length=255), nullable=True),
        sa.Column("ordering", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_item_people_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name=op.f("fk_item_people_person_id_people"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "person_id", "role", name=op.f("pk_item_people")),
    )

    op.create_table(
        "images",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("blurhash", sa.String(length=128), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_images_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_images")),
    )
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.create_index("ix_images_item_kind", ["item_id", "kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_images_item_id"), ["item_id"], unique=False)

    op.create_table(
        "provider_cache",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "key", name=op.f("pk_provider_cache")),
    )
    with op.batch_alter_table("provider_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_provider_cache_expires_at"), ["expires_at"], unique=False
        )

    op.create_table(
        "user_item_state",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("played", sa.Boolean(), nullable=False),
        sa.Column("position_ms", sa.Integer(), nullable=False),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("play_count", sa.Integer(), nullable=False),
        sa.Column("last_played_at", UTCDateTime(), nullable=True),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 10",
            name=op.f("ck_user_item_state_rating"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_user_item_state_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_item_state_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "item_id", name=op.f("pk_user_item_state")),
    )

    # External-content FTS index. Triggers keep it in sync for every writer,
    # including administrative SQL and future migrations. Queries join on the
    # implicit media_items.rowid, which is stable for the lifetime of a row.
    op.execute(
        """
        CREATE VIRTUAL TABLE media_items_fts USING fts5(
            title,
            sort_title,
            content='media_items',
            content_rowid='rowid',
            tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER media_items_fts_ai AFTER INSERT ON media_items BEGIN
            INSERT INTO media_items_fts(rowid, title, sort_title)
            VALUES (new.rowid, new.title, new.sort_title);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER media_items_fts_ad AFTER DELETE ON media_items BEGIN
            INSERT INTO media_items_fts(media_items_fts, rowid, title, sort_title)
            VALUES ('delete', old.rowid, old.title, old.sort_title);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER media_items_fts_au AFTER UPDATE OF title, sort_title ON media_items BEGIN
            INSERT INTO media_items_fts(media_items_fts, rowid, title, sort_title)
            VALUES ('delete', old.rowid, old.title, old.sort_title);
            INSERT INTO media_items_fts(rowid, title, sort_title)
            VALUES (new.rowid, new.title, new.sort_title);
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS media_items_fts_au")
    op.execute("DROP TRIGGER IF EXISTS media_items_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS media_items_fts_ai")
    op.execute("DROP TABLE IF EXISTS media_items_fts")

    op.drop_table("user_item_state")
    with op.batch_alter_table("provider_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_provider_cache_expires_at"))
    op.drop_table("provider_cache")
    with op.batch_alter_table("images", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_images_item_id"))
        batch_op.drop_index("ix_images_item_kind")
    op.drop_table("images")
    op.drop_table("item_people")
    with op.batch_alter_table("people", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_people_name"))
    op.drop_table("people")
    op.drop_table("item_genres")
    with op.batch_alter_table("genres", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_genres_name"))
    op.drop_table("genres")
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_media_files_partial_hash"))
        batch_op.drop_index(batch_op.f("ix_media_files_item_id"))
        batch_op.drop_index("ix_media_files_item_missing")
        batch_op.drop_index("ix_media_files_identity")
    op.drop_table("media_files")
    op.drop_table("movies")
    with op.batch_alter_table("media_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_media_items_match_state"))
        batch_op.drop_index(batch_op.f("ix_media_items_year"))
        batch_op.drop_index(batch_op.f("ix_media_items_sort_title"))
        batch_op.drop_index(batch_op.f("ix_media_items_type"))
        batch_op.drop_index(batch_op.f("ix_media_items_library_id"))
        batch_op.drop_index("ix_media_items_match_due")
        batch_op.drop_index("ix_media_items_library_added")
        batch_op.drop_index("ix_media_items_library_title")
    op.drop_table("media_items")
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.drop_column("last_scan_stats")
        batch_op.drop_column("last_scan_error")
        batch_op.drop_column("last_scan_status")
        batch_op.drop_column("last_scan_at")
