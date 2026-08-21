from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, orphan_guard


class WatchdogDecisionTest(unittest.TestCase):
    def test_handle_present_arms(self) -> None:
        self.assertEqual(orphan_guard.decide_watchdog_action(object()), orphan_guard.ARMED)

    def test_missing_handle_disables(self) -> None:
        self.assertEqual(orphan_guard.decide_watchdog_action(None), orphan_guard.DISABLED)


class WatchdogInstallTest(unittest.TestCase):
    def test_missing_handle_disables_without_firing(self) -> None:
        fired = threading.Event()
        logs: list[str] = []
        action = orphan_guard.install_parent_death_watchdog(
            on_parent_death=fired.set,
            log=logs.append,
            open_handle=lambda ppid: None,  # parent already gone at startup
            wait_for_exit=lambda handle: None,
            image_name=lambda ppid: "claude.exe",
        )
        self.assertEqual(action, orphan_guard.DISABLED)
        self.assertFalse(fired.wait(timeout=0.5))
        self.assertTrue(any("watchdog disabled" in line for line in logs))

    def test_armed_handle_fires_on_parent_exit(self) -> None:
        fired = threading.Event()
        action = orphan_guard.install_parent_death_watchdog(
            on_parent_death=fired.set,
            log=lambda message: None,
            open_handle=lambda ppid: object(),       # live handle captured
            wait_for_exit=lambda handle: 0,           # parent "exits" immediately
            image_name=lambda ppid: "claude.exe",
        )
        self.assertEqual(action, orphan_guard.ARMED)
        self.assertTrue(fired.wait(timeout=2.0), "on_parent_death was not invoked")

    def test_wait_failed_does_not_fire_parent_death(self) -> None:
        fired = threading.Event()
        logs: list[str] = []
        action = orphan_guard.install_parent_death_watchdog(
            on_parent_death=fired.set,
            log=logs.append,
            open_handle=lambda ppid: object(),
            wait_for_exit=lambda handle: 0xFFFFFFFF,
            image_name=lambda ppid: "claude.exe",
        )
        self.assertEqual(action, orphan_guard.ARMED)
        self.assertFalse(fired.wait(timeout=0.5))
        self.assertTrue(any("WAIT_FAILED" in line for line in logs))


class WalkPastRedirectorsTest(unittest.TestCase):
    """Skip our venv launcher layer(s) and resolve the real
    ancestor. The immediate parent is the venv launcher (it outlives Claude Code); the
    watchdog/reclaim must look past it. Fully hermetic via injected facts."""

    VENV = "C:\\venv\\Scripts\\python.exe"

    def test_immediate_non_launcher_parent_is_returned(self) -> None:
        target = orphan_guard.walk_past_redirectors(
            300, self.VENV,
            get_parent=lambda pid: None,
            get_full_path=lambda pid: "C:\\Program Files\\nodejs\\node.exe",
        )
        self.assertEqual(target, 300)

    def test_single_launcher_layer_is_skipped(self) -> None:
        parents = {200: 300}
        paths = {200: self.VENV, 300: "C:\\node.exe"}
        target = orphan_guard.walk_past_redirectors(
            200, self.VENV, get_parent=parents.get, get_full_path=paths.get
        )
        self.assertEqual(target, 300)

    def test_multiple_launcher_layers_are_skipped(self) -> None:
        parents = {200: 250, 250: 300}
        paths = {200: self.VENV, 250: self.VENV, 300: "C:\\node.exe"}
        target = orphan_guard.walk_past_redirectors(
            200, self.VENV, get_parent=parents.get, get_full_path=paths.get
        )
        self.assertEqual(target, 300)

    def test_unknown_path_is_treated_as_non_launcher(self) -> None:
        target = orphan_guard.walk_past_redirectors(
            200, self.VENV, get_parent=lambda pid: 300, get_full_path=lambda pid: None
        )
        self.assertEqual(target, 200)

    def test_missing_first_ppid_returns_none(self) -> None:
        self.assertIsNone(
            orphan_guard.walk_past_redirectors(
                None, self.VENV, get_parent=lambda pid: 1, get_full_path=lambda pid: None
            )
        )

    def test_launcher_cycle_fails_closed(self) -> None:
        target = orphan_guard.walk_past_redirectors(
            200, self.VENV, get_parent=lambda pid: 200, get_full_path=lambda pid: self.VENV
        )
        self.assertIsNone(target)

    def test_install_watches_resolved_ancestor_not_immediate_parent(self) -> None:
        opened: list[int] = []
        ppid = os.getppid()
        parents = {ppid: 300, 300: 0}              # immediate parent is the launcher
        paths = {ppid: self.VENV, 300: "C:\\node.exe"}
        action = orphan_guard.install_parent_death_watchdog(
            on_parent_death=lambda: None,
            log=lambda message: None,
            open_handle=lambda pid: opened.append(pid) or object(),
            wait_for_exit=lambda handle: 0x102,    # WAIT_TIMEOUT: never fire during the test
            image_name=lambda pid: "claude.exe",
            get_parent=parents.get,
            get_full_path=paths.get,
            self_exe=self.VENV,
        )
        self.assertEqual(action, orphan_guard.ARMED)
        self.assertEqual(opened, [300], "watchdog must open a handle on the resolved ancestor, not the launcher")


