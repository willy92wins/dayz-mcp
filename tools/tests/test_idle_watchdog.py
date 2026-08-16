from __future__ import annotations

import socket
import sys
import threading
import time
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, orphan_guard, server


class ComputeIdleSecondsTest(unittest.TestCase):
    def test_only_start_baseline(self) -> None:
        self.assertEqual(orphan_guard.compute_idle_seconds(100.0, 10.0, None, None, None), 90.0)

    def test_picks_latest_activity(self) -> None:
        # server poll (80) is the most recent sign of life.
        idle = orphan_guard.compute_idle_seconds(100.0, 10.0, 50.0, 80.0, None)
        self.assertEqual(idle, 20.0)

    def test_tool_call_can_be_latest(self) -> None:
        idle = orphan_guard.compute_idle_seconds(100.0, 10.0, 95.0, 80.0, 70.0)
        self.assertEqual(idle, 5.0)

    def test_floor_zero_when_clock_skews(self) -> None:
        # start in the "future" relative to now must never yield a negative idle.
        self.assertEqual(orphan_guard.compute_idle_seconds(100.0, 110.0, None, None, None), 0.0)


class InstallIdleWatchdogTest(unittest.TestCase):
    def test_nonpositive_timeout_disables(self) -> None:
        fired = threading.Event()
        logs: list[str] = []
        action = orphan_guard.install_idle_watchdog(
            idle_seconds=lambda: 9999.0,
            timeout_s=0.0,
            on_idle=fired.set,
            log=logs.append,
        )
        self.assertEqual(action, orphan_guard.DISABLED)
        self.assertFalse(fired.wait(timeout=0.3))
        self.assertTrue(any("disabled" in line for line in logs))

    def test_fires_when_idle_exceeds_timeout(self) -> None:
        fired = threading.Event()
        action = orphan_guard.install_idle_watchdog(
            idle_seconds=lambda: 999.0,     # already past the threshold
            timeout_s=1.0,
            on_idle=fired.set,
            log=lambda message: None,
            sleep=lambda seconds: None,     # do not actually wait between polls
        )
        self.assertEqual(action, orphan_guard.ARMED)
        self.assertTrue(fired.wait(timeout=2.0), "idle watchdog did not fire")

    def test_does_not_fire_below_timeout(self) -> None:
        fired = threading.Event()
        stop = threading.Event()
        action = orphan_guard.install_idle_watchdog(
            idle_seconds=lambda: 0.0,       # always fresh
            timeout_s=10.0,
            on_idle=fired.set,
            log=lambda message: None,
            poll_interval=0.01,
            stop=stop,
        )
        self.assertEqual(action, orphan_guard.ARMED)
        self.assertFalse(fired.wait(timeout=0.2))
        stop.set()


class _StubState:
    def __init__(self, server_poll: float | None, client_poll: float | None) -> None:
        self._server_poll = server_poll
        self._client_poll = client_poll

    def status_snapshot(self, now: float | None = None) -> dict:
        return {
            "peers": {
                "server": {"last_poll_at": self._server_poll},
                "client": {"last_poll_at": self._client_poll},
            },
            "results_pending": 0,
        }


class _StubLoopback:
    def __init__(self, state: _StubState) -> None:
        self.state = state


class RuntimeIdleSecondsTest(unittest.TestCase):
    def _runtime(self) -> server.Runtime:
        return server.Runtime(server.ServerConfig())

    def test_fresh_runtime_is_not_idle(self) -> None:
        runtime = self._runtime()
        self.assertLess(runtime.idle_seconds(), 1.0)

    def test_old_start_with_no_activity_is_idle(self) -> None:
        runtime = self._runtime()
        runtime.start_monotonic = time.monotonic() - 100.0
        self.assertGreater(runtime.idle_seconds(), 90.0)

    def test_touch_resets_idle(self) -> None:
        runtime = self._runtime()
        runtime.start_monotonic = time.monotonic() - 100.0
        runtime.touch()
        self.assertLess(runtime.idle_seconds(), 1.0)

    def test_game_poll_resets_idle(self) -> None:
        runtime = self._runtime()
        runtime.start_monotonic = time.monotonic() - 100.0
        runtime.last_tool_activity = time.monotonic() - 100.0
        runtime.loopback = _StubLoopback(_StubState(time.monotonic(), None))  # type: ignore[assignment]
        self.assertLess(runtime.idle_seconds(), 1.0)


class IdleWatchdogReleaseIntegrationTest(unittest.TestCase):
    """End-to-end on a REAL loopback (no FastMCP): the idle watchdog must actually
    free the bound port, not just invoke the callback. Closes the seam the unit
    tests cannot reach short of the in-vivo gate."""

    def test_idle_watchdog_frees_real_loopback_port(self) -> None:
        state = loopback.ServerState("k")
        httpd = loopback.create_http_server(0, state, reclaim_orphans=False)
        port = httpd.server_address[1]
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        try:
            # The loopback holds the port: an exclusive bind must fail right now.
            with self.assertRaises(OSError):
                loopback.ExclusiveThreadingHTTPServer(("127.0.0.1", port), loopback.Handler)

            released = threading.Event()

            def on_idle() -> None:
                httpd.shutdown()
                httpd.server_close()
                released.set()

            start = time.monotonic()
            action = orphan_guard.install_idle_watchdog(
                idle_seconds=lambda: time.monotonic() - start,  # idle grows from 0
                timeout_s=0.5,
                on_idle=on_idle,
                poll_interval=0.05,
            )
            self.assertEqual(action, orphan_guard.ARMED)
            self.assertTrue(released.wait(timeout=3.0), "idle watchdog never released the loopback")
            self.assertTrue(self._port_free(port, timeout=3.0), "port not free after idle release")
        finally:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass

    def _port_free(self, port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
            finally:
                sock.close()
        return False


if __name__ == "__main__":
    unittest.main()
