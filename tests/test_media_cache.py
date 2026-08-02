"""Tests for coalesced and bounded preview generation."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from growcam.media_cache import MediaCache

if TYPE_CHECKING:
    from pathlib import Path


def test_identical_concurrent_preview_builds_are_coalesced(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())
    build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0

    def builder(destination: Path) -> None:
        nonlocal build_count
        build_count += 1
        build_started.set()
        assert release_build.wait(timeout=5)
        _ = destination.write_bytes(b"preview")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_build, "same", ".mp4", builder)
        assert build_started.wait(timeout=5)
        second = executor.submit(cache.get_or_build, "same", ".mp4", builder)
        release_build.set()
        first_path, first_hit = first.result(timeout=5)
        second_path, second_hit = second.result(timeout=5)

    assert build_count == 1
    assert first_path == second_path
    assert first_path.read_bytes() == b"preview"
    assert first_hit is False
    assert second_hit is True


def test_completed_preview_is_reused_without_rebuilding(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())
    build_count = 0

    def builder(destination: Path) -> None:
        nonlocal build_count
        build_count += 1
        _ = destination.write_bytes(b"preview")

    first, first_hit = cache.get_or_build("stable", ".mp4", builder)
    second, second_hit = cache.get_or_build("stable", ".mp4", builder)

    assert (first, first_hit) == (second, False)
    assert second_hit is True
    assert build_count == 1


def test_completed_preview_is_reused_by_a_new_cache_instance(tmp_path: Path) -> None:
    first_cache = MediaCache(tmp_path, threading.Lock())

    def builder(destination: Path) -> None:
        _ = destination.write_bytes(b"persistent preview")

    first, first_hit = first_cache.get_or_build("persistent", ".mp4", builder)
    second_cache = MediaCache(tmp_path, threading.Lock())
    second, second_hit = second_cache.get_or_build(
        "persistent",
        ".mp4",
        lambda _destination: pytest.fail("persistent preview was rebuilt"),
    )

    assert first == second
    assert first_hit is False
    assert second_hit is True


def test_empty_persistent_preview_is_rebuilt(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())

    with pytest.raises(RuntimeError, match="non-empty"):
        _ = cache.get_or_build(
            "empty",
            ".mp4",
            lambda destination: destination.touch(),
        )

    assert not list(tmp_path.iterdir())


def test_cache_startup_removes_only_stale_generated_partials(tmp_path: Path) -> None:
    streaming_partial = tmp_path / "preview.mp4.part"
    transcode_partial = tmp_path / "preview.part.mp4"
    completed = tmp_path / "completed.mp4"
    _ = streaming_partial.write_bytes(b"interrupted stream")
    _ = transcode_partial.write_bytes(b"interrupted transcode")
    _ = completed.write_bytes(b"completed preview")

    _ = MediaCache(tmp_path, threading.Lock())

    assert not streaming_partial.exists()
    assert not transcode_partial.exists()
    assert completed.read_bytes() == b"completed preview"
