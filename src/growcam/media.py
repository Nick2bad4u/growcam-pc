"""FFmpeg-backed access to the camera's RTSP stream."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from pathlib import Path


class MediaError(RuntimeError):
    """Raised when FFmpeg is missing or cannot read the RTSP stream."""


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


def _ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise MediaError("FFmpeg is required but was not found on PATH")
    return executable
