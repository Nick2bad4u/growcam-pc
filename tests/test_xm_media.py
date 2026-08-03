"""Tests for Xiongmai recording stream demultiplexing."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from growcam.xm_media import XMRecordingDemuxer, demux_xm_recording

if TYPE_CHECKING:
    from pathlib import Path


def _video_frame(payload: bytes) -> bytes:
    metadata = bytes((0x13, 15, 64, 180)) + (12345).to_bytes(4, "little")
    return b"\x00\x00\x01\xfc" + metadata + len(payload).to_bytes(4, "little") + payload


def _video_continuation(payload: bytes) -> bytes:
    return b"\x00\x00\x01\xfd" + len(payload).to_bytes(4, "little") + payload


def _audio_frame(payload: bytes) -> bytes:
    return b"\x00\x00\x01\xfa" + bytes((0x0E, 2)) + len(payload).to_bytes(2, "little") + payload


def test_interleaved_stream_is_demultiplexed_across_arbitrary_chunks() -> None:
    video_payload = b"\x00\x00\x00\x01\x40\x01video"
    continuation = b"\x00\x00\x00\x01\x02\x01more"
    audio_payload = bytes(range(64))
    source = b"partial-leading-frame" + _video_frame(video_payload) + _audio_frame(audio_payload)
    source += _video_continuation(continuation) + b"truncated-tail"
    video = io.BytesIO()
    audio = io.BytesIO()
    demuxer = XMRecordingDemuxer(video, audio)

    for offset in range(0, len(source), 7):
        assert demuxer.write(source[offset : offset + 7]) == len(source[offset : offset + 7])
    stats = demuxer.finish()

    assert video.getvalue() == video_payload + continuation
    assert audio.getvalue() == audio_payload
    assert stats.source_bytes == len(source)
    assert stats.video_bytes == len(video_payload) + len(continuation)
    assert stats.audio_bytes == len(audio_payload)
    assert stats.framed is True
    assert stats.frames_per_second == 15.0


def test_pure_hevc_stream_passes_through_unchanged() -> None:
    source = b"\x00\x00\x00\x01\x40\x01" + b"raw-hevc" * 20
    video = io.BytesIO()
    demuxer = XMRecordingDemuxer(video)

    _ = demuxer.write(source[:13])
    _ = demuxer.write(source[13:])
    stats = demuxer.finish()

    assert video.getvalue() == source
    assert stats.framed is False
    assert stats.video_bytes == len(source)
    assert stats.audio_bytes == 0
    assert stats.frames_per_second is None


def test_frame_rate_consumer_receives_first_valid_video_rate_once() -> None:
    observed_rates: list[float] = []
    video = io.BytesIO()
    demuxer = XMRecordingDemuxer(video, frame_rate_consumer=observed_rates.append)

    _ = demuxer.write(_video_frame(b"first") + _video_frame(b"second"))
    stats = demuxer.finish()

    assert observed_rates == [15.0]
    assert stats.frames_per_second == 15.0


def test_orphan_continuations_are_dropped_until_the_first_keyframe() -> None:
    keyframe = b"\x00\x00\x00\x01\x40\x01keyframe"
    continuation = b"\x00\x00\x00\x01\x02\x01follow-up"
    video = io.BytesIO()
    demuxer = XMRecordingDemuxer(video)

    _ = demuxer.write(
        _video_continuation(b"orphan")
        + _video_continuation(b"still-orphaned")
        + _video_frame(keyframe)
        + _video_continuation(continuation)
    )
    stats = demuxer.finish()

    assert video.getvalue() == keyframe + continuation
    assert stats.video_bytes == len(keyframe) + len(continuation)


def test_audio_preroll_is_dropped_until_the_first_keyframe() -> None:
    video = io.BytesIO()
    audio = io.BytesIO()
    demuxer = XMRecordingDemuxer(video, audio)
    aligned_audio = b"aligned-audio"

    _ = demuxer.write(_audio_frame(b"orphan-audio") + _video_frame(b"keyframe") + _audio_frame(aligned_audio))
    stats = demuxer.finish()

    assert audio.getvalue() == aligned_audio
    assert stats.audio_bytes == len(aligned_audio)


def test_invalid_frame_length_resynchronizes_at_the_next_marker() -> None:
    invalid = b"\x00\x00\x01\xfd" + (2**32 - 1).to_bytes(4, "little")
    expected = b"\x00\x00\x00\x01\x40\x01valid"
    video = io.BytesIO()
    demuxer = XMRecordingDemuxer(video)

    _ = demuxer.write(invalid + _video_frame(expected))
    stats = demuxer.finish()

    assert video.getvalue() == expected
    assert stats.framed is True


def test_file_demux_writes_optional_audio_atomically(tmp_path: Path) -> None:
    source = tmp_path / "recording.xm"
    video_destination = tmp_path / "recording.hevc"
    audio_destination = tmp_path / "recording.alaw"
    video_payload = b"\x00\x00\x00\x01\x40\x01video"
    audio_payload = b"\xd5" * 320
    _ = source.write_bytes(_video_frame(video_payload) + _audio_frame(audio_payload))

    stats = demux_xm_recording(source, video_destination, audio_destination)

    assert stats.audio_bytes == 320
    assert video_destination.read_bytes() == video_payload
    assert audio_destination.read_bytes() == audio_payload
    assert not list(tmp_path.glob("*.part"))


def test_file_demux_omits_empty_audio_destination(tmp_path: Path) -> None:
    source = tmp_path / "recording.hevc"
    video_destination = tmp_path / "video.hevc"
    audio_destination = tmp_path / "audio.alaw"
    raw_video = b"\x00\x00\x00\x01\x40\x01video"
    _ = source.write_bytes(raw_video)

    stats = demux_xm_recording(source, video_destination, audio_destination)

    assert stats.framed is False
    assert video_destination.read_bytes() == raw_video
    assert not audio_destination.exists()
