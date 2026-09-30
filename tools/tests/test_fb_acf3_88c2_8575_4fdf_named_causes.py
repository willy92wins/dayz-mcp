"""Startup and re-accreditation failures name their cause and the remedy that cures it.

- acf3 (fb-20260824-010234-acf3): daemon_reaccreditation_failed_open_new_session
  was raised at five sites with one remedy in its name, and opening a new session
  cures none of them: every session derives the same authority from the same
  registration. The code is unchanged; the error now carries
  reaccreditation_stage, one of a closed set taken from those raise sites, and
  the public text adds the remedy that holds for that stage.
- 88c2 part 3 (fb-20260823-144715-88c2): the doctor's diagnostic_failure names
  the check that raised and the exception class, never the exception message.
- 8575 (fb-20260823-040320-8575): a daemon started by an interpreter other than
  tools/.venv-mcp/Scripts/python.exe prints one line naming that path and exits
  with DAEMON_PYTHON_NOT_APPROVED, instead of a traceback and exit code 1.
- 4fdf (fb-20260930-171015-4fdf): the sealed worker's bare internal_failure
  names its stage where the result leaves the unsealed code, and a server whose
  own sources are stale or unknown sends the caller to server_reload or to a
  reopened MCP client.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import (
    accredited_daemon_transport,
    control_client,
    daemon,
    daemon_credential,
    dayz_test_tool,
    doctor,
    host_config,
    native_launcher_transaction,
    server,
)
from dayz_mcp.daemon_policy import AccreditedDaemonPolicy
from dayz_mcp.server import ServerConfig, build_app
from tests._tiers import slow_test
from tests._tree_identity import editable_mapped_tools_dir
from tests.client_helpers import _fixture_client_runtime
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy as _project_policy,
    _sealed,
    _terminal,
)
from tests.mcp_helpers import _content_json


_REACCREDITATION_FAILED = "daemon_reaccreditation_failed_open_new_session"
_DAEMON_FIXTURE_SITE = Path(__file__).resolve().parent / "fixtures" / "dayz_mcp"


def _daemon_policy(keyfile: Path) -> AccreditedDaemonPolicy:
    authority = {
        "argv": [
            r"P:\Runtime\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ],
        "cwd": r"P:\DayZ_MCP_dev\tools",
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": r"P:\Runtime\python.exe",
        "port": 8765,
        "security_build_id": None,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(
            authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=8765,
        keyfile=str(keyfile),
        native_executable=r"P:\Runtime\python.exe",
        argv=tuple(authority["argv"]),
        cwd=r"P:\DayZ_MCP_dev\tools",
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


def _transport_error(
    code: str = "daemon_identity_unverified",
    stage: str = "pre_request",
    sent: int = 0,
) -> accredited_daemon_transport.AccreditedTransportError:
    return accredited_daemon_transport.AccreditedTransportError(
        code, request_stage=stage, http_bytes_sent=sent
    )


def _scripted(outcomes: list[object]):
    """A request_fn that plays one outcome per call and records every call."""
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> tuple[int, bytes]:
        calls.append(dict(kwargs))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return request, calls


_UNAUTHORIZED = (401, b'{"error":"unauthorized"}')


def _stage_cases() -> tuple[tuple[str, list[object], str, int], ...]:
    """(label, transport outcomes, stage, attempts): one row per raise site.

    The replay site splits by what the transport reported, because that decides
    whether anything was sent and so what cures it.
    """
    return (
        (
            "identity failure reported after bytes left",
            [_transport_error(stage="post_request", sent=1)],
            "identity_after_send",
            1,
        ),
        (
            "replay still fails the identity check",
            [_transport_error(), _transport_error()],
            "replay_identity",
            2,
        ),
        (
            "replay cannot reach the daemon",
            [_transport_error(), _transport_error("daemon_transport_failure")],
            "replay_transport",
            2,
        ),
        (
            "deadline spent before the replay",
            [_transport_error(), TimeoutError("daemon_request_deadline_exceeded")],
            "replay_deadline",
            2,
        ),
        (
            "transport deadline before the replay was sent",
            [_transport_error(), _transport_error("daemon_request_deadline_exceeded")],
            "replay_deadline",
            2,
        ),
        (
            "replay sent, answer lost",
            [
                _transport_error(),
                _transport_error("daemon_transport_failure", "post_request", 1),
            ],
            "replay_response",
            2,
        ),
        (
            "replay answer too large",
            [
                _transport_error(),
                _transport_error("daemon_response_too_large", "post_request", 1),
            ],
            "replay_response",
            2,
        ),
        (
            "replacement daemon refuses the credential",
            [_transport_error(), _UNAUTHORIZED],
            "replay_unauthorized",
            2,
        ),
        (
            "daemon changes during the credential retry",
            [_UNAUTHORIZED, _transport_error()],
            "refresh_retry_identity",
            2,
        ),
    )


def _raised_refresh_error(
    testcase: unittest.TestCase, outcomes: list[object]
) -> tuple[daemon_credential.CredentialRefreshError, list[dict[str, object]]]:
    request, calls = _scripted(outcomes)
    with tempfile.TemporaryDirectory() as temporary:
        keyfile = Path(temporary) / "daemon.key"
        keyfile.write_text("fixture-credential\n", encoding="utf-8")
        provider = daemon_credential.RefreshingDaemonCredential(
            policy=_daemon_policy(keyfile), request_fn=request
        )
        with testcase.assertRaises(daemon_credential.CredentialRefreshError) as raised:
            provider.request_with_refresh(
                method="GET",
                path="/status",
                query={},
                body=None,
                headers={},
                deadline=1234.5,
            )
    return raised.exception, calls


def _public_text(stage: str) -> str:
    return f"{_REACCREDITATION_FAILED}: {daemon_credential.reaccreditation_hint(stage)}"


class ReaccreditationStageTest(unittest.TestCase):
    def test_every_raise_site_names_its_stage_and_keeps_the_code(self) -> None:
        for label, outcomes, stage, attempts in _stage_cases():
            with self.subTest(label):
                error, calls = _raised_refresh_error(self, outcomes)
                self.assertEqual(error.code, _REACCREDITATION_FAILED)
                self.assertEqual(error.reaccreditation_stage, stage)
                # str() is still the bare code: callers and docs compare it.
                self.assertEqual(str(error), _REACCREDITATION_FAILED)
                self.assertEqual(len(calls), attempts)
                self.assertEqual(
                    daemon_credential.public_refresh_error(error), _public_text(stage)
                )
                self.assertTrue(
                    daemon_credential.reaccreditation_hint(stage).startswith(
                        f"reaccreditation_stage={stage}. "
                    )
                )

    def test_each_remedy_names_what_cures_its_stage(self) -> None:
        expected = {
            "identity_after_send": ("session_status",),
            "replay_identity": (
                "new daemon generation",
                "--idle-timeout",
                "host operator",
                "python -m dayz_mcp.doctor --daemon-policy normal --json",
            ),
            "replay_transport": ("Retry the call",),
            "replay_deadline": ("Retry the call",),
            "replay_response": ("session_status",),
            "replay_unauthorized": ("Retry the call once", "keyfile"),
            "refresh_retry_identity": (
                "Retry the call once",
                "reaccreditation_stage=replay_identity",
            ),
        }
        self.assertEqual(set(expected), daemon_credential.REACCREDITATION_STAGES)
        for stage, phrases in expected.items():
            hint = daemon_credential.reaccreditation_hint(stage)
            with self.subTest(stage=stage):
                for phrase in phrases:
                    self.assertIn(phrase, hint)
                if stage == "replay_identity":
                    # The one stage a new client session cannot change.
                    self.assertIn("opening a new MCP session", hint)
                    self.assertNotIn("A new MCP session is not needed", hint)
                else:
                    self.assertIn("A new MCP session is not needed", hint)

    def test_only_a_closed_stage_of_this_code_is_kept(self) -> None:
        for stage in daemon_credential.REACCREDITATION_STAGES:
            with self.subTest(stage=stage):
                self.assertTrue(server._is_safe_error_token(stage))
                self.assertEqual(
                    daemon_credential.public_reaccreditation_stage(stage), stage
                )
        for value in (
            None,
            "",
            "REPLAY_IDENTITY",
            "replay_identity ",
            "C:\\Users\\someone\\daemon.key",
            7,
            True,
            ["replay_identity"],
            {"replay_identity": 1},
        ):
            with self.subTest(value=value):
                self.assertIsNone(daemon_credential.public_reaccreditation_stage(value))
                self.assertIsNone(daemon_credential.reaccreditation_hint(value))
        for code, stage in (
            (_REACCREDITATION_FAILED, "C:\\Users\\someone"),
            (_REACCREDITATION_FAILED, "unknown_stage"),
            ("stale_client_credential_retry_rejected", "replay_identity"),
        ):
            with self.subTest(code=code, stage=stage):
                with self.assertRaisesRegex(
                    ValueError, "^invalid_credential_refresh_error$"
                ):
                    daemon_credential.CredentialRefreshError(
                        code, reaccreditation_stage=stage
                    )
        other = daemon_credential.CredentialRefreshError("daemon_credential_desynchronized")
        self.assertIsNone(other.reaccreditation_stage)
        self.assertEqual(
            daemon_credential.public_refresh_error(other),
            "daemon_credential_desynchronized",
        )
        # No stage (an older construction): the text stays the bare code.
        bare = daemon_credential.CredentialRefreshError(_REACCREDITATION_FAILED)
        self.assertEqual(
            daemon_credential.public_refresh_error(bare), _REACCREDITATION_FAILED
        )

    def test_other_credential_failures_carry_no_stage(self) -> None:
        error, calls = _raised_refresh_error(self, [_UNAUTHORIZED, _UNAUTHORIZED])
        self.assertEqual(error.code, "daemon_credential_desynchronized")
        self.assertIsNone(error.reaccreditation_stage)
        self.assertEqual(
            daemon_credential.public_refresh_error(error),
            "daemon_credential_desynchronized",
        )
        self.assertEqual(len(calls), 2)


class ReaccreditationStageCensusTest(unittest.TestCase):
    """Every raise of the code names a stage, and every stage is raised somewhere."""

    def test_every_raise_passes_a_stage_and_every_stage_is_raised(self) -> None:
        tree = ast.parse(
            Path(daemon_credential.__file__).read_text(encoding="utf-8")
        )
        raised: set[str] = set()
        sites = 0
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "CredentialRefreshError"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == _REACCREDITATION_FAILED
            ):
                continue
            sites += 1
            stage = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "reaccreditation_stage"
                ),
                None,
            )
            self.assertIsNotNone(stage, ast.dump(node))
            if isinstance(stage, ast.Constant):
                raised.add(stage.value)
            else:
                self.assertEqual(
                    getattr(getattr(stage, "func", None), "id", ""),
                    "_replay_transport_stage",
                    ast.dump(node),
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_replay_transport_stage":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return) and isinstance(
                        inner.value, ast.Constant
                    ):
                        raised.add(inner.value.value)
        # The five sites the ticket counted; the census cannot pass by finding none.
        self.assertEqual(sites, 5)
        self.assertEqual(raised, daemon_credential.REACCREDITATION_STAGES)


def _control(keyfile: Path) -> control_client.ControlClient:
    identity = control_client.ControlIdentity(
        platform="unknown",
        pid=123,
        ppid=45,
        started_at_utc="2026-07-22T00:00:00Z",
        session_id="12345678-1234-4234-8234-1234567890ab",
        task_label="acf3",
    )
    return control_client.ControlClient(policy=_daemon_policy(keyfile), identity=identity)


class ReaccreditationCarrierTest(unittest.IsolatedAsyncioTestCase):
    """The stage and its remedy reach the caller through every client path."""

    async def test_the_control_client_keeps_the_stage_and_its_remedy(self) -> None:
        request, calls = _scripted([_transport_error(), _transport_error()])
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            client = _control(keyfile)
            with patch.object(
                control_client.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with self.assertRaises(control_client.ControlClientError) as raised:
                    await client.session_status()
        error = raised.exception
        self.assertEqual(error.code, _REACCREDITATION_FAILED)
        self.assertEqual(error.reaccreditation_stage, "replay_identity")
        self.assertEqual(
            error.hint, daemon_credential.reaccreditation_hint("replay_identity")
        )
        self.assertEqual(str(error), _public_text("replay_identity"))
        self.assertEqual((error.request_stage, error.http_bytes_sent), ("pre_request", 0))
        self.assertEqual(len(calls), 2)

    def test_a_stage_travels_only_with_its_code_and_only_from_the_set(self) -> None:
        kept = control_client.ControlClientError(
            _REACCREDITATION_FAILED,
            request_stage="pre_request",
            http_bytes_sent=0,
            reaccreditation_stage="replay_deadline",
        )
        self.assertEqual(kept.reaccreditation_stage, "replay_deadline")
        for bad in ("C:\\Users\\someone", "REPLAY_DEADLINE", "", 7, None, ["replay_deadline"]):
            with self.subTest(bad=bad):
                dropped = control_client.ControlClientError(
                    _REACCREDITATION_FAILED,
                    request_stage="pre_request",
                    http_bytes_sent=0,
                    reaccreditation_stage=bad,
                )
                self.assertIsNone(dropped.reaccreditation_stage)
        other = control_client.ControlClientError(
            "daemon_identity_unverified",
            request_stage="pre_request",
            http_bytes_sent=0,
            reaccreditation_stage="replay_identity",
        )
        self.assertIsNone(other.reaccreditation_stage)

    async def test_a_session_verb_publishes_the_stage_and_remedy_without_a_spawn(
        self,
    ) -> None:
        request, _calls = _scripted([_transport_error(), _transport_error()])
        spawns: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            runtime = object.__new__(server.ClientRuntime)
            runtime._control = _control(keyfile)
            runtime._ensure_daemon = lambda *_args: spawns.append("spawn") or True
            with patch.object(
                control_client.transport,
                "verified_daemon_http_request",
                side_effect=request,
            ):
                with self.assertRaises(server.ToolError) as raised:
                    await runtime.session_status()
        self.assertEqual(str(raised.exception), _public_text("replay_identity"))
        self.assertEqual(spawns, [])

    def test_a_bridge_call_publishes_the_stage_and_remedy(self) -> None:
        request, calls = _scripted([_transport_error(), _UNAUTHORIZED])
        spawns: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            runtime = object.__new__(server.ClientRuntime)
            runtime._credential_provider = daemon_credential.RefreshingDaemonCredential(
                policy=_daemon_policy(keyfile), request_fn=request
            )
            runtime._time_fn = lambda: 100.0
            runtime._allow_stale_policy = False
            runtime._ensure_daemon = lambda *_args: spawns.append("spawn") or True
            with self.assertRaises(server.ToolError) as raised:
                runtime._call("GET", "/status")
        self.assertEqual(str(raised.exception), _public_text("replay_unauthorized"))
        self.assertEqual(spawns, [])
        self.assertEqual(len(calls), 2)

    def test_the_daemon_health_probe_publishes_the_stage_and_remedy(self) -> None:
        request, _calls = _scripted(
            [_transport_error(), _transport_error("daemon_transport_failure")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("fixture-key\n", encoding="utf-8")
            runtime = object.__new__(server.ClientRuntime)
            runtime._probe = None
            runtime._credential_provider = daemon_credential.RefreshingDaemonCredential(
                policy=_daemon_policy(keyfile), request_fn=request
            )
            with self.assertRaises(server.ToolError) as raised:
                runtime._daemon_healthy(1234.5)
        self.assertEqual(str(raised.exception), _public_text("replay_transport"))


def _raise(error: BaseException):
    def fail(*_args: object, **_kwargs: object) -> object:
        raise error

    return fail


def _doctor_sources(root: Path, **overrides: object) -> doctor.DoctorSources:
    """Sources every check reads without raising: both config probes fail."""
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    scan = root / "launchers"
    scan.mkdir(exist_ok=True)
    values: dict[str, object] = {
        "claude_config": lambda: (1, ""),
        "codex_config": lambda: (1, ""),
        "listener_pid": lambda _port: None,
        "process_argv": lambda _pid: None,
        "daemon_status": lambda _port, _keyfile: {},
        "process_snapshot": lambda _names: {"known": True, "processes": []},
        "process_identity": lambda _pid: {"error": "process_not_found", "exit_code": 4},
        "runtime_paths": doctor.RuntimePaths(
            runtime,
            runtime / "audit",
            runtime / "coordination.json",
            runtime / "runs.json",
        ),
        "scan_roots": (scan,),
        "expected_command": sys.executable,
    }
    values.update(overrides)
    return doctor.DoctorSources(**values)


def _run_doctor_main(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdout", out):
        code = doctor.main(argv)
    return code, out.getvalue()


class DoctorFailedCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_the_fixture_sources_diagnose_without_a_failure(self) -> None:
        payload, exit_code = doctor.execute(_doctor_sources(self.root))
        self.assertNotIn("error", payload)
        self.assertEqual(exit_code, 1)

    def test_the_payload_names_the_check_and_the_exception_class(self) -> None:
        secret = r"C:\Users\someone\.dayz_mcp.key"
        payload, exit_code = doctor.execute(
            _doctor_sources(self.root, claude_config=_raise(RuntimeError(secret)))
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "diagnostic_failure",
                "failed_check": "registrations",
                "exception_class": "RuntimeError",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
        )
        for rendered in (doctor.render_json(payload), doctor.render_human(payload)):
            self.assertNotIn(secret, rendered)
            self.assertNotIn("someone", rendered)
        self.assertEqual(
            doctor.render_human(payload).splitlines()[-1],
            "[ERROR] diagnostic_failure: failed_check=registrations "
            "exception_class=RuntimeError",
        )

    def test_later_checks_are_named_too(self) -> None:
        def bad_pid_finding(_sources, _registrations, findings) -> None:
            findings.append({"code": "FIXTURE", "severity": "WARN", "pid": "not-a-pid"})

        cases = (
            (
                "runs",
                "LookupError",
                patch.object(doctor, "RunManifestStore", side_effect=LookupError("runs")),
            ),
            (
                "launchers",
                "KeyError",
                patch.object(doctor, "_check_launchers", side_effect=KeyError("x")),
            ),
            (
                "knowledge_pack",
                "AttributeError",
                patch.object(
                    doctor, "_check_knowledge_pack", side_effect=AttributeError("x")
                ),
            ),
            (
                "summary",
                "ValueError",
                patch.object(doctor, "_check_knowledge_pack", side_effect=bad_pid_finding),
            ),
        )
        for check, exception_class, patcher in cases:
            with self.subTest(check=check), patcher:
                payload, exit_code = doctor.execute(_doctor_sources(self.root))
            self.assertEqual(exit_code, 2)
            self.assertEqual(
                (payload["failed_check"], payload["exception_class"]),
                (check, exception_class),
            )
            self.assertEqual(payload["findings"], [])

    def test_the_policy_and_sources_steps_are_named(self) -> None:
        payload, exit_code = doctor.execute()
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            (payload["failed_check"], payload["exception_class"]),
            ("daemon_policy", "ValueError"),
        )
        keyfile = self.root / "daemon.key"
        keyfile.write_text("fixture-key\n", encoding="utf-8")
        with patch.object(
            doctor, "default_sources", side_effect=RuntimeError(str(keyfile))
        ):
            payload, exit_code = doctor.execute(policy=_daemon_policy(keyfile))
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            (payload["failed_check"], payload["exception_class"]),
            ("sources", "RuntimeError"),
        )
        self.assertNotIn(str(keyfile), doctor.render_json(payload))

    def test_main_names_the_policy_step_and_a_host_config_code(self) -> None:
        with patch.object(
            doctor.daemon_policy,
            "load_daemon_policy",
            side_effect=host_config.HostConfigError("daemon_provenance_incomplete"),
        ):
            code, out = _run_doctor_main(["--daemon-policy", "normal", "--json"])
            human_code, human = _run_doctor_main(["--daemon-policy", "normal"])
        self.assertEqual((code, human_code), (2, 2))
        payload = json.loads(out)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": "diagnostic_failure",
                "failed_check": "daemon_policy",
                "exception_class": "HostConfigError",
                "exception_code": "daemon_provenance_incomplete",
                "findings": [],
                "summary": {"fail": 0, "warn": 0},
            },
        )
        self.assertEqual(
            human.strip().splitlines()[-1],
            "[ERROR] diagnostic_failure: failed_check=daemon_policy "
            "exception_class=HostConfigError "
            "exception_code=daemon_provenance_incomplete",
        )

    def test_no_other_exception_text_travels(self) -> None:
        for error in (
            RuntimeError("sensitive"),
            ValueError("daemon_policy_drift"),
            OSError(r"C:\Users\someone\secret.key"),
            host_config.HostConfigError(r"C:\Users\someone\.claude.json"),
        ):
            with self.subTest(error=repr(error)):
                with patch.object(
                    doctor.daemon_policy, "load_daemon_policy", side_effect=error
                ):
                    code, out = _run_doctor_main(["--daemon-policy", "normal", "--json"])
                self.assertEqual(code, 2)
                payload = json.loads(out)
                self.assertEqual(payload["failed_check"], "daemon_policy")
                self.assertEqual(payload["exception_class"], type(error).__name__)
                self.assertNotIn("exception_code", payload)
                self.assertNotIn(str(error), out)


class DoctorCheckCensusTest(unittest.TestCase):
    """Every step name the doctor can report is in DIAGNOSTIC_CHECKS, and back."""

    def test_every_named_step_is_in_the_set_and_every_member_is_named(self) -> None:
        tree = ast.parse(Path(doctor.__file__).read_text(encoding="utf-8"))
        named: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "step"
                        and isinstance(node.value, ast.Constant)
                    ):
                        named.add(node.value.value)
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "step"
                        and isinstance(node.value, ast.List)
                    ):
                        named.update(
                            element.value
                            for element in node.value.elts
                            if isinstance(element, ast.Constant) and element.value
                        )
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "_diagnostic_failure"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                named.add(node.args[0].value)
        self.assertEqual(named, doctor.DIAGNOSTIC_CHECKS)


def _unused_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _approved_python() -> Path:
    return Path(daemon.__file__).resolve().parents[1] / ".venv-mcp" / "Scripts" / "python.exe"


class DaemonPythonNotApprovedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime = tempfile.TemporaryDirectory()
        self._environment = patch.dict(os.environ, {"LOCALAPPDATA": self._runtime.name})
        self._environment.start()

    def tearDown(self) -> None:
        self._environment.stop()
        self._runtime.cleanup()

    def test_the_exit_code_is_its_own(self) -> None:
        codes = {
            daemon.DAEMON_STARTUP_CONTENDED,
            daemon.DAEMON_PYTHON_NOT_APPROVED,
            0,
            1,
            2,
            130,
            # The startup-deadlock fixtures exit 91 for a simulated crash and 92
            # for a lost election; a real startup failure must not look like one.
            91,
            92,
        }
        self.assertEqual(len(codes), 8)
        self.assertEqual(daemon.DAEMON_PYTHON_NOT_APPROVED, 78)

    def test_run_daemon_prints_one_line_and_returns_its_code(self) -> None:
        base = getattr(sys, "_base_executable", None)
        cases = {"interpreter that does not resolve": r"C:\Foreign\python.exe"}
        if isinstance(base, str) and os.path.normcase(base) != os.path.normcase(sys.executable):
            cases["another interpreter"] = base
        for label, foreign in cases.items():
            with self.subTest(label):
                lines: list[str] = []
                config = SimpleNamespace(
                    key="fixture-key",
                    keyfile=None,
                    port=_unused_port(),
                    idle_timeout_s=0.0,
                    enable_exec_enforce=False,
                    exec_allowlist=None,
                    require_version=False,
                    expected_game_version=None,
                    log_sink=lines.append,
                )
                with (
                    patch.object(daemon.sys, "executable", foreign),
                    patch.object(
                        daemon.orphan_guard, "probe_status_healthy", return_value=False
                    ),
                    patch.object(daemon, "NativeProcessGuard") as guard,
                    patch.object(daemon, "build_server_state") as build,
                ):
                    result = daemon.run_daemon(config)
                self.assertEqual(result, daemon.DAEMON_PYTHON_NOT_APPROVED)
                self.assertEqual(len(lines), 1, lines)
                line = lines[0]
                self.assertNotIn("\n", line)
                self.assertIn("daemon_python_not_approved", line)
                self.assertIn(str(_approved_python()), line)
                self.assertIn(foreign, line)
                self.assertIn("tools\\install-mcp.ps1", line)
                self.assertIn("tools\\.venv-mcp", line)
                guard.assert_not_called()
                build.assert_not_called()

    def test_the_gate_still_raises_its_named_runtime_error(self) -> None:
        with (
            patch.object(daemon.sys, "executable", r"C:\Foreign\python.exe"),
            patch.object(daemon, "NativeProcessGuard") as guard,
            self.assertRaisesRegex(RuntimeError, "^daemon_python_not_approved$") as raised,
        ):
            daemon._ensure_identity_migration(SimpleNamespace(port=8765))
        self.assertIsInstance(raised.exception, daemon.DaemonPythonNotApproved)
        self.assertEqual(raised.exception.approved, _approved_python())
        self.assertEqual(raised.exception.current, r"C:\Foreign\python.exe")
        guard.assert_not_called()

    @slow_test
    def test_a_daemon_started_by_another_venv_exits_with_one_line(self) -> None:
        """The ticket's repro: a clone's venv named .venv-clone starts the daemon.

        The ticket pip-installed the package into that venv. Here a .pth lends it
        the approved venv's site-packages instead, through site.addsitedir so
        pywin32's own .pth runs (mcp imports pywintypes); the daemon only looks
        at which interpreter runs it. The fixture site (PYTHONPATH) pins
        dayz_mcp to this checkout, as the startup-deadlock tests do.
        """
        mapped = editable_mapped_tools_dir()
        approved_site = Path(sys.executable).resolve().parents[1] / "Lib" / "site-packages"
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            venv = base / "clone" / ".venv-clone"
            created = subprocess.run(
                [sys.executable, "-m", "venv", str(venv), "--without-pip"],
                capture_output=True,
                text=True,
                timeout=60.0,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            other_python = venv / "Scripts" / "python.exe"
            self.assertTrue(other_python.is_file(), other_python)
            (venv / "Lib" / "site-packages" / "dayz_mcp_dependencies.pth").write_text(
                f"import site; site.addsitedir({str(approved_site)!r})\n",
                encoding="utf-8",
            )
            keyfile = base / "fixture.key"
            keyfile.write_text("fixture-key", encoding="ascii")
            environment = os.environ.copy()
            environment["LOCALAPPDATA"] = str(base / "local")
            environment["PYTHONPATH"] = str(_DAEMON_FIXTURE_SITE)
            environment["DAYZ_MCP_FIXTURE_MODE"] = "none"
            environment["DAYZ_MCP_FIXTURE_SIGNAL"] = str(base / "signal")
            environment["DAYZ_MCP_FIXTURE_MIGRATION"] = str(base / "migration")
            if mapped is not None:
                environment["DAYZ_MCP_HOST_TOOLS"] = str(mapped)
            argv = daemon.build_daemon_argv(
                SimpleNamespace(
                    port=_unused_port(),
                    keyfile=str(keyfile),
                    idle_timeout_s=0.5,
                    expected_game_version=None,
                    require_version=False,
                    enable_exec_enforce=False,
                ),
                python=str(other_python),
            )
            completed = subprocess.run(
                argv,
                cwd=str(_TOOLS_DIR),
                env=environment,
                capture_output=True,
                text=True,
                timeout=120.0,
            )
        self.assertEqual(
            completed.returncode, daemon.DAEMON_PYTHON_NOT_APPROVED, completed.stderr
        )
        self.assertNotIn("Traceback", completed.stderr)
        lines = [line for line in completed.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stderr)
        line = os.path.normcase(lines[0])
        self.assertIn(
            os.path.normcase(str(_TOOLS_DIR / ".venv-mcp" / "Scripts" / "python.exe")),
            line,
        )
        self.assertIn(os.path.normcase(str(other_python)), line)
        self.assertIn(os.path.normcase("tools\\.venv-mcp"), line)


_INTERNAL_FAILURE_TERMINAL = {
    "cleanup_degraded": False,
    "error_code": "internal_failure",
    "exit_code": 2,
    "ok": False,
    "run_id": None,
}


class WorkerInternalFailureTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, terminal: dict[str, object]) -> dict[str, object]:
        policy = _project_policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"]("stdout", _terminal(terminal))
            return int(terminal["exit_code"])

        with (
            patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()),
            patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                return_value=_Bundle(_sealed(policy)),
            ),
            patch.object(
                dayz_test_tool,
                "preflight_vpp_request",
                return_value=native_launcher_transaction.VppPreflightResult(
                    error_code=None,
                    missing=(),
                    warnings=(),
                    hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
                ),
            ),
            patch.object(
                dayz_test_tool.secure_launcher,
                "execute_secure_launcher_request",
                side_effect=launch,
            ),
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                _Runtime(), project=policy.mod, mode="server"
            )

    async def test_internal_failure_names_its_stage_where_it_leaves_the_sealed_worker(
        self,
    ) -> None:
        result = await self._run(_INTERNAL_FAILURE_TERMINAL)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "internal_failure")
        self.assertEqual(result["phase"], "executing")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["reason"], "sealed_worker_exception")
        # The sealed worker reports only its code, so the class is unknown here,
        # and null says so rather than a missing key.
        self.assertIn("exception_class", result)
        self.assertIsNone(result["exception_class"])
        remediation = result["remediation"]
        self.assertIsInstance(remediation, str)
        self.assertIn("Retry once", remediation)
        self.assertIn("pipeline_feedback", remediation)

    async def test_a_classified_worker_failure_keeps_its_shape(self) -> None:
        result = await self._run(
            dict(_INTERNAL_FAILURE_TERMINAL, error_code="build_failed", exit_code=1)
        )
        self.assertEqual(result["error_code"], "build_failed")
        self.assertIsNone(result["reason"])
        self.assertIsNone(result["remediation"])
        self.assertNotIn("exception_class", result)


def _fresh_snapshot() -> dict[str, object]:
    return {
        "stale": [],
        "unreadable": [],
        "unreadable_reasons": {},
        "watched_count": 3,
        "server_pid": 1,
        "server_started_at": 0.0,
    }


def _stale_snapshot() -> dict[str, object]:
    payload = _fresh_snapshot()
    payload["stale"] = ["dayz_mcp.dayz_test_tool"]
    return payload


def _unknown_snapshot() -> dict[str, object]:
    payload = _fresh_snapshot()
    payload["unreadable"] = ["dayz_mcp.dayz_test_tool"]
    payload["unreadable_reasons"] = {"dayz_mcp.dayz_test_tool": "source_unreadable_now"}
    return payload


def _internal_failure_result() -> dict[str, object]:
    """What execute_dayz_test_run returns for the sealed worker's internal_failure."""
    return {
        "status": "failed",
        "project": "ExampleMod",
        "mode": "server",
        "run_id": None,
        "phase": "executing",
        "elapsed_s": 1.86,
        "artifacts_paths": [],
        "error_code": "internal_failure",
        "cleanup_degraded": False,
        "reason": "sealed_worker_exception",
        "exception_class": None,
        "remediation": "tool-layer remediation",
    }


