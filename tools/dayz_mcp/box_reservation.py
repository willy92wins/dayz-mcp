"""The DayZ Studio (M1) reservation of the DayZ test box.

While ``C:\\tmp\\caja-dayz\\m1.espera`` exists the box is reserved: M1 goes
first. M1's test runs chain several DayZ servers that its own harness launches
(DayZ this daemon does not manage), with gaps of a few seconds between them in
which no DayZ holds a game port, so the box would read free. On 2026-11-17 two
queued dayz_test_run calls launched in those gaps.

The daemon reads the box as occupied while the file exists
(``session_status.box.reservation``), refuses a launch at admission and again
right before the spawn, and a queued launch keeps waiting.

Fail-closed: the file never expires on its own, and a path that exists but
cannot be examined counts as a reservation. A forgotten file is removed by
hand; refusals name it and its age. Windows hosts only: elsewhere there is no
reservation to read.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

BOX_RESERVATION_PATH = "C:\\tmp\\caja-dayz\\m1.espera"
BOX_RESERVED = "box_reserved"


def reservation_at(path: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The reservation the file at ``path`` makes, or None when it does not exist.

    ``age_s`` is the time since the file was last written, never negative,
    and None when the path exists but cannot be examined.
    """
    try:
        stat = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError):
        return {"path": path, "age_s": None}
    clock = time.time() if now is None else float(now)
    age = clock - stat.st_mtime
    if not math.isfinite(age):
        return {"path": path, "age_s": None}
    return {"path": path, "age_s": round(max(0.0, age), 3)}


def read_box_reservation() -> dict[str, Any] | None:
    """The host's reservation. The test package replaces this reader."""
    if os.name != "nt":
        return None
    return reservation_at(BOX_RESERVATION_PATH)


def current_reservation() -> dict[str, Any] | None:
    """read_box_reservation, fail-closed: a reader that fails is a reservation."""
    try:
        return read_box_reservation()
    except Exception:
        return {"path": BOX_RESERVATION_PATH, "age_s": None}


def public_reservation(value: object) -> dict[str, Any] | None:
    """A box payload's reservation as {path, age_s}, or None when it has none."""
    if not isinstance(value, dict):
        return None
    path = value.get("path")
    if not isinstance(path, str) or not path:
        path = BOX_RESERVATION_PATH
    age = value.get("age_s")
    if (
        isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not math.isfinite(float(age))
        or age < 0
    ):
        age = None
    return {"path": path, "age_s": float(age) if age is not None else None}


def reservation_hint(reservation: dict[str, Any]) -> str:
    """The refusal text: the file, its age, and who may remove it."""
    path = reservation.get("path") or BOX_RESERVATION_PATH
    age = reservation.get("age_s")
    if isinstance(age, (int, float)) and not isinstance(age, bool):
        written = f"last written {float(age):.0f} s ago"
    else:
        written = "its age could not be read"
    return (
        f"the DayZ test box is reserved for DayZ Studio (M1) while {path} "
        f"exists ({written}): dayz_test_run does not launch until it is "
        'removed, and on_busy="queue" waits for that. The file never expires; '
        "if no M1 run is waiting for or using the box, a person removes it by "
        "hand, not an agent"
    )
