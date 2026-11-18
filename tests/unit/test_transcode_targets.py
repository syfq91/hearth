"""Unit tests for transcode target selection."""

from app.services.transcode.capabilities import CodecCapability, ServerTranscodeCapabilities
from app.services.transcode.profile import ClientCapabilities, TranscodeTarget
from app.services.transcode.targets import select_transcode_targets


def _server(
    *,
    software: tuple[str, ...] = ("libx264", "aac"),
    h264_hw: bool = False,
) -> ServerTranscodeCapabilities:
    caps = ServerTranscodeCapabilities(
        render_device="/dev/dri/renderD128",
        available=h264_hw,
        software_encoders=list(software),
    )
    if h264_hw:
        caps.codecs["h264"] = CodecCapability(
            codec="h264", decode=True, encode=True, test_encode_ok=True
        )
    return caps


def test_defaults_keep_existing_behavior() -> None:
    selection = select_transcode_targets(ClientCapabilities(), _server())
    assert selection.video_codec == "libx264"
    assert selection.video_family == "h264"
    assert selection.audio_codec == "aac"
    assert selection.audio_family == "aac"


def test_ordered_client_preference_is_honored() -> None:
    client = ClientCapabilities(
        video_transcode_codecs=["hevc", "h264"],
        audio_transcode_codecs=["ac3", "aac"],
    )
    server = _server(software=("libx264", "libx265", "aac", "ac3"))
    selection = select_transcode_targets(client, server)
    assert selection.video_codec == "libx265"
    assert selection.video_family == "hevc"
    assert selection.audio_codec == "ac3"


def test_source_codec_wins_over_list_order() -> None:
    client = ClientCapabilities(video_transcode_codecs=["h264", "hevc"])
    server = _server(software=("libx264", "libx265", "aac"))
    selection = select_transcode_targets(client, server, source_video_codec="hvc1")
    assert selection.video_codec == "libx265"


def test_unencodable_target_falls_back_to_default() -> None:
    client = ClientCapabilities(video_transcode_codecs=["av1", "vp9"])
    selection = select_transcode_targets(client, _server())
    assert selection.video_family == "h264"
    assert selection.video_codec == "libx264"


def test_non_hls_codec_is_filtered_out() -> None:
    client = ClientCapabilities(
        video_transcode_codecs=["mpeg2"],
        audio_transcode_codecs=["flac"],
    )
    selection = select_transcode_targets(client, _server())
    assert selection.video_family == "h264"
    assert selection.audio_family == "aac"


def test_hardware_encoder_is_used_only_when_allowed() -> None:
    client = ClientCapabilities()
    server = _server(h264_hw=True)
    assert select_transcode_targets(client, server).video_codec == "h264_vaapi"
    assert (
        select_transcode_targets(client, server, allow_video_hardware=False).video_codec
        == "libx264"
    )


def test_hardware_encoder_stays_scoped_to_h264() -> None:
    client = ClientCapabilities(video_transcode_codecs=["hevc"])
    server = _server(software=("libx264", "libx265", "aac"))
    server.available = True
    server.codecs["hevc"] = CodecCapability(
        codec="hevc", decode=True, encode=True, test_encode_ok=True
    )
    assert select_transcode_targets(client, server).video_codec == "libx265"


def test_conditional_target_ranking_prefers_source_match() -> None:
    client = ClientCapabilities(
        transcode_targets=[
            TranscodeTarget(video_codecs=["h264"], audio_codecs=["aac"]),
            TranscodeTarget(
                video_codecs=["hevc"],
                audio_codecs=["aac"],
                max_width=3840,
                max_bitrate=20_000_000,
            ),
        ]
    )
    server = _server(software=("libx264", "libx265", "aac"))
    selection = select_transcode_targets(
        client, server, source_video_codec="hvc1", source_width=1920, source_height=1080
    )
    assert selection.target_index == 1
    assert selection.video_codec == "libx265"
    assert selection.constraints is not None
    assert selection.constraints.max_width == 3840
    assert selection.constraints.max_bitrate == 20_000_000
    assert selection.fallback_used is False


def test_conditional_target_audio_match_breaks_ties() -> None:
    client = ClientCapabilities(
        transcode_targets=[
            TranscodeTarget(video_codecs=["h264"], audio_codecs=["aac"]),
            TranscodeTarget(video_codecs=["h264"], audio_codecs=["ac3"]),
        ]
    )
    server = _server(software=("libx264", "aac", "ac3"))
    selection = select_transcode_targets(client, server, source_audio_codec="ac-3")
    assert selection.target_index == 1
    assert selection.audio_codec == "ac3"


def test_conditional_target_with_unproducible_container_falls_back() -> None:
    client = ClientCapabilities(
        video_transcode_codecs=["hevc"],
        transcode_targets=[TranscodeTarget(container="mkv", video_codecs=["hevc"])],
    )
    server = _server(software=("libx264", "libx265", "aac"))
    selection = select_transcode_targets(client, server, source_video_codec="hevc")
    assert selection.target_index is None
    assert selection.video_codec == "libx265"
    assert selection.fallback_used is True


def test_conditional_target_with_unencodable_codec_reports_fallback() -> None:
    client = ClientCapabilities(
        transcode_targets=[TranscodeTarget(video_codecs=["av1"], audio_codecs=["aac"])]
    )
    selection = select_transcode_targets(client, _server())
    assert selection.target_index == 0
    assert selection.video_codec == "libx264"
    assert selection.fallback_used is True


def test_fmp4_container_alias_is_accepted() -> None:
    client = ClientCapabilities(
        transcode_targets=[
            TranscodeTarget(container="fmp4", video_codecs=["h264"], audio_codecs=["aac"])
        ]
    )
    selection = select_transcode_targets(client, _server())
    assert selection.target_index == 0
    assert selection.constraints is not None


def test_audio_source_match_preserves_codec() -> None:
    client = ClientCapabilities(audio_transcode_codecs=["aac", "ac3"])
    server = _server(software=("libx264", "aac", "ac3"))
    by_source = select_transcode_targets(client, server, source_audio_codec="ac-3")
    no_match = select_transcode_targets(client, server, source_audio_codec="truehd")
    assert by_source.audio_codec == "ac3"
    assert no_match.audio_codec == "aac"
