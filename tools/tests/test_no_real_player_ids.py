"""No real player identifiers may live under tools/tests/ (fb-20261112-205605-199c).

Real SteamID64 values and the DayZ uids derived from them identify the
owner, so the tree carries only the nine fake placeholders in
ALLOWED_STEAM_IDS and the uids derived from those placeholders. This
watchdog scans every file under tools/tests/ (recursive, skipping
__pycache__, reading bytes, including this module) and fails when a
real-looking SteamID64 or DayZ uid appears that is not allow-listed.

Failure messages name ``path:line`` and mask the offending value: a
Steam ID keeps only its first 7 digits followed by ``x`` characters,
and a uid is shown only as a sha1 prefix. No real value is ever
printed or embedded in a message.
"""

from __future__ import annotations

import base64
import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ALLOWED_STEAM_IDS: tuple[str, ...] = (
    "76561198000000000",
    "76561198000000001",
    "76561198000000002",
    "76561198000000003",
    "76561198000000009",
    "76561198000000101",
    "76561198000000102",
    "76561198000000103",
    "76561198000000104",
)

_STEAM_ID_RE = re.compile(rb"7656119\d{10}")
_UID_RE = re.compile(
    rb"(?<![A-Za-z0-9_\-+/])[A-Za-z0-9_\-+/]{43}=(?![A-Za-z0-9=])"
)
_STEAM_ID_TEXT_RE = re.compile(r"7656119\d{10}")
_UID_TEXT_RE = re.compile(r"[A-Za-z0-9_\-+/]{43}=")

_TESTS_DIR = Path(__file__).resolve().parent


def _derive_uid(steam_id: str) -> str:
    digest = hashlib.sha256(steam_id.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


_ALLOWED_STEAM_ID_SET = frozenset(ALLOWED_STEAM_IDS)
_ALLOWED_UID_SET = frozenset(_derive_uid(item) for item in ALLOWED_STEAM_IDS)


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _mask_steam_id(value: str) -> str:
    return value[:7] + "x" * (len(value) - 7)


def _mask_uid(value: str) -> str:
    prefix = hashlib.sha1(value.encode("ascii")).hexdigest()[:12]
    return f"uid sha1:{prefix}"


def find_violations(data: bytes) -> list[tuple[int, str]]:
    """Return ``(line, masked)`` for every non-allowed ID or uid in *data*."""
    violations: list[tuple[int, str]] = []
    for match in _STEAM_ID_RE.finditer(data):
        value = match.group().decode("ascii")
        if value in _ALLOWED_STEAM_ID_SET:
            continue
        violations.append(
            (_line_number(data, match.start()), _mask_steam_id(value))
        )
    for match in _UID_RE.finditer(data):
        value = match.group().decode("ascii")
        if value in _ALLOWED_UID_SET:
            continue
        violations.append(
            (_line_number(data, match.start()), _mask_uid(value))
        )
    violations.sort()
    return violations


def _mask_path_text(text: str) -> str:
    masked = _STEAM_ID_TEXT_RE.sub(
        lambda match: _mask_steam_id(match.group()), text
    )
    return _UID_TEXT_RE.sub(
        lambda match: _mask_uid(match.group()[:-1]) + "=", masked
    )


def _display_path(path: Path, root: Path | None = None) -> str:
    """Return *path* relative to *root*, with any identifiers masked."""
    base = _TESTS_DIR if root is None else root
    try:
        relative = path.relative_to(base)
    except ValueError:
        relative = Path(path.name)
    return _mask_path_text(str(relative))


def _has_symlinked_parent(path: Path, base: Path) -> bool:
    try:
        relative = path.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _walk_problem(display: str, exc: OSError) -> str:
    return f"{display}: unreadable ({type(exc).__name__})"


def _iter_test_files_with_problems(
    root: Path | None = None,
) -> tuple[list[Path], list[str]]:
    """Return ``(files, problems)`` for the tree under *root*.

    Every walk or metadata call is guarded: an OSError from resolving
    the root, iterating ``rglob``, or calling ``is_symlink``,
    ``stat``, ``is_file`` or ``resolve`` on an entry becomes a masked
    problem string (masked display path plus the exception class name
    only), so the entry is never skipped silently and no raw path or
    exception text can leak. A sibling wrapper keeps the historical
    files-only view for callers that do not report problems.
    """
    raw_base = _TESTS_DIR if root is None else root
    try:
        base = raw_base.resolve()
    except OSError as exc:
        display = _mask_path_text(raw_base.name or ".")
        return [], [_walk_problem(display, exc)]
    files: list[Path] = []
    problems: list[str] = []
    try:
        for path in base.rglob("*"):
            display = _display_path(path, base)
            try:
                if path.is_symlink():
                    continue
                try:
                    relative = path.relative_to(base)
                except ValueError:
                    continue
                if "__pycache__" in relative.parts:
                    continue
                if _has_symlinked_parent(path, base):
                    continue
                path.stat()
                if not path.is_file():
                    continue
                resolved = path.resolve()
            except OSError as exc:
                problems.append(_walk_problem(display, exc))
                continue
            try:
                resolved.relative_to(base)
            except ValueError:
                continue
            files.append(path)
    except OSError as exc:
        problems.append(_walk_problem(_display_path(base, base), exc))
    return sorted(files), problems


def _iter_test_files(root: Path | None = None) -> list[Path]:
    files, _problems = _iter_test_files_with_problems(root)
    return files


def _scan_directory(root: Path | None = None) -> list[str]:
    raw_base = _TESTS_DIR if root is None else root
    try:
        base = raw_base.resolve()
    except OSError as exc:
        display = _mask_path_text(raw_base.name or ".")
        return [_walk_problem(display, exc)]
    files, problems = _iter_test_files_with_problems(base)
    problems = list(problems)
    for path in files:
        display = _display_path(path, base)
        try:
            data = path.read_bytes()
        except OSError as exc:
            problems.append(
                f"{display}: unreadable ({type(exc).__name__})"
            )
            continue
        for line, masked in find_violations(data):
            problems.append(f"{display}:{line}: {masked}")
    return problems


_FAKE_STEAM_ID = "7656119" + "8123456789"


class FindViolationsTest(unittest.TestCase):
    def test_invented_steam_id_is_flagged_and_masked(self) -> None:
        violations = find_violations(("id=" + _FAKE_STEAM_ID).encode("ascii"))

        self.assertEqual(len(violations), 1)
        line, masked = violations[0]
        self.assertEqual(line, 1)
        self.assertEqual(masked, "7656119" + "x" * 10)
        self.assertNotIn(_FAKE_STEAM_ID, masked)

    def test_invented_steam_id_line_number_is_reported(self) -> None:
        data = ("clean\nid=" + _FAKE_STEAM_ID + "\n").encode("ascii")

        self.assertEqual([line for line, _ in find_violations(data)], [2])

    def test_invented_uid_is_flagged_and_masked(self) -> None:
        fake_uid = _derive_uid(_FAKE_STEAM_ID)
        violations = find_violations(("uid=" + fake_uid).encode("ascii"))

        self.assertEqual(len(violations), 1)
        line, masked = violations[0]
        self.assertEqual(line, 1)
        self.assertTrue(masked.startswith("uid sha1:"))
        self.assertNotIn(fake_uid, masked)

    def test_allowed_steam_ids_are_not_flagged(self) -> None:
        for steam_id in ALLOWED_STEAM_IDS:
            with self.subTest(steam_id=steam_id):
                self.assertEqual(find_violations(steam_id.encode("ascii")), [])

    def test_allowed_uids_are_not_flagged(self) -> None:
        for steam_id in ALLOWED_STEAM_IDS:
            with self.subTest(steam_id=steam_id):
                uid = _derive_uid(steam_id)
                self.assertEqual(find_violations(uid.encode("ascii")), [])

    def test_two_concatenated_allowed_ids_are_not_flagged(self) -> None:
        joined = ALLOWED_STEAM_IDS[5] + ALLOWED_STEAM_IDS[8]

        self.assertEqual(find_violations(joined.encode("ascii")), [])

    def test_invented_id_after_an_allowed_id_is_flagged(self) -> None:
        joined = ALLOWED_STEAM_IDS[5] + _FAKE_STEAM_ID
        violations = find_violations(joined.encode("ascii"))

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][1], "7656119" + "x" * 10)

    def test_clean_bytes_have_no_violations(self) -> None:
        self.assertEqual(find_violations(b"no player identifiers here\n"), [])


