"""Persistent, bounded single-flight cache for expensive camera media previews."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True)
class CacheStats:
    """Snapshot of generated preview storage and its configured limits."""

    entry_count: int
    current_bytes: int
    maximum_entries: int
    maximum_bytes: int
    busy: bool

    def to_api(self) -> dict[str, object]:
        """Return the browser-facing cache statistics representation."""
        return {
            "entryCount": self.entry_count,
            "currentBytes": self.current_bytes,
            "maximumEntries": self.maximum_entries,
            "maximumBytes": self.maximum_bytes,
            "busy": self.busy,
        }


class MediaCache:
    """Coalesce identical builds and retain a bounded set of completed previews."""

    def __init__(
        self,
        directory: Path,
        operation_lock: threading.Lock,
        *,
        maximum_entries: int = 24,
        maximum_bytes: int = 4 * 1024**3,
    ) -> None:
        """Configure cache storage and the shared camera-operation lock."""
        _validate_limits(maximum_entries, maximum_bytes)
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._operation_lock = operation_lock
        self._maximum_entries = maximum_entries
        self._maximum_bytes = maximum_bytes
        self._lock = threading.Lock()
        self._completed: OrderedDict[str, Path] = OrderedDict()
        self._inflight: dict[str, Future[Path]] = {}
        self._inflight_paths: dict[str, Path] = {}
        self._remove_stale_partials()
        with self._lock:
            self._trim_directory_locked()

    def stats(self) -> CacheStats:
        """Return a consistent cache usage snapshot."""
        with self._lock:
            previews = self._preview_files_locked()
            return CacheStats(
                entry_count=len(previews),
                current_bytes=sum(size for _path, size, _mtime in previews),
                maximum_entries=self._maximum_entries,
                maximum_bytes=self._maximum_bytes,
                busy=bool(self._inflight),
            )

    def reconfigure(self, *, maximum_entries: int, maximum_bytes: int) -> CacheStats:
        """Apply new limits immediately and evict least-recently-used previews."""
        _validate_limits(maximum_entries, maximum_bytes)
        with self._lock:
            self._maximum_entries = maximum_entries
            self._maximum_bytes = maximum_bytes
            self._trim_directory_locked()
            return self._stats_locked()

    def clear(self) -> CacheStats:
        """Remove completed generated previews when no build is in progress."""
        with self._lock:
            if self._inflight:
                raise RuntimeError("A preview is still being generated; wait before clearing the cache")
            for path, _size, _mtime in self._preview_files_locked():
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
            self._completed.clear()
            return self._stats_locked()

    def get_or_build(self, key: str, suffix: str, builder: Callable[[Path], None]) -> tuple[Path, bool]:
        """Return a cached path or run one build while identical callers wait."""
        destination = self._destination(key, suffix)
        recovered_failed_leader = False
        while True:
            cached, future, leader = self._claim_build(key, destination)
            if cached is not None:
                return cached, True
            if future is None:
                raise RuntimeError("Media cache build claim did not provide a future")
            if leader:
                return self._build_preview(key, destination, future, builder)
            try:
                return future.result(), True
            except Exception:
                if recovered_failed_leader:
                    raise
                self._discard_inflight(key, future)
                recovered_failed_leader = True

    def lookup(self, key: str, suffix: str) -> tuple[Path | None, bool]:
        """Return a completed preview and whether the same key is still building."""
        destination = self._destination(key, suffix)
        with self._lock:
            if key in self._inflight:
                return None, True
            cached = self._cached_path(key, destination)
            return cached, False

    def _destination(self, key: str, suffix: str) -> Path:
        if not key:
            raise ValueError("Cache key must not be empty")
        if not suffix.startswith("."):
            raise ValueError("Cache suffix must begin with a dot")
        return self._directory / f"{hashlib.sha256(key.encode()).hexdigest()}{suffix}"

    def _claim_build(self, key: str, destination: Path) -> tuple[Path | None, Future[Path] | None, bool]:
        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                return None, existing, False
            cached = self._cached_path(key, destination)
            if cached is not None:
                return cached, None, False
            future: Future[Path] = Future()
            self._inflight[key] = future
            self._inflight_paths[key] = destination
            return None, future, True

    def _build_preview(
        self,
        key: str,
        destination: Path,
        future: Future[Path],
        builder: Callable[[Path], None],
    ) -> tuple[Path, bool]:
        try:
            with self._operation_lock:
                builder(destination)
            _require_preview(destination)
        except BaseException as error:
            with suppress(FileNotFoundError):
                destination.unlink()
            future.set_exception(error)
            raise
        else:
            future.set_result(destination)
            self._store_completed(key, destination)
        finally:
            self._discard_inflight(key, future)
        return destination, False

    def _discard_inflight(self, key: str, future: Future[Path]) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                _ = self._inflight.pop(key)
                _ = self._inflight_paths.pop(key, None)

    def _cached_path(self, key: str, destination: Path) -> Path | None:
        cached = self._completed.get(key)
        if cached is not None and _is_valid_preview(cached):
            self._completed.move_to_end(key)
            cached.touch()
            return cached
        if cached is not None:
            _ = self._completed.pop(key, None)
        if not destination.is_file():
            return None
        if not _is_valid_preview(destination):
            destination.unlink()
            return None
        destination.touch()
        self._completed[key] = destination
        self._completed.move_to_end(key)
        return destination

    def _store_completed(self, key: str, destination: Path) -> None:
        with self._lock:
            self._completed[key] = destination
            self._completed.move_to_end(key)
            self._trim_directory_locked(protected={destination})

    def _trim_directory_locked(self, *, protected: set[Path] | None = None) -> None:
        protected_paths = set(self._inflight_paths.values())
        if protected is not None:
            protected_paths.update(protected)
        previews = self._preview_files_locked()
        protected_entries = [entry for entry in previews if entry[0] in protected_paths]
        kept_paths = {path for path, _size, _mtime in protected_entries}
        kept_count = len(protected_entries)
        kept_bytes = sum(size for _path, size, _mtime in protected_entries)
        for path, size, _mtime in previews:
            if path in kept_paths:
                continue
            if kept_count < self._maximum_entries and kept_bytes + size <= self._maximum_bytes:
                kept_paths.add(path)
                kept_count += 1
                kept_bytes += size
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
        stale_keys = [key for key, path in self._completed.items() if path not in kept_paths]
        for key in stale_keys:
            _ = self._completed.pop(key, None)

    def _preview_files_locked(self) -> list[tuple[Path, int, int]]:
        previews: list[tuple[Path, int, int]] = []
        for path in self._directory.glob("*.mp4"):
            if path.name.endswith(".part.mp4"):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if stat.st_size <= 0:
                with suppress(FileNotFoundError):
                    path.unlink()
                continue
            previews.append((path, stat.st_size, stat.st_mtime_ns))
        previews.sort(key=lambda entry: entry[2], reverse=True)
        return previews

    def _stats_locked(self) -> CacheStats:
        previews = self._preview_files_locked()
        return CacheStats(
            entry_count=len(previews),
            current_bytes=sum(size for _path, size, _mtime in previews),
            maximum_entries=self._maximum_entries,
            maximum_bytes=self._maximum_bytes,
            busy=bool(self._inflight),
        )

    def _remove_stale_partials(self) -> None:
        for pattern in ("*.part", "*.part.mp4"):
            for partial in self._directory.glob(pattern):
                try:
                    partial.unlink()
                except (FileNotFoundError, PermissionError):
                    # Another server thread or process may still own this build.
                    continue


def _require_preview(destination: Path) -> None:
    if not _is_valid_preview(destination):
        raise RuntimeError("Media preview builder did not create a non-empty destination")


def _is_valid_preview(destination: Path) -> bool:
    return destination.is_file() and destination.stat().st_size > 0


def _validate_limits(maximum_entries: int, maximum_bytes: int) -> None:
    if maximum_entries <= 0:
        raise ValueError("maximum_entries must be greater than zero")
    if maximum_bytes <= 0:
        raise ValueError("maximum_bytes must be greater than zero")
