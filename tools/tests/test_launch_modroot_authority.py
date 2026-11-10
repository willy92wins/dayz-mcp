"""FINDING-launch-modroot-20261110: the launch accredits the runtime mod_dir.

At ec006d27 execute_native_launcher_transaction accredited only the declared
lists (base_mods, extra_mods, server_mods, server_only_mods). The official
mod_dir is resolved by the worker runtime, and only the MCP preflight checked
it against policy.mod_roots. F3 r1 loaded a Temp mod_dir that no sealed
mod_root contains. These tests drive the real launch route
(secure_launcher.execute_secure_launcher_request -> the transaction) with a
counting control client and a fake native backend. No DayZ, no daemon.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import (
    dayz_test_request,
    dayz_test_worker,
    native_launcher_transaction,
    request_path_authority,
    secure_launcher,
)


class _CountingControlClient:
    def __init__(self) -> None:
        self.acquire_calls = 0
        self.calls: list[object] = []

    async def session_acquire_wait(
        self, purpose: str, max_wait_s: float | None = None, progress_cb=None
    ) -> dict[str, object]:
        self.acquire_calls += 1
        self.calls.append(("acquire_wait", purpose))
        return {
            "status": "active",
            "lease_token": "lease-token-modroot",
            "lease_id": "lease-modroot",
            "client_identity_json": '{"platform":"unknown"}',
        }

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]:
        return {
            "status": "active",
            "lease_token": lease_token,
            "lease_id": "lease-modroot",
            "expires_in_s": 120.0,
        }

    async def session_release(self, lease_token: str) -> dict[str, object]:
        self.calls.append(("release", lease_token))
        return {"status": "released"}

    async def session_status(self) -> dict[str, object]:
        return {"self": {"state": "none", "position": None}, "pending_commands": 0}


def _workspace(root: Path, *, extra_mod_roots: tuple[Path, ...] = ()):
    project = root / "ExampleMod_Suite"
    source = project / "source"
    missions = project / "_server" / "mpmissions"
    chernarus = missions / "dayzOffline.chernarusplus"
    mods = root / "Mods"
    for path in (source, chernarus, mods / "@CF", mods / "@VPPAdminTools"):
        path.mkdir(parents=True, exist_ok=True)
    (mods / "@VPPAdminTools" / "meta.cpp").write_text(
        "protocol = 1;\n"
        "publishedid = 1828439124;\n"
        'name = "VPPAdminTools";\n'
        "timestamp = 5250883055442304087;\n",
        encoding="ascii",
    )
    (project / "_server" / "serverDZ.cfg").write_text(
        "vppDisablePassword = 1;\n", encoding="ascii"
    )
    for extra in extra_mod_roots:
        extra.mkdir(parents=True, exist_ok=True)
    policy = dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=str(project),
        default_source=str(source),
        default_base_mods=("@CF",),
        mission_roots=(str(missions),),
        mod_roots=(str(mods), *(str(item) for item in extra_mod_roots)),
    )
    runtime = dayz_test_worker.WorkerRuntimePolicy(
        dev_root=str(project),
        mod="ExampleMod",
        diag_executable=str(root / "DayZ" / "DayZDiag_x64.exe"),
        game_directory=str(root / "DayZ"),
        mission_aliases=(("chernarus", str(chernarus)),),
        mods_root=str(mods),
        build_temp_root=str(root / "build-temp"),
        build_source_basename=None,
    )
    sealed = request_path_authority._seal_project_policy_for_test(policy)
    return policy, sealed, runtime


def _server_request(policy, mod_dir: Path) -> bytes:
    return json.dumps(
        {
            "version": 3,
            "mod": policy.mod,
            "dev_root": policy.dev_root,
            "mode": "server",
            # No admin tool is requested, so the VPP gate only warns and the
            # path authority is the check under test.
            "mod_dir": str(mod_dir),
        }
    ).encode("utf-8")


class LaunchModDirAuthorityTest(unittest.TestCase):
    """The launch applies the preflight's runtime check before the lease."""

    def _launch(self, raw: bytes, sealed, runtime):
        control = _CountingControlClient()
        runtime_calls: list[tuple[str, str]] = []
        launches: list[object] = []

        def validated_worker_runtime(mod: str, dev_root: str):
            runtime_calls.append((mod, dev_root))
            return runtime

        async def native_launch(_opened: object, **kwargs: object) -> int:
            launches.append(kwargs)
            return 0

        bundle = SimpleNamespace(
            sealed_policies=(sealed,),
            validated_worker_runtime=validated_worker_runtime,
        )
        raised: BaseException | None = None
        result: int | None = None
        with patch.object(
            secure_launcher,
            "serialize_normal_daemon_policy",
            return_value="policy-json",
        ), patch(
            "dayz_mcp.native_launcher_backend.launch_registered_native",
            side_effect=native_launch,
        ):
            try:
                result = asyncio.run(
                    secure_launcher.execute_secure_launcher_request(
                        raw,
                        opened_launcher=object(),
                        verified_bundle=bundle,
                        control_client=control,
                        daemon_policy=object(),
                        output_sink=lambda _channel, _chunk: None,
                    )
                )
            except BaseException as error:  # noqa: BLE001 - asserted below
                raised = error
        return raised, result, control, runtime_calls, launches

    def test_mod_dir_outside_every_mod_root_is_refused_before_the_lease(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy, sealed, runtime = _workspace(root)
            isolated = root / "Temp" / "ah-supervisor-real" / "mod-f3" / "@DayZ_MCP_F3"
            isolated.mkdir(parents=True)
            raw = _server_request(policy, isolated)
            # The parser admits it: the gap is not in the request schema.
            parsed = dayz_test_request.parse_dayz_test_request(
                raw, policies=(policy,)
            )
            self.assertEqual(Path(parsed.payload["mod_dir"]), isolated)

            raised, result, control, runtime_calls, launches = self._launch(
                raw, sealed, runtime
            )

        self.assertIsInstance(
            raised,
            ValueError,
            msg=(
                f"result={result!r} acquire_calls={control.acquire_calls} "
                f"native_launches={len(launches)}"
            ),
        )
        self.assertEqual(str(raised), "invalid_dayz_test_path_authority")
        self.assertEqual(control.acquire_calls, 0)
        self.assertEqual(control.calls, [])
        self.assertEqual(launches, [])
        self.assertEqual(runtime_calls, [(policy.mod, policy.dev_root)])

    def test_mod_dir_under_a_sealed_mod_root_still_launches(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            f3_root = root / "dayz-mcp-profiles" / "f3"
            policy, sealed, runtime = _workspace(root, extra_mod_roots=(f3_root,))
            isolated = f3_root / "@DayZ_MCP_F3"
            isolated.mkdir(parents=True)
            raised, result, control, runtime_calls, launches = self._launch(
                _server_request(policy, isolated), sealed, runtime
            )

        self.assertIsNone(raised)
        self.assertEqual(result, 0)
        self.assertEqual(control.acquire_calls, 1)
        self.assertEqual(len(launches), 1)
        self.assertEqual(runtime_calls, [(policy.mod, policy.dev_root)])
        self.assertEqual(control.calls[-1], ("release", "lease-token-modroot"))


class TransactionRuntimeAuthorityTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_transaction_requires_a_worker_runtime(self) -> None:
        with TemporaryDirectory() as temporary:
            policy, sealed, _runtime = _workspace(Path(temporary))
            control = _CountingControlClient()

            async def consumer(**_kwargs: object) -> int:
                self.fail("consumer must not run")

            with self.assertRaisesRegex(
                ValueError, "^invalid_native_launcher_transaction$"
            ):
                await native_launcher_transaction.execute_native_launcher_transaction(
                    _server_request(policy, Path(temporary) / "Mods" / "@CF"),
                    sealed_policies=(sealed,),
                    worker_runtime=None,  # type: ignore[arg-type]
                    control_client=control,
                    consumer=consumer,
                )
        self.assertEqual(control.acquire_calls, 0)

    async def test_a_kill_request_loads_nothing_and_skips_the_runtime_check(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            policy, sealed, _runtime = _workspace(Path(temporary))
            control = _CountingControlClient()
            raw = json.dumps(
                {
                    "version": 1,
                    "mod": policy.mod,
                    "dev_root": policy.dev_root,
                    "mode": "offline",
                    "kill": True,
                    "run_id": str(uuid.uuid4()),
                }
            ).encode("utf-8")

            def runtime_must_not_run(_mod: str, _dev_root: str):
                raise AssertionError("a kill request resolves no runtime path")

            async def consumer(**_kwargs: object) -> int:
                return 0

            result = await native_launcher_transaction.execute_native_launcher_transaction(
                raw,
                sealed_policies=(sealed,),
                worker_runtime=runtime_must_not_run,
                control_client=control,
                consumer=consumer,
            )
        self.assertEqual(result, 0)
        self.assertEqual(control.acquire_calls, 1)


if __name__ == "__main__":
    unittest.main()
