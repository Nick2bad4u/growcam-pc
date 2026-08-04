# Changelog

All notable changes to GrowCam PC are documented here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-03

### Added

- Document first-run setup, camera-address discovery, local credential
  handling, and safe GrowCam C4 factory-reset recovery.
- Add product-specific site metadata and compatibility guidance so GrowCam C4
  owners can find and evaluate the project more easily.

### Changed

- Mark the Python package as production/stable and establish the documented
  dashboard, CLI, configuration, and media behavior as the 1.0 public contract.
- Require an explicit camera address or `GROWCAM_HOST` instead of shipping a
  developer-LAN address, and accept `GROWCAM_PORT` as a command default.
- Keep the live RTSP view available when DVRIP camera controls cannot log in,
  while clearly identifying which storage and playback features are unavailable.
- Send a best-effort DVRIP logout before closing an authenticated session to
  reduce stale camera-side sessions.

### Fixed

- Explain common DVRIP login rejection codes, including the GrowCam firmware's
  `Ret=205` account lock, instead of returning only an opaque number.
- Replace the deprecated browser unload listener with `pagehide` cleanup and
  prevent an unavailable device-info request from hiding the working live feed.

## [0.3.0] - 2026-08-03

### Added

- Add a Files dashboard that combines ordinary recordings and the reserved
  time-lapse partition for a selected date, with filtering, sorting, previews,
  and safe downloads of completed files.
- Add persistent application settings for cache byte and entry limits, Rewind
  defaults, automatic continuation, and native HEVC versus H.264 preview mode.
- Add opt-in live audio plus G.711 A-law recovery and AAC encoding for recording
  previews and downloads.
- Add browser capability detection and a native HEVC preview path that skips
  video transcoding when supported.
- Add strict branch coverage, Codecov coverage and JUnit uploads, distribution
  smoke tests, and documentation checks to the cross-platform CI matrix.
- Add a private npm toolchain for repository checks, git-cliff release notes,
  Lychee link validation, and a Docusaurus documentation site deployed through
  GitHub Pages.
- Add PyPI Trusted Publishing to tagged releases while retaining verified
  GitHub release artifacts.

### Changed

- Reorganize the dashboard into Live, Rewind, Time-lapse, Files, and Settings
  tabs with shorter labels, clearer state colors, and Nerd Font-enhanced icons
  that keep readable text fallbacks.
- Expand Daily Rewind with one-, two-, five-, and ten-minute windows, a full-day
  scrubber, automatic adjacent-window playback, and indexed cached seeking.
- Detect the camera's adaptive ordinary-recording frame rate instead of assuming
  one fixed rate, and use byte-bounded media queues during progressive demuxing.
- Use DVRIP time-range downloads for Rewind and time-lapse previews so data can
  be encoded while it is still transferring.
- Expand strict tests around CLI behavior, media commands, camera file indexes,
  active-file safeguards, and route dispatch.

### Fixed

- Avoid touching uninitialized media-cache state when a second web server
  cannot bind to an address already in use.
- Build in-progress time-lapse previews from the complete captured range instead
  of the short prefix returned by camera playback mode, preserving every
  recovered capture in the accelerated result.
- Treat abandoned progressive-media connections as normal browser cancellation
  instead of attempting a malformed JSON 502 response after video headers were
  sent.
- Bound the preview cache by both size and entry count, support safe live
  reconfiguration and clearing, and remove incomplete files after failures.

## [0.2.0] - 2026-08-02

### Changed

- Cancel abandoned camera preview pipelines immediately, finalize completed
  streams as indexed fast-start MP4, and support HTTP byte-range seeking on
  cached media.
- Pace cold native timelapse previews at 2 fps to match the camera's stored-frame
  delivery, then losslessly retime the completed cache to 25 fps.
- Stream timelapse, quick-rewind, and recording-block previews as fragmented
  MP4 so the first decoded frame can appear while transfer, transcoding, and
  persistent caching continue in the background.
- Remove browser `Blob` buffering and load media endpoints directly, with
  recent recording metadata retained briefly to avoid duplicate camera index
  queries.
- Reduce DVRIP playback completion latency after media starts and tune FFmpeg
  for low-latency HEVC decoding and H.264 encoding.
- Add 4× and 8× timelapse playback speeds.

### Fixed

- Remove abandoned generated preview partials on startup without disturbing a
  partial still owned by another running server.
- Clean up failed direct-download partials instead of blocking later retries.

## [0.1.0] - 2026-08-01

### Added

- Local browser dashboard with live camera video, snapshots, and device/storage
  status.
- Daily rewind with a 24-hour recording timeline, two- and five-minute quick
  previews, full recording blocks, gap handling, and automatic continuation.
- Native timelapse schedule inspection and guarded editing with revision
  checks, read-back verification, and rollback.
- In-progress and completed timelapse previews encoded as browser-compatible
  MP4, including persistent caching and adjustable playback speed.
- DVRIP recording listing/downloads, RTSP snapshots and clips, playable MKV
  remuxing, and strict command-line diagnostics.
- Cross-platform preview cache locations, default-browser launch, safe
  localhost binding, security headers, and explicit remote-bind consent.
- Strict Ruff, mypy, Pyright, pytest, package-build, and multi-platform GitHub
  Actions validation.

[0.1.0]: https://github.com/Nick2bad4u/growcam-pc/releases/tag/v0.1.0
[0.2.0]: https://github.com/Nick2bad4u/growcam-pc/compare/v0.1.0...v0.2.0
[0.3.0]: https://github.com/Nick2bad4u/growcam-pc/compare/v0.2.0...v0.3.0
[1.0.0]: https://github.com/Nick2bad4u/growcam-pc/compare/v0.3.0...v1.0.0
[Unreleased]: https://github.com/Nick2bad4u/growcam-pc/compare/v1.0.0...HEAD
