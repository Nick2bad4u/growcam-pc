---
sidebar_position: 5
title: Troubleshooting
description: Resolve connection, port, FFmpeg, and preview problems.
---

# Troubleshooting

## Port 8876 is already in use

Only one process can bind the default local dashboard port. Open the existing instance, stop it, or choose another port:

```shell
growcam --host 192.168.1.50 web --http-port 8877
```

## The camera cannot be reached

- Confirm the camera and computer are on the same trusted LAN.
- Confirm the address in your router or vendor app.
- Check that client isolation is not blocking TCP ports `34567` and `554`.
- Verify the camera username and password.

GrowCam PC does not discover cameras or relay traffic over the internet.

## FFmpeg is unavailable

Run both commands in the same shell that starts GrowCam PC:

```shell
ffmpeg -version
ffprobe -version
```

## A preview starts slowly

The camera stores proprietary XM-framed HEVC rather than browser-ready MP4. A cold preview must transfer and demultiplex camera packets. Browsers without native HEVC support also require H.264 encoding. Short Rewind windows transfer less data and usually start fastest; Settings can select Auto, native HEVC, or compatible H.264.

Completed previews are indexed and cached. Opening the same unchanged file again should be much faster.

## A time-lapse preview is too short or misses captures

Refresh the Time-lapse view and build **Preview latest** again. Active files receive a new cache identity as their reported size changes. A complete preview reports its recovered frame count; that count should closely match the schedule's estimated captures so far. GrowCam PC uses the camera's download-style by-time range command because playback mode can return only the beginning of a long-running time-lapse. The cold stream runs at 2 fps while the camera is delivering frames; the completed cache is retimed to 25 fps.

## A time-lapse download is unavailable

An active time-lapse file is still growing. Preview it in the Time-lapse view and download the final MKV after the camera closes the file.
