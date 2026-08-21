"""Offline tests for the synthetic fencing probe (replacement for launching a second DayZDiag).

All cases run against an in-process ServerState. They must not open a
socket to the live daemon port (8765) or touch a real DayZ process.
"""
from __future__ import annotations

import inspect
import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import fence_canary_probe as probe
from dayz_mcp import loopback
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION, build_status
from dayz_mcp.instance_fence import BINDING_AMBIGUOUS, BINDING_BOUND, BINDING_STARTING
from tests.fence_helpers import INST_CLIENT, PID_CLIENT


PROBE_PID = 59999
MUTATION_CMD = "camera_set"
MUTATION_ARGS = {"cam_mode": "orient"}


def _rich(
    binding: str | None,
    *,
    ambiguous: int = 0,
    unaccredited: int = 0,
) -> dict:
    payload: dict = {
        "fence": {
            "mutation_rejects_by_code": {"instance_ambiguous": ambiguous},
            "unaccredited_mutation_enqueues": unaccredited,
        }
    }
    if binding is not None:
        payload["client_peer"] = {"binding_state": binding}
    return payload


def _raw_peers(
    binding: str,
    *,
    ambiguous: int = 0,
    unaccredited: int = 0,
) -> dict:
    return {
        "peers": {"client": {"binding_state": binding}},
        "fence": {
            "mutation_rejects_by_code": {"instance_ambiguous": ambiguous},
            "unaccredited_mutation_enqueues": unaccredited,
        },
    }


def _bound_client(state: loopback.ServerState, pid: int = PID_CLIENT) -> None:
    state.install_bound_peer(
        instance=INST_CLIENT,
        role="client",
        pid=pid,
    )


class ClientPeerParserTest(unittest.TestCase):
    """Regression of the old canary: it read status['peers']['client'] and
    therefore never saw AMBIGUOUS on the rich /status payload."""

    def test_reads_client_peer_not_nested_peers(self) -> None:
        rich = _rich(BINDING_AMBIGUOUS, ambiguous=1)
        raw = _raw_peers(BINDING_AMBIGUOUS, ambiguous=1)
        self.assertEqual(probe.client_binding_state(rich), BINDING_AMBIGUOUS)
        self.assertIsNone(probe.client_binding_state(raw))

    def test_source_mentions_client_peer_and_not_peers(self) -> None:
        source = inspect.getsource(probe.client_binding_state)
        self.assertIn("client_peer", source)
        self.assertNotIn("peers", source)

    def test_evaluate_on_raw_peers_shape_is_unmeasurable_never_pass(self) -> None:
        result = probe.evaluate_verdict(
            before=_raw_peers(BINDING_BOUND),
            after=_raw_peers(BINDING_AMBIGUOUS, ambiguous=1),
            mutation_status=409,
            mutation_body={"error": "instance_ambiguous"},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)


