from __future__ import annotations

import json
import io
import sys
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from dayz_mcp import dayz_test_request as request
from dayz_mcp import steam_launch_guard as guard
from dayz_mcp import steam_preflight as sp
from dayz_mcp.steam_prepare_helper import GuardedHost
from dayz_mcp.steam_prepare_supervisor import Helper, SteamPreparationGate
from tests.test_steamfastpath_repair import MemoryProvider, MemoryHost
from tests import test_process_lifecycle as lifecycle
from tests import test_dayz_test_worker as worker_tests


WALL = 2_000_000_000.0


def ticks(age):
    return int((WALL - age) * 10_000_000) + 116444736000000000


class Provider(MemoryProvider):
    def __init__(self, *, age=1000, stale=False, marker=False):
        super().__init__()
        self.pid = 99 if stale else 41
        self.created = ticks(age)
        self.marker = marker
        self.marker_reads = 0

    def process_creation_ticks(self, pid):
        return self.created

    def steam_startup_complete(self, pid):
        self.marker_reads += 1
        return self.marker


class Host(MemoryHost):
    def __init__(self, provider):
        super().__init__(provider)
        self.cancelled = False
        self.on_sleep = None
        self.deadline = guard.PREPARE_BUDGET_S

    def checkpoint(self):
        if self.cancelled:
            raise guard.PreparationCancelled()
        if self.now >= self.deadline:
            raise guard.PreparationCancelled("steam_prepare_timeout")

    def sleep(self, seconds):
        self.checkpoint()
        self.now += min(seconds, self.deadline - self.now)
        if self.on_sleep:
            self.on_sleep()
        self.checkpoint()

    def invoke_steam(self, executable, args):
        super().invoke_steam(executable, args)
        if args == ("-silent",):
            self.provider.created = ticks(0)  # restart can reuse the same PID


class SteamLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sp.subprocess, "Popen", side_effect=AssertionError("real host forbidden")))
        self.enterContext(patch.object(sp.WindowsSteamPreflightProvider, "read_active_process",
                                      side_effect=AssertionError("real registry forbidden")))

    def prepare(self, provider, consent=False, host=None):
        host = host or Host(provider)
        return guard.prepare(provider, host, consent, wall_time=lambda: WALL), host

    def test_old_stable_log_rotated_is_explicit_limited_pass_without_mutation(self):
        result, host = self.prepare(Provider())
        self.assertIsNone(result.error_code)
        self.assertEqual(result.startup, "old_stable_unobserved")
        self.assertEqual(host.now, 0)
        self.assertEqual(host.writes, [])
        self.assertEqual(host.invocations, [])

    def test_old_stale_registry_pid_repair_does_not_require_missing_marker(self):
        result, host = self.prepare(Provider(stale=True), True)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.pid_repair, "applied")
        self.assertEqual(result.startup, "old_stable_unobserved")
        self.assertEqual(host.writes, [41])
        self.assertEqual(host.invocations, [])
        self.assertEqual(host.now, 0)

    def test_recent_coherent_registry_waits_read_only_for_marker(self):
        provider = Provider(age=1)
        host = Host(provider)
        host.on_sleep = lambda: setattr(provider, "marker", host.now >= 2)
        result, host = self.prepare(provider, host=host)
        self.assertGreaterEqual(host.now, 2)
        self.assertEqual(result.startup, "observed")
        self.assertEqual(host.writes + host.invocations, [])

    def test_recent_cannot_age_into_old_branch_during_wait(self):
        provider = Provider(age=179)
        host = Host(provider)
        with self.assertRaises(guard.PreparationCancelled) as raised:
            guard.prepare(provider, host, False, wall_time=lambda: WALL + host.now)
        self.assertEqual(raised.exception.code, "steam_prepare_timeout")
        self.assertEqual(host.now, guard.PREPARE_BUDGET_S)

    def test_same_pid_recreated_during_wait_is_not_accepted(self):
        provider = Provider(age=1)
        host = Host(provider)
        def replace():
            provider.created += 10000000
            provider.marker = True
        host.on_sleep = replace
        result, _ = self.prepare(provider, host=host)
        self.assertEqual(result.error_code, "steam_identity_changed")

    def test_restart_of_old_steam_requires_new_lifetime_marker(self):
        provider = Provider(stale=True)
        host = Host(provider)
        host.write_error = OSError("fake failed write")
        host.on_sleep = lambda: setattr(provider, "marker", host.now >= 3)
        result, _ = self.prepare(provider, True, host)
        self.assertTrue(result.restarted)
        self.assertGreaterEqual(host.now, 3)
        self.assertEqual(result.startup, "observed")

    def test_no_consent_stale_does_not_write_or_invoke(self):
        result, host = self.prepare(Provider(stale=True))
        self.assertEqual(result.error_code, "steam_session_stale")
        self.assertEqual(host.writes + host.invocations, [])

    def test_final_check_detects_registry_change_and_pid_reuse(self):
        for field, value in (("pid", 99), ("created", ticks(1))):
            provider = Provider(marker=True)
            result, _ = self.prepare(provider)
            setattr(provider, field, value)
            self.assertFalse(guard.final_check(result, provider))

    def test_cancellation_inside_wait_cannot_be_swallowed_as_probe_failure(self):
        provider = Provider(age=1)
        host = Host(provider)
        host.on_sleep = lambda: setattr(host, "cancelled", True)
        with self.assertRaises(guard.PreparationCancelled):
            self.prepare(provider, True, host)
        self.assertEqual(host.writes + host.invocations, [])

    def test_creation_identity_must_be_readable_even_for_old_steam(self):
        provider = Provider()
        provider.process_creation_ticks = lambda pid: None
        result, _ = self.prepare(provider)
        self.assertEqual(result.error_code, "steam_session_stale")


