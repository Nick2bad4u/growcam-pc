"""Persistent application preferences for the local GrowCam interface."""

from __future__ import annotations

import json
import os
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

_SCHEMA_VERSION = 1
_MEBIBYTE = 1024**2
_MINIMUM_CACHE_BYTES = 128 * _MEBIBYTE
_MAXIMUM_CACHE_BYTES = 128 * 1024 * _MEBIBYTE
_MINIMUM_CACHE_ENTRIES = 1
_MAXIMUM_CACHE_ENTRIES = 512
_ALLOWED_REWIND_SECONDS = frozenset({60, 120, 300, 600})
_ALLOWED_PREVIEW_VIDEO_CODECS = frozenset({"auto", "h264", "hevc"})
_SETTING_KEYS = frozenset(
    {
        "cacheMaxBytes",
        "cacheMaxEntries",
        "rewindPreviewSeconds",
        "continuePlayback",
        "previewVideoCodec",
    }
)


class SettingsError(RuntimeError):
    """Raised when application settings cannot be loaded or saved."""


class SettingsConflictError(SettingsError):
    """Raised when a browser tries to replace an out-of-date settings revision."""


class SettingsValidationError(ValueError):
    """Raised when a settings document contains unsupported values."""


@dataclass(frozen=True)
class AppSettings:
    """Validated local application preferences."""

    revision: int = 0
    cache_max_bytes: int = 4 * 1024**3
    cache_max_entries: int = 24
    rewind_preview_seconds: int = 120
    continue_playback: bool = True
    preview_video_codec: str = "auto"

    def to_api(self) -> dict[str, object]:
        """Return the stable browser-facing settings representation."""
        return {
            "revision": self.revision,
            "cacheMaxBytes": self.cache_max_bytes,
            "cacheMaxEntries": self.cache_max_entries,
            "rewindPreviewSeconds": self.rewind_preview_seconds,
            "continuePlayback": self.continue_playback,
            "previewVideoCodec": self.preview_video_codec,
        }

    @classmethod
    def from_api(cls, values: Mapping[str, object], *, revision: int) -> AppSettings:
        """Validate a complete browser-facing settings representation."""
        unexpected = set(values) - _SETTING_KEYS
        missing = _SETTING_KEYS - set(values)
        if unexpected:
            raise SettingsValidationError(f"Unsupported setting: {min(unexpected)}")
        if missing:
            raise SettingsValidationError(f"Missing setting: {min(missing)}")
        cache_max_bytes = _required_integer(values, "cacheMaxBytes")
        cache_max_entries = _required_integer(values, "cacheMaxEntries")
        rewind_preview_seconds = _required_integer(values, "rewindPreviewSeconds")
        continue_playback = values["continuePlayback"]
        preview_video_codec = values["previewVideoCodec"]
        if not _MINIMUM_CACHE_BYTES <= cache_max_bytes <= _MAXIMUM_CACHE_BYTES:
            raise SettingsValidationError("Cache size must be between 128 MiB and 128 GiB")
        if not _MINIMUM_CACHE_ENTRIES <= cache_max_entries <= _MAXIMUM_CACHE_ENTRIES:
            raise SettingsValidationError("Cached preview count must be between 1 and 512")
        if rewind_preview_seconds not in _ALLOWED_REWIND_SECONDS:
            raise SettingsValidationError("Rewind preview length must be 1, 2, 5, or 10 minutes")
        if not isinstance(continue_playback, bool):
            raise SettingsValidationError("Continue playback must be a boolean")
        if not isinstance(preview_video_codec, str) or preview_video_codec not in _ALLOWED_PREVIEW_VIDEO_CODECS:
            raise SettingsValidationError("Preview video codec must be auto, h264, or hevc")
        return cls(
            revision=revision,
            cache_max_bytes=cache_max_bytes,
            cache_max_entries=cache_max_entries,
            rewind_preview_seconds=rewind_preview_seconds,
            continue_playback=continue_playback,
            preview_video_codec=preview_video_codec,
        )


class SettingsStore:
    """Thread-safe JSON-backed settings store with optimistic revisions."""

    def __init__(self, path: Path | None) -> None:
        """Load persisted settings, or use defaults for an in-memory store."""
        self._path = path
        self._lock = threading.RLock()
        self._current = self._load() if path is not None else AppSettings()

    @property
    def path(self) -> Path | None:
        """Return the backing file path, if persistence is enabled."""
        return self._path

    def snapshot(self) -> AppSettings:
        """Return the immutable current settings revision."""
        with self._lock:
            return self._current

    def update(self, *, expected_revision: int, values: Mapping[str, object]) -> AppSettings:
        """Validate, persist, and publish one complete settings replacement."""
        with self._lock:
            if expected_revision != self._current.revision:
                raise SettingsConflictError(
                    f"Settings changed since this page loaded (expected revision {expected_revision}, "
                    f"current revision {self._current.revision})"
                )
            desired = AppSettings.from_api(values, revision=self._current.revision + 1)
            self._save(desired)
            self._current = desired
            return desired

    def _load(self) -> AppSettings:
        path = cast("Path", self._path)
        if not path.exists():
            return AppSettings()
        try:
            decoded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SettingsError(f"Could not read GrowCam settings from {path}") from error
        if not isinstance(decoded, dict):
            raise SettingsError(f"GrowCam settings in {path} must be a JSON object")
        document = cast("dict[object, object]", decoded)
        if document.get("schemaVersion") != _SCHEMA_VERSION:
            raise SettingsError(f"GrowCam settings in {path} use an unsupported schema version")
        revision = document.get("revision")
        raw_settings = document.get("settings")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise SettingsError(f"GrowCam settings in {path} have an invalid revision")
        if not isinstance(raw_settings, dict):
            raise SettingsError(f"GrowCam settings in {path} are missing the settings object")
        values = _string_mapping(cast("dict[object, object]", raw_settings), path)
        try:
            return AppSettings.from_api(values, revision=revision)
        except SettingsValidationError as error:
            raise SettingsError(f"GrowCam settings in {path} are invalid: {error}") from error

    def _save(self, settings: AppSettings) -> None:
        if self._path is None:
            return
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        document = {
            "schemaVersion": _SCHEMA_VERSION,
            "revision": settings.revision,
            "settings": {key: value for key, value in settings.to_api().items() if key != "revision"},
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(document, output, indent=2, sort_keys=True)
                _ = output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            _ = temporary.replace(path)
            with suppress(OSError):
                path.chmod(0o600)
                # Windows ACLs and some network filesystems do not map POSIX modes.
        except OSError as error:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise SettingsError(f"Could not save GrowCam settings to {path}") from error


def settings_path(
    *,
    platform_name: str = sys.platform,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform-native persistent application settings path."""
    values = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    if platform_name == "win32":
        local_app_data = values.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        return root / "GrowCam" / "settings.json"
    if platform_name == "darwin":
        return user_home / "Library" / "Application Support" / "GrowCam" / "settings.json"
    xdg_config_home = values.get("XDG_CONFIG_HOME")
    root = Path(xdg_config_home) if xdg_config_home else user_home / ".config"
    return root / "growcam" / "settings.json"


def _required_integer(values: Mapping[str, object], key: str) -> int:
    value = values[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise SettingsValidationError(f"{key} must be an integer")
    return value


def _string_mapping(values: dict[object, object], path: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise SettingsError(f"GrowCam settings in {path} contain a non-string key")
        result[key] = value
    return result
