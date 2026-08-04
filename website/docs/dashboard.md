---
sidebar_position: 3
title: Dashboard
description: Use the GrowCam C4 Live, Rewind, Time-lapse, Files, and Settings views in GrowCam PC.
---

# Dashboard

## Live

The Live view converts the camera's RTSP stream to browser-compatible MJPEG. Choose **SD** for the tested camera's 800×448 substream or **FHD** for its 2560×1440 main stream, converted to 1920×1080 for the browser. GrowCam PC remembers the choice locally and restarts only the live-video connection when it changes. The stream runs only while the tab is visible. Use **Snapshot** to save a full-resolution JPEG. Audio is opt-in: **Enable audio** starts a separate local RTSP-to-MP3 stream after the browser receives a user gesture.

## Rewind

RTSP itself cannot rewind. The Rewind view queries continuous microSD recordings and places each block on a 24-hour timeline.

1. Choose a date.
2. Drag the day scrubber to a colored recording segment.
3. Select a one-, two-, five-, or ten-minute or full-block preview.
4. Enable automatic continuation to move through adjacent footage.

Short windows normally open faster because the camera transfers less data. GrowCam PC recovers the recording's G.711 A-law audio as AAC. Completed previews are cached locally and support normal browser seeking.

## Time-lapse

The Time-lapse view reads the camera's native `Storage.EpitomeRecord` configuration. It shows schedule progress, estimated captures, the daily window, and files from the separately reserved time-lapse partition.

**Preview latest** requests the complete current time range through the camera's download-style DVRIP range mode and removes the proprietary XM framing. A cold stream is paced at 2 fps so it does not outrun the camera's stored-frame delivery; the completed cache is retimed to 25 fps. It is an accelerated progress movie containing every recovered capture rather than a short excerpt. Previewing is read-only. Schedule edits require a review, revision check, read-back verification, and rollback on mismatch.

## Files

The Files view combines ordinary recordings and native time-lapses that overlap a selected day, including a multi-day time-lapse that began earlier.

- Search by date, time, generated download name, or camera path.
- Filter recordings and time-lapses.
- Sort by time or size.
- Preview a file in its native dashboard view.
- Download closed files as playable Matroska video.

Files still being written are labeled **Recording** and do not offer a final download until the camera closes them.

## Settings

The Settings view stores application preferences in a revisioned local JSON file. It controls:

- cache size and preview-count limits;
- the default Rewind window;
- automatic continuation;
- automatic native HEVC selection, forced HEVC, or compatible H.264;
- cache inspection and clearing.

Auto mode uses native HEVC only when the browser advertises HEVC-in-MP4 playback. H.264 is the compatible fallback. Native HEVC skips video transcoding, while recorded audio is still converted to AAC.

| Platform | Settings file |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\settings.json` |
| macOS | `~/Library/Application Support/GrowCam/settings.json` |
| Linux | `$XDG_CONFIG_HOME/growcam/settings.json` or `~/.config/growcam/settings.json` |

## Preview cache

| Platform | Directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\preview-cache` |
| macOS | `~/Library/Caches/GrowCam/preview-cache` |
| Linux | `$XDG_CACHE_HOME/growcam/preview-cache` or `~/.cache/growcam/preview-cache` |

The default cache permits 24 MP4 previews and 4 GiB. Least-recently-used files are evicted when either limit is reached, and both limits can be changed in Settings.
