# -*- coding: utf-8 -*-
r"""External oracle for lote V-server: five ergonomics products on public surfaces of server.py.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Design notes:
- Self-contained: imports the modules under test by name. The single helper it borrows from the
  suite (tests.test_mcp_tools.bind_both_peers) is outside the implementer write-set.
- Expectations are literal. Field names (reason, not_ready_probes, last_error) and message
  texts are contracts the brief names; the method behind them belongs to the implementer.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


TOOLS = Path.cwd()
STALE = "game_not_ready:reason=server_poll_stale"
SIX = {"ready", "no_run", "server_poll_stale", "client_not_polling", "client_legacy_blocked", "version_mismatch"}


def _new_app(**cfg):
    from dayz_mcp.server import ServerConfig, build_app
    return build_app(ServerConfig(key="k", port=0, log_sink=lambda _m: None, **cfg))


def _desc(app, name: str) -> str:
    return app._tool_manager.get_tool(name).description or ""


def _text_json(result):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


async def _catch(awaitable):
    """Return the result, or {"raised": message} when the tool raised: a check then fails with detail."""
    from dayz_mcp.server import ToolError
    try:
        return await awaitable
    except ToolError as exc:
        return {"raised": str(exc)}


class _Lock:
    """asyncio.Lock wrapper that counts acquisitions (to prove a rejection happens before any lock)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.acquisitions = 0

    async def __aenter__(self):
        await self._lock.__aenter__()
        self.acquisitions += 1

    async def __aexit__(self, *args):
        return await self._lock.__aexit__(*args)


class _Probe:
    """Fake runtime: a script of probe outcomes. str -> raise ToolError(str); int -> that many players."""

    def __init__(self, script, fallback=0) -> None:
        self.tool_lock = _Lock()
        self.script = list(script)
        self.fallback = fallback
        self.calls = 0
        self.lifecycle_status = None

    async def call_bridge(self, cmd, args, peer, timeout_s):
        from dayz_mcp.server import ToolError
        self.calls += 1
        if cmd != "query_all_players":
            raise ToolError(f"unexpected:{cmd}")
        item = self.script.pop(0) if self.script else self.fallback
        if isinstance(item, str):
            raise ToolError(item)
        return {"ok": 1, "players": [{} for _ in range(item)]}


