"""Unit tests for FFmpeg-backed media helpers."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import io
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from typing_extensions import override

from growcam import media

if TYPE_CHECKING:
    from typing import BinaryIO

    from growcam.xm_media import XMStreamStats


def test_rtsp_url_quotes_credentials() -> None:
    assert media.rtsp_url("192.0.2.1", "a@b", "p:/?") == "rtsp://a%40b:p%3A%2F%3F@192.0.2.1:554/"


def test_ffmpeg_reports_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_executable(_name: str) -> None:
        return None

    monkeypatch.setattr("growcam.media.shutil.which", missing_executable)

    with pytest.raises(media.MediaError, match="not found on PATH"):
        _ = media.snapshot("192.0.2.1")


def test_snapshot_returns_ffmpeg_jpeg_and_uses_encoded_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        assert kwargs["check"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 15
        return subprocess.CompletedProcess(command, 0, b"jpeg bytes", b"")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    assert media.snapshot("192.0.2.1", "camera@home", "secret/value") == b"jpeg bytes"
    assert "rtsp://camera%40home:secret%2Fvalue@192.0.2.1:554/" in observed_command
    assert "mjpeg" in observed_command


def test_snapshot_reports_ffmpeg_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"camera refused stream")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", failed_run)

    with pytest.raises(media.MediaError, match="camera refused stream"):
        _ = media.snapshot("192.0.2.1")


def test_mjpeg_stream_uses_requested_rate_and_width(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    process = cast("subprocess.Popen[bytes]", object())

    def fake_popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        observed_command.extend(command)
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        return process

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.Popen", fake_popen)

    assert media.start_mjpeg("192.0.2.1", frames_per_second=8, width=960) is process
    assert "fps=8,scale=960:-2" in observed_command
    assert "growcam" in observed_command


def test_live_audio_transcodes_camera_alaw_to_browser_mp3(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    process = cast("subprocess.Popen[bytes]", object())

    def fake_popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        observed_command.extend(command)
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        return process

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.Popen", fake_popen)

    assert media.start_live_audio("192.0.2.1") is process
    assert observed_command[observed_command.index("-map") + 1] == "0:a:0"
    assert observed_command[observed_command.index("-c:a") + 1] == "libmp3lame"
    assert observed_command[observed_command.index("-ar") + 1] == "8000"
    assert observed_command[-2:] == ["mp3", "pipe:1"]


def test_live_clip_creates_parent_and_copies_the_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "clips" / "live.mkv"
    observed_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.save_live_clip("192.0.2.1", destination, 12.5)

    assert destination.parent.is_dir()
    assert observed_command[observed_command.index("-t") + 1] == "12.5"
    assert observed_command[observed_command.index("-c") + 1] == "copy"
    assert observed_command[-1] == str(destination)


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
    assert "15.0" in observed_command
    assert not destination.with_name(destination.name + ".part.mkv").exists()


def test_remux_recording_encodes_recovered_alaw_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "camera.hevc"
    audio = tmp_path / "camera.alaw"
    destination = tmp_path / "recording.mkv"
    _ = source.write_bytes(b"video")
    _ = audio.write_bytes(b"audio")
    observed_command: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        _ = Path(command[-1]).write_bytes(b"mkv bytes")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.remux_recording(source, destination, audio_source=audio)

    assert observed_command[observed_command.index("-ar") + 1] == "8000"
    assert observed_command[observed_command.index("-ac") + 1] == "1"
    assert observed_command[observed_command.index("-c:a") + 1] == "aac"
    assert "1:a:0" in observed_command


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


def test_fragmented_preview_transcoder_uses_low_latency_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    process = cast("subprocess.Popen[bytes]", object())

    def fake_popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        observed_command.extend(command)
        assert kwargs["stdin"] is subprocess.PIPE
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        return process

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.Popen", fake_popen)

    assert media.start_fragmented_preview_transcode(frames_per_second=25.0) is process
    assert "pipe:0" in observed_command
    assert "pipe:1" in observed_command
    assert "+frag_every_frame+empty_moov+default_base_moof" in observed_command
    assert observed_command[observed_command.index("-g") + 1] == "60"
    assert observed_command[observed_command.index("-probesize") + 1] == "32768"
    assert observed_command[observed_command.index("-analyzeduration") + 1] == "1"
    assert observed_command[observed_command.index("-fpsprobesize") + 1] == "0"
    assert observed_command[observed_command.index("-threads") + 1] == "1"
    assert observed_command[observed_command.index("-tune") + 1] == "zerolatency"


def test_xm_preview_transcoder_accepts_separate_video_and_audio_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    process = cast("subprocess.Popen[bytes]", object())

    def fake_popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        observed_command.extend(command)
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        return process

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.Popen", fake_popen)

    assert (
        media.start_xm_fragmented_preview_transcode(
            frames_per_second=7.5,
            video_port=41001,
            audio_port=41002,
        )
        is process
    )
    assert "tcp://127.0.0.1:41001" in observed_command
    assert "tcp://127.0.0.1:41002" in observed_command
    assert "alaw" in observed_command
    assert observed_command[observed_command.index("-c:a") + 1] == "aac"
    assert observed_command.index("tcp://127.0.0.1:41002") < observed_command.index("tcp://127.0.0.1:41001")
    assert "0:a:0?" in observed_command
    assert "1:v:0" in observed_command
    assert "-shortest" not in observed_command
    assert observed_command.count("-thread_queue_size") == 2


def test_xm_preview_transcoder_can_copy_native_hevc(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    process = cast("subprocess.Popen[bytes]", object())

    def fake_popen(command: list[str], **_kwargs: object) -> subprocess.Popen[bytes]:
        observed_command.extend(command)
        return process

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.Popen", fake_popen)

    assert (
        media.start_xm_fragmented_preview_transcode(
            frames_per_second=15.0,
            video_port=41001,
            audio_port=41002,
            video_codec="hevc",
        )
        is process
    )
    assert observed_command[observed_command.index("-c:v") + 1] == "copy"
    assert observed_command[observed_command.index("-tag:v") + 1] == "hvc1"
    assert "262144" in observed_command
    assert "1000000" in observed_command
    assert "-shortest" not in observed_command
    assert "libx264" not in observed_command


def test_xm_preview_feeder_publishes_camera_frame_rate() -> None:
    video_sink = media._QueuedMediaSink()
    audio_sink = media._QueuedMediaSink()
    source_result: Future[tuple[int, XMStreamStats]] = Future()
    frame_rate_result: Future[float] = Future()
    state = media._XMPreviewFeedState(source_result, frame_rate_result, 15.0)
    video_payload = b"\x00\x00\x00\x01\x40\x01video"
    metadata = bytes((0x13, 8, 64, 180)) + (12345).to_bytes(4, "little")
    framed_source = b"\x00\x00\x01\xfc" + metadata + len(video_payload).to_bytes(4, "little") + video_payload

    def source(output: BinaryIO) -> int:
        return output.write(framed_source)

    media._feed_xm_preview_source(source, video_sink, audio_sink, state)

    source_bytes, stats = source_result.result()
    assert source_bytes == len(framed_source)
    assert frame_rate_result.result() == 8.0
    assert stats.frames_per_second == 8.0


def test_xm_preview_feeder_uses_fallback_for_raw_video() -> None:
    video_sink = media._QueuedMediaSink()
    audio_sink = media._QueuedMediaSink()
    source_result: Future[tuple[int, XMStreamStats]] = Future()
    frame_rate_result: Future[float] = Future()
    state = media._XMPreviewFeedState(source_result, frame_rate_result, 15.0)
    raw_video = b"\x00\x00\x00\x01\x40\x01video"

    def source(output: BinaryIO) -> int:
        return output.write(raw_video)

    media._feed_xm_preview_source(source, video_sink, audio_sink, state)

    _source_bytes, stats = source_result.result()
    assert frame_rate_result.result() == 15.0
    assert stats.frames_per_second is None


def test_video_only_preview_feeder_demuxes_xm_packets() -> None:
    class OpenBytesIO(io.BytesIO):
        @override
        def close(self) -> None:
            """Keep captured bytes readable after the feeder closes its pipe."""

    video = OpenBytesIO()
    result: Future[int] = Future()
    video_payload = b"\x00\x00\x00\x01\x40\x01keyframe"
    continuation = b"\x00\x00\x00\x01\x02\x01follow-up"
    metadata = bytes((0x13, 25, 64, 180)) + (12345).to_bytes(4, "little")
    framed_video = b"\x00\x00\x01\xfc" + metadata + len(video_payload).to_bytes(4, "little") + video_payload
    framed_video += b"\x00\x00\x01\xfd" + len(continuation).to_bytes(4, "little") + continuation

    def source(output: BinaryIO) -> int:
        return output.write(framed_video)

    media._feed_preview_source(source, video, result)

    assert result.result() == len(framed_video)
    assert video.getvalue() == video_payload + continuation


def test_queued_media_sink_applies_byte_backpressure_until_aborted() -> None:
    sink = media._QueuedMediaSink(maximum_bytes=4)
    assert sink.write(b"full") == 4
    attempted = threading.Event()
    result: Future[int] = Future()

    def blocked_write() -> None:
        attempted.set()
        try:
            result.set_result(sink.write(b"x"))
        except BrokenPipeError as error:
            result.set_exception(error)

    writer = threading.Thread(target=blocked_write, daemon=True)
    writer.start()
    assert attempted.wait(timeout=1)
    assert not result.done()

    sink.abort()
    writer.join(timeout=1)

    assert not writer.is_alive()
    with pytest.raises(BrokenPipeError, match="stopped reading"):
        _ = result.result()


def test_queued_media_sink_rejects_one_chunk_larger_than_its_memory_limit() -> None:
    sink = media._QueuedMediaSink(maximum_bytes=3)

    with pytest.raises(ValueError, match="exceeds the queue memory limit"):
        _ = sink.write(b"four")


def test_fragmented_preview_is_finalized_as_indexed_fast_start_mp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "preview.mp4"
    _ = source.write_bytes(b"fragmented mp4")
    observed_command: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        _ = Path(command[-1]).write_bytes(b"indexed mp4")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.finalize_fragmented_preview(source, timestamp_scale=0.08)

    assert source.read_bytes() == b"indexed mp4"
    assert observed_command[observed_command.index("-itsscale") + 1] == "0.08"
    assert "+faststart" in observed_command
    assert "copy" in observed_command
    assert "0:a:0?" in observed_command
    assert not source.with_name("preview.indexed.part.mp4").exists()


def test_fragmented_preview_aligns_video_timestamps_to_recovered_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "preview.mp4"
    _ = source.write_bytes(b"fragmented mp4")
    observed_command: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        _ = Path(command[-1]).write_bytes(b"indexed mp4")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def stream_durations(_source: Path) -> tuple[float, float]:
        return 112.0, 119.5

    monkeypatch.setattr("growcam.media._ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("growcam.media._preview_stream_durations", stream_durations)
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    media.finalize_fragmented_preview(source, align_video_to_audio=True)

    timestamp_scale = float(observed_command[observed_command.index("-itsscale") + 1])
    assert timestamp_scale == pytest.approx(119.5 / 112.0)
    assert observed_command.count(str(source)) == 2
    assert "1:a:0?" in observed_command


def test_preview_stream_durations_reads_ffprobe_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_command: list[str] = []
    output = b'{"streams":[{"codec_type":"video","duration":"112.0"},{"codec_type":"audio","duration":"119.5"}]}'

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed_command.extend(command)
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr("growcam.media._ffprobe", lambda: "ffprobe")
    monkeypatch.setattr("growcam.media.subprocess.run", fake_run)

    assert media._preview_stream_durations(Path("preview.mp4")) == (112.0, 119.5)
    assert observed_command[0] == "ffprobe"
