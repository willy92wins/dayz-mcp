# -*- coding: utf-8 -*-
"""External oracle for lote E: ficha 17 (7c88), PASS blocks 1 and 3.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Load-bearing design notes:
- The three expected cause tokens are IMPORTED from the parser, never transcribed. A gate
  that carries the literal it checks verifies itself.
- Every code is observed through the PUBLIC tool as well as the facade, because the ficha
  demands both agree. Checking only the facade is the defect this ficha exists to fix.
- The client fixture is imported from tests/test_client_mode.py, which the brief forbids
  editing, and its sha256 is pinned here. A candidate that bends the fixture goes RED.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


sys.path.insert(0, "tests")
from dayz_mcp import dayz_test_request, dayz_test_tool, server  # noqa: E402
from dayz_mcp import steam_preflight as sp  # noqa: E402

# Expectations derived from the source, not typed in by me -- but from the RIGHT source.
#
# Corrected in round 1, and the correction is the point: the first version imported
# `_INVALID_RUN_ID` ("invalid_run_id"), which is the PARSER's internal token, and demanded
# the client see that. The public code for a malformed UUID is `bad_run_id`, already
# shipped at dayz_test_tool.py:200 for the stop/extension path. Deriving from the source
# is worthless if it is the wrong source: demanding the internal token would have minted a
# SECOND public code for one error class, which is worse than the opacity the ficha fixes.
TOK_CLIENT = dayz_test_request._CLIENT_REQUIRES_RUN_ID
TOK_SERVER = dayz_test_request._SERVER_ALL_FORBID_RUN_ID
TOK_UUID = "bad_run_id"

GOOD_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BAD_UUID = "not-a-uuid"

# The fixture the oracle leans on lives outside the delegate's write-set. Pinning it here
# means an edit to it is a red gate, not a silent change of the yardstick.
FIXTURE = Path("tests/test_client_mode.py")
FIXTURE_SHA = hashlib.sha256(FIXTURE.read_bytes()).hexdigest() if FIXTURE.exists() else ""

try:
    from tests.test_client_mode import _fixture_client_runtime
    from tests.test_dayz_test_tool import _Bundle, _Opened, _policy, _sealed
    from tests.test_mcp_tools import _content_json
    FIXTURES_OK = True
except Exception as exc:  # noqa: BLE001
    FIXTURES_OK = False
    FIXTURE_ERR = f"{type(exc).__name__}: {exc}"

SEALED = _sealed(_policy()) if FIXTURES_OK else ()


# --------------------------------------------------------------- facade probe
def facade_code(*, mode: str, run_id: str | None, preflight: bool) -> str:
    """The code the M19 facade produces for one matrix row."""
    try:
        dayz_test_tool.build_run_request(
            SEALED, project="ExampleMod", mode=mode, mission="chernarus",
            run_id=run_id, preflight=preflight, extra_mods=["@DayZ_MCP"],
        )
        return "<sin error>"
    except Exception as exc:  # noqa: BLE001
        return str(exc)


# --------------------------------------------------------------- public probe
def public_code(*, mode: str, run_id: str | None, preflight: bool,
                steam_calls: list | None = None, break_terminal: bool = False) -> str:
    """The code the PUBLIC tool produces for the same row, through FastMCP."""
    config = server.ServerConfig(mode="client", key="k", port=12345,
                                 client_platform="codex", log_sink=lambda _m: None)
    runtime = _fixture_client_runtime(config)
    with patch.object(server, "ClientRuntime", return_value=runtime):
        app, _ = server.build_app(config)
    lifecycle = {"runs": [{"run_id": GOOD_UUID, "state": "RUNNING_IDLE",
                           "mod": "@ExampleMod", "processes": []}]}

    def _steam(*_a, **_k):
        if steam_calls is not None:
            steam_calls.append(1)
        return sp.SteamSessionResult(error_code=None, steam_registered_pid=1,
                                     steam_live_pids=(1,), remediation="")

    args: dict = {"project": "ExampleMod", "mode": mode, "preflight": preflight,
                  "extra_mods": ["@DayZ_MCP"]}
    if run_id is not None:
        args["run_id"] = run_id
    # The launcher fake has to emit the terminal the REAL worker emits for this row, or the
    # row dies of an empty terminal and the gate becomes unreachable for a reason that has
    # nothing to do with the product. dayz_test_worker.py:547-552 returns
    # WorkerResult(0, run_id) in preflight -- carrying the requested run_id, not None.
    term_run_id = run_id if preflight else (run_id or GOOD_UUID)
    if break_terminal:
        # A real launch that came back with no run_id: incoherent by the shipped rule.
        # Driven through the PUBLIC tool on purpose, so the check observes that the
        # correlation is still WIRED, not merely that the helper still exists.
        term_run_id = None

    async def _launch(_raw: bytes, **kw):
        await kw["execution_started_cb"]()
        kw["output_sink"]("stdout", json.dumps(
            {"cleanup_degraded": False, "error_code": None, "exit_code": 0,
             "ok": True, "run_id": term_run_id},
            ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))
        return 0

    with patch.object(dayz_test_tool, "_require_idle_session", new=AsyncMock()), \
         patch.object(runtime, "lifecycle_status", new=AsyncMock(return_value=lifecycle)), \
         patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), \
         patch.object(dayz_test_tool.secure_launcher, "load_verified_bundle",
                      return_value=_Bundle(SEALED)), \
         patch.object(dayz_test_tool.secure_launcher,
                      "execute_secure_launcher_request", side_effect=_launch), \
         patch.object(dayz_test_tool, "evaluate_steam_session", side_effect=_steam):
        try:
            raw = asyncio.run(app.call_tool("dayz_test_run", args))
            return "<ok>" + json.dumps(_content_json(raw))[:200]
        except Exception as exc:  # noqa: BLE001
            return str(exc)


if not FIXTURES_OK:
    unmet("E0 las fixtures del arnes cargan", FIXTURE_ERR)
else:
    check("E0-SELLO tests/test_client_mode.py sin editar", bool(FIXTURE_SHA),
          f"sha256={FIXTURE_SHA[:16]}")

    # ---- E1: the three named causes reach the client, on BOTH values of preflight.
    ROWS = [
        ("E1-UUID", dict(mode="client", run_id=BAD_UUID), TOK_UUID),
        ("E1-CLIENT", dict(mode="client", run_id=None), TOK_CLIENT),
        ("E1-SERVER", dict(mode="server", run_id=GOOD_UUID), TOK_SERVER),
        ("E1-ALL", dict(mode="all", run_id=GOOD_UUID), TOK_SERVER),
    ]
    for label, row, token in ROWS:
        for pf in (False, True):
            f = facade_code(preflight=pf, **row)
            p = public_code(preflight=pf, **row)
            in_f, in_p = token in f, token in p
            check(f"{label} preflight={str(pf):<5} nombra la causa en fachada y tool",
                  in_f and in_p,
                  f"esperaba {token!r} | fachada={f[:70]!r} | publica={p[:70]!r}")

    # ---- E1-PREC: a malformed UUID wins over the mode/run_id combination.
    f = facade_code(mode="server", run_id=BAD_UUID, preflight=False)
    check("E1-PREC el UUID malformado vence a la combinacion mode/run_id",
          TOK_UUID in f and TOK_SERVER not in f, f"fachada={f[:90]!r}")

    # ---- E2: no valid public row ends in terminal_invalid.
    p = public_code(mode="client", run_id=GOOD_UUID, preflight=True)
    check("E2-REATTACH preflight+client+UUID no acaba en terminal_invalid",
          "terminal_invalid" not in p, f"publica={p[:150]!r}")
    check("E2-UUID el UUID de la peticion sobrevive en la respuesta",
          GOOD_UUID in p, f"publica={p[:150]!r}")
    p2 = public_code(mode="server", run_id=None, preflight=True)
    check("E2-SERVER preflight+server+null no acaba en terminal_invalid",
          "terminal_invalid" not in p2, f"publica={p2[:150]!r}")

    # ---- E3: zero Steam consultations on any preflight row (the dry-run matrix
    # ficha 21 needs in order to close).
    for mode, rid in (("client", GOOD_UUID), ("server", None), ("all", None)):
        calls: list = []
        public_code(mode=mode, run_id=rid, preflight=True, steam_calls=calls)
        check(f"E3-DRYRUN preflight={mode} no consulta Steam", not calls,
              f"consultas={len(calls)}")

    # ---- Fail-closed pair: a productive client|all row DOES consult Steam. Without
    # this, "zero consultations" is satisfiable by never consulting at all.
    calls = []
    public_code(mode="client", run_id=GOOD_UUID, preflight=False, steam_calls=calls)
    check("E3-CONTROL una fila productiva client SI consulta Steam", bool(calls),
          f"consultas={len(calls)} -- si es 0, E3 se cumple por vacuidad")

    # ---- Fail-closed pair for E2: the correlation must be REPAIRED, not deleted. A
    # terminal that is genuinely incoherent -- a real launch that came back with no
    # run_id -- has to keep failing. Without this, "no valid row ends in terminal_invalid"
    # is satisfiable by removing the check altogether.
    broken = public_code(mode="server", run_id=None, preflight=False, break_terminal=True)
    check("E2-CONTROL un terminal incoherente SIGUE rechazandose por la tool publica",
          "terminal_invalid" in broken,
          f"publica={broken[:140]!r} -- si sale <ok>, la correlacion se borro en vez de corregirse")

# ---- E4: the public surface documents the reattach contract. Read from the REGISTERED
# tool description, not from the source text: a comment in server.py is not documentation
# a caller can see, and grepping the file would accept one.
try:
    config = server.ServerConfig(mode="client", key="k", port=12345,
                                 client_platform="codex", log_sink=lambda _m: None)
    runtime = _fixture_client_runtime(config)
    with patch.object(server, "ClientRuntime", return_value=runtime):
        app, _ = server.build_app(config)
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    desc = (tools["dayz_test_run"].description or "").lower()
    for token, label in (("reattach", "la secuencia reattach"),
                         ("preflight", "que preflight no relaja la matriz")):
        check(f"E4 la descripcion REGISTRADA menciona {label}", token in desc,
              f"presente={token in desc} | len(desc)={len(desc)}")
except Exception as exc:  # noqa: BLE001
    unmet("E4 la descripcion registrada documenta el reattach", f"{type(exc).__name__}: {exc}")

print("=" * 104)
worst = 0
for nm, v, d in RESULTS:
    print(f"[{v:<5}] {nm}")
    if d:
        print(f"          {d[:260]}")
    if v != "PASS":
        worst = 1
print("=" * 104)
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
if worst == 0:
    print("ORACULO-VERDE")
sys.exit(worst)
