"""Local-only browser interface for a GrowCam camera."""

from __future__ import annotations

import errno
import json
import os
import re
import socket
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

from .dvrip import DVRIPClient
from .media import MediaError, remux_recording, snapshot, start_mjpeg, transcode_timelapse_preview
from .media_cache import MediaCache
from .timelapse import (
    TIMELAPSE_CONFIG_NAME,
    TimelapseConfig,
    TimelapseConflictError,
    TimelapseUpdateError,
    TimelapseValidationError,
    update_timelapse,
)

_MAX_REQUEST_BYTES = 16 * 1024
_WINDOWS_ADDRESS_IN_USE = 10048
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; img-src 'self' blob:; media-src 'self' blob:; object-src 'none'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'"
)
_CAMERA_FILE_PATTERN = re.compile(
    r"^/idea0/(?P<date>\d{4}-\d{2}-\d{2})/\d+/"
    r"(?P<start>\d{2}\.\d{2}\.\d{2})-(?P<end>\d{2}\.\d{2}\.\d{2})"
    r"\[(?P<event>[A-Z])\].*\.(?:_?h264|jpg)$"
)


class WebRequestError(RuntimeError):
    """An expected browser request error with an explicit HTTP status."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        """Store the response status and user-facing message."""
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class WebConfig:
    """Connection settings shared by local HTTP request handlers."""

    camera_host: str
    camera_port: int = 34567
    username: str = "admin"
    password: str = ""


class GrowCamHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying immutable camera configuration."""

    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], config: WebConfig) -> None:
        """Create a server for one immutable camera configuration."""
        self.config = config
        self.camera_operation_lock = threading.Lock()
        super().__init__(address, GrowCamHandler)
        self.media_cache = MediaCache(_preview_cache_directory(), self.camera_operation_lock)

    @override
    def server_bind(self) -> None:
        """Bind exclusively so a stale process cannot share the listen port."""
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()


