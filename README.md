# GrowCam PC

[![CI](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml/badge.svg)](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/License-MIT-8ff5ad.svg)](LICENSE) [![Latest GitHub release.](https://flat.badgen.net/github/release/Nick2bad4u/growcam-pc?color=cyan)](https://github.com/Nick2bad4u/growcam-pc/releases) [![GitHub stars.](https://flat.badgen.net/github/stars/Nick2bad4u/growcam-pc?color=yellow)](https://github.com/Nick2bad4u/growcam-pc/stargazers) [![GitHub forks.](https://flat.badgen.net/github/forks/Nick2bad4u/growcam-pc?color=orange)](https://github.com/Nick2bad4u/growcam-pc/forks) [![GitHub open issues.](https://flat.badgen.net/github/open-issues/Nick2bad4u/growcam-pc?color=red)](https://github.com/Nick2bad4u/growcam-pc/issues) [![Codecov.](https://flat.badgen.net/codecov/github/Nick2bad4u/growcam-pc?color=blue)](https://codecov.io/gh/Nick2bad4u/growcam-pc) [![Repo Checks.](https://flat.badgen.net/github/checks/nick2bad4u/growcam-pc?color=green)](https://github.com/Nick2bad4u/growcam-pc/actions)

A private, local-first desktop dashboard for VIVOSUN GrowCam cameras. The
[documentation site](https://nick2bad4u.github.io/growcam-pc/) covers setup,
dashboard use, commands, troubleshooting, and security. GrowCam
PC talks directly to the camera's RTSP and DVRIP services—no vendor cloud or
mobile app is needed for live video, device status, microSD recordings, daily
rewind, or native time-lapse progress.

The project was developed against a VIVOSUN GrowCam C4 (`B0D8PQQWM3`). Other
XMEye/DVRIP cameras may expose similar services, but are not yet verified.

## Highlights

- Responsive live browser feed, opt-in audio, and full-resolution snapshots.
- A 24-hour daily viewer assembled from the camera's recording blocks.
- One-, two-, five-, and ten-minute or full-block rewind previews with recovered
  audio and automatic continuation through adjacent footage.
- Native time-lapse schedule, progress, reserved-partition file index, guarded
  schedule editing, and an accelerated preview of every captured frame so far.
- Persistent, bounded preview caching so repeat playback starts quickly—even
  after GrowCam PC restarts.
- JSON-backed application settings for cache limits, rewind defaults,
  continuation, and native HEVC versus compatible H.264 playback.
- A date-based Files browser for ordinary recordings and the reserved
  time-lapse partition, with filtering, preview actions, and safe downloads.
- Direct recording downloads, live clips, and command-line camera diagnostics.
- Localhost-only defaults, cross-origin write protection, and explicit consent
  before exposing the unauthenticated dashboard to a network.
- Strictly checked Python and CI coverage on Windows, macOS, and Linux.

## Quick start

You need [Python 3.11 or newer](https://www.python.org/downloads/),
[uv](https://docs.astral.sh/uv/getting-started/installation/), and
[FFmpeg](https://ffmpeg.org/download.html) on `PATH`.

Install the published package as an isolated command-line tool:

```shell
uv tool install growcam-pc
```

`pipx install growcam-pc` is an equivalent isolated installation. To test an
unreleased commit, pass the repository URL to `uv tool install` instead.

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
load. The snapshot action fetches one full-resolution JPEG frame. Audio remains
off until you click **Enable audio**; that user gesture starts a separate local
RTSP-to-MP3 stream and switching tabs stops it.

### Daily rewind

RTSP is a live stream and cannot be rewound by itself. The **Daily rewind** tab
queries the ordinary recording index on the microSD card and lays each block on
a virtual 24-hour scrubber. Select a recorded moment to create a browser MP4.

The two-minute default is deliberately fast and transfers only the requested
camera-time window through DVRIP's download-style range mode. One-, five-, and
ten-minute or full recording-block modes are available when you need different
context. Enable **Continue to the next window** to move through adjacent footage
automatically. Real gaps are shown rather than silently jumping to unrelated
video. GrowCam PC also recovers the camera's G.711 A-law recording audio and
encodes it as AAC alongside the video.

### Time-lapse studio

The camera keeps native time-lapse data on a separately reserved formatted
partition. That is why the ordinary recording partition reports less than the
microSD card's full capacity. GrowCam PC queries both indexes and labels them
separately.

The Time-lapse tab reads the firmware's `Storage.EpitomeRecord` configuration
and exposes its enabled state, interval, date range, daily capture window, and
estimated progress. Schedule writes are guarded:

- The proposed schedule is validated and shown for confirmation.
- A revision check prevents a stale browser tab from overwriting newer values.
- GrowCam PC reads the value back after writing and verifies it.
- A mismatch triggers a rollback to the previous schedule.
- Preview generation and schedule changes cannot run at the same time.

Changing an active schedule may close the current camera file and begin a new
one. Previewing is read-only and does not interrupt capture.

**Preview latest** requests the complete current time range through the camera's
download-style DVRIP range mode, removes the proprietary XM frame envelopes, and
includes every captured image. A cold preview is paced at 2 fps so playback stays
behind the camera transfer; the completed cache is losslessly retimed to 25 fps.
The result is an accelerated progress movie, not a short excerpt from the
beginning of the schedule.

### Files

The Files tab queries both camera indexes, then combines ordinary recordings and
native time-lapse files that overlap the selected date in one sortable table.
That includes a multi-day time-lapse that began earlier. Filter by name or path,
restrict the result to one media type, preview compatible entries in their
native dashboard player, or download completed camera files.
An active file remains previewable but is not offered as a download until the
camera closes it, which avoids presenting a truncated archive as complete.

### Application settings

The Settings tab persists the following choices outside the package install:

- preview cache size and entry-count limits;
- the default Rewind window and automatic-continuation choice;
- automatic native HEVC selection, forced native HEVC, or compatible H.264;
- current cache usage and a guarded **Clear cache** action.

Settings use optimistic revisions, so a stale tab cannot silently overwrite a
newer save. Their platform locations are:

| Platform | Settings file |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\settings.json` |
| macOS | `~/Library/Application Support/GrowCam/settings.json` |
| Linux | `$XDG_CONFIG_HOME/growcam/settings.json` or `~/.config/growcam/settings.json` |

## How progressive previews work

The camera wraps native HEVC and G.711 A-law audio in proprietary XM frames
rather than a ready-made browser video. GrowCam PC removes those envelopes while
the camera is transferring them, detects ordinary recording rates such as 8 or
15 fps, and returns fragmented MP4 immediately. In **Auto** mode, a browser that
advertises working HEVC-in-MP4 support receives copied native HEVC; other
browsers receive compatible H.264. Recorded audio is encoded as AAC in either
case. Leaving or replacing a preview cancels that camera operation immediately
instead of making the next selection wait behind abandoned work.

A cold time-lapse preview requests the entire captured range through the
camera's download-style range command, demultiplexes it, and encodes every stored
capture. The cold progressive stream is paced at 2 fps so playback does not
outrun the camera's measured stored-frame delivery; once transfer completes,
the indexed cache is losslessly retimed to 25 fps for accelerated replay. The
first fragments can play while transfer and caching continue. Daily Rewind's
short windows normally start fastest; full-block and long-running time-lapse
previews have more data to finish caching. The timestamp below each player
identifies the real-world period covered even when the result is accelerated or
the camera used an adaptive recording rate.

Completed previews support HTTP byte ranges and carry a complete MP4 seek index.
Replay and arbitrary seeking therefore do not require reading the file from the start.
An active file gets a new cache key when its camera-reported size changes,
ensuring a later preview
includes newly captured frames rather than stale output. Interrupted generated
partials are cleaned up safely when GrowCam PC restarts.

The vendor app has a structural advantage: its native [FunSDK media API][funsdk-media]
consumes HEVC directly and exposes remote-recording, absolute-time seek, buffer,
sound, and playback-speed controls. Xiongmai's own [open-platform overview][xmeye-open-platform]
describes FunSDK as the mobile SDK, MyEyeSDK as the Windows desktop SDK, and a
legacy WebClient/NetSDK stack for web integrations. It does not provide a modern
browser/WASM player, and its documentation does not identify a redistributable
cross-platform binary suitable for a PyPI package, so GrowCam PC does not bundle
proprietary vendor SDK files. Its cross-platform equivalent is DVRIP
download-by-time, a pure-Python XM demultiplexer, and FFmpeg. Daily Rewind opens
the selected camera time directly and gives the completed standard MP4 a seek
index. Native HEVC avoids video transcoding when the current browser genuinely
supports it; H.264 remains the portable fallback.

Preview cache locations:

| Platform | Directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\GrowCam\preview-cache` |
| macOS | `~/Library/Caches/GrowCam/preview-cache` |
| Linux | `$XDG_CACHE_HOME/growcam/preview-cache` or `~/.cache/growcam/preview-cache` |

The default cache allows 24 previews and 4 GiB, evicting least-recently-used
files when either limit is reached. Both limits are configurable in Settings.

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

Although the tested camera names ordinary recordings `.h264`, it stores HEVC in
proprietary XM framing and changes recording rate with camera conditions; 8 and
15 fps have both been observed. GrowCam PC detects that rate, recovers G.711
A-law audio, and writes playable HEVC/AAC Matroska downloads. Browser previews
use AAC audio with native HEVC when supported or H.264 otherwise.

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
npm install
npm run release:verify
```

`release:verify` runs formatting, Ruff, strict mypy, strict Pyright, branch-aware
pytest coverage, bytecode compilation, distribution validation, the Docusaurus
production build, and an offline git-cliff preview. Use `npm run docs:start` for
the local documentation server. The private `package.json` exists only to
orchestrate repository checks and documentation; it is not part of the Python
runtime package.

See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request guidance and
[CHANGELOG.md](CHANGELOG.md) for release history. GrowCam PC is available under
the [MIT License](LICENSE).

[funsdk-media]: https://github.com/xmeye/openplatform-docs/blob/7db946442bb3254402f9f20e16baba97a85e4b56/docs/en/FunSDKAndroidInterfacedescription-mediafunctionmethod.md
[xmeye-open-platform]: https://github.com/xmeye/openplatform-docs
