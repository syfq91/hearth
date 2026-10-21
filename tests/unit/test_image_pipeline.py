"""Unit tests for artwork caching, variants and the matcher integration."""

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ValidationError
from app.db.models import Image as ImageModel
from app.db.models import Library, MediaFile, MediaItem, Movie
from app.services.images.pipeline import ImagePipeline
from app.services.media.titles import sort_title
from app.services.metadata.base import MovieMetadata, ProviderImage, SearchResult
from app.services.metadata.matching import MetadataMatcher
from tests.support import FakeProvider


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (200, 300)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (120, 40, 200)).save(buffer, image_format)
    return buffer.getvalue()


async def _seed_item(
    sessionmaker: async_sessionmaker[AsyncSession], *, state: str = "matched"
) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title="The Matrix",
            sort_title=sort_title("The Matrix"),
            year=1999,
            match_state=state,
            next_match_at=datetime.now(UTC),
        )
        item.movie = Movie()
        item.files.append(
            MediaFile(
                path=f"/media/{uuid.uuid4().hex}.mkv",
                size_bytes=1024,
                mtime=1.0,
                partial_hash="a" * 64,
                duration_seconds=8160.0,
            )
        )
        session.add(item)
        await session.commit()
        return item.id


async def _images(sessionmaker: async_sessionmaker[AsyncSession]) -> list[ImageModel]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(ImageModel)))


