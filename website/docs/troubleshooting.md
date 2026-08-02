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

The camera stores native HEVC rather than browser-ready MP4. A cold preview must transfer camera packets and encode H.264 before the browser can display the first frame. Two-minute Rewind windows transfer less data and usually start fastest.

Completed previews are indexed and cached. Opening the same unchanged file again should be much faster.

## A time-lapse download is unavailable

An active time-lapse file is still growing. Preview it in the Time-lapse view and download the final MKV after the camera closes the file.
