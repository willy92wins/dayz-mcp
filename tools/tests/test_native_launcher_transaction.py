from __future__ import annotations

import asyncio
import importlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class _ControlClient:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.grant = {
            "status": "active",
            "lease_token": "secret-lease-token",
            "lease_id": "lease-one",
            "client_identity_json": '{"platform":"unknown"}',
        }
        self.status = {
            "self": {"state": "none", "position": None},
            "pending_commands": 0,
        }

    async def session_acquire_wait(
        self,
        purpose: str,
        max_wait_s: float | None = None,
        progress_cb=None,
    ) -> dict[str, object]:
        self.calls.append(("acquire_wait", purpose, max_wait_s))
        if progress_cb is not None:
            await progress_cb(0.0, max_wait_s, "En cola (posición 3) para dayz-test")
        return dict(self.grant)

    async def session_heartbeat(self, lease_token: str) -> dict[str, object]:
        self.calls.append(("heartbeat", lease_token))
        return {
            "status": "active",
            "lease_token": lease_token,
            "lease_id": "lease-one",
            "expires_in_s": 120.0,
        }

    async def session_release(self, lease_token: str) -> dict[str, object]:
        self.calls.append(("release", lease_token))
        return {"status": "released"}

    async def session_status(self) -> dict[str, object]:
        self.calls.append("status")
        return self.status


