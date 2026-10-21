"""Filesystem walking with Hearth's ignore rules."""

import glob
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = frozenset(
    {
        ".mkv",
        ".mp4",
        ".m4v",
        ".avi",
        ".mov",
        ".ts",
        ".m2ts",
        ".webm",
        ".wmv",
        ".flv",
        ".mpg",
        ".mpeg",
        ".ogv",
    }
)

IGNORED_DIRECTORIES = frozenset(
    {
        "extras",
        "extra",
        "featurettes",
        "behind the scenes",
        "behind-the-scenes",
        "samples",
        "sample",
        "subtitles",
        "subs",
        "@eadir",
        "#recycle",
        ".trash",
        ".recycle",
        "lost+found",
    }
)

_SAMPLE = re.compile(r"(?:^|[.\s_-])sample(?:[.\s_-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ScannedFile:
    """One media file observed on disk."""

    path: str
    size_bytes: int
    mtime: float


def _normalize_root(root: str | Path) -> str:
    return os.path.abspath(os.path.normpath(os.fspath(root)))


def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent + os.sep)


def scope_dirs(roots: Sequence[str], targets: Sequence[str] | None) -> list[str]:
    """Turn changed file paths into directories that should be walked.

    Missing targets climb to their nearest existing ancestor, never above the
    library root. Nested scopes are collapsed so no directory is walked twice.
    """
    if targets is None:
        return [_normalize_root(root) for root in roots]

    scopes: list[str] = []
    for raw_target in targets:
        target = _normalize_root(raw_target)
        root = next((r for r in map(_normalize_root, roots) if _inside(target, r)), None)
        if root is None:
            continue

        candidate = target if os.path.isdir(target) else os.path.dirname(target)
        while not os.path.isdir(candidate) and candidate != root:
            candidate = os.path.dirname(candidate)
        if not os.path.isdir(candidate):
            continue
        scopes.append(candidate)

    collapsed: list[str] = []
    for scope in sorted(set(scopes)):
        if not any(_inside(scope, parent) for parent in collapsed):
            collapsed.append(scope)
    return collapsed


def walk_movie_files(
    roots: Sequence[str],
    *,
    start_dirs: Sequence[str] | None = None,
) -> list[ScannedFile]:
    """Walk movie roots and return supported, non-ignored files.

    Symlinked directories are not followed, hidden names are skipped, and a
    directory containing ``.nomedia`` is skipped entirely. Results are sorted
    for deterministic scan statistics and tests.
    """
    search_dirs = (
        scope_dirs(roots, start_dirs)
        if start_dirs is not None
        else [_normalize_root(root) for root in roots]
    )
    files: list[ScannedFile] = []

    for start in search_dirs:
        if not os.path.isdir(start):
            continue
        for dirpath, dirnames, filenames in os.walk(start, topdown=True, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name.casefold() not in IGNORED_DIRECTORIES
                and not name.startswith(".")
                and not os.path.islink(os.path.join(dirpath, name))
            ]
            if ".nomedia" in filenames:
                dirnames.clear()
                continue

            for filename in filenames:
                if filename.startswith("."):
                    continue
                extension = os.path.splitext(filename)[1].casefold()
                if extension not in VIDEO_EXTENSIONS:
                    continue
                if _SAMPLE.search(os.path.splitext(filename)[0]):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                if not os.path.isfile(path) or os.path.islink(path):
                    continue
                files.append(ScannedFile(path=path, size_bytes=stat.st_size, mtime=stat.st_mtime))

    files.sort(key=lambda item: item.path)
    return files


def find_sidecar_subtitles(path: Path | str) -> list[str]:
    """Return subtitle sidecars sharing a video's stem.

    Both ``Movie.srt`` and language-tagged files such as ``Movie.en.srt`` are
    supported; unrelated files in the folder are ignored.
    """
    target = Path(path)
    extensions = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
    found = {
        str(candidate)
        for extension in extensions
        if (candidate := target.with_suffix(extension)).is_file()
    }
    for candidate in target.parent.glob(f"{glob.escape(target.stem)}.*"):
        if candidate.is_file() and candidate.suffix.casefold() in extensions:
            found.add(str(candidate))
    return sorted(found)
