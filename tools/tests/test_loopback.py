from __future__ import annotations

import inspect
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from dayz_mcp import loopback
from dayz_mcp import server
from dayz_mcp.session_coordination import (
    ClientIdentity,
    MAX_SESSION_QUEUE,
    SessionCoordinator,
)
from tests.fence_helpers import INST_CLIENT, INST_SERVER, accredited_poll, bind_both_peers


class LoopbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)
        self.httpd = loopback.create_http_server(0, self.state, log_sink=lambda _message: None)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2.0)
        self.httpd.server_close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        include_key: bool = True,
    ) -> tuple[int, dict]:
        params = dict(query or {})
        if include_key:
            params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                raw = response.read().decode("utf-8")
                return int(response.status), json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
                return int(exc.code), json.loads(raw)
            finally:
                exc.close()

    def _enable_coordination(self) -> tuple[dict[str, object], str]:
        identity_payload: dict[str, object] = {
            "platform": "codex",
            "pid": 101,
            "ppid": 10,
            "started_at_utc": "2026-07-14T20:00:00Z",
            "session_id": "retail-reason-owner",
            "task_label": "retail reason",
        }
        client = ClientIdentity.from_payload(identity_payload)
        coordinator = SessionCoordinator(
            token_fn=lambda: "retail-token",
            id_fn=lambda: "retail-lease",
            audit=lambda _event: True,
            cleanup=lambda session_id, lease_id, reason, vehicle_active: self.state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            ),
        )
        self.state.coordination = coordinator
        status, active = coordinator.acquire(client, "retail reason test")
        self.assertEqual(status, 200)
        return identity_payload, active["lease_token"]

    def _assert_retail_quarantine_reason(
        self,
        identity_payload: dict[str, object],
        lease_token: str,
        expected_reason: str,
    ) -> None:
        status, body = self.request(
            "POST",
            "/enqueue",
            {
                "cmd": "world_spawn",
                "args": {"type": "X", "pos": [1, 2, 3]},
                "identity": identity_payload,
                "lease_token": lease_token,
            },
        )
        self.assertEqual(
            (status, body),
            (
                409,
                {
                    "error": "retail_quarantine",
                    "reason": expected_reason,
                },
            ),
        )

    def test_retail_quarantine_409_reports_probe_error(self) -> None:
        identity_payload, lease_token = self._enable_coordination()

        def broken_probe():
            raise RuntimeError("probe unavailable")

        self.state.retail_probe = broken_probe
        self._assert_retail_quarantine_reason(
            identity_payload, lease_token, "probe_error"
        )

    def test_retail_quarantine_409_distinguishes_malformed_and_unknown_probe(
        self,
    ) -> None:
        identity_payload, lease_token = self._enable_coordination()
        cases = (
            ([], "probe_malformed"),
            ({"known": "yes", "processes": []}, "probe_malformed"),
            ({"known": False, "processes": []}, "probe_unknown"),
        )
        for probe_result, expected_reason in cases:
            with self.subTest(
                probe_result=probe_result, expected_reason=expected_reason
            ):
                self.state.retail_probe = lambda result=probe_result: result
                self._assert_retail_quarantine_reason(
                    identity_payload, lease_token, expected_reason
                )

    def test_retail_quarantine_409_distinguishes_process_shape_and_presence(
        self,
    ) -> None:
        identity_payload, lease_token = self._enable_coordination()
        cases = (
            ({"known": True, "processes": {}}, "probe_malformed"),
            ({"known": True, "processes": [{"pid": 123}]}, "retail_present"),
        )
        for probe_result, expected_reason in cases:
            with self.subTest(
                probe_result=probe_result, expected_reason=expected_reason
            ):
                self.state.retail_probe = lambda result=probe_result: result
                self._assert_retail_quarantine_reason(
                    identity_payload, lease_token, expected_reason
                )

    def test_retail_quarantine_409_reports_no_probe(self) -> None:
        identity_payload, lease_token = self._enable_coordination()
        self.state.retail_probe = None
        self._assert_retail_quarantine_reason(
            identity_payload, lease_token, "no_probe"
        )

    def test_credential_recovery_telemetry_expires_and_saturates(self) -> None:
        now = [100.0]
        state = loopback.ServerState("fixture-key", time_fn=lambda: now[0])
        state._credential_recovery_count = (
            loopback.CREDENTIAL_RECOVERY_COUNT_MAX
        )

        state.record_credential_recovery()
        self.assertEqual(
            state.credential_recovery_snapshot(),
            {
                "recovered_count": loopback.CREDENTIAL_RECOVERY_COUNT_MAX,
                "recent": True,
                "last_recovered_age_s": 0.0,
            },
        )

        now[0] += loopback.CREDENTIAL_RECOVERY_TTL_S
        self.assertIs(
            state.credential_recovery_snapshot()["recent"],
            True,
        )
        now[0] += 0.001
        self.assertEqual(
            state.credential_recovery_snapshot(),
            {
                "recovered_count": loopback.CREDENTIAL_RECOVERY_COUNT_MAX,
                "recent": False,
                "last_recovered_age_s": None,
            },
        )

    def test_five_endpoints_round_trip(self) -> None:
        status, body = self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
        self.assertEqual(status, 200)
        command_id = body["id"]

        status, body = self.request("GET", "/poll", query={"inst": INST_SERVER})
        self.assertEqual(status, 200)
        self.assertEqual(body["commands"][0]["id"], command_id)

        status, body = self.request(
            "POST",
            "/result",
            {"id": command_id, "ok": 1, "value": 7},
            query={"inst": INST_SERVER},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

        status, body = self.request("GET", "/await", query={"id": command_id})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "done")
        self.assertTrue(body["result"]["ok"])

        status, body = self.request("POST", "/set_poll_delay", {"ms": 0})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_set_poll_delay_applies_once_and_range_checks(self) -> None:
        status, body = self.request("POST", "/set_poll_delay", {"ms": -1})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_ms_range"})

        status, body = self.request("POST", "/set_poll_delay", {"ms": 5001})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_ms_range"})

        status, body = self.request("POST", "/set_poll_delay", {"ms": 80})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})

        t0 = time.monotonic()
        status, body = self.request("GET", "/poll", query={"inst": INST_SERVER})
        first_elapsed = time.monotonic() - t0
        self.assertEqual(status, 200)
        self.assertEqual(len(body["commands"]), 1)
        self.assertGreaterEqual(first_elapsed, 0.06)

        self.request("POST", "/enqueue", {"cmd": "query_player_state", "args": {}})
        t1 = time.monotonic()
        status, body = self.request("GET", "/poll", query={"inst": INST_SERVER})
        second_elapsed = time.monotonic() - t1
        self.assertEqual(status, 200)
        self.assertEqual(len(body["commands"]), 1)
        self.assertLess(second_elapsed, 0.06)

    def test_last_poll_and_version_hook(self) -> None:
        status, body = self.request("GET", "/poll", query={"peer": "client", "ver": "4~1.29.0"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"commands": []})

        snapshot = self.state.status_snapshot()
        client = snapshot["peers"]["client"]
        self.assertIsNotNone(client["last_poll_at"])
        self.assertIsNotNone(client["last_poll_age_s"])
        self.assertEqual(client["version"], "4~1.29.0")

    def test_server_state_api(self) -> None:
        status, payload = self.state.enqueue_command("camera_get", {}, peer="client")
        self.assertEqual(status, 200)
        command_id = payload["id"]
        status, poll = accredited_poll(self.state, "client", version="4~game")
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"][0]["id"], command_id)
        status, result = self.state.store_result(
            {"id": command_id, "ok": 1}, instance=INST_CLIENT
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.state.take_result(command_id))
        self.assertIsNotNone(self.state.take_result(command_id, remove=True))
        self.assertIsNone(self.state.take_result(command_id))
        snapshot = self.state.status_snapshot()
        self.assertEqual(snapshot["peers"]["client"]["version"], "4~game")

    def test_drive_probe_client_routes_to_client_peer(self) -> None:
        self.assertEqual(loopback.peer_for_command("drive_probe_client"), "client")
        self.assertIn("drive_probe_client", loopback.WHITELISTED_COMMANDS)

        status, body = self.state.enqueue_command("drive_probe_client", {"throttle": 1.0})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "client")

        status, body = self.state.enqueue_command("drive_probe_client", {"throttle": 1.0}, peer="server")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

    def test_vehicle_trace_routes_to_client_and_uses_one_result_per_read_id(self) -> None:
        args = {
            "mode": "read",
            "trace_id": "a" * 32,
            "cursor": 0,
            "limit": 64,
            "sample_hz": 20,
            "max_samples": 4096,
        }
        first_status, first = self.state.enqueue_command("vehicle_trace", args)
        second_status, second = self.state.enqueue_command(
            "vehicle_trace", args | {"cursor": 64}
        )
        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual((first["peer"], second["peer"]), ("client", "client"))
        self.assertNotEqual(first["id"], second["id"])

        self.state.store_result(
            {"id": first["id"], "ok": 1, "cursor": 0}, instance=INST_CLIENT
        )
        self.state.store_result(
            {"id": second["id"], "ok": 1, "cursor": 64}, instance=INST_CLIENT
        )
        self.assertEqual(self.state.take_result(first["id"])["cursor"], 0)
        self.assertEqual(self.state.take_result(second["id"])["cursor"], 64)

    def test_query_get_in_condition_routes_to_server_peer(self) -> None:
        self.assertEqual(loopback.peer_for_command("query_get_in_condition"), "server")
        self.assertIn("query_get_in_condition", loopback.WHITELISTED_COMMANDS)

        status, body = self.state.enqueue_command("query_get_in_condition", {"pos": [1.0, 2.0, 3.0]})
        self.assertEqual(status, 200)
        self.assertEqual(body["peer"], "server")

        status, body = self.state.enqueue_command("query_get_in_condition", {"pos": [1.0, 2.0, 3.0]}, peer="client")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_peer"})

    def test_h0_server_primitives_route_and_validate_args(self) -> None:
        cases = [
            ("object_delete", {"object_id": 7}),
            ("notify_players", {"show_time": 5.0, "title": "Game Master", "detail": "", "icon": ""}),
        ]
        for verb, args in cases:
            with self.subTest(verb=verb):
                self.assertEqual(loopback.peer_for_command(verb), "server")
                self.assertIn(verb, loopback.WHITELISTED_COMMANDS)

                status, body = self.state.enqueue_command(verb, args)
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "server")

                status, body = self.state.enqueue_command(verb, args, peer="client")
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_peer"})

        bad_cases = [
            ("object_delete", {}),
            ("object_delete", {"object_id": 0}),
            ("object_delete", {"object_id": 7.5}),
            ("object_delete", {"object_id": "7"}),
            ("notify_players", {"show_time": 5.0}),
            ("notify_players", {"show_time": 0.0, "title": "Game Master"}),
            ("notify_players", {"show_time": 5.0, "title": ""}),
            ("notify_players", {"show_time": 5.0, "title": "Game Master", "detail": 3}),
            ("object_delete", {"object_id": 7, "unexpected": "x"}),
            ("notify_players", {"show_time": 5.0, "title": "Game Master", "unexpected": "x"}),
        ]
        for verb, args in bad_cases:
            with self.subTest(verb=verb, args=args):
                status, body = self.state.enqueue_command(verb, args)
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_args"})

        status, body = self.state.enqueue_command("not_a_command", {})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "not_whitelisted"})

        status, body = self.request(
            "POST",
            "/enqueue",
            {"cmd": "object_delete", "args": {"object_id": 7}},
            include_key=False,
        )
        self.assertEqual(status, 401)
        self.assertEqual(body, {"error": "unauthorized"})

    def test_vehicle_tramo_a_commands_route_to_client_peer(self) -> None:
        verbs = [
            "vehicle_get_in_client",
            "engine_set",
            "vehicle_control",
            "vehicle_telemetry",
            "vehicle_release",
        ]

        for verb in verbs:
            with self.subTest(verb=verb):
                self.assertEqual(loopback.peer_for_command(verb), "client")
                self.assertIn(verb, loopback.WHITELISTED_COMMANDS)

                status, body = self.state.enqueue_command(verb, {})
                self.assertEqual(status, 200)
                self.assertEqual(body["peer"], "client")

                status, body = self.state.enqueue_command(verb, {}, peer="server")
                self.assertEqual(status, 400)
                self.assertEqual(body, {"error": "bad_peer"})

    def test_vehicle_tramo_a_tools_are_registered(self) -> None:
        app, _runtime = server.build_app(
            server.ServerConfig(mode="embedded", key="test-key", port=0, log_sink=lambda _message: None)
        )
        tool_names = [
            "query_get_in_condition",
            "object_delete",
            "notify_players",
            "vehicle_get_in_client",
            "engine_set",
            "vehicle_control",
            "vehicle_telemetry",
            "vehicle_release",
        ]

        for name in tool_names:
            with self.subTest(name=name):
                tool = app._tool_manager.get_tool(name)  # type: ignore[attr-defined]
                self.assertIsNotNone(tool)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


