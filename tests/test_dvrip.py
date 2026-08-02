"""Unit tests for DVRIP parsing and protocol helpers."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import socket
import threading
from datetime import datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import pytest

from growcam.dvrip import DVRIPClient, DVRIPError, _decode_json, _recording_times, sofia_hash

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO


def test_empty_password_sofia_hash_matches_camera_protocol() -> None:
    assert sofia_hash("") == "tlJwpbo6"


def test_recording_times_are_parsed_from_camera_path() -> None:
    start, end = _recording_times("/idea0/2026-07-31/001/23.40.00-23.50.00[R][@17f2][0].h264")

    assert start == datetime(2026, 7, 31, 23, 40)
    assert end == datetime(2026, 7, 31, 23, 50)


def test_recording_times_handle_midnight_rollover() -> None:
    start, end = _recording_times("/idea0/2026-07-31/001/23.55.00-00.05.00[R][0].h264")

    assert start == datetime(2026, 7, 31, 23, 55)
    assert end == datetime(2026, 8, 1, 0, 5)


def test_recording_times_reject_unstructured_path() -> None:
    with pytest.raises(ValueError, match="infer recording timestamps"):
        _ = _recording_times("/idea0/unknown.h264")


def test_decode_json_strips_camera_terminators() -> None:
    assert _decode_json(b'{"Ret":100}\n\x00', 1001) == {"Ret": 100}


def test_decode_json_rejects_non_object() -> None:
    with pytest.raises(DVRIPError, match="Unexpected DVRIP response type"):
        _ = _decode_json(b"[]", 1001)


def test_download_requires_absolute_camera_path(tmp_path: Path) -> None:
    camera = DVRIPClient("192.0.2.1")

    with pytest.raises(ValueError, match="absolute DVRIP path"):
        _ = camera.download("relative.h264", tmp_path / "recording.h264")


def test_download_removes_partial_file_after_stream_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = DVRIPClient("192.0.2.1")
    destination = tmp_path / "recording.h264"

    def fail_stream(_filename: str, output: BinaryIO) -> int:
        _ = output.write(b"partial media")
        raise DVRIPError("camera stopped")

    monkeypatch.setattr(camera, "stream_download", fail_stream)

    with pytest.raises(DVRIPError, match="camera stopped"):
        _ = camera.download(
            "/idea0/2026-07-31/001/23.40.00-23.50.00[R][@17f2][0].h264",
            destination,
        )

    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()


def test_require_ok_includes_camera_return_code() -> None:
    with pytest.raises(DVRIPError, match=r"Ret=101"):
        DVRIPClient._require_ok("login", {"Ret": 101})


def test_recordings_passes_explicit_timelapse_event(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = DVRIPClient("192.0.2.1")
    requests: list[dict[str, Any]] = []

    def fake_request(message_id: int, body: dict[str, Any], **_kwargs: object) -> dict[str, Any]:
        assert message_id == 1440
        requests.append(body)
        return {"Ret": 110, "OPFileQuery": []}

    monkeypatch.setattr(camera, "_request", fake_request)

    assert camera.recordings(start=datetime(2026, 8, 1), end=datetime(2026, 8, 2), event="E") == []
    assert requests[0]["OPFileQuery"]["Event"] == "E"


def test_config_set_uses_config_write_command(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = DVRIPClient("192.0.2.1")
    observed: list[tuple[int, dict[str, Any]]] = []

    def fake_request(message_id: int, body: dict[str, Any], **_kwargs: object) -> dict[str, Any]:
        observed.append((message_id, body))
        return {"Ret": 100}

    monkeypatch.setattr(camera, "_request", fake_request)

    camera.config_set("Storage.EpitomeRecord", [{"Enable": False}])

    assert observed == [(1040, {"Name": "Storage.EpitomeRecord", "Storage.EpitomeRecord": [{"Enable": False}]})]


class _FakeDataSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, seconds: float) -> None:
        """Accept the playback timeout configuration."""
        self.timeouts.append(seconds)

    def close(self) -> None:
        """Accept socket cleanup."""


def test_stream_download_writes_until_explicit_end(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = DVRIPClient("192.0.2.1")
    camera._socket = socket.socket()
    data_socket = _FakeDataSocket()
    responses: list[tuple[int, bytes, bool]] = [
        (1425, b'{"Ret":100}', False),
        (1426, b"complete media", True),
    ]
    sent_packets: list[dict[str, Any]] = []
    control_requests: list[dict[str, Any]] = []

    def fake_connect(*_args: Any, **_kwargs: Any) -> socket.socket:
        return cast("socket.socket", data_socket)

    def fake_send(*_args: Any, **_kwargs: Any) -> None:
        sent_packets.append(cast("dict[str, Any]", _args[2]))

    def fake_request(_message_id: int, body: dict[str, Any], **_kwargs: Any) -> dict[str, int]:
        control_requests.append(body)
        return {"Ret": 100}

    def fake_receive(_data_socket: socket.socket) -> tuple[int, bytes, bool]:
        return responses.pop(0)

    monkeypatch.setattr("growcam.dvrip.socket.create_connection", fake_connect)
    monkeypatch.setattr(camera, "_send_packet", fake_send)
    monkeypatch.setattr(camera, "_request", fake_request)
    monkeypatch.setattr(camera, "_receive_packet", fake_receive)
    output = BytesIO()

    try:
        written = camera.stream_download(
            "/idea0/2026-07-31/001/01.45.29-23.44.10[E][@b9][12]._h264",
            output,
        )
    finally:
        camera.close()

    assert written == len(b"complete media")
    assert output.getvalue() == b"complete media"
    assert sent_packets[0]["OPPlayBack"]["Action"] == "DownloadStart"
    assert control_requests[0]["OPPlayBack"]["Action"] == "DownloadStart"
    assert control_requests[-1]["OPPlayBack"]["Action"] == "DownloadStop"
    assert data_socket.timeouts == [30.0]


def test_stream_download_stops_camera_after_consumer_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = DVRIPClient("192.0.2.1")
    camera._socket = socket.socket()
    responses = iter(
        [
            (1425, b'{"Ret":100}', False),
            (1426, b"media after browser disconnect", False),
        ]
    )
    control_actions: list[str] = []

    class DisconnectedOutput:
        def write(self, _payload: bytes) -> int:
            raise BrokenPipeError

    def fake_connect(*_args: Any, **_kwargs: Any) -> socket.socket:
        return cast("socket.socket", _FakeDataSocket())

    def fake_request(_message_id: int, body: dict[str, Any], **_kwargs: Any) -> dict[str, int]:
        control_actions.append(str(cast("dict[str, Any]", body["OPPlayBack"])["Action"]))
        return {"Ret": 100}

    def fake_send(*_args: Any, **_kwargs: Any) -> None:
        return None

    def fake_receive(_socket: socket.socket) -> tuple[int, bytes, bool]:
        return next(responses)

    monkeypatch.setattr("growcam.dvrip.socket.create_connection", fake_connect)
    monkeypatch.setattr(camera, "_send_packet", fake_send)
    monkeypatch.setattr(camera, "_request", fake_request)
    monkeypatch.setattr(camera, "_receive_packet", fake_receive)

    try:
        with pytest.raises(BrokenPipeError):
            _ = camera.stream_download(
                "/idea0/2026-08-02/001/00.30.00-00.40.00[R][0].h264",
                cast("BinaryIO", DisconnectedOutput()),
            )
    finally:
        camera.close()

    assert control_actions == ["DownloadStart", "DownloadStop"]


def test_playback_snapshot_accepts_firmware_close_after_valid_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = DVRIPClient("192.0.2.1")
    camera._socket = socket.socket()
    responses: list[tuple[int, bytes, bool] | DVRIPError] = [
        (1425, b'{"Ret":100}', False),
        (1422, b"valid media", False),
        DVRIPError("Camera closed the DVRIP connection"),
    ]
    sent_packets: list[dict[str, Any]] = []
    control_requests: list[dict[str, Any]] = []

    def fake_receive(_data_socket: socket.socket) -> tuple[int, bytes, bool]:
        response = responses.pop(0)
        if isinstance(response, DVRIPError):
            raise response
        return response

    def fake_connect(*_args: Any, **_kwargs: Any) -> socket.socket:
        return cast("socket.socket", _FakeDataSocket())

    def fake_send(*_args: Any, **_kwargs: Any) -> None:
        sent_packets.append(cast("dict[str, Any]", _args[2]))

    def fake_request(_message_id: int, body: dict[str, Any], **_kwargs: Any) -> dict[str, int]:
        control_requests.append(body)
        return {"Ret": 100}

    monkeypatch.setattr("growcam.dvrip.socket.create_connection", fake_connect)
    monkeypatch.setattr(camera, "_send_packet", fake_send)
    monkeypatch.setattr(camera, "_request", fake_request)
    monkeypatch.setattr(camera, "_receive_packet", fake_receive)
    destination = tmp_path / "preview.hevc"

    try:
        written = camera.playback_snapshot(
            "/idea0/2026-07-31/001/01.45.29-12.59.09[E][@8e][12]._h264",
            destination,
            expected_bytes=100,
        )
    finally:
        camera.close()

    assert written == len(b"valid media")
    assert destination.read_bytes() == b"valid media"
    assert sent_packets[0]["OPPlayBack"]["Action"] == "Claim"
    assert sent_packets[0]["OPPlayBack"]["Parameter"]["PlayMode"] == "ByName"
    assert control_requests[0]["OPPlayBack"]["Action"] == "Start"
    assert control_requests[-1]["OPPlayBack"]["Action"] == "Stop"


def test_playback_by_time_sends_native_file_type_and_stops_at_eof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = DVRIPClient("192.0.2.1")
    camera._socket = socket.socket()
    data_socket = _FakeDataSocket()
    responses = iter(
        [
            (1425, b'{"Ret":100}', False),
            (1422, b"epitome media", True),
            (1423, b"", True),
        ]
    )
    sent_packets: list[dict[str, Any]] = []
    control_requests: list[dict[str, Any]] = []

    def fake_connect(*_args: Any, **_kwargs: Any) -> socket.socket:
        return cast("socket.socket", data_socket)

    def fake_send(*_args: Any, **_kwargs: Any) -> None:
        sent_packets.append(cast("dict[str, Any]", _args[2]))

    def fake_request(_message_id: int, body: dict[str, Any], **_kwargs: Any) -> dict[str, int]:
        control_requests.append(body)
        return {"Ret": 100}

    def fake_receive(_data_socket: socket.socket) -> tuple[int, bytes, bool]:
        return next(responses)

    monkeypatch.setattr("growcam.dvrip.socket.create_connection", fake_connect)
    monkeypatch.setattr(camera, "_send_packet", fake_send)
    monkeypatch.setattr(camera, "_request", fake_request)
    monkeypatch.setattr(camera, "_receive_packet", fake_receive)
    destination = tmp_path / "epitome.hevc"

    try:
        written = camera.playback_by_time_snapshot(
            start=datetime(2026, 7, 31, 1, 45, 29),
            end=datetime(2026, 8, 1, 14, 14, 9),
            destination=destination,
            file_type=5,
        )
    finally:
        camera.close()

    assert written == len(b"epitome media")
    assert destination.read_bytes() == b"epitome media"
    claim = sent_packets[0]["OPPlayBack"]
    start = control_requests[0]["OPPlayBack"]
    assert claim["Action"] == "Claim"
    assert claim["StreamType"] == 0
    assert claim["Parameter"]["PlayMode"] == "ByTime"
    assert claim["Parameter"]["StreamType"] == 0
    assert claim["Parameter"]["Value"] == 5
    assert start["Action"] == "Start"
    assert control_requests[-1]["OPPlayBack"]["Action"] == "Stop"
    assert data_socket.timeouts == [30.0, 1.0]


def test_media_keepalive_sends_heartbeat_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = DVRIPClient("192.0.2.1")
    heartbeat_sent = threading.Event()
    requests: list[tuple[int, dict[str, Any]]] = []

    def fake_request(message_id: int, body: dict[str, Any], **_kwargs: object) -> dict[str, int]:
        requests.append((message_id, body))
        heartbeat_sent.set()
        return {"Ret": 100}

    monkeypatch.setattr(camera, "_keepalive_interval", lambda: 0.001)
    monkeypatch.setattr(camera, "_request", fake_request)
    keepalive = camera._start_keepalive()
    assert heartbeat_sent.wait(timeout=1)
    camera._finish_keepalive(keepalive)

    assert requests[0] == (1006, {"Name": "KeepAlive"})
