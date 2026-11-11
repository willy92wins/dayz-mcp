"""Algorithm regression tests for the chat_read UTF-8 sanitize (S1 of #375).

The Enforce helper ``MCPChatLog.KeepValidUtf8Bytes`` in
``addon/scripts/5_Mission/MCPChatLog.c`` must match ``keep_valid_utf8_bytes``
below bit by bit: keep only complete, valid UTF-8 sequences (1-4 bytes, no
overlong forms, no surrogates U+D800-U+DFFF, nothing above U+10FFFF) and
drop invalid bytes one at a time, retrying at the next byte. Enforce is not
executable here, so this file pins the algorithm in Python; the source
contract in ``test_chat_read.py`` pins the Enforce shape (byte walk with
``Length``/``Substring``/``ToAscii`` and the same range constants).

Run: python -m pytest tools/tests/test_chat_read_utf8_sanitize.py -q
"""
from __future__ import annotations

import unittest
from pathlib import Path

_CHAT_LOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "addon"
    / "scripts"
    / "5_Mission"
    / "MCPChatLog.c"
)


def keep_valid_utf8_bytes(data: bytes) -> tuple[bytes, bool]:
    """Reference implementation of KeepValidUtf8Bytes. Returns (cleaned, removed)."""
    cleaned = bytearray()
    removed = False
    index = 0
    total = len(data)
    while index < total:
        lead = data[index]
        if lead < 128:
            cleaned.append(lead)
            index += 1
            continue
        if 194 <= lead <= 223:
            seq_len = 2
            code_point = lead - 192
        elif 224 <= lead <= 239:
            seq_len = 3
            code_point = lead - 224
        elif 240 <= lead <= 244:
            seq_len = 4
            code_point = lead - 240
        else:
            seq_len = 0
            code_point = 0
        seq_valid = False
        if seq_len > 0 and index + seq_len <= total:
            seq_valid = True
            cont_index = 1
            while cont_index < seq_len and seq_valid:
                cont = data[index + cont_index]
                if cont < 128 or cont > 191:
                    seq_valid = False
                else:
                    code_point = code_point * 64 + (cont - 128)
                cont_index += 1
            if seq_valid and seq_len == 2 and code_point < 128:
                seq_valid = False
            if seq_valid and seq_len == 3 and code_point < 2048:
                seq_valid = False
            if seq_valid and seq_len == 3 and 55296 <= code_point <= 57343:
                seq_valid = False
            if seq_valid and seq_len == 4 and code_point < 65536:
                seq_valid = False
            if seq_valid and seq_len == 4 and code_point > 1114111:
                seq_valid = False
        if seq_valid:
            cleaned += data[index : index + seq_len]
            index += seq_len
        else:
            removed = True
            index += 1
    return bytes(cleaned), removed


def vanilla_add_split(name: str, text: str, max_length: int) -> list[tuple[bytes, bytes]]:
    """Offline model of vanilla Chat.Add byte-split (copied from the S1 repro)."""
    name_b = name.encode("utf-8")
    text_b = text.encode("utf-8")
    if len(text_b) + len(name_b) <= max_length:
        return [(name_b, text_b)]
    pieces: list[tuple[bytes, bytes]] = []
    pos = 0
    length = max(0, min(max_length - len(name_b), len(text_b)))
    sender = name_b
    while pos < len(text_b):
        pieces.append((sender, text_b[pos : pos + length]))
        sender = b""
        pos += length
        length = max(0, min(len(text_b) - pos, max_length))
    return pieces


