# -*- coding: utf-8 -*-
"""External oracle for lote G: ficha 16 (4d66), PASS blocks 1 and 2 -- activity.

Run:  cd <ws>/tools && PYTHONPATH=. <venv-python> ../gate/oracle.py

Load-bearing design notes:
- Self-contained fixtures. It imports NOTHING from tools/tests/, which the implementer may
  edit: a gate whose fixtures live in the candidate's write-set is an answer key. The fakes
  below are shaped like the shipped ones but owned here.
- The clock is injected through the shipped seam `box_occupancy(now=...)`, which also
  bypasses the cache (process_lifecycle.py:2425). The 899/901 boundary is therefore exact,
  not approximate.
- The writer fault is injected through the audit sink returning False, which is the shipped
  mechanism (`AuditSink.fail_events` in the tree's own tests).
- Every acceptance check is paired with a fail-closed check. The ficha names its own
  mutants; the ones it names are encoded here as checks, not as prose.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


from dayz_mcp.process_lifecycle import (  # noqa: E402
    ProcessLifecycle,
    ProcessRecord,
    RunManifestStore,
    RunRecord,
    _utc_epoch,
    occupancy_error_fields,
)

# `box_occupancy(now=...)` takes an ABSOLUTE epoch, not an offset: _run_age_s computes
# `now - min(stamps)` over the processes' creation_time_utc (process_lifecycle.py:171-179).
# Passing 899.0 makes that difference hugely negative and max(0.0, ...) flattens it to zero,
# so the whole threshold would have measured a constant. Measured during calibration; the
# shipped tests never drive this seam, so there was no precedent to copy.
CREATED_UTC = "2026-07-15T00:00:41.0000000Z"
# Un proceso ANTERIOR al daemon actual: sirve para preguntar si la generacion nueva
# importa continuidad que no le pertenece.
VIEJO_UTC = "2026-06-01T00:00:00.0000000Z"


def at(offset: float) -> float:
    """An absolute clock `offset` seconds after the run's processes were created."""
    base = _utc_epoch(CREATED_UTC)
    assert base is not None, "no se pudo derivar el epoch del run"
    return base + offset
from dayz_mcp.runtime_state import RuntimePaths  # noqa: E402
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator  # noqa: E402

IDENTITY = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")
HASH_A = "a" * 64
THRESHOLD = 900.0


