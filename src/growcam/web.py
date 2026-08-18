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
import time as time_module
from collections import OrderedDict
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping
    from contextlib import AbstractContextManager
    from io import BufferedIOBase
    from typing import BinaryIO
    from urllib.parse import ParseResult

from .dvrip import DVRIPClient, DVRIPError
from .media import (
    MediaError,
    build_fragmented_preview,
    build_xm_fragmented_preview,
    finalize_fragmented_preview,
    remux_recording,
    snapshot,
    start_live_audio,
    start_mjpeg,
)
from .media_cache import MediaCache, MediaCacheBusyError
from .settings import (
    SettingsConflictError,
    SettingsError,
    SettingsStore,
    SettingsValidationError,
)
from .settings import (
    settings_path as _settings_path,
)
from .timelapse import (
    TIMELAPSE_CONFIG_NAME,
    TimelapseConfig,
    TimelapseConflictError,
    TimelapseUpdateError,
    TimelapseValidationError,
    update_timelapse,
)
from .xm_media import demux_xm_recording

_MAX_REQUEST_BYTES = 16 * 1024
_RECORDING_METADATA_TTL_SECONDS = 60.0
_MAX_RECORDING_METADATA_ENTRIES = 2048
_HISTORY_PREVIEW_VERSION = "indexed-v16-av-aligned"
_TIMELAPSE_PREVIEW_VERSION = "indexed-v22-unthrottled-progress"
_TIMELAPSE_STREAM_FRAMES_PER_SECOND = 2.0
_TIMELAPSE_CACHED_FRAMES_PER_SECOND = 25.0
_WINDOWS_ADDRESS_IN_USE = 10048
_LOCKED_LOGIN_RETURN_CODE = 205
_SETTINGS_API = "/api/settings"
_TIMELAPSE_API = "/api/timelapse"
_MP4_CONTENT_TYPE = "video/mp4"
_INVALID_BYTE_RANGE = "Media byte range is invalid"
_ACCOUNT_REJECTION_RETURN_CODES = frozenset({106, 203, 204, 205, 206, 207, 430})
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


class PreviewClientDisconnectedError(ConnectionError):
    """Signal that an abandoned browser preview should stop building."""


@dataclass(frozen=True)
class WebConfig:
    """Connection settings shared by local HTTP request handlers."""

    camera_host: str
    camera_port: int = 34567
    username: str = "admin"
    password: str = ""


class CameraControlStatus(StrEnum):
    """Lifecycle states for the server-wide DVRIP control connection."""

    UNVERIFIED = "unverified"
    AVAILABLE = "available"
    BLOCKED = "blocked"


class LiveQuality(StrEnum):
    """Camera RTSP profiles exposed by the dashboard."""

    SD = "sd"
    FHD = "fhd"


@dataclass(frozen=True)
class LiveStreamProfile:
    """Camera stream index and browser-facing MJPEG output width."""

    stream_index: int
    output_width: int


_LIVE_STREAM_PROFILES = {
    LiveQuality.SD: LiveStreamProfile(stream_index=1, output_width=800),
    LiveQuality.FHD: LiveStreamProfile(stream_index=0, output_width=1920),
}


@dataclass(frozen=True)
class CameraControlState:
    """Thread-safe snapshot of whether new DVRIP logins are permitted."""

    status: CameraControlStatus
    message: str | None = None
    return_code: int | None = None
    manual_retry_permitted: bool = False

    @property
    def retry_allowed(self) -> bool:
        """Allow at most one explicit retry for a non-account failure."""
        return (
            self.status is CameraControlStatus.BLOCKED
            and self.manual_retry_permitted
            and self.return_code != _LOCKED_LOGIN_RETURN_CODE
        )

    def to_api(self) -> dict[str, object]:
        """Return the stable browser representation of this state."""
        return {
            "status": self.status.value,
            "available": self.status is CameraControlStatus.AVAILABLE,
            "circuitOpen": self.status is CameraControlStatus.BLOCKED,
            "retryAllowed": self.retry_allowed,
            "locked": (self.status is CameraControlStatus.BLOCKED and self.return_code == _LOCKED_LOGIN_RETURN_CODE),
            "returnCode": self.return_code,
            "message": self.message,
        }


class CameraControlError(WebRequestError):
    """Report an open DVRIP circuit without touching the camera again."""

    def __init__(self, state: CameraControlState) -> None:
        """Retain the control state for the browser error payload."""
        super().__init__(
            HTTPStatus.LOCKED,
            state.message or "Camera controls are paused until an explicit retry",
        )
        self.state = state


class CameraControlBusyError(WebRequestError):
    """Reject extra camera work instead of queueing another DVRIP operation."""

    def __init__(self) -> None:
        """Return a retryable conflict without opening another camera connection."""
        super().__init__(
            HTTPStatus.CONFLICT,
            "Camera controls are busy; wait for the current operation to finish",
        )


