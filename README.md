# GrowCam PC

[![CI](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml/badge.svg)](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-8ff5ad.svg)](LICENSE)

A private, local-first desktop dashboard for VIVOSUN GrowCam cameras. GrowCam
PC talks directly to the camera's RTSP and DVRIP services—no vendor cloud or
mobile app is needed for live video, device status, microSD recordings, daily
rewind, or native timelapse progress.

The project was developed against a VIVOSUN GrowCam C4 (`B0D8PQQWM3`). Other
XMEye/DVRIP cameras may expose similar services, but are not yet verified.

## Highlights

- Live 2560 × 1440 browser feed and one-click snapshots.
- A 24-hour daily viewer assembled from the camera's recording blocks.
- Two-minute, five-minute, and full-block rewind previews with automatic
  continuation through adjacent footage.
- Native timelapse schedule, progress, reserved-partition file index, guarded
  schedule editing, and an accelerated preview of every captured frame so far.
- Persistent, bounded preview caching so repeat playback starts quickly—even
  after GrowCam PC restarts.
- Direct recording downloads, live clips, and command-line camera diagnostics.
- Localhost-only defaults, cross-origin write protection, and explicit consent
  before exposing the unauthenticated dashboard to a network.
- Strictly checked Python and CI coverage on Windows, macOS, and Linux.

## Quick start

