#!/usr/bin/env bash
# Regenerate the committed e2e media fixtures under web/e2e/fixtures/media/.
#
# - The EPUB is plain XML in a zip: generated with Python (stdlib only).
# - The video and audio come from ffmpeg inside the Hearth image itself, so the
#   codecs match what the scanner and the player see in the e2e container.
#
# The fixtures are committed; this script only needs to run when they change.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$REPO_ROOT/web/e2e/fixtures"
IMAGE="${HEARTH_IMAGE:-hearth:e2e}"

command -v docker >/dev/null || { echo "docker is required (ffmpeg runs in $IMAGE)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

mkdir -p "$FIXTURES/media/movies" "$FIXTURES/media/music/Neon Hollow" "$FIXTURES/media/books"

# --- EPUB: a tiny valid EPUB 3 file (no external tools) ---------------------
python3 - "$FIXTURES/media/books/Field Notes from Tomorrow.epub" <<'PY'
import sys, zipfile

path = sys.argv[1]
chapters = [
    ("c1", "Chapter One: The Quiet Coast",
     "<p>The fog came in over the harbour at four in the morning, and by breakfast "
     "every familiar silhouette had been edited out of the world.</p>"),
    ("c2", "Chapter Two: Field Notes",
     "<p>Note to self: the tide table is a promise, not a schedule. Also, the "
     "radio speaks in futures tense after midnight.</p>"),
    ("c3", "Chapter Three: Return",
     "<p>When the fog lifted, the lighthouse was exactly where it had always been. "
     "That was the strangest part.</p>"),
]

with zipfile.ZipFile(path, "w") as z:
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr(
        "META-INF/container.xml",
        '<?xml version="1.0"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>',
    )
    manifest = "".join(
        f'<item id="{cid}" href="{cid}.xhtml" media-type="application/xhtml+xml"/>'
        for cid, _, _ in chapters
    )
    spine = "".join(f'<itemref idref="{cid}"/>' for cid, _, _ in chapters)
    z.writestr(
        "OEBPS/content.opf",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bid">urn:uuid:0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0</dc:identifier>'
        "<dc:title>Field Notes from Tomorrow</dc:title>"
        "<dc:language>en</dc:language>"
        '<meta property="dcterms:modified">2024-01-01T00:00:00Z</meta>'
        f"</metadata><manifest>{manifest}</manifest>"
        f"<spine>{spine}</spine></package>",
    )
    z.writestr(
        "OEBPS/nav.xhtml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        "<head><title>Contents</title></head><body>"
        '<nav epub:type="toc"><ol>'
        + "".join(f'<li><a href="{cid}.xhtml">{title}</a></li>' for cid, title, _ in chapters)
        + "</ol></nav></body></html>",
    )
    for cid, title, body in chapters:
        z.writestr(
            f"OEBPS/{cid}.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>",
        )
print(f"wrote {path}")
PY

# --- Audio + video: ffmpeg inside the Hearth image ---------------------------
# H.264 + AAC so Chromium can decode without transcoding. The video is 75s on
# purpose: the player refuses to jump to a resume point within 30s of the end
# (rewatch, not resume), so a short fixture could never exercise resume. The
# mp3 carries the ID3 tags the scanner reads (album_artist = album artist).
docker run --rm -v "$FIXTURES/media:/media" "$IMAGE" sh -c '
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=320x180:rate=25:duration=75" \
    -f lavfi -i "sine=frequency=440:duration=75" \
    -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -c:a aac -shortest \
    "/media/movies/Embers at Dawn (2024).mp4"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "sine=frequency=523:duration=25" \
    -c:a libmp3lame -b:a 96k \
    -metadata title="Midnight Signal" \
    -metadata artist="The Static Coast" \
    -metadata album_artist="The Static Coast" \
    -metadata album="Neon Hollow" \
    -metadata track="1" \
    "/media/music/Neon Hollow/01 - Midnight Signal.mp3"
'
echo "wrote video + audio fixtures"
echo "Fixtures in $FIXTURES/media:"
find "$FIXTURES/media" -type f -exec ls -lh {} \; | awk '{print " ", $9, "("$5")"}'
