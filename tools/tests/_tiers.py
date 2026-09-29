"""The slow tier of the suite, and the switch that leaves it out.

The whole suite runs by default. With DAYZ_MCP_FAST_TESTS=1 every test marked
@slow_test skips with its reason named, which leaves a fast tier for the
edit-run loop: about 80 s of the 420 s the whole suite took on 2026-09-28. Any
other value, or none, runs everything, so a typo costs time and never coverage.

A slow test is as required as any other; the split only decides when it runs.
CI runs the fast tier and the whole suite in separate jobs on every pull
request, and the whole suite on main.

Membership came from that run's per-test durations (unittest --durations 0):
every test that took 0.2 s or more, most of them because they start real
processes or wait on real time, plus a few that do either and have failed
under load in CI however long they took. A new test that starts real processes
or waits on real time belongs here too.

The fast tier also refuses the real pre-run desktop gate. Calling
mcp_capture.run_prerun_desktop_gate with a real desktop probe
(probe_input_desktop or probe_desktop_brightness) raises at once and names
the test. A function that was a real probe before importlib.reload is still
real: reload rebinds the names, and a saved reference passed explicitly is
refused too. Calls that inject both probes — the gate's own tests — are
unchanged. The whole suite does not install that refusal, and the gate's
production behaviour is untouched.
"""

from __future__ import annotations

import functools
import os
import sys
import unittest

FAST_TIER_ONLY = os.environ.get("DAYZ_MCP_FAST_TESTS") == "1"

slow_test = unittest.skipIf(
    FAST_TIER_ONLY,
    "DAYZ_MCP_FAST_TESTS=1 leaves out the slow tier; the whole suite runs it",
)

_GUARD_FLAG = "_fast_tier_desktop_guard"

# Every function object that has been probe_input_desktop or
# probe_desktop_brightness. importlib.reload rebinds those names; a test
# can still pass the previous function explicitly.
_REAL_DESKTOP_PROBES: set[object] = set()


def _remember_loaded_real_probes() -> None:
    import mcp_capture

    _REAL_DESKTOP_PROBES.add(mcp_capture.probe_input_desktop)
    _REAL_DESKTOP_PROBES.add(mcp_capture.probe_desktop_brightness)


def fast_tier_real_desktop_gate_message(test_id: str) -> str:
    return (
        "fast tier called the real pre-run desktop gate without faking it: "
        + test_id
    )


def real_desktop_probes_used(
    *, probe_desktop: object, probe_brightness: object
) -> bool:
    """True when this call can touch the host desktop or sleep on it.

    Compared against every real probe seen so far, including the functions
    bound before the latest importlib.reload(mcp_capture).
    """
    _remember_loaded_real_probes()
    return (
        probe_desktop in _REAL_DESKTOP_PROBES
        or probe_brightness in _REAL_DESKTOP_PROBES
    )


def calling_test_id() -> str:
    """The unittest id on some live stack, or a label when none is visible."""
    found: list[str] = []
    for frame in sys._current_frames().values():
        seen: set[int] = set()
        while frame is not None and id(frame) not in seen:
            seen.add(id(frame))
            owner = frame.f_locals.get("self")
            if isinstance(owner, unittest.TestCase):
                found.append(owner.id())
                break
            frame = frame.f_back
    unique: list[str] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    if unique:
        return ", ".join(unique)
    return "unknown test"


def reject_real_desktop_gate_in_fast_tier(
    *,
    fast_tier: bool,
    probe_desktop: object,
    probe_brightness: object,
    test_id: str,
) -> None:
    """Fail closed in the fast tier when a call still uses a real probe.

    The whole suite passes fast_tier=False and returns without looking at the
    probes, so this helper does not change a normal run.
    """
    if not fast_tier:
        return
    if real_desktop_probes_used(
        probe_desktop=probe_desktop, probe_brightness=probe_brightness
    ):
        raise AssertionError(fast_tier_real_desktop_gate_message(test_id))


def _install_on_loaded_capture_module() -> None:
    import mcp_capture

    # Record this generation before wrapping, including when the wrapper is
    # already in place. A later reload adds the new functions and keeps these.
    _remember_loaded_real_probes()
    original = mcp_capture.run_prerun_desktop_gate
    if getattr(original, _GUARD_FLAG, False):
        return

    @functools.wraps(original)
    def guarded(*args: object, **kwargs: object) -> object:
        probe_desktop = kwargs.get("probe_desktop", mcp_capture.probe_input_desktop)
        probe_brightness = kwargs.get(
            "probe_brightness", mcp_capture.probe_desktop_brightness
        )
        reject_real_desktop_gate_in_fast_tier(
            fast_tier=True,
            probe_desktop=probe_desktop,
            probe_brightness=probe_brightness,
            test_id=calling_test_id(),
        )
        return original(*args, **kwargs)

    setattr(guarded, _GUARD_FLAG, True)
    mcp_capture.run_prerun_desktop_gate = guarded


def _reinstall_after_capture_reload() -> None:
    """importlib.reload(mcp_capture) would drop the wrapper.

    tests/test_capture_frame_stale.py reloads the module to simulate a new
    process. Re-apply the wrapper after that reload, and only that reload.
    """
    import importlib

    if getattr(importlib.reload, _GUARD_FLAG, False):
        return
    original_reload = importlib.reload

    def reload_and_reinstall(module: object) -> object:
        reloaded = original_reload(module)
        if getattr(reloaded, "__name__", None) == "mcp_capture":
            _install_on_loaded_capture_module()
        return reloaded

    setattr(reload_and_reinstall, _GUARD_FLAG, True)
    importlib.reload = reload_and_reinstall  # type: ignore[assignment]


def install_fast_tier_desktop_gate_guard() -> None:
    """Wrap the real gate while DAYZ_MCP_FAST_TESTS=1. No-op otherwise."""
    if not FAST_TIER_ONLY:
        return
    _install_on_loaded_capture_module()
    _reinstall_after_capture_reload()