async def test_store_local_artwork_copies_and_describes(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    item_id = await _seed_item(db_sessionmaker)
    source = tmp_path / "poster.png"
    source.write_bytes(_image_bytes("PNG", (200, 300)))
    pipeline = ImagePipeline(db_settings, db_sessionmaker)

    count = await pipeline.store_for_item(
        item_id,
        "local",
        [ProviderImage(kind="poster", file_path=str(source), is_primary=True)],
    )

    assert count == 1
    rows = await _images(db_sessionmaker)
    assert len(rows) == 1
    row = rows[0]
    assert (row.width, row.height) == (200, 300)
    assert row.blurhash
    assert row.kind == "poster"
    assert row.provider == "local"
    assert row.source_url == str(source)
    assert row.is_primary is True
    cached = Path(row.local_path)
    assert cached.is_file()
    assert cached.is_relative_to(db_settings.images_dir)
    assert cached.read_bytes() == source.read_bytes()


async def test_store_remote_artwork_downloads_within_allowlist(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = db_settings.model_copy(update={"image_allowed_hosts": ["images.test"]})
    requests: list[str] = []
    payload = _image_bytes("JPEG", (300, 450))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pipeline = ImagePipeline(settings, db_sessionmaker, client=client)
    item_id = await _seed_item(db_sessionmaker)

    count = await pipeline.store_for_item(
        item_id,
        "tmdb",
        [
            ProviderImage(
                kind="poster",
                source_url="https://images.test/t/p/w780/p.jpg",
                is_primary=True,
            )
        ],
    )

    assert count == 1
    assert requests == ["https://images.test/t/p/w780/p.jpg"]
    rows = await _images(db_sessionmaker)
    assert len(rows) == 1
    assert rows[0].source_url == "https://images.test/t/p/w780/p.jpg"
    assert rows[0].local_path.endswith(".jpg")
    await client.aclose()


async def test_host_outside_allowlist_is_never_fetched(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=_image_bytes("PNG"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pipeline = ImagePipeline(db_settings, db_sessionmaker, client=client)
    item_id = await _seed_item(db_sessionmaker)

    count = await pipeline.store_for_item(
        item_id,
        "tmdb",
        [ProviderImage(kind="poster", source_url="https://evil.test/p.jpg", is_primary=True)],
    )

    assert count == 0
    assert requests == []
    assert await _images(db_sessionmaker) == []
    await client.aclose()


async def test_redirects_are_not_followed(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = db_settings.model_copy(update={"image_allowed_hosts": ["images.test"]})
    payload = _image_bytes("PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.test/p.png"}, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pipeline = ImagePipeline(settings, db_sessionmaker, client=client)
    item_id = await _seed_item(db_sessionmaker)

    count = await pipeline.store_for_item(
        item_id,
        "tmdb",
        [ProviderImage(kind="poster", source_url="https://images.test/p.png")],
    )

    assert count == 0
    assert await _images(db_sessionmaker) == []
    await client.aclose()


async def test_download_over_size_limit_is_skipped(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = db_settings.model_copy(
        update={"image_allowed_hosts": ["images.test"], "image_max_download_bytes": 64}
    )
    payload = _image_bytes("PNG", (400, 400))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pipeline = ImagePipeline(settings, db_sessionmaker, client=client)
    item_id = await _seed_item(db_sessionmaker)

    count = await pipeline.store_for_item(
        item_id,
        "tmdb",
        [ProviderImage(kind="poster", source_url="https://images.test/p.png")],
    )

    assert count == 0
    assert await _images(db_sessionmaker) == []
    await client.aclose()


async def test_duplicate_origin_is_stored_once(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    item_id = await _seed_item(db_sessionmaker)
    source = tmp_path / "poster.png"
    source.write_bytes(_image_bytes("PNG"))
    pipeline = ImagePipeline(db_settings, db_sessionmaker)
    provider_image = ProviderImage(kind="poster", file_path=str(source), is_primary=True)

    assert await pipeline.store_for_item(item_id, "local", [provider_image]) == 1
    assert await pipeline.store_for_item(item_id, "local", [provider_image]) == 0
    assert len(await _images(db_sessionmaker)) == 1


async def test_primary_flag_replaces_previous_primary(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    item_id = await _seed_item(db_sessionmaker)
    first = tmp_path / "first.png"
    first.write_bytes(_image_bytes("PNG", (100, 100)))
    second = tmp_path / "second.png"
    second.write_bytes(_image_bytes("PNG", (120, 120)))
    pipeline = ImagePipeline(db_settings, db_sessionmaker)

    await pipeline.store_for_item(
        item_id, "local", [ProviderImage(kind="poster", file_path=str(first), is_primary=True)]
    )
    await pipeline.store_for_item(
        item_id, "local", [ProviderImage(kind="poster", file_path=str(second), is_primary=True)]
    )

    rows = await _images(db_sessionmaker)
    assert len(rows) == 2
    primaries = [row for row in rows if row.is_primary]
    assert len(primaries) == 1
    assert primaries[0].source_url == str(second)


async def test_ensure_variant_renders_and_caches(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    item_id = await _seed_item(db_sessionmaker)
    source = tmp_path / "poster.png"
    source.write_bytes(_image_bytes("PNG", (400, 600)))
    pipeline = ImagePipeline(db_settings, db_sessionmaker)
    await pipeline.store_for_item(
        item_id, "local", [ProviderImage(kind="poster", file_path=str(source), is_primary=True)]
    )
    row = (await _images(db_sessionmaker))[0]

    target = await pipeline.ensure_variant(row.id, row.local_path, width=100, image_format="webp")

    assert target.is_file()
    assert target == pipeline.variant_path(row.id, width=100, image_format="webp")
    assert target.is_relative_to(db_settings.images_dir)
    with PILImage.open(target) as variant:
        assert variant.format == "WEBP"
        assert variant.width == 100
        assert variant.height == 150

    # A second request reuses the cached file instead of re-encoding.
    assert (
        await pipeline.ensure_variant(row.id, row.local_path, width=100, image_format="webp")
        == target
    )


async def test_ensure_variant_rejects_bad_requests(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    item_id = await _seed_item(db_sessionmaker)
    source = tmp_path / "poster.png"
    source.write_bytes(_image_bytes("PNG"))
    pipeline = ImagePipeline(db_settings, db_sessionmaker)
    await pipeline.store_for_item(
        item_id, "local", [ProviderImage(kind="poster", file_path=str(source), is_primary=True)]
    )
    row = (await _images(db_sessionmaker))[0]

    with pytest.raises(ValidationError):
        await pipeline.ensure_variant(row.id, row.local_path, width=100, image_format="gif")
    with pytest.raises(ValidationError):
        await pipeline.ensure_variant(row.id, row.local_path, width=4, image_format="webp")
    with pytest.raises(ValidationError):
        await pipeline.ensure_variant(
            row.id, str(tmp_path / "missing.png"), width=100, image_format="webp"
        )


async def test_matcher_stores_artwork_and_counts_it(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = db_settings.model_copy(update={"image_allowed_hosts": ["images.test"]})
    payload = _image_bytes("JPEG", (300, 450))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    item_id = await _seed_item(db_sessionmaker, state="pending")
    provider = FakeProvider(
        results=[SearchResult("tmdb", "603", "The Matrix", 1999)],
        details={
            "603": MovieMetadata(
                provider="tmdb",
                provider_id="603",
                title="The Matrix",
                year=1999,
                runtime_seconds=8160,
            )
        },
        images=[
            ProviderImage(
                kind="poster",
                source_url="https://images.test/t/p/w780/p.jpg",
                is_primary=True,
            )
        ],
    )
    artwork = ImagePipeline(settings, db_sessionmaker, client=client)
    matcher = MetadataMatcher(settings, db_sessionmaker, providers=[provider], artwork=artwork)

    stats = await matcher.match_pending()

    assert stats.images_stored == 1
    rows = await _images(db_sessionmaker)
    assert len(rows) == 1
    assert rows[0].item_id == item_id
    assert rows[0].provider == "tmdb"
    await client.aclose()
