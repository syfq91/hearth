"""Unit tests for transcode capabilities detection and vainfo parsing."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.capabilities import (
    CodecCapability,
    ServerTranscodeCapabilities,
    parse_vainfo_output,
    probe_transcode_capabilities,
    reset_transcode_capabilities_cache,
)

SAMPLE_INTEL_VAINFO = """
vainfo: VA-API version: 1.17 (libva 2.12.0)
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 23.1.1
vainfo: Supported profile and entrypoints
      VAProfileNone                   :	VAEntrypointVideoProc
      VAProfileNone                   :	VAEntrypointStats
      VAProfileMPEG2Simple            :	VAEntrypointVLD
      VAProfileMPEG2Main              :	VAEntrypointVLD
      VAProfileH264ConstrainedBaseline:	VAEntrypointVLD
      VAProfileH264ConstrainedBaseline:	VAEntrypointEncSlice
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSlice
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSlice
      VAProfileHEVCMain               :	VAEntrypointVLD
      VAProfileHEVCMain               :	VAEntrypointEncSlice
      VAProfileHEVCMain10             :	VAEntrypointVLD
      VAProfileHEVCMain10             :	VAEntrypointEncSlice
      VAProfileVP9Profile0            :	VAEntrypointVLD
      VAProfileVP9Profile2            :	VAEntrypointVLD
      VAProfileAV1Profile0            :	VAEntrypointVLD
      VAProfileAV1Profile0            :	VAEntrypointEncSlice
"""

SAMPLE_AMD_VAINFO = """
vainfo: VA-API version: 1.14 (libva 2.12.0)
vainfo: Driver version: Mesa Gallium driver 22.3.6 for AMD Radeon RX 6600
vainfo: Supported profile and entrypoints
      VAProfileMPEG2Simple            :	VAEntrypointVLD
      VAProfileMPEG2Main              :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSliceLP
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSliceLP
      VAProfileHEVCMain               :	VAEntrypointVLD
      VAProfileHEVCMain               :	VAEntrypointEncSliceLP
      VAProfileVP9Profile0            :	VAEntrypointVLD
"""


def test_parse_vainfo_output_intel() -> None:
    codecs = parse_vainfo_output(SAMPLE_INTEL_VAINFO)
    assert codecs["h264"] == (True, True)
    assert codecs["hevc"] == (True, True)
    assert codecs["av1"] == (True, True)
    assert codecs["vp9"] == (True, False)
    assert codecs["mpeg2"] == (True, False)


def test_parse_vainfo_output_amd_low_power_entrypoint() -> None:
    codecs = parse_vainfo_output(SAMPLE_AMD_VAINFO)
    assert codecs["h264"] == (True, True)
    assert codecs["hevc"] == (True, True)
    assert codecs["vp9"] == (True, False)
    assert codecs["mpeg2"] == (True, False)
    assert "av1" not in codecs


def test_parse_vainfo_empty_or_invalid() -> None:
    assert parse_vainfo_output("") == {}
    assert parse_vainfo_output("Random text without profiles") == {}


def test_server_capabilities_helpers() -> None:
    caps = ServerTranscodeCapabilities(
        render_device="/dev/dri/renderD128",
        available=True,
        codecs={
            "h264": CodecCapability(codec="h264", decode=True, encode=True, test_encode_ok=True),
            "hevc": CodecCapability(codec="hevc", decode=True, encode=True, test_encode_ok=False),
            "vp9": CodecCapability(codec="vp9", decode=True, encode=False),
        },
    )
    assert caps.can_vaapi_encode("h264") is True
    assert caps.can_vaapi_encode("hevc") is False  # test encode failed
    assert caps.can_vaapi_encode("vp9") is False
    assert caps.can_vaapi_decode("h264") is True
    assert caps.can_vaapi_decode("vp9") is True
    assert caps.can_vaapi_decode("av1") is False

    # When force_software is true
    caps.force_software = True
    assert caps.can_vaapi_encode("h264") is False
    assert caps.can_vaapi_decode("h264") is False


@pytest.mark.asyncio
async def test_probe_force_software() -> None:
    reset_transcode_capabilities_cache()
    settings = Settings(transcode_force_software=True)
    caps = await probe_transcode_capabilities(settings)
    assert caps.available is False
    assert caps.force_software is True
    assert caps.error == "forced software mode"
    reset_transcode_capabilities_cache()


@pytest.mark.asyncio
async def test_probe_missing_device(tmp_path: Path) -> None:
    reset_transcode_capabilities_cache()
    missing = tmp_path / "nonexistent_render"
    settings = Settings(render_device=missing, transcode_force_software=False)
    caps = await probe_transcode_capabilities(settings)
    assert caps.available is False
    assert caps.error == "render device not present"
    reset_transcode_capabilities_cache()


@pytest.mark.asyncio
async def test_probe_vainfo_failure(tmp_path: Path) -> None:
    reset_transcode_capabilities_cache()
    fake_dev = tmp_path / "renderD128"
    fake_dev.touch()

    settings = Settings(render_device=fake_dev, transcode_force_software=False)
    with patch(
        "app.services.transcode.capabilities._run",
        new=AsyncMock(return_value=(1, "", "libva error: vaGetDriverName() failed")),
    ):
        caps = await probe_transcode_capabilities(settings)
        assert caps.available is False
        assert "libva error" in (caps.error or "")

    reset_transcode_capabilities_cache()


@pytest.mark.asyncio
async def test_probe_success_with_test_encodes(tmp_path: Path) -> None:
    reset_transcode_capabilities_cache()
    fake_dev = tmp_path / "renderD128"
    fake_dev.touch()

    settings = Settings(render_device=fake_dev, transcode_force_software=False)

    async def mock_run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
        if cmd[0] == settings.vainfo_binary:
            return 0, SAMPLE_INTEL_VAINFO, ""
        if "-filters" in cmd:
            return 0, "... tonemap_vaapi ...", ""
        if "-f" in cmd and "null" in cmd:
            # test encode
            if "h264_vaapi" in cmd or "hevc_vaapi" in cmd:
                return 0, "", ""
            return 1, "", "av1_vaapi not supported by hardware"
        return 0, "", ""

    with patch("app.services.transcode.capabilities._run", new=AsyncMock(side_effect=mock_run)):
        caps = await probe_transcode_capabilities(settings)
        assert caps.available is True
        assert caps.driver_version == "Intel iHD driver for Intel(R) Gen Graphics - 23.1.1"
        assert caps.tonemap_vaapi is True
        assert caps.can_vaapi_encode("h264") is True
        assert caps.can_vaapi_encode("hevc") is True
        assert caps.can_vaapi_encode("av1") is False
        assert caps.can_vaapi_decode("vp9") is True

        # Test caching
        cached_caps = await probe_transcode_capabilities(settings)
        assert cached_caps is caps

    reset_transcode_capabilities_cache()