class WalkHelpersTest(unittest.TestCase):
    def test_masked_display_path_hides_ids_in_file_name(self) -> None:
        fake_uid = _derive_uid(_FAKE_STEAM_ID)
        name = _FAKE_STEAM_ID + "." + fake_uid + ".txt"
        display = _display_path(_TESTS_DIR / name)

        self.assertNotIn(_FAKE_STEAM_ID, display)
        self.assertNotIn(fake_uid, display)
        self.assertIn("7656119" + "x" * 10, display)
        self.assertTrue(display.endswith(".txt"))

    def test_display_path_masks_uid_after_underscore(self) -> None:
        fake_uid = _derive_uid(_FAKE_STEAM_ID)
        name = "fixture_" + fake_uid + ".txt"
        display = _display_path(_TESTS_DIR / name)

        self.assertNotIn(fake_uid, display)
        self.assertNotIn(_FAKE_STEAM_ID, display)

    def test_display_path_masks_uid_as_directory_name(self) -> None:
        fake_uid = _derive_uid(_FAKE_STEAM_ID)
        nested = _TESTS_DIR / "sub" / fake_uid / "inner.txt"
        display = _display_path(nested)

        self.assertNotIn(fake_uid, display)
        self.assertNotIn(_FAKE_STEAM_ID, display)

    def test_display_path_masks_steam_id_in_longer_word(self) -> None:
        fake_uid = _derive_uid(_FAKE_STEAM_ID)
        name = "prefix" + _FAKE_STEAM_ID + "suffix.txt"
        display = _display_path(_TESTS_DIR / name)

        self.assertNotIn(_FAKE_STEAM_ID, display)
        self.assertNotIn(fake_uid, display)
        self.assertIn("7656119" + "x" * 10, display)

    def test_read_error_is_reported_with_masked_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            named = root / (_FAKE_STEAM_ID + ".txt")
            named.write_bytes(b"clean\n")
            plain = root / "plain.txt"
            plain.write_bytes(b"clean\n")
            real_read_bytes = Path.read_bytes

            def fake_read_bytes(self: Path) -> bytes:
                if self.name == named.name:
                    raise PermissionError("denied: " + str(self))
                return real_read_bytes(self)

            with mock.patch.object(Path, "read_bytes", fake_read_bytes):
                problems = _scan_directory(root)

        self.assertEqual(len(problems), 1)
        self.assertNotIn(_FAKE_STEAM_ID, problems[0])
        self.assertIn("PermissionError", problems[0])
        self.assertIn("7656119" + "x" * 10, problems[0])

    def test_is_symlink_error_is_reported_masked(self) -> None:
        secret = "7656119" + "8123456789"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            named = root / (secret + ".txt")
            named.write_bytes(b"clean\n")
            plain = root / "plain.txt"
            plain.write_bytes(b"clean\n")
            real_is_symlink = Path.is_symlink

            def fake_is_symlink(self: Path) -> bool:
                if self.name == named.name:
                    raise PermissionError(13, "Permission denied", secret)
                return real_is_symlink(self)

            with mock.patch.object(Path, "is_symlink", fake_is_symlink):
                problems = _scan_directory(root)

        self.assertTrue(problems)
        self.assertEqual(len(problems), 1)
        self.assertNotIn(secret, problems[0])
        self.assertIn("PermissionError", problems[0])

    def test_is_file_error_is_reported_masked(self) -> None:
        secret = _derive_uid("7656119" + "8123456789")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            named = root / (secret + ".txt")
            named.write_bytes(b"clean\n")
            plain = root / "plain.txt"
            plain.write_bytes(b"clean\n")
            real_is_file = Path.is_file

            def fake_is_file(self: Path) -> bool:
                if self.name == named.name:
                    raise PermissionError(13, "Permission denied", secret)
                return real_is_file(self)

            with mock.patch.object(Path, "is_file", fake_is_file):
                problems = _scan_directory(root)

        self.assertTrue(problems)
        self.assertEqual(len(problems), 1)
        self.assertNotIn(secret, problems[0])
        self.assertIn("PermissionError", problems[0])

    def test_stat_error_is_reported_masked(self) -> None:
        secret = _derive_uid("7656119" + "9987654321")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            named = root / (secret + ".txt")
            named.write_bytes(b"clean\n")
            plain = root / "plain.txt"
            plain.write_bytes(b"clean\n")
            real_stat = Path.stat

            def fake_stat(self: Path, *args, **kwargs):
                if self.name == named.name:
                    raise PermissionError(13, "Permission denied", secret)
                return real_stat(self, *args, **kwargs)

            with mock.patch.object(Path, "stat", fake_stat):
                problems = _scan_directory(root)

        self.assertTrue(problems)
        self.assertEqual(len(problems), 1)
        self.assertNotIn(secret, problems[0])
        self.assertIn("PermissionError", problems[0])

    def test_scan_masks_file_name_in_temp_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            named = root / (_FAKE_STEAM_ID + ".txt")
            named.write_bytes(("id=" + _FAKE_STEAM_ID).encode("ascii"))
            plain = root / "plain.txt"
            plain.write_bytes(b"no player identifiers here\n")

            files = _iter_test_files(root)
            problems = _scan_directory(root)

        self.assertEqual(
            {path.name for path in files}, {named.name, plain.name}
        )
        self.assertEqual(len(problems), 1)
        self.assertNotIn(_FAKE_STEAM_ID, problems[0])
        self.assertTrue(
            problems[0].startswith("7656119" + "x" * 10 + ".txt:1:")
        )

    def test_symlinked_file_to_outside_is_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "tests"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_bytes(("id=" + _FAKE_STEAM_ID).encode("ascii"))
            link = root / "fixture.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"cannot create symlink: {exc}")

            files = _iter_test_files(root)
            problems = _scan_directory(root)

        self.assertNotIn("fixture.txt", {path.name for path in files})
        self.assertEqual(problems, [])

    def test_pycache_parent_in_absolute_path_does_not_exclude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "__pycache__" / "scan"
            cached = root / "__pycache__"
            cached.mkdir(parents=True)
            plain = root / "plain.txt"
            plain.write_bytes(b"no player identifiers here\n")
            skipped = cached / "cached.txt"
            skipped.write_bytes(b"no player identifiers here\n")

            files = _iter_test_files(root)

        self.assertIn("plain.txt", {path.name for path in files})
        self.assertNotIn("cached.txt", {path.name for path in files})


class TestsTreeScanTest(unittest.TestCase):
    def test_no_real_player_ids_under_tests(self) -> None:
        problems = _scan_directory(_TESTS_DIR)

        self.assertEqual(
            problems,
            [],
            "real-looking player identifiers under tools/tests/ "
            "(masked; replace with an allow-listed placeholder):\n"
            + "\n".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
