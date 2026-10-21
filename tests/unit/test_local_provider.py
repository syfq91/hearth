"""Tests for the local NFO provider."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.services.metadata.base import ProviderError
from app.services.metadata.local import LocalProvider

NFO = """<?xml version="1.0" encoding="UTF-8"?>
<movie>
  <title>The Matrix</title>
  <originaltitle>The Matrix</originaltitle>
  <sorttitle>Matrix</sorttitle>
  <year>1999</year>
  <releasedate>1999-03-31</releasedate>
  <plot>A hacker learns the truth.</plot>
  <tagline>Free your mind.</tagline>
  <runtime>136</runtime>
  <ratings><rating name="tmdb" max="10"><value>8.2</value></rating></ratings>
  <genre>Action</genre>
  <genre>Science Fiction</genre>
  <uniqueid type="tmdb" default="true">603</uniqueid>
  <uniqueid type="imdb">tt0133093</uniqueid>
  <director>Lana Wachowski</director>
  <director>Lilly Wachowski</director>
  <writer>Lana Wachowski</writer>
  <actor><name>Keanu Reeves</name><role>Neo</role><order>0</order></actor>
  <actor><name>Carrie-Anne Moss</name><role>Trinity</role><order>1</order></actor>
  <thumb aspect="poster">poster.jpg</thumb>
  <fanart><thumb>fanart.jpg</thumb></fanart>
</movie>
"""


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / "movies" / "The Matrix (1999)"
    folder.mkdir(parents=True)
    video = folder / "The Matrix (1999).mkv"
    video.write_bytes(b"video")
    (folder / "poster.jpg").write_bytes(b"poster")
    (folder / "fanart.jpg").write_bytes(b"fanart")
    (folder / "The Matrix (1999).srt").write_text("1\n", encoding="utf-8")
    nfo = folder / "movie.nfo"
    nfo.write_text(NFO, encoding="utf-8")
    return video, nfo


def test_parse_nfo_maps_details_images_and_subtitles(tmp_path: Path) -> None:
    video, nfo = _fixture(tmp_path)

    details = LocalProvider().find_movie(video)

    assert details is not None
    assert details.provider == "local"
    assert details.provider_id == str(nfo)
    assert details.title == "The Matrix"
    assert details.original_title == "The Matrix"
    assert details.year == 1999
    assert details.release_date == "1999-03-31"
    assert details.overview == "A hacker learns the truth."
    assert details.tagline == "Free your mind."
    assert details.runtime_seconds == 136 * 60
    assert details.rating == 8.2
    assert details.genres == ("Action", "Science Fiction")
    assert details.provider_ids == {"tmdb": "603", "imdb": "tt0133093"}
    assert {image.kind for image in details.images} == {"poster", "backdrop"}
    assert all(image.file_path and Path(image.file_path).is_file() for image in details.images)
    assert details.sidecar_subtitles == (str(video.with_suffix(".srt")),)

    credits = {(credit.name, credit.role) for credit in details.credits}
    assert ("Lana Wachowski", "director") in credits
    assert ("Keanu Reeves", "cast") in credits
    assert next(c for c in details.credits if c.name == "Keanu Reeves").character == "Neo"


async def test_provider_interface_search_details_and_images(tmp_path: Path) -> None:
    video, nfo = _fixture(tmp_path)
    provider = LocalProvider()

    results = await provider.search(str(video), year=1999)
    assert len(results) == 1
    assert results[0].provider_id == str(nfo)

    details = await provider.fetch_details(str(nfo))
    assert details is not None and details.title == "The Matrix"

    images = await provider.fetch_images(str(nfo))
    assert {image.kind for image in images} == {"poster", "backdrop"}
    assert images[0].is_primary is True


def test_missing_nfo_returns_none(tmp_path: Path) -> None:
    video = tmp_path / "No Metadata (2020).mkv"
    video.write_bytes(b"video")

    assert LocalProvider().find_movie(video) is None


def test_malformed_nfo_raises_provider_error(tmp_path: Path) -> None:
    video, nfo = _fixture(tmp_path)
    nfo.write_text("<movie><title>Broken", encoding="utf-8")

    with pytest.raises(ProviderError, match="Invalid NFO"):
        LocalProvider().find_movie(video)


def test_remote_nfo_artwork_is_not_trusted(tmp_path: Path) -> None:
    video, nfo = _fixture(tmp_path)
    root = ET.parse(nfo).getroot()
    root.find("thumb").text = "https://evil.example/poster.jpg"  # type: ignore[union-attr]
    ET.ElementTree(root).write(nfo, encoding="utf-8", xml_declaration=True)

    details = LocalProvider().find_movie(video)
    assert details is not None
    assert all(
        image.file_path is not None and Path(image.file_path).is_file() for image in details.images
    )


def test_nfo_only_unique_id_uses_fallback_title(tmp_path: Path) -> None:
    video, nfo = _fixture(tmp_path)
    nfo.write_text('<movie><uniqueid type="tmdb">603</uniqueid></movie>', encoding="utf-8")

    details = LocalProvider().find_movie(video)
    assert details is not None
    assert details.title == "The Matrix (1999)"
    assert details.provider_ids == {"tmdb": "603"}
