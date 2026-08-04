"""Tests for cross-process DVRIP session exclusion."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import pytest

import growcam.camera_lock as camera_lock_module
from growcam.camera_lock import CameraLockUnavailableError, CameraProcessLock
from growcam.dvrip import DVRIPClient, DVRIPError

if TYPE_CHECKING:
    from pathlib import Path


def test_second_process_lock_is_rejected_until_the_owner_releases(tmp_path: Path) -> None:
    first = CameraProcessLock.acquire("192.0.2.1", 34567, directory=tmp_path)
    try:
        with pytest.raises(CameraLockUnavailableError, match="already using this camera"):
            _ = CameraProcessLock.acquire("192.0.2.1", 34567, directory=tmp_path)
    finally:
        first.release()

    second = CameraProcessLock.acquire("192.0.2.1", 34567, directory=tmp_path)
    second.release()


def test_client_lock_failure_never_opens_a_camera_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(camera_lock_module, "gettempdir", lambda: str(tmp_path))
    owner = CameraProcessLock.acquire("192.0.2.1", 34567)
    socket_attempted = False

    def fail_connection(*_args: object, **_kwargs: object) -> socket.socket:
        nonlocal socket_attempted
        socket_attempted = True
        raise AssertionError("socket.create_connection must not run when the local camera lock is held")

    monkeypatch.setattr(socket, "create_connection", fail_connection)
    camera = DVRIPClient("192.0.2.1")
    try:
        with pytest.raises(DVRIPError, match="already using this camera") as raised:
            _ = camera.connect()
    finally:
        owner.release()

    assert raised.value.operation == "local lock"
    assert raised.value.return_code is None
    assert socket_attempted is False


def test_failed_camera_login_releases_the_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(camera_lock_module, "gettempdir", lambda: str(tmp_path))
    connection = socket.socket()

    def open_connection(*_args: object, **_kwargs: object) -> socket.socket:
        return connection

    def reject_login(_message_id: int, _body: dict[str, object], **_kwargs: object) -> dict[str, int]:
        return {"Ret": 106}

    monkeypatch.setattr(socket, "create_connection", open_connection)
    camera = DVRIPClient("192.0.2.1")
    monkeypatch.setattr(camera, "_request", reject_login)

    with pytest.raises(DVRIPError, match="username or password is incorrect"):
        _ = camera.connect()

    assert connection.fileno() == -1
    replacement = CameraProcessLock.acquire("192.0.2.1", 34567)
    replacement.release()
