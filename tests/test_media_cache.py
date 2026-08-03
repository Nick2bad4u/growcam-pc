"""Tests for coalesced and bounded preview generation."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pytest
from typing_extensions import override

import growcam.media_cache as media_cache_module
from growcam.media_cache import MediaCache


def _write_preview(destination: Path, content: bytes) -> None:
    _ = destination.write_bytes(content)


def test_identical_concurrent_preview_builds_are_coalesced(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())
    build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0

    def builder(destination: Path) -> None:
        nonlocal build_count
        build_count += 1
        _ = destination.write_bytes(b"preview")
        build_started.set()
        assert release_build.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get_or_build, "same", ".mp4", builder)
        assert build_started.wait(timeout=5)
        assert cache.lookup("same", ".mp4") == (None, True)
        second = executor.submit(cache.get_or_build, "same", ".mp4", builder)
        with pytest.raises(TimeoutError):
            _ = second.result(timeout=0.05)
        release_build.set()
        first_path, first_hit = first.result(timeout=5)
        second_path, second_hit = second.result(timeout=5)

    assert build_count == 1
    assert first_path == second_path
    assert first_path.read_bytes() == b"preview"
    assert first_hit is False
    assert second_hit is True
    assert cache.lookup("same", ".mp4") == (first_path, False)


def test_waiting_caller_rebuilds_after_inflight_leader_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiter_started = threading.Event()

    class ObservedFuture(Future[Path]):
        @override
        def result(self, timeout: float | None = None) -> Path:
            waiter_started.set()
            return super().result(timeout)

    monkeypatch.setattr(media_cache_module, "Future", ObservedFuture)
    cache = MediaCache(tmp_path, threading.Lock())
    first_build_started = threading.Event()
    release_failed_build = threading.Event()
    build_count = 0

    def builder(destination: Path) -> None:
        nonlocal build_count
        build_count += 1
        if build_count == 1:
            first_build_started.set()
            assert release_failed_build.wait(timeout=5)
            _ = destination.write_bytes(b"incomplete")
            raise RuntimeError("leader disconnected")
        _ = destination.write_bytes(b"recovered preview")

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(cache.get_or_build, "retry", ".mp4", builder)
        assert first_build_started.wait(timeout=5)
        follower = executor.submit(cache.get_or_build, "retry", ".mp4", builder)
        assert waiter_started.wait(timeout=5)
        release_failed_build.set()

        with pytest.raises(RuntimeError, match="leader disconnected"):
            _ = leader.result(timeout=5)
        follower_path, follower_hit = follower.result(timeout=5)

    assert build_count == 2
    assert follower_hit is False
    assert follower_path.read_bytes() == b"recovered preview"
    assert cache.stats().busy is False


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


def test_cache_stats_and_reconfiguration_ignore_active_indexing_partial(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock(), maximum_entries=1, maximum_bytes=64)
    completed = tmp_path / "completed.mp4"
    indexing_partial = tmp_path / "completed.indexed.part.mp4"
    _ = completed.write_bytes(b"completed preview")
    _ = indexing_partial.write_bytes(b"in-progress indexed preview")

    stats = cache.stats()
    reconfigured = cache.reconfigure(maximum_entries=1, maximum_bytes=64)

    assert stats.entry_count == 1
    assert stats.current_bytes == len(b"completed preview")
    assert reconfigured.entry_count == 1
    assert indexing_partial.read_bytes() == b"in-progress indexed preview"


def test_reconfigure_does_not_evict_a_visible_inflight_destination(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())
    destination_written = threading.Event()
    release_build = threading.Event()

    def builder(destination: Path) -> None:
        _ = destination.write_bytes(b"streaming preview")
        destination_written.set()
        assert release_build.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        build = executor.submit(cache.get_or_build, "active", ".mp4", builder)
        assert destination_written.wait(timeout=5)
        _ = cache.reconfigure(maximum_entries=1, maximum_bytes=1)
        assert next(iter(tmp_path.glob("*.mp4"))).read_bytes() == b"streaming preview"
        release_build.set()
        path, cache_hit = build.result(timeout=5)

    assert path.read_bytes() == b"streaming preview"
    assert cache_hit is False


def test_cache_evicts_older_previews_to_honor_byte_budget(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock(), maximum_entries=10, maximum_bytes=10)

    first, _first_hit = cache.get_or_build("first", ".mp4", lambda destination: _write_preview(destination, b"123456"))
    second, _second_hit = cache.get_or_build(
        "second",
        ".mp4",
        lambda destination: _write_preview(destination, b"abcdef"),
    )

    assert not first.exists()
    assert second.read_bytes() == b"abcdef"
    assert cache.stats().to_api() == {
        "entryCount": 1,
        "currentBytes": 6,
        "maximumEntries": 10,
        "maximumBytes": 10,
        "busy": False,
    }


def test_new_oversized_preview_remains_available_to_its_request(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock(), maximum_entries=2, maximum_bytes=4)

    preview, cache_hit = cache.get_or_build(
        "oversized",
        ".mp4",
        lambda destination: _write_preview(destination, b"preview"),
    )

    assert cache_hit is False
    assert preview.read_bytes() == b"preview"
    assert cache.stats().current_bytes == 7


def test_reconfigure_trims_existing_cache_immediately(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock(), maximum_entries=3, maximum_bytes=100)
    for key in ("one", "two", "three"):
        _ = cache.get_or_build(
            key,
            ".mp4",
            partial(_write_preview, content=key.encode()),
        )

    stats = cache.reconfigure(maximum_entries=1, maximum_bytes=100)

    assert stats.entry_count == 1
    assert len(list(tmp_path.glob("*.mp4"))) == 1


def test_clear_removes_only_completed_cache_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "keep.txt"
    _ = unrelated.write_text("user file", encoding="utf-8")
    cache = MediaCache(tmp_path, threading.Lock())
    _ = cache.get_or_build("preview", ".mp4", lambda destination: _write_preview(destination, b"preview"))

    stats = cache.clear()

    assert stats.entry_count == 0
    assert unrelated.read_text(encoding="utf-8") == "user file"


def test_clear_refuses_while_a_preview_build_is_inflight(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path, threading.Lock())
    build_started = threading.Event()
    release_build = threading.Event()

    def builder(destination: Path) -> None:
        build_started.set()
        assert release_build.wait(timeout=5)
        _ = destination.write_bytes(b"preview")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(cache.get_or_build, "busy", ".mp4", builder)
        assert build_started.wait(timeout=5)
        with pytest.raises(RuntimeError, match="still being generated"):
            _ = cache.clear()
        release_build.set()
        _ = future.result(timeout=5)


@pytest.mark.parametrize(("entries", "size"), [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_cache_rejects_non_positive_limits(tmp_path: Path, entries: int, size: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _ = MediaCache(tmp_path, threading.Lock(), maximum_entries=entries, maximum_bytes=size)
