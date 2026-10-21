"""Unit tests for scanning support: walking, hashing and a simple parser."""

import os
from pathlib import Path

from app.services.media.titles import normalize_title, sort_title
from app.services.scanner.hashing import partial_hash
from app.services.scanner.parsing import parse_movie_path
from app.services.scanner.walker import scope_dirs, walk_movie_files


def _touch(root: Path, relative: str, content: bytes = b"media") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_walk_filters_extensions_samples_hidden_and_nomedia(tmp_path: Path) -> None:
    root = tmp_path / "movies"
    wanted_a = _touch(root, "The Matrix (1999)/The Matrix.mkv")
    wanted_b = _touch(root, "Arrival (2016).mp4")
    _touch(root, "notes.txt")
    _touch(root, "Movie.2020.sample.mkv")
    _touch(root, ".hidden.mkv")
    _touch(root, "Extras/deleted-scene.mkv")
    _touch(root, "No Media/.nomedia", b"")
    _touch(root, "No Media/ignored.mkv")
    _touch(root, "subdir/Another Movie (2010).webm")

    found = walk_movie_files([str(root)])

    assert [Path(item.path) for item in found] == [
        wanted_b,
        wanted_a,
        root / "subdir" / "Another Movie (2010).webm",
    ]
    assert all(item.size_bytes > 0 for item in found)


def test_scope_dirs_turns_files_into_parents_and_climbs_missing_paths(tmp_path: Path) -> None:
    root = tmp_path / "movies"
    file_path = _touch(root, "Film (2020)/Film.mkv")
    file_path.unlink()
    (root / "Film (2020)").rmdir()

    scopes = scope_dirs([str(root)], [str(root / "Film (2020)" / "Film.mkv")])
    assert scopes == [str(root)]

    scopes = scope_dirs([str(root)], [str(file_path)])
    assert scopes == [str(root)]


def test_scope_dirs_ignores_paths_outside_roots(tmp_path: Path) -> None:
    root = tmp_path / "movies"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert scope_dirs([str(root)], [str(outside)]) == []


def test_partial_hash_changes_with_content_and_survives_copy(tmp_path: Path) -> None:
    original = _touch(tmp_path, "a.mkv", b"a" * 200_000)
    copy = _touch(tmp_path, "b.mkv", b"a" * 200_000)
    assert partial_hash(original) == partial_hash(copy)

    with original.open("ab") as handle:
        handle.write(b"tail")
    assert partial_hash(original) != partial_hash(copy)

    same_size = _touch(tmp_path, "c.mkv", b"a" * 200_000)
    with same_size.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"b")
    assert partial_hash(same_size) != partial_hash(copy)


def test_titles_ignore_articles_accents_and_punctuation() -> None:
    assert normalize_title("Amélie!") == "amelie"
    assert sort_title("The Matrix") == "matrix"
    assert sort_title("A Quiet Place") == "quiet place"
    assert sort_title("An Education") == "education"


def test_simple_parser_uses_folder_year_and_release_tokens(tmp_path: Path) -> None:
    folder = tmp_path / "The Matrix (1999)"
    folder.mkdir()
    parsed = parse_movie_path(folder / "The.Matrix.1080p.BluRay.x264.mkv")
    assert parsed.title == "The Matrix"
    assert parsed.year == 1999
    assert parsed.sort_title == "matrix"

    no_year = parse_movie_path(folder / "The.Matrix.mkv")
    assert no_year.title == "The Matrix"
    assert no_year.year == 1999

    edition = parse_movie_path(tmp_path / "Blade Runner 2049 (2017) Director's Cut Part 2.mkv")
    assert edition.title == "Blade Runner 2049"
    assert edition.year == 2017
    assert edition.edition == "Director's Cut"
    assert edition.part == 2


def test_parser_handles_dots_underscores_and_bad_years(tmp_path: Path) -> None:
    parsed = parse_movie_path(tmp_path / "2001_A_Space_Odyssey_1968.mkv")
    assert parsed.title == "2001 A Space Odyssey"
    assert parsed.year == 1968

    parsed = parse_movie_path(tmp_path / "Movie_Without_Year.mkv")
    assert parsed.title == "Movie Without Year"
    assert parsed.year is None

    # A numeric title that is not a plausible year must not be stripped.
    parsed = parse_movie_path(tmp_path / "1984.mkv")
    assert parsed.title == "1984"
    assert parsed.year is None


def test_sidecar_scope_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "movies"
    root.mkdir()
    outside = tmp_path / "outside"
    _touch(outside, "Escaped (2021).mkv")
    os.symlink(outside, root / "linked")

    assert walk_movie_files([str(root)]) == []
