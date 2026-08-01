"""Behavior tests for guarded timelapse configuration updates."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, cast

import pytest

from growcam.timelapse import (
    TIMELAPSE_CONFIG_NAME,
    TimelapseConfig,
    TimelapseConflictError,
    TimelapseUpdateError,
    TimelapseValidationError,
    update_timelapse,
)


def _camera_value(*, enabled: bool = True, interval: int = 900) -> list[dict[str, object]]:
    return [
        {
            "Enable": enabled,
            "EndTime": "2026-09-11 01:44:02",
            "Interval": interval,
            "StartTime": "2026-07-31 01:45:28",
            "TimeSection": [
                "1 00:00:00-23:59:59",
                "0 00:00:00-23:59:59",
                "0 00:00:00-23:59:59",
                "0 00:00:00-23:59:59",
                "0 00:00:00-23:59:59",
                "0 00:00:00-23:59:59",
            ],
        }
    ]


class FakeConfigClient:
    """Stateful ConfigGet/ConfigSet fake with controllable verification failures."""

    def __init__(self, value: object) -> None:
        """Initialize the fake with one camera-shaped value."""
        self.value = deepcopy(value)
        self.writes: list[object] = []
        self.retain_first_write = True
        self.fail_rollback = False

    def config_get(self, name: str) -> Any:
        """Return the current fake configuration."""
        assert name == TIMELAPSE_CONFIG_NAME
        return deepcopy(self.value)

    def config_set(self, name: str, value: object) -> None:
        """Record and optionally retain one fake configuration write."""
        assert name == TIMELAPSE_CONFIG_NAME
        self.writes.append(deepcopy(value))
        if len(self.writes) == 1 and not self.retain_first_write:
            return
        if len(self.writes) > 1 and self.fail_rollback:
            raise OSError("rollback transport failed")
        self.value = deepcopy(value)


def test_camera_config_parses_progress_and_daily_window() -> None:
    config = TimelapseConfig.from_camera(_camera_value())

    payload = config.to_api(now=datetime(2026, 8, 1, 1, 45, 28))

    assert config.interval_seconds == 900
    assert config.daily_window.start == "00:00:00"
    assert payload["active"] is True
    assert cast("float", payload["progressPercent"]) > 0
    assert payload["revision"] == config.revision


def test_browser_config_builds_one_guarded_daily_window() -> None:
    config = TimelapseConfig.from_browser(
        {
            "enabled": True,
            "intervalSeconds": 300,
            "startTime": "2026-08-01T08:00:00",
            "endTime": "2026-08-15T18:00:00",
            "dailyStart": "08:30:00",
            "dailyEnd": "17:45:00",
        }
    )

    assert config.time_sections[0] == "1 08:30:00-17:45:00"
    assert len(config.time_sections) == 6
    assert config.to_camera_value()[0]["Interval"] == 300


@pytest.mark.parametrize("interval", [0, 86_401])
def test_interval_outside_firmware_range_is_rejected(interval: int) -> None:
    with pytest.raises(TimelapseValidationError, match="between 1 and 86400"):
        _ = TimelapseConfig.from_camera(_camera_value(interval=interval))


def test_invalid_daily_window_is_rejected() -> None:
    with pytest.raises(TimelapseValidationError, match="daily end time"):
        _ = TimelapseConfig.from_browser(
            {
                "enabled": True,
                "intervalSeconds": 900,
                "startTime": "2026-08-01T08:00:00",
                "endTime": "2026-08-15T18:00:00",
                "dailyStart": "18:00:00",
                "dailyEnd": "08:00:00",
            }
        )


def test_update_writes_and_verifies_requested_config() -> None:
    previous = TimelapseConfig.from_camera(_camera_value())
    desired = TimelapseConfig.from_camera(_camera_value(interval=300))
    client = FakeConfigClient(previous.to_camera_value())

    result = update_timelapse(client, desired, expected_revision=previous.revision)

    assert result.previous == previous
    assert result.current == desired
    assert client.writes == [desired.to_camera_value()]


def test_stale_revision_blocks_write() -> None:
    previous = TimelapseConfig.from_camera(_camera_value())
    client = FakeConfigClient(previous.to_camera_value())

    with pytest.raises(TimelapseConflictError, match="changed"):
        _ = update_timelapse(client, previous, expected_revision="stale")

    assert client.writes == []


def test_verification_mismatch_rolls_back_previous_config() -> None:
    previous = TimelapseConfig.from_camera(_camera_value())
    desired = TimelapseConfig.from_camera(_camera_value(interval=300))
    client = FakeConfigClient(previous.to_camera_value())
    client.retain_first_write = False

    with pytest.raises(TimelapseUpdateError) as raised:
        _ = update_timelapse(client, desired, expected_revision=previous.revision)

    assert raised.value.rollback_verified is True
    assert client.value == previous.to_camera_value()
    assert client.writes == [desired.to_camera_value(), previous.to_camera_value()]


def test_rollback_failure_is_reported() -> None:
    previous = TimelapseConfig.from_camera(_camera_value())
    desired = TimelapseConfig.from_camera(_camera_value(interval=300))
    client = FakeConfigClient(previous.to_camera_value())
    client.retain_first_write = False
    client.fail_rollback = True

    with pytest.raises(TimelapseUpdateError) as raised:
        _ = update_timelapse(client, desired, expected_revision=previous.revision)

    assert raised.value.rollback_verified is False