class EvaluateVerdictTest(unittest.TestCase):
    def test_starting_not_bound_is_unmeasurable_never_pass(self) -> None:
        for starting in (BINDING_STARTING, BINDING_AMBIGUOUS, "LEGACY_UNBOUND", None):
            with self.subTest(starting=starting):
                before = _rich(starting)
                after = _rich(BINDING_AMBIGUOUS, ambiguous=1)
                result = probe.evaluate_verdict(
                    before=before,
                    after=after,
                    mutation_status=409,
                    mutation_body={"error": "instance_ambiguous"},
                )
                self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
                self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_unreachable_daemon_is_unmeasurable_never_pass(self) -> None:
        result = probe.evaluate_verdict(
            before=None,
            after=None,
            mutation_status=None,
            mutation_body=None,
            reachable=False,
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_missing_client_peer_is_unmeasurable_never_pass(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(None),
            after=_rich(BINDING_AMBIGUOUS, ambiguous=1),
            mutation_status=409,
            mutation_body={"error": "instance_ambiguous"},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_happy_path_is_pass(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(BINDING_BOUND),
            after=_rich(BINDING_AMBIGUOUS, ambiguous=1),
            mutation_status=409,
            mutation_body={"error": "instance_ambiguous"},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_PASS)

    def test_binding_stays_bound_is_fail(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(BINDING_BOUND),
            after=_rich(BINDING_BOUND),
            mutation_status=200,
            mutation_body={"id": 1},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_FAIL)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_unaccredited_increment_is_fail(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(BINDING_BOUND, unaccredited=0),
            after=_rich(BINDING_AMBIGUOUS, ambiguous=1, unaccredited=1),
            mutation_status=409,
            mutation_body={"error": "instance_ambiguous"},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_FAIL)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)
        self.assertIn(
            "unaccredited_mutation_enqueues_incremented", result["reasons"]
        )

    def test_reject_count_not_incremented_is_fail(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(BINDING_BOUND, ambiguous=0),
            after=_rich(BINDING_AMBIGUOUS, ambiguous=0),
            mutation_status=200,
            mutation_body={"id": 1},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_FAIL)

    def test_lease_required_without_reject_increment_is_unmeasurable(self) -> None:
        result = probe.evaluate_verdict(
            before=_rich(BINDING_BOUND),
            after=_rich(BINDING_AMBIGUOUS, ambiguous=0),
            mutation_status=423,
            mutation_body={"error": "lease_required"},
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)


class ProbeOnServerStateTest(unittest.TestCase):
    def test_other_source_pid_marks_binding_ambiguous(self) -> None:
        state = loopback.ServerState("k")
        _bound_client(state)
        self.assertEqual(state._bindings[INST_CLIENT].state, BINDING_BOUND)
        result = probe.run_probe_on_state(
            state,
            instance=INST_CLIENT,
            source_pid=PROBE_PID,
        )
        self.assertEqual(state._bindings[INST_CLIENT].state, BINDING_AMBIGUOUS)
        self.assertIn(PROBE_PID, state._bindings[INST_CLIENT].presented_pids)
        self.assertEqual(result["verdict"], probe.VERDICT_PASS)

    def test_ambiguous_mutation_rejected_with_instance_ambiguous(self) -> None:
        state = loopback.ServerState("k")
        _bound_client(state)
        result = probe.run_probe_on_state(
            state,
            instance=INST_CLIENT,
            source_pid=PROBE_PID,
        )
        fence = state.status_snapshot()["fence"]
        self.assertEqual(fence["mutation_rejects_by_code"]["instance_ambiguous"], 1)
        self.assertEqual(fence["unaccredited_mutation_enqueues"], 0)
        self.assertEqual(result["mutation_status"], 409)
        self.assertEqual(result["mutation_error"], "instance_ambiguous")
        self.assertEqual(result["verdict"], probe.VERDICT_PASS)

    def test_public_status_has_client_peer_not_peers(self) -> None:
        state = loopback.ServerState("k")
        _bound_client(state)
        status = probe.public_status(state)
        self.assertIn("client_peer", status)
        self.assertNotIn("peers", status)
        self.assertEqual(status["client_peer"]["binding_state"], BINDING_BOUND)
        assembled = build_status(
            state.status_snapshot(),
            require_version=False,
            expected_game_version=None,
        )
        self.assertEqual(status["client_peer"], assembled["client_peer"])
        self.assertEqual(status["fence"], assembled["fence"])

    def test_starting_without_binding_is_unmeasurable_never_pass(self) -> None:
        state = loopback.ServerState("k")
        result = probe.run_probe_on_state(
            state,
            instance=INST_CLIENT,
            source_pid=PROBE_PID,
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_already_ambiguous_is_unmeasurable_never_pass(self) -> None:
        state = loopback.ServerState("k")
        _bound_client(state)
        state.record_poll(
            "client", instance=INST_CLIENT, source_pid=PROBE_PID
        )
        self.assertEqual(state._bindings[INST_CLIENT].state, BINDING_AMBIGUOUS)
        result = probe.run_probe_on_state(
            state,
            instance=INST_CLIENT,
            source_pid=PROBE_PID,
        )
        self.assertEqual(result["verdict"], probe.VERDICT_UNMEASURABLE)
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)

    def test_negative_control_collapsed_resolve_poll_pid_is_not_pass(self) -> None:
        """Collapsed discriminator: resolve_poll_pid always returns the
        bound PID, so a second presenter is invisible. The probe must not PASS.

        install_bound_peer installs TestIdentityOverride, which is the same
        collapse (resolve ignores the socket and returns binding.pid).
        """
        state = loopback.ServerState("k")
        _bound_client(state)
        self.assertEqual(
            state.resolve_poll_pid(INST_CLIENT, object()),
            PID_CLIENT,
        )

        def collapsed(_instance: str | None, _sock: object) -> int | None:
            return PID_CLIENT

        with patch.object(state, "resolve_poll_pid", side_effect=collapsed):
            result = probe.run_probe_on_state(
                state,
                instance=INST_CLIENT,
                sock=object(),
            )
        self.assertNotEqual(result["verdict"], probe.VERDICT_PASS)
        self.assertEqual(state._bindings[INST_CLIENT].state, BINDING_BOUND)
        fence = state.status_snapshot()["fence"]
        self.assertEqual(fence["mutation_rejects_by_code"]["instance_ambiguous"], 0)

    def test_run_probe_calls_production_record_poll(self) -> None:
        state = loopback.ServerState("k")
        _bound_client(state)
        original = state.record_poll
        calls: list[tuple] = []

        def wrapped(peer, version=None, instance=None, source_pid=None, source_creation_time=None):
            calls.append((peer, instance, source_pid))
            return original(
                peer,
                version,
                instance=instance,
                source_pid=source_pid,
                source_creation_time=source_creation_time,
            )

        with patch.object(state, "record_poll", side_effect=wrapped):
            probe.run_probe_on_state(
                state,
                instance=INST_CLIENT,
                source_pid=PROBE_PID,
            )
        self.assertEqual(calls, [("client", INST_CLIENT, PROBE_PID)])
        self.assertTrue(inspect.isfunction(loopback.ServerState.record_poll))


class UrlAndProfileTest(unittest.TestCase):
    def test_status_url_puts_key_in_query(self) -> None:
        url = probe.status_url(probe.DEFAULT_PORT, "secret-key")
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.hostname, "127.0.0.1")
        self.assertEqual(parsed.port, 8765)
        self.assertEqual(parsed.path, "/status")
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs.get("key"), ["secret-key"])
        self.assertEqual(probe.DEFAULT_PORT, 8765)

    def test_poll_url_carries_inst_peer_ver_and_key(self) -> None:
        ver = f"{EXPECTED_BRIDGE_VERSION}~unknown"
        url = probe.poll_url(
            8765,
            "k",
            peer="client",
            ver=ver,
            inst=INST_CLIENT,
        )
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.path, "/poll")
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs.get("key"), ["k"])
        self.assertEqual(qs.get("peer"), ["client"])
        self.assertEqual(qs.get("ver"), [ver])
        self.assertEqual(qs.get("inst"), [INST_CLIENT])

    def test_arg_parser_defaults(self) -> None:
        parser = probe.build_arg_parser()
        args = parser.parse_args(
            ["--client-profiles", "profiles", "--json-out", "out.json"]
        )
        self.assertEqual(args.port, 8765)
        self.assertEqual(args.peer, "client")
        self.assertIsNone(args.key)
        self.assertEqual(args.client_profiles, "profiles")
        self.assertEqual(args.json_out, "out.json")

    def test_read_profile_instance(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "dayz_mcp.json").write_text(
                json.dumps(
                    {
                        "url": "http://127.0.0.1:8765/",
                        "key": "k",
                        "pollHz": 5,
                        "instance": INST_CLIENT,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            self.assertEqual(probe.read_profile_instance(directory), INST_CLIENT)

    def test_read_profile_instance_missing_is_error(self) -> None:
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "dayz_mcp.json").write_text(
                json.dumps({"url": "http://127.0.0.1:8765/", "key": "k"}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                probe.read_profile_instance(directory)


class NoLiveDaemonTest(unittest.TestCase):
    def test_module_does_not_open_live_port_on_import(self) -> None:
        source = Path(probe.__file__).read_text(encoding="utf-8")
        self.assertNotIn("urlopen", inspect.getsource(probe.client_binding_state))
        self.assertNotIn("urlopen", inspect.getsource(probe.evaluate_verdict))
        self.assertNotIn("urlopen", inspect.getsource(probe.run_probe_on_state))
        self.assertIn("urlopen", source)


if __name__ == "__main__":
    unittest.main()
