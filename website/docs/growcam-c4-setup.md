---
sidebar_position: 2
slug: /growcam-c4-setup
title: VIVOSUN GrowCam C4 setup and compatibility
description: Connect a VIVOSUN GrowCam C4 to a Windows, macOS, or Linux PC for local live video, daily rewind, and time-lapse previews.
keywords:
 - VIVOSUN GrowCam C4 setup
 - GrowCam C4 PC viewer
 - GrowCam C4 RTSP
 - GrowCam C4 time-lapse
 - GrowCam C4 recordings
---

# VIVOSUN GrowCam C4 setup and compatibility

GrowCam PC is developed and protocol-tested with the **VIVOSUN GrowCam C4**
(model VSC-GCC4). It uses the camera's local RTSP and DVRIP services, so camera
media stays between the GrowCam C4 and your computer instead of passing through
a hosted GrowCam PC service.

## Supported GrowCam C4 features

| Feature                          | Local camera source                                  |
| -------------------------------- | ---------------------------------------------------- |
| Live video, audio, and snapshots | RTSP on TCP port `554`                               |
| Device and storage status        | DVRIP on TCP port `34567`                            |
| Full-day rewind                  | Continuous recordings on the main microSD partition  |
| Time-lapse progress preview      | Native captures on the reserved time-lapse partition |
| Camera file browser              | Combined recording and time-lapse indexes            |
| Playable downloads               | Local conversion to standard Matroska video          |

The VIVOSUN mobile app can remain installed, but it is not a relay or runtime
dependency for the PC dashboard after the camera is configured on the LAN.

## Quick connection check

1. Complete the camera's initial Wi-Fi pairing.
2. Find its IPv4 address in your router's connected-device or DHCP-client list.
3. Install Python 3.11 or newer, FFmpeg, and GrowCam PC.
4. Query the camera before opening the dashboard.

Run the installation and connection checks from a terminal:

```shell
uv tool install growcam-pc
growcam --host 192.168.1.50 info
growcam --host 192.168.1.50 web
```

The address is assigned by your network and may change. GrowCam PC deliberately
does not ship with another person's private LAN address as a default and does
not scan the network.

## Local camera credentials

The tested camera originally accepted the local username `admin` and a blank
password. This is a camera-local RTSP/DVRIP account, **not** the email address
and password used to sign in to VIVOSUN. Firmware, provisioning, or a
user-configured camera password may change those credentials.

Do not automate credential guessing. A rejected login with `Ret=205` means the
camera locked its local user. On the tested unit the lock survived a normal
power cycle and cleared only after a factory reset. Resetting can require
restoring camera settings and VIVOSUN app pairing; follow the
[official GrowCam C4 guide](https://vivosun.com/support/guide/growcam-c4) when
recovery is necessary.

## Why the recording partition is smaller than the card

The GrowCam C4 formats a separate reserved partition for native time-lapse
captures. The ordinary recording partition therefore cannot report the full
microSD capacity by itself. GrowCam PC queries both indexes: daily footage
appears in Rewind, while time-lapse files appear in Time-lapse and Files.

## Compatibility boundary

Other VIVOSUN, XMEye, or DVRIP cameras may expose similar services, but their
login, media framing, configuration names, or recording behavior may differ.
Include the camera model and sanitized firmware version when reporting
compatibility results.

Continue with [Install and connect](./getting-started.md) for credentials and
environment variables, [Dashboard](./dashboard.md) for every view, or
[Troubleshooting](./troubleshooting.md) when a connection fails.
