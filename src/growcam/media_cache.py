"""Persistent, bounded single-flight cache for expensive camera media previews."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from concurrent.futures import Future
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class MediaCache:
    """Coalesce identical builds and retain a bounded set of completed previews."""

    def __init__(
        self,
        directory: Path,
        operation_lock: threading.Lock,
        *,
        maximum_entries: int = 24,
    ) -> None:
        """Configure cache storage and the shared camera-operation lock."""
        if maximum_entries <= 0:
            raise ValueError("maximum_entries must be greater than zero")
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._operation_lock = operation_lock
        self._maximum_entries = maximum_entries
        self._lock = threading.Lock()
        self._completed: OrderedDict[str, Path] = OrderedDict()
        self._inflight: dict[str, Future[Path]] = {}
        self._remove_stale_partials()
        self._trim_directory()

    def get_or_build(self, key: str, suffix: str, builder: Callable[[Path], None]) -> tuple[Path, bool]:
        """Return a cached path or run one build while identical callers wait."""
        if not key:
            raise ValueError("Cache key must not be empty")
        if not suffix.startswith("."):
            raise ValueError("Cache suffix must begin with a dot")
        destination = self._directory / f"{hashlib.sha256(key.encode()).hexdigest()}{suffix}"
        leader = False
        with self._lock:
            cached = self._cached_path(key, destination)
            if cached is not None:
                return cached, True
            future: Future[Path] | None = self._inflight.get(key)
            if future is None:
                created: Future[Path] = Future()
                self._inflight[key] = created
                future = created
                leader = True

        if not leader:
            return future.result(), True

        try:
            with self._operation_lock:
                builder(destination)
            _require_preview(destination)
        except BaseException as error:
            future.set_exception(error)
            if destination.exists():
                destination.unlink()
            raise
        else:
            future.set_result(destination)
            self._store_completed(key, destination)
        finally:
            with self._lock:
                _ = self._inflight.pop(key, None)
        return destination, False

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
            while len(self._completed) > self._maximum_entries:
                _old_key, old_path = self._completed.popitem(last=False)
                if old_path != destination and old_path.exists():
                    old_path.unlink()
            self._trim_directory()

    def _trim_directory(self) -> None:
        previews = sorted(
            self._directory.glob("*.mp4"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in previews[self._maximum_entries :]:
            stale.unlink()

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
