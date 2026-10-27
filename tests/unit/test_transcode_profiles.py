"""Unit tests for client capability profiles and preset resolution."""

from app.services.transcode.profile import (
    BUILTIN_PRESETS,
    ClientCapabilities,
    resolve_client_capabilities,
)


def test_builtin_presets_exist() -> None:
    assert "chrome" in BUILTIN_PRESETS
    assert "firefox" in BUILTIN_PRESETS
    assert "safari" in BUILTIN_PRESETS
    assert "android_tv" in BUILTIN_PRESETS
    assert "unknown" in BUILTIN_PRESETS


def test_resolve_default_is_unknown() -> None:
    res = resolve_client_capabilities(None)
    assert res.preset == "unknown"
    assert res.containers == ["mp4"]
    assert res.video_codecs == ["h264"]
    assert res.max_bitrate == 4_000_000
    assert res.hdr is False


def test_resolve_chrome_preset() -> None:
    client = ClientCapabilities(preset="chrome")
    res = resolve_client_capabilities(client)
    assert res.preset == "chrome"
    assert "mp4" in res.containers
    assert "webm" in res.containers
    assert "h264" in res.video_codecs
    assert "vp9" in res.video_codecs
    assert "av1" in res.video_codecs
    assert res.hls_fmp4 is True
    assert res.hdr is False


def test_resolve_safari_preset() -> None:
    client = ClientCapabilities(preset="safari")
    res = resolve_client_capabilities(client)
    assert res.preset == "safari"
    assert "hevc" in res.video_codecs
    assert res.hdr is True
    assert res.hls_fmp4 is True


def test_resolve_android_tv_preset() -> None:
    client = ClientCapabilities(preset="android_tv")
    res = resolve_client_capabilities(client)
    assert "mkv" in res.containers
    assert "srt" in res.subtitle_formats


def test_preset_with_overrides() -> None:
    client = ClientCapabilities(
        preset="chrome",
        max_bitrate=8_000_000,
        max_width=1920,
        max_height=1080,
        hdr=True,
    )
    res = resolve_client_capabilities(client)
    assert res.preset == "chrome"
    assert res.max_bitrate == 8_000_000
    assert res.max_width == 1920
    assert res.max_height == 1080
    assert res.hdr is True  # overridden
    assert "vp9" in res.video_codecs  # kept from preset


def test_custom_profile_without_preset() -> None:
    client = ClientCapabilities(
        containers=["mkv", "mp4"],
        video_codecs=["hevc"],
        audio_codecs=["opus"],
    )
    res = resolve_client_capabilities(client)
    assert res.containers == ["mkv", "mp4"]
    assert res.video_codecs == ["hevc"]
    assert res.audio_codecs == ["opus"]


def test_supports_container_helper() -> None:
    caps = ClientCapabilities(containers=["mp4", "webm"])
    assert caps.supports_container("mp4") is True
    assert caps.supports_container(".mp4") is True
    assert caps.supports_container("mov") is True  # alias
    assert caps.supports_container("m4v") is True  # alias
    assert caps.supports_container("mkv") is False


def test_supports_video_codec_and_profile_helper() -> None:
    caps = ClientCapabilities(
        video_codecs=["h264", "hevc"],
        video_profiles=["high", "main 10"],
    )
    assert caps.supports_video_codec("h264") is True
    assert caps.supports_video_codec("avc1") is True  # alias
    assert caps.supports_video_codec("hevc") is True
    assert caps.supports_video_codec("h265") is True  # alias
    assert caps.supports_video_codec("vp9") is False

    # Profile matching
    assert caps.supports_video_codec("h264", "High") is True
    assert caps.supports_video_codec("hevc", "Main 10") is True
    assert caps.supports_video_codec("h264", "Baseline") is False


def test_supports_audio_codec_helper() -> None:
    caps = ClientCapabilities(audio_codecs=["aac", "eac3"])
    assert caps.supports_audio_codec("aac") is True
    assert caps.supports_audio_codec("mp4a") is True  # alias
    assert caps.supports_audio_codec("eac3") is True
    assert caps.supports_audio_codec("ec-3") is True  # alias
    assert caps.supports_audio_codec("dts") is False


def test_supports_subtitle_helper() -> None:
    caps = ClientCapabilities(subtitle_formats=["vtt", "srt"])
    assert caps.supports_subtitle("vtt") is True
    assert caps.supports_subtitle(".vtt") is True
    assert caps.supports_subtitle("srt") is True
    assert caps.supports_subtitle("ass") is False
    assert caps.supports_subtitle("pgs") is False