class GrowCamHandler(BaseHTTPRequestHandler):
    """Serve static UI assets and guarded camera API endpoints."""

    server_version = "GrowCam"
    sys_version = ""

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Keep routine camera paths and request noise out of terminal history."""
        _ = (format, args)

    @override
    def end_headers(self) -> None:
        """Add browser hardening headers to every local response."""
        self.send_header("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: C901, PLR0912 - explicit route dispatch is clearer here.
        """Route one HTTP GET request."""
        request = urlparse(self.path)
        try:
            if request.path == "/":
                self._static("index.html", "text/html; charset=utf-8")
            elif request.path == "/app.css":
                self._static("app.css", "text/css; charset=utf-8")
            elif request.path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
            elif request.path == "/favicon.svg":
                self._static("favicon.svg", "image/svg+xml")
            elif request.path == "/api/info":
                self._json(self._camera_info())
            elif request.path == "/api/recordings":
                query = parse_qs(request.query)
                hours = min(max(float(query.get("hours", ["24"])[0]), 0.1), 168.0)
                self._json(self._recordings(hours))
            elif request.path == "/api/history":
                query = parse_qs(request.query)
                self._json(self._history(query.get("date", [""])[0]))
            elif request.path == "/api/history/preview":
                query = parse_qs(request.query)
                camera_file = query.get("file", [""])[0]
                at = query.get("at", [""])[0]
                duration = query.get("duration", [""])[0]
                self._history_preview(camera_file, at=at, duration=duration)
            elif request.path == "/api/timelapse":
                self._json(self._timelapse_state())
            elif request.path == "/api/timelapse/preview":
                query = parse_qs(request.query)
                camera_file = query.get("file", [""])[0]
                download = query.get("download", ["0"])[0] == "1"
                self._timelapse_preview(camera_file, download=download)
            elif request.path == "/api/download":
                query = parse_qs(request.query)
                camera_file = query.get("file", [""])[0]
                self._download(camera_file)
            elif request.path == "/snapshot.jpg":
                self._snapshot()
            elif request.path == "/stream.mjpg":
                self._mjpeg()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except WebRequestError as error:
            self._json({"error": str(error)}, status=error.status)
        except (OSError, ValueError, RuntimeError) as error:
            self._json({"error": str(error)}, status=HTTPStatus.BAD_GATEWAY)

    def do_POST(self) -> None:
        """Apply a guarded local timelapse configuration update."""
        request = urlparse(self.path)
        if request.path != "/api/timelapse":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_request()
            expected_revision, desired = _timelapse_update_request(payload)
            server = cast("GrowCamHTTPServer", self.server)
            _claim_camera_operation(server)
            try:
                with self._camera() as camera:
                    result = update_timelapse(camera, desired, expected_revision=expected_revision)
            finally:
                server.camera_operation_lock.release()
            self._json(
                {
                    "config": result.current.to_api(now=datetime.now()),
                    "previousRevision": result.previous.revision,
                }
            )
        except WebRequestError as error:
            self._json({"error": str(error)}, status=error.status)
        except TimelapseValidationError as error:
            self._json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except TimelapseConflictError as error:
            self._json({"error": str(error)}, status=HTTPStatus.CONFLICT)
        except TimelapseUpdateError as error:
            self._json(
                {"error": str(error), "rollbackVerified": error.rollback_verified},
                status=HTTPStatus.BAD_GATEWAY,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self._json({"error": str(error)}, status=HTTPStatus.BAD_GATEWAY)

    def _camera(self) -> DVRIPClient:
        config = cast("GrowCamHTTPServer", self.server).config
        return DVRIPClient(
            config.camera_host,
            config.camera_port,
            config.username,
            config.password,
        )

    def _camera_info(self) -> dict[str, Any]:
        with self._camera() as camera:
            if camera.login_info is None:
                raise RuntimeError("Camera login metadata is unavailable")
            return {
                "login": asdict(camera.login_info),
                "system": camera.system_info("SystemInfo"),
                "storage": camera.system_info("StorageInfo"),
                "workState": camera.system_info("WorkState"),
            }

    def _recordings(self, hours: float) -> dict[str, Any]:
        end = datetime.now()
        with self._camera() as camera:
            recordings = camera.recordings(
                start=end - timedelta(hours=hours),
                end=end,
                channel=0,
                event="R",
            )
        return {"hours": hours, "recordings": recordings}

    def _history(self, requested_date: str) -> dict[str, object]:
        selected_date = _history_date(requested_date)
        day_start = datetime.combine(selected_date, time.min)
        now = datetime.now()
        day_end = min(day_start + timedelta(days=1), now)
        if day_end <= day_start:
            recordings: list[dict[str, Any]] = []
        else:
            with self._camera() as camera:
                recordings = camera.recordings(
                    start=day_start,
                    end=day_end,
                    channel=0,
                    event="R",
                )
        return {
            "date": selected_date.isoformat(),
            "recordings": _browser_recordings(
                recordings,
                active_filename=_active_history_filename(recordings, selected_date, now),
            ),
        }

    def _timelapse_state(self) -> dict[str, object]:
        with self._camera() as camera:
            return _timelapse_state(camera, now=datetime.now())

    def _snapshot(self) -> None:
        config = cast("GrowCamHTTPServer", self.server).config
        content = snapshot(config.camera_host, config.username, config.password)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        _ = self.wfile.write(content)

    def _download(self, camera_file: str) -> None:
        match = _camera_file(camera_file)
        server = cast("GrowCamHTTPServer", self.server)
        _claim_camera_operation(server)
        try:
            timelapse_record: dict[str, object] | None = None
            if match.group("event") == "E":
                state = self._timelapse_state()
                timelapse_record = _matching_recording(state, camera_file)
                if cast("bool", timelapse_record["active"]):
                    raise WebRequestError(
                        HTTPStatus.CONFLICT,
                        "The active timelapse must be previewed; its final download is available after completion",
                    )
            with TemporaryDirectory(prefix="growcam-") as temporary_directory:
                raw = Path(temporary_directory) / "camera.raw-hevc"
                playable = Path(temporary_directory) / "recording.mkv"
                with self._camera() as camera:
                    if timelapse_record is None:
                        _ = camera.download(camera_file, raw)
                    else:
                        _ = camera.playback_by_time_snapshot(
                            start=_camera_datetime(timelapse_record["beginTime"]),
                            end=_camera_datetime(timelapse_record["endTime"]),
                            destination=raw,
                            file_type=5,
                        )
                frame_rate = 25.0 if match.group("event") == "E" else 7.5
                remux_recording(raw, playable, frames_per_second=frame_rate)
                self._send_file(
                    playable,
                    content_type="video/x-matroska",
                    disposition=f'attachment; filename="{_download_name(camera_file)}"',
                )
        finally:
            server.camera_operation_lock.release()

    def _timelapse_preview(self, camera_file: str, *, download: bool) -> None:
        _ = _camera_file(camera_file, required_event="E")
        server = cast("GrowCamHTTPServer", self.server)
        with self._camera() as camera:
            state = _timelapse_state(camera, now=datetime.now())
        record = _matching_recording(state, camera_file)
        resolved_file = cast("str", record["fileName"])
        key = f"timelapse:{_recording_identity(resolved_file)}:{record['sizeBytes']}"

        def build(preview: Path) -> None:
            raw = preview.with_suffix(".raw-hevc")
            try:
                with self._camera() as camera:
                    _ = camera.playback_by_time_snapshot(
                        start=_camera_datetime(record["beginTime"]),
                        end=_camera_datetime(record["endTime"]),
                        destination=raw,
                        file_type=5,
                    )
                transcode_timelapse_preview(raw, preview, frames_per_second=25.0)
            finally:
                if raw.exists():
                    raw.unlink()

        preview, cache_hit = server.media_cache.get_or_build(key, ".mp4", build)
        mode = "attachment" if download else "inline"
        self._send_file(
            preview,
            content_type="video/mp4",
            disposition=f'{mode}; filename="{_preview_name(resolved_file)}"',
            cache_status="HIT" if cache_hit else "MISS",
        )

    def _history_preview(self, camera_file: str, *, at: str, duration: str) -> None:
        match = _camera_file(camera_file, required_event="R")
        selected_date = date.fromisoformat(match.group("date"))
        day_start = datetime.combine(selected_date, time.min)
        day_end = min(day_start + timedelta(days=1), datetime.now())
        if day_end <= day_start:
            raise WebRequestError(HTTPStatus.NOT_FOUND, "No recording exists for that future date")
        with self._camera() as camera:
            recordings = camera.recordings(
                start=day_start,
                end=day_end,
                channel=0,
                event="R",
            )
        state: dict[str, object] = {
            "recordings": _browser_recordings(
                recordings,
                active_filename=_active_history_filename(recordings, selected_date, datetime.now()),
            )
        }
        record = _matching_recording(state, camera_file)
        resolved_file = cast("str", record["fileName"])
        preview_range = _history_preview_range(record, at=at, duration=duration)
        range_key = (
            "full" if preview_range is None else f"{preview_range[0].isoformat()}:{preview_range[1].isoformat()}"
        )
        key = f"history:{_recording_identity(resolved_file)}:{record['sizeBytes']}:{range_key}"
        server = cast("GrowCamHTTPServer", self.server)

        def build(preview: Path) -> None:
            raw = preview.with_suffix(".raw-hevc")
            try:
                with self._camera() as camera:
                    if preview_range is not None:
                        _ = camera.playback_by_time_snapshot(
                            start=preview_range[0],
                            end=preview_range[1],
                            destination=raw,
                            file_type=0,
                        )
                    elif cast("bool", record["active"]):
                        _ = camera.playback_snapshot(
                            resolved_file,
                            raw,
                            expected_bytes=cast("int", record["sizeBytes"]),
                        )
                    else:
                        _ = camera.download(resolved_file, raw)
                transcode_timelapse_preview(raw, preview, frames_per_second=7.5)
            finally:
                if raw.exists():
                    raw.unlink()

        preview, cache_hit = server.media_cache.get_or_build(key, ".mp4", build)
        self._send_file(
            preview,
            content_type="video/mp4",
            disposition=f'inline; filename="{_history_preview_name(resolved_file)}"',
            cache_status="HIT" if cache_hit else "MISS",
        )

    def _mjpeg(self) -> None:
        config = cast("GrowCamHTTPServer", self.server).config
        process = start_mjpeg(config.camera_host, config.username, config.password)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=growcam")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            if process.stdout is None:
                raise MediaError("FFmpeg did not expose an MJPEG stream")
            while chunk := process.stdout.read(64 * 1024):
                _ = self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            process.terminate()
            try:
                _ = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _ = process.wait()

    def _read_json_request(self) -> dict[str, object]:  # noqa: C901 - defensive HTTP boundary validation.
        if self.headers.get("X-GrowCam-Request") != "1":
            raise WebRequestError(HTTPStatus.FORBIDDEN, "Missing local request confirmation header")
        if not self.headers.get_content_type() == "application/json":
            raise WebRequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Request body must be application/json")
        origin = self.headers.get("Origin")
        port = cast("tuple[str, int]", self.server.server_address)[1]
        allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        request_host = self.headers.get("Host")
        if request_host is not None:
            allowed_origins.add(f"http://{request_host}")
        if origin is not None and origin not in allowed_origins:
            raise WebRequestError(HTTPStatus.FORBIDDEN, "Cross-origin configuration updates are not allowed")
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise WebRequestError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(content_length)
        except ValueError as error:
            raise WebRequestError(HTTPStatus.BAD_REQUEST, "Content-Length is invalid") from error
        if not 0 < length <= _MAX_REQUEST_BYTES:
            raise WebRequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body is too large")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WebRequestError(HTTPStatus.BAD_REQUEST, "Request body is not valid JSON") from error
        if not isinstance(value, dict):
            raise WebRequestError(HTTPStatus.BAD_REQUEST, "Request body must be a JSON object")
        payload: dict[str, object] = {}
        for key, item in cast("dict[object, object]", value).items():
            if not isinstance(key, str):
                raise WebRequestError(HTTPStatus.BAD_REQUEST, "Request body contains a non-string key")
            payload[key] = item
        return payload

    def _send_file(
        self,
        path: Path,
        *,
        content_type: str,
        disposition: str,
        cache_status: str | None = None,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        if cache_status is not None:
            self.send_header("X-GrowCam-Preview-Cache", cache_status)
        self.end_headers()
        try:
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    _ = self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _static(self, name: str, content_type: str) -> None:
        content = files("growcam").joinpath("static", name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        _ = self.wfile.write(content)

    def _json(self, payload: object, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        _ = self.wfile.write(content)


def serve(
    config: WebConfig,
    listen: str,
    port: int,
    *,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Run the local browser interface until interrupted."""
    try:
        server = GrowCamHTTPServer((listen, port), config)
    except OSError as error:
        if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == _WINDOWS_ADDRESS_IN_USE:
            url = f"http://{listen}:{port}/"
            message = (
                f"Local port {port} is already in use. GrowCam may already be running at {url} "
                f"Stop that instance or choose another port with --http-port."
            )
            raise OSError(message) from error
        raise
    try:
        if on_ready is not None:
            on_ready()
        server.serve_forever()
    finally:
        server.server_close()


