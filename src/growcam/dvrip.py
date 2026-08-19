"""Small, tolerant DVRIP/XMEye client for camera queries and guarded writes."""

from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self, cast

from .camera_lock import CameraLockUnavailableError, CameraProcessLock

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import BinaryIO

_HEADER = struct.Struct("<BB2xIIBBHI")
_MAGIC = 0xFF
_SOFIA_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_OK = {100, 515}
_LOGOUT_MESSAGE = 1002
_LOGOUT_TIMEOUT = 1.0
_LOGIN_REJECTION_DETAILS = {
    106: "username or password is incorrect",
    203: "password is incorrect",
    204: "user is invalid",
    205: "user is locked",
    206: "user is blacklisted",
    207: "user is already logged in",
    430: "user does not exist",
}
_DOWNLOAD_CLAIM_MESSAGE = 1425
_DOWNLOAD_DATA_MESSAGE = 1426
_PLAYBACK_DATA_MESSAGE = 1422
_PLAYBACK_EOF_MESSAGE = 1423
_PLAYBACK_IDLE_TIMEOUT = 1.0
_ABSOLUTE_CAMERA_PATH_ERROR = "Camera filename must be an absolute DVRIP path"
_NOT_CONNECTED_ERROR = "Client is not connected"
JSONDict = dict[str, Any]


class DVRIPError(RuntimeError):
    """Raised when the camera rejects or corrupts a DVRIP request."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        return_code: int | None = None,
    ) -> None:
        """Retain structured rejection details for safe retry decisions."""
        super().__init__(message)
        self.operation = operation
        self.return_code = return_code


@dataclass(frozen=True)
class LoginInfo:
    """Stable device metadata returned by a successful camera login."""

    session_id: int
    channel_count: int
    device_type: str
    keepalive_interval: int


def sofia_hash(password: str) -> str:
    """Encode a password using Xiongmai's eight-character Sofia hash."""
    # DVRIP requires this legacy MD5-derived wire representation. It is not
    # used for local password storage and cannot be changed without breaking
    # authentication compatibility with the camera firmware.
    digest = hashlib.md5(password.encode("utf-8"), usedforsecurity=False).digest()
    return "".join(_SOFIA_CHARS[(digest[index * 2] + digest[index * 2 + 1]) % 62] for index in range(8))


