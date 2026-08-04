---
sidebar_position: 1
slug: /
title: VIVOSUN GrowCam C4 desktop viewer
description: Use GrowCam PC as a private local viewer, daily rewind, and time-lapse dashboard for the VIVOSUN GrowCam C4.
keywords:
  - VIVOSUN GrowCam C4
  - GrowCam C4 PC viewer
  - GrowCam time-lapse
  - GrowCam rewind
---

# VIVOSUN GrowCam C4 desktop viewer

GrowCam PC is an open-source local dashboard and command-line tool for the VIVOSUN GrowCam C4. It connects directly to the camera's RTSP and DVRIP services on your LAN after initial camera setup.

Use it to:

- Watch live video with opt-in audio and save snapshots.
- Rewind continuous recordings with audio across a 24-hour timeline.
- Preview native time-lapse progress and manage its schedule.
- Browse and download files from both camera storage partitions.
- Persist preview, cache, and playback preferences locally.
- Inspect device, storage, and work-state data from the command line.

GrowCam PC does not ask for or use your VIVOSUN account credentials. The dashboard listens only on `127.0.0.1` unless you explicitly allow a network bind.

:::note Tested hardware

Development and protocol testing use a VIVOSUN GrowCam C4 (model VSC-GCC4, product `B0D8PQQWM3`). Other XMEye or DVRIP cameras may expose similar services, but they are not verified. GrowCam PC is an independent community project and is not affiliated with or endorsed by VIVOSUN.

:::

## Next steps

Start with the [GrowCam C4 compatibility and setup guide](./growcam-c4-setup.md),
continue through [installation and connection](./getting-started.md), then tour
the [dashboard views](./dashboard.md).