# Child binds a real loopback, installs the real (ancestor-walk) watchdog, records the
# pid it ended up watching, then idles until that ancestor is killed.
_C1FIX_CHILD = r'''
import json, os, sys, threading, time
tools_dir = sys.argv[1]
sigfile = sys.argv[2]
stopfile = sigfile + ".stop"

def stop_on_request():
    while not os.path.exists(stopfile):
        time.sleep(0.05)
    os._exit(0)

threading.Thread(target=stop_on_request, daemon=True).start()
open(sigfile + ".child-pid", "w").write(json.dumps({"pid": os.getpid()}))
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

from dayz_mcp import loopback, orphan_guard
state = loopback.ServerState("k")
httpd = loopback.create_http_server(0, state, reclaim_orphans=False)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

def on_death():
    try:
        httpd.server_close()
    except Exception:
        pass
    os._exit(0)

action = orphan_guard.install_parent_death_watchdog(on_parent_death=on_death)
watched = orphan_guard.walk_past_redirectors(os.getppid(), sys.executable)
open(sigfile, "w").write(json.dumps({"port": port, "pid": os.getpid(), "action": action, "watched": watched}))
while True:
    time.sleep(0.2)
'''

# Grandparent runs under the BASE interpreter (no launcher layer above it) and spawns
# the venv-python child, which materialises the redirector layer the child must skip.
_C1FIX_GRANDPARENT = r'''
import json, os, subprocess, sys, threading, time
venv_python = sys.argv[1]
child_script = sys.argv[2]
tools_dir = sys.argv[3]
sigfile = sys.argv[4]
stopfile = sigfile + ".stop"
launcher = subprocess.Popen([venv_python, child_script, tools_dir, sigfile])

def stop_on_request():
    while not os.path.exists(stopfile):
        time.sleep(0.05)
    if launcher.poll() is None:
        launcher.terminate()
    try:
        launcher.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    os._exit(0)

threading.Thread(target=stop_on_request, daemon=True).start()
open(sigfile + ".launcher-pid", "w").write(json.dumps({"pid": launcher.pid}))
while True:
    time.sleep(0.2)
'''


# Regression fixture: B publishes only its pid, then deliberately never signals ready.
_C1FIX_TIMEOUT_CHILD = r'''
import json, os, sys, threading, time
sigfile = sys.argv[2]
stopfile = sigfile + ".stop"

def stop_on_request():
    while not os.path.exists(stopfile):
        time.sleep(0.05)
    os._exit(0)

threading.Thread(target=stop_on_request, daemon=True).start()
open(sigfile + ".child-pid", "w").write(json.dumps({"pid": os.getpid()}))
while True:
    time.sleep(0.2)
'''

