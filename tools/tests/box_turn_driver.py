"""Test-only launcher for ``box_turn``.

Production ``python -m dayz_mcp.box_turn`` never reads this module and never
reads probe settings from the environment. Tests pass a JSON spec as the
first argument. The spec may set ``scan`` (``inject`` or ``unknown``),
``pidfile`` (live pids that count as DayZ) and ``reservation`` (a path for
``reservation_at``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dayz_mcp import box_reservation, box_turn
from dayz_mcp.native_process_guard import NativeProcessGuard


def _alive(pid: int) -> bool:
    snap = NativeProcessGuard().snapshot(pid)
    if not isinstance(snap, dict):
        return True
    return snap.get("error") != "process_not_found"


def _pids(path: str) -> list[int]:
    if not path or not Path(path).is_file():
        return []
    found: list[int] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            pid = int(text)
        except ValueError:
            continue
        if pid > 0 and _alive(pid):
            found.append(pid)
    return found


def install(spec: dict) -> None:
    scan = spec.get("scan")
    pidfile = spec.get("pidfile") if isinstance(spec.get("pidfile"), str) else ""
    reservation = spec.get("reservation") if isinstance(spec.get("reservation"), str) else ""

    def names() -> list[dict]:
        if scan == "unknown":
            return [{"known": False, "processes": []}]
        processes = [
            {"pid": pid, "name": "DayZ_x64.exe"} for pid in _pids(pidfile)
        ]
        return [{"known": True, "processes": processes}]

    def udp() -> dict:
        if scan == "unknown":
            return {"known": False, "holders": []}
        return {"known": True, "holders": []}

    def reserved() -> dict | None:
        if reservation:
            return box_reservation.reservation_at(reservation)
        return box_reservation.current_reservation()

    box_turn.install_probe_hooks(names=names, udp=udp, reservation=reserved)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        return 2
    install(spec)
    return box_turn.main(args[1:])


if __name__ == "__main__":
    sys.exit(main())
