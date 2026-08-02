---
sidebar_position: 2
title: Getting started
description: Install GrowCam PC and open the local dashboard.
---

# Getting started

## Requirements

- Python 3.11 or newer.
- FFmpeg and FFprobe on `PATH`.
- A GrowCam camera reachable from the same local network.
- The camera's host address and, if configured, its credentials.

GrowCam PC supports Windows, macOS, and Linux. The Python package contains the complete dashboard; Node.js is only used to build this documentation site.

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

## Keep credentials out of shell history

```powershell title="PowerShell"
$env:GROWCAM_PASSWORD = Read-Host "Camera password" -MaskInput
growcam --host 192.168.1.50 web
```

```bash title="bash or zsh"
read -rs GROWCAM_PASSWORD && export GROWCAM_PASSWORD
growcam --host 192.168.1.50 web
```

`GROWCAM_HOST`, `GROWCAM_USERNAME`, and `GROWCAM_PASSWORD` provide command defaults. The tested DVRIP port is `34567`.

## Verify FFmpeg

```shell
ffmpeg -version
ffprobe -version
```

The dashboard uses the platform's FFmpeg executable to turn the camera's native media into browser-compatible previews.
