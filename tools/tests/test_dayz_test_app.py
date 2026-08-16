from __future__ import annotations

import importlib.util
import io
import json
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import (
    accredited_daemon_transport,
    dayz_test_worker,
    native_broker_protocol,
    normal_daemon_policy,
    pinned_keyfile,
)


APP_PATH = (
    Path(__file__).resolve().parents[1]
    / "native-launchers"
    / "dayz-test-v1"
    / "src"
    / "app_main.py"
)
RUN_ID = "12345678-1234-4234-8234-1234567890ab"


def _load_app():
    spec = importlib.util.spec_from_file_location("dayz_test_v1_app_test", APP_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("app_main unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _payload(raw: bytes) -> dict[str, object]:
    size = struct.unpack("<I", raw[:4])[0]
    framed = raw[4:]
    if len(framed) != size or not framed.startswith(b"DZW1"):
        raise AssertionError("invalid terminal frame")
    value = json.loads(framed[4:])
    if not isinstance(value, dict):
        raise AssertionError("invalid terminal payload")
    return value


class DayzTestAppTerminalTests(unittest.TestCase):
    def test_lifecycle_status_forwards_exact_run_after_erasing_inherited_secrets(
        self,
    ) -> None:
        app = _load_app()
        identity = {"session_id": "fixture-session"}
        frame = native_broker_protocol.encode_request(
            native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
            {
                "command": "status",
                "launch_operation_id": None,
                "run_id": RUN_ID,
            },
        )
        output = io.BytesIO()
        recorded: list[dict[str, object]] = []
        environment_at_transport: list[tuple[bool, bool]] = []
        policy = SimpleNamespace(
            argv=("fixture-daemon",),
            cwd=r"C:\fixture",
            host="127.0.0.1",
            keyfile=r"C:\fixture\keyfile",
            native_executable=r"C:\fixture\daemon.exe",
            port=9876,
            revalidate=lambda: None,
        )

        def transport(**kwargs):
            environment_at_transport.append(
                (
                    app._IDENTITY in app.os.environ,
                    app._TOKEN in app.os.environ,
                )
            )
            recorded.append(kwargs)
            return 200, b'{"ok":true,"runs":[]}'

        with patch.dict(
            app.os.environ,
            {
                app._IDENTITY: json.dumps(
                    identity,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                app._TOKEN: "fixture-lease",
            },
            clear=False,
        ), patch.object(
            app.sys,
            "stdin",
            SimpleNamespace(buffer=io.BytesIO(frame)),
        ), patch.object(
            app.sys,
            "stdout",
            SimpleNamespace(buffer=output),
        ), patch.object(
            normal_daemon_policy,
            "load_inherited_normal_daemon_policy",
            return_value=policy,
        ), patch.object(
            pinned_keyfile,
            "read_pinned_keyfile",
            return_value="fixture-key",
        ), patch.object(
            accredited_daemon_transport,
            "verified_daemon_http_request",
            side_effect=transport,
        ):
            self.assertEqual(app._lifecycle_main(), 0)

        self.assertEqual(environment_at_transport, [(False, False)])
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["path"], "/lifecycle/status")
        self.assertEqual(
            json.loads(recorded[0]["body"]),
            {
                "identity": identity,
                "lease_token": "fixture-lease",
                "run_id": RUN_ID,
            },
        )
        self.assertEqual(json.loads(output.getvalue()), {"ok": True, "runs": []})

    def test_success_terminal_has_exact_closed_schema(self) -> None:
        app = _load_app()
        output = io.BytesIO()
        with patch.object(app.sys, "stdout", SimpleNamespace(buffer=output)):
            app._write_worker_terminal(0, RUN_ID, None, False)

        self.assertEqual(
            _payload(output.getvalue()),
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            },
        )

    def test_main_serializes_known_worker_failure_without_exception_text(self) -> None:
        readiness_codes = sorted(
            code
            for code in dayz_test_worker.WORKER_ERROR_CODES
            if code.startswith("readiness_")
        )
        self.assertEqual(len(readiness_codes), 9)
        for code in readiness_codes:
            with self.subTest(code=code):
                app = _load_app()
                output = io.BytesIO()
                error = dayz_test_worker.DayzTestWorkerError(
                    code,
                    run_id=RUN_ID,
                    cleanup_degraded=True,
                )

                def fail_known(coroutine):
                    coroutine.close()
                    raise error

                with patch.object(
                    app.sys,
                    "argv",
                    [str(APP_PATH)],
                ), patch.object(
                    app.sys,
                    "stdout",
                    SimpleNamespace(buffer=output),
                ), patch.object(
                    app.asyncio,
                    "run",
                    side_effect=fail_known,
                ):
                    self.assertEqual(app.main(), 2)

                raw = output.getvalue()
                payload = _payload(raw)
                self.assertEqual(
                    set(payload),
                    {
                        "cleanup_degraded",
                        "error_code",
                        "exit_code",
                        "ok",
                        "run_id",
                    },
                )
                self.assertTrue(raw[4:].startswith(b"DZW1"))
                self.assertNotIn(b"DZW2", raw)
                self.assertEqual(payload["error_code"], code)
                self.assertEqual(payload["run_id"], RUN_ID)
                self.assertIs(payload["cleanup_degraded"], True)

    def test_main_maps_unknown_exception_to_internal_failure(self) -> None:
        app = _load_app()
        output = io.BytesIO()

        def fail_unknown(coroutine):
            coroutine.close()
            raise RuntimeError("secret exception text")

        with patch.object(app.sys, "argv", [str(APP_PATH)]), patch.object(
            app.sys, "stdout", SimpleNamespace(buffer=output)
        ), patch.object(
            app.asyncio, "run", side_effect=fail_unknown
        ):
            self.assertEqual(app.main(), 2)

        payload = _payload(output.getvalue())
        self.assertEqual(payload["error_code"], "internal_failure")
        self.assertIsNone(payload["run_id"])
        self.assertNotIn(b"secret exception text", output.getvalue())


if __name__ == "__main__":
    unittest.main()