class KeepValidUtf8BytesTest(unittest.TestCase):
    def assert_clean(self, data: bytes, expected: bytes, removed: bool) -> None:
        cleaned, was_removed = keep_valid_utf8_bytes(data)
        self.assertEqual(cleaned, expected)
        self.assertEqual(was_removed, removed)
        cleaned.decode("utf-8")  # strict: must never raise

    def test_valid_strings_are_intact(self) -> None:
        for text in (
            "",
            "hello",
            "ñ",
            "€",
            "Ж",
            "😀",
            "ñandú 25°C €😀",
            "a" * 300,
            "€" * 85,
        ):
            with self.subTest(text=text[:20]):
                self.assert_clean(text.encode("utf-8"), text.encode("utf-8"), False)

    def test_empty_and_single_invalid_bytes(self) -> None:
        self.assert_clean(b"", b"", False)
        self.assert_clean(b"\xff", b"", True)
        self.assert_clean(b"\x80", b"", True)
        self.assert_clean(b"a\xffb", b"ab", True)
        self.assert_clean(b"\x00", b"\x00", False)

    def test_orphan_continuation_at_start(self) -> None:
        self.assert_clean(b"\x82\xacabc", b"abc", True)
        self.assert_clean("€".encode("utf-8")[1:] + b"x", b"x", True)
        self.assert_clean("😀".encode("utf-8")[2:], b"", True)

    def test_cut_mid_sequence_at_end(self) -> None:
        euro = "€".encode("utf-8")
        emoji = "😀".encode("utf-8")
        self.assert_clean(b"ab" + euro[:1], b"ab", True)
        self.assert_clean(b"ab" + euro[:2], b"ab", True)
        self.assert_clean(b"ab" + emoji[:3], b"ab", True)
        self.assert_clean(b"ab" + euro, b"ab" + euro, False)

    def test_overlong_surrogate_and_out_of_range(self) -> None:
        cases = (
            b"\xc0\x80",  # overlong NUL
            b"\xc1\xbf",  # overlong
            b"\xe0\x80\x80",  # overlong 3-byte
            b"\xe0\x9f\xbf",  # overlong 3-byte, cp < 2048
            b"\xed\xa0\x80",  # U+D800 surrogate
            b"\xed\xbf\xbf",  # U+DFFF surrogate
            b"\xf0\x80\x80\x80",  # overlong 4-byte
            b"\xf0\x8f\xbf\xbf",  # overlong 4-byte, cp < 65536
            b"\xf4\x90\x80\x80",  # U+110000, above U+10FFFF
            b"\xf5\x88\x98\xa0",  # lead 0xF5 is never valid
        )
        for data in cases:
            with self.subTest(data=data.hex()):
                cleaned, removed = keep_valid_utf8_bytes(data)
                self.assertTrue(removed)
                self.assertNotEqual(cleaned, data)
                cleaned.decode("utf-8")

    def test_boundary_code_points_are_kept(self) -> None:
        for text in ("\u0080", "\u07ff", "\u0800", "\ud7ff", "\ue000", "\uffff", "\U00010000", "\U0010ffff"):
            with self.subTest(text=repr(text)):
                self.assert_clean(text.encode("utf-8"), text.encode("utf-8"), False)

    def test_invalid_byte_dropped_one_at_a_time_and_resyncs(self) -> None:
        euro = "€".encode("utf-8")
        # Lead byte followed by ASCII: drop only the lead, keep the rest.
        self.assert_clean(b"\xe2\x82a", b"a", True)
        self.assert_clean(euro[:2] + b"a" + euro, b"a" + euro, True)
        # An invalid byte between valid sequences keeps both sequences.
        self.assert_clean(euro + b"\xff" + euro, euro + euro, True)

    def test_vanilla_split_pieces_sanitize_to_strict_utf8(self) -> None:
        for name, text, max_length in (
            ("", "€" * 300, 256),
            ("ab", "€" * 300, 255),
            ("", "ñ" * 400, 255),
            ("", "😀" * 200, 255),
            ("ab", "ñandú 25°C €😀" * 80, 256),
        ):
            pieces = vanilla_add_split(name, text, max_length)
            self.assertGreater(len(pieces), 1)
            for sender, piece in pieces:
                for field in (sender, piece):
                    cleaned, removed = keep_valid_utf8_bytes(field)
                    cleaned.decode("utf-8")
                    try:
                        field.decode("utf-8")
                    except UnicodeDecodeError:
                        self.assertTrue(removed)
                        self.assertNotEqual(cleaned, field)
                    else:
                        self.assertFalse(removed)
                        self.assertEqual(cleaned, field)


class EnforceHelperPresenceTest(unittest.TestCase):
    def test_helper_and_calls_exist_in_addon_source(self) -> None:
        source = _CHAT_LOG_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "string KeepValidUtf8Bytes(string value, out bool removed)", source
        )
        self.assertEqual(source.count("KeepValidUtf8Bytes(sender, removed)"), 2)
        self.assertEqual(source.count("KeepValidUtf8Bytes(text, removed)"), 2)
        for field in ("sender", "text"):
            sanitize_at = source.index(f"{field} = KeepValidUtf8Bytes({field}, removed);")
            cap_at = source.index(f"{field}.LengthUtf8()", sanitize_at)
            self.assertLess(sanitize_at, cap_at)


if __name__ == "__main__":
    unittest.main()
