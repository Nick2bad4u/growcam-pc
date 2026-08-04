"""FFmpeg-backed access to the camera's RTSP stream."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
from collections import deque
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote

from .dvrip import sofia_hash
from .xm_media import XMRecordingDemuxer

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import BinaryIO

    from .xm_media import XMStreamStats

_MAXIMUM_TCP_PORT = 65535
_MINIMUM_TCP_ADDRESS_FIELDS = 2
_PREVIEW_VIDEO_QUEUE_BYTES = 64 * 1024**2
_PREVIEW_AUDIO_QUEUE_BYTES = 16 * 1024**2
_PREVIEW_VIDEO_CODECS = frozenset({"h264", "hevc"})
_XM_FRAME_RATE_DETECTION_TIMEOUT_SECONDS = 10
_MINIMUM_TIMELINE_SCALE_DELTA = 0.001


class MediaError(RuntimeError):
    """Raised when FFmpeg is missing or cannot read the RTSP stream."""


@dataclass(frozen=True)
class _XMPreviewFeedState:
    source_result: Future[tuple[int, XMStreamStats]]
    frame_rate_result: Future[float]
    fallback_frames_per_second: float


class _ReadableBinaryPipe(Protocol):
    def read1(self, size: int = -1) -> bytes:
        """Read currently buffered bytes without waiting to fill the request."""
        ...


class _QueuedMediaSink:
    """Byte-bounded producer queue served to one FFmpeg TCP input."""

    def __init__(self, *, maximum_bytes: int = _PREVIEW_VIDEO_QUEUE_BYTES) -> None:
        if maximum_bytes <= 0:
            raise ValueError("Preview media queue limit must be greater than zero")
        self._maximum_bytes = maximum_bytes
        self._condition = threading.Condition()
        self._chunks: deque[bytes | None] = deque()
        self._queued_bytes = 0
        self._closed = False
        self._stopped = False
        self.failures: list[OSError] = []

    def write(self, data: bytes) -> int:
        """Queue one recovered elementary-stream chunk with cancellation checks."""
        if not data:
            return 0
        if len(data) > self._maximum_bytes:
            raise ValueError("One preview media chunk exceeds the queue memory limit")
        with self._condition:
            while not self._stopped and self._queued_bytes + len(data) > self._maximum_bytes:
                _ = self._condition.wait(timeout=0.25)
            if self._stopped or self._closed:
                raise BrokenPipeError("FFmpeg stopped reading the preview media stream")
            self._chunks.append(data)
            self._queued_bytes += len(data)
            self._condition.notify_all()
        return len(data)

    def flush(self) -> None:
        """Satisfy the binary writer interface; queue delivery is immediate."""

    def close(self) -> None:
        """Signal normal end-of-stream to the socket-serving thread."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._chunks.append(None)
            self._condition.notify_all()

    def abort(self) -> None:
        """Cancel producers and unblock a queue consumer after FFmpeg exits."""
        with self._condition:
            self._stopped = True
            self._chunks.clear()
            self._queued_bytes = 0
            self._condition.notify_all()

    def serve(self, listener: socket.socket) -> None:
        """Accept FFmpeg and copy queued chunks into its elementary-stream input."""
        try:
            listener.settimeout(30)
            connection, _address = listener.accept()
            with connection:
                while True:
                    with self._condition:
                        while not self._chunks and not self._stopped:
                            _ = self._condition.wait()
                        if self._stopped and not self._chunks:
                            break
                        chunk = self._chunks.popleft()
                        if chunk is not None:
                            self._queued_bytes -= len(chunk)
                        self._condition.notify_all()
                    if chunk is None:
                        break
                    connection.sendall(chunk)
        except OSError as error:
            self.failures.append(error)
        finally:
            with self._condition:
                self._stopped = True
                self._chunks.clear()
                self._queued_bytes = 0
                self._condition.notify_all()
            listener.close()


