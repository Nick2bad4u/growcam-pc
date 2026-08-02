# Changelog

All notable changes to GrowCam PC are documented here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-08-02

### Added

- Add a Files dashboard that combines ordinary recordings and the reserved
  time-lapse partition for a selected date, with filtering, sorting, previews,
  and safe downloads of completed files.
- Add strict branch coverage, Codecov coverage and JUnit uploads, distribution
  smoke tests, and documentation checks to the cross-platform CI matrix.
- Add a private npm toolchain for repository checks, git-cliff release notes,
  Lychee link validation, and a Docusaurus documentation site deployed through
  GitHub Pages.
- Add PyPI Trusted Publishing to tagged releases while retaining verified
  GitHub release artifacts.

### Changed

- Reorganize the dashboard into Live, Rewind, Time-lapse, and Files tabs with
  shorter labels, clearer state colors, and Nerd Font-enhanced icons that keep
  readable text fallbacks.
- Expand strict tests around CLI behavior, media commands, camera file indexes,
  active-file safeguards, and route dispatch.

### Fixed

- Avoid touching uninitialized media-cache state when a second web server
  cannot bind to an address already in use.

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
[Unreleased]: https://github.com/Nick2bad4u/growcam-pc/compare/v0.3.0...HEAD
