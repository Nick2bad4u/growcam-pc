# Contributing to GrowCam PC

Thank you for helping improve local GrowCam access.

## Development setup

1. Install Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), FFmpeg,
   Node.js 24 or newer, and npm 12 or newer.
2. Fork and clone the repository.
3. Run `uv sync --all-groups` and `npm install` from the repository root.
4. Create a focused branch such as `fix/preview-cache`.

The camera protocol is not formally documented, so changes to DVRIP handling
should include tests built from minimal sanitized protocol samples. Never
commit camera credentials, private addresses, recordings, or device serials.

## Required checks

```shell
npm run release:verify
```

That command covers the strict Python checks, branch-aware coverage,
distribution validation, Markdown and secret scanning, changelog generation,
and the production documentation build. Run `npm run lint:lychee` separately
when the native `lychee` executable is installed; CI runs the pinned Lychee
action on every change.

Pull requests should explain the user-visible behavior, note the camera model
and firmware when protocol behavior is involved, and include before/after
screenshots for substantial UI changes.
