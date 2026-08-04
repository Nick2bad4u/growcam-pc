"""Cross-process exclusion for the camera's fragile DVRIP login service."""

from __future__ import annotations

import hashlib
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from typing import BinaryIO

_LOCK_DIRECTORY_NAME = "growcam-pc-camera-locks"


class CameraLockUnavailableError(RuntimeError):
    """Raised before networking when another GrowCam process owns the camera."""


class CameraProcessLock:
    """Hold one operating-system lock for a camera host and DVRIP port."""

    def __init__(self, handle: BinaryIO, path: Path) -> None:
        """Retain the locked handle until the DVRIP session closes."""
        self._handle: BinaryIO | None = handle
        self.path = path

    @classmethod
    def acquire(
        cls,
        camera_host: str,
        camera_port: int,
        *,
        directory: Path | None = None,
    ) -> Self:
        """Acquire a non-blocking lock without contacting the camera."""
        lock_directory = directory or Path(gettempdir()) / _LOCK_DIRECTORY_NAME
        identity = f"{camera_host.strip().casefold()}:{camera_port}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        path = lock_directory / f"camera-{digest}.lock"
        handle: BinaryIO | None = None
        try:
            lock_directory.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            _ = handle.seek(0, 2)
            if handle.tell() == 0:
                _ = handle.write(b"\0")
                handle.flush()
            _ = handle.seek(0)
            _lock_handle(handle)
        except OSError as error:
            if handle is not None:
                handle.close()
            raise CameraLockUnavailableError(
                "Another GrowCam process is already using this camera's control connection"
            ) from error
        return cls(handle, path)

    def release(self) -> None:
        """Release the operating-system lock idempotently."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        with suppress(OSError):
            _unlock_handle(handle)
        handle.close()

    def __enter__(self) -> Self:
        """Return this acquired lock."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Release the lock when leaving a context manager."""
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock_handle(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_handle(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_handle(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(handle: BinaryIO) -> None:
        _ = handle.seek(0)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
