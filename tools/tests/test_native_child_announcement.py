from __future__ import annotations

import importlib
import ntpath
import os
import struct
import unittest
from types import SimpleNamespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import dayz_tools_paths, native_bundle
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


def _accept_addon(path: str, sealed: str) -> None:
    decoded = ChildAnnouncementDecoder(addon_builder_path=sealed).feed(
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
        decoder = ChildAnnouncementDecoder(addon_builder_path=addon)
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
            sealed = str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))
            self.assertEqual(ntpath.normcase(sealed), ntpath.normcase(resolved))
            _accept_addon(resolved, sealed)
            _accept_addon(folded, sealed)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder(addon_builder_path=sealed).feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=default, sha=b"A" * 32, file_id=b"I" * 16)
                )

    def test_dayz_tools_path_selects_the_announcement_when_the_marker_exists(self) -> None:
        resolved = _addon_exe(_FOREIGN_TOOLS)
        default = _addon_exe(DEFAULT_TOOLS_ROOT)
        with _sealed_layout(found=_FOREIGN_TOOLS, environ_tools=str(_FOREIGN_TOOLS), registry=False):
            sealed = str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))
            _accept_addon(resolved, sealed)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder(addon_builder_path=sealed).feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=default, sha=b"A" * 32, file_id=b"I" * 16)
                )

    def test_default_c_layout_is_accepted_when_nothing_else_resolves(self) -> None:
        default = _addon_exe(DEFAULT_TOOLS_ROOT)
        folded = _other_case(default)
        foreign = _addon_exe(_FOREIGN_TOOLS)
        self.assertNotEqual(folded, default)
        with _sealed_layout(found=None, registry=False):
            sealed = str(resolved_layout().tools.joinpath(*ADDON_BUILDER_RELATIVE))
            self.assertEqual(sealed, default)
            _accept_addon(default, sealed)
            _accept_addon(folded, sealed)
            with self.assertRaisesRegex(ChildAnnouncementError, "invalid_native_child_announcement"):
                ChildAnnouncementDecoder(addon_builder_path=sealed).feed(
                    _frame(kind=int(BrokerKind.ADDON_BUILDER), path=foreign, sha=b"A" * 32, file_id=b"I" * 16)
                )


def _switchable_installs(registry_root: Path, environ_tools: str | None):
    """Both installs have markers. Registry and DAYZ_TOOLS_PATH move independently."""
    roots = (
        Path(r"F:\SteamA\steamapps\common\DayZ Tools"),
        Path(r"G:\SteamB\steamapps\common\DayZ Tools"),
    )
    present: set[str] = set()
    for root in roots:
        present.add(ntpath.normcase(_addon_exe(root)))
        present.add(ntpath.normcase(str(root.parents[2] / "steamclient.dll")))
        present.add(ntpath.normcase(str(root.parent / "DayZ" / DIAG_NAME)))

    def registry_read(hive: str, subkey: str, value: str) -> str | None:
        if (hive, subkey, value) == (
            "HKEY_CURRENT_USER",
            r"Software\Bohemia Interactive\DayZ Tools",
            "path",
        ):
            return str(registry_root)
        return None

    environ = {key: value for key, value in os.environ.items() if key != "DAYZ_TOOLS_PATH"}
    if environ_tools is not None:
        environ["DAYZ_TOOLS_PATH"] = environ_tools
    return (
        patch.dict(os.environ, environ, clear=True),
        patch.object(dayz_tools_paths, "read_registry_string", side_effect=registry_read),
        patch.object(
            Path,
            "is_file",
            autospec=True,
            side_effect=lambda path: ntpath.normcase(str(path)) in present,
        ),
    )


