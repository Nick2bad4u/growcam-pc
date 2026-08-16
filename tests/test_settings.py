"""Tests for persistent local application preferences."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from growcam.settings import (
    AppSettings,
    SettingsConflictError,
    SettingsError,
    SettingsStore,
    SettingsValidationError,
    settings_path,
)


def _settings_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "cacheMaxBytes": 2 * 1024**3,
        "cacheMaxEntries": 40,
        "rewindPreviewSeconds": 300,
        "continuePlayback": False,
        "previewVideoCodec": "hevc",
    }
    values.update(overrides)
    return values


def test_settings_path_is_platform_native(tmp_path: Path) -> None:
    assert settings_path(
        platform_name="win32",
        environment={"LOCALAPPDATA": "C:/LocalData"},
        home=tmp_path,
    ) == Path("C:/LocalData/GrowCam/settings.json")
    assert settings_path(platform_name="darwin", environment={}, home=tmp_path) == (
        tmp_path / "Library" / "Application Support" / "GrowCam" / "settings.json"
    )
    assert settings_path(platform_name="linux", environment={}, home=tmp_path) == (
        tmp_path / ".config" / "growcam" / "settings.json"
    )


def test_settings_path_honors_xdg_config_home(tmp_path: Path) -> None:
    config_root = tmp_path / "xdg-config"

    assert (
        settings_path(
            platform_name="linux",
            environment={"XDG_CONFIG_HOME": str(config_root)},
            home=tmp_path,
        )
        == config_root / "growcam" / "settings.json"
    )


def test_settings_store_persists_and_reloads_an_atomic_revision(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = SettingsStore(path)

    updated = store.update(expected_revision=0, values=_settings_values())
    reloaded = SettingsStore(path).snapshot()

    assert updated == reloaded
    assert updated.revision == 1
    assert updated.cache_max_bytes == 2 * 1024**3
    assert updated.cache_max_entries == 40
    assert updated.rewind_preview_seconds == 300
    assert updated.continue_playback is False
    assert updated.preview_video_codec == "hevc"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 1
    assert document["revision"] == 1
    assert "revision" not in document["settings"]
    assert not list(path.parent.glob("*.tmp"))


def test_in_memory_settings_store_uses_defaults_without_writing() -> None:
    store = SettingsStore(None)

    assert store.path is None
    assert store.snapshot() == AppSettings()
    assert store.update(expected_revision=0, values=_settings_values()).revision == 1


def test_stale_settings_revision_is_rejected_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    first = store.update(expected_revision=0, values=_settings_values())
    stale_values = _settings_values(cacheMaxEntries=12)

    with pytest.raises(SettingsConflictError, match="current revision 1"):
        _ = store.update(expected_revision=0, values=stale_values)

    assert SettingsStore(path).snapshot() == first


@pytest.mark.parametrize(
    "values",
    [
        _settings_values(cacheMaxBytes=127 * 1024**2),
        _settings_values(cacheMaxEntries=0),
        _settings_values(rewindPreviewSeconds=15),
        _settings_values(continuePlayback=1),
        _settings_values(previewVideoCodec="av1"),
        {key: value for key, value in _settings_values().items() if key != "cacheMaxEntries"},
        {**_settings_values(), "unknown": True},
    ],
)
def test_invalid_settings_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(SettingsValidationError):
        _ = AppSettings.from_api(values, revision=1)


def test_corrupt_persisted_settings_are_not_silently_replaced(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    _ = path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(SettingsError, match="Could not read"):
        _ = SettingsStore(path)

    assert path.read_text(encoding="utf-8") == "{not-json"
