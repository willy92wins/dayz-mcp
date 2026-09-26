# -*- coding: utf-8 -*-
"""External oracle for lote H: ficha 16 (4d66) PASS blocks 3 and 4, ficha 17 (7c88) PASS 2.

Subject: the generation projection of a PRESENT run (H1), the retired-run diagnostics ring
(H2), the structured envelope of `execute_dayz_test_stop` (H3), its public transport through
`dayz_test_stop` (H4), and telling a successful `lifecycle_stop_outcome` apart from every
other terminal shape (H5).

Run:  cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 <venv-python> ../gate/oracle.py

Load-bearing design notes:
- Self-contained fixtures. It imports NOTHING from tools/tests/, which the implementer may
  edit: a gate whose fixtures live in the candidate's write-set is an answer key. The fakes
  below (`Sink`, `Guard`, `Launcher`, `_Runtime`, `_Opened`, `_Bundle`, `client_runtime`) are
  shaped like the shipped ones but owned here.
- Expectations are DERIVED, never literals. The daemon generation is a fresh uuid4 per
  process, injected through the shipped `ProcessLifecycle(daemon_generation=...)` seam
  (process_lifecycle.py:805,819-821); an implementation that hardcodes a generation cannot
  satisfy it. The copied-envelope checks read their expected values back out of the very
  snapshot the fixture injected.
- The mid-life prune that makes a stopped run ABSENT is the shipped one: `repair_manifest_
  recovery` rebinds `self.manifest = RunManifestStore(paths)` (process_lifecycle.py:2320-2326)
  and `RunManifestStore.__init__` prunes EXITED on load (:452,517-539). The fixture reproduces
  exactly that rebind, so the ring survives the prune the way the daemon's does.
- Every acceptance check is paired with a fail-closed check. Every check that depends on a
  fixture reaching a state guards it: if the fixture does not get there, UNMET, never PASS
  and never FAIL.
- The RONDA 2 block (H6-H10) encodes the five defects reproduced by Codex's blind review
  plus the ring-exhaustion one, on a delivery that already passes the 29 original checks.
- Every `path:line` in this file was verified with the file open: the RONDA 1 ones against
  the round-1 reference tree, the RONDA 2 ones against ws-frozen-r1. Line numbers shift with
  each delivery; the symbol names do not, so grep the symbol before trusting the number.
- Checks named [GUARDA] are regression guards: they are green before the lote and must stay
  green. They are declared as such in GATES-lote-H.md; they measure preservation, not product.
"""
from __future__ import annotations

import asyncio
import builtins
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import types
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


from dayz_mcp import dayz_test_request, dayz_test_tool, host_config  # noqa: E402
from dayz_mcp import server as server_module  # noqa: E402
from dayz_mcp.process_lifecycle import (  # noqa: E402
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
)
from dayz_mcp.runtime_state import JsonlAuditWriter, RuntimePaths  # noqa: E402
from dayz_mcp.server import ServerConfig, build_app  # noqa: E402
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator  # noqa: E402

IDENTITY = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
HASH_A = "a" * 64
HASH_B = "b" * 64
CREATED_UTC = "2026-07-15T00:00:41.0000000Z"
# UUID4 canonico: `_exact_run` lo exige antes de mirar el manifiesto
# (dayz_test_tool.py:206-211), asi que el run del fixture tiene que llevarlo.
RUN_UUID = "12345678-1234-4234-8234-1234567890ab"
OTHER_UUID = "87654321-4321-4321-8321-ba0987654321"
PROJECT_MOD = "StorageMod"
DEV_ROOT = r"C:\Tools\LFV_D2_Executor"
PROFILES = DEV_ROOT + r"\_client\profiles"

# Los tres campos del contrato. Se nombran una vez para que ningun check los teclee suelto.
GEN_LAUNCH = "daemon_generation_at_launch"
GEN_CURRENT = "daemon_generation_current"
GEN_CHANGED = "generation_changed"
GEN_FIELDS = (GEN_LAUNCH, GEN_CURRENT, GEN_CHANGED)
DIAG_KEY = "retired_run_diagnostics"
DIAG_REQUIRED = frozenset(
    {"run_id", GEN_LAUNCH, GEN_CURRENT, "event", "reason", "decision", "state"}
)
DIAG_CAP = 32


def fresh_generation(tag: str) -> str:
    """A generation nobody can hardcode: derived here, injected by constructor."""
    return f"gen-{tag}-{uuid.uuid4().hex}"


