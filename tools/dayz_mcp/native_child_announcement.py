"""Incremental, fail-closed decoder for PE child-image announcements."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from dayz_mcp.dayz_tools_paths import addon_builder_exe
from dayz_mcp.native_broker_protocol import BrokerKind
from dayz_mcp.request_path_authority import PathIdentity


_HEADER = struct.Struct("<4sBBHII32sQ16s")
_MAGIC = b"DZA1"
_VERSION = 1
_MAX_BUFFER_BYTES = 65_536
_MAX_PATH_BYTES = 2047
_PYTHON_PATH = r"runtime\python.exe"


class ChildAnnouncementError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChildAnnouncement:
    sequence: int
    kind: BrokerKind
    announced_path: str
    image_sha256: str
    identity: PathIdentity


def _invalid() -> None:
    raise ChildAnnouncementError("invalid_native_child_announcement")


class ChildAnnouncementDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._next_sequence = 1
        self._failed = False

    def feed(self, chunk: bytes) -> tuple[ChildAnnouncement, ...]:
        if self._failed or type(chunk) is not bytes:
            _invalid()
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_BUFFER_BYTES:
            self._fail()
        decoded: list[ChildAnnouncement] = []
        try:
            while len(self._buffer) >= _HEADER.size:
                (
                    magic,
                    version,
                    kind_value,
                    flags,
                    sequence,
                    path_bytes,
                    image_sha256,
                    volume_serial_number,
                    file_id,
                ) = _HEADER.unpack_from(self._buffer)
                if (
                    magic != _MAGIC
                    or version != _VERSION
                    or flags != 0
                    or sequence != self._next_sequence
                    or not 1 <= path_bytes <= _MAX_PATH_BYTES
                    or volume_serial_number == 0
                    or file_id == b"\0" * 16
                ):
                    _invalid()
                try:
                    kind = BrokerKind(kind_value)
                except ValueError:
                    _invalid()
                total = _HEADER.size + path_bytes
                if len(self._buffer) < total:
                    break
                path_raw = bytes(self._buffer[_HEADER.size:total])
                try:
                    path = path_raw.decode("utf-8")
                except UnicodeError:
                    _invalid()
                expected_path = (
                    addon_builder_exe()
                    if kind is BrokerKind.ADDON_BUILDER
                    else _PYTHON_PATH
                )
                if path != expected_path or "\0" in path:
                    _invalid()
                decoded.append(
                    ChildAnnouncement(
                        sequence=sequence,
                        kind=kind,
                        announced_path=path,
                        image_sha256=image_sha256.hex().upper(),
                        identity=PathIdentity(
                            volume_serial_number=volume_serial_number,
                            file_id=file_id.hex().upper(),
                        ),
                    )
                )
                del self._buffer[:total]
                self._next_sequence += 1
        except ChildAnnouncementError:
            self._failed = True
            self._buffer.clear()
            raise
        return tuple(decoded)

    def finish(self) -> None:
        if self._failed or self._buffer:
            self._fail()

    @property
    def has_pending_frame(self) -> bool:
        return bool(self._buffer)

    def _fail(self) -> None:
        self._failed = True
        self._buffer.clear()
        _invalid()
