---
sidebar_position: 1
slug: /
title: GrowCam PC
description: Local PC access for VIVOSUN GrowCam cameras.
---

# GrowCam PC

GrowCam PC is a local dashboard and command-line tool for VIVOSUN GrowCam cameras. It connects directly to the camera's RTSP and DVRIP services on your LAN.

Use it to:

- Watch live video and save snapshots.
- Rewind continuous recordings across a 24-hour timeline.
- Preview native time-lapse progress and manage its schedule.
- Browse and download files from both camera storage partitions.
- Inspect device, storage, and work-state data from the command line.

No vendor cloud account is required. The dashboard listens only on `127.0.0.1` unless you explicitly allow a network bind.

:::note Tested hardware

Development and protocol testing use a VIVOSUN GrowCam C4 (`B0D8PQQWM3`). Other XMEye or DVRIP cameras may expose similar services, but they are not verified yet.

:::

## Next steps

Start with [installation and camera setup](./getting-started.md), then tour the [dashboard views](./dashboard.md).