COORDINATED_IDENTITY = {
    "platform": "codex",
    "pid": 101,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:00Z",
    "session_id": "owner-a",
    "task_label": "queue",
}


class DelayedAuthorizationCoordinator:
    def __init__(
        self,
        coordinator: SessionCoordinator,
        authorized: threading.Event,
        resume: threading.Event,
    ) -> None:
        self._coordinator = coordinator
        self._authorized = authorized
        self._resume = resume

    def authorize(self, *args, **kwargs):
        decision = self._coordinator.authorize(*args, **kwargs)
        self._authorized.set()
        if not self._resume.wait(2.0):
            raise RuntimeError("authorization_resume_timeout")
        return decision

    def __getattr__(self, name):
        return getattr(self._coordinator, name)


class OwnerScopedQueueStateTest(unittest.TestCase):
    def _coordinated_state(self, *, cleanup=None, audit=None, **state_kwargs):
        constructor = inspect.signature(loopback.ServerState.__init__).parameters
        enqueue = inspect.signature(loopback.ServerState.enqueue_command).parameters
        self.assertIn("coordination", constructor)
        self.assertTrue(enqueue["identity_payload"].kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertTrue(enqueue["lease_token"].kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertTrue(enqueue["operation_timeout_s"].kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertTrue(enqueue["internal"].kind is inspect.Parameter.KEYWORD_ONLY)
        for method in ("cancel_owner_pending", "pending_for_owner", "cleanup_owner"):
            self.assertTrue(hasattr(loopback.ServerState, method), method)

        clock = FakeClock()
        state = loopback.ServerState(
            "k", time_fn=clock, coordination=None, **state_kwargs
        )
        client = ClientIdentity.from_payload(COORDINATED_IDENTITY)
        audit_events: list[dict[str, object]] = []

        def record_audit(event: dict[str, object]):
            audit_events.append(event)
            return True if audit is None else audit(event)

        coordinator = SessionCoordinator(
            time_fn=clock,
            token_fn=lambda: "token-a",
            id_fn=lambda: "lease-a",
            audit=record_audit,
            cleanup=cleanup
            or (lambda session_id, lease_id, reason, vehicle_active: state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            )),
        )
        coordinator.test_audit_events = audit_events  # type: ignore[attr-defined]
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        bind_both_peers(state)
        status, active = coordinator.acquire(client, "queue test")
        self.assertEqual(status, 200)
        return state, coordinator, client, active["lease_token"], clock

    def test_owner_metadata_never_reaches_wire_and_result_clears_pin(self) -> None:
        state, coordinator, client, token, _clock = self._coordinated_state()
        status, queued = state.enqueue_command(
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            peer="server",
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
            operation_timeout_s=9999.0,
        )
        self.assertEqual(status, 200)
        command_id = queued["id"]
        self.assertEqual(state.pending_for_owner(client.session_id), 1)
        self.assertGreater(coordinator.status(client)["owner"]["expires_in_s"], 299.0)

        status, poll = accredited_poll(state, "server")
        self.assertEqual(status, 200)
        self.assertEqual(
            poll["commands"],
            [
                {
                    "id": command_id,
                    "cmd": "world_spawn",
                    "args": {"type": "X", "pos": [1, 2, 3]},
                }
            ],
        )

        state.store_result({"id": command_id, "ok": 1}, instance=INST_SERVER)
        self.assertLessEqual(coordinator.status(client)["owner"]["expires_in_s"], 120.0)
        self.assertEqual(state.pending_for_owner(client.session_id), 1)
        state.take_result(command_id, remove=True)
        self.assertEqual(state.pending_for_owner(client.session_id), 0)

    def test_vehicle_release_clears_cleanup_only_after_successful_result(self) -> None:
        for ok_value, expected_cleanup in [(1, False), (0, True)]:
            cleanup_flags: list[bool] = []

            def cleanup(
                _session_id: str,
                _lease_id: str,
                _reason: str,
                vehicle_active: bool,
            ) -> dict[str, object]:
                cleanup_flags.append(vehicle_active)
                return {
                    "cancelled": 0,
                    "vehicle_release_enqueued": int(vehicle_active),
                    "runs_released": [],
                }

            with self.subTest(ok=ok_value):
                state, coordinator, client, token, _clock = self._coordinated_state(
                    cleanup=cleanup
                )
                state.enqueue_command(
                    "vehicle_control",
                    {"throttle": 1.0},
                    peer="client",
                    identity_payload=COORDINATED_IDENTITY,
                    lease_token=token,
                )
                _, release = state.enqueue_command(
                    "vehicle_release",
                    {},
                    peer="client",
                    identity_payload=COORDINATED_IDENTITY,
                    lease_token=token,
                )
                accredited_poll(state, "client")
                state.store_result(
                    {"id": release["id"], "ok": ok_value}, instance=INST_CLIENT
                )
                coordinator.release(client, token)
                self.assertEqual(cleanup_flags[-1], expected_cleanup)

    def test_discarded_vehicle_release_preserves_cleanup_obligation(self) -> None:
        cleanup_flags: list[bool] = []

        def cleanup(
            _session_id: str,
            _lease_id: str,
            _reason: str,
            vehicle_active: bool,
        ) -> dict[str, object]:
            cleanup_flags.append(vehicle_active)
            return {
                "cancelled": 0,
                "vehicle_release_enqueued": int(vehicle_active),
                "runs_released": [],
            }

        state, coordinator, client, token, clock = self._coordinated_state(
            cleanup=cleanup
        )
        state.enqueue_command(
            "vehicle_control",
            {"throttle": 1.0},
            peer="client",
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
        )
        state.enqueue_command(
            "vehicle_release",
            {},
            peer="client",
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
        )
        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        accredited_poll(state, "client")
        coordinator.release(client, token)
        self.assertTrue(cleanup_flags[-1])

    def test_cancel_owner_pending_marks_only_owner_commands(self) -> None:
        state, _coordinator, client, token, _clock = self._coordinated_state()
        _, owned = state.enqueue_command(
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
        )
        _, unowned_read = state.enqueue_command(
            "query_player_state", {}, identity_payload={**COORDINATED_IDENTITY, "session_id": "reader-b"}
        )

        result = state.cancel_owner_pending(client.session_id, "owner_release")
        self.assertEqual(result, {"cancelled": 1})
        self.assertEqual(state.take_result(owned["id"])["error"], "owner_release")
        _, poll = accredited_poll(state, "server")
        self.assertEqual([command["id"] for command in poll["commands"]], [unowned_read["id"]])

    def test_cancel_owner_pending_drops_command_owner_entries(self) -> None:
        state, _coordinator, client, token, _clock = self._coordinated_state()
        owned_n = 3
        for _ in range(owned_n):
            status, _queued = state.enqueue_command(
                "world_spawn",
                {"type": "X", "pos": [1, 2, 3]},
                identity_payload=COORDINATED_IDENTITY,
                lease_token=token,
            )
            self.assertEqual(status, 200)
        self.assertEqual(state.pending_for_owner(client.session_id), owned_n)
        self.assertEqual(len(state._command_owner), owned_n)

        result = state.cancel_owner_pending(client.session_id, "owner_release")
        self.assertEqual(result, {"cancelled": owned_n})
        self.assertEqual(state._command_owner, {})
        self.assertEqual(state.pending_for_owner(client.session_id), 0)

    def test_stale_discard_finishes_operation_pin(self) -> None:
        state, coordinator, client, token, clock = self._coordinated_state()
        _, queued = state.enqueue_command(
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
            operation_timeout_s=300.0,
        )
        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        _, poll = accredited_poll(state, "server")
        self.assertEqual(poll["commands"], [])
        self.assertEqual(state.take_result(queued["id"])["error"], "stale_discarded")
        self.assertLess(coordinator.status(client)["owner"]["expires_in_s"], 100.0)

    def test_release_cannot_race_authorize_then_append_into_zombie_command(self) -> None:
        cleanup_started = threading.Event()
        state_holder: list[loopback.ServerState] = []

        def cleanup(
            session_id: str, lease_id: str, reason: str, vehicle_active: bool
        ):
            cleanup_started.set()
            return state_holder[0].cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            )

        state, coordinator, client, token, _clock = self._coordinated_state(cleanup=cleanup)
        state_holder.append(state)
        authorized = threading.Event()
        resume = threading.Event()
        state.coordination = DelayedAuthorizationCoordinator(coordinator, authorized, resume)
        enqueue_result: list[tuple[int, dict]] = []
        release_result: list[tuple[int, dict]] = []

        enqueue_thread = threading.Thread(
            target=lambda: enqueue_result.append(
                state.enqueue_command(
                    "world_spawn",
                    {"type": "X", "pos": [1, 2, 3]},
                    identity_payload=COORDINATED_IDENTITY,
                    lease_token=token,
                )
            )
        )
        enqueue_thread.start()
        self.assertTrue(authorized.wait(1.0))

        release_thread = threading.Thread(
            target=lambda: release_result.append(coordinator.release(client, token))
        )
        release_thread.start()
        self.assertTrue(cleanup_started.wait(1.0))
        resume.set()
        enqueue_thread.join(timeout=2.0)
        release_thread.join(timeout=2.0)
        self.assertFalse(enqueue_thread.is_alive())
        self.assertFalse(release_thread.is_alive())
        self.assertEqual(enqueue_result[0], (409, {"error": "lease_invalid"}))
        self.assertEqual(release_result[0][0], 200)

        _, poll = accredited_poll(state, "server")
        self.assertEqual(poll["commands"], [])
        self.assertEqual(state.pending_for_owner(client.session_id), 0)

    def _assert_post_authorize_rejection_aborts(
        self,
        state,
        coordinator,
        client,
        token,
        command: str,
        args: dict,
        expected_status: int,
        expected_error: str,
        *,
        peer: str | None = None,
    ) -> None:
        status, payload = state.enqueue_command(
            command,
            args,
            peer=peer,
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
            operation_timeout_s=9999.0,
        )
        self.assertEqual((status, payload["error"]), (expected_status, expected_error))
        self.assertEqual(coordinator._active.pending_authorizations, [])  # type: ignore[attr-defined]
        self.assertLessEqual(coordinator.status(client)["owner"]["expires_in_s"], 120.0)
        rejected = [
            event
            for event in coordinator.test_audit_events  # type: ignore[attr-defined]
            if event["event"] == "session_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], expected_error)
        self.assertEqual(rejected[-1]["decision"], "authorization_aborted")

    def test_post_authorize_whitelist_args_and_peer_rejections_abort(self) -> None:
        cases = [
            ("brand_new_tool", {}, None, 400, "not_whitelisted"),
            ("object_delete", {}, None, 400, "bad_args"),
            (
                "world_spawn",
                {"type": "X", "pos": [1, 2, 3]},
                "client",
                400,
                "bad_peer",
            ),
        ]
        for command, args, peer, expected_status, expected_error in cases:
            with self.subTest(error=expected_error):
                state, coordinator, client, token, _clock = self._coordinated_state()
                self._assert_post_authorize_rejection_aborts(
                    state,
                    coordinator,
                    client,
                    token,
                    command,
                    args,
                    expected_status,
                    expected_error,
                    peer=peer,
                )

    def test_post_authorize_version_blocked_aborts(self) -> None:
        state, coordinator, client, token, _clock = self._coordinated_state(
            version_validator=lambda _version: "legacy_blocked"
        )
        self._assert_post_authorize_rejection_aborts(
            state,
            coordinator,
            client,
            token,
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            409,
            "version_blocked",
        )
        status, payload = state.enqueue_command(
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            peer="server",
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
            operation_timeout_s=15.0,
        )
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "version_blocked")
        self.assertEqual(payload["expected"], loopback.EXPECTED_BRIDGE_VERSION)
        self.assertEqual(payload["state"], "legacy_blocked")

    def test_lease_required_includes_version_block_fields(self) -> None:
        state, coordinator, _client, _token, _clock = self._coordinated_state(
            version_validator=lambda _version: "version_mismatch"
        )
        status, payload = state.enqueue_command(
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            peer="server",
            identity_payload=COORDINATED_IDENTITY,
            lease_token=None,
            operation_timeout_s=15.0,
        )
        self.assertEqual(payload["error"], "lease_required")
        self.assertEqual(payload["version_state"], "version_mismatch")
        self.assertEqual(payload["expected"], loopback.EXPECTED_BRIDGE_VERSION)
        self.assertIn(status, {403, 423})

    def test_post_authorize_queue_full_aborts(self) -> None:
        state, coordinator, client, token, _clock = self._coordinated_state()
        with state._lock:
            state._queues["server"] = [
                {"id": 1000 + index, "cmd": "query_player_state", "args": {}}
                for index in range(loopback.MAX_QUEUE)
            ]
        self._assert_post_authorize_rejection_aborts(
            state,
            coordinator,
            client,
            token,
            "world_spawn",
            {"type": "X", "pos": [1, 2, 3]},
            429,
            "queue_full",
        )

    def test_post_authorize_exec_policy_and_audit_rejections_abort(self) -> None:
        denied, denied_coord, denied_client, denied_token, _ = self._coordinated_state(
            enable_exec_enforce=True
        )
        self._assert_post_authorize_rejection_aborts(
            denied,
            denied_coord,
            denied_client,
            denied_token,
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            403,
            "exec_not_allowed",
        )

        def fail_exec_audit(*_args):
            raise OSError("exec audit failed")

        failed, failed_coord, failed_client, failed_token, _ = self._coordinated_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=fail_exec_audit,
        )
        self._assert_post_authorize_rejection_aborts(
            failed,
            failed_coord,
            failed_client,
            failed_token,
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            503,
            "audit_failed",
        )

    def test_post_authorize_late_exec_queue_full_aborts(self) -> None:
        state_holder = []

        def fill_queue_during_audit(*_args):
            state = state_holder[0]
            state._queues["server"] = [
                {"id": 2000 + index, "cmd": "query_player_state", "args": {}}
                for index in range(loopback.MAX_QUEUE)
            ]

        state, coordinator, client, token, _clock = self._coordinated_state(
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=fill_queue_during_audit,
        )
        state_holder.append(state)
        self._assert_post_authorize_rejection_aborts(
            state,
            coordinator,
            client,
            token,
            "exec_enforce",
            {"expr": "probe()", "main_fn": "Main"},
            429,
            "queue_full",
        )

    def test_abort_audit_failure_is_merged_without_changing_primary_error(self) -> None:
        def fail_abort_audit(event):
            return not (
                event["event"] == "session_rejected"
                and event.get("decision") == "authorization_aborted"
            )

        state, coordinator, _client, token, _clock = self._coordinated_state(
            audit=fail_abort_audit
        )
        status, payload = state.enqueue_command(
            "object_delete",
            {},
            identity_payload=COORDINATED_IDENTITY,
            lease_token=token,
            operation_timeout_s=9999.0,
        )
        self.assertEqual((status, payload["error"]), (400, "bad_args"))
        self.assertIn("cleanup_degraded", payload)
        self.assertEqual(payload["cleanup_degraded"], ["audit_failed"])
        self.assertEqual(coordinator._active.pending_authorizations, [])  # type: ignore[attr-defined]

    def test_authorized_reservation_is_aborted_when_enqueue_raises(self) -> None:
        state, coordinator, _client, token, _clock = self._coordinated_state()
        original_enqueue = state._enqueue_command

        def raise_after_authorize(*_args, **_kwargs):
            raise RuntimeError("test_enqueue_exception")

        state._enqueue_command = raise_after_authorize  # type: ignore[method-assign]
        try:
            with self.assertRaisesRegex(RuntimeError, "test_enqueue_exception"):
                state.enqueue_command(
                    "world_spawn",
                    {"type": "X", "pos": [1, 2, 3]},
                    identity_payload=COORDINATED_IDENTITY,
                    lease_token=token,
                    operation_timeout_s=300.0,
                )
        finally:
            state._enqueue_command = original_enqueue  # type: ignore[method-assign]

        active = coordinator._active  # type: ignore[attr-defined]
        self.assertEqual(active.pending_authorizations, [])
        self.assertEqual(active.operation_pins, {})
        rejected = [
            event
            for event in coordinator.test_audit_events  # type: ignore[attr-defined]
            if event["event"] == "session_rejected"
        ]
        self.assertEqual(rejected[-1]["reason"], "enqueue_exception")
        self.assertEqual(rejected[-1]["decision"], "authorization_aborted")

    def test_internal_vehicle_release_result_is_purged_without_owner_mapping(self) -> None:
        state = loopback.ServerState("k")
        bind_both_peers(state)
        cleanup = state.cleanup_owner(
            "released-owner", "released-lease", "owner_release", True
        )
        self.assertEqual(cleanup["vehicle_release_enqueued"], 1)
        self.assertEqual(state.pending_for_owner("released-owner"), 0)

        _, poll = accredited_poll(state, "client")
        self.assertEqual(len(poll["commands"]), 1)
        command_id = poll["commands"][0]["id"]
        self.assertEqual(poll["commands"][0]["cmd"], "vehicle_release")
        self.assertNotIn(command_id, state._command_owner)
        self.assertIn(command_id, state._fire_and_forget_ids)

        status, _ = state.store_result(
            {"id": command_id, "ok": 1}, instance=INST_CLIENT
        )
        self.assertEqual(status, 200)
        self.assertIsNone(state.take_result(command_id))
        self.assertNotIn(command_id, state._fire_and_forget_ids)
        self.assertEqual(state.status_snapshot()["results_pending"], 0)
        self.assertEqual(state.pending_for_owner("released-owner"), 0)

    def test_internal_vehicle_release_ttl_discard_purges_result_and_id(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        bind_both_peers(state)
        state.cleanup_owner(
            "released-owner", "released-lease", "owner_release", True
        )
        command_id = state._bound_queues[INST_CLIENT][0]["id"]

        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        _, poll = accredited_poll(state, "client")
        self.assertEqual(poll["commands"], [])
        self.assertIsNone(state.take_result(command_id))
        self.assertNotIn(command_id, state._fire_and_forget_ids)
        self.assertEqual(state.status_snapshot()["results_pending"], 0)
        self.assertEqual(state.pending_for_owner("released-owner"), 0)

    def test_internal_vehicle_release_survives_reconnect_gap_on_bound_queue(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        bind_both_peers(state)
        accredited_poll(state, "client")
        state.cleanup_owner(
            "released-owner", "released-lease", "owner_release", True
        )
        command_id = state._bound_queues[INST_CLIENT][0]["id"]

        clock.advance(loopback.PEER_RECONNECT_GAP_S + 1.0)
        _, poll = accredited_poll(state, "client")
        # Bound queues are not flushed on reconnect gap.
        self.assertEqual([command["id"] for command in poll["commands"]], [command_id])
        status, _ = state.store_result(
            {"id": command_id, "ok": 1}, instance=INST_CLIENT
        )
        self.assertEqual(status, 200)
        self.assertIsNone(state.take_result(command_id))
        self.assertNotIn(command_id, state._fire_and_forget_ids)
        self.assertEqual(state.status_snapshot()["results_pending"], 0)
        self.assertEqual(state.pending_for_owner("released-owner"), 0)


class StaleCommandHygieneTest(unittest.TestCase):
    """The daemon must not deliver a previous session's queued commands
    to a freshly (re)connected peer. record_poll expires by TTL and flushes on a
    reconnect gap. A fake clock makes both paths deterministic."""

    def test_command_ttl_drops_aged_command(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        command_id = payload["id"]

        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        status, poll = state.record_poll("client")
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"], [])

        result = state.take_result(command_id)
        self.assertIsNotNone(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stale_discarded")

    def test_command_within_ttl_is_delivered(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        command_id = payload["id"]

        clock.advance(loopback.COMMAND_TTL_S - 1.0)
        _status, poll = state.record_poll("client")
        self.assertEqual([c["id"] for c in poll["commands"]], [command_id])
        self.assertIsNone(state.take_result(command_id))

    def test_reconnect_flush_drops_previous_session_command(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        # Session N establishes a last-poll, then a command is queued but unpolled.
        state.record_poll("client")
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        command_id = payload["id"]
        # The fresh client's first poll arrives after a long gap (below the TTL, to
        # isolate the flush path from the TTL path).
        clock.advance(loopback.PEER_RECONNECT_GAP_S + 1.0)
        _status, poll = state.record_poll("client")
        self.assertEqual(poll["commands"], [])

        result = state.take_result(command_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["error"], "peer_reconnect_flush")

    def test_small_gap_delivers_command(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        state.record_poll("client")
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        command_id = payload["id"]
        clock.advance(loopback.PEER_RECONNECT_GAP_S - 1.0)
        _status, poll = state.record_poll("client")
        self.assertEqual([c["id"] for c in poll["commands"]], [command_id])
        self.assertIsNone(state.take_result(command_id))

    def test_first_poll_never_flushes(self) -> None:
        # A command queued before the peer's very first poll is legitimate: with no
        # prior poll there is no measurable gap, so the flush must not fire.
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        _status, payload = state.enqueue_command("camera_get", {}, peer="client")
        command_id = payload["id"]
        clock.advance(5.0)
        _status, poll = state.record_poll("client")
        self.assertEqual([c["id"] for c in poll["commands"]], [command_id])

    def test_client_flush_leaves_server_queue_untouched(self) -> None:
        clock = FakeClock()
        state = loopback.ServerState("k", time_fn=clock)
        state.record_poll("client")
        _status, payload = state.enqueue_command("query_player_state", {}, peer="server")
        server_cmd_id = payload["id"]
        clock.advance(loopback.PEER_RECONNECT_GAP_S + 1.0)
        # Client reconnect flush must touch only the client queue.
        state.record_poll("client")
        snapshot = state.status_snapshot()
        self.assertEqual(snapshot["peers"]["server"]["queue_depth"], 1)
        self.assertIsNone(state.take_result(server_cmd_id))

    def test_exec_enforce_discard_records_audit(self) -> None:
        audit_calls: list[tuple] = []
        clock = FakeClock()
        state = loopback.ServerState(
            "k",
            enable_exec_enforce=True,
            exec_allowlist={"probe()"},
            exec_audit=lambda *a: audit_calls.append(a),
            time_fn=clock,
        )
        bind_both_peers(state)
        status, payload = state.enqueue_command(
            "exec_enforce", {"expr": "probe()", "main_fn": "Main"}, peer="server"
        )
        self.assertEqual(status, 200)
        command_id = payload["id"]
        self.assertIn(("probe()", "allowed", "Main", command_id), audit_calls)

        clock.advance(loopback.COMMAND_TTL_S + 1.0)
        state.record_poll("server")
        # Dropped before delivery -> a 'discarded' audit keeps the ledger equal to
        # commands that actually reached the game.
        self.assertIn(("probe()", "discarded", "Main", command_id), audit_calls)
        self.assertEqual(state.take_result(command_id)["error"], "stale_discarded")


class LoopbackHttpBodyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.key = "test-key"
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)
        self.httpd = loopback.create_http_server(0, self.state, log_sink=lambda _message: None)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2.0)
        self.httpd.server_close()

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        include_key: bool = True,
    ) -> tuple[int, dict]:
        params = dict(query or {})
        if include_key:
            params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=2.0) as response:
                raw = response.read().decode("utf-8")
                return int(response.status), json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8")
                return int(exc.code), json.loads(raw)
            finally:
                exc.close()

    def _raw_http(self, request: bytes, timeout: float = 2.0) -> tuple[int, dict]:
        parsed = urllib.parse.urlparse(self.base)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        finally:
            sock.close()
        raw = b"".join(chunks)
        header, sep, body = raw.partition(b"\r\n\r\n")
        if not sep:
            raise AssertionError(f"no HTTP header separator in {raw!r}")
        status = int(header.split(b"\r\n", 1)[0].split()[1])
        if not body:
            return status, {}
        return status, json.loads(body.decode("utf-8"))

    def test_ui_dialog_enqueue_well_formed_and_rejects_seven_fields(self) -> None:
        status, body = self.request(
            "POST",
            "/enqueue",
            {
                "cmd": "ui_dialog",
                "args": {
                    "kind": "acknowledge",
                    "title": "Ready",
                    "message": "Mod loaded.",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("id", body)
        status, poll = self.request(
            "GET", "/poll", query={"peer": "client", "inst": INST_CLIENT}
        )
        self.assertEqual(status, 200)
        self.assertEqual(poll["commands"][0]["cmd"], "ui_dialog")

        status, body = self.request(
            "POST",
            "/enqueue",
            {
                "cmd": "ui_dialog",
                "args": {
                    "kind": "form",
                    "title": "Form",
                    "fields": [
                        {"id": f"f{index}", "label": f"Field {index}"}
                        for index in range(7)
                    ],
                },
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})

    def test_ui_dialog_unhashable_kind_is_bad_args(self) -> None:
        status, body = self.request(
            "POST",
            "/enqueue",
            {"cmd": "ui_dialog", "args": {"kind": {}, "title": "T"}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_args"})

    def test_read_json_rejects_negative_content_length(self) -> None:
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Length: -1\r\n"
                "\r\n"
            ).encode("ascii")
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_content_length"})

    def test_read_json_rejects_non_numeric_content_length(self) -> None:
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Length: nope\r\n"
                "\r\n"
            ).encode("ascii")
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_content_length"})

    def test_read_json_rejects_oversize_without_reading_body(self) -> None:
        too_big = loopback.MAX_BODY_BYTES + 1
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                f"Content-Length: {too_big}\r\n"
                "\r\n"
            ).encode("ascii")
        )
        self.assertEqual(status, 413)
        self.assertEqual(body, {"error": "body_too_large"})

    def test_read_json_rejects_invalid_utf8(self) -> None:
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Length: 1\r\n"
                "\r\n"
            ).encode("ascii")
            + b"\xff"
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_json"})

    def test_read_json_rejects_short_body(self) -> None:
        payload = b'{"cmd":"x"}'
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Length: 20\r\n"
                "\r\n"
            ).encode("ascii")
            + payload
        )
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "bad_body_length"})

    def test_read_json_happy_path_still_enqueues(self) -> None:
        payload = json.dumps(
            {"cmd": "query_player_state", "args": {}}, separators=(",", ":")
        ).encode("utf-8")
        status, body = self._raw_http(
            (
                f"POST /enqueue?key={self.key} HTTP/1.0\r\n"
                "Host: 127.0.0.1\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n"
                "\r\n"
            ).encode("ascii")
            + payload
        )
        self.assertEqual(status, 200)
        self.assertIn("id", body)

    def test_idle_tcp_connections_do_not_spawn_past_http_worker_ceiling(self) -> None:
        # F-03: a connecting socket used to cost a thread before auth.
        self.assertEqual(loopback.MAX_HTTP_WORKERS, MAX_SESSION_QUEUE + 32)
        parsed = urllib.parse.urlparse(self.base)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 80)
        extra = 8
        baseline = threading.active_count()
        socks: list[socket.socket] = []
        try:
            for _ in range(loopback.MAX_HTTP_WORKERS + extra):
                sock = socket.create_connection((host, port), timeout=2.0)
                sock.settimeout(0.5)
                socks.append(sock)
            deadline = time.monotonic() + 5.0
            peak = 0
            while time.monotonic() < deadline:
                peak = max(peak, threading.active_count() - baseline)
                if peak >= loopback.MAX_HTTP_WORKERS:
                    time.sleep(0.15)
                    peak = max(peak, threading.active_count() - baseline)
                    break
                time.sleep(0.01)
            self.assertGreaterEqual(
                peak,
                loopback.MAX_HTTP_WORKERS,
                "accept loop never filled the worker ceiling; test would be vacuous",
            )
            self.assertLessEqual(
                peak,
                loopback.MAX_HTTP_WORKERS + 3,
                f"idle TCP spawned {peak} threads (ceiling {loopback.MAX_HTTP_WORKERS})",
            )
        finally:
            for sock in socks:
                try:
                    sock.close()
                except OSError:
                    pass
        deadline = time.monotonic() + 2.0
        while threading.active_count() > baseline + 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        status, _body = self.request("GET", "/status")
        self.assertEqual(status, 200)


class ResultCapTests(unittest.TestCase):
    def test_results_are_capped_dropping_oldest_first(self) -> None:
        # F-11: /await defaults to remove=0, so finished results used to grow
        # without a bound. Oldest insertion is the one that must disappear.
        state = loopback.ServerState("result-cap-key")
        bind_both_peers(state)
        ids: list[int] = []
        overflow = 3
        for _ in range(loopback.MAX_RESULTS + overflow):
            status, body = state.enqueue_command("query_player_state", {})
            self.assertEqual(status, 200)
            accredited_poll(state, "server")
            store_status, _stored = state.store_result(
                {"id": body["id"], "ok": 1}, instance=INST_SERVER
            )
            self.assertEqual(store_status, 200)
            ids.append(body["id"])
        self.assertEqual(state.status_snapshot()["results_pending"], loopback.MAX_RESULTS)
        for evicted_id in ids[:overflow]:
            self.assertIsNone(state.take_result(evicted_id))
        self.assertIsNotNone(state.take_result(ids[-1]))
        self.assertIsNotNone(state.take_result(ids[overflow]))


if __name__ == "__main__":
    unittest.main()

