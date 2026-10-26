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


TVSHOW_NFO = """<?xml version="1.0" encoding="UTF-8"?>
<tvshow>
  <title>Chernobyl</title>
  <originaltitle>Chernobyl</originaltitle>
  <year>2019</year>
  <premiered>2019-05-06</premiered>
  <plot>An explosion at the Chernobyl plant becomes a catastrophe.</plot>
  <status>Ended</status>
  <studio>HBO</studio>
  <rating>9.4</rating>
  <genre>Drama</genre>
  <genre>History</genre>
  <uniqueid type="tmdb">87108</uniqueid>
  <uniqueid type="imdb">tt8772298</uniqueid>
  <actor><name>Jared Harris</name><role>Valery Legasov</role><order>0</order></actor>
  <actor><name>Stellan Skarsgård</name><role>Boris Shcherbina</role><order>1</order></actor>
</tvshow>
"""

EPISODE_NFO = """<?xml version="1.0" encoding="UTF-8"?>
<episodedetails>
  <title>1:23:45</title>
  <season>1</season>
  <episode>1</episode>
  <aired>2019-05-06</aired>
  <plot>Plant workers risk their lives to control a catastrophic explosion.</plot>
  <runtime>59</runtime>
  <rating>9.0</rating>
  <uniqueid type="tmdb">1748281</uniqueid>
  <director>Johan Renck</director>
  <writer>Craig Mazin</writer>
  <actor><name>Adam Nagaitis</name><role>Vasily Ignatenko</role><order>0</order></actor>
</episodedetails>
"""


def _tv_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    series_dir = tmp_path / "tv" / "Chernobyl"
    season_dir = series_dir / "Season 1"
    season_dir.mkdir(parents=True)

    tv_nfo = series_dir / "tvshow.nfo"
    tv_nfo.write_text(TVSHOW_NFO, encoding="utf-8")
    (series_dir / "poster.jpg").write_bytes(b"poster")
    (series_dir / "fanart.jpg").write_bytes(b"fanart")
    (series_dir / "season01.jpg").write_bytes(b"season01_poster")

    ep_video = season_dir / "Chernobyl.S01E01.mkv"
    ep_video.write_bytes(b"ep_video")
    (season_dir / "Chernobyl.S01E01-thumb.jpg").write_bytes(b"ep_thumb")

    ep_nfo = season_dir / "Chernobyl.S01E01.nfo"
    ep_nfo.write_text(EPISODE_NFO, encoding="utf-8")

    return series_dir, tv_nfo, ep_video, ep_nfo


def test_parse_tvshow_nfo(tmp_path: Path) -> None:
    series_dir, tv_nfo, ep_video, ep_nfo = _tv_fixture(tmp_path)
    provider = LocalProvider()

    series_meta = provider.find_series(series_dir)
    assert series_meta is not None
    assert series_meta.title == "Chernobyl"
    assert series_meta.original_title == "Chernobyl"
    assert series_meta.year == 2019
    assert series_meta.first_air_date == "2019-05-06"
    assert "Chernobyl plant" in (series_meta.overview or "")
    assert series_meta.status == "Ended"
    assert series_meta.network == "HBO"
    assert series_meta.rating == 9.4
    assert series_meta.genres == ("Drama", "History")
    assert series_meta.provider_ids == {"tmdb": "87108", "imdb": "tt8772298"}
    assert len(series_meta.credits) == 2
    assert series_meta.credits[0].name == "Jared Harris"
    assert series_meta.credits[0].character == "Valery Legasov"
    assert {image.kind for image in series_meta.images} == {"poster", "backdrop"}


def test_parse_episode_nfo(tmp_path: Path) -> None:
    series_dir, tv_nfo, ep_video, ep_nfo = _tv_fixture(tmp_path)
    provider = LocalProvider()

    ep_meta = provider.find_episode(ep_video)
    assert ep_meta is not None
    assert ep_meta.title == "1:23:45"
    assert ep_meta.season_number == 1
    assert ep_meta.episode_number == 1
    assert ep_meta.air_date == "2019-05-06"
    assert "Plant workers" in (ep_meta.overview or "")
    assert ep_meta.runtime_seconds == 59 * 60
    assert ep_meta.rating == 9.0
    assert ep_meta.provider_ids == {"tmdb": "1748281"}
    credits = {(c.name, c.role) for c in ep_meta.credits}
    assert ("Johan Renck", "director") in credits
    assert ("Craig Mazin", "writer") in credits
    assert ("Adam Nagaitis", "cast") in credits
    assert len(ep_meta.images) >= 1
    assert ep_meta.images[0].kind in ("still", "thumbnail")


async def test_local_provider_tv_interfaces(tmp_path: Path) -> None:
    series_dir, tv_nfo, ep_video, ep_nfo = _tv_fixture(tmp_path)
    provider = LocalProvider()

    # Search TV
    results = await provider.search_tv(str(series_dir))
    assert len(results) == 1
    assert results[0].title == "Chernobyl"
    assert results[0].provider_id == str(tv_nfo)

    # Fetch TV details
    details = await provider.fetch_tv_details(str(tv_nfo))
    assert details is not None
    assert details.title == "Chernobyl"

    # Fetch Season
    season = await provider.fetch_tv_season(str(tv_nfo), 1)
    assert season is not None
    assert season.season_number == 1
    assert len(season.images) >= 1
    assert season.images[0].kind == "poster"
    assert len(season.episodes) == 1
    assert season.episodes[0].episode_number == 1
    assert season.episodes[0].title == "1:23:45"

    # Fetch Episode
    ep = await provider.fetch_tv_episode(str(tv_nfo), 1, 1)
    assert ep is not None
    assert ep.title == "1:23:45"
