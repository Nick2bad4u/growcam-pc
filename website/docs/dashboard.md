---
sidebar_position: 3
title: Dashboard
description: Use Live, Rewind, Time-lapse, and Files views.
---

# Dashboard

## Live

The Live view converts the camera's RTSP stream to browser-compatible MJPEG. The stream runs only while the tab is visible. Use **Snapshot** to save a full-resolution JPEG.

## Rewind

RTSP itself cannot rewind. The Rewind view queries continuous microSD recordings and places each block on a 24-hour timeline.

1. Choose a date.
2. Drag the day scrubber to a colored recording segment.
3. Select a two-minute, five-minute, or full-block preview.
4. Enable automatic continuation to move through adjacent footage.

Short windows normally open faster because the camera transfers less data. Completed previews are cached locally and support normal browser seeking.

## Time-lapse

The Time-lapse view reads the camera's native `Storage.EpitomeRecord` configuration. It shows schedule progress, estimated captures, the daily window, and files from the separately reserved time-lapse partition.

**Preview latest** builds an accelerated MP4 from frames captured so far. Previewing is read-only. Schedule edits require a review, revision check, read-back verification, and rollback on mismatch.

## Files

The Files view combines ordinary recordings and native time-lapses for a selected day.

- Search by date, time, generated download name, or camera path.
- Filter recordings and time-lapses.
- Sort by time or size.
- Preview a file in its native dashboard view.
- Download closed files as playable Matroska video.

Files still being written are labeled **Recording** and do not offer a final download until the camera closes them.

## Preview cache

| Platform | Directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\preview-cache` |
| macOS | `~/Library/Caches/GrowCam/preview-cache` |
| Linux | `$XDG_CACHE_HOME/growcam/preview-cache` or `~/.cache/growcam/preview-cache` |

The cache keeps the 24 most recently used MP4 previews.