class CameraControlCoordinator:
    """Own one persistent DVRIP session and reject overlapping camera work."""

    def __init__(self, config: WebConfig) -> None:
        """Create an unverified coordinator for one immutable camera configuration."""
        self._config = config
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = CameraControlState(CameraControlStatus.UNVERIFIED)
        self._closed = False
        self._client: DVRIPClient | None = None
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    def snapshot(self) -> CameraControlState:
        """Return the current immutable state without waiting for a camera operation."""
        with self._state_lock:
            return self._state

    @contextmanager
    def camera(self, *, explicit_retry: bool = False) -> Generator[DVRIPClient, None, None]:
        """Yield the shared authenticated client or reject overlapping work."""
        if not self._operation_lock.acquire(blocking=False):
            raise CameraControlBusyError
        try:
            with self._state_lock:
                state = self._state
                closed = self._closed
            if closed:
                raise CameraControlError(state)
            if state.status is CameraControlStatus.BLOCKED and (not explicit_retry or not state.retry_allowed):
                raise CameraControlError(state)

            camera = self._client
            if camera is None:
                camera = DVRIPClient(
                    self._config.camera_host,
                    self._config.camera_port,
                    self._config.username,
                    self._config.password,
                )
                try:
                    _ = camera.connect()
                except (DVRIPError, OSError, TypeError, ValueError) as error:
                    state = self._blocked_state(
                        error,
                        allow_retry=not explicit_retry and self._manual_retry_is_safe(error),
                    )
                    with suppress(DVRIPError, OSError, TypeError, ValueError):
                        camera.close()
                    self._set_state(state)
                    raise CameraControlError(state) from error
                self._client = camera
                self._set_state(CameraControlState(CameraControlStatus.AVAILABLE))
                self._ensure_keepalive_thread()
            try:
                yield camera
            except (DVRIPError, OSError, TypeError, ValueError) as error:
                self._block_client(
                    error,
                    allow_retry=not explicit_retry and self._manual_retry_is_safe(error),
                )
                raise
        finally:
            self._operation_lock.release()

    def close(self) -> None:
        """Stop background heartbeats, log out once, and release the process lock."""
        with self._state_lock:
            self._closed = True
            self._state = CameraControlState(
                CameraControlStatus.BLOCKED,
                "Camera controls are closed because the GrowCam server is shutting down",
            )
            self._keepalive_stop.set()
            keepalive_thread = self._keepalive_thread
        if keepalive_thread is not None and keepalive_thread is not threading.current_thread():
            keepalive_thread.join()
        with self._operation_lock:
            camera = self._client
            self._client = None
            if camera is not None:
                camera.close()

    def _ensure_keepalive_thread(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            keepalive_thread = self._keepalive_thread
            if keepalive_thread is not None and keepalive_thread.is_alive():
                return
            self._keepalive_stop.clear()
            self._keepalive_thread = threading.Thread(
                target=self._run_keepalives,
                name="growcam-web-dvrip-keepalive",
                daemon=True,
            )
            self._keepalive_thread.start()

    def _run_keepalives(self) -> None:
        while not self._keepalive_stop.wait(self._keepalive_interval()):
            if not self._operation_lock.acquire(blocking=False):
                continue
            try:
                camera = self._client
                if camera is None:
                    continue
                try:
                    camera.keepalive()
                except (DVRIPError, OSError, TypeError, ValueError) as error:
                    self._block_client(error, allow_retry=self._manual_retry_is_safe(error))
            finally:
                self._operation_lock.release()

    def _keepalive_interval(self) -> float:
        camera = self._client
        if camera is None or camera.login_info is None:
            return 1.0
        return max(1.0, min(10.0, camera.login_info.keepalive_interval / 2))

    def _block_client(self, error: Exception, *, allow_retry: bool) -> None:
        camera = self._client
        self._client = None
        if camera is not None:
            with suppress(DVRIPError, OSError, TypeError, ValueError):
                camera.close()
        self._set_state(self._blocked_state(error, allow_retry=allow_retry))

    @staticmethod
    def _blocked_state(error: Exception, *, allow_retry: bool) -> CameraControlState:
        return CameraControlState(
            status=CameraControlStatus.BLOCKED,
            message=str(error),
            return_code=error.return_code if isinstance(error, DVRIPError) else None,
            manual_retry_permitted=allow_retry,
        )

    @staticmethod
    def _manual_retry_is_safe(error: Exception) -> bool:
        """Refuse retries after any camera-side account or login rejection."""
        if not isinstance(error, DVRIPError):
            return True
        return error.operation != "login" and error.return_code not in _ACCOUNT_REJECTION_RETURN_CODES

    def _set_state(self, state: CameraControlState) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._state = state


class GrowCamHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying immutable camera configuration."""

    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        config: WebConfig,
        *,
        settings_file: Path | None = None,
    ) -> None:
        """Create a server for one immutable camera configuration."""
        self.config = config
        self.camera_controls = CameraControlCoordinator(config)
        self.camera_operation_lock = threading.Lock()
        self.recording_metadata_lock = threading.Lock()
        self.recording_metadata: OrderedDict[str, tuple[float, dict[str, object]]] = OrderedDict()
        self.settings_store = SettingsStore(settings_file)
        app_settings = self.settings_store.snapshot()
        super().__init__(address, GrowCamHandler)
        self.media_cache = MediaCache(
            _preview_cache_directory(),
            self.camera_operation_lock,
            maximum_entries=app_settings.cache_max_entries,
            maximum_bytes=app_settings.cache_max_bytes,
        )

    def remember_recordings(self, recordings: list[dict[str, object]]) -> None:
        """Retain recently returned camera metadata for immediate preview requests."""
        now = time_module.monotonic()
        with self.recording_metadata_lock:
            self._discard_expired_recordings(now)
            for record in recordings:
                filename = record.get("fileName")
                if not isinstance(filename, str) or not filename:
                    continue
                self.recording_metadata[filename] = (now, dict(record))
                self.recording_metadata.move_to_end(filename)
            while len(self.recording_metadata) > _MAX_RECORDING_METADATA_ENTRIES:
                _ = self.recording_metadata.popitem(last=False)

    def cached_recording(self, filename: str) -> dict[str, object] | None:
        """Return fresh recording metadata previously sent to this browser session."""
        now = time_module.monotonic()
        with self.recording_metadata_lock:
            self._discard_expired_recordings(now)
            cached = self.recording_metadata.get(filename)
            if cached is None:
                return None
            self.recording_metadata.move_to_end(filename)
            return dict(cached[1])

    def _discard_expired_recordings(self, now: float) -> None:
        expired = [
            filename
            for filename, (stored_at, _record) in self.recording_metadata.items()
            if now - stored_at > _RECORDING_METADATA_TTL_SECONDS
        ]
        for filename in expired:
            del self.recording_metadata[filename]

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

    @override
    def server_close(self) -> None:
        """Release the persistent DVRIP session before closing the HTTP socket."""
        try:
            self.camera_controls.close()
        finally:
            super().server_close()


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

    def do_GET(self) -> None:
        """Route one HTTP GET request."""
        request = urlparse(self.path)
        try:
            self._route_get(request)
        except PreviewClientDisconnectedError:
            # Browsers routinely cancel a progressive media request after they
            # have enough data or replace it with a byte-range request. The
            # response may already be committed, so emitting a JSON 502 here
            # creates a false console error and a malformed second response.
            self.close_connection = True
        except MediaCacheBusyError as error:
            self._json({"error": str(error)}, status=HTTPStatus.CONFLICT)
        except CameraControlError as error:
            self._json(
                {"error": str(error), "cameraControl": error.state.to_api()},
                status=error.status,
            )
        except WebRequestError as error:
            self._json({"error": str(error)}, status=error.status)
        except (OSError, ValueError, RuntimeError) as error:
            self._json({"error": str(error)}, status=HTTPStatus.BAD_GATEWAY)

    def _route_get(self, request: ParseResult) -> None:
        """Dispatch a parsed GET request without mixing route selection and error handling."""
        static_route = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }.get(request.path)
        if static_route is not None:
            self._static(*static_route)
            return

        json_handler = {
            "/api/info": self._camera_info,
            _SETTINGS_API: self._settings_state,
            _TIMELAPSE_API: self._timelapse_state,
        }.get(request.path)
        if json_handler is not None:
            self._json(json_handler())
            return
        if request.path == "/api/camera-control":
            self._json(cast("GrowCamHTTPServer", self.server).camera_controls.snapshot().to_api())
            return

        query = parse_qs(request.query)
        if self._route_recording_get(request.path, query):
            return
        if request.path == "/snapshot.jpg":
            self._snapshot()
        elif request.path == "/stream.mjpg":
            self._mjpeg(_live_quality(query.get("quality", [LiveQuality.FHD.value])[0]))
        elif request.path == "/stream.mp3":
            self._live_audio()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _route_recording_get(self, path: str, query: dict[str, list[str]]) -> bool:
        """Serve recording and preview routes; return whether the path matched."""
        if path == "/api/recordings":
            hours = min(max(float(query.get("hours", ["24"])[0]), 0.1), 168.0)
            self._json(self._recordings(hours))
        elif path == "/api/history":
            self._json(self._history(query.get("date", [""])[0]))
        elif path == "/api/files":
            self._json(self._files(query.get("date", [""])[0]))
        elif path == "/api/history/preview":
            self._history_preview(
                query.get("file", [""])[0],
                at=query.get("at", [""])[0],
                duration=query.get("duration", [""])[0],
                video_codec=_preview_video_codec(query.get("videoCodec", ["h264"])[0]),
                cache_only=query.get("cacheOnly", ["0"])[0] == "1",
            )
        elif path == "/api/timelapse/preview":
            self._timelapse_preview(
                query.get("file", [""])[0],
                download=query.get("download", ["0"])[0] == "1",
                cache_only=query.get("cacheOnly", ["0"])[0] == "1",
            )
        elif path == "/api/download":
            self._download(query.get("file", [""])[0])
        else:
            return False
        return True

    def do_POST(self) -> None:  # noqa: C901, PLR0912 - each guarded update keeps explicit error mapping here.
        """Apply one guarded local configuration update."""
        request = urlparse(self.path)
        if request.path not in {
            _TIMELAPSE_API,
            _SETTINGS_API,
            "/api/cache/clear",
            "/api/camera-control/retry",
        }:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json_request()
            if request.path == _TIMELAPSE_API:
                self._apply_timelapse_settings(payload)
            elif request.path == _SETTINGS_API:
                self._apply_app_settings(payload)
            elif request.path == "/api/camera-control/retry":
                self._retry_camera_controls(payload)
            else:
                self._clear_media_cache(payload)
        except CameraControlError as error:
            self._json(
                {"error": str(error), "cameraControl": error.state.to_api()},
                status=error.status,
            )
        except WebRequestError as error:
            self._json({"error": str(error)}, status=error.status)
        except SettingsValidationError as error:
            self._json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except SettingsConflictError as error:
            self._json({"error": str(error)}, status=HTTPStatus.CONFLICT)
        except SettingsError as error:
            self._json({"error": str(error)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
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

    def _apply_timelapse_settings(self, payload: dict[str, object]) -> None:
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

    def _apply_app_settings(self, payload: dict[str, object]) -> None:
        expected_revision, values = _app_settings_update_request(payload)
        server = cast("GrowCamHTTPServer", self.server)
        updated = server.settings_store.update(expected_revision=expected_revision, values=values)
        _ = server.media_cache.reconfigure(
            maximum_entries=updated.cache_max_entries,
            maximum_bytes=updated.cache_max_bytes,
        )
        self._json(self._settings_state())

    def _clear_media_cache(self, payload: dict[str, object]) -> None:
        if payload:
            raise WebRequestError(HTTPStatus.BAD_REQUEST, "Cache clear request must be an empty JSON object")
        server = cast("GrowCamHTTPServer", self.server)
        try:
            stats = server.media_cache.clear()
        except RuntimeError as error:
            raise WebRequestError(HTTPStatus.CONFLICT, str(error)) from error
        self._json({"cache": stats.to_api()})

    def _retry_camera_controls(self, payload: dict[str, object]) -> None:
        if payload:
            raise WebRequestError(HTTPStatus.BAD_REQUEST, "Camera control retry must be an empty JSON object")
        self._json(self._camera_info(explicit_retry=True))

    def _camera(self, *, explicit_retry: bool = False) -> AbstractContextManager[DVRIPClient]:
        server = cast("GrowCamHTTPServer", self.server)
        return server.camera_controls.camera(explicit_retry=explicit_retry)

    def _camera_info(self, *, explicit_retry: bool = False) -> dict[str, Any]:
        server = cast("GrowCamHTTPServer", self.server)
        with self._camera(explicit_retry=explicit_retry) as camera:
            if camera.login_info is None:
                raise RuntimeError("Camera login metadata is unavailable")
            return {
                "login": asdict(camera.login_info),
                "system": camera.system_info("SystemInfo"),
                "storage": camera.system_info("StorageInfo"),
                "workState": camera.system_info("WorkState"),
                "cameraControl": server.camera_controls.snapshot().to_api(),
            }

    def _settings_state(self) -> dict[str, object]:
        server = cast("GrowCamHTTPServer", self.server)
        return {
            "settings": server.settings_store.snapshot().to_api(),
            "cache": server.media_cache.stats().to_api(),
            "persistent": server.settings_store.path is not None,
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
        browser_recordings = _browser_recordings(
            recordings,
            active_filename=_active_history_filename(recordings, selected_date, now),
        )
        browser_recordings = _playable_history_recordings(browser_recordings)
        cast("GrowCamHTTPServer", self.server).remember_recordings(browser_recordings)
        return {
            "date": selected_date.isoformat(),
            "recordings": browser_recordings,
        }

    def _files(self, requested_date: str) -> dict[str, object]:
        selected_date = _history_date(requested_date)
        day_start = datetime.combine(selected_date, time.min)
        now = datetime.now()
        day_end = min(day_start + timedelta(days=1), now)
        continuous: list[dict[str, Any]] = []
        timelapses: list[dict[str, Any]] = []
        active_timelapse_filename = ""
        if day_end > day_start:
            with self._camera() as camera:
                continuous = camera.recordings(
                    start=day_start,
                    end=day_end,
                    channel=0,
                    event="R",
                )
                timelapses = camera.recordings(
                    start=day_start,
                    end=day_end,
                    channel=0,
                    event="E",
                )
                config = TimelapseConfig.from_camera(camera.config_get(TIMELAPSE_CONFIG_NAME))
                current_timelapses, active_timelapse_filename = _timelapse_index(camera, config, now)
                indexed_by_name = {
                    str(record.get("FileName", "")): record for record in timelapses if record.get("FileName")
                }
                for record in current_timelapses:
                    filename = str(record.get("FileName", ""))
                    if filename and _recording_overlaps(record, day_start, day_end):
                        indexed_by_name[filename] = record
                timelapses = list(indexed_by_name.values())
        browser_files = _browser_files(
            continuous,
            timelapses,
            active_recording_filename=_active_history_filename(continuous, selected_date, now),
            active_timelapse_filename=active_timelapse_filename,
        )
        cast("GrowCamHTTPServer", self.server).remember_recordings(browser_files)
        return {
            "date": selected_date.isoformat(),
            "files": browser_files,
            "summary": {
                "count": len(browser_files),
                "recordings": len(continuous),
                "timelapses": len(timelapses),
                "sizeBytes": sum(cast("int", item["sizeBytes"]) for item in browser_files),
            },
        }

    def _timelapse_state(self) -> dict[str, object]:
        with self._camera() as camera:
            state = _timelapse_state(camera, now=datetime.now())
        records = state.get("recordings")
        if isinstance(records, list):
            cast("GrowCamHTTPServer", self.server).remember_recordings(
                [
                    cast("dict[str, object]", record)
                    for record in cast("list[object]", records)
                    if isinstance(record, dict)
                ]
            )
        return state

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
            record: dict[str, object]
            cached_record = server.cached_recording(camera_file)
            if cached_record is not None:
                record = cached_record
            elif match.group("event") == "E":
                record = _matching_recording(self._timelapse_state(), camera_file)
            else:
                selected_date = date.fromisoformat(match.group("date"))
                day_start = datetime.combine(selected_date, time.min)
                day_end = min(day_start + timedelta(days=1), datetime.now())
                if day_end <= day_start:
                    raise WebRequestError(HTTPStatus.NOT_FOUND, "No recording exists for that future date")
                record = self._history_recording(camera_file, selected_date, day_start, day_end)
            if cast("bool", record["active"]):
                raise WebRequestError(
                    HTTPStatus.CONFLICT,
                    "An active camera file can be previewed; its final download is available after completion",
                )
            with TemporaryDirectory(prefix="growcam-") as temporary_directory:
                raw = Path(temporary_directory) / "camera.xm"
                video = Path(temporary_directory) / "camera.hevc"
                audio = Path(temporary_directory) / "camera.alaw"
                playable = Path(temporary_directory) / "recording.mkv"
                with self._camera() as camera:
                    if match.group("event") == "R":
                        _ = camera.download(camera_file, raw)
                    else:
                        with raw.open("xb") as output:
                            _ = camera.stream_download_by_time(
                                start=_camera_datetime(record["beginTime"]),
                                end=_camera_datetime(record["endTime"]),
                                output=output,
                                file_type=5,
                            )
                stats = demux_xm_recording(raw, video, audio)
                frame_rate = 25.0 if match.group("event") == "E" else stats.frames_per_second or 15.0
                remux_recording(
                    video,
                    playable,
                    frames_per_second=frame_rate,
                    audio_source=audio if audio.is_file() else None,
                )
                self._send_file(
                    playable,
                    content_type="video/x-matroska",
                    disposition=f'attachment; filename="{_download_name(camera_file)}"',
                )
        finally:
            server.camera_operation_lock.release()

    def _timelapse_preview(self, camera_file: str, *, download: bool, cache_only: bool) -> None:
        _ = _camera_file(camera_file, required_event="E")
        server = cast("GrowCamHTTPServer", self.server)
        record = server.cached_recording(camera_file)
        if record is None:
            record = _matching_recording(self._timelapse_state(), camera_file)
        resolved_file = cast("str", record["fileName"])
        key = f"timelapse:{_TIMELAPSE_PREVIEW_VERSION}:{_recording_identity(resolved_file)}:{record['sizeBytes']}"
        mode = "attachment" if download else "inline"
        disposition = f'{mode}; filename="{_preview_name(resolved_file)}"'
        if cache_only:
            self._preview_cache_probe(key, disposition=disposition)
            return
        response_started = False

        def build(preview: Path) -> None:
            nonlocal response_started
            response_started = True

            def source(output: BinaryIO) -> int:
                with self._camera() as camera:
                    return camera.stream_download_by_time(
                        start=_camera_datetime(record["beginTime"]),
                        end=_camera_datetime(record["endTime"]),
                        output=output,
                        file_type=5,
                    )

            self._build_streaming_preview(
                preview,
                source,
                frames_per_second=_TIMELAPSE_STREAM_FRAMES_PER_SECOND,
                cached_frames_per_second=_TIMELAPSE_CACHED_FRAMES_PER_SECOND,
                disposition=disposition,
            )

        try:
            preview, cache_hit = server.media_cache.get_or_build(key, ".mp4", build)
        except (OSError, RuntimeError, ValueError):
            if response_started:
                self.close_connection = True
                return
            raise
        if response_started:
            return
        self._send_file(
            preview,
            content_type=_MP4_CONTENT_TYPE,
            disposition=disposition,
            cache_status="HIT" if cache_hit else "MISS",
        )

    def _history_preview(
        self,
        camera_file: str,
        *,
        at: str,
        duration: str,
        video_codec: str,
        cache_only: bool,
    ) -> None:
        match = _camera_file(camera_file, required_event="R")
        selected_date = date.fromisoformat(match.group("date"))
        day_start = datetime.combine(selected_date, time.min)
        day_end = min(day_start + timedelta(days=1), datetime.now())
        if day_end <= day_start:
            raise WebRequestError(HTTPStatus.NOT_FOUND, "No recording exists for that future date")
        server = cast("GrowCamHTTPServer", self.server)
        record = self._history_recording(camera_file, selected_date, day_start, day_end)
        resolved_file = cast("str", record["fileName"])
        preview_range = _history_preview_range(record, at=at, duration=duration)
        range_key = (
            "full" if preview_range is None else f"{preview_range[0].isoformat()}:{preview_range[1].isoformat()}"
        )
        key = (
            f"history:{_HISTORY_PREVIEW_VERSION}:{video_codec}:{_recording_identity(resolved_file)}:"
            f"{record['sizeBytes']}:{range_key}"
        )
        disposition = f'inline; filename="{_history_preview_name(resolved_file)}"'
        if cache_only:
            self._preview_cache_probe(key, disposition=disposition)
            return
        response_started = False
        source = self._history_preview_source(record, resolved_file, preview_range)

        def build(preview: Path) -> None:
            nonlocal response_started
            response_started = True
            self._build_streaming_preview(
                preview,
                source,
                frames_per_second=15.0,
                disposition=disposition,
                recover_audio=True,
                video_codec=video_codec,
            )

        try:
            preview, cache_hit = server.media_cache.get_or_build(key, ".mp4", build)
        except (OSError, RuntimeError, ValueError):
            if response_started:
                self.close_connection = True
                return
            raise
        if response_started:
            return
        self._send_file(
            preview,
            content_type=_MP4_CONTENT_TYPE,
            disposition=disposition,
            cache_status="HIT" if cache_hit else "MISS",
        )

    def _history_preview_source(
        self,
        record: dict[str, object],
        resolved_file: str,
        preview_range: tuple[datetime, datetime] | None,
    ) -> Callable[[BinaryIO], int]:
        """Build a lazy camera source for one full or time-bounded rewind preview."""
        if preview_range is not None:

            def ranged_source(output: BinaryIO) -> int:
                with self._camera() as camera:
                    return camera.stream_download_by_time(
                        start=preview_range[0],
                        end=preview_range[1],
                        output=output,
                        file_type=0,
                    )

            return ranged_source

        def full_source(output: BinaryIO) -> int:
            with self._camera() as camera:
                if cast("bool", record["active"]):
                    return camera.stream_download_by_time(
                        start=_camera_datetime(record["beginTime"]),
                        end=_camera_datetime(record["endTime"]),
                        output=output,
                        file_type=0,
                    )
                return camera.stream_download(resolved_file, output)

        return full_source

    def _history_recording(
        self,
        camera_file: str,
        selected_date: date,
        day_start: datetime,
        day_end: datetime,
    ) -> dict[str, object]:
        server = cast("GrowCamHTTPServer", self.server)
        cached = server.cached_recording(camera_file)
        if cached is not None:
            return cached
        with self._camera() as camera:
            recordings = camera.recordings(
                start=day_start,
                end=day_end,
                channel=0,
                event="R",
            )
        browser_recordings = _browser_recordings(
            recordings,
            active_filename=_active_history_filename(recordings, selected_date, datetime.now()),
        )
        browser_recordings = _playable_history_recordings(browser_recordings)
        server.remember_recordings(browser_recordings)
        return _matching_recording({"recordings": browser_recordings}, camera_file)

    def _build_streaming_preview(  # noqa: PLR0913 - explicit media options make each preview mode auditable.
        self,
        destination: Path,
        source: Callable[[BinaryIO], int],
        *,
        frames_per_second: float,
        cached_frames_per_second: float | None = None,
        disposition: str,
        recover_audio: bool = False,
        video_codec: str = "h264",
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _MP4_CONTENT_TYPE)
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-GrowCam-Preview-Cache", "MISS")
        self.send_header("X-GrowCam-Preview-Streaming", "1")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def consume(chunk: bytes) -> None:
            _write_preview_chunk(self.wfile, chunk)

        if recover_audio:
            _ = build_xm_fragmented_preview(
                source,
                destination,
                consume,
                frames_per_second=frames_per_second,
                video_codec=video_codec,
            )
        else:
            _ = build_fragmented_preview(
                source,
                destination,
                consume,
                frames_per_second=frames_per_second,
            )
        cached_rate = frames_per_second if cached_frames_per_second is None else cached_frames_per_second
        finalize_fragmented_preview(
            destination,
            timestamp_scale=frames_per_second / cached_rate,
            align_video_to_audio=recover_audio,
        )

    def _preview_cache_probe(self, key: str, *, disposition: str) -> None:
        server = cast("GrowCamHTTPServer", self.server)
        preview, building = server.media_cache.lookup(key, ".mp4")
        if preview is not None:
            self._send_file(
                preview,
                content_type=_MP4_CONTENT_TYPE,
                disposition=disposition,
                cache_status="HIT",
            )
            return
        status = HTTPStatus.ACCEPTED if building else HTTPStatus.OK
        self._json({"ready": False, "building": building}, status=status)

    def _mjpeg(self, quality: LiveQuality) -> None:
        config = cast("GrowCamHTTPServer", self.server).config
        profile = _LIVE_STREAM_PROFILES[quality]
        process = start_mjpeg(
            config.camera_host,
            config.username,
            config.password,
            width=profile.output_width,
            stream_index=profile.stream_index,
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=growcam")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-GrowCam-Live-Quality", quality.value)
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

    def _live_audio(self) -> None:
        config = cast("GrowCamHTTPServer", self.server).config
        process = start_live_audio(config.camera_host, config.username, config.password)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if process.stdout is None:
                raise MediaError("FFmpeg did not expose a live audio stream")
            standard_output = cast("BufferedIOBase", process.stdout)
            while chunk := standard_output.read1(16 * 1024):
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
        if self.headers.get_content_type() != "application/json":
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
        file_size = path.stat().st_size
        requested_range = _byte_range(self.headers.get("Range"), file_size)
        if requested_range is None:
            start = 0
            end = file_size - 1
            status = HTTPStatus.OK
        else:
            start, end = requested_range
            status = HTTPStatus.PARTIAL_CONTENT
        content_length = end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        if status is HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        if cache_status is not None:
            self.send_header("X-GrowCam-Preview-Cache", cache_status)
        self.end_headers()
        try:
            with path.open("rb") as source:
                _ = source.seek(start)
                remaining = content_length
                while remaining > 0 and (chunk := source.read(min(1024 * 1024, remaining))):
                    _ = self.wfile.write(chunk)
                    remaining -= len(chunk)
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
        server = GrowCamHTTPServer((listen, port), config, settings_file=_settings_path())
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
    recordings, active_filename = _timelapse_index(camera, config, now)
    return {
        "config": config.to_api(now=now),
        "recordings": list(reversed(_browser_recordings(recordings, active_filename=active_filename))),
    }


def _timelapse_index(
    camera: DVRIPClient,
    config: TimelapseConfig,
    now: datetime,
) -> tuple[list[dict[str, Any]], str]:
    """Return the current schedule index and its active camera filename."""
    search_start = min(config.start_time, now) - timedelta(days=1)
    recordings = camera.recordings(start=search_start, end=now, channel=0, event="E")
    return recordings, _active_timelapse_filename(config, recordings, now)


def _active_timelapse_filename(
    config: TimelapseConfig,
    recordings: list[dict[str, Any]],
    now: datetime,
) -> str:
    if config.enabled and config.start_time <= now < config.end_time and recordings:
        return str(recordings[-1].get("FileName", ""))
    return ""


def _timelapse_update_request(payload: dict[str, object]) -> tuple[str, TimelapseConfig]:
    expected_revision = payload.get("expectedRevision")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise TimelapseValidationError("expectedRevision must be a non-empty string")
    return expected_revision, TimelapseConfig.from_browser(payload.get("config"))


def _app_settings_update_request(payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    if set(payload) != {"expectedRevision", "settings"}:
        raise SettingsValidationError("Settings update must contain only expectedRevision and settings")
    expected_revision = payload["expectedRevision"]
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
        raise SettingsValidationError("expectedRevision must be a non-negative integer")
    raw_settings = payload["settings"]
    if not isinstance(raw_settings, dict):
        raise SettingsValidationError("settings must be a JSON object")
    values: dict[str, object] = {}
    for key, value in cast("dict[object, object]", raw_settings).items():
        if not isinstance(key, str):
            raise SettingsValidationError("settings contains a non-string key")
        values[key] = value
    return expected_revision, values


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
    filename_indexes: dict[str, int] = {}
    for item in recordings:
        filename = str(item.get("FileName", ""))
        record: dict[str, object] = {
            "beginTime": str(item.get("BeginTime", "")),
            "endTime": str(item.get("EndTime", "")),
            "fileName": filename,
            "sizeBytes": _file_length_bytes(item.get("FileLength")),
            "active": filename == active_filename,
        }
        duplicate_index = filename_indexes.get(filename) if filename else None
        if duplicate_index is None:
            if filename:
                filename_indexes[filename] = len(browser_records)
            browser_records.append(record)
        else:
            browser_records[duplicate_index] = record
    return browser_records


def _playable_history_recordings(recordings: list[dict[str, object]]) -> list[dict[str, object]]:
    """Exclude zero-length camera artifacts that cannot represent a rewind interval."""
    return [
        record for record in recordings if _camera_datetime(record["endTime"]) > _camera_datetime(record["beginTime"])
    ]


def _browser_files(
    recordings: list[dict[str, Any]],
    timelapses: list[dict[str, Any]],
    *,
    active_recording_filename: str,
    active_timelapse_filename: str,
) -> list[dict[str, object]]:
    files = [
        _file_view_record(record, kind="recording")
        for record in _browser_recordings(recordings, active_filename=active_recording_filename)
    ]
    files.extend(
        _file_view_record(record, kind="timelapse")
        for record in _browser_recordings(timelapses, active_filename=active_timelapse_filename)
    )
    files.sort(key=lambda record: cast("str", record["beginTime"]), reverse=True)
    return files


def _file_view_record(record: dict[str, object], *, kind: str) -> dict[str, object]:
    filename = cast("str", record["fileName"])
    active = cast("bool", record["active"])
    return {
        **record,
        "kind": kind,
        "downloadName": _download_name(filename),
        "downloadable": not active,
    }


def _active_history_filename(
    recordings: list[dict[str, Any]],
    selected_date: date,
    now: datetime,
) -> str:
    if selected_date != now.date() or not recordings:
        return ""
    return str(recordings[-1].get("FileName", ""))


def _recording_overlaps(record: dict[str, Any], start: datetime, end: datetime) -> bool:
    """Return whether one camera index row intersects a half-open time range."""
    return _camera_datetime(record.get("BeginTime")) < end and _camera_datetime(record.get("EndTime")) > start


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
    if duration_seconds not in {60, 120, 300, 600}:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind duration must be 60, 120, 300, or 600 seconds")
    recording_start = _camera_datetime(record["beginTime"])
    recording_end = _camera_datetime(record["endTime"])
    if not recording_start <= requested_start < recording_end:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind time is outside the selected recording")
    requested_end = min(requested_start + timedelta(seconds=duration_seconds), recording_end)
    if requested_end <= requested_start:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Quick rewind window is empty")
    return requested_start, requested_end


def _preview_video_codec(value: str) -> str:
    if value not in {"h264", "hevc"}:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "videoCodec must be h264 or hevc")
    return value


def _live_quality(value: str) -> LiveQuality:
    """Validate one browser live-view quality selector value."""
    try:
        return LiveQuality(value.casefold())
    except ValueError as error:
        raise WebRequestError(HTTPStatus.BAD_REQUEST, "Live quality must be 'sd' or 'fhd'") from error


def _byte_range(header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse one satisfiable HTTP byte range for a non-empty local file."""
    if header is None:
        return None
    if file_size <= 0:
        raise WebRequestError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Media file is empty")
    unit, separator, value = header.partition("=")
    if separator != "=" or unit.strip().casefold() != "bytes" or "," in value:
        raise _invalid_byte_range()
    start_value, end_value = _parse_byte_range_values(value)
    start, end = _resolve_byte_range(start_value, end_value, file_size)
    if start < 0 or start >= file_size or end < start:
        raise WebRequestError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Media byte range is unsatisfiable")
    return start, min(end, file_size - 1)


def _parse_byte_range_values(value: str) -> tuple[int | None, int | None]:
    start_text, dash, end_text = value.strip().partition("-")
    if dash != "-" or (not start_text and not end_text):
        raise _invalid_byte_range()
    try:
        start_value = int(start_text) if start_text else None
        end_value = int(end_text) if end_text else None
    except ValueError as error:
        raise _invalid_byte_range() from error
    return start_value, end_value


def _resolve_byte_range(start_value: int | None, end_value: int | None, file_size: int) -> tuple[int, int]:
    if start_value is not None:
        return start_value, file_size - 1 if end_value is None else end_value
    if end_value is None or end_value <= 0:
        raise _invalid_byte_range()
    return max(0, file_size - end_value), file_size - 1


def _invalid_byte_range() -> WebRequestError:
    return WebRequestError(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, _INVALID_BYTE_RANGE)


def _write_preview_chunk(output: BufferedIOBase, chunk: bytes) -> None:
    """Write one browser fragment or cancel the camera build after disconnect."""
    try:
        _ = output.write(chunk)
        output.flush()
    except OSError as error:
        raise PreviewClientDisconnectedError("Browser abandoned the media preview") from error


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
