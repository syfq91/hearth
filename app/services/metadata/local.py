"""Local metadata from Kodi-style NFO files and sidecar artwork."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

from app.services.metadata.base import (
    EpisodeMetadata,
    MetadataProvider,
    MovieMetadata,
    PersonCredit,
    ProviderError,
    ProviderImage,
    SearchResult,
    SeasonMetadata,
    SeriesMetadata,
)
from app.services.scanner.walker import find_sidecar_subtitles

logger = logging.getLogger("hearth.metadata.local")

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_POSTER_NAMES = ("poster", "folder", "cover")
_BACKDROP_NAMES = ("fanart", "backdrop", "background")


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold()


def _text(root: ET.Element, name: str) -> str | None:
    for element in root.iter():
        if _tag(element) == name:
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _direct_text(root: ET.Element, name: str) -> str | None:
    for element in root:
        if _tag(element) == name:
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _all_text(root: ET.Element, name: str) -> list[str]:
    values: list[str] = []
    for element in root.iter():
        if _tag(element) == name:
            value = (element.text or "").strip()
            if value:
                values.append(value)
    return values


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _year_from_date(value: str | None) -> int | None:
    if not value or len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:4])


def _first_existing(folder: Path, stems: list[str], names: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for stem in stems:
        for name in names:
            for extension in _IMAGE_EXTENSIONS:
                candidate = (
                    folder / f"{stem}-{name}{extension}" if stem else folder / f"{name}{extension}"
                )
                if candidate.is_file():
                    found.append(candidate)
    return found


class LocalProvider(MetadataProvider):
    """Read NFO files and sidecar images next to a movie file."""

    name = "local"

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        path = Path(query)
        if not path.exists():
            return []
        details = self.find_movie(path)
        if details is None:
            return []
        return [
            SearchResult(
                provider=self.name,
                provider_id=details.provider_id,
                title=details.title,
                year=details.year,
                overview=details.overview,
                popularity=1.0,
            )
        ]

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        path = Path(provider_id)
        if path.is_file() and path.suffix.casefold() == ".nfo":
            return self._parse_nfo(path, path.parent)
        if path.exists():
            return self.find_movie(path)
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        details = await self.fetch_details(provider_id)
        return list(details.images) if details else []

    def find_movie(self, path: Path | str) -> MovieMetadata | None:
        """Find and parse an NFO for a video file, folder or NFO path."""
        target = Path(path)
        nfo = self._find_nfo(target)
        if nfo is None:
            return None
        video = target if target.is_file() and target.suffix.casefold() != ".nfo" else None
        movie_root = target if target.is_dir() else target.parent
        return self._parse_nfo(nfo, movie_root, video=video)

    @staticmethod
    def _find_nfo(path: Path) -> Path | None:
        if path.is_file() and path.suffix.casefold() == ".nfo":
            return path if path.is_file() else None
        folder = path if path.is_dir() else path.parent
        stem = path.stem if path.is_file() else folder.name
        candidates = [folder / f"{stem}.nfo", folder / "movie.nfo", folder / f"{folder.name}.nfo"]
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def _parse_nfo(
        self, nfo: Path, movie_root: Path, *, video: Path | None = None
    ) -> MovieMetadata | None:
        try:
            root = ET.parse(nfo).getroot()
        except (ET.ParseError, OSError) as exc:
            raise ProviderError(f"Invalid NFO file {nfo.name}: {exc}") from None

        title = _text(root, "title") or _text(root, "originaltitle")
        year = _as_int(_text(root, "year"))
        release_date = _text(root, "releasedate") or _text(root, "premiered")
        year = year or _year_from_date(release_date)

        unique_ids: dict[str, str] = {}
        for element in root.iter():
            if _tag(element) != "uniqueid":
                continue
            provider = (element.attrib.get("type") or "").casefold()
            value = (element.text or "").strip()
            if provider and value:
                unique_ids[provider] = value
        tmdb_id = _direct_text(root, "tmdbid") or unique_ids.get("tmdb")
        imdb_id = unique_ids.get("imdb") or _direct_text(root, "imdbid")
        if tmdb_id:
            unique_ids["tmdb"] = tmdb_id
        if imdb_id:
            unique_ids["imdb"] = imdb_id

        if not any((title, year, unique_ids, _text(root, "plot"))):
            return None

        runtime_minutes = _as_int(_text(root, "runtime"))
        rating = _as_float(_text(root, "rating")) or _as_float(_text(root, "value"))

        credits: list[PersonCredit] = []
        ordering = 0
        for director in _all_text(root, "director"):
            credits.append(PersonCredit(name=director, role="director", ordering=ordering))
            ordering += 1
        for writer in _all_text(root, "writer"):
            credits.append(PersonCredit(name=writer, role="writer", ordering=ordering))
            ordering += 1
        for actor in root.iter():
            if _tag(actor) != "actor":
                continue
            name = _text(actor, "name")
            if not name:
                continue
            credits.append(
                PersonCredit(
                    name=name,
                    role="cast",
                    character=_text(actor, "role"),
                    ordering=_as_int(_text(actor, "order")) or ordering,
                    provider_id=_text(actor, "tmdbid"),
                )
            )
            ordering += 1

        images = self._images(root, nfo, movie_root, video=video)
        subtitles = find_sidecar_subtitles(video) if video is not None else []

        return MovieMetadata(
            provider=self.name,
            provider_id=str(nfo),
            title=title or (video.stem if video is not None else movie_root.stem),
            year=year,
            original_title=_text(root, "originaltitle"),
            release_date=release_date,
            overview=_text(root, "plot") or _text(root, "outline"),
            tagline=_text(root, "tagline"),
            runtime_seconds=runtime_minutes * 60 if runtime_minutes else None,
            rating=rating,
            genres=tuple(dict.fromkeys(_all_text(root, "genre"))),
            credits=tuple(credits),
            provider_ids=unique_ids,
            images=tuple(images),
            sidecar_subtitles=tuple(subtitles),
            raw={"nfo": str(nfo)},
        )

    def _images(
        self,
        root: ET.Element,
        nfo: Path,
        movie_root: Path,
        *,
        video: Path | None = None,
    ) -> list[ProviderImage]:
        folder = nfo.parent
        found: dict[str, ProviderImage] = {}
        fanart_thumbs = {
            id(element)
            for fanart in root.iter()
            if _tag(fanart) == "fanart"
            for element in fanart.iter()
            if _tag(element) == "thumb"
        }

        def add(kind: str, path: Path) -> None:
            if not path.is_file():
                return
            key = str(path.resolve())
            if key in found:
                return
            found[key] = ProviderImage(
                kind=kind,
                file_path=key,
                is_primary=not any(image.kind == kind for image in found.values()),
            )

        # NFO references can be local paths; remote thumbnails are ignored so a
        # malicious NFO cannot make Hearth fetch an arbitrary URL.
        for element in root.iter():
            tag = _tag(element)
            text = (element.text or "").strip()
            if not text:
                continue
            kind: str | None = None
            if tag == "poster" or (tag == "thumb" and id(element) not in fanart_thumbs):
                kind = "poster"
            elif tag == "thumb" and id(element) in fanart_thumbs or tag == "fanart":
                kind = "backdrop"
            else:
                continue
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = folder / candidate
            add(kind, candidate)

        stems = [video.stem if video is not None else nfo.parent.name, ""]
        for path in _first_existing(folder, stems, _POSTER_NAMES):
            add("poster", path)
        for path in _first_existing(folder, stems, _BACKDROP_NAMES):
            add("backdrop", path)

        return list(found.values())

    async def search_tv(self, query: str, year: int | None = None) -> list[SearchResult]:
        path = Path(query)
        if not path.exists():
            return []
        details = self.find_series(path)
        if details is None:
            return []
        return [
            SearchResult(
                provider=self.name,
                provider_id=details.provider_id,
                title=details.title,
                year=details.year,
                overview=details.overview,
                popularity=1.0,
            )
        ]

    async def fetch_tv_details(self, series_id: str) -> SeriesMetadata | None:
        path = Path(series_id)
        if path.is_file() and path.suffix.casefold() == ".nfo":
            return self._parse_tvshow_nfo(path, path.parent)
        if path.exists():
            return self.find_series(path)
        return None

    async def fetch_tv_images(self, series_id: str) -> list[ProviderImage]:
        details = await self.fetch_tv_details(series_id)
        return list(details.images) if details else []

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        nfo_path = Path(series_id)
        series_dir = nfo_path if nfo_path.is_dir() else nfo_path.parent
        season_poster = self._find_season_poster(series_dir, season_number)
        images: list[ProviderImage] = []
        if season_poster is not None:
            images.append(
                ProviderImage(
                    kind="poster",
                    file_path=str(season_poster.resolve()),
                    is_primary=True,
                )
            )

        episodes: list[EpisodeMetadata] = []
        for candidate in sorted(series_dir.glob("**/*.nfo")):
            if candidate.name.casefold() in ("tvshow.nfo", "movie.nfo"):
                continue
            try:
                ep_meta = self._parse_episode_nfo(candidate)
                if ep_meta and ep_meta.season_number == season_number:
                    episodes.append(ep_meta)
            except ProviderError:
                continue

        episodes.sort(key=lambda ep: ep.episode_number or 0)
        return SeasonMetadata(
            provider=self.name,
            provider_id=str(series_dir),
            series_provider_id=series_id,
            season_number=season_number,
            title="Specials" if season_number == 0 else f"Season {season_number}",
            images=tuple(images),
            episodes=tuple(episodes),
        )

    async def fetch_tv_episode(
        self, series_id: str, season_number: int, episode_number: int
    ) -> EpisodeMetadata | None:
        season = await self.fetch_tv_season(series_id, season_number)
        if season:
            for ep in season.episodes:
                if ep.episode_number == episode_number:
                    return ep
        return None

    def find_series(self, path: Path | str) -> SeriesMetadata | None:
        """Find and parse tvshow.nfo for a directory or nfo path."""
        target = Path(path)
        if target.is_file() and target.name.casefold() == "tvshow.nfo":
            return self._parse_tvshow_nfo(target, target.parent)
        series_dir = target if target.is_dir() else target.parent
        nfo = series_dir / "tvshow.nfo"
        if not nfo.is_file() and series_dir.parent.is_dir():
            parent_nfo = series_dir.parent / "tvshow.nfo"
            if parent_nfo.is_file():
                nfo = parent_nfo
                series_dir = series_dir.parent
        if nfo.is_file():
            return self._parse_tvshow_nfo(nfo, series_dir)
        return None

    def find_episode(self, path: Path | str) -> EpisodeMetadata | None:
        """Find and parse episode nfo for a video file or nfo path."""
        target = Path(path)
        if target.is_file() and target.suffix.casefold() == ".nfo":
            return self._parse_episode_nfo(target)
        if not target.is_file():
            return None
        folder = target.parent
        candidates = [
            folder / f"{target.stem}.nfo",
            folder / "episode.nfo",
        ]
        nfo = next((c for c in candidates if c.is_file()), None)
        if nfo is not None:
            return self._parse_episode_nfo(nfo, video=target)
        return None

    def _parse_tvshow_nfo(self, nfo: Path, series_dir: Path) -> SeriesMetadata | None:
        try:
            root = ET.parse(nfo).getroot()
        except (ET.ParseError, OSError) as exc:
            raise ProviderError(f"Invalid NFO file {nfo.name}: {exc}") from None

        title = _text(root, "title") or _text(root, "originaltitle") or series_dir.name
        year = _as_int(_text(root, "year"))
        first_air_date = _text(root, "premiered") or _text(root, "releasedate")
        year = year or _year_from_date(first_air_date)

        unique_ids: dict[str, str] = {}
        for element in root.iter():
            if _tag(element) != "uniqueid":
                continue
            provider = (element.attrib.get("type") or "").casefold()
            value = (element.text or "").strip()
            if provider and value:
                unique_ids[provider] = value
        tmdb_id = _direct_text(root, "tmdbid") or unique_ids.get("tmdb")
        imdb_id = unique_ids.get("imdb") or _direct_text(root, "imdbid")
        tvdb_id = unique_ids.get("tvdb") or _direct_text(root, "tvdbid")
        if tmdb_id:
            unique_ids["tmdb"] = tmdb_id
        if imdb_id:
            unique_ids["imdb"] = imdb_id
        if tvdb_id:
            unique_ids["tvdb"] = tvdb_id

        credits: list[PersonCredit] = []
        ordering = 0
        for actor in root.iter():
            if _tag(actor) != "actor":
                continue
            name = _text(actor, "name")
            if not name:
                continue
            credits.append(
                PersonCredit(
                    name=name,
                    role="cast",
                    character=_text(actor, "role"),
                    ordering=_as_int(_text(actor, "order")) or ordering,
                    provider_id=_text(actor, "tmdbid"),
                )
            )
            ordering += 1

        images = self._tv_images(root, nfo, series_dir)

        return SeriesMetadata(
            provider=self.name,
            provider_id=str(nfo),
            title=title,
            year=year,
            original_title=_text(root, "originaltitle"),
            first_air_date=first_air_date,
            overview=_text(root, "plot") or _text(root, "outline"),
            status=_text(root, "status"),
            network=_text(root, "studio") or _text(root, "network"),
            rating=_as_float(_text(root, "rating")) or _as_float(_text(root, "value")),
            genres=tuple(dict.fromkeys(_all_text(root, "genre"))),
            credits=tuple(credits),
            provider_ids=unique_ids,
            images=tuple(images),
            raw={"nfo": str(nfo)},
        )

    def _parse_episode_nfo(self, nfo: Path, *, video: Path | None = None) -> EpisodeMetadata | None:
        try:
            root = ET.parse(nfo).getroot()
        except (ET.ParseError, OSError) as exc:
            raise ProviderError(f"Invalid NFO file {nfo.name}: {exc}") from None

        title = _text(root, "title") or (video.stem if video else nfo.stem)
        season_number = _as_int(_text(root, "season"))
        episode_number = _as_int(_text(root, "episode"))
        air_date = _text(root, "aired") or _text(root, "premiered")
        runtime_val = _as_int(_text(root, "runtime"))
        runtime_seconds = (runtime_val * 60) if runtime_val and runtime_val < 300 else runtime_val

        unique_ids: dict[str, str] = {}
        for element in root.iter():
            if _tag(element) != "uniqueid":
                continue
            provider = (element.attrib.get("type") or "").casefold()
            value = (element.text or "").strip()
            if provider and value:
                unique_ids[provider] = value
        tmdb_id = _direct_text(root, "tmdbid") or unique_ids.get("tmdb")
        imdb_id = unique_ids.get("imdb") or _direct_text(root, "imdbid")
        if tmdb_id:
            unique_ids["tmdb"] = tmdb_id
        if imdb_id:
            unique_ids["imdb"] = imdb_id

        credits: list[PersonCredit] = []
        ordering = 0
        for director in _all_text(root, "director"):
            credits.append(PersonCredit(name=director, role="director", ordering=ordering))
            ordering += 1
        for writer in _all_text(root, "writer"):
            credits.append(PersonCredit(name=writer, role="writer", ordering=ordering))
            ordering += 1
        for actor in root.iter():
            if _tag(actor) != "actor":
                continue
            name = _text(actor, "name")
            if not name:
                continue
            credits.append(
                PersonCredit(
                    name=name,
                    role="cast",
                    character=_text(actor, "role"),
                    ordering=_as_int(_text(actor, "order")) or ordering,
                    provider_id=_text(actor, "tmdbid"),
                )
            )
            ordering += 1

        folder = nfo.parent
        thumb_path = self._find_episode_thumb(folder, video.stem if video else nfo.stem)
        images: list[ProviderImage] = []
        if thumb_path is not None:
            images.append(
                ProviderImage(
                    kind="still",
                    file_path=str(thumb_path.resolve()),
                    is_primary=True,
                )
            )

        return EpisodeMetadata(
            provider=self.name,
            provider_id=str(nfo),
            season_number=season_number,
            episode_number=episode_number,
            title=title,
            overview=_text(root, "plot") or _text(root, "outline"),
            air_date=air_date,
            runtime_seconds=runtime_seconds,
            rating=_as_float(_text(root, "rating")) or _as_float(_text(root, "value")),
            credits=tuple(credits),
            provider_ids=unique_ids,
            images=tuple(images),
            raw={"nfo": str(nfo)},
        )

    def _find_episode_thumb(self, folder: Path, stem: str) -> Path | None:
        for suffix in ("-thumb", "-still", "-screenshot", ""):
            for ext in _IMAGE_EXTENSIONS:
                candidate = folder / f"{stem}{suffix}{ext}"
                if candidate.is_file():
                    return candidate
        return None

    def _find_season_poster(self, series_dir: Path, season_number: int) -> Path | None:
        patterns = [
            f"season{season_number:02d}",
            f"season{season_number:02d}-poster",
            f"season{season_number}",
            f"season {season_number}",
            f"season {season_number:02d}",
        ]
        if season_number == 0:
            patterns.extend(["season-specials", "season-specials-poster", "specials"])
        for pat in patterns:
            for ext in _IMAGE_EXTENSIONS:
                candidate = series_dir / f"{pat}{ext}"
                if candidate.is_file():
                    return candidate

        season_folders = [
            series_dir / f"Season {season_number}",
            series_dir / f"Season {season_number:02d}",
            series_dir / f"Season{season_number}",
            series_dir / f"Season{season_number:02d}",
        ]
        if season_number == 0:
            season_folders.append(series_dir / "Specials")
        for s_folder in season_folders:
            if s_folder.is_dir():
                for name in _POSTER_NAMES:
                    for ext in _IMAGE_EXTENSIONS:
                        candidate = s_folder / f"{name}{ext}"
                        if candidate.is_file():
                            return candidate
        return None

    def _tv_images(
        self,
        root: ET.Element,
        nfo: Path,
        series_dir: Path,
    ) -> list[ProviderImage]:
        folder = series_dir
        found: dict[str, ProviderImage] = {}

        def add(kind: str, path: Path) -> None:
            if not path.is_file():
                return
            key = str(path.resolve())
            if key in found:
                return
            found[key] = ProviderImage(
                kind=kind,
                file_path=key,
                is_primary=not any(image.kind == kind for image in found.values()),
            )

        stems = [series_dir.name, "show", ""]
        for path in _first_existing(folder, stems, _POSTER_NAMES):
            add("poster", path)
        for path in _first_existing(folder, stems, _BACKDROP_NAMES):
            add("backdrop", path)

        return list(found.values())
