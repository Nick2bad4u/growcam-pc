"""Unit tests for safe browser download naming."""

# pyright: reportPrivateUsage=false

import errno
import http.client
import threading
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from io import BufferedIOBase

from growcam import web
from growcam.web import (
    GrowCamHTTPServer,
    WebConfig,
    WebRequestError,
    _byte_range,
    _download_name,
    _file_length_bytes,
    _history_date,
    _history_preview_range,
    _matching_recording,
    _preview_cache_directory,
    _write_preview_chunk,
    serve,
)


def test_download_name_uses_recording_timestamp_only() -> None:
    camera_file = "/idea0/2026-07-31/001/23.40.00-23.50.00[R][@17f2][0].h264"

    assert _download_name(camera_file) == "growcam-2026-07-31_23-40-00_23-50-00.mkv"


def test_download_name_does_not_reflect_unstructured_input() -> None:
    assert _download_name("/\r\nX-Unsafe: value") == "growcam-recording.mkv"


def test_timelapse_download_name_identifies_the_recording_type() -> None:
    camera_file = "/idea0/2026-07-31/001/01.45.29-12.29.09[E][@8c][12]._h264"

    assert _download_name(camera_file) == "growcam-timelapse-2026-07-31_01-45-29_12-29-09.mkv"


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
    def fail_to_bind(_address: tuple[str, int], _config: WebConfig) -> GrowCamHTTPServer:
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
