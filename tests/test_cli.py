"""Unit tests for user-facing command defaults."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import json
import webbrowser
from typing import TYPE_CHECKING
from unittest.mock import Mock

from growcam import cli
from growcam.cli import _browser_url, _parser
from growcam.dvrip import LoginInfo

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path
    from typing import Self

    import pytest


def test_default_camera_and_browser_bind_are_local() -> None:
    arguments = _parser().parse_args(["web"])

    assert arguments.host == "192.168.1.137"
    assert arguments.listen == "127.0.0.1"
    assert arguments.http_port == 8876
    assert arguments.open_browser is True


def test_download_defaults_to_playable_output() -> None:
    arguments = _parser().parse_args(["download", "/idea0/example.h264"])

    assert arguments.raw is False
    assert arguments.output is None


def test_password_can_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = "test-value"
    monkeypatch.setenv("GROWCAM_PASSWORD", expected)

    arguments = _parser().parse_args(["info"])

    assert arguments.password == expected


def test_camera_identity_can_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROWCAM_HOST", "192.0.2.8")
    monkeypatch.setenv("GROWCAM_USERNAME", "grower")

    arguments = _parser().parse_args(["info"])

    assert arguments.host == "192.0.2.8"
    assert arguments.username == "grower"


def test_web_browser_can_be_disabled() -> None:
    arguments = _parser().parse_args(["web", "--no-open"])

    assert arguments.open_browser is False


def test_wildcard_bind_has_a_reachable_local_browser_url() -> None:
    assert _browser_url("0.0.0.0", 8876) == "http://127.0.0.1:8876/"  # noqa: S104
    assert _browser_url("::1", 8876) == "http://[::1]:8876/"


def test_remote_web_bind_requires_explicit_consent(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["web", "--listen", "0.0.0.0", "--no-open"]) == 1  # noqa: S104
    assert "without --allow-remote" in capsys.readouterr().out


def test_snapshot_command_writes_the_requested_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "captures" / "plant.jpg"

    def fake_snapshot(_host: str, _username: str, _password: str) -> bytes:
        return b"jpeg"

    monkeypatch.setattr(cli, "snapshot", fake_snapshot)

    assert cli.main(["snapshot", "--output", str(destination)]) == 0

    assert destination.read_bytes() == b"jpeg"
    assert json.loads(capsys.readouterr().out) == {"output": str(destination)}


def test_info_command_serializes_camera_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeCamera:
        login_info = LoginInfo(42, 1, "GrowCam", 20)

        def __init__(self, host: str, port: int, username: str, password: str) -> None:
            assert (host, port, username, password) == ("192.0.2.8", 34567, "admin", "")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def system_info(self, name: str) -> dict[str, object]:
            return {"name": name}

    monkeypatch.setattr(cli, "DVRIPClient", FakeCamera)

    assert cli.main(["--host", "192.0.2.8", "info"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["login"]["session_id"] == 42
    assert payload["system"] == {"name": "SystemInfo"}
    assert payload["storage"] == {"name": "StorageInfo"}


def test_recordings_command_forwards_search_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, object] = {}

    class FakeCamera:
        def __init__(self, *_args: object) -> None:
            return None

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def recordings(
            self,
            *,
            start: datetime,
            end: datetime,
            channel: int,
            file_type: str,
        ) -> list[dict[str, object]]:
            observed.update(hours=(end - start).total_seconds() / 3600, channel=channel, file_type=file_type)
            return [{"FileName": "camera.jpg"}]

    monkeypatch.setattr(cli, "DVRIPClient", FakeCamera)

    assert cli.main(["recordings", "--hours", "2", "--channel", "3", "--type", "jpg"]) == 0

    assert observed == {"hours": 2.0, "channel": 3, "file_type": "jpg"}
    assert json.loads(capsys.readouterr().out)["recordings"] == [{"FileName": "camera.jpg"}]


def test_raw_download_command_keeps_camera_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "recording.hevc"

    class FakeCamera:
        def __init__(self, *_args: object) -> None:
            return None

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def download(self, camera_file: str, output: Path) -> int:
            assert camera_file == "/idea0/recording.h264"
            assert output == destination
            return 4096

    monkeypatch.setattr(cli, "DVRIPClient", FakeCamera)

    assert cli.main(["download", "/idea0/recording.h264", "--raw", "--output", str(destination)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["bytes_downloaded"] == 4096
    assert payload["format"] == "raw HEVC"


def test_clip_command_forwards_duration_and_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    destination = tmp_path / "plant.mkv"
    observed: list[object] = []

    def fake_clip(host: str, output: Path, seconds: float, username: str, password: str) -> None:
        observed.extend((host, output, seconds, username, password))

    monkeypatch.setattr(cli, "save_live_clip", fake_clip)

    assert cli.main(["--host", "192.0.2.8", "clip", "--seconds", "5", "--output", str(destination)]) == 0

    assert observed == ["192.0.2.8", destination, 5.0, "admin", ""]
    assert json.loads(capsys.readouterr().out) == {"output": str(destination), "seconds": 5.0}


def test_loopback_detection_handles_names_and_invalid_addresses() -> None:
    assert cli._is_loopback("localhost") is True
    assert cli._is_loopback("not an address") is False


def test_web_does_not_announce_before_bind_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_before_ready(*_args: object, **_kwargs: object) -> None:
        raise OSError("Local port is already in use")

    monkeypatch.setattr(cli, "serve", fail_before_ready)

    assert cli.main(["web"]) == 1
    assert capsys.readouterr().out == "growcam: Local port is already in use\n"


def test_web_opens_default_browser_only_after_server_is_ready(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    timer = Mock()
    timer_factory = Mock(return_value=timer)

    def ready_server(
        _config: object,
        _listen: str,
        _port: int,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        assert on_ready is not None
        on_ready()

    monkeypatch.setattr("growcam.cli.threading.Timer", timer_factory)
    monkeypatch.setattr(cli, "serve", ready_server)

    assert cli.main(["web"]) == 0
    timer_factory.assert_called_once_with(
        0.5,
        webbrowser.open,
        args=("http://127.0.0.1:8876/",),
    )
    assert timer.daemon is True
    timer.start.assert_called_once_with()
    assert "GrowCam browser interface: http://127.0.0.1:8876/" in capsys.readouterr().out


def test_keyboard_interrupt_stops_the_local_server(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def interrupted_server(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "serve", interrupted_server)

    assert cli.main(["web", "--no-open"]) == 0
    assert capsys.readouterr().out == "\nStopped.\n"
