from __future__ import annotations

"""Sitecustomize for the starting-cancel direct-route live-daemon test.

Chains the official dayz_mcp fixture (identity isolation) and, when
DAYZ_MCP_HOLD_LAUNCH_DIR is set, replaces ProcessLifecycle.launcher with a
blocking host launcher so STARTING stays open. The coordinator, lease and run
store stay the daemon's own.
"""

import os
import subprocess
import sys
import time
from pathlib import Path


def _official_sitecustomize() -> Path:
    return Path(__file__).resolve().parents[1] / "dayz_mcp" / "sitecustomize.py"


def _install_official() -> None:
    official = _official_sitecustomize()
    source = official.read_text(encoding="utf-8")
    ns: dict[str, object] = {"__name__": "sitecustomize_official", "__file__": str(official)}
    exec(compile(source, str(official), "exec"), ns, ns)


def _install_hold_launcher() -> None:
    raw = os.environ.get("DAYZ_MCP_HOLD_LAUNCH_DIR")
    if not raw:
        return
    hold = Path(raw)
    hold.mkdir(parents=True, exist_ok=True)
    entered = hold / "entered"
    release = hold / "release"
    (hold / "sitecustomize_ok").write_text("1", encoding="ascii")

    from dayz_mcp.process_lifecycle import ProcessLifecycle

    class _HoldLauncher:
        def __init__(self) -> None:
            self.proc: subprocess.Popen[str] | None = None
            self.pid = 0

        def __call__(self, argv: list[str], cwd: str, window_style: str):
            del argv, cwd, window_style
            self.proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.pid = int(self.proc.pid)
            entered.write_text(str(self.pid), encoding="ascii")
            deadline = time.monotonic() + 90.0
            while time.monotonic() < deadline:
                if release.exists():
                    break
                time.sleep(0.02)
            return self

        def terminate(self) -> None:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()

        def wait(self, timeout: float) -> int:
            if self.proc is None:
                return 0
            return int(self.proc.wait(timeout=timeout))

        def poll(self):
            if self.proc is None:
                return 0
            return self.proc.poll()

    original_init = ProcessLifecycle.__init__

    def wrapped_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.launcher = _HoldLauncher()
        empty_diag = lambda: {"known": True, "processes": []}
        self.diag_probe = empty_diag
        self.retail_probe = empty_diag
        self.port_probe = None

        class _TestBindings:
            minted = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            def prepare(self, run_id: str, role: str, profiles: str) -> str:
                del run_id, role, profiles
                return self.minted

            def confirm(self, minted: str, record: object) -> None:
                del minted, record

            def retire_role(self, run_id: str, role: str, reason: str) -> None:
                del run_id, role, reason

            def retire_run(self, run_id: str, reason: str) -> None:
                del run_id, reason

        self.bindings = _TestBindings()
        marker = hold / "lifecycle_init"
        marker.write_text("1", encoding="ascii")

    ProcessLifecycle.__init__ = wrapped_init  # type: ignore[method-assign]


_install_official()
_install_hold_launcher()