class NativeLauncherTransactionTests(unittest.IsolatedAsyncioTestCase):
    def _fixture(self, root: Path):
        request_module = importlib.import_module("dayz_mcp.dayz_test_request")
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        project = root / "ExampleMod_Suite"
        source = project / "source"
        missions = project / "_server" / "mpmissions"
        mods = root / "Mods"
        for path in (source, missions, mods / "@CF"):
            path.mkdir(parents=True, exist_ok=True)
        policy = request_module.RequestProjectPolicy(
            mod="ExampleMod",
            dev_root=str(project),
            default_source=str(source),
            default_base_mods=("@CF",),
            mission_roots=(str(missions),),
            mod_roots=(str(mods),),
        )
        sealed = authority._seal_project_policy_for_test(policy)
        raw = json.dumps(
            {
                "version": 1,
                "mod": policy.mod,
                "dev_root": policy.dev_root,
                "preflight": True,
            }
        ).encode("utf-8")
        return sealed, raw

    async def test_schema_and_paths_precede_acquire_then_supervise_consumer(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        control = _ControlClient()
        consumed: dict[str, object] = {}
        progress: list[str | None] = []

        async def queue_progress(
            _elapsed: float, _maximum: float | None, message: str | None
        ) -> None:
            progress.append(message)

        async def consumer(**kwargs: object) -> int:
            consumed.update(kwargs)
            self.assertTrue(kwargs["heartbeat_supervisor"].running)
            self.assertFalse(kwargs["cancel_event"].is_set())
            return 0

        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            result = await module.execute_native_launcher_transaction(
                raw,
                sealed_policies=(sealed,),
                control_client=control,
                consumer=consumer,
                max_wait_s=None,
                queue_progress_cb=queue_progress,
            )

        self.assertEqual(result, 0)
        self.assertEqual(control.calls[0], ("acquire_wait", "dayz-test", None))
        self.assertEqual(
            control.calls[-2:],
            [("release", "secret-lease-token"), "status"],
        )
        self.assertEqual(
            json.loads(consumed["canonical_request"].decode("utf-8"))["mod"],
            "ExampleMod",
        )
        self.assertEqual(consumed["lease_token"], "secret-lease-token")
        self.assertEqual(
            consumed["client_identity_json"], '{"platform":"unknown"}'
        )
        self.assertNotIn(b"secret-lease-token", consumed["canonical_request"])
        self.assertEqual(progress, ["En cola (posición 3) para dayz-test"])

    async def test_invalid_schema_or_path_identity_sends_no_control_request(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        for label in ("schema", "identity"):
            with self.subTest(label=label), TemporaryDirectory() as temporary:
                root = Path(temporary)
                sealed, raw = self._fixture(root)
                if label == "schema":
                    raw = b'{"version":2}'
                else:
                    original = Path(sealed.policy.dev_root)
                    original.rename(root / "old-project")
                    original.mkdir()
                control = _ControlClient()

                async def consumer(**_kwargs: object) -> int:
                    self.fail("consumer must not run")

                with self.assertRaises(ValueError):
                    await module.execute_native_launcher_transaction(
                        raw,
                        sealed_policies=(sealed,),
                        control_client=control,
                        consumer=consumer,
                    )
                self.assertEqual(control.calls, [])

    async def test_consumer_failure_still_releases_and_verifies_terminal_status(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        control = _ControlClient()

        async def consumer(**_kwargs: object) -> int:
            raise RuntimeError("consumer_failed")

        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            with self.assertRaisesRegex(RuntimeError, "consumer_failed"):
                await module.execute_native_launcher_transaction(
                    raw,
                    sealed_policies=(sealed,),
                    control_client=control,
                    consumer=consumer,
                )
        self.assertEqual(
            control.calls[-2:],
            [("release", "secret-lease-token"), "status"],
        )

    async def test_malformed_active_grant_with_token_is_released_before_error(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        control = _ControlClient()
        control.grant.pop("lease_id")

        async def consumer(**_kwargs: object) -> int:
            self.fail("consumer must not run")

        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            with self.assertRaisesRegex(
                module.NativeLauncherTransactionError, "invalid_session_grant"
            ):
                await module.execute_native_launcher_transaction(
                    raw,
                    sealed_policies=(sealed,),
                    control_client=control,
                    consumer=consumer,
                )
        self.assertEqual(
            control.calls[-2:],
            [("release", "secret-lease-token"), "status"],
        )

    async def test_heartbeat_failure_cancels_consumer_before_release(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        supervisor_module = importlib.import_module("dayz_mcp.lease_supervisor")
        control = _ControlClient()
        cancelled = asyncio.Event()
        ordering: list[str] = []

        class FailedSupervisor:
            running = True

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return None

            def start(self) -> None:
                ordering.append("heartbeat_start")

            async def wait_failed(self):
                await asyncio.sleep(0)
                return supervisor_module.LeaseHeartbeatError(
                    "lease_heartbeat_failed"
                )

            def ensure_healthy(self) -> None:
                return None

            async def stop(self) -> None:
                ordering.append("heartbeat_stop")

        async def consumer(**kwargs: object) -> int:
            ordering.append("consumer_start")
            try:
                await kwargs["cancel_event"].wait()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                ordering.append("consumer_cancelled")
                cancelled.set()
                raise
            return 0

        original_release = control.session_release

        async def release(lease_token: str):
            ordering.append("release")
            return await original_release(lease_token)

        control.session_release = release  # type: ignore[method-assign]
        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            with patch.object(
                module.lease_supervisor,
                "LeaseHeartbeatSupervisor",
                FailedSupervisor,
            ), self.assertRaisesRegex(
                supervisor_module.LeaseHeartbeatError, "lease_heartbeat_failed"
            ):
                await module.execute_native_launcher_transaction(
                    raw,
                    sealed_policies=(sealed,),
                    control_client=control,
                    consumer=consumer,
                )
        self.assertTrue(cancelled.is_set())
        self.assertLess(
            ordering.index("consumer_cancelled"), ordering.index("release")
        )

    async def test_non_terminal_status_turns_success_into_cleanup_error(self) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        cleanup_module = importlib.import_module("dayz_mcp.lease_supervisor")
        control = _ControlClient()
        control.status = {
            "self": {"state": "active", "lease_id": "lease-one"},
            "pending_commands": 0,
        }

        async def consumer(**_kwargs: object) -> int:
            return 0

        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            with self.assertRaisesRegex(
                cleanup_module.LeaseCleanupError, "session_close_degraded"
            ):
                await module.execute_native_launcher_transaction(
                    raw,
                    sealed_policies=(sealed,),
                    control_client=control,
                    consumer=consumer,
                )

    async def test_repeated_task_cancellation_cannot_skip_consumer_cleanup_or_release(
        self,
    ) -> None:
        module = importlib.import_module("dayz_mcp.native_launcher_transaction")
        control = _ControlClient()
        consumer_started = asyncio.Event()
        consumer_cleanup_started = asyncio.Event()
        allow_consumer_cleanup = asyncio.Event()
        consumer_cleanup_done = asyncio.Event()

        async def consumer(**_kwargs: object) -> int:
            consumer_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                consumer_cleanup_started.set()
                await allow_consumer_cleanup.wait()
                consumer_cleanup_done.set()
                raise
            return 0

        with TemporaryDirectory() as temporary:
            sealed, raw = self._fixture(Path(temporary))
            transaction = asyncio.create_task(
                module.execute_native_launcher_transaction(
                    raw,
                    sealed_policies=(sealed,),
                    control_client=control,
                    consumer=consumer,
                )
            )
            await consumer_started.wait()
            transaction.cancel()
            await consumer_cleanup_started.wait()
            transaction.cancel()
            await asyncio.sleep(0)
            self.assertFalse(transaction.done())
            allow_consumer_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await transaction

        self.assertTrue(consumer_cleanup_done.is_set())
        self.assertEqual(
            control.calls[-2:],
            [("release", "secret-lease-token"), "status"],
        )


if __name__ == "__main__":
    unittest.main()
