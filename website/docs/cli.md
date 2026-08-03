---
sidebar_position: 4
title: Command line
description: Inspect the camera and save media without the dashboard.
---

# Command line

Global connection options come before the command:

```text
growcam [--host HOST] [--port PORT] [--username USER] [--password PASSWORD] COMMAND
```

## Camera information

```shell
growcam --host 192.168.1.50 info
```

Returns login metadata plus device, storage, and work-state data as JSON.

## List recordings

```shell
growcam --host 192.168.1.50 recordings --hours 24
```

Use `--type jpg` for camera snapshots or `--channel` for another channel.

## Download a recording

Pass a `FileName` returned by the recording index:

```shell
growcam --host 192.168.1.50 download "/idea0/2026-08-02/001/12.00.00-12.10.00[R][0].h264" --output recording.mkv
```

GrowCam names ordinary recordings `.h264`, but the tested model wraps HEVC video and G.711 A-law audio in proprietary XM frames. GrowCam PC demultiplexes them and writes playable HEVC/AAC Matroska media. Use `--raw` only when you need the demultiplexed HEVC video elementary stream without audio.

## Snapshot and live clip

```shell
growcam --host 192.168.1.50 snapshot --output snapshot.jpg
growcam --host 192.168.1.50 clip --seconds 30 --output live.mkv
```

GrowCam PC refuses to overwrite an existing destination.
