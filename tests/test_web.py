"""Unit tests for safe browser download naming."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import errno
import http.client
import json
import threading
from datetime import datetime
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

import pytest
from typing_extensions import override

from growcam import web
from growcam.settings import settings_path
from growcam.web import (
    GrowCamHTTPServer,
    WebConfig,
    WebRequestError,
    _browser_files,
    _byte_range,
    _download_name,
    _file_length_bytes,
    _history_date,
    _history_preview_range,
    _matching_recording,
    _playable_history_recordings,
    _preview_cache_directory,
    _write_preview_chunk,
    serve,
)

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Callable
    from datetime import tzinfo
    from io import BufferedIOBase
    from types import TracebackType
    from typing import BinaryIO, Self


def test_download_name_uses_recording_timestamp_only() -> None:
    camera_file = "/idea0/2026-07-31/001/23.40.00-23.50.00[R][@17f2][0].h264"

    assert _download_name(camera_file) == "growcam-2026-07-31_23-40-00_23-50-00.mkv"


def test_download_name_does_not_reflect_unstructured_input() -> None:
    assert _download_name("/\r\nX-Unsafe: value") == "growcam-recording.mkv"


def test_timelapse_download_name_identifies_the_recording_type() -> None:
    camera_file = "/idea0/2026-07-31/001/01.45.29-12.29.09[E][@8c][12]._h264"

    assert _download_name(camera_file) == "growcam-timelapse-2026-07-31_01-45-29_12-29-09.mkv"


def test_file_view_merges_indexes_and_guards_active_downloads() -> None:
    recording = {
        "BeginTime": "2026-08-02 12:00:00",
        "EndTime": "2026-08-02 12:10:00",
        "FileName": "/idea0/2026-08-02/001/12.00.00-12.10.00[R][0].h264",
        "FileLength": "0x400",
    }
    timelapse = {
        "BeginTime": "2026-08-02 14:00:00",
        "EndTime": "2026-08-02 14:30:00",
        "FileName": "/idea0/2026-08-02/001/14.00.00-14.30.00[E][0]._h264",
        "FileLength": "0x800",
    }

    files = _browser_files(
        [recording, {**recording, "FileLength": "0x800"}],
        [timelapse],
        active_recording_filename="",
        active_timelapse_filename=timelapse["FileName"],
    )

    assert [item["kind"] for item in files] == ["timelapse", "recording"]
    assert files[0]["active"] is True
    assert files[0]["downloadable"] is False
    assert files[0]["downloadName"] == "growcam-timelapse-2026-08-02_14-00-00_14-30-00.mkv"
    assert files[1]["downloadable"] is True
    assert files[1]["sizeBytes"] == 2 * 1024**2


def test_rewind_excludes_zero_length_camera_artifacts() -> None:
    valid: dict[str, object] = {
        "beginTime": "2026-08-02 02:50:00",
        "endTime": "2026-08-02 02:52:00",
        "fileName": "/idea0/valid[R].h264",
        "sizeBytes": 1024,
        "active": False,
    }
    zero_length = {
        **valid,
        "beginTime": "2026-08-02 02:52:00",
        "endTime": "2026-08-02 02:52:00",
        "fileName": "/idea0/zero[R].h264",
    }

    assert _playable_history_recordings([valid, zero_length]) == [valid]


def test_camera_hex_file_length_is_kibibytes() -> None:
    assert _file_length_bytes("0x400") == 1024**2


def test_active_recording_match_survives_camera_filename_end_time_update() -> None:
    requested = "/idea0/2026-07-31/001/01.45.29-14.14.09[E][@93][12]._h264"
    current = "/idea0/2026-07-31/001/01.45.29-14.29.09[E][@94][12]._h264"
    record: dict[str, object] = {
        "beginTime": "2026-07-31 01:45:29",
        "endTime": "2026-08-01 14:29:09",
        "fileName": current,
        "sizeBytes": 1024,
        "active": True,
    }

    assert _matching_recording({"recordings": [record]}, requested) is record


def test_history_date_rejects_non_iso_input() -> None:
    with pytest.raises(WebRequestError) as raised:
        _ = _history_date("08/01/2026")

    assert raised.value.status is HTTPStatus.BAD_REQUEST


def test_quick_history_range_is_clamped_to_recording_end() -> None:
    record: dict[str, object] = {
        "beginTime": "2026-08-01 12:00:00",
        "endTime": "2026-08-01 12:10:00",
    }

    assert _history_preview_range(record, at="2026-08-01T12:09:00", duration="120") == (
        datetime(2026, 8, 1, 12, 9),
        datetime(2026, 8, 1, 12, 10),
    )


def test_quick_history_range_rejects_time_outside_recording() -> None:
    record: dict[str, object] = {
        "beginTime": "2026-08-01 12:00:00",
        "endTime": "2026-08-01 12:10:00",
    }

    with pytest.raises(WebRequestError, match="outside"):
        _ = _history_preview_range(record, at="2026-08-01T12:11:00", duration="120")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=2-5", (2, 5)),
        ("bytes=7-", (7, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=7-99", (7, 9)),
    ],
)
def test_media_byte_range_supports_browser_seek_requests(
    header: str | None,
    expected: tuple[int, int] | None,
) -> None:
    assert _byte_range(header, 10) == expected


@pytest.mark.parametrize("header", ["items=0-1", "bytes=", "bytes=10-", "bytes=4-2", "bytes=0-1,4-5"])
def test_media_byte_range_rejects_invalid_or_unsatisfiable_requests(header: str) -> None:
    with pytest.raises(WebRequestError) as raised:
        _ = _byte_range(header, 10)

    assert raised.value.status is HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE


def test_streaming_preview_disconnect_is_a_cancellation() -> None:
    class DisconnectedOutput:
        def write(self, _chunk: bytes) -> int:
            raise BrokenPipeError

        def flush(self) -> None:
            pytest.fail("flush should not follow a failed write")

    with pytest.raises(web.PreviewClientDisconnectedError):
        _write_preview_chunk(cast("BufferedIOBase", DisconnectedOutput()), b"fragment")


def test_duplicate_server_bind_preserves_the_original_socket_error() -> None:
    first = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    port = first.server_address[1]
    try:
        with pytest.raises(OSError, match=r"(?:Address already in use|socket address)") as raised:
            _ = GrowCamHTTPServer(("127.0.0.1", port), WebConfig("192.0.2.1"))
        assert raised.value.errno == errno.EADDRINUSE or getattr(raised.value, "winerror", None) == 10048
    finally:
        first.server_close()


def test_preview_cache_directory_is_platform_native(tmp_path: Path) -> None:
    home = tmp_path

    assert _preview_cache_directory(
        platform_name="win32",
        environment={"LOCALAPPDATA": "C:/LocalData"},
        home=home,
    ) == Path("C:/LocalData/GrowCam/preview-cache")
    assert _preview_cache_directory(platform_name="darwin", environment={}, home=home) == (
        home / "Library" / "Caches" / "GrowCam" / "preview-cache"
    )
    assert _preview_cache_directory(platform_name="linux", environment={}, home=home) == (
        home / ".cache" / "growcam" / "preview-cache"
    )


def test_preview_cache_honors_xdg_cache_home(tmp_path: Path) -> None:
    home = tmp_path
    cache_root = home / "xdg-cache"

    assert (
        _preview_cache_directory(
            platform_name="linux",
            environment={"XDG_CACHE_HOME": str(cache_root)},
            home=home,
        )
        == cache_root / "growcam" / "preview-cache"
    )


def test_recent_recording_metadata_avoids_a_second_camera_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([10.0, 10.0, 71.0])
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path)
    monkeypatch.setattr("growcam.web.time_module.monotonic", lambda: next(clock))
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    filename = "/idea0/2026-08-01/001/12.00.00-12.10.00[R][0].h264"
    record: dict[str, object] = {
        "fileName": filename,
        "beginTime": "2026-08-01 12:00:00",
        "endTime": "2026-08-01 12:10:00",
        "sizeBytes": 1024,
        "active": False,
    }

    try:
        server.remember_recordings([record])
        assert server.cached_recording(filename) == record
        assert server.cached_recording(filename) is None
    finally:
        server.server_close()


def test_address_in_use_is_reported_consistently(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_bind(
        _address: tuple[str, int],
        _config: WebConfig,
        *,
        settings_file: Path | None = None,
    ) -> GrowCamHTTPServer:
        assert settings_file == settings_path()
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(web, "GrowCamHTTPServer", fail_to_bind)

    with pytest.raises(OSError, match="Local port 8876 is already in use"):
        serve(WebConfig("192.0.2.1"), "127.0.0.1", 8876)


def test_static_responses_have_browser_security_headers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        _ = response.read()

        assert response.status == HTTPStatus.OK
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("Referrer-Policy") == "no-referrer"
        assert "default-src 'self'" in (response.getheader("Content-Security-Policy") or "")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_settings_route_persists_revision_and_reconfigures_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path / "cache")
    settings_file = tmp_path / "config" / "settings.json"
    server = GrowCamHTTPServer(
        ("127.0.0.1", 0),
        WebConfig("192.0.2.1"),
        settings_file=settings_file,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    desired = {
        "cacheMaxBytes": 256 * 1024**2,
        "cacheMaxEntries": 7,
        "rewindPreviewSeconds": 300,
        "continuePlayback": False,
        "previewVideoCodec": "auto",
    }
    headers = {"Content-Type": "application/json", "X-GrowCam-Request": "1"}
    try:
        connection.request("GET", "/api/settings")
        initial_response = connection.getresponse()
        initial = json.loads(initial_response.read())

        assert initial_response.status == HTTPStatus.OK
        assert initial["persistent"] is True
        assert initial["settings"]["revision"] == 0

        body = json.dumps({"expectedRevision": 0, "settings": desired})
        connection.request("POST", "/api/settings", body=body, headers=headers)
        update_response = connection.getresponse()
        updated = json.loads(update_response.read())

        assert update_response.status == HTTPStatus.OK
        assert updated["settings"] == {"revision": 1, **desired}
        assert updated["cache"]["maximumBytes"] == desired["cacheMaxBytes"]
        assert updated["cache"]["maximumEntries"] == desired["cacheMaxEntries"]
        assert json.loads(settings_file.read_text(encoding="utf-8"))["revision"] == 1

        connection.request("POST", "/api/settings", body=body, headers=headers)
        conflict_response = connection.getresponse()
        conflict = json.loads(conflict_response.read())

        assert conflict_response.status == HTTPStatus.CONFLICT
        assert "current revision 1" in conflict["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cache_clear_route_removes_only_generated_previews(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_directory = tmp_path / "cache"
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: cache_directory)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))

    def build_preview(destination: Path) -> None:
        _ = destination.write_bytes(b"generated preview")

    _ = server.media_cache.get_or_build("fixture", ".mp4", build_preview)
    unrelated = cache_directory / "keep.txt"
    _ = unrelated.write_text("not generated media", encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(
            "POST",
            "/api/cache/clear",
            body="{}",
            headers={"Content-Type": "application/json", "X-GrowCam-Request": "1"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == HTTPStatus.OK
        assert payload["cache"]["entryCount"] == 0
        assert unrelated.read_text(encoding="utf-8") == "not generated media"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_cache_probe_reports_missing_then_serves_one_ready_byte(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path / "cache")
    camera_file = "/idea0/2026-08-02/001/03.00.00-03.10.00[R][@2fc5][0].h264"
    record: dict[str, object] = {
        "beginTime": "2026-08-02 03:00:00",
        "endTime": "2026-08-02 03:10:00",
        "fileName": camera_file,
        "sizeBytes": 20_299_776,
        "active": False,
    }
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"), settings_file=None)
    server.remember_recordings([record])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    parameters = {
        "file": camera_file,
        "videoCodec": "h264",
        "at": "2026-08-02T03:00:00",
        "duration": "120",
        "cacheOnly": "1",
    }
    request_path = f"/api/history/preview?{urlencode(parameters)}"
    range_key = "2026-08-02T03:00:00:2026-08-02T03:02:00"
    cache_key = (
        f"history:{web._HISTORY_PREVIEW_VERSION}:h264:{web._recording_identity(camera_file)}:"
        f"{record['sizeBytes']}:{range_key}"
    )

    def build_cached_preview(destination: Path) -> None:
        _ = destination.write_bytes(b"preview")

    try:
        connection.request("GET", request_path, headers={"Range": "bytes=0-0"})
        missing_response = connection.getresponse()
        missing = json.loads(missing_response.read())

        assert missing_response.status == HTTPStatus.OK
        assert missing == {"ready": False, "building": False}

        preview, cache_hit = server.media_cache.get_or_build(
            cache_key,
            ".mp4",
            build_cached_preview,
        )
        assert cache_hit is False
        assert preview.read_bytes() == b"preview"

        connection.request("GET", request_path, headers={"Range": "bytes=0-0"})
        ready_response = connection.getresponse()

        assert ready_response.status == HTTPStatus.PARTIAL_CONTENT
        assert ready_response.getheader("Content-Range") == "bytes 0-0/7"
        assert ready_response.read() == b"p"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_timelapse_preview_downloads_the_complete_range_without_playback_throttling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    camera_file = "/idea0/2026-07-31/001/01.45.29-19.54.08[E][@10a][12]._h264"
    record: dict[str, object] = {
        "beginTime": "2026-07-31 01:45:29",
        "endTime": "2026-08-02 19:54:08",
        "fileName": camera_file,
        "sizeBytes": 70_558_720,
        "active": True,
    }
    observed: dict[str, object] = {}

    class TimelapseCamera:
        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exception_type: type[BaseException] | None,
            exception: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def stream_download_by_time(
            self,
            *,
            start: datetime,
            end: datetime,
            output: BinaryIO,
            file_type: int = 0,
        ) -> int:
            observed.update(start=start, end=end, file_type=file_type)
            return output.write(b"complete XM range")

    def open_camera(_handler: web.GrowCamHandler) -> TimelapseCamera:
        return TimelapseCamera()

    def build_preview(
        handler: web.GrowCamHandler,
        destination: Path,
        source: Callable[[BinaryIO], int],
        **options: object,
    ) -> None:
        source_output = BytesIO()
        assert source(source_output) == len(b"complete XM range")
        assert source_output.getvalue() == b"complete XM range"
        observed.update({"recover_audio": False, "video_codec": "h264", **options})
        _ = destination.write_bytes(b"browser preview")
        handler.send_response(HTTPStatus.NO_CONTENT)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path / "cache")
    monkeypatch.setattr(web.GrowCamHandler, "_camera", open_camera)
    monkeypatch.setattr(web.GrowCamHandler, "_build_streaming_preview", build_preview)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"), settings_file=None)
    server.remember_recordings([record])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", f"/api/timelapse/preview?{urlencode({'file': camera_file})}")
        response = connection.getresponse()

        assert response.status == HTTPStatus.NO_CONTENT
        assert response.read() == b""
        assert observed == {
            "start": datetime(2026, 7, 31, 1, 45, 29),
            "end": datetime(2026, 8, 2, 19, 54, 8),
            "file_type": 5,
            "frames_per_second": 2.0,
            "cached_frames_per_second": 25.0,
            "disposition": 'inline; filename="growcam-timelapse-2026-07-31_01-45-29_19-54-08-preview.mp4"',
            "recover_audio": False,
            "video_codec": "h264",
        }
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_files_route_returns_the_selected_camera_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload: dict[str, object] = {
        "date": "2026-08-02",
        "files": [],
        "summary": {"count": 0, "recordings": 0, "timelapses": 0, "sizeBytes": 0},
    }

    def selected_files(_handler: web.GrowCamHandler, requested_date: str) -> dict[str, object]:
        assert requested_date == "2026-08-02"
        return payload

    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path)
    monkeypatch.setattr(web.GrowCamHandler, "_files", selected_files)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/api/files?date=2026-08-02")
        response = connection.getresponse()

        assert response.status == HTTPStatus.OK
        assert json.loads(response.read()) == payload
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_live_audio_flushes_available_mp3_bytes_without_waiting_for_a_full_buffer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class AvailableOutput(BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError(f"buffered read({size}) would delay low-bitrate live audio")

    class AudioProcess:
        def __init__(self) -> None:
            self.stdout = AvailableOutput(b"first-mp3-frame")
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 5
            return 0

        def kill(self) -> None:
            raise AssertionError("the completed audio process should not be killed")

    process = AudioProcess()

    def start_audio(_host: str, _username: str, _password: str) -> subprocess.Popen[bytes]:
        return cast("subprocess.Popen[bytes]", process)

    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path)
    monkeypatch.setattr(web, "start_live_audio", start_audio)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/stream.mp3")
        response = connection.getresponse()

        assert response.status == HTTPStatus.OK
        assert response.getheader("Content-Type") == "audio/mpeg"
        assert response.read() == b"first-mp3-frame"
        assert process.terminated is True
    finally:
        connection.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        thread.join(timeout=5)


def test_files_route_queries_both_camera_partitions(  # noqa: C901 - integration fixture covers both indexes.
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FixedDateTime(datetime):
        @classmethod
        @override
        def now(cls, tz: tzinfo | None = None) -> FixedDateTime:
            assert tz is None
            return cls(2026, 8, 2, 15, 0)

    class FileIndexCamera:
        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            return None

        def recordings(
            self,
            *,
            start: datetime,
            end: datetime,
            channel: int,
            event: str,
        ) -> list[dict[str, object]]:
            assert channel == 0
            if event == "R":
                if start == datetime(2026, 8, 1):
                    assert end == datetime(2026, 8, 2)
                    return []
                assert start == datetime(2026, 8, 2)
                assert end == datetime(2026, 8, 2, 15)
                return [
                    {
                        "BeginTime": "2026-08-02 12:00:00",
                        "EndTime": "2026-08-02 12:10:00",
                        "FileName": "/idea0/2026-08-02/001/12.00.00-12.10.00[R][0].h264",
                        "FileLength": "0x400",
                    },
                    {
                        "BeginTime": "2026-08-02 14:30:00",
                        "EndTime": "2026-08-02 15:00:00",
                        "FileName": "/idea0/2026-08-02/001/14.30.00-15.00.00[R][0].h264",
                        "FileLength": "0x800",
                    },
                ]
            assert event == "E"
            if start == datetime(2026, 8, 2):
                assert end == datetime(2026, 8, 2, 15)
                return [
                    {
                        "BeginTime": "2026-08-02 13:00:00",
                        "EndTime": "2026-08-02 14:00:00",
                        "FileName": "/idea0/2026-08-02/001/13.00.00-14.00.00[E][0]._h264",
                        "FileLength": "0x200",
                    }
                ]
            if start == datetime(2026, 8, 1):
                assert end == datetime(2026, 8, 2)
            else:
                assert start == datetime(2026, 7, 31, 8)
                assert end == datetime(2026, 8, 2, 15)
            return [
                {
                    "BeginTime": "2026-08-01 08:00:00",
                    "EndTime": "2026-08-02 15:00:00",
                    "FileName": "/idea0/2026-08-01/001/08.00.00-15.00.00[E][0]._h264",
                    "FileLength": "0x300",
                }
            ]

        def config_get(self, name: str) -> object:
            assert name == "Storage.EpitomeRecord"
            return [
                {
                    "Enable": True,
                    "EndTime": "2026-08-03 18:00:00",
                    "Interval": 300,
                    "StartTime": "2026-08-01 08:00:00",
                    "TimeSection": [
                        "1 08:00:00-18:00:00",
                        "0 00:00:00-23:59:59",
                        "0 00:00:00-23:59:59",
                        "0 00:00:00-23:59:59",
                        "0 00:00:00-23:59:59",
                        "0 00:00:00-23:59:59",
                    ],
                }
            ]

    camera = FileIndexCamera()

    def open_camera(_handler: web.GrowCamHandler) -> FileIndexCamera:
        return camera

    monkeypatch.setattr(web, "datetime", FixedDateTime)
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path)
    monkeypatch.setattr(web.GrowCamHandler, "_camera", open_camera)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/api/files?date=2026-08-02")
        response = connection.getresponse()
        payload = json.loads(response.read())

        assert response.status == HTTPStatus.OK
        assert payload["summary"] == {"count": 4, "recordings": 2, "timelapses": 2, "sizeBytes": 4456448}
        assert [item["kind"] for item in payload["files"]] == [
            "recording",
            "timelapse",
            "recording",
            "timelapse",
        ]
        assert [item["downloadable"] for item in payload["files"]] == [False, True, True, False]

        active_file = payload["files"][0]["fileName"]
        connection.request("GET", f"/api/download?{urlencode({'file': active_file})}")
        active_response = connection.getresponse()
        active_payload = json.loads(active_response.read())

        assert active_response.status == HTTPStatus.CONFLICT
        assert active_payload["error"] == (
            "An active camera file can be previewed; its final download is available after completion"
        )

        connection.request("GET", "/api/files?date=2026-08-01")
        historical_response = connection.getresponse()
        historical_payload = json.loads(historical_response.read())

        assert historical_response.status == HTTPStatus.OK
        assert historical_payload["summary"] == {
            "count": 1,
            "recordings": 0,
            "timelapses": 1,
            "sizeBytes": 768 * 1024,
        }
        assert historical_payload["files"][0]["active"] is True
        assert historical_payload["files"][0]["downloadable"] is False
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cached_media_supports_http_range_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media = tmp_path / "preview.mp4"
    _ = media.write_bytes(b"0123456789")
    monkeypatch.setattr(web, "_preview_cache_directory", lambda: tmp_path / "cache")

    def send_fixture(handler: web.GrowCamHandler, _name: str, _content_type: str) -> None:
        handler._send_file(media, content_type="video/mp4", disposition='inline; filename="preview.mp4"')

    monkeypatch.setattr(web.GrowCamHandler, "_static", send_fixture)
    server = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request("GET", "/", headers={"Range": "bytes=2-5"})
        response = connection.getresponse()

        assert response.status == HTTPStatus.PARTIAL_CONTENT
        assert response.getheader("Accept-Ranges") == "bytes"
        assert response.getheader("Content-Range") == "bytes 2-5/10"
        assert response.getheader("Content-Length") == "4"
        assert response.read() == b"2345"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
