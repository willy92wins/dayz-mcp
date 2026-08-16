from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from dayz_mcp import daemon
from dayz_mcp.identity_migration import RunsBackupGateError


def config() -> SimpleNamespace:
    return SimpleNamespace(
        key="fixture-key",
        keyfile=None,
        port=8765,
        idle_timeout_s=0.0,
        enable_exec_enforce=False,
        exec_allowlist=None,
        require_version=False,
        expected_game_version=None,
        log_sink=lambda _message: None,
    )


class DaemonIdentityMigrationGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime = TemporaryDirectory()
        self._environment = patch.dict(
            os.environ,
            {"LOCALAPPDATA": self._runtime.name},
        )
        self._environment.start()

    def tearDown(self) -> None:
        self._environment.stop()
        self._runtime.cleanup()

    def test_candidate_drain_retries_only_writer_presence(self) -> None:
        attempts = [
            RunsBackupGateError("dayz_mcp_process_present"),
            RunsBackupGateError("dayz_mcp_process_present"),
            None,
        ]
        with (
            patch.object(daemon, "_ensure_identity_migration", side_effect=attempts) as gate,
        ):
            daemon._ensure_identity_migration_after_candidate_drain(
                config(), deadline=11.0, time_fn=lambda: 10.0,
                sleep_fn=lambda _duration: None,
            )
        self.assertEqual(gate.call_count, 3)

        with (
            patch.object(
                daemon,
                "_ensure_identity_migration",
                side_effect=RunsBackupGateError("process_scan_incomplete"),
            ) as gate,
            self.assertRaisesRegex(RunsBackupGateError, "process_scan_incomplete"),
        ):
            daemon._ensure_identity_migration_after_candidate_drain(
                config(), deadline=11.0, time_fn=lambda: 10.0,
                sleep_fn=lambda _duration: None,
            )
        self.assertEqual(gate.call_count, 1)

    def test_migration_gate_rejects_nonapproved_python_before_snapshot(self) -> None:
        with (
            patch.object(daemon.sys, "executable", r"C:\Foreign\python.exe"),
            patch.object(daemon, "NativeProcessGuard") as guard,
            patch.object(daemon, "ensure_runs_v1_backup") as backup,
            self.assertRaisesRegex(RuntimeError, "daemon_python_not_approved"),
        ):
            daemon._ensure_identity_migration(config())

        guard.assert_not_called()
        backup.assert_not_called()

    def test_run_daemon_checks_migration_after_health_probe_and_before_bind(self) -> None:
        order: list[str] = []
        bind_keywords: dict[str, object] = {}
        state = SimpleNamespace(daemon_generation=None)
        daemon_config = config()
        native_image = r"C:\Python314\python.exe"

        def gate(_config: object) -> None:
            order.append("gate")

        def bind(*_args: object, **kwargs: object) -> None:
            order.append("bind")
            bind_keywords.update(kwargs)
            return None

        with (
            patch.object(daemon.orphan_guard, "probe_status_healthy", return_value=False),
            patch.object(
                daemon.orphan_guard,
                "full_image_path_of",
                return_value=native_image,
            ) as image_lookup,
            patch.object(daemon, "_ensure_identity_migration", side_effect=gate),
            patch.object(daemon, "build_server_state", return_value=state),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", side_effect=bind),
        ):
            result = daemon.run_daemon(daemon_config)

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)
        image_lookup.assert_called_once_with(daemon.os.getpid())
        self.assertEqual(order, ["gate", "bind"])
        self.assertEqual(bind_keywords["expected_executable"], native_image)
        self.assertEqual(
            bind_keywords["expected_argv"],
            daemon.build_daemon_argv(daemon_config, python=daemon.sys.executable),
        )

    def test_coordination_activation_deadline_bounds_real_io_phases(self) -> None:
        for expire_phase in ("recovery", "manifest", "coordination"):
            with self.subTest(expire_phase=expire_phase):
                clock = [100.0]
                crossed: list[str] = []

                def cross(phase: str) -> None:
                    crossed.append(phase)
                    if phase == expire_phase:
                        clock[0] = 101.0

                class Audit:
                    def write(self, _event: object) -> bool:
                        cross("audit")
                        return True

                class Snapshot:
                    def write_coordination(self, _payload: object) -> bool:
                        return True

                    def ensure_coordination(self, _payload: object) -> bool:
                        return True

                    def consume_previous_generation(
                        self, _generation: str, *, previous_snapshot: object
                    ) -> dict[str, object]:
                        cross("coordination")
                        return {}

                class Faults:
                    def arm(self, *_args: object, **_kwargs: object) -> str:
                        return "fault"

                    def transition(self, *_args: object, **_kwargs: object) -> str:
                        return "fault"

                    def clear(self, *_args: object, **_kwargs: object) -> bool:
                        return True

                class Manifest:
                    def quarantine_legacy_active(self, _audit: object) -> list[str]:
                        return []

                    def list_runs(self) -> list[object]:
                        return []

                    def recover_after_restart(self) -> dict[str, list[str]]:
                        return {}

                def recover(*_args: object, **_kwargs: object) -> object:
                    cross("recovery")
                    return SimpleNamespace(
                        audit_fault=None,
                        can_consume_snapshot=True,
                        snapshot={"revision": 0},
                    )

                def manifest_factory(
                    _paths: object, *, checkpoint: object = None
                ) -> Manifest:
                    cross("manifest")
                    return Manifest()

                state = SimpleNamespace(
                    cleanup_owner=lambda *_args, **_kwargs: {}
                )
                with (
                    patch.object(
                        daemon.RuntimePaths,
                        "from_env",
                        return_value=SimpleNamespace(
                            lifecycle_recovery_active_path=Path(__file__).with_name(
                                "__missing_lifecycle_recovery_active.json"
                            )
                        ),
                    ),
                    patch.object(daemon, "JsonlAuditWriter", return_value=Audit()),
                    patch.object(
                        daemon, "CoordinationSnapshotStore", return_value=Snapshot()
                    ),
                    patch.object(
                        daemon, "CoordinationFaultStore", return_value=Faults()
                    ),
                    patch.object(
                        daemon, "recover_coordination_startup", side_effect=recover
                    ),
                    patch.object(daemon, "SessionCoordinator", return_value=object()),
                    patch.object(
                        daemon, "RunManifestStore", side_effect=manifest_factory
                    ),
                    patch.object(daemon, "NativeProcessGuard", return_value=object()),
                    patch.object(daemon, "ProcessLifecycle", return_value=object()),
                    self.assertRaisesRegex(
                        TimeoutError, "daemon_startup_deadline_exceeded"
                    ),
                ):
                    daemon._activate_server_coordination(
                        state,
                        "fixture-generation",
                        deadline=100.5,
                        time_fn=lambda: clock[0],
                    )

                self.assertIn(expire_phase, crossed)
                self.assertIsNone(getattr(state, "coordination", None))

    def test_run_closes_candidate_when_activation_deadline_expires(self) -> None:
        closed: list[bool] = []
        httpd = SimpleNamespace(server_close=lambda: closed.append(True))
        state = SimpleNamespace(daemon_generation=None, lifecycle=None)

        with (
            patch.object(
                daemon.orphan_guard,
                "full_image_path_of",
                return_value=r"C:\Python314\python.exe",
            ),
            patch.object(
                daemon.orphan_guard, "probe_status_healthy", return_value=False
            ),
            patch.object(daemon, "_ensure_identity_migration", return_value=None),
            patch.object(daemon, "build_server_state", return_value=state),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", return_value=httpd),
            patch.object(
                daemon,
                "_activate_server_coordination",
                side_effect=TimeoutError("daemon_startup_deadline_exceeded"),
            ) as activate,
            patch.object(daemon, "_status_accredits_generation") as status_accredit,
        ):
            result = daemon.run_daemon(config())

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)
        self.assertEqual(closed, [True])
        self.assertIn("deadline", activate.call_args.kwargs)
        status_accredit.assert_not_called()

    def test_bind_loser_reports_success_only_after_exact_health_probe(self) -> None:
        state = SimpleNamespace(daemon_generation=None)
        probes = iter((False, False, True))
        with (
            patch.object(
                daemon.orphan_guard,
                "probe_status_healthy",
                side_effect=lambda *_args, **_kwargs: next(probes),
            ),
            patch.object(daemon, "_ensure_identity_migration", return_value=None),
            patch.object(daemon, "build_server_state", return_value=state),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", return_value=None),
        ):
            self.assertEqual(daemon.run_daemon(config()), 0)

    def test_contended_election_never_migrates_or_binds_and_is_not_false_success(self) -> None:
        @contextmanager
        def contended(_paths):
            yield False

        with (
            patch.object(daemon.orphan_guard, "probe_status_healthy", return_value=False),
            patch.object(daemon, "daemon_startup_election", side_effect=contended),
            patch.object(daemon, "_ensure_identity_migration") as migration,
            patch.object(daemon, "_bind_with_reclaim") as bind,
        ):
            result = daemon.run_daemon(config())

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)
        migration.assert_not_called()
        bind.assert_not_called()

    def test_contended_election_reports_success_only_if_winner_is_healthy(self) -> None:
        @contextmanager
        def contended(_paths):
            yield False

        probes = iter((False, True))
        with (
            patch.object(
                daemon.orphan_guard,
                "probe_status_healthy",
                side_effect=lambda *_args, **_kwargs: next(probes),
            ),
            patch.object(daemon, "daemon_startup_election", side_effect=contended),
        ):
            self.assertEqual(daemon.run_daemon(config()), 0)

    def test_wrong_generation_after_activation_is_contended_not_success(self) -> None:
        state = SimpleNamespace(daemon_generation=None, lifecycle=None)

        class Httpd:
            server_address = ("127.0.0.1", 8765)

            def serve_forever(self, **_kwargs: object) -> None:
                return None

            def shutdown(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        probes = iter((False, False))
        with (
            patch.object(
                daemon.orphan_guard,
                "probe_status_healthy",
                side_effect=lambda *_args, **_kwargs: next(probes),
            ),
            patch.object(daemon, "_ensure_identity_migration", return_value=None),
            patch.object(daemon, "build_server_state", return_value=state),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", return_value=Httpd()),
            patch.object(daemon, "_activate_server_coordination", return_value=None),
            patch.object(daemon, "_status_accredits_generation", return_value=False),
        ):
            result = daemon.run_daemon(config())

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)

    def test_foreign_listener_never_receives_key_and_candidate_is_contended(self) -> None:
        logs: list[str] = []
        daemon_config = config()
        daemon_config.key = "secret-never-send"
        daemon_config.log_sink = logs.append
        foreign_argv = [daemon.sys.executable, "-m", "http.server", "8765"]

        class Foreign200:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _limit: int = -1) -> bytes:
                return b'{}'

        with (
            patch.object(
                daemon.orphan_guard, "_native_listener_pid_for_port", return_value=4321
            ),
            patch.object(
                daemon.orphan_guard,
                "full_image_path_of",
                return_value=daemon.sys.executable,
            ),
            patch.object(
                daemon.orphan_guard, "command_argv_of", return_value=foreign_argv
            ),
            patch.object(
                daemon.orphan_guard.urllib.request,
                "urlopen",
                return_value=Foreign200(),
            ) as request,
            patch.object(daemon, "_ensure_identity_migration", return_value=None),
            patch.object(daemon, "build_server_state", return_value=SimpleNamespace()),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", return_value=None),
        ):
            result = daemon.run_daemon(daemon_config)

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)
        request.assert_not_called()
        self.assertNotIn("secret-never-send", "\n".join(logs))

    def test_one_startup_deadline_reaches_probe_drain_bind_reclaim_and_activation(self) -> None:
        observed: list[tuple[str, float]] = []
        state = SimpleNamespace(daemon_generation=None, lifecycle=None)

        class Httpd:
            server_address = ("127.0.0.1", 8765)

            def serve_forever(self, **_kwargs: object) -> None:
                return None

            def shutdown(self) -> None:
                return None

            def server_close(self) -> None:
                return None

        def probe(*_args: object, **kwargs: object) -> bool:
            observed.append(("probe", float(kwargs["deadline"])))
            return False

        def drain(_config: object, *, deadline: float) -> None:
            observed.append(("drain", deadline))

        def bind(*_args: object, **kwargs: object):
            observed.append(("bind", float(kwargs["deadline"])))
            return Httpd()

        def accredit(*_args: object, **kwargs: object) -> bool:
            observed.append(("activation", float(kwargs["deadline"])))
            return False

        with (
            patch.object(daemon.time, "monotonic", return_value=100.0),
            patch.object(
                daemon.orphan_guard, "probe_status_healthy", side_effect=probe
            ),
            patch.object(
                daemon,
                "_ensure_identity_migration_after_candidate_drain",
                side_effect=drain,
            ),
            patch.object(daemon, "build_server_state", return_value=state),
            patch.object(daemon, "make_status_provider", return_value=lambda: {}),
            patch.object(daemon, "_bind_with_reclaim", side_effect=bind),
            patch.object(daemon, "_activate_server_coordination", return_value=None),
            patch.object(
                daemon, "_status_accredits_generation", side_effect=accredit
            ),
        ):
            result = daemon.run_daemon(config())

        self.assertEqual(result, daemon.DAEMON_STARTUP_CONTENDED)
        self.assertEqual({deadline for _phase, deadline in observed}, {140.0})
        self.assertTrue(
            {"probe", "drain", "bind", "activation"}.issubset(
                {phase for phase, _deadline in observed}
            )
        )

    def test_bind_closes_candidate_if_deadline_expires_during_create(self) -> None:
        closed: list[bool] = []
        httpd = SimpleNamespace(server_close=lambda: closed.append(True))
        with (
            patch.object(daemon.time, "monotonic", side_effect=[100.0, 100.0, 101.0]),
            patch.object(daemon, "create_http_server", return_value=httpd),
            self.assertRaisesRegex(TimeoutError, "daemon_startup_deadline_exceeded"),
        ):
            daemon._bind_with_reclaim(
                8765,
                "fixture-key",
                SimpleNamespace(),
                lambda _message: None,
                lambda: {},
                deadline=100.5,
                expected_executable=r"C:\Python\python.exe",
                expected_argv=[
                    r"C:\Python\python.exe", "-m", "dayz_mcp", "--daemon",
                    "--port", "8765",
                ],
                expected_cwd=r"C:\DayZ_MCP\tools",
            )
        self.assertEqual(closed, [True])

    def test_explicit_coordination_activation_cannot_bypass_migration_gate(self) -> None:
        order: list[str] = []

        with (
            patch.object(
                daemon,
                "_ensure_identity_migration",
                side_effect=lambda _config: order.append("gate"),
            ),
            patch.object(
                daemon,
                "_activate_server_coordination",
                side_effect=lambda _state, _generation, **_kwargs: order.append("activate"),
            ),
        ):
            daemon.build_server_state(
                config(),
                "fixture-key",
                daemon_generation="fixture-generation",
                activate_coordination=True,
            )

        self.assertEqual(order, ["gate", "activate"])


if __name__ == "__main__":
    unittest.main()
