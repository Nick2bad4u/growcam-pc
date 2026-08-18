"""Demultiplex Xiongmai's interleaved recording stream into standard media."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import BinaryIO

_VIDEO_FRAME = b"\x00\x00\x01\xfc"
_VIDEO_CONTINUATION = b"\x00\x00\x01\xfd"
_AUDIO_FRAME = b"\x00\x00\x01\xfa"
_AUDIO_INFO = b"\x00\x00\x01\xf9"
_JPEG_FRAME = b"\x00\x00\x01\xfe"
_MARKERS = (_VIDEO_FRAME, _VIDEO_CONTINUATION, _AUDIO_FRAME, _AUDIO_INFO, _JPEG_FRAME)
_MAXIMUM_VIDEO_FRAME_BYTES = 32 * 1024**2
_MAXIMUM_AUDIO_FRAME_BYTES = 64 * 1024
_MAXIMUM_FRAME_RATE = 60
_FORMAT_DETECTION_BYTES = 2 * 1024**2


@dataclass(frozen=True)
class XMStreamStats:
    """Byte counts recovered from one Xiongmai recording stream."""

    source_bytes: int
    video_bytes: int
    audio_bytes: int
    framed: bool
    frames_per_second: float | None


class XMRecordingDemuxer:
    """Binary writer that separates proprietary XM video and G.711A frames."""

    def __init__(
        self,
        video: BinaryIO,
        audio: BinaryIO | None = None,
        frame_rate_consumer: Callable[[float], None] | None = None,
    ) -> None:
        """Route recovered elementary streams to the supplied binary outputs."""
        self._video = video
        self._audio = audio
        self._frame_rate_consumer = frame_rate_consumer
        self._buffer = bytearray()
        self._mode = "detect"
        self._source_bytes = 0
        self._video_bytes = 0
        self._audio_bytes = 0
        self._frames_per_second: float | None = None
        self._video_started = False
        self._finished = False

    def write(self, data: bytes) -> int:
        """Consume an arbitrary source chunk and return its accepted byte count."""
        if self._finished:
            raise ValueError("Cannot write to a finished XM recording demuxer")
        self._source_bytes += len(data)
        if self._mode == "passthrough":
            self._write_video(data)
            return len(data)
        self._buffer.extend(data)
        self._process_buffer()
        return len(data)

    def flush(self) -> None:
        """Flush recovered media already emitted to both destinations."""
        self._video.flush()
        if self._audio is not None:
            self._audio.flush()

    def finish(self) -> XMStreamStats:
        """Finish the stream, preserving pure HEVC input and dropping partial XM frames."""
        if self._finished:
            raise ValueError("XM recording demuxer is already finished")
        if self._mode == "detect":
            self._mode = "passthrough"
            self._write_video(bytes(self._buffer))
        elif self._mode == "framed":
            self._process_buffer(final=True)
        self._buffer.clear()
        self.flush()
        self._finished = True
        return XMStreamStats(
            source_bytes=self._source_bytes,
            video_bytes=self._video_bytes,
            audio_bytes=self._audio_bytes,
            framed=self._mode == "framed",
            frames_per_second=self._frames_per_second,
        )

    def _process_buffer(self, *, final: bool = False) -> None:
        while self._buffer:
            located = _first_marker(self._buffer)
            if located is None:
                self._handle_unmarked_buffer(final=final)
                return
            if not self._consume_located_frame(*located, final=final):
                return

    def _consume_located_frame(self, marker_offset: int, marker: bytes, *, final: bool) -> bool:
        """Consume one complete framed payload and report whether scanning can continue."""
        if self._mode == "detect":
            self._mode = "framed"
        if marker_offset:
            del self._buffer[:marker_offset]
        frame = _frame_layout(self._buffer, marker)
        if frame is None:
            return self._discard_incomplete_frame(final=final)
        header_bytes, payload_bytes, target = frame
        frame_bytes = header_bytes + payload_bytes
        self._capture_frame_rate(marker)
        if len(self._buffer) < frame_bytes:
            return self._discard_incomplete_frame(final=final)
        payload = bytes(self._buffer[header_bytes:frame_bytes])
        del self._buffer[:frame_bytes]
        self._emit_payload(marker, target, payload)
        return True

    def _discard_incomplete_frame(self, *, final: bool) -> bool:
        if final:
            del self._buffer[:1]
        return final

    def _capture_frame_rate(self, marker: bytes) -> None:
        if marker != _VIDEO_FRAME or self._frames_per_second is not None:
            return
        frames_per_second = self._buffer[5]
        if not 1 <= frames_per_second <= _MAXIMUM_FRAME_RATE:
            return
        self._frames_per_second = float(frames_per_second)
        if self._frame_rate_consumer is not None:
            self._frame_rate_consumer(self._frames_per_second)

    def _handle_unmarked_buffer(self, *, final: bool) -> None:
        if self._mode == "detect" and len(self._buffer) >= _FORMAT_DETECTION_BYTES:
            self._mode = "passthrough"
            self._write_video(bytes(self._buffer))
            self._buffer.clear()
        elif self._mode == "framed":
            overlap = 0 if final else len(_VIDEO_FRAME) - 1
            if len(self._buffer) > overlap:
                del self._buffer[: len(self._buffer) - overlap]

    def _emit_payload(self, marker: bytes, target: str, payload: bytes) -> None:
        if target == "video":
            # ByTime playback can begin in the middle of a GOP. Continuation
            # frames reference parameter sets carried by the next 0x1FC frame,
            # so exposing them first makes FFmpeg fail with "PPS id out of
            # range" instead of producing a preview.
            if marker == _VIDEO_CONTINUATION and not self._video_started:
                return
            self._video_started = True
            self._write_video(payload)
        elif target == "audio" and self._video_started and self._audio is not None:
            _ = self._audio.write(payload)
            self._audio_bytes += len(payload)

    def _write_video(self, data: bytes) -> None:
        if not data:
            return
        _ = self._video.write(data)
        self._video_bytes += len(data)


def demux_xm_recording(source: Path, video_destination: Path, audio_destination: Path) -> XMStreamStats:
    """Atomically recover HEVC and optional G.711A files from an XM recording."""
    if not source.is_file():
        raise FileNotFoundError(f"Recording source does not exist: {source}")
    for destination in (video_destination, audio_destination):
        if destination.exists():
            raise FileExistsError(f"Recording destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
    video_partial = video_destination.with_name(video_destination.name + ".part")
    audio_partial = audio_destination.with_name(audio_destination.name + ".part")
    if video_partial.exists() or audio_partial.exists():
        raise FileExistsError("A partial recording demux destination already exists")
    try:
        with (
            source.open("rb") as input_stream,
            video_partial.open("xb") as video_stream,
            audio_partial.open("xb") as audio_stream,
        ):
            demuxer = XMRecordingDemuxer(video_stream, audio_stream)
            while chunk := input_stream.read(1024 * 1024):
                _ = demuxer.write(chunk)
            stats = demuxer.finish()
        _require_video(stats)
        _ = video_partial.rename(video_destination)
        if stats.audio_bytes > 0:
            _ = audio_partial.rename(audio_destination)
        else:
            audio_partial.unlink()
    except BaseException:
        with suppress(OSError):
            video_partial.unlink(missing_ok=True)
        with suppress(OSError):
            audio_partial.unlink(missing_ok=True)
        with suppress(OSError):
            video_destination.unlink(missing_ok=True)
        with suppress(OSError):
            audio_destination.unlink(missing_ok=True)
        raise
    else:
        return stats


def _first_marker(buffer: bytearray) -> tuple[int, bytes] | None:
    matches = [(offset, marker) for marker in _MARKERS if (offset := buffer.find(marker)) >= 0]
    return min(matches, default=None)


def _frame_layout(buffer: bytearray, marker: bytes) -> tuple[int, int, str] | None:
    if marker in (_VIDEO_FRAME, _JPEG_FRAME):
        header_bytes = 16
        length_offset = 12
        maximum_bytes = _MAXIMUM_VIDEO_FRAME_BYTES
        target = "video" if marker == _VIDEO_FRAME else "discard"
    elif marker == _VIDEO_CONTINUATION:
        header_bytes = 8
        length_offset = 4
        maximum_bytes = _MAXIMUM_VIDEO_FRAME_BYTES
        target = "video"
    else:
        header_bytes = 8
        length_offset = 6
        maximum_bytes = _MAXIMUM_AUDIO_FRAME_BYTES
        target = "audio" if marker == _AUDIO_FRAME else "discard"
    if len(buffer) < header_bytes:
        return None
    length_bytes = 2 if marker in {_AUDIO_FRAME, _AUDIO_INFO} else 4
    payload_bytes = int.from_bytes(buffer[length_offset : length_offset + length_bytes], "little")
    if not 0 < payload_bytes <= maximum_bytes:
        return 1, 0, "discard"
    return header_bytes, payload_bytes, target


def _require_video(stats: XMStreamStats) -> None:
    if stats.video_bytes <= 0:
        raise RuntimeError("XM recording did not contain a recoverable video stream")
