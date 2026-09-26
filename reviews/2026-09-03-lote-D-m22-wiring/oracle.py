# -*- coding: utf-8 -*-
"""External oracle for lote D. Observes the PRODUCT only, from outside the delegate's tests.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> oracle.py
Exit: 0 iff every check passes. One line per check.

Load-bearing design notes:
- Self-contained fixtures. It imports nothing from tools/tests/, which the implementer
  may edit: a gate whose fixtures live in the candidate's write-set is an answer key.
- No expectation is a literal handed to me. P2-D derives the envelope key set AT RUN TIME
  from a second, already-shipped failure row, so the oracle cannot drift from the source.
- Every product check is paired with a fail-closed check, so an implementation that
  satisfies the product check by deleting the subject goes RED (LL-376).
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []  # (name, PASS|FAIL|UNMET, detail)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


FOUR = (
    "tool_registry_fingerprint",
    "tool_registry_captured_at",
    "tool_registry_source_stale",
    "tool_registry_remediation",
)

from dayz_mcp import dayz_test_request, dayz_test_tool  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402


# ============================================================ P1 - ficha 07
def _app():
    return build_app(ServerConfig(key="k", port=0, log_sink=lambda m: None))


try:
    app, rt = _app()
    rt.start_loopback()
    try:
        res = asyncio.run(app.call_tool("bridge_status", {}))
        payload = (
            res[1]
            if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], dict)
            else json.loads(res[0][0].text)
        )
    finally:
        rt.stop_loopback()
except Exception as exc:
    payload = None
    unmet("P1-A bridge_status publica los cuatro campos", f"{type(exc).__name__}: {exc}")

if payload is not None:
    missing = [k for k in FOUR if k not in payload]
    check(
        "P1-A bridge_status publica los cuatro campos",
        not missing,
        f"faltan={missing}" if missing else f"claves={sorted(payload)}",
    )
    # Conditional on P1-A: a value check over an absent key passes for the wrong
    # reason. The subject has to exist before its shape can be judged.
    if "tool_registry_source_stale" in payload:
        stale = payload["tool_registry_source_stale"]
        check(
            "P1-D1 source_stale es null/unknown sin autoridad, nunca False",
            stale is None or stale == "unknown",
            f"valor={stale!r} tipo={type(stale).__name__}",
        )
    else:
        unmet("P1-D1 source_stale es null/unknown sin autoridad, nunca False", "la clave no existe")
    if "tool_registry_remediation" in payload:
        rem = payload["tool_registry_remediation"]
        check("P1-D2 remediation == reopen_mcp_client", rem == "reopen_mcp_client", f"valor={rem!r}")
    else:
        unmet("P1-D2 remediation == reopen_mcp_client", "la clave no existe")

# P1-B: the overlay is LOCAL to the FastMCP process; the loopback /status stays clean.
try:
    import urllib.request

    app, rt = _app()
    rt.start_loopback()
    raw = None
    try:
        bound = rt.loopback.httpd.server_address[1]
        url = f"http://127.0.0.1:{bound}/status?key=k"
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    finally:
        rt.stop_loopback()
    leaked = [k for k in FOUR if k in json.dumps(raw)]
    check("P1-B /status del loopback sin tool_registry_*", not leaked, f"filtrados={leaked}")
except Exception as exc:
    unmet("P1-B /status del loopback sin tool_registry_*", f"{type(exc).__name__}: {exc}")

# P1-C: the daemon status provider stays clean too.
try:
    from dayz_mcp import daemon as _daemon

    with open(_daemon.__file__, encoding="utf-8") as fh:
        src = fh.read()
    leaked = [k for k in FOUR if k in src]
    check("P1-C daemon.py sin tool_registry_*", not leaked, f"filtrados={leaked}")
except Exception as exc:
    unmet("P1-C daemon.py sin tool_registry_*", f"{type(exc).__name__}: {exc}")


# ============================================================ P2 - ficha 21
# Fixture copied from the shipped, passing test, not invented: default_base_mods
# has to be non-empty or build_run_request fails for a reason unrelated to mission.
def _policy() -> dayz_test_request.RequestProjectPolicy:
    dev_root = r"P:\ExampleMod_Suite"
    return dayz_test_request.RequestProjectPolicy(
        mod="ExampleMod",
        dev_root=dev_root,
        default_source=r"P:\ExampleMod",
        default_base_mods=("@CF", "@Dabs Framework"),
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


SEALED = (types.SimpleNamespace(policy=_policy()),)
INSIDE = r"P:\ExampleMod_Suite\_server\mpmissions\custom.ChernarusPlus"
OUTSIDE = r"C:\Windows\Temp\evil.ChernarusPlus"


# mode="client" also requires a run_id (dayz_test_tool.py:178, client_requires_run_id).
# Omitting it makes every mission case fail for a reason that has nothing to do with the
# mission -- an unreachable gate. Measured during calibration, before delegating.
RUN_ID = "6f1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"


def build(mission: str):
    return dayz_test_tool.build_run_request(
        SEALED, project="ExampleMod", mode="client", mission=mission,
        run_id=RUN_ID, extra_mods=["@DayZ_MCP"]
    )


try:
    build(INSIDE)
    check("P2-A mision absoluta DENTRO de mission_roots aceptada", True, INSIDE)
except Exception as exc:
    check("P2-A mision absoluta DENTRO de mission_roots aceptada", False, f"{type(exc).__name__}: {exc}")

try:
    build("chernarus")
    check("P2-B alias sigue aceptado, sin regresion", True, "chernarus")
except Exception as exc:
    check("P2-B alias sigue aceptado, sin regresion", False, f"{type(exc).__name__}: {exc}")

try:
    build(OUTSIDE)
    check(
        "P2-C mision FUERA de roots sigue rechazada, fail-closed",
        False,
        "ACEPTADA: el arreglo borro el sujeto en vez de corregirlo",
    )
except Exception as exc:
    check(
        "P2-C mision FUERA de roots sigue rechazada, fail-closed",
        True,
        f"rechazada con {type(exc).__name__}: {exc}",
    )


# ---- P2-D/E: the Steam gate. Self-contained fakes, shaped like the shipped ones.
class _Opened:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def validate_native_pe(self) -> None:
        return None


class _Bundle:
    def __init__(self, sealed):
        self.sealed_policies = sealed

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None


class _Rt:
    def __init__(self):
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = object()
        self.bridge_payload = {"ready": {"ready": True, "reason": "ready"}}

    async def bridge_status_payload(self):
        return self.bridge_payload

    async def lifecycle_status(self):
        return {"runs": []}

    async def reconcile_idle_session(self):
        return {"reconciled": False}


def _term(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _drive(*, mode: str, preflight: bool, error_code: str | None, exit_code: int,
           run_id: str | None = None):
    """Run execute_dayz_test_run through the shipped launcher seams.

    The terminal this fake emits has to satisfy the invariant the tree already ships,
    `_validate_terminal_context` (dayz_test_tool.py:524): preflight runs end with no
    run_id, real runs end with one. An incoherent fake is not a hard gate, it is a
    demand that production be loosened to accept it.
    """
    term_run_id = None if preflight else (run_id or RUN_ID)

    async def launch(_raw: bytes, **kw):
        await kw["execution_started_cb"]()
        kw["output_sink"]("stdout", _term({
            "cleanup_degraded": False, "error_code": error_code,
            "exit_code": exit_code, "ok": error_code is None,
            "run_id": None if error_code is not None else term_run_id,
        }))
        return exit_code

    with patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()), \
         patch.object(dayz_test_tool.secure_launcher, "load_verified_bundle",
                      return_value=_Bundle(SEALED)), \
         patch.object(dayz_test_tool.secure_launcher, "execute_secure_launcher_request",
                      side_effect=launch):
        extra = {"run_id": RUN_ID} if mode == "client" else {}
        return asyncio.run(dayz_test_tool.execute_dayz_test_run(
            _Rt(), project="ExampleMod", mode=mode, preflight=preflight,
            extra_mods=["@DayZ_MCP"], **extra))


# The reference row: an already-shipped typed failure. Its key set IS the contract.
try:
    reference = _drive(mode="all", preflight=False,
                       error_code="readiness_udp_foreign_owner", exit_code=2)
    REF_KEYS = set(reference)
except Exception as exc:
    REF_KEYS = None
    unmet("P2-D fila Steam usa el MISMO sobre que la fila de fallo tipado",
          f"no se pudo producir la fila de referencia: {type(exc).__name__}: {exc}")

SEAM = "evaluate_steam_session"
if not hasattr(dayz_test_tool, SEAM):
    check(f"P2-D0 el punto de llamada existe como dayz_test_tool.{SEAM}", False,
          "ausente: el gate de Steam no esta cableado")
    unmet("P2-D fila Steam usa el MISMO sobre que la fila de fallo tipado", "sin punto de llamada")
    unmet("P2-E preflight=True y mode=server no consultan Steam", "sin punto de llamada")
else:
    check(f"P2-D0 el punto de llamada existe como dayz_test_tool.{SEAM}", True, "")
    from dayz_mcp import steam_preflight as _sp

    stale = _sp.SteamSessionResult(
        error_code=_sp.STEAM_SESSION_STALE, steam_registered_pid=4321,
        steam_live_pids=(1, 2, 3), remediation="restart Steam")
    calls: list[int] = []

    def _stale_provider(*_a, **_k):
        calls.append(1)
        return stale

    if REF_KEYS is not None:
        try:
            with patch.object(dayz_test_tool, SEAM, side_effect=_stale_provider):
                row = _drive(mode="client", preflight=False, error_code=None, exit_code=0)
            same = set(row) == REF_KEYS
            shape = (row.get("status") == "failed"
                     and row.get("error_code") == _sp.STEAM_SESSION_STALE
                     and row.get("run_id") is None
                     and row.get("phase") == "validating"
                     and row.get("artifacts_paths") == [])
            check("P2-D fila Steam usa el MISMO sobre que la fila de fallo tipado",
                  same and shape,
                  f"mismas_claves={same} forma={shape} extra={sorted(set(row)-REF_KEYS)} "
                  f"faltan={sorted(REF_KEYS-set(row))} status={row.get('status')!r} "
                  f"error_code={row.get('error_code')!r} phase={row.get('phase')!r}")
        except Exception as exc:
            check("P2-D fila Steam usa el MISMO sobre que la fila de fallo tipado", False,
                  f"{type(exc).__name__}: {exc}")

    # P2-F (B-01 de la revision cruzada de Codex): el evaluador de Steam es HOY una funcion
    # total -- _read_stable_snapshot y _safe_live_pids envuelven todo, y el constructor del
    # proveedor no tiene __init__ -- asi que este disparador no es alcanzable en produccion.
    # El gate existe igual porque esa totalidad es un detalle de OTRO modulo: el contrato de
    # dayz_test_run es devolver siempre el sobre, y no puede depender de que un vecino no
    # lance nunca. Endurece una frontera, no cierra un agujero explotable.
    try:
        with patch.object(dayz_test_tool, SEAM, side_effect=RuntimeError("sonda P2-F")):
            row = _drive(mode="client", preflight=False, error_code=None, exit_code=0)
        ok = (isinstance(row, dict) and row.get("status") == "failed"
              and (REF_KEYS is None or set(row) == REF_KEYS))
        check("P2-F una excepcion del evaluador sigue dando el sobre, no ToolError", ok,
              f"status={row.get('status')!r} error_code={row.get('error_code')!r} "
              f"mismas_claves={REF_KEYS is not None and set(row) == REF_KEYS}")
    except Exception as exc:
        check("P2-F una excepcion del evaluador sigue dando el sobre, no ToolError", False,
              f"escapo {type(exc).__name__}: {exc}")

    for label, kw in (("preflight=True", dict(mode="all", preflight=True)),
                      ("mode=server", dict(mode="server", preflight=False))):
        calls.clear()
        try:
            with patch.object(dayz_test_tool, SEAM, side_effect=_stale_provider):
                _drive(error_code=None, exit_code=0, **kw)
            check(f"P2-E {label} no consulta Steam", not calls, f"consultas={len(calls)}")
        except Exception as exc:
            check(f"P2-E {label} no consulta Steam", False, f"{type(exc).__name__}: {exc}")

print("=" * 100)
worst = 0
for nm, verdict, detail in RESULTS:
    print(f"[{verdict:<5}] {nm}")
    if detail:
        print(f"          {detail[:280]}")
    if verdict != "PASS":
        worst = 1
print("=" * 100)
tally = {v: sum(1 for _, x, _ in RESULTS if x == v) for v in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO: PASS={tally['PASS']} FAIL={tally['FAIL']} UNMET={tally['UNMET']} de {len(RESULTS)}")
# Stable success marker, emitted after every assertion and independent of how many checks
# the topology produced. The count is diagnostics; this line is the contract.
if worst == 0:
    print("ORACULO-VERDE")
sys.exit(worst)
