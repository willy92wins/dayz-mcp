"""The STARTING cancel direct route through a real FastMCP session and daemon.

#364's public-surface tests call FastMCP.app.call_tool outside a request, so
Context.request_context raises ValueError and report_dayz_progress returns
before it looks up the stage. A live MCP session (create_connected_server_and_client_session)
sets that context. The stage the direct route reports is "stopping", which was
missing from the progress map: KeyError, then the public tool falls back to
the locked forced_kill path.

This test starts a real daemon subprocess (its own coordinator, lease and run
store; no fixture lease token) and drives dayz_test_run/stop through that
session. The sealed launcher bundle does not ship in CI, so only the host
open of the bundle and the native transport are substituted; the worker posts
the tool-built request to the live daemon. The daemon's ProcessLifecycle uses
a file-gated host launcher so STARTING stays open; no DayZ process is started.

The test must fail on main 32a7c60f (fallback KeyError / forced_kill) and
pass when report_dayz_progress accepts "stopping".
"""
from __future__ import annotations

import asyncio
import hashlib
import contextlib
import io
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.shared.memory import create_connected_server_and_client_session

from dayz_mcp import (
    dayz_test_tool,
    host_config,
    native_broker_protocol,
    native_launcher_backend,
    request_path_authority,
    server,
)
from dayz_mcp.daemon_contract import build_daemon_argv, daemon_runtime_cwd
from dayz_mcp.dayz_test_request import RequestProjectPolicy
from dayz_mcp.server import ServerConfig
from tests._tiers import slow_test
from tests.daemon_helpers import _http
from tests.dayz_test_tool_helpers import RUN_ID, _Bundle, _Opened, _terminal


_TOOLS_DIR = Path(__file__).resolve().parents[1]
_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "starting_cancel_direct"
_OFFICIAL_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "dayz_mcp"
_INSTANCE = "sckeyerr"
_OP_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"



