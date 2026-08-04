"""Command-line entry point for GrowCam PC access."""

from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from dataclasses import asdict
from datetime import datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import __version__
from .dvrip import DVRIPClient, DVRIPError
from .media import remux_recording, save_live_clip, snapshot
from .web import WebConfig, serve
from .xm_media import demux_xm_recording


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _ = parser.add_argument(
        "--host",
        metavar="ADDRESS",
        default=os.environ.get("GROWCAM_HOST"),
        help="camera LAN address (required unless GROWCAM_HOST is set)",
    )
    _ = parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("GROWCAM_PORT", "34567"),
        help="camera DVRIP port (env: GROWCAM_PORT; default: 34567)",
    )
    _ = parser.add_argument(
        "--username",
        default=os.environ.get("GROWCAM_USERNAME", "admin"),
        help="camera control username (env: GROWCAM_USERNAME; default: admin)",
    )
    _ = parser.add_argument(
        "--password",
        default=os.environ.get("GROWCAM_PASSWORD", ""),
        help="camera control password (prefer the GROWCAM_PASSWORD environment variable)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _ = subparsers.add_parser("info", help="show device, storage, and work-state data")
    recordings = subparsers.add_parser("recordings", help="list recordings or snapshots on the microSD card")
    _ = recordings.add_argument("--hours", type=float, default=24.0)
    _ = recordings.add_argument("--channel", type=int, default=0)
    _ = recordings.add_argument("--type", choices=("h264", "jpg"), default="h264")
    download = subparsers.add_parser("download", help="download one microSD recording by its DVRIP filename")
    _ = download.add_argument("camera_file")
    _ = download.add_argument("--output", type=Path)
    _ = download.add_argument(
        "--raw",
        action="store_true",
        help="save demultiplexed raw HEVC video instead of creating an MKV file",
    )
    snapshot = subparsers.add_parser("snapshot", help="save one live JPEG frame")
    _ = snapshot.add_argument("--output", type=Path)
    clip = subparsers.add_parser("clip", help="save a live Matroska clip")
    _ = clip.add_argument("--seconds", type=float, default=10.0)
    _ = clip.add_argument("--output", type=Path)
    web = subparsers.add_parser("web", help="run the localhost browser interface")
    _ = web.add_argument("--listen", default="127.0.0.1")
    _ = web.add_argument("--http-port", type=int, default=8876)
    _ = web.add_argument(
        "--open",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="open_browser",
        help="open the system default browser after the server is ready (default: enabled)",
    )
    _ = web.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow an unauthenticated dashboard bind beyond this computer",
    )
    return parser


def _require_camera_host(value: object) -> str:
    """Return a normalized camera address or raise an actionable CLI error."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Camera address required. Pass --host ADDRESS before the command or set GROWCAM_HOST.")
    return value.strip()


def main(argv: list[str] | None = None) -> int:
    """Run the requested GrowCam command and return a process exit code."""
    args = _parser().parse_args(argv)
    try:
        args.host = _require_camera_host(args.host)
        if args.command in {"info", "recordings", "download"}:
            output = _run_dvrip_command(args)
        elif args.command == "snapshot":
            destination = args.output or Path(f"growcam-snapshot-{datetime.now():%Y%m%d-%H%M%S}.jpg")
            if destination.exists():
                raise FileExistsError(f"Destination already exists: {destination}")
            _ = destination.parent.mkdir(parents=True, exist_ok=True)
            _ = destination.write_bytes(snapshot(args.host, args.username, args.password))
            output = {"output": str(destination)}
        elif args.command == "clip":
            destination = args.output or Path(f"growcam-live-{datetime.now():%Y%m%d-%H%M%S}.mkv")
            save_live_clip(
                args.host,
                destination,
                args.seconds,
                args.username,
                args.password,
            )
            output = {"output": str(destination), "seconds": args.seconds}
        else:
            _validate_web_bind(args.listen, allow_remote=args.allow_remote)
            url = _browser_url(args.listen, args.http_port)

            def announce_ready() -> None:
                if args.open_browser:
                    browser_timer = threading.Timer(0.5, webbrowser.open, args=(url,))
                    browser_timer.daemon = True
                    browser_timer.start()
                print(f"GrowCam browser interface: {url}")
                print("Press Ctrl+C to stop.")

            try:
                serve(
                    WebConfig(args.host, args.port, args.username, args.password),
                    args.listen,
                    args.http_port,
                    on_ready=announce_ready,
                )
            except KeyboardInterrupt:
                print("\nStopped.")
            return 0
        print(json.dumps(output, indent=2, default=str))
        return 0
    except (DVRIPError, OSError, ValueError) as error:
        print(f"growcam: {error}")
        return 1


def _run_dvrip_command(args: argparse.Namespace) -> dict[str, Any]:
    with DVRIPClient(
        args.host,
        args.port,
        args.username,
        args.password,
    ) as camera:
        if args.command == "info":
            if camera.login_info is None:
                raise DVRIPError("Camera login metadata is unavailable")
            return {
                "login": asdict(camera.login_info),
                "system": camera.system_info("SystemInfo"),
                "storage": camera.system_info("StorageInfo"),
                "work_state": camera.system_info("WorkState"),
            }
        if args.command == "recordings":
            end = datetime.now()
            return {
                "recordings": camera.recordings(
                    start=end - timedelta(hours=args.hours),
                    end=end,
                    channel=args.channel,
                    file_type=args.type,
                )
            }
        camera_name = Path(args.camera_file).name
        destination = args.output or (Path(camera_name) if args.raw else Path(camera_name).with_suffix(".mkv"))
        output_path = Path(destination)
        if output_path.exists():
            raise FileExistsError(f"Destination already exists: {output_path}")
        with TemporaryDirectory(prefix="growcam-download-") as temporary_directory:
            temporary = Path(temporary_directory)
            camera_stream = temporary / "camera.xm"
            video_stream = output_path if args.raw else temporary / "camera.hevc"
            audio_stream = temporary / "camera.alaw"
            bytes_downloaded = camera.download(args.camera_file, camera_stream)
            stats = demux_xm_recording(camera_stream, video_stream, audio_stream)
            if not args.raw:
                remux_recording(
                    video_stream,
                    output_path,
                    frames_per_second=stats.frames_per_second or 15.0,
                    audio_source=audio_stream if audio_stream.is_file() else None,
                )
        return {
            "camera_file": args.camera_file,
            "output": str(destination),
            "bytes_downloaded": bytes_downloaded,
            "format": "raw HEVC video" if args.raw else "Matroska/HEVC/AAC media",
        }


def _is_loopback(host: str) -> bool:
    """Return whether a listen address is restricted to this computer."""
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_web_bind(listen: str, *, allow_remote: bool) -> None:
    """Reject accidental exposure of the unauthenticated dashboard."""
    if not allow_remote and not _is_loopback(listen):
        raise ValueError(
            "Refusing a non-local dashboard bind without --allow-remote. The web interface has no user authentication."
        )


def _browser_url(listen: str, port: int) -> str:
    """Return a locally reachable URL for a bound dashboard address."""
    browser_host = "127.0.0.1" if listen in {"0.0.0.0", "::"} else listen  # noqa: S104
    formatted_host = f"[{browser_host}]" if ":" in browser_host else browser_host
    return f"http://{formatted_host}:{port}/"
