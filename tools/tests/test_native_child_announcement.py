from __future__ import annotations

import ntpath
import os
import struct
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import dayz_tools_paths
from dayz_mcp.dayz_tools_paths import (
    ADDON_BUILDER_RELATIVE,
    DEFAULT_TOOLS_ROOT,
    DIAG_NAME,
    resolved_layout,
)
from dayz_mcp.native_child_announcement import (
    ChildAnnouncementDecoder,
    ChildAnnouncementError,
)
from dayz_mcp.native_broker_protocol import BrokerKind


_HEADER = struct.Struct("<4sBBHII32sQ16s")
_FOREIGN_TOOLS = Path(r"F:\SteamLibrary\steamapps\common\DayZ Tools")


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


def _addon_exe(tools: Path) -> str:
    return str(tools.joinpath(*ADDON_BUILDER_RELATIVE))


def _other_case(path: str) -> str:
    return "".join(character.lower() if character.isupper() else character.upper() for character in path)


@contextmanager
def _sealed_layout(*, found: Path | None, environ_tools: str | None = None, registry: bool = True):
    """Make resolved_layout() see ``found`` and nothing else.

    Markers exist only for ``found``. ``environ_tools`` is DAYZ_TOOLS_PATH and is
    not given markers of its own, so a missing value falls through.
    """
    present: set[str] = set()
    if found is not None:
        present.add(ntpath.normcase(_addon_exe(found)))
        present.add(ntpath.normcase(str(found.parents[2] / "steamclient.dll")))
        present.add(ntpath.normcase(str(found.parent / "DayZ" / DIAG_NAME)))

    def registry_read(hive: str, subkey: str, value: str) -> str | None:
        if (
            registry
            and found is not None
            and (hive, subkey, value)
            == ("HKEY_CURRENT_USER", r"Software\Bohemia Interactive\DayZ Tools", "path")
        ):
            return str(found)
        return None

    environ = {key: value for key, value in os.environ.items() if key != "DAYZ_TOOLS_PATH"}
    if environ_tools is not None:
        environ["DAYZ_TOOLS_PATH"] = environ_tools
    with patch.dict(os.environ, environ, clear=True), patch.object(
        dayz_tools_paths, "read_registry_string", side_effect=registry_read
    ), patch.object(
        Path, "is_file", autospec=True, side_effect=lambda path: ntpath.normcase(str(path)) in present
    ):
        yield


def _accept_addon(path: str) -> None:
    decoded = ChildAnnouncementDecoder().feed(
        _frame(
            kind=int(BrokerKind.ADDON_BUILDER),
            path=path,
            sha=b"A" * 32,
            file_id=b"I" * 16,
        )
    )
    if len(decoded) != 1 or decoded[0].announced_path != path or decoded[0].kind is not BrokerKind.ADDON_BUILDER:
        raise AssertionError(decoded)


class ChildAnnouncementDecoderTest(unittest.TestCase):
    def test_decodes_fragmented_monotonic_announcements(self) -> None:
        addon = str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))
        decoder = ChildAnnouncementDecoder()
        first = _frame()
        second = _frame(
            sequence=2,
            kind=int(BrokerKind.ADDON_BUILDER),
            path=addon,
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


class SealedAddonBuilderAnnouncementTest(unittest.TestCase):
    """fb-20260928-124739-a2d5: the announcement is the resolved layout, not C:."""

    def test_layout_outside_c_accepts_its_path_and_refuses_the_default(self) -> None:
        resolved = _addon_exe(_FOREIGN_TOOLS)
        folded = _other_case(resolved)
        default = _addon_exe(DEFAULT_TOOLS_ROOT)
        self.assertNotEqual(folded, resolved)
        self.assertEqual(ntpath.normcase(folded), ntpath.normcase(resolved))
        self.assertNotEqual(ntpath.normcase(default), ntpath.normcase(resolved))
        with _sealed_layout(found=_FOREIGN_TOOLS):
            self.assertEqual(
                ntpath.normcase(str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))),
                ntpath.normcase(resolved),
            )
            _accept_addon(resolved)
            _accept_addon(folded)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder().feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=default, sha=b"A" * 32, file_id=b"I" * 16)
                )

    def test_dayz_tools_path_selects_the_announcement_when_the_marker_exists(self) -> None:
        resolved = _addon_exe(_FOREIGN_TOOLS)
        default = _addon_exe(DEFAULT_TOOLS_ROOT)
        with _sealed_layout(found=_FOREIGN_TOOLS, environ_tools=str(_FOREIGN_TOOLS), registry=False):
            _accept_addon(resolved)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder().feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=default, sha=b"A" * 32, file_id=b"I" * 16)
                )

    def test_default_c_layout_is_accepted_when_nothing_else_resolves(self) -> None:
        default = _addon_exe(DEFAULT_TOOLS_ROOT)
        folded = _other_case(default)
        foreign = _addon_exe(_FOREIGN_TOOLS)
        self.assertNotEqual(folded, default)
        with _sealed_layout(found=None, registry=False):
            self.assertEqual(
                str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE)),
                default,
            )
            _accept_addon(default)
            _accept_addon(folded)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder().feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=foreign, sha=b"A" * 32, file_id=b"I" * 16)
                )


if __name__ == "__main__":
    unittest.main()
