"""dayz_test_run tool fixtures shared by many tests.

Moved verbatim from test_dayz_test_tool.py so tests stop importing each other
(review 2026-09-25, T1).
"""
from __future__ import annotations

import json
import types

from dayz_mcp import dayz_test_request


RUN_ID = "12345678-1234-4234-8234-1234567890ab"


def _policy(
    *,
    mod: str = "ExampleMod",
    dev_root: str = r"P:\ExampleMod_Suite",
    default_source: str = r"P:\ExampleMod",
    default_base_mods: tuple[str, ...] = ("@CF", "@Dabs Framework"),
) -> dayz_test_request.RequestProjectPolicy:
    return dayz_test_request.RequestProjectPolicy(
        mod=mod,
        dev_root=dev_root,
        default_source=default_source,
        default_base_mods=default_base_mods,
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


def _sealed(*policies: dayz_test_request.RequestProjectPolicy) -> tuple[object, ...]:
    return tuple(types.SimpleNamespace(policy=policy) for policy in policies)


def _terminal(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class _Opened:
    def __init__(self) -> None:
        self.validated = False

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def validate_native_pe(self) -> None:
        self.validated = True


class _Bundle:
    def __init__(self, sealed_policies: tuple[object, ...]) -> None:
        self.sealed_policies = sealed_policies

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def validated_worker_runtime(self, mod: str, dev_root: str):
        policy = self.sealed_policies[0].policy
        if policy.mod != mod or policy.dev_root != dev_root:
            raise ValueError("runtime_policy_invalid")
        from dayz_mcp.dayz_test_worker import WorkerRuntimePolicy, runtime_policy_acceptable

        built = WorkerRuntimePolicy(
            dev_root=dev_root,
            mod=mod,
            diag_executable=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe",
            game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
            mission_aliases=(
                ("chernarus", dev_root + r"\_server\mpmissions\dayzOffline.chernarusplus"),
                ("livonia", dev_root + r"\_server\mpmissions\dayzOffline.enoch"),
                ("sakhal", dev_root + r"\_server\mpmissions\dayzOffline.sakhal"),
                ("lfheli", dev_root + r"\_server\mpmissions\lfheli"),
            ),
            mods_root=policy.mod_roots[0],
            build_temp_root=dev_root + r"\_build",
            build_source_basename=None,
        )
        if not runtime_policy_acceptable(built):
            raise ValueError("runtime_policy_invalid")
        return built


class _Runtime:
    def __init__(self, lifecycle: dict[str, object] | None = None) -> None:
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = object()
        self.lifecycle = lifecycle or {"runs": []}
        self.lifecycle_calls = 0
        self.reconcile_calls = 0
        # M19: the bridge snapshot the readiness projection reads. Counted so a
        # test can say how many times it was consulted, and on which rows.
        self.bridge_payload: object = {"ready": {"ready": True, "reason": "ready"}}
        self.bridge_calls = 0
        self.bridge_raises = False

    async def bridge_status_payload(self) -> dict[str, object]:
        self.bridge_calls += 1
        if self.bridge_raises:
            raise RuntimeError("snapshot unavailable")
        return self.bridge_payload  # type: ignore[return-value]

    async def lifecycle_status(self) -> dict[str, object]:
        self.lifecycle_calls += 1
        return self.lifecycle

    async def session_status(self) -> dict[str, object]:
        """Box observer the preflight samples. Tests may replace the payload."""
        self.session_calls = getattr(self, "session_calls", 0) + 1
        replacement = getattr(self, "session_payload", None)
        if isinstance(replacement, dict):
            return replacement
        runs = self.lifecycle.get("runs", []) if isinstance(self.lifecycle, dict) else []
        if not isinstance(runs, list):
            runs = []
        return {
            "box": {
                "occupied": bool(runs),
                "runs": list(runs),
                "foreign": [],
                "scan_known": True,
                "port_scan_known": True,
            }
        }

    async def reconcile_idle_session(self) -> dict[str, object]:
        self.reconcile_calls += 1
        return {"reconciled": False}