def rtsp_url(
    host: str,
    username: str = "admin",
    password: str = "",
    *,
    stream_index: int | None = None,
) -> str:
    """Build an authenticated root or explicit XMEye RTSP stream URL."""
    user = quote(username, safe="")
    if stream_index is not None:
        if stream_index not in {0, 1}:
            raise ValueError("RTSP stream_index must be 0 (main) or 1 (substream)")
        return f"rtsp://{host}:554/user={user}&password={sofia_hash(password)}&channel=1&stream={stream_index}.sdp"
    secret = quote(password, safe="")
    return f"rtsp://{user}:{secret}@{host}:554/"


def snapshot(host: str, username: str = "admin", password: str = "") -> bytes:
    """Return one current frame as JPEG bytes."""
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-i",
        rtsp_url(host, username, password),
        "-frames:v",
        "1",
        "-an",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFmpeg could not capture a camera frame") from error
    return result.stdout


def start_mjpeg(  # noqa: PLR0913 - explicit camera and conversion options keep live profiles auditable.
    host: str,
    username: str = "admin",
    password: str = "",
    *,
    frames_per_second: int = 5,
    width: int = 1280,
    stream_index: int | None = None,
) -> subprocess.Popen[bytes]:
    """Start an FFmpeg process that emits a browser-compatible MJPEG stream."""
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-i",
        rtsp_url(host, username, password, stream_index=stream_index),
        "-an",
        "-vf",
        f"fps={frames_per_second},scale={width}:-2",
        "-q:v",
        "5",
        "-f",
        "mpjpeg",
        "-boundary_tag",
        "growcam",
        "pipe:1",
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def start_live_audio(
    host: str,
    username: str = "admin",
    password: str = "",
) -> subprocess.Popen[bytes]:
    """Start an FFmpeg process that emits browser-compatible live MP3 audio."""
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-fflags",
        "nobuffer",
        "-i",
        rtsp_url(host, username, password),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "48k",
        "-ar",
        "8000",
        "-ac",
        "1",
        "-flush_packets",
        "1",
        "-f",
        "mp3",
        "pipe:1",
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def save_live_clip(
    host: str,
    destination: Path,
    seconds: float,
    username: str = "admin",
    password: str = "",
) -> None:
    """Copy a section of the live stream to a new Matroska file."""
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-i",
        rtsp_url(host, username, password),
        "-t",
        str(seconds),
        "-map",
        "0",
        "-c",
        "copy",
        str(destination),
    ]
    try:
        _ = subprocess.run(command, check=True, timeout=max(seconds + 15, 30))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise MediaError("FFmpeg could not save the live clip") from error


