"""Unit tests for safe browser download naming."""

# pyright: reportPrivateUsage=false

import errno
import http.client
import threading
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

import pytest

from growcam import web
from growcam.web import (
    GrowCamHTTPServer,
    WebConfig,
    WebRequestError,
    _download_name,
    _file_length_bytes,
    _history_date,
    _history_preview_range,
    _matching_recording,
    _preview_cache_directory,
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


def test_duplicate_server_bind_preserves_the_original_socket_error() -> None:
    first = GrowCamHTTPServer(("127.0.0.1", 0), WebConfig("192.0.2.1"))
    port = first.server_address[1]
    try:
        with pytest.raises(OSError) as raised:
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
