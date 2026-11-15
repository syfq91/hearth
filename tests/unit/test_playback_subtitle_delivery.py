"""Embedded subtitle delivery URL selection."""

from types import SimpleNamespace

from app.api.v1.playback import _embedded_subtitle_delivery, _embedded_subtitle_urls
from app.services.media.probe import ProbeStream
from app.services.transcode.decision import PlaybackMode, SubtitleAction, TranscodePlan

FILE = SimpleNamespace(id="018f0000-0000-7000-8000-000000000000")
PREFIX = f"/api/v1/media/{FILE.id}/subtitles/"


def _plan(mode: PlaybackMode, action: SubtitleAction, index: int | None) -> TranscodePlan:
    return TranscodePlan(
        mode=mode,
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action=action,
        subtitle_stream_index=index,
        hwaccel="none",
    )


def test_direct_play_sidecar_vtt_is_served_directly() -> None:
    url, fmt = _embedded_subtitle_delivery(_plan("direct_play", "sidecar_vtt", 0), FILE, "tok")
    assert url == f"{PREFIX}0.vtt?token=tok"
    assert fmt == "vtt"


def test_hls_sidecar_vtt_is_left_to_the_manifest() -> None:
    url, fmt = _embedded_subtitle_delivery(_plan("remux_hls", "sidecar_vtt", 0), FILE, "tok")
    assert url is None
    assert fmt is None


def test_external_ass_is_served_for_client_rendering() -> None:
    modes: tuple[PlaybackMode, ...] = ("direct_play", "remux_hls", "transcode_hls")
    for mode in modes:
        url, fmt = _embedded_subtitle_delivery(_plan(mode, "external_ass", 1), FILE, "tok")
        assert url == f"{PREFIX}1.ass?token=tok"
        assert fmt == "ass"


def _subtitle_stream(index: int, codec: str) -> ProbeStream:
    return ProbeStream(index=index, codec_type="subtitle", codec_name=codec)


def test_embedded_subtitle_urls_skip_bitmap_tracks_and_keep_the_selector_index() -> None:
    streams = [
        _subtitle_stream(2, "subrip"),
        _subtitle_stream(3, "hdmv_pgs_subtitle"),
        _subtitle_stream(4, "ass"),
    ]
    urls = _embedded_subtitle_urls(streams, FILE, "tok")

    assert [(entry.index, entry.format) for entry in urls] == [(2, "vtt"), (4, "ass")]
    assert urls[0].url == f"{PREFIX}0.vtt?token=tok"
    assert urls[1].url == f"{PREFIX}2.ass?token=tok"


def test_burn_in_and_off_subtitles_have_no_direct_url() -> None:
    assert _embedded_subtitle_delivery(_plan("transcode_hls", "burn_in", 0), FILE, "tok") == (
        None,
        None,
    )
    assert _embedded_subtitle_delivery(_plan("direct_play", "none", None), FILE, "tok") == (
        None,
        None,
    )
