"""FFmpeg-backed access to the camera's RTSP stream."""

from __future__ import annotations

import shutil
import subprocess
import threading
from concurrent.futures import Future
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import BinaryIO


class MediaError(RuntimeError):
    """Raised when FFmpeg is missing or cannot read the RTSP stream."""


class _ReadableBinaryPipe(Protocol):
    def read1(self, size: int = -1) -> bytes:
        """Read currently buffered bytes without waiting to fill the request."""
        ...


def rtsp_url(host: str, username: str = "admin", password: str = "") -> str:
    """Build the authenticated RTSP URL accepted by the GrowCam firmware."""
    user = quote(username, safe="")
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


def start_mjpeg(
    host: str,
    username: str = "admin",
    password: str = "",
    *,
    frames_per_second: int = 5,
    width: int = 1280,
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
        rtsp_url(host, username, password),
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
    frames_per_second: float = 7.5,
) -> None:
    """Convert a raw GrowCam SD-card download into a playable Matroska file.

    GrowCam names these files ``.h264``, but this model actually stores a raw
    HEVC video elementary stream. Its timestamps are implicit, so the observed
    recording rate must be supplied while remuxing.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Recording source does not exist: {source}")
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
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-n",
        str(partial),
    ]
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


def build_fragmented_preview(
    source: Callable[[BinaryIO], int],
    destination: Path,
    consumer: Callable[[bytes], None],
    *,
    frames_per_second: float,
    width: int = 1280,
) -> int:
    """Transcode camera packets into an atomic cache file while streaming MP4 fragments."""
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


def finalize_fragmented_preview(source: Path, *, timestamp_scale: float = 1.0) -> None:
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
    if timestamp_scale != 1.0:
        command.extend(("-itsscale", str(timestamp_scale)))
    command.extend(
        (
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
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


def _feed_preview_source(
    source: Callable[[BinaryIO], int],
    standard_input: BinaryIO,
    result: Future[int],
) -> None:
    try:
        result.set_result(source(standard_input))
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