def _complete_start_request(
    request: dict[str, object], *, game: Path, mission: Path
) -> dict[str, object]:
    """Fill the fields ProcessLifecycle requires of a new launch request."""
    completed = dict(request)
    if completed.get("run_id") is not None:
        return completed
    exe = game / "DayZDiag_x64.exe"
    completed["role"] = (
        "server" if completed.get("mode") in {"server", "all"} else "offline"
    )
    mod = completed.get("mod") or "StorageMod"
    completed["mod"] = "@" + str(mod).lstrip("@")
    completed["label"] = str(completed["mod"]) + " " + str(completed["role"])
    completed["window_style"] = "normal"
    completed["cwd"] = str(game)
    # ProcessLifecycle gates Steam on argv, not the role label. Without
    # -server, a mode=server request still requires a live Steam session.
    # CI (windows-latest, no Steam) refused /lifecycle/start as
    # steam_session_stale before the file-gated hold launcher ran.
    argv = [str(exe), "-mission=" + str(mission)]
    if completed["role"] == "server":
        argv.append("-server")
    completed["argv"] = argv
    completed["profiles"] = str(game / "profiles-sckeyerr")
    completed["mission"] = str(mission)
    completed["auto_remediate_steam"] = False
    completed["storage_seal"] = "a" * 64
    completed["new_run_id"] = RUN_ID
    completed["launch_operation_id"] = _OP_ID
    completed.pop("launch_request_sha256", None)
    canonical = json.dumps(
        completed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    completed["launch_request_sha256"] = hashlib.sha256(canonical).hexdigest()
    return completed


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _raw_status(port: int, key: str) -> dict[str, object] | None:
    url = (
        "http://127.0.0.1:" + str(port) + "/status?key="
        + urllib.parse.quote(key, safe="")
    )
    try:
        with urllib.request.urlopen(url, timeout=0.25) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return value if isinstance(value, dict) else None


def _wait_ready(child: subprocess.Popen[str], *, port: int, key: str) -> dict[str, object]:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        payload = _raw_status(port, key)
        if payload is not None:
            return payload
        if child.poll() is not None:
            break
        time.sleep(0.025)
    stderr = ""
    if child.poll() is not None:
        _stdout, stderr = child.communicate(timeout=2.0)
    raise AssertionError(
        "isolated daemon did not become ready; returncode="
        + str(child.poll())
        + "; stderr="
        + stderr
    )


def _tool_payload(result: object) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise AssertionError("MCP tool returned no object content: " + repr(result))
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise AssertionError("MCP tool returned no text content: " + repr(result))
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AssertionError("MCP tool returned non-object content")
    return payload


def _surface_policy(root: Path) -> RequestProjectPolicy:
    dev = root / "dev"
    # FINDING-launch-modroot: the launch also accredits what the roles load,
    # the chernarus alias and the implicit @StorageMod among them.
    (dev / "_server" / "mpmissions" / "dayzOffline.chernarusplus").mkdir(
        parents=True
    )
    source = dev / "staged-source" / "StorageMod"
    source.mkdir(parents=True)
    mods = root / "mods"
    (mods / "@CF").mkdir(parents=True)
    (mods / "@StorageMod").mkdir(parents=True)
    (mods / "@DayZ_MCP").mkdir(parents=True)
    return RequestProjectPolicy(
        mod="StorageMod",
        dev_root=str(dev),
        default_source=str(source),
        default_base_mods=("@CF",),
        mission_roots=(str(dev / "_server" / "mpmissions"),),
        mod_roots=(str(mods),),
    )


def _write_client_home(profile: Path, runtime_python: Path, port: int, keyfile: Path) -> None:
    args = [
        "-m",
        "dayz_mcp",
        "--client",
        "--port",
        str(port),
        "--keyfile",
        str(keyfile),
        "--idle-timeout",
        "120.0",
        "--no-daemon-autospawn",
        "--client-platform",
        "codex",
        "--instance",
        _INSTANCE,
    ]
    (profile / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "dayz-mcp": {
                        "type": "stdio",
                        "command": str(runtime_python),
                        "args": args,
                        "timeout": 604_800_000,
                    }
                }
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    codex = profile / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "config.toml").write_text(
        "\n".join(
            [
                "[mcp_servers.dayz-mcp]",
                "command = " + json.dumps(str(runtime_python)),
                "args = " + json.dumps(args),
                "tool_timeout_sec = 604800",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _wait_file(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def _audit_has_lifecycle_stop(audit: Path) -> bool:
    if not audit.is_file():
        return False
    try:
        text = audit.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return '"event": "lifecycle_stop"' in text or '"event":"lifecycle_stop"' in text


class DirectRouteStartingCancelLiveDaemonTest(unittest.TestCase):
    @slow_test
    def test_public_stop_during_starting_returns_cancelled_during_start(self) -> None:
        runtime_python = _TOOLS_DIR / ".venv-mcp" / "Scripts" / "python.exe"
        if not runtime_python.is_file():
            self.skipTest("project MCP runtime is not installed")
        runtime_python = runtime_python.resolve(strict=True)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            hold = root / "hold"
            hold.mkdir()
            release = hold / "release"
            entered = hold / "entered"
            keyfile = (root / "daemon.key").resolve()
            key = "sckeyerr-direct-route-key"
            keyfile.write_text(key + "\n", encoding="utf-8")
            port = _unused_port()
            profile = root / "profile"
            profile.mkdir()
            _write_client_home(profile, runtime_python, port, keyfile)
            local = root / "local"
            local.mkdir()
            migration = root / "migration"
            migration.mkdir()
            policy_root = root / "surface"
            policy = _surface_policy(policy_root)
            sealed = request_path_authority._seal_project_policy_for_test(policy)
            game = root / "DayZ"
            game.mkdir()
            (game / "DayZDiag_x64.exe").write_bytes(b"MZ")
            (game / "profiles-sckeyerr").mkdir()
            mission = root / "mpmissions" / "dayzOffline.chernarusplus"
            mission.mkdir(parents=True)

            daemon_config = SimpleNamespace(
                port=port,
                keyfile=str(keyfile),
                idle_timeout_s=120.0,
                expected_game_version=None,
                require_version=False,
                enable_exec_enforce=False,
                instance_token=_INSTANCE,
                game_path=str(game),
            )
            daemon_argv = build_daemon_argv(daemon_config, python=str(runtime_python))
            cwd = str(Path(daemon_runtime_cwd()).resolve(strict=True))
            environment = os.environ.copy()
            environment["USERPROFILE"] = str(profile)
            environment["HOME"] = str(profile)
            environment["LOCALAPPDATA"] = str(local)
            inherited = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(_FIXTURE_SITE), str(_OFFICIAL_FIXTURE_SITE)]
            )
            if inherited:
                environment["PYTHONPATH"] += os.pathsep + inherited
            environment["DAYZ_MCP_FIXTURE_MODE"] = "none"
            environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(root / "unused")
            environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(migration)
            environment["DAYZ_MCP_HOLD_LAUNCH_DIR"] = str(hold)
            environment["DAYZ_MCP_HOST_TOOLS"] = str(_TOOLS_DIR)

            daemon_out = (root / "daemon.stdout.log").open("w", encoding="utf-8")
            daemon_err = (root / "daemon.stderr.log").open("w", encoding="utf-8")
            child = subprocess.Popen(
                daemon_argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=daemon_out,
                stderr=daemon_err,
                text=True,
            )
            logs: list[str] = []

            def capture(message: str) -> None:
                logs.append(message)

            def transport(**kwargs: object) -> tuple[int, bytes]:
                host = str(kwargs["host"])
                posted_port = int(kwargs["port"])
                posted_key = str(kwargs["key"])
                path = str(kwargs["path"])
                method = str(kwargs["method"])
                query = dict(kwargs.get("query") or {})
                query["key"] = posted_key
                url = (
                    "http://"
                    + host
                    + ":"
                    + str(posted_port)
                    + path
                    + "?"
                    + urllib.parse.urlencode(query)
                )
                request = urllib.request.Request(
                    url,
                    data=kwargs.get("body"),
                    headers=dict(kwargs.get("headers") or {}),
                    method=method,
                )
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        with urllib.request.urlopen(request, timeout=30.0) as response:
                            return int(response.status), response.read()
                except urllib.error.HTTPError as exc:
                    try:
                        return int(exc.code), exc.read()
                    finally:
                        exc.close()

            async def launch_worker(
                opened_launcher: object,
                *,
                verified_bundle: object,
                canonical_request: bytes,
                lease_token: str,
                identity_json: str,
                daemon_policy_json: str,
                cancel_event: object,
                output_sink: object = None,
                **_kwargs: object,
            ) -> int:
                del opened_launcher, verified_bundle, daemon_policy_json, cancel_event
                frame = native_broker_protocol.decode_request(canonical_request)
                request = _complete_start_request(
                    json.loads(frame.stdin.decode("utf-8")),
                    game=game,
                    mission=mission,
                )
                identity = json.loads(identity_json)
                def post_start() -> dict[str, object]:
                    _status, body = _http(
                        "http://127.0.0.1:" + str(port),
                        "POST",
                        "/lifecycle/start",
                        key,
                        payload={
                            "identity": identity,
                            "lease_token": lease_token,
                            "request": request,
                        },
                        timeout=60.0,
                    )
                    return body if isinstance(body, dict) else {}

                result = await asyncio.to_thread(post_start)
                ok = result.get("ok") is True
                raw_error = result.get("error")
                if ok:
                    cleanup_degraded = False
                    error_code = None
                    terminal_run_id = result.get("run_id")
                elif raw_error in {
                    "launch_cancelled",
                    "launch_cancel_pending",
                    "launch_cancel_degraded",
                }:
                    cleanup_degraded = bool(result.get("cleanup_degraded"))
                    error_code = raw_error
                    terminal_run_id = result.get("run_id") if cleanup_degraded else None
                else:
                    # A start that never created a run must publish a failed
                    # terminal (cleanup_degraded=False, run_id=None). Setting
                    # cleanup_degraded with a missing run_id is terminal_invalid
                    # and hides the daemon reason.
                    cleanup_degraded = False
                    error_code = "launch_cancel_degraded"
                    terminal_run_id = None
                    logs.append(
                        "lifecycle_start_error="
                        + repr(raw_error)
                        + " result_keys="
                        + repr(sorted(result))
                    )
                if callable(output_sink):
                    output_sink(
                        "stdout",
                        _terminal(
                            {
                                "cleanup_degraded": cleanup_degraded,
                                "error_code": error_code,
                                "exit_code": 0 if ok else 1,
                                "ok": ok,
                                "run_id": terminal_run_id,
                            }
                        ),
                    )
                return 0 if ok else 1

            async def not_stale(*_args: object) -> bool:
                return False

            try:
                _wait_ready(child, port=port, key=key)
                config = ServerConfig(
                    mode="client",
                    key=key,
                    keyfile=str(keyfile),
                    port=port,
                    client_platform="codex",
                    auto_spawn_daemon=False,
                    log_sink=capture,
                    idle_timeout_s=120.0,
                    instance_token=_INSTANCE,
                    game_path=str(game),
                )
                launcher_path = str(runtime_python)
                provenance = host_config.DaemonProvenance(
                    launch_executable=launcher_path,
                    native_executable=launcher_path,
                    argv=tuple(daemon_argv),
                    cwd=cwd,
                    port=port,
                    keyfile=str(keyfile),
                    auto_spawn_daemon=False,
                    instance_token=_INSTANCE,
                    game_path=str(game),
                )
                env_patch = {
                    "USERPROFILE": str(profile),
                    "HOME": str(profile),
                    "LOCALAPPDATA": str(local),
                }
                with (
                    patch.dict(os.environ, env_patch, clear=False),
                    patch.object(
                        host_config,
                        "resolve_daemon_provenance",
                        return_value=provenance,
                    ),
                    patch.object(
                        host_config,
                        "_local_launch_executable",
                        return_value=launcher_path,
                    ),
                    patch.object(
                        host_config,
                        "_local_native_executable",
                        return_value=launcher_path,
                    ),
                    patch.object(
                        server.orphan_guard,
                        "verified_daemon_http_request",
                        side_effect=transport,
                    ),
                    patch.object(
                        server, "_observe_caller_tool_registry_stale", new=not_stale
                    ),
                    patch.object(
                        dayz_test_tool, "open_approved_launcher", return_value=_Opened()
                    ),
                    patch.object(
                        dayz_test_tool.secure_launcher,
                        "load_verified_bundle",
                        return_value=_Bundle((sealed,)),
                    ),
                    patch.object(
                        dayz_test_tool,
                        "preflight_bridge_contract_request",
                        return_value=(),
                    ),
                    patch.object(
                        dayz_test_tool,
                        "preflight_vpp_request",
                        return_value=SimpleNamespace(
                            error_code=None, missing=[], warnings=[]
                        ),
                    ),
                    patch.object(
                        native_launcher_backend,
                        "launch_registered_native",
                        side_effect=launch_worker,
                    ),
                ):
                    app, _built = server.build_app(config)
                    audit = (
                        local
                        / ("DayZ_MCP_" + _INSTANCE)
                        / "audit"
                        / "events.jsonl"
                    )

                    async def exercise() -> None:
                        async with create_connected_server_and_client_session(
                            app._mcp_server
                        ) as session:
                            run_task = asyncio.create_task(
                                session.call_tool(
                                    "dayz_test_run",
                                    {"project": "StorageMod", "mode": "server"},
                                )
                            )
                            entered_ok = await asyncio.to_thread(_wait_file, entered, 15.0)
                            if not entered_ok:
                                err_text = ""
                                out_text = ""
                                try:
                                    daemon_err.flush()
                                    daemon_out.flush()
                                    err_text = (root / "daemon.stderr.log").read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                    out_text = (root / "daemon.stdout.log").read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                except OSError:
                                    pass
                                run_state = "pending"
                                if run_task.done():
                                    try:
                                        run_state = repr(run_task.result())
                                    except Exception as exc:
                                        run_state = "exc=" + repr(exc)
                                audit_tail = ""
                                if audit.is_file():
                                    audit_tail = audit.read_text(
                                        encoding="utf-8", errors="replace"
                                    )[-2000:]
                                self.fail(
                                    "daemon launcher never entered STARTING hold; logs="
                                    + repr(logs[-30:])
                                    + " daemon="
                                    + str(child.poll())
                                    + " run_task="
                                    + run_state
                                    + " stdout="
                                    + out_text[-4000:]
                                    + " stderr="
                                    + err_text[-4000:]
                                    + " hold="
                                    + repr(sorted(p.name for p in hold.iterdir()))
                                    + " audit="
                                    + audit_tail
                                )
                            stop_task = asyncio.create_task(
                                session.call_tool("dayz_test_stop", {})
                            )
                            deadline = time.monotonic() + 8.0
                            cancel_seen = False
                            while time.monotonic() < deadline:
                                if _audit_has_lifecycle_stop(audit):
                                    cancel_seen = True
                                    break
                                if any("direct route fallback" in item for item in logs):
                                    break
                                await asyncio.sleep(0.05)
                            release.write_text("1", encoding="ascii")
                            stop_result = await asyncio.wait_for(stop_task, timeout=20.0)
                            run_result = await asyncio.wait_for(run_task, timeout=20.0)
                            fallback = [
                                item
                                for item in logs
                                if "direct route fallback" in item
                            ]
                            self.assertFalse(
                                fallback,
                                "direct route fell back instead of cancelling during STARTING: "
                                + repr(fallback)
                                + " stop="
                                + repr(stop_result),
                            )
                            if getattr(stop_result, "isError", False):
                                self.fail("dayz_test_stop returned an MCP error: " + repr(stop_result))
                            stop = _tool_payload(stop_result)
                            self.assertEqual(stop.get("status"), "succeeded", stop)
                            self.assertEqual(
                                stop.get("stop_method"),
                                "cancelled_during_start",
                                stop,
                            )
                            run_payload = _tool_payload(run_result) if not getattr(run_result, "isError", False) else {}
                            if run_payload:
                                self.assertEqual(
                                    run_payload.get("error_code"),
                                    "launch_cancelled",
                                    run_payload,
                                )

                    asyncio.run(exercise())
            finally:
                if not release.exists():
                    release.write_text("1", encoding="ascii")
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=8.0)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5.0)
                for handle in (daemon_out, daemon_err):
                    try:
                        handle.close()
                    except Exception:
                        pass
                dummy_pid_text = entered.read_text(encoding="ascii") if entered.exists() else ""
                if dummy_pid_text.strip().isdigit():
                    dummy_pid = int(dummy_pid_text.strip())
                    try:
                        os.kill(dummy_pid, 15)
                    except OSError:
                        pass


if __name__ == "__main__":
    unittest.main()
