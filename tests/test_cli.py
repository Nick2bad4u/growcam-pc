"""Unit tests for user-facing command defaults."""

# pyright: reportPrivateUsage=false

import webbrowser
from collections.abc import Callable
from unittest.mock import Mock

import pytest

from growcam import cli
from growcam.cli import _browser_url, _parser


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
