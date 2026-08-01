# Contributing to GrowCam PC

Thank you for helping improve local GrowCam access.

## Development setup

1. Install Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and FFmpeg.
2. Fork and clone the repository.
3. Run `uv sync --all-groups` from the repository root.
4. Create a focused branch such as `fix/preview-cache`.

The camera protocol is not formally documented, so changes to DVRIP handling
should include tests built from minimal sanitized protocol samples. Never
commit camera credentials, private addresses, recordings, or device serials.

## Required checks

```shell
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pyright
uv run pytest
uv run python -m compileall -q src tests
uv build
```

Pull requests should explain the user-visible behavior, note the camera model
and firmware when protocol behavior is involved, and include before/after
screenshots for substantial UI changes.
