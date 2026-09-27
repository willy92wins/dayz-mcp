"""H14: H3 reads and owned dayz_test_stop survive policy.revalidate() failure.

R26 fixtures:
- POS: exempted ControlClient lifecycle_status and credential 401 retry send HTTP
  when revalidate fails and the authority tuple is unchanged. The 401 retry
  reuses the already-emitted credential; it must not pass by tautology if the
  keyfile rotates. Live D4 shape: ownerless RUNNING_IDLE (RunRecord clears
  owner_session_id) is H14(b) owned stop via H11 adopt-then-kill.
- NEG: MCP session_acquire_wait stays fail-closed with http_bytes_sent=0 even
  inside stale_policy_exemption(); generic exemption does not open
  /session/status. Authority type/tuple change stays fail-closed. A foreign
  owner stays fail-closed. Owned stop may lease internally under
  owned_stop_kill_exemption(), including the post-kill
  protected_release_and_verify hop. That hop runs the real
  execute_secure_launcher_request forward of control_client and the MCP
  tool_lock shared by dayz_test_stop / session_status / session_acquire_wait.
- INCONCLUSO: in-game idle-timeout at ~20 min (c261). Not simulated here.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import sys
import tempfile
import textwrap
import types
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import accredited_daemon_transport, dayz_test_tool, native_launcher_backend, server
from dayz_mcp.session_coordination import command_requires_lease
from tests.lease_helpers import parse_dpf_table
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
_TERMINAL_SESSION_STATUS = (
    b'{"self":{"state":"none","position":null},"pending_commands":0}'
)


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


def _h14_stop_workspace(root: Path):
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
    return authority._seal_project_policy_for_test(policy)


def _h14_is_client_tool_lock(expr: ast.AST) -> bool:
    return (
        isinstance(expr, ast.Attribute)
        and expr.attr == "tool_lock"
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "client"
    )


def _h14_iter_excluding_nested_defs(root: ast.AST):
    stack = [root]
    skip_nested = False
    while stack:
        node = stack.pop()
        if skip_nested and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        skip_nested = True
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _h14_awaited_body_inside_client_tool_lock(fn: ast.AsyncFunctionDef) -> bool:
    for node in _h14_iter_excluding_nested_defs(fn):
        if not isinstance(node, ast.AsyncWith):
            continue
        if not any(_h14_is_client_tool_lock(item.context_expr) for item in node.items):
            continue
        if any(
            isinstance(child, ast.Await)
            for child in _h14_iter_excluding_nested_defs(node)
        ):
            return True
    return False


def _h14_build_app_mcp_tool(tree: ast.AST, name: str) -> ast.AsyncFunctionDef | None:
    build = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_app"
    )
    for node in build.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _h14_nested_mcp_tool_holds_tool_lock(
    name: str, source: str | None = None
) -> bool:
    if source is None:
        path = Path(server.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    else:
        tree = ast.parse(source)
    fn = _h14_build_app_mcp_tool(tree, name)
    if fn is None:
        return False
    return _h14_awaited_body_inside_client_tool_lock(fn)


class _H14ClientRuntime(server.ClientRuntime):
    """ClientRuntime surface without host provenance so MCP wrappers apply."""

    def __init__(
        self,
        *,
        control: object,
        daemon_policy: object,
        identity: object,
    ) -> None:
        self.tool_lock = asyncio.Lock()
        self._control = control
        self.daemon_policy = daemon_policy
        self.identity = identity
        self._log = lambda _message: None
        self.session_acquire_via_runtime = 0
        self.session_status_exemptions: list[bool] = []

    async def session_acquire_wait(self, *args: object, **kwargs: object):
        self.session_acquire_via_runtime += 1
        return await super().session_acquire_wait(*args, **kwargs)

    async def session_status(self) -> dict[str, object]:
        self.session_status_exemptions.append(
            bool(getattr(self._control, "_allow_owned_stop_lease", False))
        )
        return await super().session_status()


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

    async def test_session_status_stays_fail_closed_under_stale_policy_exemption(
        self,
    ) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        requests = 0

        def request(**_kwargs: object) -> tuple[int, bytes]:
            nonlocal requests
            requests += 1
            return 200, _TERMINAL_SESSION_STATUS

        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            policy = _policy(keyfile)
            object.__setattr__(policy, "_revalidation_hook", _fail_after(3))
            client = control.ControlClient(
                policy=policy, identity=_identity(control, "h14-status-generic-neg")
            )
            with patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with client.stale_policy_exemption():
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
                return 200, _TERMINAL_SESSION_STATUS
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
                    status = await client.session_status()

        self.assertEqual(granted["status"], "active")
        self.assertEqual(granted["lease_token"], "lease-h14")
        self.assertEqual(
            requests, ["/session/enqueue", "/session/wait", "/session/status"]
        )
        self.assertEqual(status["self"]["state"], "none")

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


async def _mcp_untrusted_dayz_test_stop(run: dict[str, object]):
    """MCP dayz_test_stop while policy.revalidate() already fails.

    Live D4 shape uses ownerless RUNNING_IDLE. Returns result dict on
    success or the raised BaseException, plus request paths and native
    launch count. Goes red if owned untrusted stop is fail-closed.
    """
    control = importlib.import_module("dayz_mcp.control_client")
    requests: list[str] = []
    operation_id = "11111111-1111-4111-8111-111111111111"
    native_launches = 0
    outcome: object = None

    with tempfile.TemporaryDirectory() as temporary:
        sealed = _h14_stop_workspace(Path(temporary))
        run = dict(run)
        run.setdefault("mod", "@" + sealed.policy.mod)

        def request(**kwargs: object) -> tuple[int, bytes]:
            path = str(kwargs["path"])
            requests.append(path)
            if path == "/lifecycle/status":
                return 200, json.dumps({"runs": [run]}).encode("ascii")
            if path == "/session/enqueue":
                return 200, json.dumps(
                    {
                        "status": "queued",
                        "ticket": "ticket-h14",
                        "position": 1,
                        "operation_id": operation_id,
                    }
                ).encode("ascii")
            if path == "/session/wait":
                return 200, json.dumps(
                    {
                        "status": "active",
                        "ticket": "ticket-h14",
                        "lease_token": "lease-h14",
                        "lease_id": "id-h14",
                        "operation_id": operation_id,
                    }
                ).encode("ascii")
            if path == "/session/release":
                return 200, b'{"status":"released"}'
            if path == "/session/heartbeat":
                return 200, b'{"status":"active"}'
            if path == "/session/status":
                return 200, _TERMINAL_SESSION_STATUS
            return 200, b'{"runs":[]}'

        keyfile = Path(temporary) / "daemon.key"
        keyfile.write_text("fixture-key\n", encoding="utf-8")
        http_policy = _policy(keyfile)
        client = control.ControlClient(
            policy=http_policy, identity=_identity(control, "h14-idle-untrusted")
        )
        object.__setattr__(http_policy, "_revalidation_hook", _fail_after(0))
        runtime = _H14ClientRuntime(
            control=client,
            daemon_policy=_policy(keyfile),
            identity=types.SimpleNamespace(session_id=_CALLER_SESSION),
        )
        config = server.ServerConfig(
            mode="client",
            key="fixture-key",
            port=8765,
            client_platform="unknown",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = server.build_app(config)
        stop_fn = app._tool_manager.get_tool("dayz_test_stop").fn

        async def launch_registered_native(*_args: object, **kwargs: object) -> int:
            nonlocal native_launches
            native_launches += 1
            sink = kwargs.get("output_sink")
            if not callable(sink):
                raise AssertionError("missing_native_output_sink")
            sink(
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
            return_value=_Bundle((sealed,)),
        ), patch.object(
            native_launcher_backend,
            "launch_registered_native",
            side_effect=launch_registered_native,
        ), patch.object(
            control.transport,
            "verified_daemon_http_request",
            side_effect=request,
        ), patch.object(
            control.uuid, "uuid4", return_value=uuid.UUID(operation_id)
        ):
            try:
                outcome = await stop_fn(run_id=RUN_ID)
            except BaseException as error:
                outcome = error

    return outcome, requests, native_launches


class H14NestedToolLockAstTests(unittest.TestCase):
    def test_head_mcp_tools_await_inside_client_tool_lock(self) -> None:
        for name in (
            "dayz_test_stop",
            "session_acquire_wait",
            "session_status",
        ):
            self.assertTrue(_h14_nested_mcp_tool_holds_tool_lock(name), name)

    def test_lock_mutants_m1_m2_m3_m4(self) -> None:
        canonical = textwrap.dedent(
            """
            def build_app():
                async def session_acquire_wait(purpose):
                    async with client.tool_lock:
                        return await client.session_acquire_wait(purpose)
            """
        )
        m1 = textwrap.dedent(
            """
            def build_app():
                async def session_acquire_wait(purpose):
                    async with client.tool_lock:
                        pass
                    return await client.session_acquire_wait(purpose)
            """
        )
        m2 = textwrap.dedent(
            """
            def build_app():
                async def session_acquire_wait(purpose):
                    async with client._control.tool_lock:
                        return await client.session_acquire_wait(purpose)
            """
        )
        m3 = textwrap.dedent(
            """
            def build_app():
                async def session_acquire_wait(purpose):
                    return await client.session_acquire_wait(purpose)
            """
        )
        m4 = textwrap.dedent(
            """
            def build_app():
                async def session_acquire_wait(purpose):
                    async def _dead():
                        async with client.tool_lock:
                            return await client.session_acquire_wait(purpose)
                    return await client.session_acquire_wait(purpose)
            """
        )
        self.assertTrue(
            _h14_nested_mcp_tool_holds_tool_lock("session_acquire_wait", canonical)
        )
        self.assertFalse(
            _h14_nested_mcp_tool_holds_tool_lock("session_acquire_wait", m1)
        )
        self.assertFalse(
            _h14_nested_mcp_tool_holds_tool_lock("session_acquire_wait", m2)
        )
        self.assertFalse(
            _h14_nested_mcp_tool_holds_tool_lock("session_acquire_wait", m3)
        )
        self.assertFalse(
            _h14_nested_mcp_tool_holds_tool_lock("session_acquire_wait", m4)
        )


def _h14_predicate_runtime():
    runtime = _Runtime({"runs": []})
    runtime.identity = types.SimpleNamespace(session_id=_CALLER_SESSION)
    return runtime


class H14OwnedStopPredicateTests(unittest.TestCase):
    def test_ownerless_idle_is_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        status = {"runs": [{"run_id": RUN_ID, "state": "RUNNING_IDLE"}]}
        self.assertTrue(dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID))

    def test_ownerless_unreconciled_is_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        status = {"runs": [{"run_id": RUN_ID, "state": "UNRECONCILED"}]}
        self.assertTrue(dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID))

    def test_matching_owner_is_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING",
                    "owner_session_id": _CALLER_SESSION,
                }
            ]
        }
        self.assertTrue(dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID))

    def test_foreign_owner_is_not_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING",
                    "owner_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                }
            ]
        }
        self.assertFalse(dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID))

    def test_missing_row_is_not_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        self.assertFalse(
            dayz_test_tool._h14_owned_stop(runtime, {"runs": []}, RUN_ID)
        )

    def test_ownerless_exited_is_not_h14_owned_stop(self) -> None:
        runtime = _h14_predicate_runtime()
        status = {"runs": [{"run_id": RUN_ID, "state": "EXITED"}]}
        self.assertFalse(dayz_test_tool._h14_owned_stop(runtime, status, RUN_ID))


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

    async def test_ownerless_idle_stop_keeps_exemption_for_kill_path(self) -> None:
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
        self.assertEqual(kill_path, [True])

    async def test_owned_stop_post_kill_status_verify_does_not_reject_tool(
        self,
    ) -> None:
        """H14-P1-03 + P2-13: real secure_launcher hop; MCP tool_lock excludes status."""
        for name in (
            "dayz_test_stop",
            "session_acquire_wait",
            "session_status",
        ):
            self.assertTrue(
                _h14_nested_mcp_tool_holds_tool_lock(name),
                name,
            )
        control = importlib.import_module("dayz_mcp.control_client")
        requests: list[str] = []
        operation_id = "11111111-1111-4111-8111-111111111111"
        inside_native = asyncio.Event()
        release_native = asyncio.Event()
        native_launches = 0

        with tempfile.TemporaryDirectory() as temporary:
            sealed = _h14_stop_workspace(Path(temporary))
            run = {
                "run_id": RUN_ID,
                "state": "RUNNING_IDLE",
                "mod": "@" + sealed.policy.mod,
                "owner_session_id": _CALLER_SESSION,
            }

            def request(**kwargs: object) -> tuple[int, bytes]:
                path = str(kwargs["path"])
                requests.append(path)
                if path == "/lifecycle/status":
                    return 200, json.dumps({"runs": [run]}).encode("ascii")
                if path == "/session/enqueue":
                    return 200, json.dumps(
                        {
                            "status": "queued",
                            "ticket": "ticket-h14",
                            "position": 1,
                            "operation_id": operation_id,
                        }
                    ).encode("ascii")
                if path == "/session/wait":
                    return 200, json.dumps(
                        {
                            "status": "active",
                            "ticket": "ticket-h14",
                            "lease_token": "lease-h14",
                            "lease_id": "id-h14",
                            "operation_id": operation_id,
                        }
                    ).encode("ascii")
                if path == "/session/release":
                    return 200, b'{"status":"released"}'
                if path == "/session/status":
                    return 200, _TERMINAL_SESSION_STATUS
                return 200, b'{"runs":[]}'

            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            http_policy = _policy(keyfile)
            client = control.ControlClient(
                policy=http_policy, identity=_identity(control, "h14-post-kill")
            )
            object.__setattr__(http_policy, "_revalidation_hook", _fail_after(0))
            runtime = _H14ClientRuntime(
                control=client,
                daemon_policy=_policy(keyfile),
                identity=types.SimpleNamespace(session_id=_CALLER_SESSION),
            )
            config = server.ServerConfig(
                mode="client",
                key="fixture-key",
                port=8765,
                client_platform="unknown",
                auto_spawn_daemon=False,
                log_sink=lambda _message: None,
            )
            with patch.object(server, "ClientRuntime", return_value=runtime):
                app, _built = server.build_app(config)
            stop_fn = app._tool_manager.get_tool("dayz_test_stop").fn
            status_fn = app._tool_manager.get_tool("session_status").fn

            async def launch_registered_native(*_args: object, **kwargs: object) -> int:
                nonlocal native_launches
                native_launches += 1
                self.assertTrue(runtime.tool_lock.locked())
                self.assertTrue(client._allow_owned_stop_lease)
                inside_native.set()
                await release_native.wait()
                sink = kwargs.get("output_sink")
                if not callable(sink):
                    raise AssertionError("missing_native_output_sink")
                sink(
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
                return_value=_Bundle((sealed,)),
            ), patch.object(
                native_launcher_backend,
                "launch_registered_native",
                side_effect=launch_registered_native,
            ), patch.object(
                control.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ), patch.object(
                control.uuid, "uuid4", return_value=uuid.UUID(operation_id)
            ):
                stop_task = asyncio.create_task(stop_fn(run_id=RUN_ID))
                await asyncio.wait_for(inside_native.wait(), timeout=5)
                self.assertTrue(runtime.tool_lock.locked())
                self.assertTrue(client._allow_owned_stop_lease)
                status_task = asyncio.create_task(status_fn())
                for _ in range(20):
                    if not status_task.done() and runtime.tool_lock.locked():
                        await asyncio.sleep(0)
                    else:
                        break
                self.assertFalse(status_task.done())
                self.assertTrue(runtime.tool_lock.locked())
                release_native.set()
                result = await stop_task
                with self.assertRaises(server.ToolError) as raised:
                    await status_task

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(native_launches, 1)
        self.assertEqual(runtime.session_acquire_via_runtime, 1)
        self.assertIn("/lifecycle/status", requests)
        self.assertIn("/session/release", requests)
        self.assertEqual(requests.count("/session/status"), 1)
        self.assertNotIn("/session/acquire", requests)
        self.assertIn(True, runtime.session_status_exemptions)
        self.assertEqual(runtime.session_status_exemptions[-1], False)
        self.assertIn(
            "client_policy_untrusted_open_new_session", str(raised.exception)
        )
        self.assertFalse(runtime.tool_lock.locked())

    async def test_owned_untrusted_stop_of_idle_run_does_not_reject_tool(
        self,
    ) -> None:
        """Live D4: ownerless RUNNING_IDLE must not fail-close under stale policy."""
        outcome, requests, native_launches = await _mcp_untrusted_dayz_test_stop(
            {"run_id": RUN_ID, "state": "RUNNING_IDLE"}
        )
        self.assertIsInstance(outcome, dict)
        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(native_launches, 1)
        self.assertIn("/lifecycle/status", requests)
        self.assertIn("/session/enqueue", requests)
        self.assertIn("/session/wait", requests)
        self.assertNotIn(
            "client_policy_untrusted_open_new_session",
            str(outcome),
        )

    async def test_foreign_untrusted_stop_stays_fail_closed(self) -> None:
        outcome, requests, native_launches = await _mcp_untrusted_dayz_test_stop(
            {
                "run_id": RUN_ID,
                "state": "RUNNING",
                "owner_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            }
        )
        self.assertIsInstance(outcome, server.ToolError)
        self.assertIn(
            "client_policy_untrusted_open_new_session", str(outcome)
        )
        self.assertEqual(native_launches, 0)
        self.assertIn("/lifecycle/status", requests)
        self.assertNotIn("/session/enqueue", requests)


if __name__ == "__main__":
    unittest.main()
