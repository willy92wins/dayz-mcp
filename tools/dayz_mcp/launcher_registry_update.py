"""CAS install and one-link rollback for the canonical launcher registry."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from typing import Sequence

from dayz_mcp import launcher_registry
from dayz_mcp.native_bundle import load_verified_bundle
from dayz_mcp.registry_lock import _CANONICAL_LOCK, acquire_registry_lock

if os.name == "nt":
    from ctypes import wintypes


_CANONICAL_REGISTRY = Path(__file__).resolve().parents[1] / "approved-launchers.json"
_CANONICAL_BUNDLE = (
    Path(__file__).resolve().parents[1] / "native-launchers" / "dayz-test-v1"
)
_CANONICAL_RECEIPTS = (
    Path(__file__).resolve().parents[1] / "approved-launchers.receipts"
)
_CANONICAL_BASELINE = (
    Path(__file__).resolve().parents[1] / "approved-launchers.baseline.json"
)
_BASELINE_SHA256 = "330B04E8D7AB06E7EE850326C1CAE180F119ED21486745DC0EC9BAAE203C653B"
_REPLACEFILE_WRITE_THROUGH = 0x00000001
_HEX = frozenset("0123456789ABCDEF")

if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.ReplaceFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    _kernel32.ReplaceFileW.restype = wintypes.BOOL


def _invalid(code: str) -> None:
    raise RuntimeError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _valid_sha(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _identity(info: os.stat_result) -> dict[str, object]:
    return launcher_registry._identity_from_stat(info).to_payload()


def _read_pinned(path: Path) -> tuple[bytes, dict[str, object]]:
    launcher_registry._reject_path_name_surrogates(
        path, error_code="invalid_launcher_registry"
    )
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        lexical = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or int(before.st_nlink) != 1
            or _identity(before) != _identity(lexical)
        ):
            _invalid("invalid_launcher_registry")
        raw = stream.read(1_048_577)
        after = os.fstat(stream.fileno())
        if len(raw) > 1_048_576 or _identity(before) != _identity(after):
            _invalid("launcher_registry_identity_drift")
    try:
        launcher_registry._parse_launcher_registry(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("invalid_launcher_registry") from error
    return raw, _identity(before)


def _write_create_only(path: Path, raw: bytes) -> dict[str, object]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _invalid("launcher_registry_write_failed")
            offset += written
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
            _invalid("launcher_registry_write_failed")
        return _identity(info)
    finally:
        os.close(descriptor)


def _replace(target: Path, replacement: Path) -> None:
    if os.name != "nt" or not _kernel32.ReplaceFileW(
        str(target),
        str(replacement),
        None,
        _REPLACEFILE_WRITE_THROUGH,
        None,
        None,
    ):
        code = ctypes.get_last_error() if os.name == "nt" else 0
        raise OSError(code, "launcher_registry_replace_failed")


def _receipt_bytes(value: dict[str, object]) -> bytes:
    return _canonical(value)


def _read_receipt(path: Path) -> bytes:
    launcher_registry._reject_path_name_surrogates(
        path, error_code="invalid_launcher_registry_receipt"
    )
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            lexical = os.stat(path, follow_symlinks=False)
            raw = stream.read(65_537)
            after = os.fstat(stream.fileno())
    except OSError as error:
        raise RuntimeError("invalid_launcher_registry_receipt") from error
    if (
        len(raw) > 65_536
        or not stat.S_ISREG(before.st_mode)
        or int(before.st_nlink) != 1
        or _identity(before) != _identity(lexical)
        or _identity(before) != _identity(after)
    ):
        _invalid("invalid_launcher_registry_receipt")
    return raw


def _bootstrap_registry(*, registry_path: Path, baseline_path: Path) -> str:
    # Create-only seed of the live registry from the shipped empty baseline.
    # A clone has no approved-launchers.json and every transition needs one to
    # CAS against, so this is the documented first step after building the
    # bundle. It never touches an existing registry: resetting one stays an
    # explicit human decision.
    try:
        raw = baseline_path.read_bytes()
    except OSError as error:
        raise RuntimeError("launcher_registry_baseline_missing") from error
    if _sha256(raw) != _BASELINE_SHA256:
        _invalid("launcher_registry_baseline_drift")
    try:
        launcher_registry._parse_launcher_registry(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("launcher_registry_baseline_invalid") from error
    if registry_path.exists():
        _invalid("launcher_registry_already_bootstrapped")
    _write_create_only(registry_path, raw)
    installed, _installed_identity = _read_pinned(registry_path)
    if installed != raw:
        _invalid("launcher_registry_bootstrap_verification_failed")
    return _sha256(raw)


def bootstrap_registry() -> str:
    return _bootstrap_registry(
        registry_path=_CANONICAL_REGISTRY,
        baseline_path=_CANONICAL_BASELINE,
    )


def _validated_entry(bundle: Path) -> dict[str, object]:
    entry = launcher_registry._create_registry_entry_for_test(
        "dayz-test-v1", bundle, "dayz-test-launcher.exe"
    )
    with launcher_registry._open_registry_entry_for_test(entry) as opened:
        opened.validate_native_pe()
        with load_verified_bundle(opened):
            pass
    return entry


def _install_transition(
    *,
    registry_path: Path,
    lock_path: Path,
    receipts_path: Path,
    bundle: Path,
    expected_sha256: str,
    fail_at: str | None = None,
) -> str:
    if not _valid_sha(expected_sha256) or fail_at not in {
        None,
        "before_replace",
        "after_replace",
    }:
        _invalid("invalid_launcher_registry_update")
    entry = _validated_entry(bundle)
    with acquire_registry_lock(exclusive=True, path=lock_path):
        current_raw, current_identity = _read_pinned(registry_path)
        current_sha = _sha256(current_raw)
        _recover_prepared(receipts_path, current_sha, current_identity)
        if current_sha != expected_sha256:
            _invalid("launcher_registry_cas_mismatch")
        current_entries = launcher_registry._parse_launcher_registry(
            current_raw.decode("utf-8")
        )
        retained = [item for item in current_entries if item["id"] != "dayz-test-v1"]
        if len(retained) != len(current_entries):
            _invalid("launcher_registry_version_already_installed")
        target_payload = {"format_version": 1, "launchers": [*retained, entry]}
        launcher_registry._validate_launcher_registry_payload(target_payload)
        target_raw = _canonical(target_payload)
        target_sha = _sha256(target_raw)
        if target_sha == current_sha:
            _invalid("launcher_registry_version_already_installed")

        receipts_path.mkdir(parents=True, exist_ok=True)
        launcher_registry._reject_path_name_surrogates(
            receipts_path, error_code="invalid_launcher_registry_receipts"
        )
        transaction = receipts_path / str(uuid.uuid4())
        transaction.mkdir()
        backup_path = transaction / "from-registry.json"
        _write_create_only(backup_path, current_raw)
        temporary = registry_path.with_name(
            f".{registry_path.name}.tmp.{uuid.uuid4()}"
        )
        to_identity = _write_create_only(temporary, target_raw)
        prepared = {
            "format_version": 1,
            "from_identity": current_identity,
            "from_sha256": current_sha,
            "outcome": "prepared",
            "to_identity": to_identity,
            "to_sha256": target_sha,
        }
        _write_create_only(transaction / "prepared.json", _receipt_bytes(prepared))
        before_replace_raw, before_replace_identity = _read_pinned(registry_path)
        if (
            before_replace_raw != current_raw
            or before_replace_identity != current_identity
        ):
            _invalid("launcher_registry_cas_mismatch")
        if fail_at == "before_replace":
            _invalid("launcher_registry_injected_failure")
        _replace(registry_path, temporary)
        installed_raw, installed_identity = _read_pinned(registry_path)
        if (
            installed_raw != target_raw
            or _sha256(installed_raw) != target_sha
            or installed_identity != to_identity
        ):
            _invalid("launcher_registry_install_verification_failed")
        if fail_at == "after_replace":
            _invalid("launcher_registry_injected_failure")
        committed = {
            **prepared,
            "outcome": "committed",
            "prepared_sha256": _sha256(_receipt_bytes(prepared)),
        }
        _write_create_only(transaction / "committed.json", _receipt_bytes(committed))
        return target_sha


def install_dayz_test_v1(*, expected_sha256: str) -> str:
    return _install_transition(
        registry_path=_CANONICAL_REGISTRY,
        lock_path=_CANONICAL_LOCK,
        receipts_path=_CANONICAL_RECEIPTS,
        bundle=_CANONICAL_BUNDLE,
        expected_sha256=expected_sha256,
    )


def describe_registry_provenance(
    *,
    registry_path: Path = _CANONICAL_REGISTRY,
    lock_path: Path = _CANONICAL_LOCK,
    receipts_path: Path = _CANONICAL_RECEIPTS,
) -> dict[str, object]:
    """Report whether the live registry bytes came from a RECORDED transition.

    BUG-072: the supported flow only ever moves the registry through
    _install_transition / _rollback_transition, and each leaves a receipt. Anything
    that rewrites the file in place -- an editor, an ad-hoc script, a second session
    -- leaves content that no receipt describes. The damage stays invisible until
    the next rollback-last dies with launcher_registry_rollback_predecessor_unknown,
    far from the cause and with the registry stuck: install then also refuses with
    launcher_registry_version_already_installed. This answers the same question up
    front, cheaply.

    Strictly READ-ONLY, unlike _rollback_transition: it never promotes a prepared
    receipt (_recover_prepared writes committed.json), so a diagnostic can never
    mutate the chain it is inspecting. The shared lock only prevents reading
    mid-transition, which would report a break that is not there.

    The match predicate mirrors _rollback_transition (sha AND NTFS identity, and
    the transaction not already rolled back) so the report cannot drift from the
    operation it predicts.

    States:
    - "anchored"   an install landed exactly this content; rollback-last will work.
    - "rolled_back" this is the predecessor a rollback restored; legitimate. Matched
      by content alone, for the ReplaceFileW reason noted at the predicate.
    - "pristine"   no transition was ever recorded here (e.g. a fresh clone).
    - "unanchored" content nobody recorded -- the bug.
    - "ambiguous"  several receipts claim it; rollback-last refuses this too.
    """
    with acquire_registry_lock(exclusive=False, path=lock_path):
        current_raw, current_identity = _read_pinned(registry_path)
        current_sha = _sha256(current_raw)
        anchors = 0
        restored = 0
        transactions = 0
        if receipts_path.is_dir():
            transactions = len(list(receipts_path.glob("*/prepared.json")))
            for committed_path in sorted(receipts_path.glob("*/committed.json")):
                committed = _load_committed(committed_path)
                undone = (committed_path.parent / "rolled-back.json").exists()
                if (
                    not undone
                    and committed["to_sha256"] == current_sha
                    and committed["to_identity"] == current_identity
                ):
                    anchors += 1
                elif undone and committed["from_sha256"] == current_sha:
                    # Content only, deliberately. ReplaceFileW hands the registry a
                    # NEW file id on every transition (measured: install and rollback
                    # each produced a different id), so a restored registry can never
                    # carry the `from_identity` the receipt recorded. Requiring it
                    # would report every correct rollback as a broken chain.
                    restored += 1
    if anchors == 1:
        status = "anchored"
    elif anchors > 1:
        status = "ambiguous"
    elif restored:
        status = "rolled_back"
    elif transactions == 0:
        status = "pristine"
    else:
        status = "unanchored"
    return {
        "anchors": anchors,
        "restored_anchors": restored,
        "sha256": current_sha,
        "status": status,
        "transactions": transactions,
    }


def _load_committed(path: Path) -> dict[str, object]:
    try:
        raw = _read_receipt(path)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid_launcher_registry_receipt") from error
    if (
        type(value) is not dict
        or set(value) != {
            "format_version",
            "from_identity",
            "from_sha256",
            "outcome",
            "prepared_sha256",
            "to_identity",
            "to_sha256",
        }
        or value.get("format_version") != 1
        or value.get("outcome") != "committed"
        or not _valid_sha(value.get("from_sha256"))
        or not _valid_sha(value.get("to_sha256"))
        or not _valid_sha(value.get("prepared_sha256"))
        or raw != _canonical(value)
    ):
        _invalid("invalid_launcher_registry_receipt")
    return value


def _load_prepared(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        raw = _read_receipt(path)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("invalid_launcher_registry_receipt") from error
    if (
        type(value) is not dict
        or set(value) != {
            "format_version",
            "from_identity",
            "from_sha256",
            "outcome",
            "to_identity",
            "to_sha256",
        }
        or value.get("format_version") != 1
        or value.get("outcome") != "prepared"
        or not _valid_sha(value.get("from_sha256"))
        or not _valid_sha(value.get("to_sha256"))
        or raw != _canonical(value)
    ):
        _invalid("invalid_launcher_registry_receipt")
    return value, raw


def _recover_prepared(
    receipts_path: Path,
    current_sha: str,
    current_identity: dict[str, object],
) -> None:
    if not receipts_path.is_dir():
        return
    for prepared_path in receipts_path.glob("*/prepared.json"):
        committed_path = prepared_path.parent / "committed.json"
        if committed_path.exists():
            continue
        prepared, prepared_raw = _load_prepared(prepared_path)
        if (
            prepared["to_sha256"] == current_sha
            and prepared["to_identity"] == current_identity
        ):
            committed = {
                **prepared,
                "outcome": "committed",
                "prepared_sha256": _sha256(prepared_raw),
            }
            _write_create_only(committed_path, _receipt_bytes(committed))
        elif (
            prepared["from_sha256"] == current_sha
            and prepared["from_identity"] == current_identity
        ):
            continue
        elif current_sha in {prepared["from_sha256"], prepared["to_sha256"]}:
            _invalid("launcher_registry_receipt_identity_drift")


def _rollback_transition(
    *, registry_path: Path, lock_path: Path, receipts_path: Path
) -> str:
    with acquire_registry_lock(exclusive=True, path=lock_path):
        current_raw, current_identity = _read_pinned(registry_path)
        current_sha = _sha256(current_raw)
        _recover_prepared(receipts_path, current_sha, current_identity)
        matches: list[tuple[Path, dict[str, object]]] = []
        if receipts_path.is_dir():
            for committed_path in receipts_path.glob("*/committed.json"):
                committed = _load_committed(committed_path)
                prepared, prepared_raw = _load_prepared(
                    committed_path.parent / "prepared.json"
                )
                if (
                    _sha256(prepared_raw) != committed["prepared_sha256"]
                    or any(
                        prepared[key] != committed[key]
                        for key in (
                            "format_version",
                            "from_identity",
                            "from_sha256",
                            "to_identity",
                            "to_sha256",
                        )
                    )
                ):
                    _invalid("invalid_launcher_registry_receipt")
                if (
                    committed["to_sha256"] == current_sha
                    and committed["to_identity"] == current_identity
                    and not (committed_path.parent / "rolled-back.json").exists()
                ):
                    matches.append((committed_path.parent, committed))
        if len(matches) != 1:
            _invalid("launcher_registry_rollback_predecessor_unknown")
        transaction, committed = matches[0]
        backup = transaction / "from-registry.json"
        from_raw, _backup_identity = _read_pinned(backup)
        if _sha256(from_raw) != committed["from_sha256"]:
            _invalid("launcher_registry_rollback_backup_drift")
        temporary = registry_path.with_name(
            f".{registry_path.name}.rollback.{uuid.uuid4()}"
        )
        restored_identity = _write_create_only(temporary, from_raw)
        before_replace_raw, before_replace_identity = _read_pinned(registry_path)
        if (
            before_replace_raw != current_raw
            or before_replace_identity != current_identity
        ):
            _invalid("launcher_registry_cas_mismatch")
        _replace(registry_path, temporary)
        restored_raw, observed_identity = _read_pinned(registry_path)
        if restored_raw != from_raw or observed_identity != restored_identity:
            _invalid("launcher_registry_rollback_verification_failed")
        rolled_back = {
            "format_version": 1,
            "from_sha256": current_sha,
            "outcome": "rolled_back",
            "to_identity": observed_identity,
            "to_sha256": _sha256(restored_raw),
        }
        _write_create_only(
            transaction / "rolled-back.json", _receipt_bytes(rolled_back)
        )
        return str(committed["from_sha256"])


def rollback_last_registry_transition() -> str:
    return _rollback_transition(
        registry_path=_CANONICAL_REGISTRY,
        lock_path=_CANONICAL_LOCK,
        receipts_path=_CANONICAL_RECEIPTS,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update the native launcher registry")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install-dayz-test-v1")
    install.add_argument("--expected-sha256", required=True)
    commands.add_parser("rollback-last")
    commands.add_parser("bootstrap")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "install-dayz-test-v1":
            result = install_dayz_test_v1(expected_sha256=args.expected_sha256)
        elif args.command == "bootstrap":
            result = bootstrap_registry()
            print(
                "next: install-dayz-test-v1 --expected-sha256 " + result,
                file=sys.stderr,
            )
        else:
            result = rollback_last_registry_transition()
    except BaseException as error:
        print(f"launcher registry update failed: {type(error).__name__}")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
