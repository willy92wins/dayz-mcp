from __future__ import annotations

import io
import struct
import unittest

from dayz_mcp.native_pe import NativePeError, validate_pinned_pe


def _pe(*, offset: int = 0x80, machine: int = 0x8664, magic: int = 0x20B) -> bytes:
    image = bytearray(0x200)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, offset)
    image[offset : offset + 4] = b"PE\0\0"
    struct.pack_into("<H", image, offset + 4, machine)
    struct.pack_into("<H", image, offset + 24, magic)
    return bytes(image)


class NativePeTest(unittest.TestCase):
    def test_accepts_amd64_pe32_plus_and_restores_stream_position(self) -> None:
        stream = io.BytesIO(_pe())
        stream.seek(17)
        result = validate_pinned_pe(stream)
        self.assertEqual(result.machine, 0x8664)
        self.assertEqual(result.optional_header_magic, 0x20B)
        self.assertEqual(result.pe_offset, 0x80)
        self.assertEqual(stream.tell(), 17)

    def test_rejects_invalid_headers_and_truncation(self) -> None:
        fixtures = {
            "mz": b"ZZ" + _pe()[2:],
            "offset": _pe()[:0x3C] + struct.pack("<I", 0x1F8) + _pe()[0x40:],
            "signature": _pe().replace(b"PE\0\0", b"NOPE", 1),
            "machine": _pe(machine=0x014C),
            "magic": _pe(magic=0x10B),
            "truncated": _pe()[:0x98],
        }
        for label, payload in fixtures.items():
            with self.subTest(label=label), self.assertRaises(NativePeError):
                validate_pinned_pe(io.BytesIO(payload))

    def test_uses_only_the_supplied_stream(self) -> None:
        class TrackingStream(io.BytesIO):
            def __init__(self, payload: bytes) -> None:
                super().__init__(payload)
                self.read_calls = 0

            def read(self, size: int = -1) -> bytes:
                self.read_calls += 1
                return super().read(size)

        stream = TrackingStream(_pe())
        validate_pinned_pe(stream)
        self.assertGreater(stream.read_calls, 0)


if __name__ == "__main__":
    unittest.main()
