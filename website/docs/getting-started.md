---
sidebar_position: 2
title: Install and connect
description: Install GrowCam PC and connect the local dashboard to a VIVOSUN GrowCam C4.
---

# Install and connect

## Requirements

- Python 3.11 or newer.
- FFmpeg and FFprobe on `PATH`.
- A VIVOSUN GrowCam C4 reachable from the same local network.
- The camera's host address and, if configured, its credentials.

GrowCam PC supports Windows, macOS, and Linux. The Python package contains the complete dashboard; Node.js is only used to build this documentation site.

## Prepare the camera

1. Complete the GrowCam C4's initial Wi-Fi pairing in the VIVOSUN app.
2. Find the camera's IPv4 address in your router's connected-device or DHCP-client list.
3. Confirm the computer can reach the same trusted LAN. The computer may use Ethernet or Wi-Fi; it does not need to join the camera's 2.4 GHz radio directly.

GrowCam PC does not discover devices or change Wi-Fi pairing. See the [official GrowCam C4 manual](https://vivosun.com/support/guide/growcam-c4) for physical setup and pairing.

## Install

Install the isolated command with one of these package managers:

```shell title="uv"
uv tool install growcam-pc
```

```shell title="pipx"
pipx install growcam-pc
```

```shell title="pip"
python -m pip install growcam-pc
```

## Open the dashboard

Replace the example address with the camera's LAN address:

```shell
growcam --host 192.168.1.50 web
```

The server announces its local URL and opens the default browser. Use `web --no-open` to keep browser launch manual.

The camera address is required unless `GROWCAM_HOST` is set. GrowCam PC does not
guess an address or scan your network.

## Camera credentials

The tested GrowCam C4 firmware accepts the local username `admin` with a blank password by default. These are camera-local RTSP/DVRIP credentials; they are not the email address and password for your VIVOSUN account.

If you configured a camera-local password, keep it out of shell history:

```powershell title="PowerShell"
$env:GROWCAM_PASSWORD = Read-Host "Camera password" -MaskInput
growcam --host 192.168.1.50 web
```

```bash title="bash or zsh"
read -rs GROWCAM_PASSWORD && export GROWCAM_PASSWORD
growcam --host 192.168.1.50 web
```

`GROWCAM_HOST`, `GROWCAM_PORT`, `GROWCAM_USERNAME`, and `GROWCAM_PASSWORD`
provide command defaults. The tested DVRIP port is `34567`; most users do not
need to change it.

Command-line options override environment variables. In PowerShell, temporary values remain set after `Clear-Host`; remove them before returning to the defaults:

```powershell
Remove-Item Env:\GROWCAM_HOST, Env:\GROWCAM_PORT, Env:\GROWCAM_USERNAME, Env:\GROWCAM_PASSWORD -ErrorAction SilentlyContinue
```

## Verify FFmpeg

```shell
ffmpeg -version
ffprobe -version
```

The dashboard uses the platform's FFmpeg executable to turn the camera's native media into browser-compatible previews.