class ScriptedHelper:
    ready = True
    def __init__(self, messages=(), drain_ok=True):
        self.messages = list(messages)
        self.sent = []
        self.drain_ok = drain_ok
        self.drains = []
        self.on_receive = None

    def send(self, value):
        self.sent.append(value)

    def receive(self, timeout):
        if self.on_receive:
            self.on_receive()
        return self.messages.pop(0) if self.messages else {"eof": True}

    def drain(self, budget=5):
        self.drains.append(budget)
        return self.drain_ok


class SupervisorTests(unittest.TestCase):
    def test_each_mutation_requires_current_authority_and_client_exclusion(self):
        for consent, permitted in ((False, True), (True, False)):
            helper = ScriptedHelper([{"mutation": True}])
            gate = SteamPreparationGate(helper_factory=lambda: helper)
            self.assertTrue(gate.claim())
            try:
                result = gate.prepare(consent=consent, authority_active=lambda: True,
                                      mutation_allowed=lambda: permitted)
            finally:
                gate.release()
            self.assertEqual(result.error_code, "steam_client_active")
            self.assertNotIn({"permit": True}, helper.sent)
            self.assertEqual(helper.drains, [5])

    def test_authority_revoked_between_request_and_permit_never_sends_permit(self):
        active = [True]
        helper = ScriptedHelper([{"mutation": True}])
        helper.on_receive = lambda: active.__setitem__(0, False)
        gate = SteamPreparationGate(helper_factory=lambda: helper)
        result = gate.prepare(consent=True, authority_active=lambda: active[0], mutation_allowed=lambda: True)
        self.assertIsNotNone(result.error_code)
        self.assertNotIn({"permit": True}, helper.sent)

    def test_unverified_helper_exit_remains_fenced_until_verified_reconciliation(self):
        helper = ScriptedHelper(drain_ok=False)
        gate = SteamPreparationGate(helper_factory=lambda: helper)
        self.assertTrue(gate.claim())
        result = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=lambda: True)
        gate.release()
        self.assertEqual(result.error_code, "steam_cleanup_degraded")
        self.assertTrue(gate.degraded)
        self.assertFalse(gate.claim())
        helper.drain_ok = True
        self.assertTrue(gate.claim())
        self.assertFalse(gate.degraded)
        gate.release()

    def test_job_setup_failure_still_verifies_helper_exit(self):
        helper = ScriptedHelper(drain_ok=False)
        helper.ready = False
        gate = SteamPreparationGate(helper_factory=lambda: helper)
        result = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=lambda: True)
        self.assertEqual(result.error_code, "steam_cleanup_degraded")
        self.assertTrue(gate.degraded)

    def test_cleanup_exception_cannot_release_the_steam_fence(self):
        helper = ScriptedHelper()
        helper.drain = lambda budget: (_ for _ in ()).throw(OSError("unreadable handle"))
        gate = SteamPreparationGate(helper_factory=lambda: helper)
        result = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=lambda: True)
        self.assertEqual(result.error_code, "steam_cleanup_degraded")
        self.assertFalse(gate.claim())

    def test_slow_client_probe_cannot_delay_deadline_or_send_a_late_permit(self):
        resume = threading.Event()
        finished = threading.Event()
        helper = ScriptedHelper([{"mutation": True}])
        def probe():
            resume.wait(2)
            finished.set()
            return True
        gate = SteamPreparationGate(helper_factory=lambda: helper, budget_s=0.15)
        try:
            result = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=probe)
            self.assertEqual(result.error_code, "steam_prepare_timeout")
            self.assertNotIn({"permit": True}, helper.sent)
        finally:
            resume.set()
        self.assertTrue(finished.wait(1))
        self.assertNotIn({"permit": True}, helper.sent)

    def test_pending_probe_blocks_more_mutations_but_allows_healthy_readiness(self):
        resume = threading.Event()
        done = threading.Event()
        healthy = guard.Preparation(identity=guard.SteamIdentity(41, ticks(1000)), startup="observed")
        helpers = iter((ScriptedHelper([{"mutation": True}]),
                        ScriptedHelper([{"mutation": True}]),
                        ScriptedHelper([{"result": healthy.payload()}])))
        gate = SteamPreparationGate(helper_factory=lambda: next(helpers), budget_s=0.05)
        def probe():
            resume.wait(2)
            done.set()
            return True
        try:
            first = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=probe)
            self.assertEqual(first.error_code, "steam_prepare_timeout")
            second = gate.prepare(consent=True, authority_active=lambda: True, mutation_allowed=probe)
            self.assertEqual(second.error_code, "steam_probe_pending")
            third = gate.prepare(consent=False, authority_active=lambda: True, mutation_allowed=probe)
            self.assertIsNone(third.error_code)
        finally:
            resume.set()
        self.assertTrue(done.wait(1))

    def test_lifecycle_lock_contention_is_not_reported_as_an_active_client(self):
        helper = ScriptedHelper([{"mutation": True}])
        result = SteamPreparationGate(helper_factory=lambda: helper).prepare(
            consent=True, authority_active=lambda: True, mutation_allowed=lambda: "steam_prepare_busy")
        self.assertEqual(result.error_code, "steam_prepare_busy")
        self.assertNotIn({"permit": True}, helper.sent)

    def test_hung_real_helper_is_terminated_after_deadline_without_host_access(self):
        created = []
        def factory():
            # A harmless Python process deliberately ignores cooperative stdin.
            helper = Helper([sys.executable, "-c", "import time; time.sleep(60)"])
            created.append(helper)
            return helper
        gate = SteamPreparationGate(helper_factory=factory, budget_s=0.3)
        started = time.monotonic()
        result = gate.prepare(consent=False, authority_active=lambda: True, mutation_allowed=lambda: False)
        self.assertEqual(result.error_code, "steam_prepare_timeout")
        self.assertLess(time.monotonic() - started, 5.5)
        self.assertIsNotNone(created[0].process.poll())
        self.assertFalse(gate.degraded)

    def test_hung_real_helper_is_terminated_on_authority_loss(self):
        created = []
        active = [True]
        def factory():
            helper = Helper([sys.executable, "-c", "import time; time.sleep(60)"])
            created.append(helper)
            active[0] = False
            return helper
        result = SteamPreparationGate(helper_factory=factory).prepare(
            consent=False, authority_active=lambda: active[0], mutation_allowed=lambda: False)
        self.assertEqual(result.error_code, "steam_prepare_cancelled")
        self.assertIsNotNone(created[0].process.poll())


class SteamAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = lifecycle.ProcessLifecycleTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.life = self.f.lifecycle
        self.gate = self.life.steam_gate
        self.f.guard.snapshots[9001] = lifecycle.snapshot(lifecycle.process(9001))

    def start(self, **overrides):
        return self.life.start_run(lifecycle.IDENTITY_A, self.f.token_a, self.f.request() | overrides)

    def test_nonexistent_run_never_enters_preparation(self):
        self.assertEqual(self.start(run_id="missing", auto_remediate_steam=True)["error"], "run_not_found")
        self.assertEqual(self.gate.preparations, [])

    def test_foreign_run_never_enters_preparation(self):
        run = self.f.add_run(lifecycle.process(55))
        run.owner_session_id = "another-owner"
        self.f.store.replace(run)
        self.assertEqual(self.start(run_id=run.run_id)["error"], "run_not_adopted")
        self.assertEqual(self.gate.preparations, [])

    def test_wait_has_no_starting_row_or_instance_and_does_not_hold_operation_lock(self):
        acquired = []
        def action(**kwargs):
            self.assertEqual(self.f.store.list_runs(), [])
            self.assertEqual(self.f.launcher.calls, [])
            self.assertEqual(self.f.guard.terminate_calls, [])
            def other():
                ok = self.life._operation_lock.acquire(timeout=0.5)
                acquired.append(ok)
                if ok:
                    self.life._operation_lock.release()
            t = threading.Thread(target=other)
            t.start()
            t.join(1)
            self.assertTrue(kwargs["authority_active"]())
            return guard.Preparation(identity=guard.SteamIdentity(41, ticks(1000)))
        self.gate.action = action
        self.assertTrue(self.start()["ok"])
        self.assertEqual(acquired, [True])
        self.assertEqual(len(self.gate.preparations), 1)

    def test_full_admission_rechecks_quarantine_and_foreign_process_after_wait(self):
        for dimension in ("quarantine", "foreign"):
            with self.subTest(dimension=dimension):
                def action(**kwargs):
                    if dimension == "quarantine":
                        self.f.probe_result = {"known": False}
                    else:
                        self.life.diag_probe = lambda: {"known": True, "processes": [{"pid": 888}]}
                    return guard.Preparation(identity=guard.SteamIdentity(41, ticks(1000)))
                self.gate.action = action
                result = self.start()
                self.assertFalse(result.get("ok", False))
                self.assertEqual(self.f.launcher.calls, [])
                self.assertEqual(self.f.store.list_runs(), [])
                self.f.probe_result = {"known": True, "processes": []}
                self.life.diag_probe = lambda: {"known": True, "processes": []}

    def test_final_identity_check_prevents_spawn(self):
        checks = iter((True, False))
        self.gate.final_check = lambda prepared: next(checks)
        result = self.start()
        self.assertEqual(result["error"], "steam_identity_changed")
        self.assertIn("run_id", result)
        self.assertIsNone(worker_tests.dayz_test_worker._pre_admission_rejection(result))
        self.assertEqual(self.f.launcher.calls, [])

    def test_server_skips_steam(self):
        server_request = self.f.request()
        server_request["argv"].append("-server")
        server_request["role"] = "server"
        with patch.object(self.life, "_rotate_storage_for_launch", return_value=None):
            result = self.life.start_run(lifecycle.IDENTITY_A, self.f.token_a, server_request)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self.gate.preparations, [])

    def test_mislabelled_client_cannot_bypass_steam(self):
        self.start(role="server")
        self.assertEqual(len(self.gate.preparations), 1)

    def test_live_client_refuses_mutation_without_replacement(self):
        old = lifecycle.process(77)
        run = self.f.add_run(old)
        self.f.guard.snapshots[77] = lifecycle.snapshot(old)
        self.life.diag_probe = lambda: {"known": True, "processes": [{"pid": 77}]}
        def action(**kwargs):
            self.assertFalse(kwargs["mutation_allowed"]())
            return guard.Preparation(error_code="steam_client_active")
        self.gate.action = action
        self.assertEqual(self.start(run_id=run.run_id, auto_remediate_steam=True)["error"], "steam_client_active")
        self.assertEqual(self.f.guard.terminate_calls, [])
        self.assertEqual(self.f.store.get(run.run_id).state, "RUNNING")
        self.assertEqual(self.f.store.get(run.run_id).processes, [old])

    def test_expired_or_aborted_exact_reservation_is_detected_while_preparing(self):
        def action(**kwargs):
            lease = self.f.coordinator._active
            pending = lease.pending_authorizations[0]
            self.f.coordinator.abort_reservation(
                lease.client.session_id, lease.lease_id, pending.reservation_id, "test_cancel")
            self.assertFalse(kwargs["authority_active"]())
            return guard.Preparation(error_code="steam_prepare_cancelled")
        self.gate.action = action
        self.start()
        self.assertEqual(self.f.launcher.calls, [])
        self.assertEqual(self.f.store.list_runs(), [])

    def test_concurrent_preparation_is_refused_without_second_writer(self):
        second = []
        def action(**kwargs):
            thread = threading.Thread(target=lambda: second.append(self.start()))
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            return guard.Preparation(identity=guard.SteamIdentity(41, ticks(1000)))
        self.gate.action = action
        self.assertTrue(self.start()["ok"])
        self.assertEqual(second[0]["error"], "steam_prepare_busy")
        self.assertEqual(len(self.gate.preparations), 1)

    def test_direct_consent_is_strict_bool(self):
        for value in (1, "true", None, [], {}):
            self.assertEqual(self.start(auto_remediate_steam=value)["error"], "invalid_start_request")
        self.assertEqual(self.gate.preparations, [])

    def test_lease_release_cancels_pending_preparation(self):
        def action(**kwargs):
            self.f.coordinator.release(lifecycle.IDENTITY_A, self.f.token_a)
            self.assertFalse(kwargs["authority_active"]())
            return guard.Preparation(error_code="steam_prepare_cancelled")
        self.gate.action = action
        self.start()
        self.assertEqual(self.f.launcher.calls, [])

    def test_abrupt_caller_loss_is_detected_at_lease_ttl(self):
        def action(**kwargs):
            expiry = self.f.coordinator._active.expires_at
            self.f.coordinator._time_fn = lambda: expiry + 0.001
            with patch.object(self.f.coordinator, "_cleanup") as cleanup:
                self.assertFalse(kwargs["authority_active"]())
                # Cancellation is observed before invoking slow release hooks.
                cleanup.assert_not_called()
            return guard.Preparation(error_code="steam_prepare_cancelled")
        self.gate.action = action
        self.start()
        self.assertEqual(self.f.launcher.calls, [])

    def test_ownership_change_during_wait_is_rejected_on_readmission(self):
        run = self.f.add_run(lifecycle.process(77))
        def action(**kwargs):
            changed = self.f.store.get(run.run_id)
            changed.owner_lease_id = "replacement-lease"
            self.f.store.replace(changed)
            return guard.Preparation(identity=guard.SteamIdentity(41, ticks(1000)))
        self.gate.action = action
        self.assertEqual(self.start(run_id=run.run_id)["error"], "run_not_adopted")
        self.assertEqual(self.f.launcher.calls, [])

    def test_old_idle_client_without_lease_still_prevents_steam_mutation(self):
        record = lifecycle.process(77)
        run = self.f.add_run(record)
        run.state = "RUNNING_IDLE"
        run.owner_session_id = run.owner_lease_id = None
        self.f.store.replace(run)
        self.life.diag_probe = lambda: {"known": True, "processes": [{"pid": 77}]}
        self.assertFalse(self.life._steam_mutation_allowed())

    def test_only_verified_actual_server_is_exempt_from_client_exclusion(self):
        record = lifecycle.process(77, role="server")
        self.f.add_run(record)
        self.f.guard.snapshots[77] = lifecycle.snapshot(record)
        self.life.diag_probe = lambda: {"known": True, "processes": [{"pid": 77}]}
        self.life.argv_of = lambda pid: ["DayZDiag_x64.exe", "-server"]
        self.assertTrue(self.life._steam_mutation_allowed())
        self.life.argv_of = lambda pid: ["DayZDiag_x64.exe"]
        self.assertFalse(self.life._steam_mutation_allowed())

    def test_hung_argv_probe_cannot_hold_lifecycle_lock_after_prepare_timeout(self):
        record = lifecycle.process(77, role="server")
        run = self.f.add_run(record)
        self.f.guard.snapshots[77] = lifecycle.snapshot(record)
        self.life.diag_probe = lambda: {"known": True, "processes": [{"pid": 77}]}
        entered, resume, done = threading.Event(), threading.Event(), threading.Event()
        def argv(pid):
            entered.set()
            resume.wait(2)
            done.set()
            return ["DayZDiag_x64.exe", "-server"]
        self.life.argv_of = argv
        helper = ScriptedHelper([{"mutation": True}])
        self.life.steam_gate = SteamPreparationGate(helper_factory=lambda: helper, budget_s=0.2)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.start(run_id=run.run_id, auto_remediate_steam=True)))
        try:
            thread.start()
            self.assertTrue(entered.wait(1))
            acquired = self.life._operation_lock.acquire(timeout=0.1)
            self.assertTrue(acquired)
            if acquired:
                self.life._operation_lock.release()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results[0]["error"], "steam_prepare_timeout")
            self.assertNotIn({"permit": True}, helper.sent)
        finally:
            resume.set()
            thread.join(3)
        self.assertTrue(done.wait(1))
        self.life.argv_of = lambda pid: None
        self.assertFalse(self.life._steam_mutation_allowed())


