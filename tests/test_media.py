"""Unit tests for FFmpeg-backed media helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from growcam import media


def test_rtsp_url_quotes_credentials() -> None:
    assert media.rtsp_url("192.0.2.1", "a@b", "p:/?") == "rtsp://a%40b:p%3A%2F%3F@192.0.2.1:554/"


def test_ffmpeg_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable(_name: str) -> None:
        return None

    monkeypatch.setattr("growcam.media.shutil.which", missing_executable)

    with pytest.raises(media.MediaError, match="not found on PATH"):
        _ = media.snapshot("192.0.2.1")


def test_remux_recording_uses_hevc_and_atomic_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "camera.h264"
    destination = tmp_path / "recording.mkv"
    _ = source.write_bytes(b"camera bytes")
    observed_command: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        _ = Path(command[-1]).write_bytes(b"mkv bytes")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.remux_recording(source, destination)

    assert destination.read_bytes() == b"mkv bytes"
    assert "hevc" in observed_command
    assert "7.5" in observed_command
    assert not destination.with_name(destination.name + ".part.mkv").exists()


@pytest.mark.parametrize("frames_per_second", [0.0, -1.0])
def test_remux_recording_rejects_invalid_frame_rate(
    frames_per_second: float,
    tmp_path: Path,
) -> None:
    source = tmp_path / "camera.h264"
    source.touch()

    with pytest.raises(ValueError, match="greater than zero"):
        media.remux_recording(source, tmp_path / "recording.mkv", frames_per_second=frames_per_second)


def test_timelapse_preview_transcodes_to_atomic_browser_mp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "timelapse.hevc"
    destination = tmp_path / "preview.mp4"
    _ = source.write_bytes(b"camera bytes")
    observed_command: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        _ = Path(command[-1]).write_bytes(b"mp4 bytes")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.transcode_timelapse_preview(source, destination)

    assert destination.read_bytes() == b"mp4 bytes"
    assert "hevc" in observed_command
    assert "libx264" in observed_command
    assert "25.0" in observed_command
    assert "+faststart" in observed_command
    assert not destination.with_name("preview.part.mp4").exists()


@pytest.mark.parametrize("width", [0, 1279])
def test_timelapse_preview_rejects_invalid_width(width: int, tmp_path: Path) -> None:
    source = tmp_path / "timelapse.hevc"
    source.touch()

    with pytest.raises(ValueError, match="positive even"):
        media.transcode_timelapse_preview(source, tmp_path / "preview.mp4", width=width)
