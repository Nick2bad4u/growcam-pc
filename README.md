# GrowCam PC — VIVOSUN GrowCam C4 desktop viewer

[![PyPI version](https://img.shields.io/pypi/v/growcam-pc?logo=pypi&logoColor=white)](https://pypi.org/project/growcam-pc/) [![CI](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml/badge.svg)](https://github.com/Nick2bad4u/growcam-pc/actions/workflows/ci.yml) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/) [![License: MIT](https://img.shields.io/badge/License-MIT-8ff5ad.svg)](LICENSE) [![Latest GitHub release.](https://flat.badgen.net/github/release/Nick2bad4u/growcam-pc?color=cyan)](https://github.com/Nick2bad4u/growcam-pc/releases) [![GitHub stars.](https://flat.badgen.net/github/stars/Nick2bad4u/growcam-pc?color=yellow)](https://github.com/Nick2bad4u/growcam-pc/stargazers) [![GitHub forks.](https://flat.badgen.net/github/forks/Nick2bad4u/growcam-pc?color=orange)](https://github.com/Nick2bad4u/growcam-pc/forks) [![GitHub open issues.](https://flat.badgen.net/github/open-issues/Nick2bad4u/growcam-pc?color=red)](https://github.com/Nick2bad4u/growcam-pc/issues) [![Codecov.](https://flat.badgen.net/codecov/github/Nick2bad4u/growcam-pc?color=blue)](https://codecov.io/gh/Nick2bad4u/growcam-pc) [![Repo Checks.](https://flat.badgen.net/github/checks/nick2bad4u/growcam-pc?color=green)](https://github.com/Nick2bad4u/growcam-pc/actions)

GrowCam PC is an open-source, local-first desktop dashboard and command-line
tool for the **VIVOSUN GrowCam C4**. It provides live video, a 24-hour daily
viewer, native time-lapse progress, and microSD downloads without routing media
through a third-party dashboard. The
[documentation site](https://nick2bad4u.github.io/growcam-pc/) covers setup,
every dashboard view, commands, troubleshooting, and security.
Start with the model-specific [VIVOSUN GrowCam C4 setup guide][growcam-c4-setup].

The project is developed and protocol-tested with the GrowCam C4 (model
VSC-GCC4, product `B0D8PQQWM3`). Other XMEye/DVRIP cameras may expose similar
services, but are not verified. GrowCam PC is an independent community project
and is not affiliated with or endorsed by VIVOSUN.

## Highlights

- Responsive SD/FHD live browser feed, opt-in audio, and full-resolution snapshots.
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
[FFmpeg and FFprobe](https://ffmpeg.org/download.html) on `PATH`, and the
camera's local IPv4 address. Find the address in your router's connected-device
or DHCP-client list; GrowCam PC deliberately does not scan the LAN.

Install the published package as an isolated command-line tool:

```shell
uv tool install growcam-pc
```

[`uv`](https://docs.astral.sh/uv/getting-started/installation/) is recommended,
but `pipx install growcam-pc` is an equivalent isolated installation and
`python -m pip install growcam-pc` works in a managed virtual environment. To
test an unreleased commit, pass the repository URL to `uv tool install` instead.

Start the dashboard with your camera's LAN address:

```shell
growcam --host 192.168.1.50 web
```

GrowCam PC opens the system's default browser after the local server is ready.
Use `web --no-open` for a terminal-only launch. On the tested firmware the local
camera-control account defaults to username `admin` with a blank password. This
is a camera-local DVRIP account—not the email address and password used to sign
in to the VIVOSUN app. If your camera has a local password, put it in an
environment variable instead of shell history:

```powershell
$env:GROWCAM_PASSWORD = Read-Host "Camera password" -MaskInput
growcam --host 192.168.1.50 web
```

```bash
read -rs GROWCAM_PASSWORD && export GROWCAM_PASSWORD
growcam --host 192.168.1.50 web
```

`GROWCAM_HOST`, `GROWCAM_PORT`, `GROWCAM_USERNAME`, and `GROWCAM_PASSWORD` can
all provide command defaults. The tested DVRIP port is `34567`; most users do
not need to change it.

Command-line values override environment variables. The camera address is
required unless `GROWCAM_HOST` is set; the public package intentionally has no
machine-specific LAN address baked in. PowerShell `$env:` values remain in that
terminal even after `Clear-Host`. Close the terminal or remove temporary test
overrides when troubleshooting:

```powershell
Remove-Item Env:\GROWCAM_HOST, Env:\GROWCAM_PORT, Env:\GROWCAM_USERNAME, Env:\GROWCAM_PASSWORD -ErrorAction SilentlyContinue
```

Do not automate username or password guessing against the camera. Its firmware
can lock local DVRIP access (`Ret=205`) even when the correct credentials are
later supplied. On the tested camera the lock survived a normal power cycle and
cleared only after a factory reset; use the vendor account-recovery path first
when preserving camera configuration matters. If reset is necessary, follow the
[official GrowCam C4 instructions](https://vivosun.com/support/guide/growcam-c4):
with the camera powered, hold its physical reset button until the factory-reset
voice prompt plays, wait for pairing mode, and add the camera to the VIVOSUN app
again.

The dashboard keeps one authenticated DVRIP session open and reuses it for
status, Rewind, Time-lapse, Files, previews, and downloads. Overlapping camera
operations are rejected locally instead of queued, and a second GrowCam process
is stopped before it opens another control socket. Camera-side login rejections
disable retry for that server run; a transient connection failure permits only
one explicit retry. The CLI likewise makes one login attempt per invocation and
never retries automatically, so do not wrap it in an unattended retry loop.

Continue with the [GrowCam C4 setup guide][growcam-c4-setup] for the complete
first-connection checklist and storage layout.

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
load. Choose **SD** for the camera's 800×448 substream or **FHD** for its
2560×1440 main stream, converted to a 1920×1080 browser feed to balance detail
and local MJPEG processing cost. The choice is remembered in the browser. Use
**Pause live** to disconnect both live streams without leaving the tab; resuming
starts video again while audio remains opt-in. The snapshot action still fetches
one full-resolution JPEG frame. Audio remains off until you click **Enable
audio**; that user gesture starts a separate local RTSP-to-MP3 stream and
switching tabs stops it.

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
separately. The dashboard estimates the fixed time-lapse allocation as one-third
of the full card from the reported two-thirds recording capacity. The firmware
does not expose time-lapse free space, so the estimate is labeled accordingly.

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

### Login is rejected with `Ret=205`

`Ret=205` means the camera has locked its local DVRIP user. Stop credential
guessing and close other native or DVRIP test clients. The tested GrowCam C4
remained locked after an ordinary power cycle; a factory reset cleared it, but
reset the camera only after accepting that its settings and vendor-app pairing
must be restored. Follow the [lockout recovery steps in the troubleshooting
guide](https://nick2bad4u.github.io/growcam-pc/docs/troubleshooting).

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
[growcam-c4-setup]: https://nick2bad4u.github.io/growcam-pc/docs/growcam-c4-setup
[xmeye-open-platform]: https://github.com/xmeye/openplatform-docs