class SealedPathCapturedOnceTest(unittest.TestCase):
    """The decoder keeps the AddonBuilder path captured at load (fb-a2d5 F1)."""

    def test_captured_path_survives_a_registry_or_env_change(self) -> None:
        install_a = Path(r"F:\SteamA\steamapps\common\DayZ Tools")
        install_b = Path(r"G:\SteamB\steamapps\common\DayZ Tools")
        sealed = _addon_exe(install_a)
        other = _addon_exe(install_b)
        folded = _other_case(sealed)
        self.assertEqual(ntpath.normcase(folded), ntpath.normcase(sealed))
        self.assertNotEqual(ntpath.normcase(sealed), ntpath.normcase(other))
        env_patch, registry_patch, file_patch = _switchable_installs(install_a, None)
        with env_patch, registry_patch, file_patch:
            captured = native_bundle._addon_builder_path()
        self.assertEqual(captured, sealed)

        for label, registry_root, environ_tools in (
            ("registry", install_b, None),
            ("env", install_a, str(install_b)),
        ):
            with self.subTest(change=label):
                env_patch, registry_patch, file_patch = _switchable_installs(
                    registry_root, environ_tools
                )
                with env_patch, registry_patch, file_patch:
                    live = native_bundle._addon_builder_path()
                    self.assertEqual(ntpath.normcase(live), ntpath.normcase(other))
                    decoder = ChildAnnouncementDecoder(addon_builder_path=captured)
                    accepted = decoder.feed(
                        _frame(
                            kind=int(BrokerKind.ADDON_BUILDER),
                            path=sealed,
                            sha=b"A" * 32,
                            file_id=b"I" * 16,
                        )
                    )
                    self.assertEqual(accepted[0].announced_path, sealed)
                    case_only = decoder.feed(
                        _frame(
                            sequence=2,
                            kind=int(BrokerKind.ADDON_BUILDER),
                            path=folded,
                            sha=b"A" * 32,
                            file_id=b"I" * 16,
                        )
                    )
                    self.assertEqual(case_only[0].announced_path, folded)
                    with self.assertRaisesRegex(
                        ChildAnnouncementError,
                        "invalid_native_child_announcement",
                    ):
                        decoder.feed(
                            _frame(
                                sequence=3,
                                kind=int(BrokerKind.ADDON_BUILDER),
                                path=other,
                                sha=b"A" * 32,
                                file_id=b"I" * 16,
                            )
                        )

    def test_no_captured_path_refuses_addon_builder_and_still_accepts_python(self) -> None:
        sealed = _addon_exe(_FOREIGN_TOOLS)
        with _sealed_layout(found=_FOREIGN_TOOLS):
            self.assertEqual(
                ntpath.normcase(native_bundle._addon_builder_path()),
                ntpath.normcase(sealed),
            )
            with self.assertRaisesRegex(
                ChildAnnouncementError,
                "invalid_native_child_announcement",
            ):
                ChildAnnouncementDecoder().feed(
                    _frame(
                        kind=int(BrokerKind.ADDON_BUILDER),
                        path=sealed,
                        sha=b"A" * 32,
                        file_id=b"I" * 16,
                    )
                )
            for captured in (None, "", "F:\\SteamA\0AddonBuilder.exe"):
                with self.subTest(captured=captured):
                    with self.assertRaisesRegex(
                        ChildAnnouncementError,
                        "invalid_native_child_announcement",
                    ):
                        ChildAnnouncementDecoder(addon_builder_path=captured).feed(
                            _frame(
                                kind=int(BrokerKind.ADDON_BUILDER),
                                path=sealed,
                                sha=b"A" * 32,
                                file_id=b"I" * 16,
                            )
                        )
            python = ChildAnnouncementDecoder().feed(_frame())
            self.assertEqual(python[0].announced_path, r"runtime\python.exe")
            python = ChildAnnouncementDecoder(addon_builder_path=sealed).feed(_frame())
            self.assertEqual(python[0].announced_path, r"runtime\python.exe")
            with self.assertRaisesRegex(
                ChildAnnouncementError,
                "invalid_native_child_announcement",
            ):
                ChildAnnouncementDecoder(addon_builder_path=sealed).feed(
                    _frame(path="runtime/python.exe")
                )

    def test_supervise_reads_one_descriptor_and_does_not_resolve_again(self) -> None:
        backend = importlib.import_module("dayz_mcp.native_launcher_backend")
        sealed = _addon_exe(Path(r"F:\SteamA\steamapps\common\DayZ Tools"))
        good = SimpleNamespace(kind=BrokerKind.ADDON_BUILDER, announced_path=sealed)
        worker = SimpleNamespace(kind=BrokerKind.PRIVATE_WORKER, announced_path=r"runtime\python.exe")
        self.assertEqual(
            backend._captured_addon_builder_path(
                SimpleNamespace(process_descriptors=(worker, good))
            ),
            sealed,
        )
        for authority in (
            SimpleNamespace(process_descriptors=()),
            SimpleNamespace(process_descriptors=(good, good)),
            SimpleNamespace(process_descriptors=(worker,)),
            SimpleNamespace(),
            SimpleNamespace(process_descriptors=[good]),
            SimpleNamespace(
                process_descriptors=(
                    SimpleNamespace(kind=BrokerKind.ADDON_BUILDER, announced_path=""),
                )
            ),
            SimpleNamespace(
                process_descriptors=(
                    SimpleNamespace(kind=BrokerKind.ADDON_BUILDER, announced_path="a\0b"),
                )
            ),
        ):
            with self.subTest(authority=authority):
                self.assertIsNone(backend._captured_addon_builder_path(authority))
        source = Path(backend.__file__).read_text(encoding="utf-8")
        start = source.index("def _supervise_created_launcher")
        body = source[start:source.index("\ndef ", start + 1)]
        self.assertIn("addon_builder_path=_captured_addon_builder_path(image_authority)", body)
        self.assertNotIn("resolved_layout", body)


if __name__ == "__main__":
    unittest.main()