def main() -> int:
    # ------------------------------------------------ V1 bridge_status: the reason space is OPEN and derived
    try:
        from dayz_mcp import server
        emitted = SIX | {"legacy_unbound"} | set(server._FENCE_BLOCK_READY.values())
        published = set(server.READY_REASONS)
        check("V1-authority-covers-everything-emitted", emitted <= published,
              f"emitted but not in READY_REASONS: {sorted(emitted - published)}")
        app, _ = _new_app()
        d = _desc(app, "bridge_status")
        missing = sorted(r for r in published | emitted if r not in d)
        check("V1-desc-lists-every-reason", not missing, f"missing in description: {missing}")
        low = d.lower()
        check("V1-desc-declares-open-set", "open set" in low and "whitelist" in low and "shape" in low,
              f"desc={d[:240]!r}")
        with patch.dict(server._FENCE_BLOCK_READY, {"ZZZ_PROBE": "zzz_fence_probe"}):
            d_fence = _desc(_new_app()[0], "bridge_status")
        d_after = _desc(_new_app()[0], "bridge_status")
        check("V1-desc-derived-from-fence-map", "zzz_fence_probe" in d_fence and "zzz_fence_probe" not in d_after,
              "a value added to _FENCE_BLOCK_READY alone must surface in a NEW app description, and only there")
        with patch.object(server, "READY_REASONS", frozenset(server.READY_REASONS) | {"zzz_ready_probe"}):
            d_ready = _desc(_new_app()[0], "bridge_status")
        check("V1-desc-derived-from-ready-reasons", "zzz_ready_probe" in d_ready,
              "a value added to READY_REASONS alone must surface in a NEW app description")
        bad = []
        for key, val in server._FENCE_BLOCK_READY.items():
            r = server.compute_bridge_ready({"server_peer": {"binding_state": key}, "client_peer": {}})
            if r.get("reason") != val or val not in server.READY_REASONS:
                bad.append((key, r.get("reason")))
        check("V1-fence-reasons-flow-and-are-published", not bad, f"bad={bad}")
    except Exception as exc:  # noqa: BLE001
        unmet("V1", repr(exc))

    # ------------------------------------------------ V2 entities_query: no_player_connected is said, not implied
    try:
        from dayz_mcp.server import _annotate_entities_reliability
        out = _annotate_entities_reliability({"ok": 1, "entities": []}, {"ok": 1, "players": []}, [0.0, 0.0, 0.0])
        check("V2-no-player-connected-named",
              out.get("reason") == "no_player_connected" and out.get("nearest_player_m") is None
              and out.get("reliability") == "remote_unverified", f"out={out}")
        out2 = _annotate_entities_reliability({"ok": 1}, {"ok": 1, "players": [{"uid": "1", "pos": [1000.0, 0.0, 0.0]}]},
                                              [0.0, 0.0, 0.0])
        check("V2-far-player-has-no-reason", "reason" not in out2 and out2.get("reliability") == "remote_unverified",
              f"out={out2}")
        out3 = _annotate_entities_reliability({"ok": 1}, {"ok": 0, "error": "x"}, [0.0, 0.0, 0.0])
        check("V2-failed-probe-does-not-claim-no-player", out3.get("reason") != "no_player_connected", f"out={out3}")
        out4 = _annotate_entities_reliability({"ok": 0, "error": "bad_args"}, {"ok": 1, "players": []}, [0.0, 0.0, 0.0])
        check("V2-failed-query-untouched", "reason" not in out4 and "reliability" not in out4, f"out={out4}")
        # Round 2 (Codex B-01): an ok probe WITHOUT a players list is not evidence of absence.
        bad5 = []
        for probe in ({"ok": 1}, {"ok": 1, "players": None}, {"ok": 1, "players": "x"}):
            out5 = _annotate_entities_reliability({"ok": 1}, probe, [0.0, 0.0, 0.0])
            if "reason" in out5 or out5.get("reliability") != "remote_unverified" or out5.get("nearest_player_m") is not None:
                bad5.append((probe, out5))
        check("V2-ok-probe-without-list-claims-nothing", not bad5, f"bad={bad5}")
        app, runtime = _new_app()
        check("V2-desc-names-reason", "no_player_connected" in _desc(app, "entities_query"), "")

        async def fake_call(cmd, args, peer, timeout_s):
            if cmd == "entities_query":
                return {"ok": 1, "count_total": 0, "entities": []}
            if cmd == "query_all_players":
                return {"ok": 1, "players": []}
            raise AssertionError(cmd)

        with patch.object(runtime, "call_bridge", fake_call):
            res = _text_json(asyncio.run(app.call_tool("entities_query", {"pos": [1.0, 2.0, 3.0], "radius": 10.0})))
        check("V2-wire", res.get("reason") == "no_player_connected" and res.get("reliability") == "remote_unverified",
              f"res={res}")
    except Exception as exc:  # noqa: BLE001
        unmet("V2", repr(exc))

    # ------------------------------------------------ V3 wait_for: the 600 s ceiling is rejected up front, never clamped
    try:
        from dayz_mcp.server import ToolError, execute_wait_for
        probe = _Probe([1])

        async def run601():
            try:
                await execute_wait_for(probe, "players_at_least", value=1, timeout_s=601.0, poll_interval_s=0.5)
                return None
            except ToolError as exc:
                return str(exc)

        msg = asyncio.run(run601())
        check("V3-rejects-above-600-before-any-probe",
              msg is not None and "bad_args: timeout_s must be <= 600" in msg and probe.calls == 0
              and probe.tool_lock.acquisitions == 0,
              f"msg={msg!r} calls={probe.calls} lock_acquisitions={probe.tool_lock.acquisitions}")
        probe2 = _Probe([1])
        res = asyncio.run(execute_wait_for(probe2, "players_at_least", value=1, timeout_s=600.0, poll_interval_s=0.5))
        check("V3-accepts-exactly-600", res.get("satisfied") is True and probe2.calls == 1, f"res={res}")
        app, runtime = _new_app()
        d = _desc(app, "wait_for")
        check("V3-desc-names-ceiling", "timeout_s" in d and "600" in d and "bad_args" in d, f"desc tail={d[-220:]!r}")
        readme = (TOOLS / "README-mcp.md").read_text(encoding="utf-8")
        check("V3-readme-truth", "capped at 600" not in readme and "bad_args: timeout_s must be <= 600" in readme,
              "README-mcp.md must stop saying the value is capped and name the rejection")
        calls: list[str] = []

        async def spy(cmd, args, peer, timeout_s):
            # One player: today the silent clamp would otherwise poll 600 s with zero players.
            calls.append(cmd)
            return {"ok": 1, "players": [{}]}

        async def wire():
            try:
                await app.call_tool("wait_for", {"condition": "players_at_least", "value": 1, "timeout_s": 601,
                                                 "poll_interval_s": 0.5})
                return None
            except Exception as exc:  # noqa: BLE001
                return str(exc)

        with patch.object(runtime, "call_bridge", spy):
            wmsg = asyncio.run(wire())
        check("V3-wire", wmsg is not None and "timeout_s must be <= 600" in wmsg and calls == [],
              f"msg={wmsg!r} bridge_calls={calls}")
    except Exception as exc:  # noqa: BLE001
        unmet("V3", repr(exc))

    # ------------------------------------------------ V4 wait_for: waits THROUGH server startup, exact reason only
    try:
        from dayz_mcp.server import ToolError, execute_wait_for
        probe = _Probe([STALE, STALE, 1])
        res = asyncio.run(_catch(execute_wait_for(probe, "players_at_least", value=1, timeout_s=10.0,
                                                  poll_interval_s=0.2)))
        check("V4-waits-through-server-startup",
              res.get("satisfied") is True and res.get("probes") == 3 and res.get("not_ready_probes") == 2
              and probe.calls == 3, f"res={res} calls={probe.calls}")
        probe = _Probe([], fallback=STALE)
        t0 = time.monotonic()
        res = asyncio.run(_catch(execute_wait_for(probe, "players_at_least", value=1, timeout_s=1.2,
                                                  poll_interval_s=0.3)))
        elapsed = time.monotonic() - t0
        check("V4-single-deadline-and-last-error",
              res.get("ok") is True and res.get("satisfied") is False and res.get("timed_out") is True
              and res.get("last_error") == STALE and (res.get("not_ready_probes") or 0) >= 2 and elapsed < 3.0,
              f"res={res} elapsed={elapsed:.2f}")
        for other in ("game_not_ready:reason=client_not_polling", "game_not_ready:reason=no_run", "version_blocked",
                      "game_not_ready:reason=server_poll_stale; server peer last polled 40.0s ago"):
            probe = _Probe([other, 1])

            async def go(p=probe):
                try:
                    await execute_wait_for(p, "players_at_least", value=1, timeout_s=5.0, poll_interval_s=0.2)
                    return None
                except ToolError as exc:
                    return str(exc)

            m = asyncio.run(go())
            tag = other.split("=")[-1][:24]
            check(f"V4-aborts-on[{tag}]", m is not None and other in m and probe.calls == 1,
                  f"msg={m!r} calls={probe.calls}")

        async def lock_probe():
            p = _Probe([], fallback=STALE)
            task = asyncio.create_task(execute_wait_for(p, "players_at_least", value=1, timeout_s=2.0,
                                                        poll_interval_s=0.5))
            await asyncio.sleep(0.25)
            got = False
            try:
                await asyncio.wait_for(p.tool_lock._lock.acquire(), timeout=1.0)
                got = True
                p.tool_lock._lock.release()
            except asyncio.TimeoutError:
                pass
            still_running = not task.done()
            r = await _catch(task)
            return got, still_running, r

        got, running, res = asyncio.run(lock_probe())
        check("V4-sleeps-outside-the-lock", got and running and res.get("satisfied") is False,
              f"acquired={got} still_running={running} res={res}")
        # Round 2 (Codex B-02): a poll interval longer than the budget must not extend the call.
        probe = _Probe([], fallback=STALE)
        t0 = time.monotonic()
        res = asyncio.run(_catch(execute_wait_for(probe, "players_at_least", value=1, timeout_s=0.1,
                                                  poll_interval_s=0.5)))
        wall = time.monotonic() - t0
        check("V4-deadline-bounds-the-sleep",
              res.get("timed_out") is True and probe.calls == 1 and wall < 0.3,
              f"wall={wall:.3f}s for timeout_s=0.1 calls={probe.calls} res={res}")
        app, _ = _new_app()
        check("V4-desc-names-startup-wait", "server_poll_stale" in _desc(app, "wait_for"), "")
        # End to end on the real in-process runtime: never-polled server + require_version is the exact path of the ficha.
        from tests.test_mcp_tools import bind_both_peers
        app, runtime = _new_app(require_version=True)
        runtime.start_loopback()
        try:
            bind_both_peers(runtime.state)

            async def e2e():
                try:
                    return _text_json(await app.call_tool("wait_for", {"condition": "players_at_least", "value": 1,
                                                                       "timeout_s": 1.0, "poll_interval_s": 0.3}))
                except Exception as exc:  # noqa: BLE001
                    return {"raised": str(exc)}

            r = asyncio.run(e2e())
        finally:
            runtime.stop_loopback()
        check("V4-e2e-never-polled-server",
              "raised" not in r and r.get("timed_out") is True and r.get("last_error") == STALE, f"r={r}")
    except Exception as exc:  # noqa: BLE001
        unmet("V4", repr(exc))

    # ------------------------------------------------ V5 action_use: `action` is an Enforce class name
    try:
        app, _ = _new_app()
        d = _desc(app, "action_use")
        low = d.lower()
        check("V5-desc-action-is-a-class-name",
              "class name" in low and "gettype()" in low and "not the visible" in low, f"desc={d!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("V5", repr(exc))

    # ------------------------------------------------ V6 (round 3, ficha 17 7c88): the mode/run_id matrix is published
    try:
        app, _ = _new_app()
        d = _desc(app, "dayz_test_run")
        check("V6-tool-desc-names-the-reattach-contract",
              "mode=client requires run_id" in d and "preserving the server" in d and "must NOT pass run_id" in d,
              f"desc={d[:300]!r}")
        props = app._tool_manager.get_tool("dayz_test_run").parameters.get("properties", {})
        mode_d = str(props.get("mode", {}).get("description", ""))
        run_d = str(props.get("run_id", {}).get("description", ""))
        check("V6-property-descriptions-carry-the-matrix",
              "run_id" in mode_d and "client" in mode_d and "client" in run_d and "server" in run_d
              and mode_d.strip().lower() not in {"", "mode"} and run_d.strip().lower() not in {"", "run id"},
              f"mode={mode_d!r} run_id={run_d!r}")
        # The enum published by the authority must survive the description patch.
        check("V6-enum-still-published", isinstance(props.get("mode", {}).get("enum"), list)
              and "server" in props["mode"]["enum"], f"mode prop={props.get('mode')!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("V6", repr(exc))

    counts = {"PASS": 0, "FAIL": 0, "UNMET": 0}
    for name, status, detail in RESULTS:
        counts[status] += 1
        print(f"{status:5} {name}" + (f"  -- {detail}" if detail and status != "PASS" else ""))
    print(f"PASS={counts['PASS']}  FAIL={counts['FAIL']}  UNMET={counts['UNMET']}  de {len(RESULTS)}")
    if counts["FAIL"] == 0 and counts["UNMET"] == 0:
        print("ORACULO-VERDE")
        return 0
    print("ORACULO-ROJO")
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
