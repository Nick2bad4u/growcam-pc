"""Typed configuration and update safeguards for camera timelapses."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

TIMELAPSE_CONFIG_NAME = "Storage.EpitomeRecord"
_CAMERA_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_SECTION_PATTERN = re.compile(r"^(?P<enabled>[01]) (?P<start>\d{2}:\d{2}:\d{2})-(?P<end>\d{2}:\d{2}:\d{2})$")
_MAX_INTERVAL_SECONDS = 86_400
_MAX_SCHEDULE_DAYS = 730
_MAX_TIME_SECTIONS = 6
_CLOCK_PART_COUNT = 3
_MAX_HOUR = 23
_MAX_MINUTE_OR_SECOND = 59


class TimelapseValidationError(ValueError):
    """Raised when a camera or browser timelapse payload is invalid."""


class TimelapseConflictError(RuntimeError):
    """Raised when camera configuration changed after the browser loaded it."""


class TimelapseUpdateError(RuntimeError):
    """Raised when a camera update fails or cannot be verified."""

    def __init__(self, message: str, *, rollback_verified: bool) -> None:
        """Record whether the previous configuration was restored."""
        super().__init__(message)
        self.rollback_verified = rollback_verified


class TimelapseConfigClient(Protocol):
    """Minimal camera configuration surface needed for guarded updates."""

    def config_get(self, name: str) -> Any:
        """Fetch a named configuration block."""

    def config_set(self, name: str, value: object) -> None:
        """Replace a named configuration block."""


@dataclass(frozen=True)
class DailyWindow:
    """One enabled daily recording window."""

    start: str
    end: str


@dataclass(frozen=True)
class TimelapseConfig:
    """Validated local representation of ``Storage.EpitomeRecord``."""

    enabled: bool
    interval_seconds: int
    start_time: datetime
    end_time: datetime
    time_sections: tuple[str, ...]

    @classmethod
    def from_camera(cls, value: object) -> TimelapseConfig:
        """Parse the camera's one-channel Epitome configuration list."""
        if not isinstance(value, list) or not value:
            raise TimelapseValidationError("Camera timelapse configuration must be a non-empty list")
        first = cast("list[object]", value)[0]
        payload = _string_mapping(first, "camera timelapse configuration")
        sections_value = payload.get("TimeSection")
        if not isinstance(sections_value, list):
            raise TimelapseValidationError("Camera TimeSection must be a list")
        sections = tuple(_required_string(item, "TimeSection entry") for item in cast("list[object]", sections_value))
        return cls._validated(
            enabled=_required_bool(payload.get("Enable"), "Enable"),
            interval_seconds=_required_int(payload.get("Interval"), "Interval"),
            start_time=_camera_datetime(payload.get("StartTime"), "StartTime"),
            end_time=_camera_datetime(payload.get("EndTime"), "EndTime"),
            time_sections=sections,
        )

    @classmethod
    def from_browser(cls, value: object) -> TimelapseConfig:
        """Parse and validate the constrained localhost configuration form."""
        payload = _string_mapping(value, "timelapse request")
        daily_start = _required_string(payload.get("dailyStart"), "dailyStart")
        daily_end = _required_string(payload.get("dailyEnd"), "dailyEnd")
        _ = _clock_seconds(daily_start, "dailyStart")
        _ = _clock_seconds(daily_end, "dailyEnd")
        if _clock_seconds(daily_end, "dailyEnd") <= _clock_seconds(daily_start, "dailyStart"):
            raise TimelapseValidationError("The daily end time must be after the daily start time")
        disabled = "0 00:00:00-23:59:59"
        return cls._validated(
            enabled=_required_bool(payload.get("enabled"), "enabled"),
            interval_seconds=_required_int(payload.get("intervalSeconds"), "intervalSeconds"),
            start_time=_browser_datetime(payload.get("startTime"), "startTime"),
            end_time=_browser_datetime(payload.get("endTime"), "endTime"),
            time_sections=(f"1 {daily_start}-{daily_end}", disabled, disabled, disabled, disabled, disabled),
        )

    @classmethod
    def _validated(
        cls,
        *,
        enabled: bool,
        interval_seconds: int,
        start_time: datetime,
        end_time: datetime,
        time_sections: tuple[str, ...],
    ) -> TimelapseConfig:
        if not 1 <= interval_seconds <= _MAX_INTERVAL_SECONDS:
            raise TimelapseValidationError("Timelapse interval must be between 1 and 86400 seconds")
        if end_time <= start_time:
            raise TimelapseValidationError("Timelapse end time must be after its start time")
        if (end_time - start_time).days > _MAX_SCHEDULE_DAYS:
            raise TimelapseValidationError("Timelapse schedules cannot exceed 730 days")
        if not 1 <= len(time_sections) <= _MAX_TIME_SECTIONS:
            raise TimelapseValidationError("TimeSection must contain between one and six windows")
        for section in time_sections:
            _ = _parse_section(section)
        return cls(enabled, interval_seconds, start_time, end_time, time_sections)

    @property
    def daily_window(self) -> DailyWindow:
        """Return the first enabled daily window, or the full day fallback."""
        for section in self.time_sections:
            enabled, start, end = _parse_section(section)
            if enabled:
                return DailyWindow(start, "23:59:59" if end == "24:00:00" else end)
        return DailyWindow("00:00:00", "23:59:59")

    @property
    def revision(self) -> str:
        """Return a stable optimistic-concurrency token for this configuration."""
        canonical = json.dumps(self.to_camera_value(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_camera_value(self) -> list[dict[str, object]]:
        """Encode the exact list shape accepted by ConfigSet command 1040."""
        return [
            {
                "Enable": self.enabled,
                "EndTime": self.end_time.strftime(_CAMERA_DATETIME_FORMAT),
                "Interval": self.interval_seconds,
                "StartTime": self.start_time.strftime(_CAMERA_DATETIME_FORMAT),
                "TimeSection": list(self.time_sections),
            }
        ]

    def to_api(self, *, now: datetime) -> dict[str, object]:
        """Return browser-safe settings and calculated schedule progress."""
        window = self.daily_window
        duration = (self.end_time - self.start_time).total_seconds()
        elapsed = min(max((now - self.start_time).total_seconds(), 0.0), duration)
        progress = elapsed / duration if duration else 0.0
        captured = int(elapsed // self.interval_seconds) + (1 if elapsed > 0 else 0)
        expected = int(duration // self.interval_seconds) + 1
        return {
            "enabled": self.enabled,
            "intervalSeconds": self.interval_seconds,
            "startTime": self.start_time.isoformat(timespec="seconds"),
            "endTime": self.end_time.isoformat(timespec="seconds"),
            "dailyStart": window.start,
            "dailyEnd": window.end,
            "revision": self.revision,
            "active": self.enabled and self.start_time <= now < self.end_time,
            "progressPercent": round(progress * 100, 2),
            "estimatedCaptures": captured,
            "expectedCaptures": expected,
        }


@dataclass(frozen=True)
class TimelapseUpdateResult:
    """Verified before-and-after values from a camera configuration write."""

    previous: TimelapseConfig
    current: TimelapseConfig


def update_timelapse(
    client: TimelapseConfigClient,
    desired: TimelapseConfig,
    *,
    expected_revision: str,
) -> TimelapseUpdateResult:
    """Write, verify, and if necessary roll back a timelapse configuration."""
    previous = TimelapseConfig.from_camera(client.config_get(TIMELAPSE_CONFIG_NAME))
    if previous.revision != expected_revision:
        raise TimelapseConflictError("Camera timelapse settings changed; refresh before applying your edits")

    write_started = False
    try:
        write_started = True
        client.config_set(TIMELAPSE_CONFIG_NAME, desired.to_camera_value())
        current = TimelapseConfig.from_camera(client.config_get(TIMELAPSE_CONFIG_NAME))
        if current != desired:
            raise TimelapseUpdateError(
                "Camera did not retain the requested timelapse settings", rollback_verified=False
            )
    except (OSError, RuntimeError, ValueError) as error:
        rollback_verified = False
        if write_started:
            try:
                client.config_set(TIMELAPSE_CONFIG_NAME, previous.to_camera_value())
                restored = TimelapseConfig.from_camera(client.config_get(TIMELAPSE_CONFIG_NAME))
                rollback_verified = restored == previous
            except (OSError, RuntimeError, ValueError):
                rollback_verified = False
        message = str(error) if isinstance(error, TimelapseUpdateError) else f"Camera timelapse update failed: {error}"
        raise TimelapseUpdateError(message, rollback_verified=rollback_verified) from error
    return TimelapseUpdateResult(previous, current)


def _string_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TimelapseValidationError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            raise TimelapseValidationError(f"{label} contains a non-string key")
        result[key] = item
    return result


def _required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TimelapseValidationError(f"{label} must be a boolean")
    return value


def _required_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TimelapseValidationError(f"{label} must be an integer")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TimelapseValidationError(f"{label} must be a non-empty string")
    return value


def _camera_datetime(value: object, label: str) -> datetime:
    text = _required_string(value, label)
    try:
        return datetime.strptime(text, _CAMERA_DATETIME_FORMAT)  # noqa: DTZ007 - Camera uses local wall-clock time.
    except ValueError as error:
        raise TimelapseValidationError(f"{label} is not a camera datetime") from error


def _browser_datetime(value: object, label: str) -> datetime:
    text = _required_string(value, label)
    try:
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise TimelapseValidationError(f"{label} is not a valid local datetime") from error
    if result.tzinfo is not None:
        raise TimelapseValidationError(f"{label} must not contain a timezone")
    return result.replace(microsecond=0)


def _clock_seconds(value: str, label: str, *, allow_end_of_day: bool = False) -> int:
    parts = value.split(":")
    if len(parts) != _CLOCK_PART_COUNT or any(not part.isdigit() for part in parts):
        raise TimelapseValidationError(f"{label} must use HH:MM:SS")
    hour, minute, second = (int(part) for part in parts)
    if allow_end_of_day and (hour, minute, second) == (24, 0, 0):
        return _MAX_INTERVAL_SECONDS
    if (
        not 0 <= hour <= _MAX_HOUR
        or not 0 <= minute <= _MAX_MINUTE_OR_SECOND
        or not 0 <= second <= _MAX_MINUTE_OR_SECOND
    ):
        raise TimelapseValidationError(f"{label} is outside the valid clock range")
    return hour * 3600 + minute * 60 + second


def _parse_section(value: str) -> tuple[bool, str, str]:
    match = _SECTION_PATTERN.fullmatch(value)
    if match is None:
        raise TimelapseValidationError("TimeSection entries must use '1 HH:MM:SS-HH:MM:SS'")
    start = match.group("start")
    end = match.group("end")
    if _clock_seconds(end, "TimeSection end", allow_end_of_day=True) <= _clock_seconds(start, "TimeSection start"):
        raise TimelapseValidationError("TimeSection end must be after its start")
    return match.group("enabled") == "1", start, end