def _timelapse_state(camera: DVRIPClient, *, now: datetime) -> dict[str, object]:
    config = TimelapseConfig.from_camera(camera.config_get(TIMELAPSE_CONFIG_NAME))
    search_start = min(config.start_time, now) - timedelta(days=1)
    recordings = camera.recordings(start=search_start, end=now, channel=0, event="E")
    active_filename = ""
    if config.enabled and config.start_time <= now < config.end_time and recordings:
        active_filename = str(recordings[-1].get("FileName", ""))
    return {
        "config": config.to_api(now=now),
        "recordings": list(reversed(_browser_recordings(recordings, active_filename=active_filename))),
    }


def _timelapse_update_request(payload: dict[str, object]) -> tuple[str, TimelapseConfig]:
    expected_revision = payload.get("expectedRevision")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise TimelapseValidationError("expectedRevision must be a non-empty string")
    return expected_revision, TimelapseConfig.from_browser(payload.get("config"))


def _claim_camera_operation(server: GrowCamHTTPServer) -> None:
    if not server.camera_operation_lock.acquire(blocking=False):
        raise WebRequestError(HTTPStatus.CONFLICT, "A camera media operation is still running")


def _matching_recording(state: dict[str, object], camera_file: str) -> dict[str, object]:
    records_value = state.get("recordings")
    if not isinstance(records_value, list):
        raise WebRequestError(HTTPStatus.BAD_GATEWAY, "Camera timelapse index is invalid")
    requested_identity = _recording_identity(camera_file)
    for item in cast("list[object]", records_value):
        if isinstance(item, dict):
            record = cast("dict[str, object]", item)
            candidate = record.get("fileName")
            if candidate == camera_file or (
                isinstance(candidate, str) and _recording_identity(candidate) == requested_identity
            ):
                return record
    raise WebRequestError(HTTPStatus.NOT_FOUND, "Recording was not found on the camera")


