"""Lease rehearsal for the supervisor+worker recycle (fb-20260909-211424-5dbe).

The spike proved the SHAPE works: edit source, call server_reload, same ClientSession
serves new code. It explicitly did NOT touch identity or the lease. This rehearses that.

What is real here and what is a double:
  REAL  - dayz_mcp.session_coordination.SessionCoordinator, the production class that
          the daemon instantiates at daemon.py:464 and wires it at daemon.py:592. Every verdict below is its own.
  REAL  - ClientIdentity, minted the way server.py:1210 mints it.
  DOUBLE- the clock (so TTL is arithmetic, not a wait), token/id generators (so
          failures name a token instead of 32 random bytes), audit and cleanup sinks.
That is the same split the production tests use (tools/tests/test_session_coordination.py:180).

No daemon, no DayZ, no lease acquired against the shared box. Nothing is mutated.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TOOLS = Path(r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\DayZ_MCP_dev\tools")
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from dayz_mcp.session_coordination import (  # noqa: E402
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)

# Measured by the spike on 2026-09-09: recycle with a 2 s call in flight drained
# and completed in 2.24 s. Used here as the budget the recycle must fit inside.
SPIKE_RECYCLE_S = 2.24


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequentialIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


def mint_identity(pid: int, ppid: int, session_uuid: str, *, seconds: int = 0) -> ClientIdentity:
    """Mint an identity exactly the way an MCP server process does at startup.

    server.py:1210 uses os.getpid(), os.getppid(), datetime.now(utc) and uuid4().
    Every one of those four is different in a freshly spawned worker; only platform
    and task_label survive on their own. The arguments here stand in for the four.
    """
    stamp = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return ClientIdentity(
        platform="claude",
        pid=pid,
        ppid=ppid,
        started_at_utc=stamp.isoformat().replace("+00:00", "Z"),
        session_id=session_uuid,
        task_label="lease rehearsal",
    )


class Rehearsal:
    def __init__(self) -> None:
        self.checks: list[tuple[bool, str, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((bool(ok), label, detail))
        return bool(ok)

    def equals(self, label: str, got: object, want: object) -> bool:
        return self.check(label, got == want, f"got {got!r}, want {want!r}")

    def report(self) -> int:
        width = max(len(label) for _, label, _ in self.checks)
        for ok, label, detail in self.checks:
            mark = "OK  " if ok else "FALLA"
            line = f"  {mark} {label.ljust(width)}"
            if detail and not ok:
                line += f"   <- {detail}"
            print(line)
        failed = [label for ok, label, _ in self.checks if not ok]
        print()
        total = len(self.checks)
        if failed:
            print(f"VEREDICTO: NO PASA. {len(failed)} de {total} en rojo.")
            for label in failed:
                print(f"  - {label}")
            return 1
        print(f"VEREDICTO: PASA. {total} comprobaciones, todas verdes.")
        return 0


def new_coordinator(clock: FakeClock) -> SessionCoordinator:
    return SessionCoordinator(
        time_fn=clock,
        token_fn=SequentialIds("token"),
        id_fn=SequentialIds("lease"),
        audit=lambda _event: None,
        cleanup=lambda *_args: {},
    )


def main() -> int:
    r = Rehearsal()

    # The three shapes a recycled worker can take.
    OWNER_UUID = "11111111111111111111111111111111"
    w0 = mint_identity(pid=1000, ppid=900, session_uuid=OWNER_UUID, seconds=0)
    # A: the worker just starts up normally -> everything new.
    w1_naive = mint_identity(pid=2000, ppid=1500, session_uuid="22222222222222222222222222222222", seconds=30)
    # B: the supervisor carries only the session_id across.
    w1_partial = mint_identity(pid=2000, ppid=1500, session_uuid=OWNER_UUID, seconds=30)
    # C: the supervisor carries the whole identity across, verbatim.
    w1_frozen = w0

    print("=== Terreno ===")
    print(f"  identidad congelada == la original: {w1_frozen == w0}")
    print(f"  parcial == la original:             {w1_partial == w0}")
    print(f"  ingenua == la original:             {w1_naive == w0}")
    print()

    # ------------------------------------------------------------------ A
    # El defecto es real, no supuesto.
    clock = FakeClock()
    c = new_coordinator(clock)
    status, active = c.acquire(w0, "rehearsal")
    r.equals("A0  el trabajador viejo tiene el lease", status, 200)
    token = active["lease_token"]
    lease_id = active["lease_id"]

    r.check("A1  y puede mutar", c.authorize(w0, token, "world_spawn").allowed)

    naive = c.authorize(w1_naive, token, "world_spawn")
    r.check("A2  reciclo ingenuo: NO puede mutar", not naive.allowed)
    r.equals("A3  y el motivo es lease_invalid", naive.error, "lease_invalid")
    r.check(
        "A4  el lease no ha muerto, solo es inalcanzable",
        c.authorize(w0, token, "world_spawn").allowed,
    )

    # ------------------------------------------------------------------ B
    # El congelado PARCIAL no solo no basta: deja al trabajador sin salida.
    partial = c.authorize(w1_partial, token, "world_spawn")
    r.check("B1  congelado parcial: NO puede mutar", not partial.allowed)
    r.equals("B2  tambien lease_invalid", partial.error, "lease_invalid")

    reacquire_status, reacquire = c.acquire(w1_partial, "recovering")
    r.equals("B3  ni puede pedir uno nuevo: 403", reacquire_status, 403)
    r.equals("B4  guardia de colision identity_mismatch", reacquire.get("error"), "identity_mismatch")

    release_status, _ = c.release(w1_partial, token)
    r.check("B5  ni puede soltar el lease colgado", release_status != 200,
            f"release devolvio {release_status}")

    # ------------------------------------------------------------------ C
    # El congelado TOTAL funciona: es el mismo lease, no uno nuevo.
    frozen = c.authorize(w1_frozen, token, "world_spawn")
    r.check("C1  congelado total: puede mutar", frozen.allowed)
    r.equals("C2  y es EL MISMO lease, no otro", frozen.lease_id, lease_id)

    hb_status, hb = c.heartbeat(w1_frozen, token)
    r.equals("C3  el trabajador nuevo puede latir", hb_status, 200)

    snapshot = c.status(w1_frozen)
    r.equals("C4  el status lo ve como dueno", snapshot["self"]["state"], "active")
    r.check("C5  y no hay sesion fantasma en cola",
            not snapshot.get("queue"), f"queue={snapshot.get('queue')!r}")

    # ------------------------------------------------------------------ D
    # Fail-closed: la identidad congelada no es una llave maestra.
    thief = mint_identity(pid=3000, ppid=2500, session_uuid="33333333333333333333333333333333", seconds=45)
    stolen = c.authorize(thief, token, "world_spawn")
    r.check("D1  token robado por un ajeno: denegado", not stolen.allowed)
    r.equals("D2  con lease_invalid", stolen.error, "lease_invalid")

    # Adivinar el session_id no basta: la igualdad es por VALOR de los seis campos.
    guesser = mint_identity(pid=3001, ppid=2501, session_uuid=OWNER_UUID, seconds=45)
    guessed = c.authorize(guesser, token, "world_spawn")
    r.check("D3  acertar el session_id tampoco basta", not guessed.allowed)

    release_status, _ = c.release(w1_frozen, token)
    r.equals("D4  el trabajador nuevo suelta limpio", release_status, 200)
    replay = c.authorize(w1_frozen, token, "world_spawn")
    r.check("D5  y el token soltado ya no vale (replay)", not replay.allowed)

    # ------------------------------------------------------------------ E
    # El presupuesto: el reciclo corre contra el TTL, sin nadie latiendo.
    clock = FakeClock()
    c = new_coordinator(clock)
    status, active = c.acquire(w0, "budget")
    token = active["lease_token"]

    # E1: latir justo antes de reciclar deja el TTL entero para el reciclo.
    c.heartbeat(w0, token)
    clock.advance(SPIKE_RECYCLE_S)
    r.check(
        f"E1  reciclo de {SPIKE_RECYCLE_S}s cabe en el TTL",
        c.authorize(w1_frozen, token, "world_spawn").allowed,
    )
    margin = SESSION_TTL_S / SPIKE_RECYCLE_S
    r.check(f"E2  margen medido x{margin:.0f} (TTL {SESSION_TTL_S}s)", margin >= 10.0,
            f"margen {margin:.1f}x")

    # E3: el modo de fallo. Un reciclo que se pasa del TTL con rival encolado
    # entrega la caja, y la identidad congelada vuelve a un lease que ya no es suyo.
    clock = FakeClock()
    c = new_coordinator(clock)
    _, active = c.acquire(w0, "overrun")
    token = active["lease_token"]
    rival = mint_identity(pid=4000, ppid=3500, session_uuid="44444444444444444444444444444444", seconds=60)
    queued_status, queued = c.acquire(rival, "waiting")
    r.equals("E3  hay un rival esperando en la cola", queued_status, 202)

    # A live queued session polls; it does not go silent. session_wait touches its
    # ticket (session_coordination.py:960), so poll at the heartbeat cadence the
    # client supervisor already uses (lease_supervisor.py: HEARTBEAT_INTERVAL_S=45).
    elapsed = 0.0
    while elapsed < SESSION_TTL_S + 1.0:
        step = min(45.0, SESSION_TTL_S + 1.0 - elapsed)
        clock.advance(step)
        elapsed += step
        c.wait(rival, queued["ticket"], 0.0)

    overrun = c.authorize(w1_frozen, token, "world_spawn")
    r.check("E4  reciclo que se pasa del TTL: pierde la caja", not overrun.allowed)
    grant_status, grant = c.wait(rival, queued["ticket"], 0.0)
    r.equals("E5  y el rival se la ha llevado", grant_status, 200)
    r.equals("E6  el rival esta activo", grant.get("status"), "active")

    # Second-order, found while fixing E5: the queue runs on the SAME TTL clock.
    # A rival that goes silent for the whole overrun loses its place too -- so an
    # overrun does not simply hand the box over, it can empty the queue as well.
    clock = FakeClock()
    c = new_coordinator(clock)
    _, active = c.acquire(w0, "silent-queue")
    silent = mint_identity(pid=5000, ppid=4500, session_uuid="55555555555555555555555555555555", seconds=90)
    _, silent_ticket = c.acquire(silent, "waiting quietly")
    clock.advance(SESSION_TTL_S + 1.0)
    silent_status, silent_payload = c.wait(silent, silent_ticket["ticket"], 0.0)
    r.equals("E7  un rival callado pierde su ticket", silent_status, 403)
    r.equals("E8  con ticket_invalid", silent_payload.get("error"), "ticket_invalid")

    # ------------------------------------------------------------------ F
    # El portador. Hasta aqui "congelada" ha sido el MISMO objeto de Python, y eso
    # es una tautologia: w0 == w0 siempre. En produccion la identidad cruza un
    # limite de proceso serializada (fichero de traspaso, variable de entorno,
    # argv), asi que lo que hay que probar es la identidad RECONSTRUIDA.
    clock = FakeClock()
    c = new_coordinator(clock)
    _, active = c.acquire(w0, "carrier")
    token = active["lease_token"]

    carried = ClientIdentity.from_payload(json.loads(json.dumps(w0.to_payload())))
    r.check("F1  identidad reconstruida == original", carried == w0,
            f"{carried!r}")
    r.check("F2  y con ella el token SIGUE valiendo",
            c.authorize(carried, token, "world_spawn").allowed)
    r.check("F3  no es el mismo objeto (no es tautologia)", carried is not w0)

    # Una variable de entorno entrega texto. from_payload rechaza pid en str
    # (session_coordination.py:79), asi que el portador tiene que re-tipar.
    stringly = w0.to_payload()
    stringly["pid"] = str(stringly["pid"])
    try:
        ClientIdentity.from_payload(stringly)
        r.check("F4  pid como texto: rechazado (fail-closed)", False, "lo acepto")
    except ValueError as exc:
        r.equals("F4  pid como texto: rechazado (fail-closed)", str(exc), "invalid_identity")

    # Lo que el portador tiene que llevar, exactamente. Son dos secretos distintos:
    # la identidad (estos seis campos) y el lease_token (secrets.token_urlsafe(32),
    # session_coordination.py:206), que NO viaja dentro de la identidad.
    r.equals(
        "F5  la identidad son exactamente 6 campos",
        sorted(w0.to_payload()),
        ["pid", "platform", "ppid", "session_id", "started_at_utc", "task_label"],
    )
    r.check("F6  el token viaja aparte, no dentro", "lease_token" not in w0.to_payload())

    print("=== Comprobaciones ===")
    return r.report()


if __name__ == "__main__":
    raise SystemExit(main())
