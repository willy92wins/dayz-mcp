"""H14: H3 reads and owned dayz_test_stop survive policy.revalidate() failure.

R26 fixtures:
- POS: exempted ControlClient lifecycle_status and credential 401 retry send HTTP
  when revalidate fails and the authority tuple is unchanged. The 401 retry
  reuses the already-emitted credential; it must not pass by tautology if the
  keyfile rotates.
- NEG: session_status / MCP session_acquire_wait stay fail-closed with
  http_bytes_sent=0 even inside stale_policy_exemption(); authority type/tuple
  change stays fail-closed even under the exemption. Owned stop may lease
  internally only under owned_stop_kill_exemption().
- INCONCLUSO: in-game idle-timeout at ~20 min (c261). Not simulated here.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import accredited_daemon_transport, dayz_test_tool
from dayz_mcp.session_coordination import command_requires_lease
from tests.test_bug046_lease_queue_liveness import parse_dpf_table
from tests.test_control_client import _policy
from tests.test_dayz_test_tool import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy as _project_policy,
    _sealed,
    _terminal,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALLER_SESSION = "12345678-1234-4234-8234-1234567890ab"


def _identity(control, label: str):
    return control.ControlIdentity(
        platform="unknown",
        pid=123,
        ppid=45,
        started_at_utc="2026-07-22T00:00:00Z",
        session_id=_CALLER_SESSION,
        task_label=label,
    )


def _fail_after(limit: int):
    revalidations = 0

    def revalidate() -> None:
        nonlocal revalidations
        revalidations += 1
        if revalidations > limit:
            raise ValueError("daemon_provenance_conflict")

    return revalidate


class H14SpecContractTests(unittest.TestCase):
    def test_product_spec_has_h14_and_h10_amendment(self) -> None:
        markdown = (_REPO_ROOT / "product-spec.md").read_text(encoding="utf-8")
        rows = parse_dpf_table(
            markdown, "### H — Coordinación segura de sesiones de agentes"
        )
        self.assertIn("H14", rows)
        h14 = rows["H14"]["verification"]
        self.assertIn("client_policy_untrusted_open_new_session", h14)
        self.assertIn("_caller_owns_run", h14)
        self.assertIn("ControlClient._request_once", h14)
        self.assertIn("daemon_credential._revalidate_authority", h14)
        self.assertIn("_assert_authority_unchanged", h14)
        self.assertIn("session_acquire_wait", h14)
        self.assertIn("http_bytes_sent=0", h14)
        h10 = rows["H10"]["verification"]
        self.assertIn("La excepción H14 no autoriza emitir el primer byte HTTP", h10)
        self.assertIn("key_disclosed", h10)
        self.assertIn("sigue 0", h10)


class H14ControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_exempted_lifecycle_status_sends_issued_credential(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            requests.append(dict(kwargs))
            return 200, b'{"runs":[]}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            client = control.ControlClient(
                policy=policy, identity=_identity(control, "h14-lifecycle-pos")
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with client.stale_policy_exemption():
                    payload = await client.lifecycle_status()

        self.assertEqual(payload, {"runs": []})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/lifecycle/status")
        self.assertEqual(requests[0]["key"], "fixture-key")

    async def test_session_status_stays_fail_closed_without_exemption(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal requests
            requests += 1
            return 200, b'{"status":"ok"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            client = control.ControlClient(
                policy=policy, identity=_identity(control, "h14-session-neg")
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with self.assertRaises(control.ControlClientError) as raised:
                    await client.session_status()

        self.assertEqual(
            raised.exception.code, "client_policy_untrusted_open_new_session"
        )
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertEqual(requests, 0)

    async def test_session_acquire_wait_is_not_exempted(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal requests
            requests += 1
            return 200, b'{"status":"queued"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            client = control.ControlClient(
                policy=policy, identity=_identity(control, "h14-acquire-neg")
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with client.stale_policy_exemption():
                    with self.assertRaises(control.ControlClientError) as raised:
                        await client.session_acquire_wait("dayz-test", max_wait_s=0.5)

        self.assertEqual(
            raised.exception.code, "client_policy_untrusted_open_new_session"
        )
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertEqual(requests, 0)

    async def test_owned_stop_kill_exemption_allows_internal_lease_only(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests: list[str] = []
        operation_id = "11111111-1111-4111-8111-111111111111"

        def request(**kwargs: object) -> tuple[int, bytes]:
            path = str(kwargs["path"])
            requests.append(path)
            if path == "/session/enqueue":
                return 200, (
                    b'{"status":"queued","ticket":"ticket-h14","position":1,'
                    b'"operation_id":"' + operation_id.encode("ascii") + b'"}'
                )
            if path == "/session/wait":
                return 200, (
                    b'{"status":"active","ticket":"ticket-h14","lease_token":"lease-h14",'
                    b'"lease_id":"id-h14","operation_id":"'
                    + operation_id.encode("ascii")
                    + b'"}'
                )
            if path == "/session/status":
                return 200, b'{"status":"ok"}'
            return 200, b'{"runs":[]}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            client = control.ControlClient(
                policy=policy, identity=_identity(control, "h14-kill-lease")
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(operation_id)
            ):
                with client.owned_stop_kill_exemption():
                    granted = await client.session_acquire_wait(
                        "dayz-test", max_wait_s=0.5
                    )
                    with self.assertRaises(control.ControlClientError) as raised:
                        await client.session_status()

        self.assertEqual(granted["status"], "active")
        self.assertEqual(granted["lease_token"], "lease-h14")
        self.assertEqual(requests, ["/session/enqueue", "/session/wait"])
        self.assertEqual(
            raised.exception.code, "client_policy_untrusted_open_new_session"
        )
        self.assertEqual(raised.exception.http_bytes_sent, 0)

    async def test_authority_type_change_stays_fail_closed_under_exemption(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests: list[dict[str, object]] = []

        def request(**kwargs: object) -> tuple[int, bytes]:
            requests.append(dict(kwargs))
            return 200, b'{"runs":[]}'

        class PolicySubstitute:
            pass

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            provider = control.daemon_credential.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            client = control.ControlClient(
                policy=policy,
                identity=_identity(control, "h14-authority-neg"),
                credential_provider=provider,
            )
            substitute = PolicySubstitute()
            for attribute in (
                "kind",
                "host",
                "port",
                "keyfile",
                "native_executable",
                "argv",
                "cwd",
                "security_build_id",
                "authority_sha256",
            ):
                setattr(substitute, attribute, getattr(policy, attribute))
            provider.policy = substitute
            with self.assertRaises(control.ControlClientError) as raised:
                with client.stale_policy_exemption():
                    await client.lifecycle_status()

        self.assertEqual(
            raised.exception.code, "client_policy_untrusted_open_new_session"
        )
        self.assertEqual(raised.exception.request_stage, "pre_request")
        self.assertEqual(raised.exception.http_bytes_sent, 0)
        self.assertEqual(requests, [])


class H14DaemonCredentialTests(unittest.TestCase):
    def test_401_refresh_proceeds_when_stale_policy_allowed(self) -> None:
        credential_module = importlib.import_module("dayz_mcp.daemon_credential")
        issued = "fixture-credential-a"
        rotated = "fixture-credential-b"
        calls: list[dict[str, object]] = []
        refresh_reads = 0

        def request(**kwargs: object) -> tuple[int, bytes]:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                return 401, b'{"error":"unauthorized"}'
            return 200, b'{"ok":true}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text(issued + "\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(2))
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            keyfile.write_text(rotated + "\n", encoding="utf-8")
            real_reader = credential_module.pinned_keyfile.read_pinned_keyfile

            def counted_reader(path: str) -> str:
                nonlocal refresh_reads
                refresh_reads += 1
                return real_reader(path)

            with patch.object(
                credential_module.pinned_keyfile,
                "read_pinned_keyfile",
                side_effect=counted_reader,
            ):
                status, body = provider.request_with_refresh(
                    method="GET",
                    path="/enqueue",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                    allow_stale_policy=True,
                )

        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["key"], issued)
        self.assertEqual(calls[1]["key"], issued)
        self.assertNotEqual(calls[1]["key"], rotated)
        self.assertEqual(refresh_reads, 0)

    def test_401_refresh_fail_closed_without_exemption(self) -> None:
        credential_module = importlib.import_module("dayz_mcp.daemon_credential")
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            return 401, b'{"error":"unauthorized"}'

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential-a\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(2))
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            with self.assertRaises(credential_module.CredentialRefreshError) as raised:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                )

        self.assertEqual(
            raised.exception.code, "client_policy_untrusted_open_new_session"
        )
        self.assertEqual(calls, 1)

    def test_identity_unverified_retry_keeps_authority_gate(self) -> None:
        credential_module = importlib.import_module("dayz_mcp.daemon_credential")
        calls = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            raise accredited_daemon_transport.AccreditedTransportError(
                "daemon_identity_unverified",
                request_stage="pre_request",
                http_bytes_sent=0,
            )

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-credential\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(2))
            provider = credential_module.RefreshingDaemonCredential(
                policy=policy,
                request_fn=request,
            )
            with self.assertRaises(credential_module.CredentialRefreshError) as raised:
                provider.request_with_refresh(
                    method="GET",
                    path="/status",
                    query={},
                    body=None,
                    headers={},
                    deadline=1234.5,
                    allow_stale_policy=True,
                )

        self.assertEqual(
            raised.exception.code,
            "daemon_reaccreditation_failed_open_new_session",
        )
        self.assertEqual(calls, 2)


class H14ClientRuntimeFlagTests(unittest.TestCase):
    def test_h3_read_is_not_lease_gated(self) -> None:
        self.assertFalse(command_requires_lease("query_player_state"))
        self.assertFalse(command_requires_lease("camera_get"))
        self.assertTrue(command_requires_lease("world_spawn"))
        self.assertTrue(command_requires_lease("session_acquire_wait"))

    def test_request_once_forwards_stale_policy_flag(self) -> None:
        server = importlib.import_module("dayz_mcp.server")
        recorded: list[bool] = []

        class Provider:
            def request_with_refresh(self, **kwargs: object) -> tuple[int, bytes]:
                recorded.append(bool(kwargs.get("allow_stale_policy")))
                return 200, b'{"id":1}'

        runtime = object.__new__(server.ClientRuntime)
        runtime._allow_stale_policy = False
        runtime._credential_provider = Provider()
        runtime._time_fn = lambda: 1.0
        runtime._request_once("POST", "/enqueue", {"cmd": "world_spawn"}, None, 1.0)
        runtime._allow_stale_policy = True
        runtime._request_once(
            "POST", "/enqueue", {"cmd": "query_player_state"}, None, 1.0
        )
        self.assertEqual(recorded, [False, True])


class _H14Control:
    def __init__(self) -> None:
        self.allow = False
        self.kill = False

    @contextmanager
    def stale_policy_exemption(self):
        previous = self.allow
        self.allow = True
        try:
            yield
        finally:
            self.allow = previous

    @contextmanager
    def owned_stop_kill_exemption(self):
        previous = self.kill
        self.kill = True
        try:
            yield
        finally:
            self.kill = previous


class H14OwnedStopWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_stop_keeps_exemption_for_kill_path(self) -> None:
        lfv = _project_policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        run = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "owner_session_id": _CALLER_SESSION,
        }
        runtime = _Runtime({"runs": [run]})
        runtime.identity = types.SimpleNamespace(session_id=_CALLER_SESSION)
        generic: list[bool] = []
        kill_path: list[bool] = []
        runtime._control = _H14Control()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            generic.append(runtime._control.allow)
            kill_path.append(runtime._control.kill)
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        # Instance-wide stale_policy_exemption must not wrap execute_request
        # (that would also open MCP session_acquire_wait). The kill path keeps
        # its own lease exemption.
        self.assertEqual(generic, [False])
        self.assertEqual(kill_path, [True])

    async def test_unowned_stop_does_not_keep_exemption_for_kill_path(self) -> None:
        lfv = _project_policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        run = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "owner_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }
        runtime = _Runtime({"runs": [run]})
        runtime.identity = types.SimpleNamespace(session_id=_CALLER_SESSION)
        generic: list[bool] = []
        kill_path: list[bool] = []
        runtime._control = _H14Control()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            generic.append(runtime._control.allow)
            kill_path.append(runtime._control.kill)
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(generic, [False])
        self.assertEqual(kill_path, [False])


if __name__ == "__main__":
    unittest.main()