def _browser_recordings(
    recordings: list[dict[str, Any]],
    *,
    active_filename: str,
) -> list[dict[str, object]]:
    browser_records: list[dict[str, object]] = []
    for item in recordings:
        filename = str(item.get("FileName", ""))
        browser_records.append(
            {
                "beginTime": str(item.get("BeginTime", "")),
                "endTime": str(item.get("EndTime", "")),
                "fileName": filename,
                "sizeBytes": _file_length_bytes(item.get("FileLength")),
                "active": filename == active_filename,
            }
        )
    return browser_records


def _active_history_filename(
    recordings: list[dict[str, Any]],
    selected_date: date,
    now: datetime,
) -> str:
    if selected_date != now.date() or not recordings:
        return ""
    return str(recordings[-1].get("FileName", ""))


def _history_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "History date must use YYYY-MM-DD") from error


def _history_preview_range(
    record: dict[str, object],
    *,
    at: str,
    duration: str,
) -> tuple[datetime, datetime] | None:
    if not at and not duration:
        return None
    if not at or not duration:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind requires both at and duration")
    try:
        requested_start = datetime.fromisoformat(at)
        duration_seconds = int(duration)
    except ValueError as error:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind time or duration is invalid") from error
    if duration_seconds not in {120, 300}:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind duration must be 120 or 300 seconds")
    recording_start = _camera_datetime(record["beginTime"])
    recording_end = _camera_datetime(record["endTime"])
    if not recording_start <= requested_start < recording_end:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind time is outside the selected recording")
    requested_end = min(requested_start + timedelta(seconds=duration_seconds), recording_end)
    if requested_end <= requested_start:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind window is empty")
    return requested_start, requested_end


