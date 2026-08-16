from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO


_PE_X64_MACHINE = 0x8664
_PE32_PLUS_MAGIC = 0x20B


class NativePEError(ValueError):
    pass


NativePeError = NativePEError


@dataclass(frozen=True, slots=True)
class NativePeInfo:
    pe_offset: int
    machine: int
    optional_header_magic: int


def _invalid_pe() -> NativePEError:
    return NativePEError("native_launcher_not_native_x64_pe")


def validate_pinned_pe(stream: BinaryIO) -> NativePeInfo:
    try:
        original_position = stream.tell()
    except (OSError, ValueError) as error:
        raise NativePEError("native_launcher_pe_unreadable") from error

    failure: NativePEError | None = None
    result: NativePeInfo | None = None
    try:
        stream.seek(0)
        dos_header = stream.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise _invalid_pe()
        pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
        if pe_offset < 64:
            raise _invalid_pe()
        stream.seek(pe_offset)
        pe_header = stream.read(26)
        machine = struct.unpack_from("<H", pe_header, 4)[0] if len(pe_header) >= 6 else -1
        magic = struct.unpack_from("<H", pe_header, 24)[0] if len(pe_header) >= 26 else -1
        if (
            len(pe_header) != 26
            or pe_header[:4] != b"PE\0\0"
            or machine != _PE_X64_MACHINE
            or magic != _PE32_PLUS_MAGIC
        ):
            raise _invalid_pe()
        result = NativePeInfo(pe_offset, machine, magic)
    except NativePEError as error:
        failure = error
    except (OSError, ValueError, struct.error) as error:
        failure = NativePEError("native_launcher_pe_unreadable")
        failure.__cause__ = error

    try:
        stream.seek(original_position)
    except (OSError, ValueError) as error:
        raise NativePEError("native_launcher_pe_unreadable") from error
    if failure is not None:
        raise failure
    if result is None:
        raise NativePEError("native_launcher_pe_unreadable")
    return result


def validate_x64_pe_stream(stream: BinaryIO) -> None:
    validate_pinned_pe(stream)