def remux_recording(
    source: Path,
    destination: Path,
    *,
    frames_per_second: float = 15.0,
    audio_source: Path | None = None,
) -> None:
    """Convert recovered GrowCam elementary streams into a playable Matroska file.

    GrowCam names these files ``.h264``, but this model actually stores a raw
    HEVC video elementary stream interleaved with optional G.711 A-law audio.
    Their timestamps are implicit, so the observed recording rate must be
    supplied while remuxing.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Recording source does not exist: {source}")
    if audio_source is not None and not audio_source.is_file():
        raise FileNotFoundError(f"Recording audio source does not exist: {audio_source}")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    if frames_per_second <= 0:
        raise ValueError("frames_per_second must be greater than zero")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part.mkv")
    if partial.exists():
        raise FileExistsError(f"Partial destination already exists: {partial}")
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-r",
        str(frames_per_second),
        "-f",
        "hevc",
        "-i",
        str(source),
    ]
    if audio_source is not None:
        command.extend(("-f", "alaw", "-ar", "8000", "-ac", "1", "-i", str(audio_source)))
    command.extend(("-map", "0:v:0"))
    if audio_source is not None:
        command.extend(("-map", "1:a:0", "-c:a", "aac", "-b:a", "48k"))
    command.extend(("-c:v", "copy", "-n", str(partial)))
    try:
        _ = subprocess.run(command, check=True, capture_output=True, timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFmpeg could not remux the recording") from error
    _ = partial.rename(destination)


def transcode_timelapse_preview(
    source: Path,
    destination: Path,
    *,
    frames_per_second: float = 25.0,
    width: int = 1280,
) -> None:
    """Turn a raw HEVC camera stream into a browser-compatible preview MP4."""
    if not source.is_file():
        raise FileNotFoundError(f"Timelapse source does not exist: {source}")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    if frames_per_second <= 0:
        raise ValueError("frames_per_second must be greater than zero")
    if width <= 0 or width % 2:
        raise ValueError("Preview width must be a positive even number")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.stem + ".part.mp4")
    if partial.exists():
        raise FileExistsError(f"Partial destination already exists: {partial}")
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-r",
        str(frames_per_second),
        "-f",
        "hevc",
        "-i",
        str(source),
        "-an",
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-n",
        str(partial),
    ]
    try:
        _ = subprocess.run(command, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFmpeg could not build the timelapse preview") from error
    _ = partial.rename(destination)


def start_fragmented_preview_transcode(
    *,
    frames_per_second: float,
    width: int = 1280,
) -> subprocess.Popen[bytes]:
    """Start a low-latency HEVC-to-fragmented-MP4 transcoder on binary pipes."""
    if frames_per_second <= 0:
        raise ValueError("frames_per_second must be greater than zero")
    if width <= 0 or width % 2:
        raise ValueError("Preview width must be a positive even number")
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-r",
        str(frames_per_second),
        "-f",
        "hevc",
        "-probesize",
        "32768",
        "-analyzeduration",
        "1",
        "-fpsprobesize",
        "0",
        "-threads",
        "1",
        "-i",
        "pipe:0",
        "-an",
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-crf",
        "24",
        "-g",
        "60",
        "-keyint_min",
        "60",
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+frag_every_frame+empty_moov+default_base_moof",
        "-flush_packets",
        "1",
        "-f",
        "mp4",
        "pipe:1",
    ]
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def start_xm_fragmented_preview_transcode(
    *,
    frames_per_second: float,
    video_port: int,
    audio_port: int,
    width: int = 1280,
    video_codec: str = "h264",
) -> subprocess.Popen[bytes]:
    """Start a low-latency XM HEVC/G.711A-to-fragmented-MP4 transcoder."""
    if frames_per_second <= 0:
        raise ValueError("frames_per_second must be greater than zero")
    if width <= 0 or width % 2:
        raise ValueError("Preview width must be a positive even number")
    if not 0 < video_port <= _MAXIMUM_TCP_PORT or not 0 < audio_port <= _MAXIMUM_TCP_PORT:
        raise ValueError("Preview media ports must be valid TCP ports")
    if video_codec not in _PREVIEW_VIDEO_CODECS:
        raise ValueError("Preview video codec must be h264 or hevc")
    # A ByTime stream may begin partway through a GOP. Native HEVC copy must
    # probe through the next VPS/SPS/PPS set before MP4 can write dimensions;
    # the H.264 transcoder can retain its much smaller low-latency probe.
    probe_size = "262144" if video_codec == "hevc" else "32768"
    analyze_duration = "1000000" if video_codec == "hevc" else "1"
    command = [
        _ffmpeg(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        # FFmpeg opens inputs in command-line order. The headerless A-law input
        # becomes ready after a few bytes; opening it first prevents HEVC stream
        # analysis from delaying the audio connection until camera EOF.
        "-thread_queue_size",
        "512",
        "-f",
        "alaw",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-ar",
        "8000",
        "-ac",
        "1",
        "-i",
        f"tcp://127.0.0.1:{audio_port}",
        "-r",
        str(frames_per_second),
        "-f",
        "hevc",
        "-probesize",
        probe_size,
        "-analyzeduration",
        analyze_duration,
        "-fpsprobesize",
        "0",
        "-threads",
        "1",
        "-thread_queue_size",
        "512",
        "-i",
        f"tcp://127.0.0.1:{video_port}",
        "-map",
        "1:v:0",
        "-map",
        "0:a:0?",
    ]
    if video_codec == "hevc":
        command.extend(("-c:v", "copy", "-tag:v", "hvc1"))
    else:
        command.extend(
            (
                "-vf",
                f"scale={width}:-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-crf",
                "24",
                "-g",
                "60",
                "-keyint_min",
                "60",
                "-sc_threshold",
                "0",
                "-pix_fmt",
                "yuv420p",
            )
        )
    command.extend(
        (
            "-c:a",
            "aac",
            "-b:a",
            "48k",
            "-ar",
            "8000",
            "-ac",
            "1",
        )
    )
    # Keep both elementary streams through natural EOF. The indexed cache pass
    # aligns the nominal raw-video clock to the recovered audio clock without
    # re-encoding or dropping stored frames.
    command.extend(
        (
            "-movflags",
            "+frag_every_frame+empty_moov+default_base_moof",
            "-flush_packets",
            "1",
            "-f",
            "mp4",
            "pipe:1",
        )
    )
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def build_fragmented_preview(
    source: Callable[[BinaryIO], int],
    destination: Path,
    consumer: Callable[[bytes], None],
    *,
    frames_per_second: float,
    width: int = 1280,
) -> int:
    """Demux and transcode camera packets while streaming an atomic preview."""
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        raise FileExistsError(f"Partial destination already exists: {partial}")

    process = start_fragmented_preview_transcode(frames_per_second=frames_per_second, width=width)
    standard_input, standard_output, standard_error = _preview_pipes(process)
    source_result: Future[int] = Future()
    error_tail = bytearray()
    feeder = threading.Thread(
        target=_feed_preview_source,
        args=(source, standard_input, source_result),
        name="growcam-preview-source",
        daemon=True,
    )
    error_reader = threading.Thread(
        target=_drain_preview_errors,
        args=(standard_error, error_tail),
        name="growcam-preview-errors",
        daemon=True,
    )
    feeder.start()
    error_reader.start()
    try:
        with partial.open("xb") as cached:
            while chunk := standard_output.read1(64 * 1024):
                _ = cached.write(chunk)
                consumer(chunk)
        return_code = process.wait(timeout=30)
        feeder.join(timeout=30)
        error_reader.join(timeout=5)
        _require_preview_completion(return_code, feeder, error_tail)
        source_bytes = source_result.result()
    except Exception:
        _stop_preview_process(process, feeder, error_reader)
        if partial.exists():
            partial.unlink()
        raise
    else:
        _ = partial.rename(destination)
        return source_bytes


def build_xm_fragmented_preview(  # noqa: PLR0913 - explicit codec controls keep the media boundary transparent.
    source: Callable[[BinaryIO], int],
    destination: Path,
    consumer: Callable[[bytes], None],
    *,
    frames_per_second: float,
    width: int = 1280,
    video_codec: str = "h264",
) -> XMStreamStats:
    """Demultiplex an XM recording into a streamed, atomic MP4 cache file."""
    if frames_per_second <= 0:
        raise ValueError("frames_per_second must be greater than zero")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        raise FileExistsError(f"Partial destination already exists: {partial}")

    video_listener = _loopback_listener()
    audio_listener = _loopback_listener()
    video_sink = _QueuedMediaSink(maximum_bytes=_PREVIEW_VIDEO_QUEUE_BYTES)
    audio_sink = _QueuedMediaSink(maximum_bytes=_PREVIEW_AUDIO_QUEUE_BYTES)
    process: subprocess.Popen[bytes] | None = None
    error_reader: threading.Thread | None = None
    source_result: Future[tuple[int, XMStreamStats]] = Future()
    frame_rate_result: Future[float] = Future()
    feed_state = _XMPreviewFeedState(source_result, frame_rate_result, frames_per_second)
    error_tail = bytearray()
    feeder = threading.Thread(
        target=_feed_xm_preview_source,
        args=(
            source,
            video_sink,
            audio_sink,
            feed_state,
        ),
        name="growcam-xm-preview-source",
        daemon=True,
    )
    video_server = threading.Thread(
        target=video_sink.serve,
        args=(video_listener,),
        name="growcam-preview-video-input",
        daemon=True,
    )
    audio_server = threading.Thread(
        target=audio_sink.serve,
        args=(audio_listener,),
        name="growcam-preview-audio-input",
        daemon=True,
    )
    try:
        # Start the camera request first so the first XM frame can provide its
        # actual 8/15 FPS metadata before FFmpeg assigns timestamps. The large
        # media queues buffer both elementary streams during this short probe.
        feeder.start()
        resolved_frames_per_second = _xm_preview_frame_rate(frame_rate_result, frames_per_second)
        process = start_xm_fragmented_preview_transcode(
            frames_per_second=resolved_frames_per_second,
            video_port=_listener_port(video_listener),
            audio_port=_listener_port(audio_listener),
            width=width,
            video_codec=video_codec,
        )
        standard_output, standard_error = _preview_output_pipes(process)
        error_reader = threading.Thread(
            target=_drain_preview_errors,
            args=(standard_error, error_tail),
            name="growcam-preview-errors",
            daemon=True,
        )
        video_server.start()
        audio_server.start()
        error_reader.start()
        with partial.open("xb") as cached:
            while chunk := standard_output.read1(64 * 1024):
                _ = cached.write(chunk)
                consumer(chunk)
        return_code = process.wait(timeout=30)
        feeder.join(timeout=30)
        video_server.join(timeout=10)
        audio_server.join(timeout=10)
        error_reader.join(timeout=5)
        stats = _require_xm_preview_completion(
            return_code,
            (feeder, video_server, audio_server),
            (video_sink, audio_sink),
            source_result,
            error_tail,
        )
    except Exception:
        _stop_xm_preview_process(
            process,
            (video_listener, audio_listener),
            (video_sink, audio_sink),
            (feeder, video_server, audio_server),
            error_reader,
        )
        if partial.exists():
            partial.unlink()
        raise
    else:
        _ = partial.rename(destination)
        return stats


def finalize_fragmented_preview(
    source: Path,
    *,
    timestamp_scale: float = 1.0,
    align_video_to_audio: bool = False,
) -> None:
    """Replace a streamed MP4 with an indexed fast-start copy for browser seeking."""
    if not source.is_file():
        raise FileNotFoundError(f"Fragmented preview does not exist: {source}")
    if timestamp_scale <= 0:
        raise ValueError("Preview timestamp scale must be greater than zero")
    indexed = source.with_name(source.stem + ".indexed.part.mp4")
    if indexed.exists():
        raise FileExistsError(f"Indexed preview destination already exists: {indexed}")
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    video_scale = timestamp_scale
    separate_audio_input = False
    if align_video_to_audio:
        video_duration, audio_duration = _preview_stream_durations(source)
        if video_duration is not None and audio_duration is not None:
            video_scale *= audio_duration / video_duration
            separate_audio_input = abs(video_scale - timestamp_scale) > _MINIMUM_TIMELINE_SCALE_DELTA
    if video_scale != 1.0:
        command.extend(("-itsscale", str(video_scale)))
    command.extend(("-i", str(source)))
    if separate_audio_input:
        command.extend(("-i", str(source)))
    command.extend(
        (
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?" if separate_audio_input else "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-n",
            str(indexed),
        )
    )
    try:
        _ = subprocess.run(command, check=True, capture_output=True, timeout=60)
        _ = indexed.replace(source)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if indexed.exists():
            indexed.unlink()
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFmpeg could not index the streamed preview") from error


def _preview_stream_durations(source: Path) -> tuple[float | None, float | None]:
    command = [
        _ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,duration",
        "-of",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, timeout=30)
        raw_payload: object = json.loads(result.stdout)
    except (json.JSONDecodeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", b"")
        message = detail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFprobe could not inspect the streamed preview") from error
    if not isinstance(raw_payload, dict):
        raise MediaError("FFprobe returned invalid preview stream metadata")
    payload = cast("dict[object, object]", raw_payload)
    streams_value = payload.get("streams")
    if not isinstance(streams_value, list):
        raise MediaError("FFprobe returned invalid preview stream metadata")
    durations: dict[str, float] = {}
    for value in cast("list[object]", streams_value):
        if not isinstance(value, dict):
            continue
        stream = cast("dict[object, object]", value)
        codec_type = stream.get("codec_type")
        duration = stream.get("duration")
        if not isinstance(codec_type, str) or codec_type in durations:
            continue
        try:
            parsed_duration = float(duration) if isinstance(duration, (int, float, str)) else 0.0
        except ValueError:
            continue
        if parsed_duration > 0:
            durations[codec_type] = parsed_duration
    return durations.get("video"), durations.get("audio")


def _preview_pipes(process: subprocess.Popen[bytes]) -> tuple[BinaryIO, _ReadableBinaryPipe, BinaryIO]:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        process.kill()
        _ = process.wait()
        raise MediaError("FFmpeg did not expose the preview streaming pipes")
    return (
        cast("BinaryIO", process.stdin),
        cast("_ReadableBinaryPipe", process.stdout),
        cast("BinaryIO", process.stderr),
    )


def _preview_output_pipes(process: subprocess.Popen[bytes]) -> tuple[_ReadableBinaryPipe, BinaryIO]:
    if process.stdout is None or process.stderr is None:
        process.kill()
        _ = process.wait()
        raise MediaError("FFmpeg did not expose the XM preview output pipes")
    return cast("_ReadableBinaryPipe", process.stdout), cast("BinaryIO", process.stderr)


def _loopback_listener() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
    except OSError:
        listener.close()
        raise
    return listener


def _listener_port(listener: socket.socket) -> int:
    address = listener.getsockname()
    if not isinstance(address, tuple):
        raise MediaError("Could not determine the local preview media port")
    fields = cast("tuple[object, ...]", address)
    if len(fields) < _MINIMUM_TCP_ADDRESS_FIELDS or not isinstance(fields[1], int):
        raise MediaError("Could not determine the local preview media port")
    return fields[1]


def _feed_xm_preview_source(
    source: Callable[[BinaryIO], int],
    video_sink: _QueuedMediaSink,
    audio_sink: _QueuedMediaSink,
    state: _XMPreviewFeedState,
) -> None:
    def publish_frame_rate(frames_per_second: float) -> None:
        _set_future_result_once(state.frame_rate_result, frames_per_second)

    demuxer = XMRecordingDemuxer(
        cast("BinaryIO", video_sink),
        cast("BinaryIO", audio_sink),
        frame_rate_consumer=publish_frame_rate,
    )
    try:
        source_bytes = source(cast("BinaryIO", demuxer))
        stats = demuxer.finish()
        _set_future_result_once(
            state.frame_rate_result,
            stats.frames_per_second or state.fallback_frames_per_second,
        )
        state.source_result.set_result((source_bytes, stats))
    except Exception as error:  # noqa: BLE001 - preserve failures crossing the source-thread boundary.
        _set_future_exception_once(state.frame_rate_result, error)
        state.source_result.set_exception(error)
    finally:
        video_sink.close()
        audio_sink.close()


def _xm_preview_frame_rate(result: Future[float], fallback: float) -> float:
    try:
        return result.result(timeout=_XM_FRAME_RATE_DETECTION_TIMEOUT_SECONDS)
    except TimeoutError:
        _set_future_result_once(result, fallback)
        return result.result()


def _set_future_result_once(result: Future[float], value: float) -> None:
    if result.done():
        return
    with suppress(InvalidStateError):
        result.set_result(value)


def _set_future_exception_once(result: Future[float], error: Exception) -> None:
    if result.done():
        return
    with suppress(InvalidStateError):
        result.set_exception(error)


def _require_xm_preview_completion(
    return_code: int,
    threads: tuple[threading.Thread, threading.Thread, threading.Thread],
    sinks: tuple[_QueuedMediaSink, _QueuedMediaSink],
    source_result: Future[tuple[int, XMStreamStats]],
    error_tail: bytearray,
) -> XMStreamStats:
    feeder, video_server, audio_server = threads
    video_sink, audio_sink = sinks
    if feeder.is_alive():
        raise MediaError("Camera XM preview source did not stop after FFmpeg completed")
    if video_server.is_alive() or audio_server.is_alive():
        raise MediaError("FFmpeg preview media inputs did not close cleanly")
    message = error_tail.decode("utf-8", errors="replace").strip()
    if return_code != 0:
        raise MediaError(message or "FFmpeg could not stream the audio-enabled media preview")
    failures = [*video_sink.failures, *audio_sink.failures]
    if failures:
        detail = f"FFmpeg stopped reading recovered camera media: {failures[0]}"
        if message:
            detail = f"{detail}\n{message}"
        raise MediaError(detail) from failures[0]
    _source_bytes, stats = source_result.result()
    if stats.video_bytes <= 0:
        raise MediaError("Camera preview contained no recoverable video")
    return stats


def _stop_xm_preview_process(
    process: subprocess.Popen[bytes] | None,
    listeners: tuple[socket.socket, socket.socket],
    sinks: tuple[_QueuedMediaSink, _QueuedMediaSink],
    threads: tuple[threading.Thread, threading.Thread, threading.Thread],
    error_reader: threading.Thread | None,
) -> None:
    for sink in sinks:
        sink.abort()
    for listener in listeners:
        with suppress(OSError):
            listener.close()
    if process is not None and process.poll() is None:
        process.kill()
        _ = process.wait()
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=30)
    if error_reader is not None and error_reader.ident is not None:
        error_reader.join(timeout=5)


def _feed_preview_source(
    source: Callable[[BinaryIO], int],
    standard_input: BinaryIO,
    result: Future[int],
) -> None:
    demuxer = XMRecordingDemuxer(standard_input)
    try:
        source_bytes = source(cast("BinaryIO", demuxer))
        stats = demuxer.finish()
        if stats.video_bytes <= 0:
            result.set_exception(MediaError("Camera preview contained no recoverable video"))
            return
        result.set_result(source_bytes)
    except Exception as error:  # noqa: BLE001 - preserve failures crossing the source-thread boundary.
        result.set_exception(error)
    finally:
        with suppress(OSError):
            standard_input.close()


def _drain_preview_errors(standard_error: BinaryIO, error_tail: bytearray) -> None:
    while chunk := standard_error.read(8192):
        error_tail.extend(chunk)
        if len(error_tail) > 64 * 1024:
            del error_tail[: len(error_tail) - 64 * 1024]


def _require_preview_completion(return_code: int, feeder: threading.Thread, error_tail: bytearray) -> None:
    if feeder.is_alive():
        raise MediaError("Camera preview source did not stop after FFmpeg completed")
    if return_code != 0:
        message = error_tail.decode("utf-8", errors="replace").strip()
        raise MediaError(message or "FFmpeg could not stream the media preview")


def _stop_preview_process(
    process: subprocess.Popen[bytes],
    feeder: threading.Thread,
    error_reader: threading.Thread,
) -> None:
    if process.poll() is None:
        process.kill()
        _ = process.wait()
    feeder.join(timeout=30)
    error_reader.join(timeout=5)


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise MediaError("FFmpeg is required but was not found on PATH")
    return executable


def _ffprobe() -> str:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise MediaError("FFprobe is required but was not found on PATH")
    return executable