You need [Python 3.11 or newer](https://www.python.org/downloads/),
[uv](https://docs.astral.sh/uv/getting-started/installation/), and
[FFmpeg](https://ffmpeg.org/download.html) on `PATH`.

Install the latest GitHub version as an isolated command-line tool:

```shell
uv tool install git+https://github.com/Nick2bad4u/growcam-pc
```

Start the dashboard with your camera's LAN address:

```shell
growcam --host 192.168.1.50 web
```

GrowCam PC opens the system's default browser after the local server is ready.
Use `web --no-open` for a terminal-only launch. If the camera uses a password,
put it in an environment variable instead of shell history:

```powershell
$env:GROWCAM_PASSWORD = Read-Host "Camera password" -MaskInput
growcam --host 192.168.1.50 web
```

```bash
read -rs GROWCAM_PASSWORD && export GROWCAM_PASSWORD
growcam --host 192.168.1.50 web
```

`GROWCAM_HOST`, `GROWCAM_USERNAME`, and `GROWCAM_PASSWORD` can all provide
persistent command defaults. The tested camera port is `34567`.

## Install from source

```shell
git clone https://github.com/Nick2bad4u/growcam-pc.git
cd growcam-pc
uv sync --all-groups
uv run growcam --host 192.168.1.50 web
```

The packaged application is pure Python, but live and recorded media conversion
uses the platform's FFmpeg executable. The dashboard itself uses only browser
features and bundled static assets; it does not require Node.js.

## Using the dashboard

### Live

The Live tab converts the camera's RTSP stream into a browser-compatible MJPEG
feed. GrowCam PC starts this process only while the Live tab is visible and
stops it when you switch views, which avoids needless camera, CPU, and network
load. The snapshot action fetches one full-resolution JPEG frame.

### Daily rewind

RTSP is a live stream and cannot be rewound by itself. The **Daily rewind** tab
queries the ordinary recording index on the microSD card and lays each block on
a virtual 24-hour scrubber. Select a recorded moment to create a browser MP4.

The two-minute default is deliberately fast and usually transfers only the
requested playback window. Five-minute and full recording-block modes are
available when you need more context. Enable **Continue to the next window** to
move through adjacent footage automatically. Real gaps are shown rather than
silently jumping to unrelated video.

### Timelapse studio

The camera keeps native timelapse data on a separately reserved formatted
partition. That is why the ordinary recording partition reports less than the
microSD card's full capacity. GrowCam PC queries both indexes and labels them
separately.

The Timelapse tab reads the firmware's `Storage.EpitomeRecord` configuration
and exposes its enabled state, interval, date range, daily capture window, and
estimated progress. Schedule writes are guarded:

- The proposed schedule is validated and shown for confirmation.
- A revision check prevents a stale browser tab from overwriting newer values.
- GrowCam PC reads the value back after writing and verifies it.
- A mismatch triggers a rollback to the previous schedule.
- Preview generation and schedule changes cannot run at the same time.

Changing an active schedule may close the current camera file and begin a new
one. Previewing is read-only and does not interrupt capture.

## Why a first preview can take a while

The camera does not serve a ready-made browser video. For a timelapse preview,
GrowCam PC must first transfer the native HEVC frames accumulated so far over
DVRIP, then encode them as H.264 MP4. A growing 36 MiB camera file therefore
cannot begin browser playback immediately; the transfer and encode must finish
before a complete cached MP4 is returned. The displayed playback is
accelerated timelapse video, while the timestamp under the player identifies
the real-world capture period it covers.

Daily rewind has the same constraint in full-block mode. Use its two-minute
window for the shortest first start. Completed previews are cached locally, so
the exact same request is normally near-instant on replay. An active file gets
a new cache key when its camera-reported size changes, ensuring the next preview
includes newly captured frames rather than stale output.

Preview cache locations:

| Platform | Directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\preview-cache` |
| macOS | `~/Library/Caches/GrowCam/preview-cache` |
| Linux | `$XDG_CACHE_HOME/growcam/preview-cache` or `~/.cache/growcam/preview-cache` |

The cache keeps the 24 most recently used MP4 previews.

## Other commands

Show device, storage, and work-state metadata:

```shell
growcam --host 192.168.1.50 info
```

List the last 24 hours of ordinary microSD recordings:

```shell
growcam --host 192.168.1.50 recordings --hours 24
```

Download one `FileName` from that output as a playable MKV:

```shell
growcam --host 192.168.1.50 download "/idea0/2026-07-31/001/23.40.00-23.50.00[R][@17f2][0].h264" --output recording.mkv
```

Save a still or a live Matroska clip:

```shell
growcam --host 192.168.1.50 snapshot --output snapshot.jpg
growcam --host 192.168.1.50 clip --seconds 30 --output live.mkv
```

Although the tested camera names ordinary recordings `.h264`, it stores an
HEVC/H.265 elementary stream at an observed 7.5 fps. GrowCam PC remuxes that
stream for playback. Recorded audio is not currently recovered from the
camera's proprietary recording framing.

## Troubleshooting

### Port 8876 is already in use

Only one process can bind the default local dashboard port. Close the existing
GrowCam PC terminal, open the already-running dashboard, or select another
port:

```shell
growcam --host 192.168.1.50 web --http-port 8877
```

GrowCam PC reports this condition cleanly on Windows, macOS, and Linux. It does
not tear down partially initialized cache state when the bind fails.

### FFmpeg is unavailable

Confirm both commands resolve in the same shell used to start GrowCam PC:

```shell
ffmpeg -version
ffprobe -version
```

### The camera cannot be reached

Confirm the camera and computer are on the same trusted LAN, find the camera's
address in your router or vendor app, and check that TCP ports 34567 and 554 are
not blocked by client isolation. GrowCam PC does not discover devices or relay
traffic through the internet.

## Security

The tested camera accepted its factory `admin` account with an empty password
over RTSP and DVRIP. If the firmware permits it, set a strong password and keep
the camera on an isolated IoT network.

The dashboard binds to `127.0.0.1` by default and has no user-authentication
layer. A non-loopback bind is rejected unless you supply `--allow-remote`:

```shell
growcam --host 192.168.1.50 web --listen 0.0.0.0 --allow-remote
```

That option exposes camera controls and video to other devices that can reach
the computer. Use it only on a trusted network with an appropriate host
firewall. See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Development

```shell
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pyright
uv run pytest
uv run python -m compileall -q src tests
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request guidance and
[CHANGELOG.md](CHANGELOG.md) for release history. GrowCam PC is available under
the [MIT License](LICENSE).
