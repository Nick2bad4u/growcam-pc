---
sidebar_position: 5
title: Troubleshooting
description: Resolve GrowCam C4 login, connection, port, FFmpeg, and preview problems.
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

## The microSD card appears smaller than expected

The GrowCam C4 reserves a separately formatted partition for native time-lapse
data. The main recording partition therefore reports less than the card's full
capacity. The Time-lapse and Files views query that reserved index separately;
the difference is not necessarily missing storage.

## Live video works but storage and recordings do not

The live feed uses RTSP, while device details, storage, Rewind, Time-lapse, and Files use the separate DVRIP camera-control connection. A working live picture therefore does not prove that the DVRIP login succeeded.

Check the terminal that started GrowCam PC for the camera's return code. Confirm that stale `GROWCAM_USERNAME` or `GROWCAM_PASSWORD` values are not overriding the defaults. The dashboard remains usable in **Live feed only** mode and labels the DVRIP-dependent features as unavailable.

GrowCam PC reuses one DVRIP session for the entire dashboard run. Repeated clicks do not create extra logins: overlapping camera work receives a local busy response, and another GrowCam process is blocked before it can open a second control socket. Camera-side login rejections cannot be retried during that server run. A transient connection failure allows one explicit retry; if that retry also fails, restart only after checking the camera address and local credentials.

## Login fails with Ret=205 (user is locked)

Stop retrying credentials. Automated username or password guessing can lock the camera's local account, and more attempts may prolong or retrigger the condition. The tested GrowCam C4 kept this lock through a normal power cycle.

Use VIVOSUN's account or device recovery path first if preserving the current camera configuration matters. If a factory reset is acceptable:

1. Leave the camera powered and press and hold its physical reset button.
2. Release it after the camera says, “Restoring factory settings, please do not power off.”
3. Wait for pairing mode, then add the camera again in the VIVOSUN app.
4. Clear stale `GROWCAM_*` environment variables before testing the default local account again.

These steps follow the [official GrowCam C4 manual](https://vivosun.com/support/guide/growcam-c4). A factory reset requires camera pairing and configuration again; do not use it as a routine login test.

## FFmpeg is unavailable

Run both commands in the same shell that starts GrowCam PC:

```shell
ffmpeg -version
ffprobe -version
```

## A preview starts slowly

The camera stores proprietary XM-framed HEVC rather than browser-ready MP4. A cold preview must transfer and demultiplex camera packets. Browsers without native HEVC support also require H.264 encoding. Short Rewind windows transfer less data and usually start fastest; Settings can select Auto, native HEVC, or compatible H.264.

Completed previews are indexed and cached. Opening the same unchanged file again should be much faster.

For the quickest first result, choose a one- or two-minute Rewind window and keep playback mode on **Auto**. A full recording block or an in-progress multi-day time-lapse requires a longer camera transfer and more encoding before its complete seekable cache is ready.

## A time-lapse preview is too short or misses captures

Refresh the Time-lapse view and build **Preview latest** again. Active files receive a new cache identity as their reported size changes. A complete preview reports its recovered frame count; that count should closely match the schedule's estimated captures so far. GrowCam PC uses the camera's download-style by-time range command because playback mode can return only the beginning of a long-running time-lapse. The cold stream runs at 2 fps while the camera is delivering frames; the completed cache is retimed to 25 fps.

## A time-lapse download is unavailable

An active time-lapse file is still growing. Preview it in the Time-lapse view and download the final MKV after the camera closes the file.
