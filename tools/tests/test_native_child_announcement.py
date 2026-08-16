from __future__ import annotations

import struct
import unittest

from dayz_mcp.native_child_announcement import (
    ChildAnnouncementDecoder,
    ChildAnnouncementError,
)
from dayz_mcp.native_broker_protocol import BrokerKind


_HEADER = struct.Struct("<4sBBHII32sQ16s")
_ADDON = (
    r"C:\Program Files (x86)\Steam\steamapps\common\DayZ Tools"
    r"\Bin\AddonBuilder\AddonBuilder.exe"
)


def _frame(
    *,
    sequence: int = 1,
    kind: int = int(BrokerKind.PRIVATE_WORKER),
    path: str = r"runtime\python.exe",
    sha: bytes = b"S" * 32,
    volume: int = 7,
    file_id: bytes = bytes(range(16)),
) -> bytes:
    encoded = path.encode("utf-8")
    return _HEADER.pack(
        b"DZA1",
        1,
        kind,
        0,
        sequence,
        len(encoded),
        sha,
        volume,
        file_id,
    ) + encoded


class ChildAnnouncementDecoderTest(unittest.TestCase):
    def test_decodes_fragmented_monotonic_announcements(self) -> None:
        decoder = ChildAnnouncementDecoder()
        first = _frame()
        second = _frame(
            sequence=2,
            kind=int(BrokerKind.ADDON_BUILDER),
            path=_ADDON,
            sha=b"A" * 32,
            file_id=b"I" * 16,
        )
        decoded = []
        wire = first + second
        for boundary in (1, 17, 71, 73, len(wire)):
            chunk, wire = wire[:boundary], wire[boundary:]
            decoded.extend(decoder.feed(chunk))
        decoded.extend(decoder.feed(wire))
        decoder.finish()

        self.assertEqual([item.sequence for item in decoded], [1, 2])
        self.assertIs(decoded[0].kind, BrokerKind.PRIVATE_WORKER)
        self.assertEqual(decoded[0].announced_path, r"runtime\python.exe")
        self.assertEqual(decoded[0].image_sha256, (b"S" * 32).hex().upper())
        self.assertEqual(decoded[0].identity.volume_serial_number, 7)
        self.assertEqual(decoded[0].identity.file_id, bytes(range(16)).hex().upper())
        self.assertIs(decoded[1].kind, BrokerKind.ADDON_BUILDER)

    def test_rejects_gap_duplicate_zero_identity_and_open_or_malformed_frame(self) -> None:
        invalid = (
            _frame(sequence=2),
            _frame(volume=0, file_id=b"\0" * 16),
            _frame(kind=9),
            _frame(path=r"runtime\..\evil.exe"),
            _frame(path="runtime/python.exe"),
        )
        for wire in invalid:
            with self.subTest(wire=wire[:20]), self.assertRaisesRegex(
                ChildAnnouncementError,
                "invalid_native_child_announcement",
            ):
                ChildAnnouncementDecoder().feed(wire)

        decoder = ChildAnnouncementDecoder()
        decoder.feed(_frame()[:-1])
        with self.assertRaisesRegex(
            ChildAnnouncementError,
            "invalid_native_child_announcement",
        ):
            decoder.finish()

    def test_rejects_unbounded_input_before_buffer_growth(self) -> None:
        with self.assertRaisesRegex(
            ChildAnnouncementError,
            "invalid_native_child_announcement",
        ):
            ChildAnnouncementDecoder().feed(b"X" * 4096)


if __name__ == "__main__":
    unittest.main()
