from __future__ import annotations

import hashlib
import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
BUNDLE = TOOLS_DIR / "native-launchers" / "dayz-test-v1"
LAUNCHER_ID = "dayz-test-v1"
RELATIVE_PATH = "dayz-test-launcher.exe"

sys.path.insert(0, str(TOOLS_DIR))

import build_native_launcher  # noqa: E402
from dayz_mcp import launcher_registry  # noqa: E402
from dayz_mcp import launcher_registry_update  # noqa: E402


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    """Report drift between the deployed launcher registry and the canonical bundle.

    Lives outside tests/ on purpose: the suite must not pin the PE hash as a
    literal, because a reproducible rebuild legitimately changes it. What is not
    legitimate is a bundle whose binary no longer matches the registry entry, or
    a registry entry pointing somewhere other than the canonical bundle - that is
    what this reports.
    """
    failures: list[str] = []

    # Same entry builder the installer uses (launcher_registry_update._validated_entry).
    try:
        expected = launcher_registry._create_registry_entry_for_test(
            LAUNCHER_ID, BUNDLE, RELATIVE_PATH
        )
    except BaseException as error:
        print("NATIVE LAUNCHER REGISTRY DRIFT")
        print(f"- cannot describe the canonical bundle: {type(error).__name__}: {error}")
        return 1

    try:
        entries = launcher_registry._parse_launcher_registry(
            launcher_registry._CANONICAL_REGISTRY.read_text(encoding="utf-8")
        )
    except BaseException as error:
        print("NATIVE LAUNCHER REGISTRY DRIFT")
        print(f"- unreadable registry: {type(error).__name__}: {error}")
        return 1

    if len(entries) != 1:
        failures.append(f"registry holds {len(entries)} launchers, expected exactly 1")
    else:
        deployed = entries[0]
        if deployed != expected:
            for key in sorted(set(deployed) | set(expected)):
                if deployed.get(key) != expected.get(key):
                    failures.append(
                        f"{key}: registry={deployed.get(key)!r} bundle={expected.get(key)!r}"
                    )

    # Provenance. Distinct from the drift comparison above: a registry
    # rewritten in place with the RIGHT content still matches the bundle and still
    # passes that check, while the receipt chain is already broken underneath.
    try:
        provenance = launcher_registry_update.describe_registry_provenance()
    except BaseException as error:
        failures.append(f"provenance unreadable: {type(error).__name__}: {error}")
    else:
        if provenance["status"] in {"unanchored", "ambiguous"}:
            failures.append(
                f"receipt chain broken (status={provenance['status']}): the live "
                f"registry sha {provenance['sha256']} is claimed by "
                f"{provenance['anchors']} committed receipts across "
                f"{provenance['transactions']} recorded transitions. The file was "
                f"written in place, outside install-dayz-test-v1 / rollback-last, "
                f"so rollback-last will fail with "
                f"launcher_registry_rollback_predecessor_unknown."
            )

    # Seals, closure and the reproducibility receipt of the bundle itself.
    try:
        build_native_launcher.verify_bundle(BUNDLE, require_receipt=True)
    except BaseException as error:
        failures.append(f"verify_bundle: {type(error).__name__}: {error}")

    lock_live = _digest(TOOLS_DIR / "dependency-lock.json")
    builder_live = _digest(TOOLS_DIR / "build_native_launcher.py")
    try:
        contract = build_native_launcher._closed_load(BUNDLE / "build-contract.json")
        if contract.get("dependency_lock_sha256") != lock_live:
            failures.append(
                f"dependency-lock drift: contract={contract.get('dependency_lock_sha256')} "
                f"live={lock_live} (rebuild the bundle)"
            )
        if contract.get("builder_sha256") != builder_live:
            failures.append(
                f"builder drift: contract={contract.get('builder_sha256')} "
                f"live={builder_live} (rebuild the bundle)"
            )
    except BaseException as error:
        failures.append(f"build-contract unreadable: {type(error).__name__}: {error}")

    if failures:
        print("NATIVE LAUNCHER REGISTRY DRIFT")
        for failure in failures:
            print(f"- {failure}")
        print(
            "Recovery: python build_native_launcher.py --offline --verify-reproducible, "
            "then python -m dayz_mcp.launcher_registry_update rollback-last followed by "
            "install-dayz-test-v1 --expected-sha256 <sha of the empty baseline registry>."
        )
        return 1

    print("NATIVE LAUNCHER REGISTRY OK")
    print(f"- launcher pe: {_digest(BUNDLE / RELATIVE_PATH)}")
    print(f"- registry   : {_digest(launcher_registry._CANONICAL_REGISTRY)}")
    print(f"- provenance : {provenance['status']} "
          f"({provenance['transactions']} recorded transitions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