# G publishes its own exact pid and Popen's exact launcher pid for observation/cleanup.
_C1FIX_TRACKING_GRANDPARENT = r'''
import json, os, subprocess, sys, threading, time
venv_python = sys.argv[1]
child_script = sys.argv[2]
tools_dir = sys.argv[3]
sigfile = sys.argv[4]
stopfile = sigfile + ".stop"
open(sigfile + ".grandparent-pid", "w").write(json.dumps({"pid": os.getpid()}))
launcher = subprocess.Popen([venv_python, child_script, tools_dir, sigfile])

def stop_on_request():
    while not os.path.exists(stopfile):
        time.sleep(0.05)
    if launcher.poll() is None:
        launcher.terminate()
    try:
        launcher.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        pass
    os._exit(0)

threading.Thread(target=stop_on_request, daemon=True).start()
open(sigfile + ".launcher-pid", "w").write(json.dumps({"pid": launcher.pid}))
while True:
    time.sleep(0.2)
'''


class WatchdogIntegrationTest(unittest.TestCase):
    def test_ancestor_walk_frees_port_when_true_grandparent_dies(self) -> None:
        # Real topology: base-python grandparent G -> venv launcher A -> child B.
        # B's watchdog must skip A and watch G; killing only G must free B's port.
        base_python = getattr(sys, "_base_executable", None) or orphan_guard.full_image_path_of(os.getpid())
        venv_python = sys.executable
        if not base_python or orphan_guard._same_path(base_python, venv_python):
            self.skipTest("no venv launcher layer in this interpreter; ancestor-walk is a no-op")

        child = Path(tempfile.gettempdir()) / f"c1fix_child_{os.getpid()}.py"
        grandparent_script = Path(tempfile.gettempdir()) / f"c1fix_gp_{os.getpid()}.py"
        sig = Path(tempfile.gettempdir()) / f"c1fix_{os.getpid()}.sig"
        launcher_pid_sig = Path(str(sig) + ".launcher-pid")
        child_pid_sig = Path(str(sig) + ".child-pid")
        stop_sig = Path(str(sig) + ".stop")
        child.write_text(_C1FIX_CHILD, encoding="utf-8")
        grandparent_script.write_text(_C1FIX_GRANDPARENT, encoding="utf-8")
        for path in (sig, launcher_pid_sig, child_pid_sig, stop_sig):
            try:
                path.unlink()
            except OSError:
                pass

        grandparent = subprocess.Popen(
            [base_python, str(grandparent_script), venv_python, str(child), str(_TOOLS_DIR), str(sig)]
        )
        launcher_pid = None
        child_pid = None
        try:
            launcher_info = self._await_json(launcher_pid_sig, timeout=10.0)
            self.assertIsNotNone(launcher_info, "launcher never published its pid")
            launcher_pid = launcher_info["pid"]
            child_info = self._await_json(child_pid_sig, timeout=10.0)
            self.assertIsNotNone(child_info, "child never published its pid")
            child_pid = child_info["pid"]

            info = self._await_json(sig, timeout=20.0)
            self.assertIsNotNone(info, "child never signalled ready")
            port = info["port"]
            self.assertEqual(info["pid"], child_pid, "ready signal changed the child pid")

            # The watchdog resolved past the venv launcher to the base-python grandparent.
            self.assertEqual(info["action"], orphan_guard.ARMED)
            self.assertEqual(
                info["watched"], grandparent.pid,
                "watchdog must watch the base-python grandparent, not the venv launcher",
            )

            # The child holds the port: an exclusive bind must fail.
            with self.assertRaises(OSError):
                loopback.ExclusiveThreadingHTTPServer(("127.0.0.1", port), loopback.Handler)

            # Kill ONLY the grandparent (no tree-kill); the ancestor-walk watchdog must
            # free the port and exit the child even though the venv launcher A survives.
            grandparent.terminate()
            self.assertTrue(
                self._await_port_free(port, timeout=8.0),
                "ancestor-walk watchdog did not free the port within 8s of grandparent death",
            )
            child_exit_deadline = time.monotonic() + 5.0
            while orphan_guard.is_pid_alive(child_pid) and time.monotonic() < child_exit_deadline:
                time.sleep(0.01)
            self.assertFalse(
                orphan_guard.is_pid_alive(child_pid),
                "child should exit once its watched ancestor dies",
            )
        finally:
            try:
                stop_sig.touch()
            except OSError:
                pass
            cleanup_deadline = time.monotonic() + 5.0
            tracked_pids = (child_pid, launcher_pid, grandparent.pid)
            while (
                any(orphan_guard.is_pid_alive(pid) for pid in tracked_pids if pid is not None)
                and time.monotonic() < cleanup_deadline
            ):
                time.sleep(0.05)
            if grandparent.poll() is None:
                grandparent.terminate()
            try:
                grandparent.wait(timeout=5.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for path in (child, grandparent_script, sig, launcher_pid_sig, child_pid_sig, stop_sig):
                try:
                    path.unlink()
                except OSError:
                    pass


    def test_cleanup_reaps_all_descendants_when_readiness_times_out(self) -> None:
        sig = Path(tempfile.gettempdir()) / f"c1fix_{os.getpid()}.sig"
        pid_files = {
            "grandparent": Path(str(sig) + ".grandparent-pid"),
            "launcher": Path(str(sig) + ".launcher-pid"),
            "child": Path(str(sig) + ".child-pid"),
        }
        stop_sig = Path(str(sig) + ".stop")
        for path in (*pid_files.values(), stop_sig):
            try:
                path.unlink()
            except OSError:
                pass

        captured: dict[str, int] = {}
        probe = WatchdogIntegrationTest(
            "test_ancestor_walk_frees_port_when_true_grandparent_dies"
        )
        original_await_json = probe._await_json

        def force_readiness_timeout(path: Path, timeout: float):
            if path == sig:
                for role, pid_file in pid_files.items():
                    info = original_await_json(pid_file, timeout=5.0)
                    self.assertIsNotNone(info, f"{role} pid was not published")
                    captured[role] = info["pid"]
                return None
            info = original_await_json(path, timeout)
            if info is not None:
                for role, pid_file in pid_files.items():
                    if path == pid_file:
                        captured[role] = info["pid"]
            return info

        original_child = globals()["_C1FIX_CHILD"]
        original_grandparent = globals()["_C1FIX_GRANDPARENT"]
        globals()["_C1FIX_CHILD"] = _C1FIX_TIMEOUT_CHILD
        globals()["_C1FIX_GRANDPARENT"] = _C1FIX_TRACKING_GRANDPARENT
        probe._await_json = force_readiness_timeout
        try:
            with self.assertRaisesRegex(AssertionError, "child never signalled ready"):
                probe.test_ancestor_walk_frees_port_when_true_grandparent_dies()

            deadline = time.monotonic() + 5.0
            survivors = list(captured.values())
            while survivors and time.monotonic() < deadline:
                survivors = [pid for pid in captured.values() if orphan_guard.is_pid_alive(pid)]
                if survivors:
                    time.sleep(0.05)
            self.assertEqual(survivors, [], f"harness descendants survived cleanup: {survivors}")
        finally:
            globals()["_C1FIX_CHILD"] = original_child
            globals()["_C1FIX_GRANDPARENT"] = original_grandparent
            try:
                stop_sig.touch()
            except OSError:
                pass
            emergency_deadline = time.monotonic() + 5.0
            while (
                any(orphan_guard.is_pid_alive(pid) for pid in captured.values())
                and time.monotonic() < emergency_deadline
            ):
                time.sleep(0.05)
            for path in (*pid_files.values(), stop_sig):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _await_json(self, sig: Path, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sig.exists():
                try:
                    return json.loads(sig.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, ValueError):
                    pass
            time.sleep(0.1)
        return None

    def _await_port_free(self, port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.1)
            finally:
                sock.close()
        return False


if __name__ == "__main__":
    unittest.main()
