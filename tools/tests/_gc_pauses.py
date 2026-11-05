"""Keep full garbage collections short in the test process.

Measured on the CI runners and locally (see CHANGELOG.md, the entry that
added this module): every stall of 0.1 s or more seen by a thread inside
the test process fell inside a full (generation 2) collection. A full
collection walks every tracked object, the process keeps about 1.5 million
of them by the end of a shard, and the pause grew from 0.03 s after
discovery to 0.85 s. The collector holds the GIL for that time, so the
HTTP server, event loop or worker thread of whatever test is running stops
with it, and a 0.2-0.5 s wall-clock gate fails.

After each full collection the survivors move to the permanent generation
(gc.freeze), so the next full collection walks only what was allocated
since. Reference counting still frees everything as before; what changes is
that a reference cycle made of objects that were alive at a full collection
is no longer reclaimed by a later one. A test that needs such a cycle
collected can call gc.unfreeze() first.
"""

from __future__ import annotations

import gc

_INSTALLED = False


def _freeze_survivors(phase: str, info: dict) -> None:
    if phase == "stop" and info.get("generation") == 2:
        gc.freeze()


def install() -> None:
    """Idempotent: the package and a child's sitecustomize may both call it."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    gc.callbacks.append(_freeze_survivors)