class StaleServerInternalFailureTest(unittest.IsolatedAsyncioTestCase):
    async def _call(
        self,
        tool: str,
        arguments: dict[str, object],
        target: str,
        snapshot: dict[str, object],
        execute_payload: dict[str, object],
    ) -> dict[str, object]:
        config = ServerConfig(
            mode="client",
            key="k",
            port=12345,
            client_platform="codex",
            log_sink=lambda _message: None,
        )
        runtime = _fixture_client_runtime(config)

        async def execute(*_args: object, **_kwargs: object) -> dict[str, object]:
            return dict(execute_payload)

        with patch.object(server, "ClientRuntime", return_value=runtime):
            app, _built = build_app(config)
        with (
            patch.object(server._SERVER_SOURCES, "snapshot", return_value=snapshot),
            patch.object(server.dayz_test_tool, target, side_effect=execute),
            patch.object(
                runtime,
                "session_status",
                new=AsyncMock(
                    return_value={
                        "self": {"state": "none"},
                        "box": {
                            "occupied": False,
                            "runs": [],
                            "foreign": [],
                            "ports_in_use": [],
                            "queue": [],
                        },
                    }
                ),
            ),
        ):
            return _content_json(await app.call_tool(tool, arguments))

    async def _call_run(
        self, snapshot: dict[str, object], execute_payload: dict[str, object]
    ) -> dict[str, object]:
        return await self._call(
            "dayz_test_run",
            {"project": "ExampleMod", "mode": "server"},
            "execute_dayz_test_run",
            snapshot,
            execute_payload,
        )

    async def _call_stop(
        self, snapshot: dict[str, object], execute_payload: dict[str, object]
    ) -> dict[str, object]:
        return await self._call(
            "dayz_test_stop",
            {"run_id": RUN_ID},
            "execute_dayz_test_stop",
            snapshot,
            execute_payload,
        )

    async def test_stale_or_unknown_sources_name_server_reload_or_a_new_client(
        self,
    ) -> None:
        for label, snapshot in (("stale", _stale_snapshot()), ("unknown", _unknown_snapshot())):
            with self.subTest(label):
                result = await self._call_run(snapshot, _internal_failure_result())
                self.assertIs(result["caller_tool_registry_stale"], True)
                self.assertEqual(result["error_code"], "internal_failure")
                self.assertEqual(result["reason"], "sealed_worker_exception")
                remediation = result["remediation"]
                self.assertIn("server_reload", remediation)
                self.assertIn("reopen the MCP client", remediation)
                self.assertIn("server_modules", remediation)

    async def test_fresh_sources_keep_the_tool_layer_remediation(self) -> None:
        result = await self._call_run(_fresh_snapshot(), _internal_failure_result())
        self.assertIs(result["caller_tool_registry_stale"], False)
        self.assertEqual(result["remediation"], "tool-layer remediation")

    async def test_stale_sources_leave_other_failures_alone(self) -> None:
        payload = dict(
            _internal_failure_result(),
            error_code="build_failed",
            reason=None,
            remediation=None,
        )
        payload.pop("exception_class")
        result = await self._call_run(_stale_snapshot(), payload)
        self.assertIs(result["caller_tool_registry_stale"], True)
        self.assertIsNone(result["remediation"])

    async def test_dayz_test_stop_names_the_same_fix_only_when_stale(self) -> None:
        stop_failure = dict(_internal_failure_result(), run_id=None)
        stale = await self._call_stop(_stale_snapshot(), stop_failure)
        self.assertEqual(stale["error_code"], "internal_failure")
        self.assertIn("server_reload", stale["remediation"])
        self.assertIn("reopen the MCP client", stale["remediation"])
        fresh = await self._call_stop(_fresh_snapshot(), stop_failure)
        self.assertEqual(fresh["remediation"], "tool-layer remediation")
        other = dict(
            stop_failure, error_code="run_stop_failed", remediation=None, reason=None
        )
        other.pop("exception_class")
        untouched = await self._call_stop(_stale_snapshot(), other)
        self.assertIsNone(untouched["remediation"])


if __name__ == "__main__":
    unittest.main()