class ConsentTransportTests(unittest.TestCase):
    def test_sealed_lifecycle_transport_has_budget_for_prepare_and_cleanup(self):
        from tests.test_dayz_test_app import _load_app, RUN_ID
        from dayz_mcp import accredited_daemon_transport, normal_daemon_policy, pinned_keyfile
        for command, seconds in (("start", 235.0), ("status", 15.0)):
            app = _load_app()
            policy = SimpleNamespace(argv=("fixture",), cwd="C:/fixture", host="127.0.0.1",
                                     keyfile="C:/fixture/key", native_executable="C:/fixture/python.exe",
                                     port=9876, revalidate=lambda: None)
            core = b'{"auto_remediate_steam":true}' if command == "start" else b""
            frame = worker_tests.native_broker_protocol.encode_request(
                worker_tests.native_broker_protocol.BrokerKind.LIFECYCLE_CLI,
                {"command": command, "launch_operation_id": None, "run_id": RUN_ID}, stdin=core)
            with patch.dict(app.os.environ, {app._IDENTITY: '{"session_id":"fixture"}', app._TOKEN: "fixture"}), \
                 patch.object(app.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(frame))), \
                 patch.object(app.sys, "stdout", SimpleNamespace(buffer=io.BytesIO())), \
                 patch.object(app.time, "monotonic", return_value=100.0), \
                 patch.object(normal_daemon_policy, "load_inherited_normal_daemon_policy", return_value=policy), \
                 patch.object(pinned_keyfile, "read_pinned_keyfile", return_value="fixture"), \
                 patch.object(accredited_daemon_transport, "verified_daemon_http_request", return_value=(200, b'{"ok":true}')) as transport:
                self.assertEqual(app._lifecycle_main(), 0)
            self.assertEqual(transport.call_args.kwargs["deadline"], 100.0 + seconds)
            if command == "start":
                self.assertIs(json.loads(transport.call_args.kwargs["body"])["request"]["auto_remediate_steam"], True)

    def test_strict_bool_and_legacy_canonical_keysets(self):
        policy = worker_tests.POLICY
        raw = {"version": 1, "mod": policy.mod, "dev_root": policy.dev_root}
        for value in (1, "true", None, [], {}):
            with self.assertRaisesRegex(ValueError, "flag_not_boolean"):
                request.parse_dayz_test_request(json.dumps(raw | {"auto_remediate_steam": value}).encode(), policies=(policy,))
        current = request.parse_dayz_test_request(json.dumps(raw).encode(), policies=(policy,))
        self.assertIs(current.payload["auto_remediate_steam"], False)
        for missing in (("auto_remediate_steam",), ("auto_remediate_steam", "replace_if_not_polling_since")):
            legacy = {k: v for k, v in current.payload.items() if k not in missing}
            parsed = request.parse_dayz_test_request(json.dumps(legacy).encode(), policies=(policy,))
            self.assertIs(parsed.payload["auto_remediate_steam"], False)
            self.assertEqual(parsed.payload["source"], policy.default_source)

    def test_real_worker_carries_consent_after_server_readiness_only_to_client(self):
        fixture = worker_tests.DayzTestWorkerTests()
        for consent in (True, False):
            broker = worker_tests._Broker()
            result = fixture._run(worker_tests._raw(mode="all", auto_remediate_steam=consent), broker)
            self.assertEqual(result.exit_code, 0)
            starts = [json.loads(row.stdin) for row in broker.requests if row.payload.get("command") == "start"]
            self.assertEqual(len(starts), 2)
            self.assertNotIn("auto_remediate_steam", starts[0])
            self.assertIs(starts[1]["auto_remediate_steam"], consent)

    def test_explicit_steam_refusal_must_not_retry_a_new_offline_preparation(self):
        broker = worker_tests._RejectingStartBroker("steam_prepare_timeout")
        fixture = worker_tests.DayzTestWorkerTests()
        with self.assertRaises(worker_tests.dayz_test_worker.DayzTestWorkerError) as raised:
            fixture._run(worker_tests._raw(mode="offline", auto_remediate_steam=True), broker)
        self.assertEqual(raised.exception.code, "steam_prepare_timeout")
        self.assertEqual([row.payload.get("command") for row in broker.requests], ["start"])

    def test_lost_offline_transport_does_not_open_a_second_steam_budget(self):
        for exception in (False, True):
            broker = worker_tests._Broker()
            broker.lose_first_new_start_response = True
            if exception:
                original = broker.invoke
                async def invoke(frame):
                    row = worker_tests.native_broker_protocol.decode_request(frame)
                    if row.payload.get("command") == "start":
                        broker.requests.append(row)
                        raise OSError("lost response")
                    return await original(frame)
                broker.invoke = invoke
            with self.assertRaises(worker_tests.dayz_test_worker.DayzTestWorkerError):
                worker_tests.DayzTestWorkerTests()._run(worker_tests._raw(mode="offline"), broker)
            self.assertEqual([row.payload.get("command") for row in broker.requests], ["start", "stop"])

    def test_helper_cleanup_degradation_survives_the_worker_envelope(self):
        broker = worker_tests._RejectingStartBroker("steam_cleanup_degraded", role="client")
        with self.assertRaises(worker_tests.dayz_test_worker.DayzTestWorkerError) as raised:
            worker_tests.DayzTestWorkerTests()._run(worker_tests._raw(mode="all"), broker)
        self.assertEqual(raised.exception.code, "steam_cleanup_degraded")
        self.assertTrue(raised.exception.cleanup_degraded)


if __name__ == "__main__":
    unittest.main()