class DVRIPClient:
    """Synchronous client for the camera's TCP/34567 control service."""

    def __init__(
        self,
        host: str,
        port: int = 34567,
        username: str = "admin",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        """Configure a client without opening a network connection."""
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._session_id = 0
        self._sequence = 0
        self._admin_token = ""
        self._process_lock: CameraProcessLock | None = None
        self.login_info: LoginInfo | None = None

    def __enter__(self) -> Self:
        """Connect and return this client for context-manager use."""
        _ = self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the camera connection when leaving a context manager."""
        self.close()

    def connect(self) -> LoginInfo:
        """Open the DVRIP connection and authenticate to the camera."""
        if self._socket is not None or self._process_lock is not None:
            raise DVRIPError("Client is already connected")
        try:
            self._process_lock = CameraProcessLock.acquire(self.host, self.port)
        except CameraLockUnavailableError as error:
            raise DVRIPError(str(error), operation="local lock") from error
        try:
            self._socket = socket.create_connection((self.host, self.port), self.timeout)
            self._socket.settimeout(self.timeout)
            response = self._request(
                1000,
                {
                    "EncryptType": "MD5",
                    "LoginType": "DVRIP-Web",
                    "PassWord": sofia_hash(self.password),
                    "UserName": self.username,
                },
                include_session=False,
            )
            self._require_ok("login", response)
            session = response.get("SessionID", 0)
            self._session_id = int(session, 16) if isinstance(session, str) else int(session)
            self._admin_token = str(response.get("AdminToken", ""))
            device_type = str(response.get("DeviceType") or response.get("DeviceType ") or "XMEye").strip()
            self.login_info = LoginInfo(
                session_id=self._session_id,
                channel_count=int(response.get("ChannelNum", 1)),
                device_type=device_type,
                keepalive_interval=int(response.get("AliveInterval", 20)),
            )
        except BaseException:
            self.close()
            raise
        return self.login_info

    def close(self) -> None:
        """Log out when authenticated, close the socket, and discard session credentials."""
        try:
            if self._socket is not None:
                try:
                    if self._session_id:
                        self._logout()
                finally:
                    try:
                        self._socket.close()
                    finally:
                        self._socket = None
                        self._session_id = 0
                        self._admin_token = ""
                        self.login_info = None
        finally:
            process_lock = self._process_lock
            self._process_lock = None
            if process_lock is not None:
                process_lock.release()

    def _logout(self) -> None:
        """Release a camera-side session without letting cleanup failures mask the caller."""
        if self._socket is None or not self._session_id:
            return
        try:
            self._socket.settimeout(min(self.timeout, _LOGOUT_TIMEOUT))
            response = self._request(_LOGOUT_MESSAGE, {"Name": ""})
            self._require_ok("logout", response, allowed={100, 202, 515})
        except (DVRIPError, OSError, TypeError, ValueError):
            pass

    def system_info(self, name: str) -> JSONDict | list[Any]:
        """Fetch one named system-information record."""
        response = self._named_request(1020, name)
        value = response.get(name)
        if isinstance(value, dict):
            return cast("JSONDict", value)
        if isinstance(value, list):
            return cast("list[Any]", cast("object", value))
        return {}

    def config_get(self, name: str) -> Any:
        """Fetch one named camera configuration record."""
        response = self._named_request(1042, name)
        return response.get(name)

    def config_set(self, name: str, value: object) -> None:
        """Replace one named camera configuration record."""
        response = self._request(1040, {"Name": name, name: value})
        self._require_ok(name, response)

    def capability_get(self, name: str = "SystemFunction") -> Any:
        """Fetch one named camera capability record."""
        response = self._named_request(1360, name)
        return response.get(name)

    def recordings(
        self,
        *,
        start: datetime,
        end: datetime,
        channel: int = 0,
        file_type: str = "h264",
        event: str = "*",
    ) -> list[JSONDict]:
        """List unique recording records in the requested time window."""
        if file_type not in {"h264", "jpg"}:
            raise ValueError("file_type must be 'h264' or 'jpg'")
        if event not in {"*", "A", "E", "H", "M", "R"}:
            raise ValueError("event must be one of '*', 'A', 'E', 'H', 'M', or 'R'")
        name = "OPFileQuery"
        results: list[JSONDict] = []
        seen: set[str] = set()
        cursor = start
        for _page in range(4096):
            response = self._request(
                1440,
                {
                    "Name": name,
                    name: {
                        "BeginTime": _camera_time(cursor),
                        "EndTime": _camera_time(end),
                        "Channel": channel,
                        "Event": event,
                        "Type": file_type,
                    },
                },
            )
            self._require_ok(name, response, allowed={100, 110, 111, 119, 515})
            page = _recording_page(response.get(name))
            added = _append_unique_recordings(page, seen, results)
            status = int(response.get("Ret", 0))
            if status in {110, 119} or not page:
                break
            last_start = page[-1].get("BeginTime")
            if not isinstance(last_start, str) or not added:
                break
            next_cursor = datetime.strptime(last_start, "%Y-%m-%d %H:%M:%S")
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        else:
            raise DVRIPError("Recording search exceeded the pagination safety limit")
        return results

    def download(
        self,
        filename: str,
        destination: Path,
    ) -> int:
        """Download one camera recording to a new local file."""
        if not filename.startswith("/"):
            raise ValueError(_ABSOLUTE_CAMERA_PATH_ERROR)
        if destination.exists():
            raise FileExistsError(f"Destination already exists: {destination}")
        partial = destination.with_name(destination.name + ".part")
        if partial.exists():
            raise FileExistsError(f"Partial destination already exists: {partial}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with partial.open("xb") as output:
                bytes_written = self.stream_download(filename, output)
        except BaseException:
            if partial.exists():
                partial.unlink()
            raise
        _ = partial.rename(destination)
        return bytes_written

    def stream_download(self, filename: str, output: BinaryIO) -> int:
        """Write one complete camera recording directly to an open binary stream."""
        if not filename.startswith("/"):
            raise ValueError(_ABSOLUTE_CAMERA_PATH_ERROR)
        start_time, end_time = _recording_times(filename)
        playback = {
            "Action": "DownloadStart",
            "Parameter": {"FileName": filename, "TransMode": "TCP"},
            "StartTime": _camera_time(start_time),
            "EndTime": _camera_time(end_time),
        }
        return self._stream_download_request(playback, output)

    def stream_download_by_time(
        self,
        *,
        start: datetime,
        end: datetime,
        output: BinaryIO,
        file_type: int = 0,
    ) -> int:
        """Download a camera-time range without throttling it to playback speed."""
        playback = self._playback_by_time_request(start, end, file_type=file_type)
        playback["Action"] = "DownloadStart"
        return self._stream_download_request(playback, output)

    def _stream_download_request(self, playback: Mapping[str, object], output: BinaryIO) -> int:
        """Run one OPPlayBack download request and stream its media payload."""
        if self._socket is None:
            raise DVRIPError(_NOT_CONNECTED_ERROR)
        body = {
            "Name": "OPPlayBack",
            "OPPlayBack": playback,
            "SessionID": f"0x{self._session_id:08X}",
        }
        if self._admin_token:
            body["AdminToken"] = self._admin_token
        data_socket = socket.create_connection((self.host, self.port), self.timeout)
        data_socket.settimeout(max(self.timeout, 30.0))
        bytes_written = 0
        keepalive: tuple[threading.Event, threading.Thread, list[Exception]] | None = None
        transfer_started = False
        try:
            self._send_packet(data_socket, 1424, body, sequence=0)
            try:
                control_body = {"Name": "OPPlayBack", "OPPlayBack": playback}
                if self._admin_token:
                    control_body["AdminToken"] = self._admin_token
                response = self._request(1420, control_body)
            except TimeoutError as error:
                raise DVRIPError("Timed out waiting for the camera to accept playback") from error
            self._require_ok("download start", response)
            transfer_started = True
            try:
                message_id, claim_payload, _end = self._receive_packet(data_socket)
            except TimeoutError as error:
                raise DVRIPError("Timed out waiting for the camera to claim the download socket") from error
            if message_id != _DOWNLOAD_CLAIM_MESSAGE:
                raise DVRIPError(f"Unexpected download claim response message {message_id}")
            claim = _decode_json(claim_payload, message_id)
            self._require_ok("download claim", claim)
            keepalive = self._start_keepalive()
            while True:
                try:
                    message_id, payload, end = self._receive_packet(data_socket)
                except TimeoutError as error:
                    raise DVRIPError("Timed out while receiving recording data") from error
                if message_id != _DOWNLOAD_DATA_MESSAGE:
                    raise DVRIPError(f"Unexpected download data message {message_id}")
                _ = output.write(payload)
                bytes_written += len(payload)
                if end:
                    break
            self._finish_keepalive(keepalive)
            keepalive = None
            return bytes_written
        finally:
            if keepalive is not None:
                self._stop_keepalive(keepalive)
            data_socket.close()
            if transfer_started:
                self._stop_media_transfer(playback, action="DownloadStop")

    def playback_snapshot(
        self,
        filename: str,
        destination: Path,
        *,
        expected_bytes: int,
        maximum_bytes: int = 1024 * 1024 * 1024,
    ) -> int:
        """Capture the currently playable portion of a recording to a new file."""
        if not filename.startswith("/"):
            raise ValueError(_ABSOLUTE_CAMERA_PATH_ERROR)
        if expected_bytes <= 0:
            raise ValueError("expected_bytes must be greater than zero")
        start_time, end_time = _recording_times(filename)
        playback = {
            "Action": "Start",
            "Parameter": {
                "Channel": 0,
                "FileName": filename,
                "PlayMode": "ByName",
                "StreamType": 0,
                "TransMode": "TCP",
                "Value": 0,
            },
            "StartTime": _camera_time(start_time),
            "EndTime": _camera_time(end_time),
        }
        return self._capture_playback(
            playback,
            destination,
            expected_bytes=expected_bytes,
            maximum_bytes=maximum_bytes,
        )

    def playback_by_time_snapshot(
        self,
        *,
        start: datetime,
        end: datetime,
        destination: Path,
        file_type: int = 0,
    ) -> int:
        """Capture playback selected by camera time and native recording type."""
        playback = self._playback_by_time_request(start, end, file_type=file_type)
        return self._capture_playback(
            playback,
            destination,
            expected_bytes=None,
            maximum_bytes=512 * 1024 * 1024,
        )

    def stream_playback_by_time(
        self,
        *,
        start: datetime,
        end: datetime,
        output: BinaryIO,
        file_type: int = 0,
    ) -> int:
        """Write camera-time playback packets directly to an open binary stream."""
        playback = self._playback_by_time_request(start, end, file_type=file_type)
        return self._stream_playback(
            playback,
            output,
            expected_bytes=None,
            maximum_bytes=512 * 1024 * 1024,
        )

    @staticmethod
    def _playback_by_time_request(start: datetime, end: datetime, *, file_type: int) -> dict[str, object]:
        if end <= start:
            raise ValueError("Playback end time must be after its start time")
        if file_type < 0:
            raise ValueError("Playback file type must not be negative")
        return {
            "Action": "Start",
            "Parameter": {
                "Channel": 0,
                "FileName": "",
                "PlayMode": "ByTime",
                "StreamType": 0,
                "TransMode": "TCP",
                "Value": file_type,
            },
            "StartTime": _camera_time(start),
            "EndTime": _camera_time(end),
            "StreamType": 0,
        }

    def _capture_playback(
        self,
        playback: Mapping[str, object],
        destination: Path,
        *,
        expected_bytes: int | None,
        maximum_bytes: int,
    ) -> int:
        if destination.exists():
            raise FileExistsError(f"Destination already exists: {destination}")
        partial = destination.with_name(destination.name + ".part")
        if partial.exists():
            raise FileExistsError(f"Partial destination already exists: {partial}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with partial.open("xb") as output:
                bytes_written = self._stream_playback(
                    playback,
                    output,
                    expected_bytes=expected_bytes,
                    maximum_bytes=maximum_bytes,
                )
        except BaseException:
            if partial.exists():
                partial.unlink()
            raise
        _ = partial.rename(destination)
        return bytes_written

    def _stream_playback(
        self,
        playback: Mapping[str, object],
        output: BinaryIO,
        *,
        expected_bytes: int | None,
        maximum_bytes: int,
    ) -> int:
        if expected_bytes is not None and expected_bytes > maximum_bytes:
            raise ValueError("Recording exceeds the playback snapshot safety limit")
        if maximum_bytes <= 0:
            raise ValueError("Playback snapshot safety limit must be greater than zero")
        if self._socket is None:
            raise DVRIPError(_NOT_CONNECTED_ERROR)

        claim_playback = {**playback, "Action": "Claim"}
        start_playback = {**playback, "Action": "Start"}
        body = {
            "Name": "OPPlayBack",
            "OPPlayBack": claim_playback,
            "SessionID": f"0x{self._session_id:08X}",
        }
        if self._admin_token:
            body["AdminToken"] = self._admin_token
        data_socket = socket.create_connection((self.host, self.port), self.timeout)
        data_socket.settimeout(max(self.timeout, 30.0))
        bytes_written = 0
        keepalive: tuple[threading.Event, threading.Thread, list[Exception]] | None = None
        transfer_started = False
        try:
            self._send_packet(data_socket, 1424, body, sequence=0)
            control_body = {"Name": "OPPlayBack", "OPPlayBack": start_playback}
            if self._admin_token:
                control_body["AdminToken"] = self._admin_token
            try:
                response = self._request(1420, control_body)
            except DVRIPError as error:
                raise DVRIPError(f"Camera rejected the playback control request: {error}") from error
            self._require_ok("playback start", response)
            transfer_started = True
            try:
                message_id, claim_payload, _end = self._receive_packet(data_socket)
            except DVRIPError as error:
                raise DVRIPError(f"Camera closed before claiming the playback stream: {error}") from error
            if message_id != _DOWNLOAD_CLAIM_MESSAGE:
                raise DVRIPError(f"Unexpected playback claim response message {message_id}")
            self._require_ok("playback claim", _decode_json(claim_payload, message_id))
            keepalive = self._start_keepalive()
            bytes_written = self._receive_playback_data(
                data_socket,
                output,
                expected_bytes=expected_bytes,
                maximum_bytes=maximum_bytes,
            )
            if bytes_written == 0:
                raise DVRIPError("Camera returned no playback data")
            self._finish_keepalive(keepalive)
            keepalive = None
            return bytes_written
        finally:
            if keepalive is not None:
                self._stop_keepalive(keepalive)
            data_socket.close()
            if transfer_started:
                self._stop_media_transfer(playback, action="Stop")

    def _receive_playback_data(
        self,
        data_socket: socket.socket,
        output: BinaryIO,
        *,
        expected_bytes: int | None,
        maximum_bytes: int,
    ) -> int:
        """Copy playback packets until EOF, the requested size, or an established-stream timeout."""
        bytes_written = 0
        while expected_bytes is None or bytes_written < expected_bytes:
            packet = self._next_playback_packet(data_socket, bytes_written=bytes_written)
            if packet is None:
                break
            _ = output.write(packet)
            bytes_written += len(packet)
            if packet and bytes_written == len(packet):
                # Some GrowCam firmware never emits playback EOF. Keep the
                # generous timeout for setup and the first media packet, then
                # finish promptly once an established stream goes idle.
                data_socket.settimeout(_PLAYBACK_IDLE_TIMEOUT)
            if bytes_written > maximum_bytes:
                raise DVRIPError("Playback exceeded the snapshot safety limit")
        return bytes_written

    def _next_playback_packet(self, data_socket: socket.socket, *, bytes_written: int) -> bytes | None:
        """Return one media packet, or ``None`` for a normal end of an established stream."""
        try:
            message_id, payload, _fragment_end = self._receive_packet(data_socket)
        except TimeoutError as error:
            if bytes_written:
                return None
            raise DVRIPError("Timed out while receiving playback data") from error
        except DVRIPError as error:
            if bytes_written and str(error) == "Camera closed the DVRIP connection":
                return None
            raise DVRIPError(f"Camera closed before returning playback media: {error}") from error
        if message_id == _PLAYBACK_EOF_MESSAGE:
            return None
        if message_id != _PLAYBACK_DATA_MESSAGE:
            raise DVRIPError(f"Unexpected playback data message {message_id}")
        return payload

    def _stop_media_transfer(self, playback: Mapping[str, object], *, action: str) -> None:
        """Best-effort release of firmware playback state without masking the caller's result."""
        stopped_playback = {**playback, "Action": action}
        body = {"Name": "OPPlayBack", "OPPlayBack": stopped_playback}
        if self._admin_token:
            body["AdminToken"] = self._admin_token
        try:
            response = self._request(1420, body)
            self._require_ok(f"playback {action.casefold()}", response)
        except (DVRIPError, OSError):
            # A closed or rebooting camera cannot retain useful playback state,
            # and cleanup must never replace the original transfer result.
            return

    def _start_keepalive(self) -> tuple[threading.Event, threading.Thread, list[Exception]]:
        """Keep a long media transfer's authenticated control session alive."""
        interval = self._keepalive_interval()
        stop = threading.Event()
        failures: list[Exception] = []

        def send_keepalives() -> None:
            while not stop.wait(interval):
                try:
                    self.keepalive()
                except (OSError, RuntimeError) as error:
                    failures.append(error)
                    return

        thread = threading.Thread(
            target=send_keepalives,
            name="growcam-dvrip-keepalive",
            daemon=True,
        )
        thread.start()
        return stop, thread, failures

    def keepalive(self) -> None:
        """Refresh the authenticated control session without opening another login."""
        response = self._request(1006, {"Name": "KeepAlive"})
        self._require_ok("keepalive", response)

    def _keepalive_interval(self) -> float:
        """Choose a heartbeat interval comfortably below the camera timeout."""
        interval = 5.0
        if self.login_info is not None:
            interval = max(1.0, min(10.0, self.login_info.keepalive_interval / 2))
        return interval

    def _finish_keepalive(
        self,
        keepalive: tuple[threading.Event, threading.Thread, list[Exception]],
    ) -> None:
        """Stop a media heartbeat and surface a failed control session."""
        self._stop_keepalive(keepalive)
        if keepalive[1].is_alive():
            raise DVRIPError("Camera keepalive thread did not stop after the media transfer")
        failures = keepalive[2]
        if failures:
            raise DVRIPError(f"Camera keepalive failed during media transfer: {failures[0]}") from failures[0]

    def _stop_keepalive(
        self,
        keepalive: tuple[threading.Event, threading.Thread, list[Exception]],
    ) -> None:
        """Stop a media heartbeat without masking an existing transfer error."""
        stop, thread, _failures = keepalive
        stop.set()
        thread.join(timeout=self.timeout + 1.0)

    def _named_request(self, message_id: int, name: str) -> JSONDict:
        response = self._request(message_id, {"Name": name})
        self._require_ok(name, response)
        return response

    def _request(
        self,
        message_id: int,
        body: JSONDict,
        *,
        include_session: bool = True,
    ) -> JSONDict:
        if self._socket is None:
            raise DVRIPError(_NOT_CONNECTED_ERROR)
        if include_session:
            body = {**body, "SessionID": f"0x{self._session_id:08X}"}
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\x0a\x00"
        header = _HEADER.pack(
            _MAGIC,
            0,
            self._session_id,
            self._sequence,
            0,
            0,
            message_id,
            len(payload),
        )
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        self._socket.sendall(header + payload)
        return self._receive()

    def _receive(self) -> JSONDict:
        if self._socket is None:
            raise DVRIPError(_NOT_CONNECTED_ERROR)
        message_id, raw, _end = self._receive_packet(self._socket)
        return _decode_json(raw, message_id)

    def _send_packet(
        self,
        connection: socket.socket,
        message_id: int,
        body: JSONDict,
        *,
        sequence: int,
    ) -> None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = _HEADER.pack(
            _MAGIC,
            1,
            self._session_id,
            sequence,
            0,
            0,
            message_id,
            len(payload),
        )
        connection.sendall(header + payload)

    @staticmethod
    def _receive_packet(connection: socket.socket) -> tuple[int, bytes, int]:
        header = _receive_exact(connection, _HEADER.size)
        (
            magic,
            _response,
            _session,
            _sequence,
            _fragment_or_channel,
            end,
            message_id,
            length,
        ) = _HEADER.unpack(header)
        if magic != _MAGIC:
            raise DVRIPError(f"Invalid DVRIP magic byte {magic:#x}")
        return message_id, _receive_exact(connection, length), end

    @staticmethod
    def _require_ok(
        operation: str,
        response: JSONDict,
        *,
        allowed: set[int] = _OK,
    ) -> None:
        code = int(response.get("Ret", 0))
        if code not in allowed:
            detail = _LOGIN_REJECTION_DETAILS.get(code)
            suffix = f": {detail}" if operation == "login" and detail is not None else ""
            raise DVRIPError(
                f"{operation} rejected by camera (Ret={code}){suffix}",
                operation=operation,
                return_code=code,
            )


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise DVRIPError("Camera closed the DVRIP connection")
        chunks.extend(chunk)
    return bytes(chunks)


def _camera_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _recording_times(filename: str) -> tuple[datetime, datetime]:
    match = re.search(
        r"/(?P<date>\d{4}-\d{2}-\d{2})/\d+/"
        r"(?P<start>\d{2}\.\d{2}\.\d{2})-(?P<end>\d{2}\.\d{2}\.\d{2})",
        filename,
    )
    if match is None:
        raise ValueError("Could not infer recording timestamps from camera filename")
    start = datetime.strptime(f"{match.group('date')} {match.group('start')}", "%Y-%m-%d %H.%M.%S")
    end = datetime.strptime(f"{match.group('date')} {match.group('end')}", "%Y-%m-%d %H.%M.%S")
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _decode_json(raw: bytes, message_id: int) -> JSONDict:
    raw = raw.rstrip(b"\x00\x0a\\")
    try:
        decoded: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError as error:
        raise DVRIPError(f"Invalid JSON in DVRIP response {message_id}") from error
    if not isinstance(decoded, dict):
        raise DVRIPError(f"Unexpected DVRIP response type for {message_id}")
    return cast("JSONDict", decoded)


def _recording_page(value: object) -> list[JSONDict]:
    if not isinstance(value, list):
        return []
    return [cast("JSONDict", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _append_unique_recordings(
    page: list[JSONDict],
    seen: set[str],
    results: list[JSONDict],
) -> int:
    added = 0
    for item in page:
        filename = str(item.get("FileName", ""))
        if not filename or filename in seen:
            continue
        seen.add(filename)
        results.append(item)
        added += 1
    return added