def _preview_cache_directory(
    *,
    platform_name: str = sys.platform,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    values = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform_name == "win32":
        local_app_data = values.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return root / "GrowCam" / "preview-cache"
    if platform_name == "darwin":
        return user_home / "Library" / "Caches" / "GrowCam" / "preview-cache"
    xdg_cache_home = values.get("XDG_CACHE_HOME")
    root = Path(xdg_cache_home) if xdg_cache_home else user_home / ".cache"
    return root / "growcam" / "preview-cache"


def _camera_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise WebRequestError(HTTPStatus.BAD_GATEWAY, "Camera returned an invalid recording timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - camera time is local and naive.
    except ValueError as error:
        raise WebRequestError(HTTPStatus.BAD_GATEWAY, "Camera returned an invalid recording timestamp") from error


def _recording_identity(camera_file: str) -> str:
    match = _camera_file(camera_file)
    return f"{match.group('date')}:{match.group('start')}:{match.group('event')}"


def _file_length_bytes(value: object) -> int:
    if isinstance(value, str) and value.startswith("0x"):
        try:
            kibibytes = int(value, 16)
        except ValueError as error:
            raise RuntimeError("Camera returned an invalid timelapse file size") from error
    elif isinstance(value, int) and not isinstance(value, bool):
        kibibytes = value
    else:
        raise RuntimeError("Camera returned an invalid timelapse file size")
    if kibibytes <= 0:
        raise RuntimeError("Camera returned an empty timelapse recording")
    return kibibytes * 1024


def _camera_file(camera_file: str, *, required_event: str | None = None) -> re.Match[str]:
    match = _CAMERA_FILE_PATTERN.fullmatch(camera_file)
    if match is None:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Camera file path is invalid")
    if required_event is not None and match.group("event") != required_event:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "The selected recording is not a timelapse")
    return match


def _download_name(camera_file: str) -> str:
    match = _CAMERA_FILE_PATTERN.fullmatch(camera_file)
    if match is None:
        return "growcam-recording.mkv"
    start = match.group("start").replace(".", "-")
    end = match.group("end").replace(".", "-")
    kind = "timelapse-" if match.group("event") == "E" else ""
    return f"growcam-{kind}{match.group('date')}_{start}_{end}.mkv"


def _preview_name(camera_file: str) -> str:
    return _download_name(camera_file).replace(".mkv", "-preview.mp4")


def _history_preview_name(camera_file: str) -> str:
    return _download_name(camera_file).replace(".mkv", "-rewind.mp4")