# ---------------------------------------------------------------- fakes propias del oraculo
class Sink:
    """Audit sink. Returning False is how the tree signals a writer fault."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict) -> bool:
        self.events.append(dict(event))
        return event.get("event") not in self.fail_events


class Guard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict] = {}
        self.terminated: list[int] = []

    def snapshot(self, pid: int) -> dict:
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: object) -> dict:
        self.terminated.append(getattr(record, "pid", record))
        return {"terminated": True}


class Launcher:
    def __init__(self, pid: int = 9001) -> None:
        self.pid = pid
        self.confirmed_exit = True

    def __call__(self, argv, cwd, window_style):
        return self

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        return None


def _paths(tmp: Path) -> RuntimePaths:
    return RuntimePaths(
        tmp / "runtime",
        tmp / "runtime" / "audit",
        tmp / "runtime" / "coordination.json",
        tmp / "runtime" / "runs.json",
    )


def _snapshot_of(record: ProcessRecord) -> dict:
    return {
        "pid": record.pid,
        "creation_time_utc": record.creation_time_utc,
        "executable_sha256": record.executable_sha256,
        "command_line_sha256": record.command_line_sha256,
        "identity_scheme": record.identity_scheme,
        "identity_complete": True,
    }


def build(
    tmp: Path,
    *,
    generation: str,
    run_id: str = RUN_UUID,
    pid: int = 9001,
    audit: object | None = None,
    manifest: RunManifestStore | None = None,
    start: bool = True,
):
    """A live ProcessLifecycle over a temp runtime root, with one started run.

    `start_run` fails closed with identity_unavailable when the guard has no snapshot for the
    launched pid, and the run then lands EXITED with an empty box -- so the snapshot is seeded
    before launching. Measured during calibration.
    """
    game = tmp / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "DayZDiag_x64.exe").write_bytes(b"")
    paths = _paths(tmp)
    sink = Sink() if audit is None else audit
    audit_fn = sink if callable(sink) else sink.write
    guard = Guard()
    coord = SessionCoordinator(
        token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=audit_fn
    )
    status, acquired = coord.acquire(IDENTITY, "lifecycle")
    assert status == 200, f"acquire devolvio {status}"
    life = ProcessLifecycle(
        coordinator=coord,
        manifest=manifest if manifest is not None else RunManifestStore(paths),
        audit=audit_fn,
        guard=guard,
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=lambda: {"known": True, "processes": []},
        game_path=game,
        launcher=Launcher(pid),
        id_fn=lambda: run_id,
        argv_of=lambda _pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        daemon_generation=generation,
    )
    launched = ProcessRecord(
        pid, CREATED_UTC, HASH_A, HASH_B, "client", identity_scheme="psutil-argv-v2"
    )
    guard.snapshots[pid] = _snapshot_of(launched)
    token = acquired["lease_token"]
    if not start:
        return life, sink, guard, paths, token
    started = life.start_run(
        IDENTITY,
        token,
        {
            "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"],
            "cwd": str(game),
            "role": "client",
            "window_style": "normal",
            "label": "gate",
            "mod": "@" + PROJECT_MOD,
            "profiles": PROFILES,
            "mission": "test",
        },
    )
    assert started.get("ok") is True, f"start_run fallo: {started}"
    return life, sink, guard, paths, token


def reload_manifest(life: ProcessLifecycle, paths: RuntimePaths) -> None:
    """The shipped mid-life prune: repair_manifest_recovery rebinds the store over the same
    paths (process_lifecycle.py:2320-2326) and __init__ prunes EXITED (:452,517-539). The ring
    is daemon memory, so it must survive this."""
    life.manifest = RunManifestStore(paths)
    life._invalidate_box_cache()


def status_runs(payload: object) -> list[dict]:
    runs = payload.get("runs") if isinstance(payload, dict) else None
    return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def row_for(payload: object, run_id: str) -> dict | None:
    for row in status_runs(payload):
        if row.get("run_id") == run_id:
            return row
    return None


def diagnostics(payload: object) -> object:
    return payload.get(DIAG_KEY) if isinstance(payload, dict) else None


def diag_for(payload: object, run_id: str) -> list[dict]:
    raw = diagnostics(payload)
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict) and d.get("run_id") == run_id]


def missing(row: object, fields=GEN_FIELDS) -> list[str]:
    if not isinstance(row, dict):
        return list(fields)
    return [f for f in fields if f not in row]


# ------------------------------------------------------- fakes de la fachada dayz_test_tool
def policy(mod: str = PROJECT_MOD, dev_root: str = DEV_ROOT):
    return dayz_test_request.RequestProjectPolicy(
        mod=mod,
        dev_root=dev_root,
        default_source=dev_root + r"\staged-source" + "\\" + mod,
        default_base_mods=("@CF",),
        mission_roots=(dev_root + r"\_server\mpmissions",),
        mod_roots=(r"P:\Mods",),
    )


def sealed(*policies):
    return tuple(types.SimpleNamespace(policy=p) for p in policies)


class _Opened:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def validate_native_pe(self) -> None:
        return None


class _Bundle:
    def __init__(self, sealed_policies):
        self.sealed_policies = sealed_policies

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None


class _Runtime:
    """The dayz_test_tool runtime seam. Counts lifecycle_status() calls so the gate can say
    how many pre-dispatch snapshots were taken."""

    def __init__(self, lifecycle: dict | None = None) -> None:
        self.active_lease_token = None
        self.active_ticket = None
        self.active_operation_id = None
        self.daemon_policy = object()
        self.lifecycle = lifecycle if lifecycle is not None else {"runs": []}
        self.sequence: list[dict] | None = None
        self.lifecycle_calls = 0
        self.calls_at_dispatch: int | None = None
        self.bridge_payload = {"ready": {"ready": True, "reason": "ready"}}

    async def bridge_status_payload(self) -> dict:
        return self.bridge_payload

    async def lifecycle_status(self) -> dict:
        self.lifecycle_calls += 1
        if self.sequence:
            index = min(self.lifecycle_calls - 1, len(self.sequence) - 1)
            return self.sequence[index]
        return self.lifecycle

    async def reconcile_idle_session(self) -> dict:
        return {"reconciled": False}


def terminal_bytes(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


POLICY = policy()


def launcher_ok(runtime: _Runtime, *, error_code=None, exit_code=0, ok=True,
                degraded=False):
    async def _launch(_raw: bytes, **kwargs):
        runtime.calls_at_dispatch = runtime.lifecycle_calls
        started = kwargs.get("execution_started_cb")
        if started is not None:
            await started()
        kwargs["output_sink"](
            "stdout",
            terminal_bytes(
                {
                    "cleanup_degraded": degraded,
                    "error_code": error_code,
                    "exit_code": exit_code,
                    "ok": ok,
                    "run_id": RUN_UUID,
                }
            ),
        )
        return exit_code

    return _launch


def tool_patches(launch):
    return (
        patch.object(dayz_test_tool, "open_approved_launcher", return_value=_Opened()),
        patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(sealed(POLICY)),
        ),
        patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ),
    )


def run_stop(status_payload, *, run_id: str = RUN_UUID, sequence=None, **launch_kw):
    """Drive execute_dayz_test_stop. Returns ('dict', payload, runtime) or
    ('raise', exception, runtime)."""
    runtime = _Runtime(status_payload)
    if sequence is not None:
        runtime.sequence = sequence
    a, b, c = tool_patches(launcher_ok(runtime, **launch_kw))

    async def _go():
        with a, b, c:
            return await dayz_test_tool.execute_dayz_test_stop(runtime, run_id)

    try:
        return "dict", asyncio.run(_go()), runtime
    except BaseException as exc:  # noqa: BLE001
        return "raise", exc, runtime


ACTIVE_ROW = {
    "run_id": RUN_UUID,
    "state": "RUNNING_IDLE",
    "mod": "@" + PROJECT_MOD,
    "profiles": PROFILES,
}
# Un run PRESENTE pero terminal: EXITED con launch reconocido no es `never_started`
# (dayz_test_tool.py:233-238), asi que resolve_stop_run llega a run_not_active.
TERMINAL_ROW = {**ACTIVE_ROW, "state": "EXITED", "launch_acknowledged": True}


def envelope_ok(result: object, code: str, source: dict) -> tuple[bool, str]:
    """The envelope contract, with every expected value READ BACK from `source`."""
    if not isinstance(result, dict):
        return False, f"no es dict: {type(result).__name__}"
    problems = []
    if result.get("status") != "failed":
        problems.append(f"status={result.get('status')!r} (esperado 'failed')")
    if result.get("run_id") != RUN_UUID:
        problems.append(f"run_id={result.get('run_id')!r}")
    if result.get("error_code") != code:
        problems.append(f"error_code={result.get('error_code')!r} (esperado {code!r})")
    for field in GEN_FIELDS:
        if field not in result:
            problems.append(f"falta {field}")
        elif result[field] != source.get(field):
            problems.append(
                f"{field}={result[field]!r} pero el snapshot dice {source.get(field)!r}"
            )
    return (not problems), "; ".join(problems)


# =========================================================================================
# H1 -- la generacion de un run PRESENTE
# =========================================================================================
GEN_A = fresh_generation("A")

with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, guard, paths, token = build(Path(d), generation=GEN_A)
        st = life.status(IDENTITY)
        row = row_for(st, RUN_UUID)
        if row is None:
            unmet(
                "H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo",
                f"el fixture no dejo el run en status(): runs={[r.get('run_id') for r in status_runs(st)]}",
            )
        else:
            faltan = missing(row)
            check(
                "H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo",
                not faltan
                and row[GEN_LAUNCH] == GEN_A
                and row[GEN_CURRENT] == GEN_A
                and row[GEN_CHANGED] is False,
                f"faltan={faltan} fila={ {k: row.get(k) for k in GEN_FIELDS} } "
                f"generacion inyectada={GEN_A!r} -- los tres campos se derivan de la "
                f"generacion que recibe el constructor, no de un literal",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H1-STATUS-GEN-PRESENTE status() adjunta las tres generaciones al run vivo",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, guard, paths, token = build(Path(d), generation=GEN_A)
        box_row = row_for(life.box_occupancy(), RUN_UUID)
        st_row = row_for(life.status(IDENTITY), RUN_UUID)
        if box_row is None or st_row is None:
            unmet(
                "H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones",
                f"el fixture no dejo el run en ambas proyecciones: box={box_row is not None} "
                f"status={st_row is not None}",
            )
        else:
            faltan = missing(box_row)
            coherente = all(box_row.get(f) == st_row.get(f) for f in GEN_FIELDS)
            check(
                "H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones",
                not faltan and coherente and box_row[GEN_LAUNCH] == GEN_A,
                f"faltan={faltan} box={ {k: box_row.get(k) for k in GEN_FIELDS} } "
                f"status={ {k: st_row.get(k) for k in GEN_FIELDS} } -- las dos proyecciones "
                f"alimentan consumidores distintos (occupancy_error_fields y resolve_stop_run) "
                f"y no pueden discrepar",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H1-BOX-GEN-PRESENTE box_occupancy adjunta las mismas tres generaciones",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # El run sobrevive al reinicio del daemon: misma runs.json, ProcessLifecycle nuevo con
    # OTRA generacion. La generacion de lanzamiento solo puede salir del MANIFIESTO.
    try:
        gen_1 = fresh_generation("1")
        gen_2 = fresh_generation("2")
        life, sink, guard, paths, token = build(Path(d), generation=gen_1)
        sobrevive = [r.run_id for r in RunManifestStore(paths).list_runs()]
        life2, sink2, guard2, _p, _t = build(
            Path(d), generation=gen_2, manifest=RunManifestStore(paths), start=False
        )
        row = row_for(life2.status(IDENTITY), RUN_UUID)
        if RUN_UUID not in sobrevive or row is None:
            unmet(
                "H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva",
                f"el fixture no dejo el run tras el reinicio: en manifiesto={sobrevive} "
                f"en status={row is not None}. No se ha medido la persistencia.",
            )
        else:
            faltan = missing(row)
            check(
                "H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva",
                not faltan
                and row[GEN_LAUNCH] == gen_1
                and row[GEN_CURRENT] == gen_2
                and row[GEN_CHANGED] is True,
                f"faltan={faltan} fila={ {k: row.get(k) for k in GEN_FIELDS} } "
                f"lanzamiento={gen_1!r} actual={gen_2!r} -- si at_launch sale igual a la "
                f"actual, la generacion de lanzamiento no se persistio y se esta "
                f"recomputando contra el daemon vivo",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H1-GEN-CAMBIADA un run heredado publica lanzamiento viejo y actual nueva",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Fail-closed pareado: manifiesto LEGACY (sin el campo nuevo). El campo a quitar se
    # deriva del propio fichero, no de una lista escrita a mano.
    try:
        gen_1 = fresh_generation("L1")
        gen_2 = fresh_generation("L2")
        life, sink, guard, paths, token = build(Path(d), generation=gen_1)
        payload = json.loads(paths.runs_path.read_text(encoding="utf-8"))
        quitados = []
        for entry in payload.get("runs", []):
            for field in GEN_FIELDS:
                if field in entry:
                    entry.pop(field)
                    quitados.append(field)
        paths.runs_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        cargado = RunManifestStore(paths)
        life2, _s, _g, _p, _t = build(
            Path(d), generation=gen_2, manifest=cargado, start=False
        )
        row = row_for(life2.status(IDENTITY), RUN_UUID)
        if row is None:
            unmet(
                "H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false",
                f"el manifiesto legacy no conservo el run (quitados={quitados}). "
                f"Si el store lo rechazo, el rojo real es D2-MANIFIESTO-VIEJO-CARGA.",
            )
        elif not missing(row) and not quitados:
            # Anti-vacuidad hacia adelante: si status ya publica los tres campos pero el
            # manifiesto nunca llevo ninguno, no se ha quitado nada y este check no ha
            # medido tolerancia a legacy: mediria lo mismo que D2-MUT-FROM-PAYLOAD-NO-LEE.
            unmet(
                "H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false",
                f"no habia campo que quitar del manifiesto (claves del run en disco sin "
                f"ninguno de {list(GEN_FIELDS)}), asi que el fixture legacy es identico al "
                f"actual. El rojo que manda es D2-MUT-FROM-PAYLOAD-NO-LEE.",
            )
        else:
            faltan = missing(row)
            check(
                "H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false",
                not faltan
                and row[GEN_LAUNCH] is None
                and row[GEN_CHANGED] is None
                and row[GEN_CURRENT] == gen_2,
                f"faltan={faltan} fila={ {k: row.get(k) for k in GEN_FIELDS} } "
                f"campos quitados del manifiesto={quitados} -- generation_changed=False "
                f"afirmaria continuidad que nadie acredito; el valor fail-closed es null",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H1-FAIL-CLOSED-SIN-GENERACION manifiesto legacy publica null, nunca false",
            f"{type(exc).__name__}: {exc}",
        )


# =========================================================================================
# D2 -- compatibilidad del manifiesto (data-critica, DZ-R9)
# =========================================================================================
with tempfile.TemporaryDirectory() as d:
    # [GUARDA] Un manifiesto preexistente (sin el campo) tiene que CARGAR. Si from_payload no
    # tolera la ausencia, todo manifiesto en produccion es invalid_run_manifest al arrancar.
    try:
        life, sink, guard, paths, token = build(Path(d), generation=fresh_generation("D2a"))
        payload = json.loads(paths.runs_path.read_text(encoding="utf-8"))
        for entry in payload.get("runs", []):
            for field in GEN_FIELDS:
                entry.pop(field, None)
        paths.runs_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        error = None
        try:
            cargado = [r.run_id for r in RunManifestStore(paths).list_runs()]
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            cargado = []
        check(
            "[GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando",
            error is None and RUN_UUID in cargado,
            f"error={error} runs={cargado} -- un campo nuevo que from_payload no tolere "
            f"ausente convierte TODO manifiesto preexistente en invalid_run_manifest en el "
            f"primer arranque tras la entrega",
        )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "[GUARDA] D2-MANIFIESTO-VIEJO-CARGA un manifiesto sin el campo nuevo sigue cargando",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Mutante de la ficha: "campo anadido pero from_payload no lo lee". Dos angulos, porque
    # se pierde en silencio por dos caminos distintos: el round-trip in-memory de _clone
    # (asdict -> from_payload, :565) y la recarga durable desde disco (:_load).
    try:
        gen = fresh_generation("D2b")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        en_disco = json.loads(paths.runs_path.read_text(encoding="utf-8"))
        entrada = next(
            (e for e in en_disco.get("runs", []) if e.get("run_id") == RUN_UUID), None
        )
        if entrada is None:
            unmet(
                "D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga",
                "el run no llego al manifiesto en disco; no se ha medido el round-trip",
            )
        elif GEN_LAUNCH not in entrada:
            check(
                "D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga",
                False,
                f"el manifiesto persistido no lleva {GEN_LAUNCH}: claves={sorted(entrada)} "
                f"-- sin persistirlo, la generacion de lanzamiento no sobrevive a un reinicio",
            )
        else:
            clonado = life.manifest.get(RUN_UUID)  # get() devuelve _clone(run)
            recargado = next(
                (r for r in RunManifestStore(paths).list_runs() if r.run_id == RUN_UUID),
                None,
            )
            v_clone = getattr(clonado, GEN_LAUNCH, "<sin atributo>")
            v_disco = getattr(recargado, GEN_LAUNCH, "<sin atributo>")
            check(
                "D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga",
                v_clone == gen and v_disco == gen,
                f"tras _clone={v_clone!r} tras recargar={v_disco!r} esperado={gen!r} "
                f"-- _clone hace asdict->from_payload: un campo que from_payload no lea se "
                f"pierde en silencio en cada get()/list_runs(), sin lanzar nada",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "D2-MUT-FROM-PAYLOAD-NO-LEE el campo sobrevive a _clone y a la recarga",
            f"{type(exc).__name__}: {exc}",
        )

try:
    # [GUARDA] La unica construccion posicional de RunRecord pasa 12 argumentos sin nombre
    # (process_lifecycle.py:1445-1462). Un campo insertado ANTES de launch_operation_id
    # desplaza los tres ultimos y launch_acknowledged recibe el sha, sin error de tipo.
    op_id = OTHER_UUID
    posicional = RunRecord(
        RUN_UUID,
        "A",
        "lease-A",
        "STARTING",
        "gate",
        "@" + PROJECT_MOD,
        PROFILES,
        "test",
        [],
        op_id,
        HASH_A,
        False,
    )
    posicional.validate()
    check(
        "[GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio",
        posicional.launch_operation_id == op_id
        and posicional.launch_request_sha256 == HASH_A
        and posicional.launch_acknowledged is False,
        f"launch_operation_id={posicional.launch_operation_id!r} "
        f"launch_request_sha256={posicional.launch_request_sha256!r} "
        f"launch_acknowledged={posicional.launch_acknowledged!r} -- el campo nuevo va al "
        f"FINAL con default; insertarlo antes desplaza el unico call-site posicional",
    )
except Exception as exc:  # noqa: BLE001
    check(
        "[GUARDA] D2-POSICIONAL-NO-SE-DESPLAZA los 12 argumentos posicionales siguen en su sitio",
        False,
        f"la construccion posicional de 12 argumentos ya no valida: {type(exc).__name__}: {exc}",
    )


# =========================================================================================
# H2 -- diagnosticos de runs retirados
# =========================================================================================
with tempfile.TemporaryDirectory() as d:
    try:
        gen = fresh_generation("H2a")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        parado = life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        st = life.status(IDENTITY)
        presente = row_for(st, RUN_UUID) is not None
        if not (isinstance(parado, dict) and parado.get("ok") is True) or presente:
            unmet(
                "H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto",
                f"el fixture no dejo el run retirado y ausente: stop={parado} "
                f"sigue_en_runs={presente}",
            )
        else:
            hits = diag_for(st, RUN_UUID)
            ok = len(hits) == 1 and not missing(hits[0], (GEN_LAUNCH, GEN_CURRENT))
            check(
                "H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto",
                ok
                and hits[0].get("event") == "lifecycle_stop_outcome"
                and hits[0].get("decision") == "stopped"
                and hits[0].get("state") == "EXITED"
                and hits[0].get(GEN_LAUNCH) == gen,
                f"diagnosticos={diagnostics(st)!r} -- se espera exactamente uno para "
                f"{RUN_UUID}, con la generacion de lanzamiento {gen!r}",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-DIAG-TRAS-STOP un run parado y podado deja UN diagnostico exacto",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Segundo camino de retirada. Un anillo enganchado solo a stop_run deja el reaper mudo,
    # y el reaper es justo el que retira un run cuyo juego se murio solo.
    try:
        gen = fresh_generation("H2b")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        reaped = life.reap_dead_runs()
        reload_manifest(life, paths)
        st = life.status(IDENTITY)
        if RUN_UUID not in reaped or row_for(st, RUN_UUID) is not None:
            unmet(
                "H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop",
                f"el fixture no reapeo/podo el run: reaped={reaped} "
                f"sigue_en_runs={row_for(st, RUN_UUID) is not None}",
            )
        else:
            hits = diag_for(st, RUN_UUID)
            check(
                "H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop",
                len(hits) == 1 and hits[0].get("event") == "run_reaped",
                f"diagnosticos={diagnostics(st)!r} -- si solo stop_run alimenta el anillo, "
                f"un run reapeado desaparece sin dejar rastro y su dayz_test_stop no puede "
                f"devolver mas que una excepcion pelada",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-DIAG-TRAS-REAP el reaper alimenta el mismo anillo que el stop",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Mutante de la ficha: ">32 diagnosticos". Se siembran 40 runs reapeables y se retiran en
    # UNA pasada; list_runs() los recorre en orden de run_id, asi que el orden de retirada es
    # conocido y el anillo tiene que quedarse con los 32 ULTIMOS.
    try:
        gen = fresh_generation("H2c")
        life, sink, guard, paths, token = build(Path(d), generation=gen, start=False)
        total = 40
        ids = [f"seed-{index:02d}" for index in range(total)]
        for index, run_id in enumerate(ids):
            pid = 20000 + index
            record = ProcessRecord(
                pid, CREATED_UTC, HASH_A, HASH_B, "client",
                identity_scheme="psutil-argv-v2",
            )
            life.manifest.add(
                RunRecord(
                    run_id, None, None, "RUNNING_IDLE", "gate", "@" + PROJECT_MOD,
                    PROFILES, "test", [record],
                )
            )
            guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}
        reaped = life.reap_dead_runs()
        reload_manifest(life, paths)
        st = life.status(IDENTITY)
        raw = diagnostics(st)
        if len(reaped) != total:
            unmet(
                "H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes",
                f"el fixture no retiro los {total} runs sembrados: reaped={len(reaped)}. "
                f"Sin {total} retiradas el tope de {DIAG_CAP} no se puede ejercitar.",
            )
        else:
            publicados = (
                [d.get("run_id") for d in raw if isinstance(d, dict)]
                if isinstance(raw, list)
                else []
            )
            esperados = ids[-DIAG_CAP:]
            check(
                "H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes",
                isinstance(raw, list)
                and len(raw) <= DIAG_CAP
                and set(publicados) == set(esperados)
                and not (set(publicados) & set(ids[:-DIAG_CAP])),
                f"retirados={len(reaped)} anillo={type(raw).__name__} "
                f"publicados={len(publicados)} (tope {DIAG_CAP}) "
                f"faltan_de_los_recientes={sorted(set(esperados) - set(publicados))[:5]} "
                f"sobran_viejos={sorted(set(publicados) & set(ids[:-DIAG_CAP]))[:5]} -- un "
                f"anillo sin tope crece con cada retirada y viaja entero en cada status",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-TOPE-32-POR-RECENCIA como maximo 32 diagnosticos, y son los mas recientes",
            f"{type(exc).__name__}: {exc}",
        )

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_WINPATH = re.compile(r"[A-Za-z]:[\\/]")
_PROHIBIDO_EN_CLAVE = ("time", "stamp", "path", "dir", "file", "profil", "argv", "cwd",
                       "mission", "utc", "epoch", "at_")


def huele_a_timestamp_o_path(key: object, value: object) -> str | None:
    name = key if isinstance(key, str) else str(key)
    lowered = name.casefold()
    if lowered not in {GEN_LAUNCH, GEN_CURRENT} and any(
        token in lowered for token in _PROHIBIDO_EN_CLAVE
    ):
        return f"clave {name!r}"
    if isinstance(value, str):
        if _ISO.search(value):
            return f"{name}={value!r} parece un timestamp ISO"
        if _WINPATH.search(value) or "\\" in value or value.startswith("/"):
            return f"{name}={value!r} parece un path"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1e9:
        return f"{name}={value!r} parece un epoch"
    return None


with tempfile.TemporaryDirectory() as d:
    # Mutante de la ficha: "timestamp o path en un diagnostico". El diagnostico es publico:
    # un profiles o un ISO lo convierten en fuga de host y en reloj absoluto.
    try:
        gen = fresh_generation("H2d")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        st = life.status(IDENTITY)
        hits = diag_for(st, RUN_UUID)
        if not hits:
            unmet(
                "H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta",
                f"no hay diagnostico que inspeccionar: {diagnostics(st)!r}. El sujeto de "
                f"este check todavia no existe.",
            )
        else:
            hallazgos = []
            for entry in hits:
                faltan = DIAG_REQUIRED - set(entry)
                if faltan:
                    hallazgos.append(f"faltan claves obligatorias {sorted(faltan)}")
                for key, value in entry.items():
                    olor = huele_a_timestamp_o_path(key, value)
                    if olor:
                        hallazgos.append(olor)
            check(
                "H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta",
                not hallazgos,
                f"hallazgos={hallazgos[:6]} diagnostico={hits[0]!r}",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-SIN-TIMESTAMP-NI-PATH ningun diagnostico expone reloj absoluto ni ruta",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # D1: el anillo es memoria del daemon. Tras un reinicio empieza VACIO y eso se publica
    # como esta. Anti-vacuidad: el audit en disco TIENE que llevar la retirada, o el check
    # estaria midiendo que no habia nada que reconstruir.
    try:
        gen = fresh_generation("H2e")
        paths = _paths(Path(d))
        paths.audit_dir.mkdir(parents=True, exist_ok=True)
        writer = JsonlAuditWriter(paths, gen)
        life, _s, guard, _p, token = build(Path(d), generation=gen, audit=writer)
        life.stop_run(IDENTITY, token, RUN_UUID)
        lineas = [
            json.loads(line)
            for line in writer.current_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        retiradas = [
            e for e in lineas
            if e.get("event") in {"lifecycle_stop_outcome", "run_reaped"}
            and e.get("run_id") == RUN_UUID
        ]
        gen2 = fresh_generation("H2e2")
        life2, _s2, _g2, _p2, _t2 = build(
            Path(d), generation=gen2, manifest=RunManifestStore(paths),
            audit=writer, start=False,
        )
        st2 = life2.status(IDENTITY)
        if not retiradas:
            unmet(
                "H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia",
                f"el audit en disco no registro la retirada ({len(lineas)} eventos); sin "
                f"historia en el jsonl el check no discrimina reconstruir de no reconstruir",
            )
        else:
            raw = diagnostics(st2)
            check(
                "H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia",
                raw == [] and row_for(st2, RUN_UUID) is None,
                f"anillo tras reiniciar={raw!r} run_presente={row_for(st2, RUN_UUID) is not None} "
                f"eventos_de_retirada_en_el_jsonl={len(retiradas)} -- publicar un diagnostico "
                f"aqui significaria haberlo reconstruido leyendo events.jsonl, que es lo que "
                f"D1 prohibe; la lista vacia es la respuesta correcta",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-ANILLO-EN-MEMORIA-EMPIEZA-VACIO tras reiniciar no se inventa historia",
            f"{type(exc).__name__}: {exc}",
        )


class OpenSpy:
    """Records every read-capable open under a directory. builtins.open, Path.open and
    os.open are patched because the three reach the filesystem by different doors."""

    def __init__(self, root: Path) -> None:
        self.root = str(root).casefold()
        self.hits: list[str] = []
        self._saved: list[tuple[object, str, object]] = []

    def _note(self, target: object, mode: object) -> None:
        text = str(target).casefold()
        if not text.startswith(self.root):
            return
        if isinstance(mode, str) and not any(ch in mode for ch in ("r", "+")):
            return
        if isinstance(mode, int) and (mode & (os.O_WRONLY | os.O_APPEND)):
            return
        self.hits.append(str(target))

    def __enter__(self) -> "OpenSpy":
        spy = self
        real_open = builtins.open
        real_path_open = pathlib.Path.open
        real_os_open = os.open
        real_read_text = pathlib.Path.read_text
        real_read_bytes = pathlib.Path.read_bytes

        def open_(file, mode="r", *a, **kw):
            spy._note(file, mode)
            return real_open(file, mode, *a, **kw)

        def path_open(self_, mode="r", *a, **kw):
            spy._note(self_, mode)
            return real_path_open(self_, mode, *a, **kw)

        def os_open(path, flags, *a, **kw):
            spy._note(path, flags)
            return real_os_open(path, flags, *a, **kw)

        def read_text(self_, *a, **kw):
            spy._note(self_, "r")
            return real_read_text(self_, *a, **kw)

        def read_bytes(self_):
            spy._note(self_, "rb")
            return real_read_bytes(self_)

        self._saved = [
            (builtins, "open", real_open),
            (pathlib.Path, "open", real_path_open),
            (os, "open", real_os_open),
            (pathlib.Path, "read_text", real_read_text),
            (pathlib.Path, "read_bytes", real_read_bytes),
        ]
        builtins.open = open_
        pathlib.Path.open = path_open
        os.open = os_open
        pathlib.Path.read_text = read_text
        pathlib.Path.read_bytes = read_bytes
        return self

    def __exit__(self, *_a) -> None:
        for holder, name, original in self._saved:
            setattr(holder, name, original)


with tempfile.TemporaryDirectory() as d:
    # D1 duro: la ruta de LECTURA no abre el audit. En Windows leer events.jsonl hace fallar
    # el os.replace del writer (PermissionError contra un handle abierto), medido 776/776 en
    # saturacion por la sesion anterior. Anti-vacuidad: el jsonl tiene que existir y no estar
    # vacio en el momento de la medida.
    try:
        gen = fresh_generation("H2f")
        paths = _paths(Path(d))
        paths.audit_dir.mkdir(parents=True, exist_ok=True)
        writer = JsonlAuditWriter(paths, gen)
        life, _s, guard, _p, token = build(Path(d), generation=gen, audit=writer)
        life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        tam = writer.current_path.stat().st_size if writer.current_path.exists() else 0
        spy = OpenSpy(paths.audit_dir)
        with spy:
            life.status(IDENTITY)
            life.box_occupancy()
        medidas = list(spy.hits)
        # CONTROL POSITIVO: el mismo instrumento tiene que VER una lectura deliberada. Sin
        # el, "cero aperturas" podria ser un falso negativo del propio espia.
        control = OpenSpy(paths.audit_dir)
        with control:
            writer.current_path.read_text(encoding="utf-8")
            with open(writer.current_path, "r", encoding="utf-8") as handle:
                handle.read(1)
        if tam <= 0:
            unmet(
                "[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez",
                f"events.jsonl vacio o ausente ({tam} bytes): no habia nada que abrir, "
                f"asi que la ausencia de aperturas no mide nada",
            )
        elif not control.hits:
            unmet(
                "[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez",
                "el control positivo no vio una lectura deliberada de events.jsonl: el "
                "espia de aperturas no discrimina y su cero no significa nada",
            )
        else:
            check(
                "[GUARDA] H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez",
                not medidas,
                f"aperturas bajo {paths.audit_dir}: {medidas[:4]} "
                f"(events.jsonl={tam} bytes; control positivo vio {len(control.hits)} "
                f"aperturas) -- leer el jsonl desde la ruta de lectura rompe la escritura "
                f"del writer en Windows; D1 lo prohibe",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H2-LECTURA-NO-ABRE-EVENTS-JSONL status/box no leen el audit ni una vez",
            f"{type(exc).__name__}: {exc}",
        )


# =========================================================================================
# H3 -- el sobre estructurado de execute_dayz_test_stop
# =========================================================================================
GEN_SNAP_OLD = fresh_generation("snapOld")
GEN_SNAP_NEW = fresh_generation("snapNew")
# Terna DELIBERADAMENTE incoherente: cualquier recomputo daria generation_changed=True.
# Copiar es la unica forma de devolver False aqui.
INCOHERENTE = {
    GEN_LAUNCH: GEN_SNAP_OLD,
    GEN_CURRENT: GEN_SNAP_NEW,
    GEN_CHANGED: False,
}

try:
    fila = {**TERMINAL_ROW, **INCOHERENTE}
    kind, payload, runtime = run_stop({"runs": [fila]})
    if kind == "raise" and not isinstance(payload, dayz_test_tool.DayzTestToolError):
        unmet(
            "H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado",
            f"el fixture no llego a resolve_stop_run: {type(payload).__name__}: {payload}",
        )
    else:
        ok, motivo = envelope_ok(payload, "run_not_active", fila)
        check(
            "H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado",
            ok,
            f"{motivo or 'ok'} | devuelto={payload if kind == 'dict' else repr(payload)} "
            f"-- hoy resolve_stop_run levanta DayzTestToolError('run_not_active') y el "
            f"llamador se queda sin run_id ni generacion",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H3-RUN-NOT-ACTIVE-SOBRE run presente y terminal devuelve el sobre estructurado",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Mutante de la ficha: "recomputar generation_changed".
    fila = {**TERMINAL_ROW, **INCOHERENTE}
    kind, payload, _rt = run_stop({"runs": [fila]})
    if kind != "dict":
        unmet(
            "H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan",
            f"no hay sobre que inspeccionar: {type(payload).__name__}: "
            f"{getattr(payload, 'code', payload)}. El sujeto todavia no existe.",
        )
    else:
        copiado = payload.get(GEN_CHANGED)
        check(
            "H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan",
            copiado is False
            and payload.get(GEN_LAUNCH) == GEN_SNAP_OLD
            and payload.get(GEN_CURRENT) == GEN_SNAP_NEW,
            f"snapshot={INCOHERENTE} sobre={ {k: payload.get(k) for k in GEN_FIELDS} } -- "
            f"la terna del snapshot es incoherente a proposito: at_launch != current con "
            f"changed=False. True significa que se recomputo contra el daemon vivo en vez "
            f"de copiar el diagnostico de M17",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H3-MUT-RECOMPUTA-GENERATION-CHANGED los tres campos se COPIAN, no se recalculan",
        f"{type(exc).__name__}: {exc}",
    )

DIAG_UNICO = {
    "run_id": RUN_UUID,
    "event": "run_reaped",
    "reason": "all_processes_gone_or_foreign",
    "decision": "reaped",
    "state": "EXITED",
    **INCOHERENTE,
}

try:
    kind, payload, _rt = run_stop({"runs": [], DIAG_KEY: [DIAG_UNICO]})
    ok, motivo = envelope_ok(payload, "run_not_found", DIAG_UNICO)
    check(
        "H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre",
        ok,
        f"{motivo or 'ok'} | devuelto={payload if kind == 'dict' else repr(payload)} "
        f"-- los tres campos salen del diagnostico, no de un recomputo",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H3-RUN-NOT-FOUND-SOBRE-CON-DIAG run podado con UN diagnostico exacto da el sobre",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Mutante de la ficha: "convertir un run podado en run_not_active". El limite de
    # EXISTENCIA no se puede mover: el run ya no esta, y decir "no activo" invita a
    # reintentar un stop sobre algo que no existe.
    kind, payload, _rt = run_stop({"runs": [], DIAG_KEY: [DIAG_UNICO]})
    codigo = payload.get("error_code") if kind == "dict" else getattr(payload, "code", None)
    check(
        "[GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active",
        codigo == "run_not_found",
        f"codigo={codigo!r} (kind={kind}) -- run_not_active afirma presencia; el run no "
        f"esta en runs y solo lo acredita un diagnostico retirado",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-MUT-PODADO-NO-ES-NOT-ACTIVE un run ausente nunca se publica como run_not_active",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Fail-closed pareado: UUID nunca visto. Ni sobre, ni campos fabricados.
    kind, payload, _rt = run_stop({"runs": [], DIAG_KEY: []})
    texto = f"{payload!r} {getattr(payload, 'args', ())!r}"
    inventados = [
        f for f in GEN_FIELDS if hasattr(payload, f) or f in texto
    ]
    check(
        "[GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO sin evidencia se levanta run_not_found pelado",
        kind == "raise"
        and isinstance(payload, dayz_test_tool.DayzTestToolError)
        and getattr(payload, "code", None) == "run_not_found"
        and not inventados,
        f"kind={kind} tipo={type(payload).__name__} "
        f"codigo={getattr(payload, 'code', None)!r} campos_inventados={inventados} -- un "
        f"sobre aqui seria historia inventada para un UUID del que no hay ninguna evidencia",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-FAIL-CLOSED-UUID-NUNCA-VISTO sin evidencia se levanta run_not_found pelado",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Fail-closed pareado: diagnostico AMBIGUO (dos para el mismo run). "Un unico
    # retired_run_diagnostic exacto" es la condicion; dos no son uno.
    ambiguo = [DIAG_UNICO, {**DIAG_UNICO, "event": "lifecycle_stop_outcome",
                            "decision": "stopped", "reason": "stopped"}]
    kind, payload, _rt = run_stop({"runs": [], DIAG_KEY: ambiguo})
    check(
        "[GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO dos diagnosticos del mismo run no acreditan nada",
        kind == "raise"
        and isinstance(payload, dayz_test_tool.DayzTestToolError)
        and getattr(payload, "code", None) == "run_not_found",
        f"kind={kind} devuelto={payload if kind == 'dict' else repr(payload)} -- con dos "
        f"diagnosticos contradictorios no hay uno exacto: elegir cualquiera de ellos es "
        f"inventar cual fue la retirada real",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-FAIL-CLOSED-DIAG-AMBIGUO dos diagnosticos del mismo run no acreditan nada",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # [GUARDA] Un unico snapshot pre-dispatch. Verde hoy: resolve_stop_run consume la unica
    # lectura previa (dayz_test_tool.py:787-789) y la relectura vive DESPUES del despacho.
    kind, payload, runtime = run_stop({"runs": [ACTIVE_ROW]})
    if runtime.calls_at_dispatch is None:
        unmet(
            "[GUARDA] H3-UN-SOLO-SNAPSHOT-PRE-DISPATCH exactamente una lectura antes de despachar",
            f"no se llego a despachar: kind={kind} "
            f"{payload if kind == 'dict' else repr(payload)}",
        )
    else:
        check(
            "[GUARDA] H3-UN-SOLO-SNAPSHOT-PRE-DISPATCH exactamente una lectura antes de despachar",
            runtime.calls_at_dispatch == 1,
            f"lecturas de lifecycle_status antes del despacho={runtime.calls_at_dispatch} "
            f"(total={runtime.lifecycle_calls}) -- dos fotos pre-dispatch permiten clasificar "
            f"con una y ejecutar con otra",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-UN-SOLO-SNAPSHOT-PRE-DISPATCH exactamente una lectura antes de despachar",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # [GUARDA] Stop sobre run ACTIVO sigue igual que hoy.
    kind, payload, _rt = run_stop({"runs": [ACTIVE_ROW]})
    check(
        "[GUARDA] H3-STOP-ACTIVO-SIGUE-IGUAL el camino feliz no cambia",
        kind == "dict"
        and payload.get("status") == "succeeded"
        and payload.get("mode") == "stop"
        and payload.get("project") == PROJECT_MOD
        and payload.get("artifacts_paths") == [PROFILES],
        f"devuelto={payload if kind == 'dict' else repr(payload)} -- el sobre nuevo es para "
        f"los casos que hoy levantan; el stop que funciona no se toca",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-STOP-ACTIVO-SIGUE-IGUAL el camino feliz no cambia",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # [GUARDA] La relectura POST-ejecucion que reconcilia run_stop_failed sigue separada.
    exited = {**ACTIVE_ROW, "state": "EXITED", "launch_acknowledged": True}
    kind, payload, runtime = run_stop(
        None,
        sequence=[{"runs": [ACTIVE_ROW]}, {"runs": [exited]}],
        error_code="run_stop_failed",
        exit_code=2,
        ok=False,
        degraded=True,
    )
    if kind != "dict":
        unmet(
            "[GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA run_stop_failed se sigue reconciliando",
            f"no hubo resultado que reconciliar: {type(payload).__name__}: "
            f"{getattr(payload, 'code', payload)}",
        )
    else:
        check(
            "[GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA run_stop_failed se sigue reconciliando",
            payload.get("status") == "succeeded"
            and payload.get("error_code") is None
            and runtime.lifecycle_calls >= 2,
            f"status={payload.get('status')!r} error_code={payload.get('error_code')!r} "
            f"lecturas={runtime.lifecycle_calls} -- fusionar esa relectura con el snapshot "
            f"pre-dispatch dejaria un stop exitoso publicado como fallido",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H3-RELECTURA-POST-EJECUCION-INTACTA run_stop_failed se sigue reconciliando",
        f"{type(exc).__name__}: {exc}",
    )

with tempfile.TemporaryDirectory() as d:
    # El check que cruza M17 y M19: el status REAL del lifecycle alimenta la fachada. Los dos
    # bloques pueden estar bien por separado y no encajar (nombres, forma, tipos).
    try:
        gen = fresh_generation("H3x")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        real = life.status(IDENTITY)
        hits = diag_for(real, RUN_UUID)
        if row_for(real, RUN_UUID) is not None or len(hits) != 1:
            unmet(
                "H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre",
                f"el fixture no dejo el run ausente con UN diagnostico: "
                f"presente={row_for(real, RUN_UUID) is not None} diagnosticos={len(hits)}. "
                f"Sin esa evidencia no se puede medir el encaje M17 -> M19.",
            )
        else:
            kind, payload, _rt = run_stop(real)
            ok, motivo = envelope_ok(payload, "run_not_found", hits[0])
            check(
                "H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre",
                ok,
                f"{motivo or 'ok'} | diagnostico_real={hits[0]!r} "
                f"devuelto={payload if kind == 'dict' else repr(payload)}",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H3-INTEGRACION-STATUS-REAL el status del lifecycle alimenta el sobre",
            f"{type(exc).__name__}: {exc}",
        )


# =========================================================================================
# H4 -- transporte por la tool publica dayz_test_stop
# =========================================================================================
def client_runtime(config: ServerConfig):
    """A client runtime that never consults live host registrations. Copied in shape from the
    tree's own fixture; owned here so the gate does not import tools/tests."""
    with tempfile.TemporaryDirectory() as directory:
        keyfile = Path(directory) / "daemon.key"
        keyfile.write_text(config.key or "fixture-key", encoding="utf-8")
        fixture = replace(config, keyfile=str(keyfile.resolve()))
        launcher = str(Path(sys.executable).resolve())
        provenance = host_config.DaemonProvenance(
            launch_executable=launcher,
            native_executable=launcher,
            argv=tuple(server_module.daemon.build_daemon_argv(fixture, python=launcher)),
            cwd=server_module.daemon.daemon_runtime_cwd(),
            port=fixture.port,
            keyfile=str(keyfile.resolve()),
            auto_spawn_daemon=fixture.auto_spawn_daemon,
        )
        with (
            patch.object(host_config, "resolve_daemon_provenance", return_value=provenance),
            patch.object(host_config, "_local_launch_executable", return_value=launcher),
            patch.object(host_config, "_local_native_executable", return_value=launcher),
        ):
            return server_module.ClientRuntime(fixture, **{})