class Sink:
    """Audit sink. Returning False is how the tree signals a writer fault."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.fail_events: set[str] = set()

    def __call__(self, event: dict) -> bool:
        self.events.append(event)
        return event.get("event") not in self.fail_events


class Guard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict] = {}

    def snapshot(self, pid: int) -> dict:
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: ProcessRecord) -> dict:
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


def build(tmp: Path, diag_probe=None):
    """A live ProcessLifecycle over a temp runtime root, with one started run."""
    game = tmp / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "DayZDiag_x64.exe").write_bytes(b"")
    paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                         tmp / "runtime" / "coordination.json",
                         tmp / "runtime" / "runs.json")
    sink = Sink()
    coord = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=sink)
    status, acquired = coord.acquire(IDENTITY, "lifecycle")
    assert status == 200, f"acquire devolvio {status}"
    lifecycle = ProcessLifecycle(
        coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=Guard(),
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=diag_probe or (lambda: {"known": True, "processes": []}),
        game_path=game, launcher=Launcher(), id_fn=lambda: "run-1",
        argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
    )
    # Without a guard snapshot for the launched pid, start_run fails closed with
    # identity_unavailable and the run lands EXITED -- the box then reports nothing and
    # every activity check would measure an empty list. Measured during calibration.
    guard = lifecycle.guard if hasattr(lifecycle, "guard") else None
    launched = ProcessRecord(9001, CREATED_UTC, HASH_A, "b" * 64,
                             "client", identity_scheme="psutil-argv-v2")
    snap = {
        "pid": launched.pid,
        "creation_time_utc": launched.creation_time_utc,
        "executable_sha256": launched.executable_sha256,
        "command_line_sha256": launched.command_line_sha256,
        "identity_scheme": launched.identity_scheme,
        "identity_complete": True,
    }
    if guard is not None:
        guard.snapshots[launched.pid] = snap
    request = {
        "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"], "cwd": str(game),
        "role": "client", "window_style": "normal", "label": "gate", "mod": "@SameMod",
        "profiles": "profiles", "mission": "test",
    }
    started = lifecycle.start_run(IDENTITY, acquired["lease_token"], request)
    assert started.get("ok") is True, f"start_run fallo: {started}"
    return lifecycle, sink, started


def run_row(box: dict) -> dict | None:
    runs = box.get("runs") if isinstance(box, dict) else None
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        return runs[0]
    return None


# ------------------------------------------------------------------ el umbral
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, started = build(Path(d))
        base = life.box_occupancy(now=at(0.0))
        row0 = run_row(base)
        if row0 is None:
            unmet("G1 la fila de box.runs es legible", f"box={str(base)[:160]}")
        else:
            for label, now, expected in (("899", at(899.0), "recent"), ("901", at(901.0), "stale")):
                row = run_row(life.box_occupancy(now=now))
                got = (row or {}).get("activity_state")
                check(f"G1-{label}s activity_state es {expected!r}", got == expected,
                      f"valor={got!r} fila={sorted(row or {})}")
            row = run_row(life.box_occupancy(now=at(899.0)))
            age = (row or {}).get("last_activity_age_s")
            check("G1-EDAD last_activity_age_s es un numero", isinstance(age, (int, float))
                  and not isinstance(age, bool), f"valor={age!r} tipo={type(age).__name__}")
    except Exception as exc:  # noqa: BLE001
        for nm in ("G1-899s activity_state es 'recent'", "G1-901s activity_state es 'stale'",
                   "G1-EDAD last_activity_age_s es un numero"):
            unmet(nm, f"{type(exc).__name__}: {exc}")

# --------------------------------------------- la proyeccion al error publico
# El mutante que la propia ficha nombra: dejar los campos SOLO en box.runs queda rojo.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, started = build(Path(d))
        box = life.box_occupancy(now=at(899.0))
        fields = occupancy_error_fields(box)
        for name in ("activity_state", "last_activity_age_s"):
            check(f"G2-{name} se proyecta a occupancy_error_fields", name in fields,
                  f"claves={sorted(fields)}")
        check("G2-age_s sigue en la proyeccion, sin regresion", "age_s" in fields,
              f"claves={sorted(fields)}")
        # Linea de preservacion: el sobre publico existente entero. Un cambio aditivo que
        # reordena o pierde una de estas claves rompe a un consumidor sin ruido.
        previas = {"occupied_by_run_id", "mod", "label", "age_s", "foreign", "hint"}
        faltan = sorted(previas - set(fields))
        # Por VALOR, no por presencia: las seis claves se inicializan en el literal de
        # `occupancy_error_fields`, asi que un mutante que salte el bucle de enriquecimiento
        # las deja todas presentes y vacias. Comprobar solo la presencia no lo veria.
        bien = (not faltan
                and fields.get("occupied_by_run_id") == "run-1"
                and fields.get("mod") == "@SameMod"
                and fields.get("label") == "gate"
                and isinstance(fields.get("age_s"), (int, float))
                and fields.get("age_s") > 0
                and fields.get("foreign") is False
                and isinstance(fields.get("hint"), str) and fields.get("hint"))
        check("G2-CONTROL las seis claves previas del error publico, con su VALOR", bien,
              f"faltan={faltan} run_id={fields.get('occupied_by_run_id')!r} "
              f"mod={fields.get('mod')!r} age_s={fields.get('age_s')!r} "
              f"hint_no_vacio={bool(fields.get('hint'))}")
        row = run_row(box) or {}
        previas_fila = {"run_id", "mod", "label", "age_s", "owner_session", "state"}
        faltan_fila = sorted(previas_fila - set(row))
        check("G2-CONTROL las seis claves previas de box.runs siguen", not faltan_fila,
              f"faltan={faltan_fila}")
        # PASS 1 de la ficha: un PID owned antiguo SIGUE ocupando. Si el trabajo de
        # actividad se cuela en la decision de ocupacion, un run viejo dejaria de ocupar
        # la caja y otro arranque pisaria una sesion viva.
        viejo = life.box_occupancy(now=at(100000.0))
        check("G2-CONTROL un PID owned antiguo sigue ocupando la caja",
              viejo.get("occupied") is True and bool(viejo.get("runs")),
              f"occupied={viejo.get('occupied')} runs={len(viejo.get('runs') or [])}")
    except Exception as exc:  # noqa: BLE001
        for nm in ("G2-activity_state se proyecta a occupancy_error_fields",
                   "G2-last_activity_age_s se proyecta a occupancy_error_fields",
                   "G2-age_s sigue en la proyeccion, sin regresion"):
            unmet(nm, f"{type(exc).__name__}: {exc}")

# ------------------------------------------------------- fail-closed: unknown
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, started = build(Path(d))
        # Un instante ANTERIOR al arranque es un timestamp futuro visto desde el run:
        # no se puede derivar una edad honesta, asi que la respuesta es unknown.
        row = run_row(life.box_occupancy(now=at(-10.0)))
        got = (row or {}).get("activity_state")
        check("G3-FUTURO un reloj anterior al arranque da unknown", got == "unknown",
              f"valor={got!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("G3-FUTURO un reloj anterior al arranque da unknown", f"{type(exc).__name__}: {exc}")

# -------------------------------------------------------------- sticky
# El corazon de PASS 2: con fallo de escritor en la MISMA generacion, la respuesta se queda
# en unknown aunque despues haya una escritura buena. Los tres mutantes que la ficha nombra
# -- "fallback al start", "success clears sticky" y "omitir el hook exec_enforce" -- mueren
# aqui si el sticky es real.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, started = build(Path(d))
        # 1. El escritor falla: el par (generacion, run) queda sticky-unknown.
        sink.fail_events.add("run_command_activity")
        rec = getattr(life, "record_command_activity", None)
        if rec is not None:
            rec("run-1", now=at(10.0))
        else:
            life.box_occupancy(now=at(10.0))
        # 2. El escritor vuelve, y hay una escritura BUENA. Esta mitad faltaba: sin ella el
        # check nunca ejercitaba "success clears sticky", que es el mutante que la ficha
        # nombra primero. Lo destapo ese mutante al escapar contra la entrega.
        sink.fail_events.discard("run_command_activity")
        escritura_buena = rec("run-1", now=at(20.0)) if rec is not None else None
        # 3. Pese a la escritura buena, la respuesta se queda en unknown.
        row = run_row(life.box_occupancy(now=at(901.0)))
        got = (row or {}).get("activity_state")
        check("G4-STICKY una escritura buena posterior NO limpia el sticky",
              got == "unknown",
              f"valor={got!r} escritura_buena={escritura_buena!r} "
              f"-- si dice 'stale', un exito posterior borro el fallo")
    except Exception as exc:  # noqa: BLE001
        unmet("G4-STICKY tras fallo de escritor sigue unknown pese a edad >900",
              f"{type(exc).__name__}: {exc}")


# ================================================== RONDA 3: EL SUSTRATO CAMBIA
# La ronda 2 hizo obligatorio LEER el events.jsonl en cada ocupacion. Eso trajo cinco
# defectos que son todos del LECTOR (carrera de os.replace en Windows, rotacion sin backups,
# procedencia falsificable, -Infinity, coste O(runs x lineas)). Decision del humano: la
# actividad deja de LEERSE del audit y vive en estado propio del daemon.
#
# Lo que hace solido el rediseno: actividad y sticky se indexan por (daemon_generation,
# run_id), y lo unico que destruye la memoria del daemon es un reinicio -- que cambia la
# generacion y ya obliga a unknown. La memoria es exactamente tan durable como hace falta.
# El audit SIGUE registrando el evento; deja de ser la fuente de verdad. Write-only.
import json  # noqa: E402
import time  # noqa: E402

from dayz_mcp import loopback  # noqa: E402
from dayz_mcp.runtime_state import JsonlAuditWriter  # noqa: E402


def build_real(tmp: Path, generation: str = "gen-A"):
    """build() con un JsonlAuditWriter de VERDAD: hay events.jsonl que inspeccionar."""
    game = tmp / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "DayZDiag_x64.exe").write_bytes(b"")
    paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                         tmp / "runtime" / "coordination.json",
                         tmp / "runtime" / "runs.json")
    paths.audit_dir.mkdir(parents=True, exist_ok=True)
    writer = JsonlAuditWriter(paths, generation)
    coord = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A",
                               audit=writer.write)
    status, acquired = coord.acquire(IDENTITY, "lifecycle")
    assert status == 200, f"acquire devolvio {status}"
    guard = Guard()
    lifecycle = ProcessLifecycle(
        coordinator=coord, manifest=RunManifestStore(paths), audit=writer.write, guard=guard,
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=lambda: {"known": True, "processes": []},
        game_path=game, launcher=Launcher(), id_fn=lambda: "run-1",
        argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        daemon_generation=generation,
    )
    guard.snapshots[9001] = {
        "pid": 9001, "creation_time_utc": CREATED_UTC, "executable_sha256": HASH_A,
        "command_line_sha256": "b" * 64, "identity_scheme": "psutil-argv-v2",
        "identity_complete": True,
    }
    started = lifecycle.start_run(IDENTITY, acquired["lease_token"], {
        "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"], "cwd": str(game),
        "role": "client", "window_style": "normal", "label": "gate", "mod": "@SameMod",
        "profiles": "profiles", "mission": "test",
    })
    assert started.get("ok") is True, f"start_run fallo: {started}"
    return lifecycle, writer, started


def eventos(writer) -> list:
    raw = writer.current_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in raw if line.strip()]


def start_epoch(writer) -> float:
    outs = [e for e in eventos(writer) if e.get("event") == "lifecycle_start_outcome"]
    assert outs, "el audit no trae lifecycle_start_outcome"
    epoch = _utc_epoch(outs[-1].get("timestamp_utc"))
    assert epoch is not None
    return epoch


# -- R1: el camino de LECTURA no escribe. Sobrevive de la ronda 2 y sigue mandando. -------
with tempfile.TemporaryDirectory() as d:
    try:
        life, writer, _ = build_real(Path(d))
        antes = len(eventos(writer))
        life.box_occupancy(now=start_epoch(writer) + 300.0)
        nuevos = eventos(writer)[antes:]
        check("R1-READONLY una lectura de box_occupancy no escribe en el audit",
              not nuevos,
              f"escribio {len(nuevos)}: {[e.get('reason') for e in nuevos]}")
    except Exception as exc:  # noqa: BLE001
        unmet("R1-READONLY una lectura de box_occupancy no escribe en el audit",
              f"{type(exc).__name__}: {exc}")

# -- R5: el rastro del audit SIGUE. Deja de ser autoridad, no deja de existir. ------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, writer, _ = build_real(Path(d))
        base = start_epoch(writer)
        rec = life.record_command_activity("run-1", now=base + 500.0)
        evs = [e for e in eventos(writer) if e.get("event") == "run_command_activity"]
        row = run_row(life.box_occupancy(now=base + 700.0)) or {}
        check("R5-RASTRO un comando aceptado sigue dejando UN evento en el audit",
              rec is True and len(evs) == 1 and row.get("activity_state") == "recent",
              f"rec={rec!r} eventos={len(evs)} estado={row.get('activity_state')!r} "
              f"-- el audit deja de ser la fuente de verdad, no deja de registrar")
    except Exception as exc:  # noqa: BLE001
        unmet("R5-RASTRO un comando aceptado sigue dejando UN evento en el audit",
              f"{type(exc).__name__}: {exc}")

# -- N4: EL SUSTRATO. Romper el audit no puede cambiar la respuesta. ----------------------
# Es la forma mas dura de preguntar "¿es el audit la fuente de verdad?": si corromperlo o
# borrarlo mueve la respuesta, lo es. No hace falta instrumentar ninguna apertura de fichero.
with tempfile.TemporaryDirectory() as d:
    try:
        life, writer, _ = build_real(Path(d))
        base = start_epoch(writer)
        sano = (run_row(life.box_occupancy(now=base + 100.0)) or {}).get("activity_state")
        writer.current_path.write_text("{basura no json\n" * 5, encoding="utf-8")
        corrupto = (run_row(life.box_occupancy(now=base + 100.0)) or {}).get("activity_state")
        writer.current_path.unlink()
        borrado = (run_row(life.box_occupancy(now=base + 100.0)) or {}).get("activity_state")
        check("N4-SUSTRATO corromper o borrar events.jsonl no cambia la actividad",
              # Ojo: NO se exige 'recent'. La basal en memoria es el arranque del proceso
              # y el reloj de este check sale del evento de start: dos anclas distintas, y
              # exigir 'recent' hacia el check IMPOSIBLE. Lo que importa es que no CAMBIE.
              # Y se exige que sano no sea ya 'unknown', o el check se satisface haciendo
              # desaparecer el sujeto.
              sano in ("recent", "stale") and corrupto == sano and borrado == sano,
              f"sano={sano!r} corrupto={corrupto!r} borrado={borrado!r} "
              f"-- si cambian, el audit sigue siendo la fuente de verdad y vuelve la carrera")
    except Exception as exc:  # noqa: BLE001
        unmet("N4-SUSTRATO corromper o borrar events.jsonl no cambia la actividad",
              f"{type(exc).__name__}: {exc}")

# --------------------------------------------------------------- RONDA 4
# Lo que cambia aqui, y por que: los checks de la ronda 3 llamaban a
# `record_box_command_activity` DIRECTAMENTE, asi que verificaban el registrador y no el
# CABLEADO enqueue -> binding -> registrador. Por ese hueco paso H-01: el enqueue conoce el
# `run_id` exacto del binding, lo descarta, y el destino se reconstruye por dueno o por
# cardinalidad. Los tests entregados tenian el mismo hueco por otra puerta: `bind_both_peers`
# ata `run_id="test-run"` y el test afirmaba que el evento salia para `run-1`, cosa que solo
# se cumplia porque habia UN run activo. Verde por el motivo equivocado.
INST_CLIENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
INST_SERVER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def dos_runs(tmp: Path):
    """Un lifecycle real con run-1 (dueno A) y run-2 (dueno B), los dos RUNNING.

    Ronda 9: con P6 estricto un run sin dueno no despacha nada, asi que run-2 tiene dueno --
    OTRO que run-1 -- para que la atribucion "al run del binding, no al del dueno que llama"
    siga siendo observable entre varios."""
    life, sink, _started = build(tmp)
    extra = ProcessRecord(9002, CREATED_UTC, HASH_A, "c" * 64, "server",
                          identity_scheme="psutil-argv-v2")
    life.manifest.add(RunRecord("run-2", "B", "lease-B", "RUNNING", "otro", "@Otro",
                                "profiles", "mission", [extra]))
    return life, sink


def estado_con(life, run_id_del_binding: str, **kw):
    """ServerState sin coordinacion (el enqueue entra directo) con el binding apuntando
    al run que se indique. El `run_id` del binding es la identidad EXACTA del comando."""
    state = loopback.ServerState("clave-gate", **kw)
    state.install_bound_peer(instance=INST_SERVER, role="server", pid=41001,
                             run_id=run_id_del_binding)
    state.install_bound_peer(instance=INST_CLIENT, role="client", pid=41002,
                             run_id=run_id_del_binding)
    state.lifecycle = life
    return state


def eventos_actividad(sink) -> list:
    return [e for e in sink.events if e.get("event") == "run_command_activity"]


# -- N1: la actividad se acredita al run del BINDING, no al del dueno --------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink = dos_runs(Path(d))
        state = estado_con(life, "run-2")          # el comando va dirigido a run-2
        antes = len(eventos_actividad(sink))
        st, payload = state.enqueue_command("camera_get", {}, peer="client")
        nuevos = eventos_actividad(sink)[antes:]
        destinos = [e.get("run_id") for e in nuevos]
        check("N1-ATRIBUCION la actividad se acredita al run del BINDING",
              st == 200 and destinos == ["run-2"],
              f"status={st} eventos={destinos} -- 'run-1' significa que el destino se "
              f"reconstruye por dueno/cardinalidad y el binding exacto se tira; "
              f"[] significa que un run sin dueno no registra nada")
    except Exception as exc:  # noqa: BLE001
        unmet("N1-ATRIBUCION la actividad se acredita al run del BINDING",
              f"{type(exc).__name__}: {exc}")

# -- N2: el enqueue INTERNO no acredita a NADIE ------------------------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink = dos_runs(Path(d))
        state = estado_con(life, "run-2")
        antes = len(eventos_actividad(sink))
        st, _payload = state.enqueue_command("vehicle_release", {}, peer="client",
                                             internal=True)
        nuevos = eventos_actividad(sink)[antes:]
        check("N2-INTERNO un enqueue interno (cleanup) no acredita a ningun run",
              st == 200 and not nuevos,
              f"status={st} eventos={[e.get('run_id') for e in nuevos]} -- el cleanup por "
              f"expiracion de lease sellaria como recien usado el run recien abandonado")
    except Exception as exc:  # noqa: BLE001
        unmet("N2-INTERNO un enqueue interno (cleanup) no acredita a ningun run",
              f"{type(exc).__name__}: {exc}")

# -- N3: la CACHE no publica una actividad que ya no es cierta ---------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        life.box_occupancy()
        sink.fail_events.add("run_command_activity")
        life.record_command_activity("run-1", now=time.time())
        cacheado = (run_row(life.box_occupancy()) or {}).get("activity_state")
        check("N3-CACHE tras un fallo de escritor la lectura cacheada no dice recent/stale",
              cacheado == "unknown",
              f"cacheado={cacheado!r} -- loopback llama al lector SIN `now`, la rama cacheada")
    except Exception as exc:  # noqa: BLE001
        unmet("N3-CACHE tras un fallo de escritor la lectura cacheada no dice recent/stale",
              f"{type(exc).__name__}: {exc}")

# -- N5: tras un reinicio, la clausula del contrato es ALCANZABLE -------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        life.daemon_generation = "gen-B"
        heredado = (run_row(life.box_occupancy(now=at(4000.0))) or {}).get("activity_state")
        # Ronda 9: sin dueno no se despacha nada (P6 estricto); el flujo real tras un reinicio
        # es ADOPTAR el run heredado y despues comandarlo.
        adoptado = life.adopt_run(IDENTITY, "token-A", "run-1")
        state = estado_con(life, "run-1")
        st_cmd, _ = state.enqueue_command("camera_get", {}, peer="client")
        despues = (run_row(life.box_occupancy(now=time.time())) or {}).get("activity_state")
        if not (isinstance(adoptado, dict) and adoptado.get("ok") is True):
            unmet("N5-REINICIO heredado es unknown y una actividad nueva lo lleva a recent",
                  f"el fixture no adopto el run heredado: adopt={adoptado!r}")
        else:
            check("N5-REINICIO heredado es unknown y una actividad nueva lo lleva a recent",
                  heredado == "unknown" and despues == "recent",
                  f"heredado={heredado!r} enqueue={st_cmd} tras_actividad={despues!r}")
    except Exception as exc:  # noqa: BLE001
        unmet("N5-REINICIO heredado es unknown y una actividad nueva lo lleva a recent",
              f"{type(exc).__name__}: {exc}")

# -- N6: la generacion nueva no importa un sello anterior al daemon -----------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        life.daemon_generation = "gen-B"
        actual = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        actual.processes.append(ProcessRecord(9003, VIEJO_UTC, HASH_A, "d" * 64, "server",
                                              identity_scheme="psutil-argv-v2"))
        life._capture_start_activity(actual)
        row = run_row(life.box_occupancy(now=at(6000.0))) or {}
        edad = row.get("last_activity_age_s")
        viejo = _utc_epoch(VIEJO_UTC)
        importado = (edad is not None
                     and abs((at(6000.0) - float(edad)) - viejo) < 2.0)
        check("N6-GENERACION extender un run heredado no importa un sello pre-daemon",
              not importado,
              f"estado={row.get('activity_state')!r} edad={edad!r} -- si la edad se mide "
              f"desde un proceso anterior a este daemon, la generacion nueva acredita una "
              f"continuidad que no tiene")
    except Exception as exc:  # noqa: BLE001
        unmet("N6-GENERACION extender un run heredado no importa un sello pre-daemon",
              f"{type(exc).__name__}: {exc}")

# -- N7: la basal del start invalida la cache --------------------------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        tmp = Path(d)
        game = tmp / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                             tmp / "runtime" / "coordination.json",
                             tmp / "runtime" / "runs.json")
        sink = Sink()
        coord = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A",
                                   audit=sink)
        st, acq = coord.acquire(IDENTITY, "lifecycle")
        guard = Guard()
        life = ProcessLifecycle(
            coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=guard,
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game, launcher=Launcher(), id_fn=lambda: "run-1",
            argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        )
        guard.snapshots[9001] = {
            "pid": 9001, "creation_time_utc": CREATED_UTC, "executable_sha256": HASH_A,
            "command_line_sha256": "b" * 64, "identity_scheme": "psutil-argv-v2",
            "identity_complete": True,
        }
        vacio = life.box_occupancy()           # siembra la cache con la caja VACIA
        started = life.start_run(IDENTITY, acq["lease_token"], {
            "argv": [str(game / "DayZDiag_x64.exe"), "-mission=test"], "cwd": str(game),
            "role": "client", "window_style": "normal", "label": "gate", "mod": "@SameMod",
            "profiles": "profiles", "mission": "test",
        })
        cacheado = life.box_occupancy()
        check("N7-CACHE-START tras start_run la lectura cacheada ve la caja ocupada",
              started.get("ok") is True and vacio.get("occupied") is False
              and cacheado.get("occupied") is True,
              f"start_ok={started.get('ok')} cache_previa={vacio.get('occupied')} "
              f"cacheado={cacheado.get('occupied')} -- publicar 'vacia' tras un arranque "
              f"invita a que otro arranque pise una sesion viva")
    except Exception as exc:  # noqa: BLE001
        unmet("N7-CACHE-START tras start_run la lectura cacheada ve la caja ocupada",
              f"{type(exc).__name__}: {exc}")

# -- N8: un lector lento no republica un snapshot anterior a la invalidacion --------------
with tempfile.TemporaryDirectory() as d:
    try:
        puerta = threading.Event()
        dentro = threading.Event()

        def _diag_lento():
            dentro.set()
            puerta.wait(5.0)
            return {"known": True, "processes": []}

        life, sink, _ = build(Path(d), diag_probe=_diag_lento)
        life.box_occupancy(now=at(10.0))
        def _lector():
            life.box_occupancy()               # sin `now`: publicara en la cache
        hilo = threading.Thread(target=_lector, daemon=True)
        hilo.start()
        dentro.wait(5.0)                       # ya calculo con el sello viejo
        sink.fail_events.add("run_command_activity")
        life.record_command_activity("run-1", now=time.time())   # sticky + invalidacion
        verdad = (run_row(life.box_occupancy(now=time.time())) or {}).get("activity_state")
        puerta.set()
        hilo.join(timeout=5)
        publicado = (run_row(life.box_occupancy()) or {}).get("activity_state")
        check("N8-CACHE-CARRERA un lector que calculo antes no publica tras la invalidacion",
              verdad == "unknown" and publicado == "unknown",
              f"verdad_directa={verdad!r} publicado_por_la_cache={publicado!r} -- si "
              f"difieren, el fallo de escritor no queda fail-closed hasta que caduque el TTL")
    except Exception as exc:  # noqa: BLE001
        unmet("N8-CACHE-CARRERA un lector que calculo antes no publica tras la invalidacion",
              f"{type(exc).__name__}: {exc}")



# --------------------------------------------------------------- RONDA 5
# La caja cacheada lleva TRES revisiones seguidas dando hallazgos nuevos, cada uno con su
# arreglo correcto: no invalidar al mutar (r2), no invalidar al sembrar la basal y pisar la
# invalidacion (r3), y devolver lo falso a quien pierde la carrera mas no invalidar al parar
# (r4). Decision del humano: dejar de cachear LA ACTIVIDAD. Se cachea lo caro -- sondas de
# proceso y argv -- y los dos campos de actividad se recalculan en cada lectura desde la
# memoria viva, que es una consulta a un diccionario. Eso disuelve la familia entera.

# -- N10 RETIRADO, y con su razon --------------------------------------------------------
# El hallazgo H1 del revisor (un start_run rechazado deja frescura en el run heredado que
# restaura) es real y esta reproducido POR EL, no por mi: mi fixture no alcanza
# `_capture_start_activity`, porque la ruta de verdad es adoptar-y-extender un run heredado
# y montar eso aqui cuesta mas de lo que el check acredita. Dejar aqui un UNMET perpetuo
# convertiria el gate en un imposible. Va al brief con su cita y lo prueba el implementador.

# -- N11: sin binding acreditado no se acredita frescura a nadie --------------------------
# Decision del orquestador, y va escrita: sin binding no hay prueba del destino. Acreditar
# por cardinalidad falsea a quien pertenece la frescura; no hacer nada publica un `stale`
# falso sobre una sesion viva, que es el P1 de la ronda 3. Lo unico fail-closed es `unknown`.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink = dos_runs(Path(d))
        state = loopback.ServerState("clave-gate")       # SIN install_bound_peer: cola legacy
        state.lifecycle = life
        antes = len(eventos_actividad(sink))
        st, _payload = state.enqueue_command("camera_get", {}, peer="client")
        nuevos = eventos_actividad(sink)[antes:]
        _box = life.box_occupancy(now=at(2000.0))
        _filas = {r.get("run_id"): r for r in (_box.get("runs") or [])}
        estados = {rid: (f or {}).get("activity_state") for rid, f in _filas.items()}
        check("N11-SIN-BINDING un enqueue sin binding no acredita frescura a nadie",
              st == 200 and all(v == "unknown" for v in estados.values()),
              f"status={st} eventos={[e.get('run_id') for e in nuevos]} estados={estados} "
              f"-- 'recent' en alguno significa que se acredito por cardinalidad, sin prueba "
              f"del destino; 'stale' significa que se dejo envejecer una sesion viva")
    except Exception as exc:  # noqa: BLE001
        unmet("N11-SIN-BINDING un enqueue sin binding no acredita frescura a nadie",
              f"{type(exc).__name__}: {exc}")

# -- N12: la edad no puede retroceder -----------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        life.record_command_activity("run-1", now=at(2000.0))   # la buena, mas reciente
        life.record_command_activity("run-1", now=at(1000.0))   # una tardia, mas vieja
        row = run_row(life.box_occupancy(now=at(2100.0))) or {}
        edad = row.get("last_activity_age_s")
        check("N12-MONOTONA una escritura tardia no hace retroceder la actividad",
              isinstance(edad, (int, float)) and abs(float(edad) - 100.0) < 1.0,
              f"edad={edad!r} (esperada ~100.0; 1100.0 significa que la escritura vieja "
              f"gano y la frescura retrocedio)")
    except Exception as exc:  # noqa: BLE001
        unmet("N12-MONOTONA una escritura tardia no hace retroceder la actividad",
              f"{type(exc).__name__}: {exc}")



# -- N13: la caja cacheada no publica un run que ya se paro --------------------------------
# El contador de revision lo introdujo ESTE lote como mecanismo de coherencia. Un mecanismo
# que no se incrementa en la mayoria de las mutaciones esta incompleto por construccion, y
# trazarlo a todos sus call-sites es trabajo de quien lo introdujo (R7). El reaper SI invalida
# y tiene test propio; stop, adopt, release, reconcile y repair no.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, started = build(Path(d))
        antes = life.box_occupancy()                    # siembra la cache con la caja OCUPADA
        parado = life.stop_run(IDENTITY, "token-A", "run-1")
        cacheado = life.box_occupancy()                 # dentro del TTL
        directo = life.box_occupancy(now=time.time())
        ok_stop = isinstance(parado, dict) and parado.get("ok") is True
        # Guarda anti-vacuidad: sin un stop de verdad este check no mide nada, y un rojo por
        # el motivo equivocado manda a arreglar la cache cuando lo roto era el andamio.
        if not ok_stop or antes.get("occupied") is not True or directo.get("occupied") is not False:
            unmet("N13-ESTRUCTURAL la caja cacheada no publica un run ya parado",
                  f"el fixture no dejo el estado necesario: stop_ok={ok_stop} "
                  f"antes={antes.get('occupied')} directo={directo.get('occupied')} "
                  f"(hacen falta True y False). No se ha medido la cache.")
        else:
            check("N13-ESTRUCTURAL la caja cacheada no publica un run ya parado",
                  cacheado.get("occupied") is False,
                  f"cacheado={cacheado.get('occupied')} directo={directo.get('occupied')} -- "
                  f"si cacheado sigue True, la sesion que acaba de parar su run ve su propia "
                  f"caja ocupada por un run EXITED, con el hint de pararlo otra vez")
    except Exception as exc:  # noqa: BLE001
        unmet("N13-ESTRUCTURAL la caja cacheada no publica un run ya parado",
              f"{type(exc).__name__}: {exc}")



# -- N9 RETIRADO, y con su razon ----------------------------------------------------------
# N9 exigia que quien pierde una carrera DEVUELVA el valor posterior a la mutacion. Esa regla
# no la cumple ningun diseno concurrente: entre el instante en que el lector fija su valor y
# el instante en que el cliente lo lee siempre cabe una escritura, asi que siempre existe una
# ventana mas tardia donde reproducir el fallo. La cuenta lo demuestra: la MISMA pregunta cayo
# en la ronda 3 (N3), en la 4 (N8), en la 5 (N9) y otra vez en la revision de la 5 (H1), cada
# vez por un ataque distinto y cada vez con su parche correcto. Cuatro ataques sobre una sola
# pregunta no es mala suerte con la metrica: es que la pregunta no es observable.
#
# La ronda 6 la sustituye por la que SI es alcanzable y es la que le importa a quien lee la
# caja: la respuesta no mezcla instantes (N14). Lo que N9 protegia de verdad -- que la CACHE
# no republique una actividad ya superada -- lo siguen midiendo N3 y N8, que son secuenciales
# y no dependen de ganar una carrera.

# -- N14: la caja publicada no MEZCLA dos instantes ---------------------------------------
# El corazon de la ronda 6. Hoy `_box_occupancy_uncached` construye las filas con el estado
# del manifiesto de T0, sondea argv y diag (E/S lenta, cientos de ms), y despues superpone la
# actividad de T2. La fila publicada es un collage: `state` de un instante y `activity_state`
# de otro. El contrato nuevo es que la respuesta sea un valor que fue cierto A LA VEZ.
#
# El oraculo no exige UN diseno: exige que la fila devuelta sea una de las dos fotos
# coherentes (la de antes de la mutacion o la de despues), nunca una tercera cosa.
# La barrera va en `argv_of`, que es un seam INYECTADO por constructor: sobrevive al refactor
# de las funciones internas, que es justo lo que esta ronda va a reescribir.
#
# La barrera no se ARMA hasta justo antes de lanzar el lector. Sin eso se la comia la lectura
# de siembra -- `argv_of` se llama en toda lectura no cacheada -- y la guarda anti-vacuidad
# daba por bueno el bloqueo del hilo equivocado.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}

        def _argv_lento(pid):
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return [str(Path(d) / "game" / "DayZDiag_x64.exe"), "-mission=test"]

        life, sink, _ = build(Path(d))
        life.argv_of = _argv_lento
        life.record_command_activity("run-1", now=time.time())   # sello fresco + invalida cache
        antes = run_row(life.box_occupancy()) or {}
        # La lectura de arriba ha vuelto a cachear: otra escritura la invalida para que el
        # lector de abajo entre de verdad por la ruta cara (si no, argv_of no se llama).
        life.record_command_activity("run-1", now=time.time())
        devuelto = {}

        def _lector():
            devuelto["box"] = life.box_occupancy()

        hilo = threading.Thread(target=_lector, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        # El lector tiene que seguir DENTRO cuando llega la mutacion; si ya ha vuelto, esto no
        # es una lectura concurrente y el check no mide nada.
        bloqueado = entro and quien.get("ident") == hilo.ident and "box" not in devuelto
        # Mutacion COMPLETA mientras el lector esta dentro: cambia el manifiesto Y la actividad.
        parado = life.stop_run(IDENTITY, "token-A", "run-1")
        sink.fail_events.add("run_command_activity")
        life.record_command_activity("run-1", now=time.time())    # sticky unknown
        puerta.set()
        hilo.join(timeout=5)
        fila = None
        for r in ((devuelto.get("box") or {}).get("runs") or []):
            if r.get("run_id") == "run-1":
                fila = r
        ok_fixture = (
            bloqueado
            and isinstance(parado, dict) and parado.get("ok") is True
            and antes.get("activity_state") == "recent"
            and "box" in devuelto
        )
        if not ok_fixture:
            # Guarda anti-vacuidad: sin lector bloqueado y sin stop de verdad este check no
            # ha ejercitado la lectura concurrente, y un verde aqui seria un verde vacio.
            unmet("N14-MEZCLA la caja publicada no mezcla dos instantes",
                  f"el fixture no dejo el estado necesario: lector_bloqueado={bloqueado} "
                  f"(entro={entro} hilo_correcto={quien.get('ident') == hilo.ident}) "
                  f"stop_ok={isinstance(parado, dict) and parado.get('ok')} "
                  f"antes={antes.get('activity_state')!r} (hace falta 'recent') "
                  f"lector_volvio={'box' in devuelto}. No se ha medido la coherencia.")
        else:
            coherente_antes = (
                fila is not None
                and fila.get("state") == "RUNNING"
                and fila.get("activity_state") == "recent"
            )
            coherente_despues = fila is None
            check("N14-MEZCLA la caja publicada no mezcla dos instantes",
                  coherente_antes or coherente_despues,
                  f"fila={fila!r} -- las dos fotos coherentes son "
                  f"(state=RUNNING, activity=recent) y (fila ausente). Una fila RUNNING con "
                  f"activity=unknown es un collage: el estado viene del instante en que "
                  f"empezo la lectura y la actividad del instante en que termino")
    except Exception as exc:  # noqa: BLE001
        unmet("N14-MEZCLA la caja publicada no mezcla dos instantes",
              f"{type(exc).__name__}: {exc}")

# -- N15: una observacion SIN binding mas VIEJA no borra un sello mas NUEVO ----------------
# Este hallazgo nace de una decision mia de la ronda 5: mande «sin binding => unknown» (P3) y
# la implementacion hace `pop()` sin mirar epocas, asi que choca con mi propio P4 monotono.
# La regla la anadi sin comprobar su interaccion con otra de la MISMA ronda.
# Secuencial y determinista: la ventana la abre el seam `now=`, no un hilo.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        t1 = time.time()
        life.record_command_activity("run-1", now=t1)             # sello ACREDITADO en t1
        sembrado = (run_row(life.box_occupancy(now=t1 + 10.0)) or {}).get("activity_state")
        life.record_box_command_activity(now=t1 - 60.0)           # observacion sin binding, t1-60
        despues = (run_row(life.box_occupancy(now=t1 + 10.0)) or {}).get("activity_state")
        if sembrado != "recent":
            unmet("N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo",
                  f"el fixture no sembro el sello: sembrado={sembrado!r} (hace falta 'recent')")
        else:
            check("N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo",
                  despues == "recent",
                  f"sembrado={sembrado!r} despues={despues!r} -- si cae a 'unknown', una "
                  f"observacion de hace un minuto ha borrado una actividad acreditada "
                  f"posterior, que es exactamente lo que P4 prohibe")
    except Exception as exc:  # noqa: BLE001
        unmet("N15-RETROCESO una observacion sin binding mas vieja no borra un sello mas nuevo",
              f"{type(exc).__name__}: {exc}")

# -- N16: y P3 sigue en pie -- la observacion sin binding POSTERIOR si manda ---------------
# Guarda de regresion, no hallazgo: el arreglo de N15 no puede consistir en quitar el olvido.
# Verde ANTES y verde DESPUES; su trabajo es que el parche no se pase de largo.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        t1 = time.time()
        life.record_command_activity("run-1", now=t1)
        sembrado = (run_row(life.box_occupancy(now=t1 + 10.0)) or {}).get("activity_state")
        life.record_box_command_activity(now=t1 + 30.0)           # sin binding, POSTERIOR
        despues = (run_row(life.box_occupancy(now=t1 + 40.0)) or {}).get("activity_state")
        if sembrado != "recent":
            unmet("N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown",
                  f"el fixture no sembro el sello: sembrado={sembrado!r}")
        else:
            check("N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown",
                  despues == "unknown",
                  f"sembrado={sembrado!r} despues={despues!r} -- si sigue 'recent', el "
                  f"arreglo de N15 ha desactivado P3 y volvemos a publicar frescura que "
                  f"nadie ha acreditado")
    except Exception as exc:  # noqa: BLE001
        unmet("N16-SIN-BINDING-MANDA una observacion sin binding posterior si publica unknown",
              f"{type(exc).__name__}: {exc}")

# -- N17: ningun binding despachable sobrevive a la salida durable de RUNNING -------------
# `stop_run` commitea EXITED en el manifiesto y DESPUES retira los bindings. En esa ventana el
# binding sigue BOUND sobre un run que ya esta durablemente muerto: el enqueue entra, deja un
# comando en una cola que la retirada tira a la basura, y acredita actividad a un run EXITED.
# El seam es `manifest.replace`, metodo publico de la clase enviada: se envuelve, no se parchea
# un interno. La accion se dispara EXACTAMENTE en la ventana, sin hilos.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        visto = {}
        real_replace = life.manifest.replace

        def _replace_espia(run):
            salida = real_replace(run)
            if getattr(run, "state", None) == "EXITED" and "st" not in visto:
                # Durable EXITED ya commiteado; la retirada del binding aun no ha ocurrido.
                visto["st"], visto["payload"] = state.enqueue_command(
                    "camera_get", {}, peer="client"
                )
            return salida

        life.manifest.replace = _replace_espia
        parado = life.stop_run(IDENTITY, "token-A", "run-1")
        life.manifest.replace = real_replace
        if "st" not in visto or not (isinstance(parado, dict) and parado.get("ok") is True):
            unmet("N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza",
                  f"el fixture no alcanzo la ventana: espia_disparo={'st' in visto} "
                  f"stop_ok={isinstance(parado, dict) and parado.get('ok')}. No se ha medido "
                  f"el ciclo de vida del binding.")
        else:
            check("N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza",
                  visto["st"] != 200,
                  f"status={visto['st']} payload={visto.get('payload')!r} -- 200 significa "
                  f"que se acepto un comando para un run que el manifiesto ya da por muerto: "
                  f"el binding correcto, pero con una vida mas larga que la del run que acredita")
    except Exception as exc:  # noqa: BLE001
        unmet("N17-BINDING-MUERE-CON-SU-RUN un enqueue tras el EXITED durable se rechaza",
              f"{type(exc).__name__}: {exc}")

# -- N18: la caja no CACHEA el manifiesto -------------------------------------------------
# Forma general de H5 y H6, y de la familia entera que lleva tres rondas dando hallazgos: el
# contador de revision como mecanismo de coherencia obliga a acertar en CADA mutacion, y basta
# olvidar una para publicar un estado durable viejo (la cuarentena legacy y la reparacion del
# manifiesto son las dos ultimas). Si las filas no se cachean NUNCA, la familia no existe:
# olvidar un incremento deja de tener consecuencia sobre `runs`.
# El check muta el manifiesto POR DEBAJO del lifecycle -- sin invalidacion ninguna -- que es
# el peor caso y el unico que discrimina.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        sembrado = (run_row(life.box_occupancy()) or {}).get("state")   # cachea RUNNING
        fila = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        fila.state = "UNRECONCILED"
        fila.owner_session_id = None
        fila.owner_lease_id = None
        life.manifest.replace(fila)                                    # durable, SIN bump
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        publicado = (run_row(life.box_occupancy()) or {}).get("state")  # dentro del TTL
        if sembrado != "RUNNING" or durable != "UNRECONCILED":
            unmet("N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado",
                  f"el fixture no dejo el estado necesario: sembrado={sembrado!r} "
                  f"durable={durable!r} (hacen falta 'RUNNING' y 'UNRECONCILED')")
        else:
            check("N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado",
                  publicado == "UNRECONCILED",
                  f"durable={durable!r} publicado={publicado!r} -- si publica 'RUNNING', las "
                  f"filas salen de una foto vieja y la coherencia depende de haber acertado el "
                  f"incremento del contador en TODAS las mutaciones, incluidas las que aun no "
                  f"se han escrito")
    except Exception as exc:  # noqa: BLE001
        unmet("N18-SIN-CACHE-DE-MANIFIESTO la caja publica el estado durable, no el cacheado",
              f"{type(exc).__name__}: {exc}")



# -- N19: un run SIN DUENO no despacha -----------------------------------------------------
# H1 de la revision de la ronda 6, reproducido por el orquestador (verificar_codex_r6.py): tras
# un release reconocido el run pasa a RUNNING_IDLE sin dueno y su binding sigue BOUND y
# despachable: `world_spawn` entra con 200 sobre un proceso que nadie posee. Es autoridad, no
# diagnostico. La retirada fisica NO es la respuesta (rompe el reattach de la ficha 17: el
# servidor vivo tendria que relanzarse): el binding sobrevive, pero no despacha sin dueno.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        st_antes, _ = state.enqueue_command("world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server")
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        st_idle, payload = state.enqueue_command(
            "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
        )
        if st_antes != 200 or durable.state != "RUNNING_IDLE" or durable.owner_session_id is not None:
            unmet("N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos",
                  f"el fixture no dejo el estado necesario: enqueue_previo={st_antes} "
                  f"durable={durable.state!r} owner={durable.owner_session_id!r} "
                  f"(hacen falta 200, 'RUNNING_IDLE' y None). No se ha medido el cerco.")
        else:
            check("N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos",
                  st_idle != 200,
                  f"status={st_idle} payload={payload!r} -- 200 significa que el daemon acepta "
                  f"una mutacion del mundo para un proceso que nadie posee, tras haber declarado "
                  f"el release como cierre seguro")
    except Exception as exc:  # noqa: BLE001
        unmet("N19-SIN-DUENO-NO-DESPACHA un run RUNNING_IDLE no acepta comandos",
              f"{type(exc).__name__}: {exc}")

# -- N20: y la adopcion REHABILITA el mismo binding, sin relanzar --------------------------
# Guarda de preservacion del reattach (ficha 17): verde HOY porque hoy todo despacha; su
# trabajo es que el arreglo de N19 no se pase de largo retirando el binding en el release.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        adoptado = life.adopt_run(IDENTITY, "token-A", "run-1")
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        st, payload = state.enqueue_command("world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server")
        ok_adopt = isinstance(adoptado, dict) and adoptado.get("ok") is True
        if not ok_adopt or durable.state != "RUNNING":
            unmet("N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar",
                  f"el fixture no adopto: adopt={adoptado!r} durable={durable.state!r}. "
                  f"No se ha medido la rehabilitacion.")
        else:
            check("N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar",
                  st == 200,
                  f"status={st} payload={payload!r} -- si no es 200, el arreglo de N19 ha "
                  f"retirado el binding en el release y el reattach exige relanzar el servidor")
    except Exception as exc:  # noqa: BLE001
        unmet("N20-ADOPCION-REHABILITA tras adopt_run el mismo binding vuelve a despachar",
              f"{type(exc).__name__}: {exc}")


# -- N21: un borrado deja TUMBA -- el escritor que muestreo su epoch antes de la E/S no resucita
# H2/H3 de la revision Opus de la ronda 6 y H2 de la de Codex, los tres verificados por el
# orquestador: `_write_command_activity` toma `epoch` ANTES de escribir el audit (6-16 ms de
# E/S) y sella DESPUES. Si en medio una observacion sin binding posterior (P3) o el rollback
# de un intento (P5) borran el sello, la escritura acreditada aterriza despues con una epoca
# mas vieja que el borrado y lo resucita: la caja publica `recent` donde el contrato exige
# `unknown`. El borrado tiene que dejar su epoca (tumba) y un escritor con epoca <= tumba no
# escribe. La barrera va en el sink de audit, seam inyectado (se envuelve `life.audit`).
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        audit_real = life.audit

        def _audit_lento(payload):
            if (
                armado.is_set() and not dentro.is_set()
                and isinstance(payload, dict)
                and payload.get("event") == "run_command_activity"
            ):
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return audit_real(payload)

        life.audit = _audit_lento
        t_w = time.time()
        devuelto = {}

        def _escritor():
            devuelto["ok"] = life.record_command_activity("run-1", now=t_w)

        hilo = threading.Thread(target=_escritor, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "ok" not in devuelto
        # Observacion sin binding POSTERIOR a la epoca del escritor, mientras el escritor sigue
        # dentro de la E/S del audit: P3 manda `unknown` y el borrado debe dejar tumba.
        life.record_box_command_activity(now=t_w + 5.0)
        puerta.set()
        hilo.join(timeout=5)
        life.audit = audit_real
        estado = (run_row(life.box_occupancy(now=t_w + 10.0)) or {}).get("activity_state")
        if not bloqueado or devuelto.get("ok") is not True:
            unmet("N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello",
                  f"el fixture no dejo el estado necesario: escritor_bloqueado={bloqueado} "
                  f"(entro={entro} hilo_correcto={quien.get('ident') == hilo.ident}) "
                  f"escritura_ok={devuelto.get('ok')}. No se ha medido la carrera.")
        else:
            check("N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello",
                  estado == "unknown",
                  f"estado={estado!r} -- 'recent' significa que la escritura acreditada, "
                  f"muestreada ANTES de la observacion sin binding, aterrizo DESPUES y "
                  f"resucito el sello que P3 habia borrado")
    except Exception as exc:  # noqa: BLE001
        unmet("N21-TUMBA un escritor con epoca anterior al borrado no resucita el sello",
              f"{type(exc).__name__}: {exc}")

# -- N22: el SELLO del cache de sondas se muestrea ANTES que el dato que certifica ----------
# H1 de la revision Opus de la ronda 6, verificado por el orquestador: `_take_box_snapshot`
# lee el manifiesto en t0 y la revision en t1 > t0. Una mutacion entre ambos deja sondas
# calculadas contra el manifiesto VIEJO (que filtran un PID por estar registrado ahi) guardadas
# bajo la revision NUEVA: un lector limpio compone filas nuevas con sondas viejas y publica
# la caja VACIA con un DayZDiag ajeno vivo. El sello tiene que ser cota INFERIOR del dato.
# Observable sin conocer el diseno: si la caja muta mientras un lector esta dentro, el
# siguiente lector NO puede reutilizar sus sondas: `diag_probe` (seam inyectado) se llama
# otra vez. La barrera envuelve `manifest.list_runs`, metodo publico del store.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        sondas = {"n": 0}

        def _diag_contado():
            sondas["n"] += 1
            return {"known": True, "processes": []}

        life, sink, _ = build(Path(d), diag_probe=_diag_contado)
        list_runs_real = life.manifest.list_runs

        def _list_runs_lento():
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return list_runs_real()

        life.manifest.list_runs = _list_runs_lento
        life.record_command_activity("run-1", now=time.time())   # deja la cache invalidada
        devuelto = {}

        def _lector():
            devuelto["box"] = life.box_occupancy()

        hilo = threading.Thread(target=_lector, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "box" not in devuelto
        # Mutacion que sube la revision mientras el lector esta entre el manifiesto y el sello.
        life.record_command_activity("run-1", now=time.time())
        puerta.set()
        hilo.join(timeout=5)
        life.manifest.list_runs = list_runs_real
        tras_primera = sondas["n"]
        life.box_occupancy()                                       # dentro del TTL de 1,5 s
        tras_segunda = sondas["n"]
        if not bloqueado or "box" not in devuelto or tras_primera < 1:
            unmet("N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas",
                  f"el fixture no dejo el estado necesario: lector_bloqueado={bloqueado} "
                  f"(entro={entro} hilo_correcto={quien.get('ident') == hilo.ident}) "
                  f"lector_volvio={'box' in devuelto} sondas_tras_primera={tras_primera}. "
                  f"No se ha medido el sello.")
        else:
            check("N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas",
                  tras_segunda > tras_primera,
                  f"sondas tras el lector={tras_primera}, tras la lectura siguiente={tras_segunda} "
                  f"-- iguales significa que las sondas calculadas contra el manifiesto de antes "
                  f"de la mutacion se guardaron bajo la revision de despues y un lector limpio "
                  f"las reutiliza: filas nuevas con sondas viejas")
    except Exception as exc:  # noqa: BLE001
        unmet("N22-SELLO-ANTES-DEL-DATO tras una mutacion en vuelo no se reutilizan las sondas",
              f"{type(exc).__name__}: {exc}")



# -- N23: sin dueno no se despacha NADA -- tampoco una lectura -----------------------------
# H1 de la revision Codex del producto final, con repro: el cerco de RUNNING_IDLE solo paraba
# mutaciones externas; una lectura (`query_player_state`, `camera_get`) atravesaba el binding
# con 200 y ademas ACREDITABA actividad al run sin dueno. La autoridad durable del run no
# permite despacho: ni lectura, ni mutacion, ni comando interno. (Mi refinamiento de la ronda
# 8 -- "las lecturas son diagnostico" -- estaba mal: refrescan la frescura de un run que nadie
# posee. Corregido aqui, y N1/N5 cambian de fixture para no exigir lo contrario.)
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        st_antes, _ = state.enqueue_command("camera_get", {}, peer="client")
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        antes_ev = len(eventos_actividad(sink))
        st_idle, payload = state.enqueue_command("camera_get", {}, peer="client")
        nuevos = eventos_actividad(sink)[antes_ev:]
        if st_antes != 200 or durable.state != "RUNNING_IDLE" or durable.owner_session_id is not None:
            unmet("N23-LECTURA-SIN-DUENO una lectura sobre un run RUNNING_IDLE se rechaza y no acredita",
                  f"el fixture no dejo el estado necesario: enqueue_previo={st_antes} "
                  f"durable={durable.state!r} owner={durable.owner_session_id!r}")
        else:
            check("N23-LECTURA-SIN-DUENO una lectura sobre un run RUNNING_IDLE se rechaza y no acredita",
                  st_idle != 200 and not nuevos,
                  f"status={st_idle} payload={payload!r} eventos_nuevos={len(nuevos)} -- 200 "
                  f"significa que el binding despacha sin dueno; un evento nuevo significa que "
                  f"una peticion de nadie ha refrescado la frescura de un run que nadie posee")
    except Exception as exc:  # noqa: BLE001
        unmet("N23-LECTURA-SIN-DUENO una lectura sobre un run RUNNING_IDLE se rechaza y no acredita",
              f"{type(exc).__name__}: {exc}")


# -- N25: el release VACIA y CERCA antes de publicar RUNNING_IDLE; un poll no gana ------------
# H2 de la revision Codex del producto final, con repro: `release_owner` persiste RUNNING_IDLE
# (`manifest.release_owner`) y SOLO DESPUES intenta el lock del loopback para vaciar la cola;
# un poll del bridge puede tomar ese lock antes y llevarse un comando encolado legitimamente
# cuando el run aun era RUNNING. Es el mismo patron que P4 (retirar antes de la transicion
# durable) aplicado al release: el cerco logico y el vaciado van bajo el lock del loopback
# ANTES de publicar; si la persistencia falla, el cerco se revierte.
# La barrera envuelve `manifest.release_owner`, metodo publico del store: bloquea justo
# despues de persistir, que es la ventana del hallazgo.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        # Ronda 10: con `lifecycle.bindings` cableado, el quiesce del lifecycle llega a ESTE
        # ServerState; sin el cable, N25 se ponia verde por la defensa del loopback sola.
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        st_enq, _ = state.enqueue_command(
            "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
        )
        release_real = life.manifest.release_owner

        def _release_lento(session_id, lease_id):
            salida = release_real(session_id, lease_id)
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return salida

        life.manifest.release_owner = _release_lento
        devuelto = {}

        def _liberador():
            devuelto["r"] = life.release_owner(run.owner_session_id, run.owner_lease_id)

        hilo = threading.Thread(target=_liberador, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        # El poll del bridge entra mientras el release esta entre la persistencia y el vaciado.
        st_poll, payload = state.record_poll("server", None, instance=INST_SERVER, source_pid=41001)
        puerta.set()
        hilo.join(timeout=5)
        life.manifest.release_owner = release_real
        entregados = payload.get("commands") if isinstance(payload, dict) else None
        if st_enq != 200 or not bloqueado or durable != "RUNNING_IDLE":
            unmet("N25-RELEASE-CERCA-ANTES-DE-PUBLICAR un poll durante el release no se lleva la cola",
                  f"el fixture no dejo el estado necesario: enqueue={st_enq} "
                  f"release_bloqueado={bloqueado} (entro={entro} hilo_correcto="
                  f"{quien.get('ident') == hilo.ident}) durable={durable!r}. No se ha medido la carrera.")
        else:
            check("N25-RELEASE-CERCA-ANTES-DE-PUBLICAR un poll durante el release no se lleva la cola",
                  not entregados,
                  f"poll={st_poll} commands={entregados!r} -- un comando entregado significa "
                  f"que el release publico RUNNING_IDLE antes de cercar y vaciar bajo el lock "
                  f"del loopback, y el bridge se llevo una mutacion de un dueno que ya no existe")
    except Exception as exc:  # noqa: BLE001
        unmet("N25-RELEASE-CERCA-ANTES-DE-PUBLICAR un poll durante el release no se lleva la cola",
              f"{type(exc).__name__}: {exc}")



# -- N26: el poll REVALIDA tras retomar el lock -- una transicion en su ventana no entrega ------
# ALTA de la revision Codex de la ronda 9, verificada: `record_poll` copia la cola bajo el lock,
# lo suelta (expire_due, sondas), y al retomarlo solo comprueba identidad y prefijo de la cola;
# si en medio `repair_manifest_recovery` -> `recover_after_restart` deja el run en RUNNING_IDLE
# (sin cerco ni vaciado), la entrega sigue adelante: un `world_spawn` encolado con dueno llega a
# un run que ya no lo tiene. La barrera va en la ventana sin lock: `command_requires_lease`
# (funcion de modulo que el poll llama por cada comando copiado). `lifecycle.bindings` va
# cableado a este ServerState para que el cerco de verdad sea visible.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        st_enq, _ = state.enqueue_command(
            "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
        )
        raw = (Path(d) / "runtime" / "runs.json").read_bytes()
        crl_real = loopback.command_requires_lease

        def _crl_lento(cmd):
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return crl_real(cmd)

        loopback.command_requires_lease = _crl_lento
        devuelto = {}

        def _poll():
            devuelto["p"] = state.record_poll("server", None, instance=INST_SERVER, source_pid=41001)

        hilo = threading.Thread(target=_poll, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "p" not in devuelto
        # Transicion a RUNNING_IDLE mientras el poll tiene la cola copiada y el lock suelto.
        reparado = life.repair_manifest_recovery(raw)
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        puerta.set()
        hilo.join(timeout=5)
        loopback.command_requires_lease = crl_real
        st_poll, payload = devuelto.get("p", (None, None))
        entregados = payload.get("commands") if isinstance(payload, dict) else None
        ok_repair = isinstance(reparado, dict) and reparado.get("terminal_safe") is True
        if st_enq != 200 or not bloqueado or not ok_repair or durable != "RUNNING_IDLE" or st_poll is None:
            unmet("N26-POLL-REVALIDA una transicion a RUNNING_IDLE en la ventana del poll no entrega",
                  f"el fixture no dejo el estado necesario: enqueue={st_enq} poll_bloqueado={bloqueado} "
                  f"(entro={entro} hilo_correcto={quien.get('ident') == hilo.ident}) "
                  f"repair={reparado!r} durable={durable!r} poll={st_poll}. No se ha medido la ventana.")
        else:
            check("N26-POLL-REVALIDA una transicion a RUNNING_IDLE en la ventana del poll no entrega",
                  not entregados,
                  f"poll={st_poll} commands={entregados!r} -- un comando entregado significa que el "
                  f"poll copio la cola con dueno y la entrego sin dueno: la transicion de la "
                  f"reparacion no cerco, y el poll no revalido al retomar el lock")
    except Exception as exc:  # noqa: BLE001
        try:
            loopback.command_requires_lease = crl_real  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass
        unmet("N26-POLL-REVALIDA una transicion a RUNNING_IDLE en la ventana del poll no entrega",
              f"{type(exc).__name__}: {exc}")

# -- N28: retirar fisicamente un run BORRA su cerco ---------------------------------------------
# MEDIA de Codex (familia nueva: ciclo de vida asimetrico del cerco): `retire_run` retira
# instancias pero no el run de `_fenced_runs`; los retiros sin adopcion (reap, reconcile sin
# supervivientes, repairs terminales) dejan una entrada por run hasta reiniciar el daemon.
# Caja blanca a proposito: lee `_fenced_runs`; si el atributo no existe, UNMET, no verde.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)        # cerca run-1
        cercados_antes = set(getattr(state, "_fenced_runs", None) or ())
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        reapeados = life.reap_dead_runs()
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        cercados = getattr(state, "_fenced_runs", None)
        if cercados is None or "run-1" not in cercados_antes or reapeados != ["run-1"] or durable != "EXITED":
            unmet("N28-CERCO-NO-SE-ACUMULA retirar un run borra su cerco",
                  f"el fixture no dejo el estado necesario: _fenced_runs={'ausente' if cercados is None else 'presente'} "
                  f"cercado_tras_release={'run-1' in cercados_antes} reapeados={reapeados!r} durable={durable!r}")
        else:
            check("N28-CERCO-NO-SE-ACUMULA retirar un run borra su cerco",
                  "run-1" not in set(cercados),
                  f"_fenced_runs tras el reap={sorted(cercados)!r} -- una marca logica que "
                  f"sobrevive a la retirada fisica de su run se acumula hasta el reinicio")
    except Exception as exc:  # noqa: BLE001
        unmet("N28-CERCO-NO-SE-ACUMULA retirar un run borra su cerco",
              f"{type(exc).__name__}: {exc}")

# -- N29: exec_enforce que pierde la carrera con un release devuelve run_not_owned -------------
# BAJA de Codex: la segunda validacion de `exec_enforce` (tras el audit `allowed`) detecta el
# cerco pero colapsa el motivo en `enqueue_cancelled`. P6 exige `run_not_owned` para TODO
# comando dirigido a un run sin dueno. El seam es `exec_audit`, callback inyectado por
# constructor: bloquea entre las dos validaciones.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}

        def _exec_audit_lento(*_args):
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return None

        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1", enable_exec_enforce=True,
                           exec_allowlist={"probe()"}, exec_audit=_exec_audit_lento)
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        devuelto = {}

        def _encolador():
            devuelto["r"] = state.enqueue_command(
                "exec_enforce", {"expr": "probe()", "main_fn": "Main"}, peer="server"
            )

        hilo = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        life.release_owner(run.owner_session_id, run.owner_lease_id)        # cerca y persiste
        puerta.set()
        hilo.join(timeout=5)
        st, payload = devuelto.get("r", (None, None))
        if not bloqueado or st is None:
            unmet("N29-EXEC-ENFORCE-RUN-NOT-OWNED perder la carrera con un release devuelve run_not_owned",
                  f"el fixture no dejo el estado necesario: bloqueado={bloqueado} (entro={entro} "
                  f"hilo_correcto={quien.get('ident') == hilo.ident}) resultado={devuelto.get('r')!r}")
        else:
            error = payload.get("error") if isinstance(payload, dict) else None
            check("N29-EXEC-ENFORCE-RUN-NOT-OWNED perder la carrera con un release devuelve run_not_owned",
                  st != 200 and error == "run_not_owned",
                  f"status={st} error={error!r} -- 200 seria despacho sin dueno; "
                  f"'enqueue_cancelled' es fail-closed pero colapsa el motivo que P6 exige")
    except Exception as exc:  # noqa: BLE001
        unmet("N29-EXEC-ENFORCE-RUN-NOT-OWNED perder la carrera con un release devuelve run_not_owned",
              f"{type(exc).__name__}: {exc}")



# -- N30: un credito EN VUELO no aterriza tras el cerco -------------------------------------------
# R9 #1 (P2, confirmado por verificador ciego): el enqueue acepta bajo el lock del loopback, lo
# suelta, y el credito (`_note_run_command_activity` -> `record_command_activity`) corre despues
# y fuera; si en medio el release cerca, vacia y persiste RUNNING_IDLE, el credito aterriza igual
# (ni el cerco ni la retirada dejan tumba) y un run sin dueno publica `recent` por un comando
# que fue DESCARTADO. Seam: `record_command_activity` es atributo de instancia y el loopback lo
# resuelve por getattr; se envuelve para bloquear al hilo del enqueue justo en el credito.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        credito_real = life.record_command_activity

        def _credito_lento(run_id, *, now=None):
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return credito_real(run_id, now=now)

        life.record_command_activity = _credito_lento
        devuelto = {}

        def _encolador():
            devuelto["r"] = state.enqueue_command(
                "world_spawn", {"classname": "SurvivorM_Mirek"}, peer="server"
            )

        hilo = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        life.release_owner(run.owner_session_id, run.owner_lease_id)   # cerca, vacia, persiste
        puerta.set()
        hilo.join(timeout=5)
        life.record_command_activity = credito_real
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        st = (devuelto.get("r") or (None, None))[0]
        estado = (run_row(life.box_occupancy(now=time.time())) or {}).get("activity_state")
        if not bloqueado or st != 200 or durable != "RUNNING_IDLE":
            unmet("N30-CREDITO-EN-VUELO un credito aceptado antes del cerco no aterriza despues",
                  f"el fixture no dejo el estado necesario: bloqueado={bloqueado} (entro={entro} "
                  f"hilo_correcto={quien.get('ident') == hilo.ident}) enqueue={st} durable={durable!r}")
        else:
            check("N30-CREDITO-EN-VUELO un credito aceptado antes del cerco no aterriza despues",
                  estado == "unknown",
                  f"activity_state={estado!r} durable={durable!r} -- 'recent' significa que un "
                  f"run sin dueno publica frescura por un comando que el cerco descarto: el "
                  f"epoch se muestreo fuera del lock y ni el cerco ni la retirada dejan tumba")
    except Exception as exc:  # noqa: BLE001
        unmet("N30-CREDITO-EN-VUELO un credito aceptado antes del cerco no aterriza despues",
              f"{type(exc).__name__}: {exc}")

# -- N31: retirar un binding CIERRA lo que descarta -------------------------------------------
# R9 #2 (P2, confirmado): `_retire_instance_locked` descarta la cola pero no ejecuta
# `_flush_queue_discards`: el `exec_audit(..., "discarded")` y el cierre de operaciones del
# coordinador no corren tras stop/reap/repair; `fence_runs` (release) si lo hace. Observable
# aqui por el audit de exec: un exec_enforce encolado y luego retirado por un stop tiene que
# dejar su registro 'discarded'.
with tempfile.TemporaryDirectory() as d:
    try:
        registros = []

        def _exec_audit(expr, decision, main_fn, command_id):
            registros.append((decision, command_id))

        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1", enable_exec_enforce=True,
                           exec_allowlist={"probe()"}, exec_audit=_exec_audit)
        life.bindings = state
        st, payload = state.enqueue_command(
            "exec_enforce", {"expr": "probe()", "main_fn": "Main"}, peer="server"
        )
        cid = payload.get("id") if isinstance(payload, dict) else None
        parado = life.stop_run(IDENTITY, "token-A", "run-1")
        ok_stop = isinstance(parado, dict) and parado.get("ok") is True
        descartados = [c for (dec, c) in registros if dec == "discarded"]
        if st != 200 or cid is None or not ok_stop:
            unmet("N31-RETIRE-CIERRA-LO-QUE-DESCARTA un exec retirado por stop queda auditado como discarded",
                  f"el fixture no dejo el estado necesario: enqueue={st} id={cid!r} stop_ok={ok_stop}")
        else:
            check("N31-RETIRE-CIERRA-LO-QUE-DESCARTA un exec retirado por stop queda auditado como discarded",
                  cid in descartados,
                  f"registros={registros!r} -- sin 'discarded' para id={cid}: la retirada tiro la "
                  f"cola sin cerrar operaciones ni auditar; el pin del coordinador vive hasta el timeout")
    except Exception as exc:  # noqa: BLE001
        unmet("N31-RETIRE-CIERRA-LO-QUE-DESCARTA un exec retirado por stop queda auditado como discarded",
              f"{type(exc).__name__}: {exc}")

# -- N32: si el estado durable NO se puede leer, el enqueue RECHAZA ---------------------------
# R9 #13 (security): `_enqueue_run_rejection` abre paso (y acredita actividad) cuando
# `manifest.get` lanza. Es fail-open, y G6 manda fail-closed. Seam: `manifest.get`, metodo
# publico del store, envuelto para lanzar durante el enqueue.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        llamadas = {"n": 0}
        get_real = life.manifest.get

        def _get_roto(run_id):
            llamadas["n"] += 1
            raise RuntimeError("manifest_unreadable")

        antes = len(eventos_actividad(sink))
        life.manifest.get = _get_roto
        try:
            st, payload = state.enqueue_command("camera_get", {}, peer="client")
        finally:
            life.manifest.get = get_real
        nuevos = eventos_actividad(sink)[antes:]
        if llamadas["n"] == 0:
            unmet("N32-ESTADO-ILEGIBLE-RECHAZA un enqueue con el manifiesto ilegible no despacha",
                  "el fixture no alcanzo el seam: el enqueue no consulto manifest.get")
        else:
            check("N32-ESTADO-ILEGIBLE-RECHAZA un enqueue con el manifiesto ilegible no despacha",
                  st != 200 and not nuevos,
                  f"status={st} payload={payload!r} eventos_nuevos={len(nuevos)} -- 200 con el estado "
                  f"durable ilegible es fail-open: se despacha y se acredita sin saber si hay dueno")
    except Exception as exc:  # noqa: BLE001
        unmet("N32-ESTADO-ILEGIBLE-RECHAZA un enqueue con el manifiesto ilegible no despacha",
              f"{type(exc).__name__}: {exc}")

# -- N34: un resultado TARDIO tras retirar el binding no se acepta como genuino ----------------
# R9 #12 (security): retirar un binding borra la valla de resultado de los comandos YA
# despachados: `store_result` sin `inst` (o de otra instancia) se acepta. Un run parado no
# puede seguir entregando resultados que nadie puede acreditar.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        st_enq, payload = state.enqueue_command("camera_get", {}, peer="client")
        cid = payload.get("id") if isinstance(payload, dict) else None
        st_poll, entregado = state.record_poll("client", None, instance=INST_CLIENT, source_pid=41002)
        ids = [c.get("id") for c in (entregado.get("commands") or [])] if isinstance(entregado, dict) else []
        parado = life.stop_run(IDENTITY, "token-A", "run-1")
        ok_stop = isinstance(parado, dict) and parado.get("ok") is True
        st_res, res = state.store_result({"id": cid, "ok": True, "result": {"late": True}}, instance=None)
        if st_enq != 200 or cid is None or cid not in ids or not ok_stop:
            unmet("N34-RESULTADO-TARDIO-TRAS-RETIRE un result sin instancia tras retirar no se acepta",
                  f"el fixture no dejo el estado necesario: enqueue={st_enq} id={cid!r} "
                  f"entregados={ids!r} stop_ok={ok_stop}")
        else:
            check("N34-RESULTADO-TARDIO-TRAS-RETIRE un result sin instancia tras retirar no se acepta",
                  st_res != 200,
                  f"store_result={st_res} {res!r} -- 200 significa que la retirada borro la valla de "
                  f"resultado y cualquiera puede cerrar un comando despachado a un run muerto")
    except Exception as exc:  # noqa: BLE001
        unmet("N34-RESULTADO-TARDIO-TRAS-RETIRE un result sin instancia tras retirar no se acepta",
              f"{type(exc).__name__}: {exc}")



# -- N35: en la retirada terminal el binding se retira ANTES de persistir EXITED ----------------
# H-01 ALTA de la revision final de Codex (composicion G x H): `_commit_retirement`, el metodo
# unico del anillo, persiste EXITED y SOLO DESPUES retira el binding, invirtiendo el P4 de G
# (retirar antes de la transicion durable) en cinco de seis caminos; en esa ventana un
# `store_result` tardio para un comando ya despachado se acepta como genuino. La base de G
# retiraba antes de persistir (ws-frozen-r8:2566). Seam: `manifest.replace` (metodo publico),
# barrera justo despues de persistir el EXITED de un reap.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        st_enq, payload = state.enqueue_command("camera_get", {}, peer="client")
        cid = payload.get("id") if isinstance(payload, dict) else None
        st_poll, entregado = state.record_poll("client", None, instance=INST_CLIENT, source_pid=41002)
        ids = [c.get("id") for c in (entregado.get("commands") or [])] if isinstance(entregado, dict) else []
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        replace_real = life.manifest.replace

        def _replace_lento(run):
            salida = replace_real(run)
            if armado.is_set() and not dentro.is_set() and getattr(run, "state", None) == "EXITED":
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return salida

        life.manifest.replace = _replace_lento
        devuelto = {}

        def _reaper():
            devuelto["r"] = life.reap_dead_runs()

        hilo = threading.Thread(target=_reaper, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        durante = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        st_res, res = state.store_result({"id": cid, "ok": True, "result": {"late": True}}, instance=None)
        puerta.set()
        hilo.join(timeout=5)
        life.manifest.replace = replace_real
        if st_enq != 200 or cid is None or cid not in ids or not bloqueado or durante != "EXITED" or devuelto.get("r") != ["run-1"]:
            unmet("N35-RETIRAR-ANTES-DE-PERSISTIR un result tardio durante la retirada terminal no se acepta",
                  f"el fixture no dejo el estado necesario: enqueue={st_enq} id={cid!r} entregados={ids!r} "
                  f"reap_bloqueado={bloqueado} durable_durante={durante!r} reaped={devuelto.get('r')!r}")
        else:
            check("N35-RETIRAR-ANTES-DE-PERSISTIR un result tardio durante la retirada terminal no se acepta",
                  st_res != 200,
                  f"store_result={st_res} {res!r} con el manifiesto ya EXITED -- 200 significa que "
                  f"el binding seguia vivo tras la transicion durable: el metodo unico del anillo "
                  f"persiste primero y retira despues, al reves que P4")
    except Exception as exc:  # noqa: BLE001
        unmet("N35-RETIRAR-ANTES-DE-PERSISTIR un result tardio durante la retirada terminal no se acepta",
              f"{type(exc).__name__}: {exc}")

# -- N36: la limpieza terminal conserva una frontera -- un credito viejo no reaparece ----------
# H-02 MEDIA de la revision final: `_forget_run_residues` borra dato, unknown, compensacion Y
# LA TUMBA; un escritor que acepto el comando antes de la retirada aterriza despues sobre un
# run EXITED y el sello reaparece (`after_release: last=..., tombstone=None`). Regla: la
# limpieza terminal deja una frontera posterior a cualquier sello aceptable. Caja blanca
# declarada: lee `_last_activity`; si el atributo no existe, UNMET.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        credito_real = life.record_command_activity

        def _credito_lento(run_id, *, now=None):
            if armado.is_set() and not dentro.is_set():
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
            return credito_real(run_id, now=now)

        life.record_command_activity = _credito_lento
        devuelto = {}

        def _encolador():
            devuelto["r"] = state.enqueue_command("camera_get", {}, peer="client")

        hilo = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        reapeados = life.reap_dead_runs()
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        puerta.set()
        hilo.join(timeout=5)
        life.record_command_activity = credito_real
        sellos = getattr(life, "_last_activity", None)
        if sellos is None or not bloqueado or reapeados != ["run-1"] or durable != "EXITED":
            unmet("N36-FRONTERA-TERMINAL un credito viejo no reaparece tras la limpieza de EXITED",
                  f"el fixture no dejo el estado necesario: _last_activity={'ausente' if sellos is None else 'presente'} "
                  f"bloqueado={bloqueado} reaped={reapeados!r} durable={durable!r}")
        else:
            reaparecido = [k for k in sellos if k[1] == "run-1"]
            check("N36-FRONTERA-TERMINAL un credito viejo no reaparece tras la limpieza de EXITED",
                  not reaparecido,
                  f"sellos de run-1 tras el credito tardio={reaparecido!r} -- la limpieza terminal "
                  f"borro la tumba y el escritor que acepto antes de la retirada aterrizo despues")
    except Exception as exc:  # noqa: BLE001
        unmet("N36-FRONTERA-TERMINAL un credito viejo no reaparece tras la limpieza de EXITED",
              f"{type(exc).__name__}: {exc}")

# -- N37: un estado durable MALFORMADO no es permiso -----------------------------------------
# H-03 MEDIA de la revision final: `_durable_run_state` devuelve None cuando el registro no
# trae `state: str` (o no hay getter), y el consumidor trata None como autorizacion. Es la
# rama fail-open hermana de N32 (getter que lanza). Seam: `manifest.get` devuelve un registro
# sin estado.
with tempfile.TemporaryDirectory() as d:
    try:
        import types as _types
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        llamadas = {"n": 0}
        get_real = life.manifest.get

        def _get_malformado(run_id):
            llamadas["n"] += 1
            return _types.SimpleNamespace(run_id=run_id, state=None)

        antes = len(eventos_actividad(sink))
        life.manifest.get = _get_malformado
        try:
            st, payload = state.enqueue_command("camera_get", {}, peer="client")
        finally:
            life.manifest.get = get_real
        nuevos = eventos_actividad(sink)[antes:]
        if llamadas["n"] == 0:
            unmet("N37-ESTADO-MALFORMADO-RECHAZA un registro durable sin estado no autoriza el enqueue",
                  "el fixture no alcanzo el seam: el enqueue no consulto manifest.get")
        else:
            check("N37-ESTADO-MALFORMADO-RECHAZA un registro durable sin estado no autoriza el enqueue",
                  st != 200 and not nuevos,
                  f"status={st} payload={payload!r} eventos_nuevos={len(nuevos)} -- 200 con un "
                  f"registro sin `state` es fail-open: None se lee como permiso")
    except Exception as exc:  # noqa: BLE001
        unmet("N37-ESTADO-MALFORMADO-RECHAZA un registro durable sin estado no autoriza el enqueue",
              f"{type(exc).__name__}: {exc}")



# -- N38: sin lifecycle no hay estado durable -- un binding BOUND no despacha ------------------
# ALTA de la revision delta de Codex: `_durable_run_state` devuelve None cuando
# `state.lifecycle is None` y el consumidor lo lee como permiso; enqueue, exec_enforce y poll
# despachan sin prueba durable. Ningun modulo de produccion acuna bindings sin lifecycle (los
# acuna el lifecycle al arrancar), asi que un BOUND sin lifecycle es un estado sin autoridad:
# rechazo. La cola legacy sin binding queda como compatibilidad y no se mide aqui.
with tempfile.TemporaryDirectory() as d:
    try:
        state = loopback.ServerState("clave-gate")
        state.install_bound_peer(instance=INST_CLIENT, role="client", pid=41002, run_id="run-1")
        state.lifecycle = None
        st, payload = state.enqueue_command("camera_get", {}, peer="client")
        st_poll, entregado = state.record_poll("client", None, instance=INST_CLIENT, source_pid=41002)
        cmds = entregado.get("commands") if isinstance(entregado, dict) else None
        check("N38-SIN-LIFECYCLE-NO-DESPACHA un binding BOUND sin lifecycle no encola ni entrega",
              st != 200 and not cmds,
              f"enqueue={st} payload={payload!r} poll_commands={cmds!r} -- 200 o una entrega "
              f"significan despacho sin ninguna prueba durable de que el run esta RUNNING")
    except Exception as exc:  # noqa: BLE001
        unmet("N38-SIN-LIFECYCLE-NO-DESPACHA un binding BOUND sin lifecycle no encola ni entrega",
              f"{type(exc).__name__}: {exc}")

# -- N39: un escritor con audit FALLIDO tras la frontera terminal no recrea `unknown` -----------
# MEDIA de la revision delta: el writer muestrea su epoch antes de la retirada, se bloquea en
# el audit, la limpieza terminal eleva la frontera, y despues el audit devuelve fallo: la rama
# `not ok` anade sticky `unknown` sin comparar con la tumba. Seam: `life.audit` envuelto para
# bloquear y devolver False. Caja blanca declarada: lee `_activity_unknown`.
with tempfile.TemporaryDirectory() as d:
    try:
        armado = threading.Event()
        dentro = threading.Event()
        puerta = threading.Event()
        quien = {}
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        audit_real = life.audit

        def _audit_lento_fallido(payload):
            if (armado.is_set() and not dentro.is_set() and isinstance(payload, dict)
                    and payload.get("event") == "run_command_activity"):
                quien["ident"] = threading.get_ident()
                dentro.set()
                puerta.wait(5.0)
                return False                    # el audit FALLA despues de la frontera
            return audit_real(payload)

        life.audit = _audit_lento_fallido
        devuelto = {}

        def _encolador():
            devuelto["r"] = state.enqueue_command("camera_get", {}, peer="client")

        hilo = threading.Thread(target=_encolador, daemon=True)
        armado.set()
        hilo.start()
        entro = dentro.wait(5.0)
        bloqueado = entro and quien.get("ident") == hilo.ident and "r" not in devuelto
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        reapeados = life.reap_dead_runs()
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        puerta.set()
        hilo.join(timeout=5)
        life.audit = audit_real
        desconocidos = getattr(life, "_activity_unknown", None)
        if desconocidos is None or not bloqueado or reapeados != ["run-1"] or durable != "EXITED":
            unmet("N39-FRONTERA-TAMBIEN-PARA-EL-FALLO un audit fallido tras la limpieza terminal no recrea unknown",
                  f"el fixture no dejo el estado necesario: _activity_unknown={'ausente' if desconocidos is None else 'presente'} "
                  f"bloqueado={bloqueado} reaped={reapeados!r} durable={durable!r}")
        else:
            recreado = [k for k in desconocidos if k[1] == "run-1"]
            check("N39-FRONTERA-TAMBIEN-PARA-EL-FALLO un audit fallido tras la limpieza terminal no recrea unknown",
                  not recreado,
                  f"_activity_unknown de run-1 tras el fallo tardio={recreado!r} -- la rama `not ok` "
                  f"anadio sticky sin comparar el epoch con la frontera terminal")
    except Exception as exc:  # noqa: BLE001
        unmet("N39-FRONTERA-TAMBIEN-PARA-EL-FALLO un audit fallido tras la limpieza terminal no recrea unknown",
              f"{type(exc).__name__}: {exc}")

# -- N40: adoptar tras una persistencia terminal fallida no entrega un run mudo -----------------
# MEDIA de la revision delta: con P19 el binding se retira ANTES de persistir; si el replace
# terminal falla (disco lleno) el manifiesto restaura el registro previo pero el binding
# sigue retirado; `adopt_run` responde ok y el run queda RUNNING con un binding
# `binding_retired`: adoptable y mudo. Regla: o la adopcion se rechaza dirigiendo al
# reap/recovery, o reconstruye acreditadamente el binding antes de decir ok.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)      # RUNNING_IDLE (reapable)
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        replace_real = life.manifest.replace
        fallos = {"n": 0}

        def _replace_roto(r):
            if getattr(r, "state", None) == "EXITED" and fallos["n"] == 0:
                fallos["n"] += 1
                raise OSError("disk full")
            return replace_real(r)

        life.manifest.replace = _replace_roto
        reapeados = life.reap_dead_runs()                                  # persistencia terminal falla
        life.manifest.replace = replace_real
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        adoptado = life.adopt_run(IDENTITY, "token-A", "run-1")
        ok_adopt = isinstance(adoptado, dict) and adoptado.get("ok") is True
        st, payload = state.enqueue_command("camera_get", {}, peer="client")
        mudo = ok_adopt and st != 200
        if fallos["n"] != 1 or reapeados or durable != "RUNNING_IDLE":
            unmet("N40-ADOPCION-TRAS-PERSISTENCIA-FALLIDA adoptar no entrega un run mudo",
                  f"el fixture no dejo el estado necesario: fallos_inyectados={fallos['n']} "
                  f"reaped={reapeados!r} durable={durable!r} (hace falta RUNNING_IDLE restaurado)")
        else:
            check("N40-ADOPCION-TRAS-PERSISTENCIA-FALLIDA adoptar no entrega un run mudo",
                  not mudo,
                  f"adopt={adoptado!r} enqueue={st} {payload!r} -- ok + binding_retired es un run "
                  f"adoptado que no puede recibir un solo comando")
    except Exception as exc:  # noqa: BLE001
        unmet("N40-ADOPCION-TRAS-PERSISTENCIA-FALLIDA adoptar no entrega un run mudo",
              f"{type(exc).__name__}: {exc}")


# -- N41: persistencia terminal fallida + REINICIO del daemon: adoptar sigue sin entregar un run mudo
# MEDIA de la segunda delta: el rechazo de P19' vivia en `ServerState._retired_run_ids`, un set en
# memoria que empieza vacio en cada daemon. Tras un reinicio el manifiesto dice RUNNING_IDLE, el
# guard dice process_not_found y `adopt_run` responde ok sobre un run cuyo unico proceso esta
# muerto y que no tiene binding. Regla durable: adoptar exige al menos un proceso registrado
# VIVO y propio segun el guard (identidad del manifiesto), no una marca en memoria.
# El reinicio se reproduce de verdad: ProcessLifecycle y ServerState NUEVOS sobre el MISMO
# manifiesto en disco y el MISMO guard; nada de lo que vivia en memoria sobrevive.
def _reinicio(d: Path, life, sink, state_nuevo, generation: str = "gen-B"):
    """A fresh lifecycle over the same runtime root: what a daemon restart leaves behind."""
    game = d / "game"
    paths = RuntimePaths(d / "runtime", d / "runtime" / "audit",
                         d / "runtime" / "coordination.json", d / "runtime" / "runs.json")
    manifest2 = RunManifestStore(paths)
    manifest2.recover_after_restart()                    # daemon.py hace exactamente esto al arrancar
    life2 = ProcessLifecycle(
        coordinator=life.coordinator, manifest=manifest2, audit=sink, guard=life.guard,
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=lambda: {"known": True, "processes": []},
        game_path=game, launcher=Launcher(), id_fn=lambda: "run-1",
        argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        bindings=state_nuevo, daemon_generation=generation,
    )
    state_nuevo.lifecycle = life2
    return life2, manifest2


with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)      # RUNNING_IDLE (reapable)
        life.guard.snapshots[9001] = {"error": "process_not_found", "exit_code": 4}
        replace_real = life.manifest.replace
        fallos = {"n": 0}

        def _replace_roto(r):
            if getattr(r, "state", None) == "EXITED" and fallos["n"] == 0:
                fallos["n"] += 1
                raise OSError("disk full")
            return replace_real(r)

        life.manifest.replace = _replace_roto
        reapeados = life.reap_dead_runs()                                  # persistencia terminal falla
        life.manifest.replace = replace_real
        durable = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0].state
        state2 = loopback.ServerState("clave-gate")                        # sin bindings, sin marcas
        life2, manifest2 = _reinicio(Path(d), life, sink, state2)
        visto = [r for r in manifest2.list_runs() if r.run_id == "run-1"]
        if fallos["n"] != 1 or reapeados or durable != "RUNNING_IDLE" or not visto:
            unmet("N41-ADOPCION-TRAS-REINICIO-NO-ENTREGA-UN-RUN-MUERTO",
                  f"el fixture no dejo el estado necesario: fallos_inyectados={fallos['n']} "
                  f"reaped={reapeados!r} durable={durable!r} visto_tras_reinicio={bool(visto)}")
        else:
            adoptado = life2.adopt_run(IDENTITY, "token-A", "run-1")
            ok_adopt = isinstance(adoptado, dict) and adoptado.get("ok") is True
            despues = [r for r in manifest2.list_runs() if r.run_id == "run-1"][0].state
            check("N41-ADOPCION-TRAS-REINICIO-NO-ENTREGA-UN-RUN-MUERTO",
                  not ok_adopt,
                  f"adopt={adoptado!r} durable_despues={despues!r} -- ok tras el reinicio significa "
                  f"que el rechazo de P19' vivia en memoria: el manifiesto dice RUNNING_IDLE, el guard "
                  f"dice process_not_found y aun asi se entrega un run sin un solo proceso vivo")
    except Exception as exc:  # noqa: BLE001
        unmet("N41-ADOPCION-TRAS-REINICIO-NO-ENTREGA-UN-RUN-MUERTO",
              f"{type(exc).__name__}: {exc}")

# -- N42: tras un reinicio con el proceso VIVO, adoptar no entrega un run mudo sin declararlo ----
# Los bindings no sobreviven al daemon (loopback.py: `unbound_after_restart`). El run sigue vivo
# y adoptable -- hace falta adoptarlo para poder pararlo -- pero no puede despachar una sola
# mutacion. Regla: o la adopcion se rechaza, o responde ok DECLARANDO `dispatchable: False`
# (con `hint`). Un ok liso sobre un run que no despacha es la misma mentira de N40.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)      # RUNNING_IDLE, proceso vivo
        state2 = loopback.ServerState("clave-gate")                        # reinicio: sin bindings
        life2, manifest2 = _reinicio(Path(d), life, sink, state2)
        visto = [r for r in manifest2.list_runs() if r.run_id == "run-1"]
        if not visto or visto[0].state != "RUNNING_IDLE":
            unmet("N42-ADOPCION-TRAS-REINICIO-DECLARA-DESPACHABILIDAD",
                  f"el fixture no dejo RUNNING_IDLE visible tras el reinicio: {visto!r}")
        else:
            adoptado = life2.adopt_run(IDENTITY, "token-A", "run-1")
            ok_adopt = isinstance(adoptado, dict) and adoptado.get("ok") is True
            st, payload = state2.enqueue_command("world_spawn", {"classname": "SurvivorM_Mirek"},
                                                 peer="server")
            declarado = isinstance(adoptado, dict) and adoptado.get("dispatchable") is False
            mudo_sin_declarar = ok_adopt and st != 200 and not declarado
            check("N42-ADOPCION-TRAS-REINICIO-DECLARA-DESPACHABILIDAD",
                  not mudo_sin_declarar,
                  f"adopt={adoptado!r} enqueue={st} {payload!r} -- un ok sin `dispatchable: False` "
                  f"sobre un run que rechaza la primera mutacion es un run adoptado y mudo")
    except Exception as exc:  # noqa: BLE001
        unmet("N42-ADOPCION-TRAS-REINICIO-DECLARA-DESPACHABILIDAD",
              f"{type(exc).__name__}: {exc}")

# -- N43: guarda de preservacion -- tras el reinicio, adoptar-para-parar sigue funcionando -------
# Es la razon por la que N42 admite el ok declarado y no exige rechazo: `stop_run` exige ser
# el adoptante (process_lifecycle.py `run_not_adopted`), asi que un adopt fail-closed sobre
# un run vivo sin binding dejaria procesos huerfanos que solo admin_reconcile podria cerrar.
# Verde HOY; su trabajo es que el arreglo de N41/N42 no se pase de largo.
with tempfile.TemporaryDirectory() as d:
    try:
        life, sink, _ = build(Path(d))
        state = estado_con(life, "run-1")
        life.bindings = state
        run = [r for r in life.manifest.list_runs() if r.run_id == "run-1"][0]
        life.release_owner(run.owner_session_id, run.owner_lease_id)
        state2 = loopback.ServerState("clave-gate")
        life2, manifest2 = _reinicio(Path(d), life, sink, state2)
        guard = life.guard

        def _terminate_y_muere(record, _g=guard):
            _g.snapshots[record.pid] = {"error": "process_not_found", "exit_code": 4}
            return {"terminated": True}

        guard.terminate = _terminate_y_muere
        adoptado = life2.adopt_run(IDENTITY, "token-A", "run-1")
        ok_adopt = isinstance(adoptado, dict) and adoptado.get("ok") is True
        if not ok_adopt:
            check("N43-ADOPTAR-PARA-PARAR-SOBREVIVE-AL-REINICIO",
                  False,
                  f"adopt={adoptado!r} -- un run VIVO sin binding tiene que seguir siendo adoptable, "
                  f"porque parar exige adoptar; rechazarlo deja procesos huerfanos")
        else:
            parado = life2.stop_run(IDENTITY, "token-A", "run-1")
            durable = [r for r in manifest2.list_runs() if r.run_id == "run-1"][0].state
            check("N43-ADOPTAR-PARA-PARAR-SOBREVIVE-AL-REINICIO",
                  isinstance(parado, dict) and parado.get("state") == "EXITED" and durable == "EXITED",
                  f"adopt={adoptado!r} stop={parado!r} durable={durable!r} -- el unico uso legitimo "
                  f"de adoptar tras un reinicio es poder parar y relanzar")
    except Exception as exc:  # noqa: BLE001
        unmet("N43-ADOPTAR-PARA-PARAR-SOBREVIVE-AL-REINICIO",
              f"{type(exc).__name__}: {exc}")


print("=" * 104)
worst = 0
for nm, v, dt in RESULTS:
    print(f"[{v:<5}] {nm}")
    if dt:
        print(f"          {dt[:240]}")
    if v != "PASS":
        worst = 1
print("=" * 104)
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
if worst == 0:
    print("ORACULO-VERDE")
sys.exit(worst)
