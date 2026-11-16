"""Unit tests for container font attachment parsing, MIME handling, and caching."""

from typing import Any

from app.api.v1.attachments import _FONT_MIME_BY_EXT
from app.services.media.probe import (
    FONT_EXTENSIONS,
    SUPPORTED_FONT_MIME_TYPES,
    AttachmentStream,
    parse_probe_payload,
)


def test_attachment_stream_mime_classification():
    """Verify font MIME types and fallback extension mapping."""
    payload: dict[str, Any] = {
        "format": {"format_name": "matroska", "duration": "100.0"},
        "streams": [
            {
                "index": 0,
                "codec_type": "attachment",
                "tags": {"filename": "arial.ttf", "mimetype": "font/ttf"},
            },
            {
                "index": 1,
                "codec_type": "attachment",
                "tags": {"filename": "MyriadPro.otf", "mimetype": "application/x-truetype-font"},
            },
            {
                "index": 2,
                "codec_type": "attachment",
                "tags": {"filename": "custom.woff2", "mimetype": "font/woff2"},
            },
            {
                "index": 3,
                "codec_type": "attachment",
                "tags": {"filename": "fallback.otf", "mimetype": "application/octet-stream"},
            },
            {
                "index": 4,
                "codec_type": "attachment",
                "tags": {"filename": "cover.jpg", "mimetype": "image/jpeg"},
            },
        ],
        "chapters": [],
    }

    result = parse_probe_payload(payload)
    attachments = result.attachments
    assert len(attachments) == 5

    # TTF
    assert attachments[0].filename == "arial.ttf"
    assert attachments[0].mime_type == "font/ttf"
    assert attachments[0].is_font is True

    # application/x-truetype-font
    assert attachments[1].filename == "MyriadPro.otf"
    assert attachments[1].mime_type == "application/x-truetype-font"
    assert attachments[1].is_font is True

    # WOFF2
    assert attachments[2].filename == "custom.woff2"
    assert attachments[2].mime_type == "font/woff2"
    assert attachments[2].is_font is True

    # Fallback from filename extension (.otf) when mimetype is generic
    assert attachments[3].filename == "fallback.otf"
    assert attachments[3].is_font is True

    # Non-font image attachment
    assert attachments[4].filename == "cover.jpg"
    assert attachments[4].mime_type == "image/jpeg"
    assert attachments[4].is_font is False


def test_font_extension_dictionary():
    """Verify font extension mapping covers common container font formats."""
    assert _FONT_MIME_BY_EXT[".ttf"] == "font/ttf"
    assert _FONT_MIME_BY_EXT[".otf"] == "font/otf"


def test_font_filename_keeps_an_extension_and_strips_paths(tmp_path):
    """Extracted fonts must have a safe name libass/fontconfig can scan."""
    from app.services.media.attachments import font_filename

    assert (
        font_filename(AttachmentStream(index=3, filename="../My Font.ttf", mime_type="font/ttf"))
        == "3-My_Font.ttf"
    )
    assert (
        font_filename(
            AttachmentStream(index=4, filename="cool", mime_type="application/vnd.ms-opentype")
        )
        == "4-cool.otf"
    )
    assert (
        font_filename(AttachmentStream(index=5, filename=None, mime_type=None))
        == "5-attachment-5.ttf"
    )


def test_attachment_cache_path_matches_the_serving_contract(tmp_path):
    from app.services.media.attachments import attachment_cache_path

    assert attachment_cache_path(tmp_path, "file-1", 2) == tmp_path / "file-1_2.bin"
    assert _FONT_MIME_BY_EXT[".woff"] == "font/woff"
    assert _FONT_MIME_BY_EXT[".woff2"] == "font/woff2"
    assert ".ttf" in FONT_EXTENSIONS
    assert "application/x-truetype-font" in SUPPORTED_FONT_MIME_TYPES


def test_attachment_stream_default_attributes():
    """Verify AttachmentStream attributes when optional metadata is missing."""
    att = AttachmentStream(index=1, filename="unknown")
    assert att.index == 1
    assert att.filename == "unknown"
    assert att.mime_type is None
    assert att.is_font is False