def unwrap(content: object) -> object:
    if isinstance(content, tuple):
        _blocks, structured = content
        if isinstance(structured, dict):
            return structured
        content = _blocks
    try:
        return json.loads(content[0].text)  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return content


def call_public_stop(status_payload):
    """Drive the REAL execute_dayz_test_stop through the public FastMCP tool, against the
    oracle's own runtime. Only the runtime is substituted; the tool body and the whole
    server-side translation are the shipped ones."""
    config = ServerConfig(
        mode="client", key="k" * 32, port=12345, client_platform="codex",
        log_sink=lambda _m: None,
    )
    built = client_runtime(config)
    with patch.object(server_module, "ClientRuntime", return_value=built):
        app, _rt = build_app(config)
    fake = _Runtime(status_payload)
    real = dayz_test_tool.execute_dayz_test_stop

    async def passthrough(_client, run_id, **_kw):
        return await real(fake, run_id)

    a, b, c = tool_patches(launcher_ok(fake))

    async def _go():
        with a, b, c, patch.object(
            server_module.dayz_test_tool, "execute_dayz_test_stop", side_effect=passthrough
        ):
            return await app.call_tool("dayz_test_stop", {"run_id": RUN_UUID})

    try:
        return "ok", unwrap(asyncio.run(_go()))
    except BaseException as exc:  # noqa: BLE001
        return "raise", exc


