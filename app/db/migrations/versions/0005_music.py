"""music schema: artists, albums, tracks, playlists and playlist items

Revision ID: 0005_music
Revises: 0004_tv_shows
Create Date: 2026-10-26 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "0005_music"
down_revision: str | None = "0004_tv_shows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artists",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("musicbrainz_id", sa.String(length=36), nullable=True),
        sa.Column("sort_name", sa.String(length=512), nullable=True),
        sa.Column("country", sa.String(length=64), nullable=True),
        sa.Column("disambiguation", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_artists_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_artists")),
    )
    with op.batch_alter_table("artists", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_artists_musicbrainz_id"), ["musicbrainz_id"], unique=False
        )

    op.create_table(
        "albums",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("artist_id", sa.String(length=36), nullable=True),
        sa.Column("album_artist", sa.String(length=512), nullable=True),
        sa.Column("musicbrainz_id", sa.String(length=36), nullable=True),
        sa.Column("musicbrainz_release_group_id", sa.String(length=36), nullable=True),
        sa.Column("release_date", sa.String(length=10), nullable=True),
        sa.Column("total_discs", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("total_tracks", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_albums_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.item_id"],
            name=op.f("fk_albums_artist_id_artists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_albums")),
    )
    with op.batch_alter_table("albums", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_albums_artist_id"), ["artist_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_albums_musicbrainz_id"), ["musicbrainz_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_albums_musicbrainz_release_group_id"),
            ["musicbrainz_release_group_id"],
            unique=False,
        )

    op.create_table(
        "tracks",
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("album_id", sa.String(length=36), nullable=True),
        sa.Column("artist_id", sa.String(length=36), nullable=True),
        sa.Column("file_id", sa.String(length=36), nullable=True),
        sa.Column("disc_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("bitrate", sa.Integer(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True, server_default="2"),
        sa.Column("replaygain_track_gain", sa.Float(), nullable=True),
        sa.Column("replaygain_track_peak", sa.Float(), nullable=True),
        sa.Column("replaygain_album_gain", sa.Float(), nullable=True),
        sa.Column("replaygain_album_peak", sa.Float(), nullable=True),
        sa.Column("musicbrainz_track_id", sa.String(length=36), nullable=True),
        sa.Column("musicbrainz_release_track_id", sa.String(length=36), nullable=True),
        sa.Column("musicbrainz_artist_id", sa.String(length=36), nullable=True),
        sa.Column("musicbrainz_album_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_tracks_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["album_id"],
            ["albums.item_id"],
            name=op.f("fk_tracks_album_id_albums"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.item_id"],
            name=op.f("fk_tracks_artist_id_artists"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["media_files.id"],
            name=op.f("fk_tracks_file_id_media_files"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_tracks")),
    )
    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tracks_album_id"), ["album_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tracks_artist_id"), ["artist_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_tracks_file_id"), ["file_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_tracks_musicbrainz_track_id"), ["musicbrainz_track_id"], unique=False
        )
        batch_op.create_index(
            "ix_tracks_album_disc_track",
            ["album_id", "disc_number", "track_number"],
            unique=False,
        )

    op.create_table(
        "playlists",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_playlists_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlists")),
    )
    with op.batch_alter_table("playlists", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_playlists_user_id"), ["user_id"], unique=False)

    op.create_table(
        "playlist_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("playlist_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("added_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["playlist_id"],
            ["playlists.id"],
            name=op.f("fk_playlist_items_playlist_id_playlists"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["media_items.id"],
            name=op.f("fk_playlist_items_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_items")),
        sa.UniqueConstraint(
            "playlist_id", "position", name=op.f("uq_playlist_items_playlist_id_position")
        ),
    )
    with op.batch_alter_table("playlist_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_playlist_items_playlist_id"), ["playlist_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_playlist_items_item_id"), ["item_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_playlist_items_position"), ["playlist_id", "position"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("playlist_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_playlist_items_position"))
        batch_op.drop_index(batch_op.f("ix_playlist_items_item_id"))
        batch_op.drop_index(batch_op.f("ix_playlist_items_playlist_id"))
    op.drop_table("playlist_items")

    with op.batch_alter_table("playlists", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_playlists_user_id"))
    op.drop_table("playlists")

    with op.batch_alter_table("tracks", schema=None) as batch_op:
        batch_op.drop_index("ix_tracks_album_disc_track")
        batch_op.drop_index(batch_op.f("ix_tracks_musicbrainz_track_id"))
        batch_op.drop_index(batch_op.f("ix_tracks_file_id"))
        batch_op.drop_index(batch_op.f("ix_tracks_artist_id"))
        batch_op.drop_index(batch_op.f("ix_tracks_album_id"))
    op.drop_table("tracks")

    with op.batch_alter_table("albums", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_albums_musicbrainz_release_group_id"))
        batch_op.drop_index(batch_op.f("ix_albums_musicbrainz_id"))
        batch_op.drop_index(batch_op.f("ix_albums_artist_id"))
    op.drop_table("albums")

    with op.batch_alter_table("artists", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artists_musicbrainz_id"))
    op.drop_table("artists")