try:
    fila = {**TERMINAL_ROW, **INCOHERENTE}
    kind, payload = call_public_stop({"runs": [fila]})
    ok, motivo = envelope_ok(payload, "run_not_active", fila) if kind == "ok" else (
        False, f"la tool levanto {type(payload).__name__}: {payload}"
    )
    check(
        "H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro",
        ok,
        f"{motivo or 'ok'} -- hoy DayzTestToolError se traduce a ToolError "
        f"(server.py:3054-3055) y el sobre no llega nunca al cliente",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H4-TOOL-TRANSPORTA-EL-SOBRE dayz_test_stop entrega el dict integro",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Fail-closed pareado en la tool publica.
    kind, payload = call_public_stop({"runs": [], DIAG_KEY: []})
    texto = str(payload)
    inventados = [f for f in GEN_FIELDS if f in texto]
    check(
        "[GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado",
        kind == "raise"
        and type(payload).__name__ == "ToolError"
        and "run_not_found" in texto
        and not inventados,
        f"kind={kind} tipo={type(payload).__name__} texto={texto[:160]!r} "
        f"campos_inventados={inventados}",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "[GUARDA] H4-TOOL-FAIL-CLOSED-SIN-CAMPOS el UUID desconocido cruza como error pelado",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # retired_run_diagnostics viaja SOLO dentro del status publico. El sobre de stop lleva
    # el run que se pidio, no el historial de todos los demas.
    otros = [
        {**DIAG_UNICO, "run_id": OTHER_UUID},
        DIAG_UNICO,
    ]
    kind, payload = call_public_stop({"runs": [], DIAG_KEY: otros})
    if kind != "ok" or not isinstance(payload, dict):
        unmet(
            "H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero",
            f"no hay sobre que inspeccionar: {type(payload).__name__}: {payload}. El "
            f"sujeto todavia no existe.",
        )
    else:
        fuga = [
            key for key, value in payload.items()
            if key == DIAG_KEY
            or (isinstance(value, list) and any(
                isinstance(v, dict) and v.get("run_id") == OTHER_UUID for v in value
            ))
        ]
        check(
            "H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero",
            not fuga,
            f"claves que arrastran el anillo={fuga} sobre={sorted(payload)} -- el anillo "
            f"lleva hasta 32 runs ajenos; su sitio es el status publico",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H4-DIAGNOSTICOS-SOLO-EN-STATUS el sobre de stop no arrastra el anillo entero",
        f"{type(exc).__name__}: {exc}",
    )


# =========================================================================================
# H5 -- distinguir un lifecycle_stop_outcome exitoso de todo lo demas
# =========================================================================================
with tempfile.TemporaryDirectory() as d:
    try:
        gen = fresh_generation("H5a")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        hits = diag_for(life.status(IDENTITY), RUN_UUID)
        if not hits:
            unmet(
                "H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED",
                "no hay diagnostico que inspeccionar; el sujeto todavia no existe",
            )
        else:
            entry = hits[0]
            check(
                "H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED",
                entry.get("event") == "lifecycle_stop_outcome"
                and entry.get("reason") == "stopped"
                and entry.get("decision") == "stopped"
                and entry.get("state") == "EXITED",
                f"diagnostico={entry!r} -- la forma exacta de la terna es la unica que "
                f"acredita terminacion; lo demas es reconciliacion o limpieza pendiente",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H5-STOP-EXITOSO-SE-DISTINGUE el exito lleva stopped/stopped/EXITED",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    try:
        gen = fresh_generation("H5b")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        reaped = life.reap_dead_runs()
        reload_manifest(life, paths)
        hits = diag_for(life.status(IDENTITY), RUN_UUID)
        if RUN_UUID not in reaped or not hits:
            unmet(
                "H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop",
                f"el fixture no reapeo o no dejo diagnostico: reaped={reaped} "
                f"diagnosticos={len(hits)}",
            )
        else:
            entry = hits[0]
            check(
                "H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop",
                entry.get("event") == "run_reaped"
                and entry.get("decision") == "reaped"
                and entry.get("decision") != "stopped"
                and guard.terminated == [],
                f"diagnostico={entry!r} terminate_llamado_sobre={guard.terminated} -- el "
                f"reaper no termina nada por construccion; publicarlo como stopped afirmaria "
                f"una terminacion que nunca ocurrio",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H5-REAP-ACREDITA-SIN-TERMINAR run_reaped no se confunde con un stop",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Fail-closed pareado de H5: un stop que NO termina deja el run UNRECONCILED, no retirado.
    # Ni diagnostico de retirada, ni decision "stopped".
    try:
        gen = fresh_generation("H5c")
        life, sink, guard, paths, token = build(Path(d), generation=gen)
        guard.snapshots[9001] = {"error": "guard_unavailable", "exit_code": 3}
        salida = life.stop_run(IDENTITY, token, RUN_UUID)
        reload_manifest(life, paths)
        st = life.status(IDENTITY)
        fila = row_for(st, RUN_UUID)
        estado = fila.get("state") if isinstance(fila, dict) else None
        if estado != "UNRECONCILED":
            unmet(
                "[GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira",
                f"el fixture no dejo el run UNRECONCILED: salida={salida} estado={estado!r}",
            )
        else:
            anillo = diagnostics(st)
            hits = diag_for(st, RUN_UUID)
            # Dos mitades. La presencia se mide HOY. La del anillo solo cuando el anillo
            # existe: sobre una lista inexistente "no contiene la retirada" seria vacuo.
            medido = (
                "presencia + anillo" if isinstance(anillo, list) else "solo presencia "
                "(el anillo aun no existe, esa mitad no se ha medido)"
            )
            check(
                "[GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira",
                estado == "UNRECONCILED"
                and hits == []
                and all(h.get("decision") != "stopped" for h in hits),
                f"medido={medido} estado={estado!r} anillo={type(anillo).__name__} "
                f"diagnosticos_del_run={hits!r} -- el run sigue presente: publicar una "
                f"retirada aqui daria por muerto un run que nadie ha conseguido parar",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "[GUARDA] H5-FAIL-CLOSED-NO-TERMINAL-NO-ES-STOPPED un stop fallido no se retira",
            f"{type(exc).__name__}: {exc}",
        )



# =========================================================================================
# RONDA 2 -- los cinco defectos reproducidos por la revision ciega de Codex, mas el sexto
# del otro revisor. Todos se miden sobre la entrega: el sujeto EXISTE y esta completo, asi
# que aqui un rojo es un rojo de producto, no de sujeto ausente.
# =========================================================================================

# El diagnostico completo, con los OCHO campos del contrato. Las variantes incompletas se
# derivan de EL, quitandole un campo cada vez: si el contrato crece, el barrido crece solo.
DIAG_COMPLETO = {
    "run_id": RUN_UUID,
    GEN_LAUNCH: GEN_SNAP_OLD,
    GEN_CURRENT: GEN_SNAP_NEW,
    GEN_CHANGED: False,
    "event": "run_reaped",
    "reason": "all_processes_gone_or_foreign",
    "decision": "reaped",
    "state": "EXITED",
}

def fuga_de(resultado, codigo_esperado: str) -> str | None:
    """Describe la fuga, o None si el rechazo fue el correcto.

    Rechazar bien no es "levantar algo": es levantar DayzTestToolError con el codigo real.
    Un KeyError de una implementacion que indexa sin comprobar cruza la tool publica como
    `dayz_test_failed:KeyError` (server.py:3058,3063-3064) y deja al cliente sin codigo.
    """
    kind, payload, _rt = resultado
    if kind == "dict":
        return f"sobre con { {k: payload.get(k) for k in GEN_FIELDS} }"
    if not isinstance(payload, dayz_test_tool.DayzTestToolError):
        return f"{type(payload).__name__}: {payload} (no es un rechazo tipado)"
    if getattr(payload, "code", None) != codigo_esperado:
        return f"codigo {getattr(payload, 'code', None)!r} (esperado {codigo_esperado!r})"
    return None


# -- H6: sin evidencia COMPLETA no hay sobre --------------------------------------------
try:
    control_kind, control_payload, _rt = run_stop(
        {"runs": [], DIAG_KEY: [dict(DIAG_COMPLETO)]}
    )
    if control_kind != "dict":
        unmet(
            "H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre",
            f"el control positivo no produjo sobre con el diagnostico COMPLETO "
            f"({type(control_payload).__name__}: {getattr(control_payload, 'code', control_payload)}): "
            f"sin el, 'no da sobre' seria cierto por vacuidad en todas las variantes",
        )
    else:
        fugas = []
        for campo in sorted(DIAG_COMPLETO):
            mutilado = {k: v for k, v in DIAG_COMPLETO.items() if k != campo}
            fuga = fuga_de(run_stop({"runs": [], DIAG_KEY: [mutilado]}), "run_not_found")
            if fuga:
                fugas.append(f"sin {campo!r} -> {fuga}")
        for estado in ("RUNNING", "RUNNING_IDLE", "STOPPING", "UNRECONCILED", ""):
            fuga = fuga_de(
                run_stop({"runs": [], DIAG_KEY: [{**DIAG_COMPLETO, "state": estado}]}),
                "run_not_found",
            )
            if fuga:
                fugas.append(f"state={estado!r} -> {fuga} (solo EXITED acredita retirada)")
        check(
            "H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre",
            not fugas,
            f"fugas={fugas[:4]} (total {len(fugas)}) | control positivo con el diagnostico "
            f"completo: {control_payload} -- un `.get()` sobre un diagnostico mutilado "
            f"convierte 'no lo se' en 'null', que es exactamente el campo fabricado que D4 "
            f"prohibe: el llamador no puede distinguirlo de una generacion desconocida real",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H6-DIAG-INCOMPLETO-NO-FABRICA un diagnostico al que le falta un campo no da sobre",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Misma helper (`_copy_generation`), otro camino: la fila presente-pero-terminal.
    fila_completa = {**TERMINAL_ROW, **INCOHERENTE}
    control_kind, control_payload, _rt = run_stop({"runs": [dict(fila_completa)]})
    if control_kind != "dict":
        unmet(
            "H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre",
            f"el control positivo no produjo sobre con la fila COMPLETA "
            f"({type(control_payload).__name__}: {getattr(control_payload, 'code', control_payload)})",
        )
    else:
        fugas = []
        for campo in GEN_FIELDS:
            mutilada = {k: v for k, v in fila_completa.items() if k != campo}
            # El run ESTA presente y no activo: la verdad sigue siendo run_not_active. Lo
            # que no puede es venir con generaciones que nadie acredito.
            fuga = fuga_de(run_stop({"runs": [mutilada]}), "run_not_active")
            if fuga:
                fugas.append(f"sin {campo!r} -> {fuga}")
        check(
            "H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre",
            not fugas,
            f"fugas={fugas[:3]} | control positivo: {control_payload} -- el sobre "
            f"run_not_active copia de la MISMA helper que el run_not_found; una fila sin "
            f"proyeccion de generacion (daemon viejo, status parcial) sale con tres null "
            f"inventados",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H6-FILA-INCOMPLETA-NO-FABRICA una fila sin las tres generaciones no da sobre",
        f"{type(exc).__name__}: {exc}",
    )


# -- H7: primero persistir, despues publicar --------------------------------------------
class ReplaceRoto:
    """Envuelve el metodo publico `manifest.replace`. Cuenta las llamadas, apunta si el
    anillo YA contenia el run en el instante de la llamada, y opcionalmente revienta."""

    def __init__(self, life, *, romper: bool) -> None:
        self.life = life
        self.romper = romper
        self.llamadas = 0
        self.anillo_al_persistir: list[list[str] | None] = []
        self._real = life.manifest.replace

    def __enter__(self) -> "ReplaceRoto":
        def _wrapped(run):
            self.llamadas += 1
            crudo = anillo_crudo(self.life)
            self.anillo_al_persistir.append(
                None if crudo is None else [e.get("run_id") for e in crudo]
            )
            if self.romper:
                raise OSError("disco lleno")
            return self._real(run)

        self.life.manifest.replace = _wrapped
        return self

    def __exit__(self, *_a) -> None:
        self.life.manifest.replace = self._real


def anillo_de(life) -> list | None:
    """La proyeccion PUBLICA del anillo. Puede filtrar a proposito (H9)."""
    raw = life.status(IDENTITY).get(DIAG_KEY)
    return raw if isinstance(raw, list) else None


def anillo_crudo(life) -> list | None:
    """El anillo EN SI, no su proyeccion. Sonda de caja blanca: `status()` suprime, por
    diseno, el diagnostico de un run cuya fila sigue no terminal -- que es exactamente el
    estado de un run cuya persistencia fallo. Medir el orden a traves de `status()` deja el
    instrumento ciego justo en el caso que se quiere ver. Devuelve None si el anillo no es
    alcanzable por este nombre, y entonces el check que lo use sale UNMET."""
    raw = getattr(life, "_retired_diagnostics", None)
    if raw is None:
        return None
    try:
        return [dataclasses.asdict(entry) for entry in list(raw)]
    except Exception:  # noqa: BLE001
        return None


def rellenar_anillo(life, guard, cuantos: int, *, prefijo: str = "fill"):
    """Deja `cuantos` diagnosticos REALES en el anillo, retirando runs sembrados. Devuelve
    (ids sembrados, ids efectivamente publicados) para que el llamador pueda guardarse de
    medir un desalojo sobre un anillo que nunca se lleno."""
    ids = [f"{prefijo}-{index:02d}" for index in range(cuantos)]
    for index, run_id in enumerate(ids):
        pid = 30000 + index
        record = ProcessRecord(
            pid, CREATED_UTC, HASH_A, HASH_B, "client", identity_scheme="psutil-argv-v2"
        )
        life.manifest.add(
            RunRecord(
                run_id, None, None, "RUNNING_IDLE", "gate", "@" + PROJECT_MOD, PROFILES,
                "test", [record],
            )
        )
        guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}
    life.reap_dead_runs()
    publicados = {
        e.get("run_id") for e in (anillo_de(life) or []) if isinstance(e, dict)
    }
    return ids, [run_id for run_id in ids if run_id in publicados]


def diag_de(life, run_id: str) -> list[dict]:
    return [
        e for e in (anillo_de(life) or []) if isinstance(e, dict) and e.get("run_id") == run_id
    ]


def fila_de(life, run_id: str) -> dict | None:
    return row_for(life.status(IDENTITY), run_id)


def sembrar_recovery(life, guard, *, run_id: str = RUN_UUID, pid: int = 9001):
    """Un run STARTING con lanzamiento sin reconocer y su proceso ya muerto: el sujeto de
    repair_recovery_fault. Devuelve el fault dict que la entrega acepta."""
    record = ProcessRecord(
        pid, CREATED_UTC, HASH_A, HASH_B, "client", identity_scheme="psutil-argv-v2"
    )
    life.manifest.add(
        RunRecord(
            run_id, "A", "lease-A", "STARTING", "gate", "@" + PROJECT_MOD, PROFILES,
            "test", [record], OTHER_UUID, HASH_A, False,
        )
    )
    guard.snapshots[pid] = {"error": "process_not_found", "exit_code": 4}
    return fault_de(life, run_id)


def fault_de(life, run_id: str) -> dict:
    run = life.manifest.get(run_id)
    return {
        "scope": "run",
        "run_id": run.run_id,
        "launch_operation_id": run.launch_operation_id,
        "run_record_sha256": hashlib.sha256(
            json.dumps(
                dataclasses.asdict(run), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def sembrar_reconcilable(life, *, run_id: str = RUN_UUID):
    life.manifest.add(
        RunRecord(
            run_id, None, None, "UNRECONCILED", "gate", "@" + PROJECT_MOD, PROFILES,
            "test", [],
        )
    )


REASON_SUCIO = r"C:\Users\alice\secret\events.jsonl @ 2026-09-04T05:00:00Z"


def camino_reap(tmp: Path, *, romper: bool, prefill: int = 0):
    life, _s, guard, paths, _t = build(tmp, generation=fresh_generation("reap"))
    # El relleno va ANTES de matar el proceso del run objetivo: si no, la pasada del
    # relleno se lo llevaria por delante y no quedaria retirada que romper.
    sembrados, publicados = rellenar_anillo(life, guard, prefill) if prefill else ([], [])
    guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
    with ReplaceRoto(life, romper=romper) as spy:
        life.reap_dead_runs()
    return life, spy, sembrados, publicados


def camino_repair(tmp: Path, *, romper: bool, prefill: int = 0):
    life, _s, guard, paths, _t = build(
        tmp, generation=fresh_generation("repair"), start=False
    )
    sembrados, publicados = rellenar_anillo(life, guard, prefill) if prefill else ([], [])
    fault = sembrar_recovery(life, guard)
    with ReplaceRoto(life, romper=romper) as spy:
        life.repair_recovery_fault(fault)
    return life, spy, sembrados, publicados


def camino_reconcile(tmp: Path, *, romper: bool, prefill: int = 0):
    life, _s, guard, paths, _t = build(
        tmp, generation=fresh_generation("reconcile"), start=False
    )
    sembrados, publicados = rellenar_anillo(life, guard, prefill) if prefill else ([], [])
    sembrar_reconcilable(life)
    with ReplaceRoto(life, romper=romper) as spy:
        life.admin_reconcile(RUN_UUID, None, "operator cleanup", empty=True)
    return life, spy, sembrados, publicados


def camino_stop(tmp: Path, *, romper: bool, prefill: int = 0):
    life, _s, guard, paths, token = build(tmp, generation=fresh_generation("stop"))
    sembrados, publicados = rellenar_anillo(life, guard, prefill) if prefill else ([], [])
    with ReplaceRoto(life, romper=romper) as spy:
        life.stop_run(IDENTITY, token, RUN_UUID)
    return life, spy, sembrados, publicados


CAMINOS = {
    "reap": camino_reap,
    "repair_recovery_fault": camino_repair,
    "admin_reconcile": camino_reconcile,
    "stop_run": camino_stop,
}

try:
    # El anillo se llena hasta el tope con retiradas REALES antes de romper la del run
    # objetivo. Asi la medida es por DESALOJO -- publica y no depende de si `status()`
    # filtra o no el diagnostico del propio run objetivo, que es justo el punto ciego.
    sanos, rotos, mudos = {}, {}, []
    for nombre, driver in CAMINOS.items():
        with tempfile.TemporaryDirectory() as d:
            life, spy, _sem, _pub = driver(Path(d), romper=False)
            sanos[nombre] = (len(diag_de(life, RUN_UUID)), spy.llamadas)
        with tempfile.TemporaryDirectory() as d:
            life, spy, sembrados, publicados = driver(
                Path(d), romper=True, prefill=DIAG_CAP
            )
            fila = fila_de(life, RUN_UUID)
            despues = {
                e.get("run_id") for e in (anillo_de(life) or []) if isinstance(e, dict)
            }
            rotos[nombre] = {
                "diag_del_objetivo": len(diag_de(life, RUN_UUID)),
                "replaces": spy.llamadas,
                "estado": fila.get("state") if isinstance(fila, dict) else None,
                "desalojados": sorted(set(publicados) - despues),
                "relleno": len(publicados),
            }
        # stop_run persiste DOS veces en el camino feliz (STOPPING y luego EXITED), asi
        # que la guarda pide "al menos una" llamada, no exactamente una. Lo que si es
        # exacto es el diagnostico: uno por retirada sana.
        if (
            sanos[nombre][0] != 1
            or sanos[nombre][1] < 1
            or rotos[nombre]["replaces"] < 1
            or rotos[nombre]["relleno"] != DIAG_CAP
        ):
            mudos.append(
                f"{nombre}: sano={sanos[nombre]} roto={rotos[nombre]} "
                f"(se espera sano=(1 diagnostico, >=1 replace), >=1 replace en el roto y "
                f"un relleno de {DIAG_CAP} diagnosticos reales publicados)"
            )
    if mudos:
        unmet(
            "H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion",
            f"el fixture no alcanzo el estado necesario en {len(mudos)} camino(s): "
            f"{mudos[:2]}. Sin una retirada sana que publique UNO, un replace que se llame "
            f"y un anillo lleno de historia real, el cero del caso roto no distingue "
            f"'no publica' de 'nunca publica' ni el desalojo se puede observar.",
        )
    else:
        malos = [
            f"{n}: desaloja {len(v['desalojados'])} diagnosticos reales "
            f"({v['desalojados'][:2]}), {v['diag_del_objetivo']} del objetivo, "
            f"run en {v['estado']!r}"
            for n, v in rotos.items()
            if v["desalojados"] or v["diag_del_objetivo"] != 0 or v["estado"] in (None, "EXITED")
        ]
        check(
            "H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion",
            not malos,
            f"caminos con fuga={malos} | sanos={sanos} -- publicar antes de persistir "
            f"acredita una retirada que el disco nunca acepto: el run sigue vivo en el "
            f"manifiesto Y la entrada fantasma desaloja historia real de un anillo de "
            f"{DIAG_CAP} posiciones",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H7-FALLO-DE-PERSISTENCIA-NO-PUBLICA un replace roto no consume una posicion",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # La invariante directa, sin inyectar fallo: en el instante de persistir, el anillo aun
    # no puede conocer el run. Cubre tambien los caminos donde inyectar seria artificioso.
    observado, sin_replace = {}, []
    for nombre, driver in CAMINOS.items():
        with tempfile.TemporaryDirectory() as d:
            life, spy, _sem, _pub = driver(Path(d), romper=False)
            if spy.llamadas == 0 or any(f is None for f in spy.anillo_al_persistir):
                sin_replace.append(nombre)
                continue
            observado[nombre] = [
                RUN_UUID in fotograma for fotograma in spy.anillo_al_persistir
            ]
    if sin_replace or not observado:
        unmet(
            "H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run",
            f"sin instante medible en {sin_replace or 'ningun camino'}: o no se llamo a "
            f"manifest.replace, o el anillo no es alcanzable por `_retired_diagnostics` "
            f"(esta sonda es de caja blanca; si el anillo cambia de nombre, este check no "
            f"mide y hay que reescribirlo, no darlo por bueno)",
        )
    else:
        pronto = [n for n, v in observado.items() if any(v)]
        check(
            "H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run",
            not pronto,
            f"caminos que publican ANTES de persistir={pronto} | por camino={observado} "
            f"-- stop_run ya lo hace bien: persiste y luego publica. Es la misma invariante "
            f"para los seis caminos que retiran runs",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H7-ORDEN-DIAGNOSTICO-TRAS-PERSISTIR al persistir, el anillo aun no conoce el run",
        f"{type(exc).__name__}: {exc}",
    )

try:
    # Pareja anti-sobrecorreccion: arreglar H7 borrando el diagnostico haria verde el
    # anterior y dejaria el lote sin producto. Una retirada SANA sigue publicando uno.
    publicados, estados = {}, {}
    for nombre, driver in CAMINOS.items():
        with tempfile.TemporaryDirectory() as d:
            life, _spy, _sem, _pub = driver(Path(d), romper=False)
            hits = diag_de(life, RUN_UUID)
            fila = fila_de(life, RUN_UUID)
            publicados[nombre] = len(hits)
            estados[nombre] = fila.get("state") if isinstance(fila, dict) else None
    check(
        "H7-PERSISTENCIA-OK-SI-PUBLICA una retirada sana sigue dejando su diagnostico",
        all(v == 1 for v in publicados.values())
        and all(v == "EXITED" for v in estados.values()),
        f"diagnosticos por camino={publicados} estado final={estados} -- si esto se pone "
        f"rojo, la correccion de H7 ha borrado el producto en vez de ordenarlo",
    )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H7-PERSISTENCIA-OK-SI-PUBLICA una retirada sana sigue dejando su diagnostico",
        f"{type(exc).__name__}: {exc}",
    )

with tempfile.TemporaryDirectory() as d:
    # Un reaper que falla al persistir corre cada 30 s. Si cada intento consume una posicion
    # del anillo, en poco mas de 15 min el anillo entero es el MISMO run fallido y toda la
    # historia real esta desalojada.
    try:
        pasadas = DIAG_CAP + 1
        life, _s, guard, paths, _t = build(Path(d), generation=fresh_generation("F6"))
        # Tres retiradas REALES primero. Si cada intento fallido gasta una posicion,
        # 3 + 33 > 32 y estas tres salen del anillo: el desalojo es la medida, y es publica.
        sembrados, publicados = rellenar_anillo(life, guard, 3, prefijo="real")
        guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        with ReplaceRoto(life, romper=True) as spy:
            for _ in range(pasadas):
                life.reap_dead_runs()
        despues = {
            e.get("run_id") for e in (anillo_de(life) or []) if isinstance(e, dict)
        }
        desalojados = sorted(set(publicados) - despues)
        fila = fila_de(life, RUN_UUID)
        if spy.llamadas != pasadas or len(publicados) != 3:
            unmet(
                "H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no desalojan historia",
                f"el fixture no ejercito las {pasadas} pasadas o no dejo historia real: "
                f"replaces={spy.llamadas} diagnosticos_reales_publicados={len(publicados)} "
                f"de 3. Sin un fallo por pasada y sin historia previa no hay desalojo que medir.",
            )
        else:
            check(
                "H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no desalojan historia",
                not desalojados
                and len(diag_de(life, RUN_UUID)) == 0
                and isinstance(fila, dict)
                and fila.get("state") != "EXITED",
                f"pasadas={spy.llamadas} desalojados={desalojados} de {publicados} "
                f"entradas_del_objetivo={len(diag_de(life, RUN_UUID))} "
                f"estado={fila.get('state') if isinstance(fila, dict) else None!r} -- el "
                f"reaper corre cada 30 s: con una entrada por intento fallido, en poco mas "
                f"de 15 min el anillo de {DIAG_CAP} posiciones es entero el MISMO run que "
                f"nunca se retiro, y la historia real esta desalojada",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H7-ANILLO-NO-SE-CONSUME-CON-FALLOS 33 retiradas fallidas no llenan el anillo",
            f"{type(exc).__name__}: {exc}",
        )


# -- H8: reparar dos veces el mismo run --------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, _s, guard, paths, _t = build(
            Path(d), generation=fresh_generation("H8"), start=False
        )
        fault = sembrar_recovery(life, guard)
        primera = life.repair_recovery_fault(fault)
        tras_primera = len(diag_de(life, RUN_UUID))
        estado_1 = life.manifest.get(RUN_UUID)
        # El fault se re-arma desde el registro ACTUAL, que es lo que hace `_arm_cleanup_fault`
        # cuando una limpieza posterior vuelve a fallar: el hash casa y los guards pasan.
        segunda = life.repair_recovery_fault(fault_de(life, RUN_UUID))
        tras_segunda = len(diag_de(life, RUN_UUID))
        if primera.get("terminal_safe") is not True or tras_primera != 1:
            unmet(
                "H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico",
                f"la primera reparacion no dejo el estado necesario: {primera} "
                f"diagnosticos={tras_primera}. Sin una primera reparacion buena no hay "
                f"repeticion que medir.",
            )
        else:
            check(
                "H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico",
                segunda.get("terminal_safe") is not True and tras_segunda == 1,
                f"primera={primera.get('terminal_safe')} segunda={segunda} "
                f"diagnosticos 1a->2a: {tras_primera}->{tras_segunda} "
                f"estado tras la 1a={getattr(estado_1, 'state', None)!r} -- el segundo "
                f"diagnostico duplica la cardinalidad y tumba el sobre de dayz_test_stop a "
                f"ToolError: un run perfectamente diagnosticable deja de serlo",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H8-REPARACION-REPETIDA-RECHAZADA reparar un run ya EXITED no anade otro diagnostico",
            f"{type(exc).__name__}: {exc}",
        )


# -- H9: la foto de status no se lee rasgada ---------------------------------------------
_TERMINAL_EN_RUNS = "EXITED"


def payload_rasgado(life, lector) -> tuple[dict, bool, str | None]:
    """Dispara UNA retirada completa entre la lectura del manifiesto y la del anillo,
    envolviendo el metodo publico `manifest.list_runs`. Sin hilos: la reentrada esta
    guardada, asi que la barrera es determinista."""
    real = life.manifest.list_runs
    estado = {"armado": True, "disparo": False, "previo": None}

    def _wrapped():
        filas = real()
        if estado["armado"]:
            estado["armado"] = False
            fila = next((r for r in filas if r.run_id == RUN_UUID), None)
            estado["previo"] = getattr(fila, "state", None)
            estado["disparo"] = True
            life.reap_dead_runs()
        return filas

    life.manifest.list_runs = _wrapped
    try:
        return lector(), estado["disparo"], estado["previo"]
    finally:
        life.manifest.list_runs = real


try:
    # Un lifecycle FRESCO por lector: tras la primera barrera el run ya esta EXITED y no
    # volveria a ser reapeable, asi que reutilizarlo dejaria la segunda ventana sin abrir.
    hallazgos, no_disparo = [], []
    for nombre in ("status", "public_status"):
        with tempfile.TemporaryDirectory() as d:
            life, _s, guard, paths, _t = build(
                Path(d), generation=fresh_generation("H9-" + nombre)
            )
            guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
            lector = (
                (lambda: life.status(IDENTITY))
                if nombre == "status"
                else (lambda: life.public_status())
            )
            payload, disparo, previo = payload_rasgado(life, lector)
            if not disparo or previo in (None, _TERMINAL_EN_RUNS):
                no_disparo.append(f"{nombre}: disparo={disparo} fila_previa={previo!r}")
                continue
            en_runs = {r.get("run_id"): r.get("state") for r in status_runs(payload)}
            en_anillo = {
                e.get("run_id")
                for e in (diagnostics(payload) or [])
                if isinstance(e, dict)
            }
            hallazgos.extend(
                f"{nombre}: {rid} en runs con state={st!r} y a la vez en el anillo"
                for rid, st in en_runs.items()
                if st != _TERMINAL_EN_RUNS and rid in en_anillo
            )
    if no_disparo:
        unmet(
            "H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico",
            f"la barrera no abrio la ventana en: {no_disparo}. Sin una fila no terminal "
            f"leida ANTES de la retirada, el payload no puede salir rasgado y la ausencia "
            f"de solape no mide nada.",
        )
    else:
        check(
            "H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico",
            not hallazgos,
            f"hallazgos={hallazgos} -- una fila RUNNING junto a un diagnostico EXITED del "
            f"mismo run solo puede salir de leer el manifiesto antes de la retirada y el "
            f"anillo despues; el lector no puede saber cual de las dos mitades es la vieja",
        )
except Exception as exc:  # noqa: BLE001
    unmet(
        "H9-STATUS-SIN-LECTURA-RASGADA ninguna fila no terminal convive con su diagnostico",
        f"{type(exc).__name__}: {exc}",
    )


with tempfile.TemporaryDirectory() as d:
    # [GUARDA] Control positivo de PRESERVACION. La convivencia fila EXITED + diagnostico es
    # por diseno: la ficha 16 (PASS 3.o) la exige para el run presente-pero-terminal. Arreglar
    # H9 filtrando "todo run que aparezca en runs" borraria ese caso.
    try:
        life, _s, _g, paths, token = build(Path(d), generation=fresh_generation("H9b"))
        parado = life.stop_run(IDENTITY, token, RUN_UUID)
        st = life.status(IDENTITY)
        fila = row_for(st, RUN_UUID)
        hits = diag_for(st, RUN_UUID)
        if not (isinstance(parado, dict) and parado.get("ok") is True):
            unmet(
                "[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA la fila EXITED y su diagnostico conviven",
                f"el stop no llego a EXITED: {parado}",
            )
        else:
            check(
                "[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA la fila EXITED y su diagnostico conviven",
                isinstance(fila, dict)
                and fila.get("state") == _TERMINAL_EN_RUNS
                and len(hits) == 1,
                f"fila={fila.get('state') if isinstance(fila, dict) else None!r} "
                f"diagnosticos={len(hits)} -- sin recargar, status() publica los EXITED: la "
                f"ficha 16 exige que el run presente-pero-terminal siga en runs Y que status "
                f"conserve su diagnostico. Esto NO es la lectura rasgada de H9.",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "[GUARDA] H9-CONVIVENCIA-EXITED-LEGITIMA la fila EXITED y su diagnostico conviven",
            f"{type(exc).__name__}: {exc}",
        )


# -- H10: el texto libre del operador no viaja en el diagnostico -------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, _s, _g, paths, _t = build(
            Path(d), generation=fresh_generation("H10"), start=False
        )
        sembrar_reconcilable(life)
        out = life.admin_reconcile(RUN_UUID, None, REASON_SUCIO, empty=True)
        hits = diag_de(life, RUN_UUID)
        # Control positivo del detector: la heuristica TIENE que marcar el reason crudo.
        control = huele_a_timestamp_o_path("reason", REASON_SUCIO)
        if not (isinstance(out, dict) and out.get("reconciled") is True) or not hits:
            unmet(
                "H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico",
                f"el fixture no retiro el run por admin_reconcile: {out} "
                f"diagnosticos={len(hits)}",
            )
        elif control is None:
            unmet(
                "H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico",
                f"el control positivo fallo: la heuristica no marca el propio reason "
                f"{REASON_SUCIO!r}, asi que su silencio sobre el diagnostico no significa nada",
            )
        else:
            olores = []
            for entry in hits:
                for key, value in entry.items():
                    olor = huele_a_timestamp_o_path(key, value)
                    if olor:
                        olores.append(olor)
            check(
                "H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico",
                not olores,
                f"olores={olores[:3]} diagnostico={hits[0]!r} (control positivo: "
                f"{control}) -- el reason es texto libre de un operador y el diagnostico es "
                f"publico: H2 prohibe timestamps y paths ahi, y este camino los cuela "
                f"enteros. El texto humano se queda en el audit.",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H10-ADMIN-REASON-NO-VIAJA el texto libre del operador no llega al diagnostico",
            f"{type(exc).__name__}: {exc}",
        )

with tempfile.TemporaryDirectory() as d:
    # Pareja anti-sobrecorreccion de H10: el codigo cerrado sigue acreditando QUE camino
    # retiro el run. Vaciar el reason haria verde el anterior y perderia el diagnostico.
    try:
        life, _s, _g, paths, _t = build(
            Path(d), generation=fresh_generation("H10b"), start=False
        )
        sembrar_reconcilable(life)
        life.admin_reconcile(RUN_UUID, None, REASON_SUCIO, empty=True)
        hits = diag_de(life, RUN_UUID)
        if len(hits) != 1:
            unmet(
                "H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino",
                f"no hay exactamente un diagnostico que inspeccionar: {len(hits)}",
            )
        else:
            entry = hits[0]
            razon = entry.get("reason")
            cerrado = (
                isinstance(razon, str)
                and 0 < len(razon) <= 64
                and razon == razon.strip()
                and all(c.isalnum() or c in "_-." for c in razon)
            )
            check(
                "H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino",
                entry.get("event") == "admin_reconcile"
                and entry.get("decision") == "confirmed"
                and entry.get("state") == "EXITED"
                and cerrado,
                f"diagnostico={entry!r} reason_es_codigo_cerrado={cerrado} -- la correccion "
                f"de H10 es sustituir el texto libre por un codigo cerrado (sin espacios, "
                f"<=64, alfanumerico + _-.), no borrar el diagnostico ni el camino que lo "
                f"produjo",
            )
    except Exception as exc:  # noqa: BLE001
        unmet(
            "H10-ADMIN-DIAGNOSTICO-SIGUE-ACREDITANDO el codigo cerrado nombra el camino",
            f"{type(exc).__name__}: {exc}",
        )

# =========================================================================================
print("=" * 104)
worst = 0
for nm, v, dt in RESULTS:
    print(f"[{v:<5}] {nm}")
    if dt:
        print(f"          {dt[:320]}")
    if v != "PASS":
        worst = 1
print("=" * 104)
# Censo: un bloque que muriera antes de llamar a check()/unmet(), o un nombre repetido,
# desaparecen del recuento sin dejar rastro. El veredicto se calcula DESPUES de esto.
EXPECTED_CHECKS = 40
nombres = [nm for nm, _v, _d in RESULTS]
duplicados = sorted({nm for nm in nombres if nombres.count(nm) > 1})
if len(RESULTS) != EXPECTED_CHECKS or duplicados:
    worst = 1
    print(
        f"ORACULO-CENSO ROTO: se esperaban {EXPECTED_CHECKS} checks y hay {len(RESULTS)}"
        + (f"; nombres repetidos: {duplicados}" if duplicados else "")
    )
else:
    print(f"ORACULO-CENSO OK: {len(RESULTS)} checks, ninguno repetido")
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
if worst == 0:
    print("ORACULO-VERDE")
sys.exit(worst)
