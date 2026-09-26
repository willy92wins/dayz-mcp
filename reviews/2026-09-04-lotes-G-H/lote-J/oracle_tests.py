# -*- coding: utf-8 -*-
"""Oracle for lote J: (A) the daemon's own cleanup survives the owner fence; (B) a granted box
lease adopts the box's ownerless run.

Run:  cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 <venv-python> ../gate/oracle_tests.py [--only PREFIX]

Load-bearing design notes:
- The daemon under test is wired exactly like production: daemon.build_server_state(...) +
  loopback.create_http_server(...) + daemon.make_status_provider(...). Nothing is imported from the
  write-set; tests/fence_helpers.py (outside the write-set, sealed by the receipt) binds the peers.
- Every check drives the daemon over HTTP the way a real MCP client and a real game peer do
  (/session/*, /lifecycle/*, /enqueue, /poll, /result, /await).
- A check that cannot reach its subject reports UNMET, never PASS.
- Calibrated 2026-09-04 against the product at bdeb87f (see GATES.md "Estado medido antes de
  delegar"): the JA1/JA3 and JB1..JB6/JB8 checks are RED for the documented reason; JA2, JA4,
  JA5 and JB7 are green and MUST stay green -- they are the negative controls (the fence still
  holds for everything that is not the daemon's own cleanup; a foreign session still cannot
  adopt). JA2 carries a positive control: the same read succeeds once the fence is patched out,
  which proves the check observes the fence and not something else.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


TOOLS = Path.cwd().resolve()
assert (TOOLS / "dayz_mcp").is_dir() and (TOOLS / "tests").is_dir(), "run from <ws>/tools"

from dayz_mcp import daemon, loopback  # noqa: E402
from dayz_mcp.process_lifecycle import ProcessRecord  # noqa: E402
from dayz_mcp.server import ServerConfig  # noqa: E402
from dayz_mcp.session_coordination import ClientIdentity  # noqa: E402
from tests.fence_helpers import INST_CLIENT, INST_SERVER, bind_both_peers  # noqa: E402

RUN_ID = "test-run"
SESSION_A = "session-A-0123456789abcdef-lote-j"
SESSION_B = "session-B-fedcba9876543210-lote-j"


# ---------------------------------------------------------------- HTTP plumbing (client side)
def http(base, method, path, key, payload=None, query=None, timeout=5.0):
    params = dict(query or {})
    params["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # No redirect_stdout here: peers call this from threads, and a nested global redirect
    # restored out of order leaves sys.stdout pointing at a StringIO for good (measured while
    # calibrating: the whole report vanished and the process exited 1 in silence).
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            exc.close()


class Peer:
    """Fake game peer: polls /poll with its bound instance, answers every command via /result."""

    def __init__(self, base: str, key: str, role: str) -> None:
        self.base, self.key, self.role = base, key, role
        self.inst = INST_SERVER if role == "server" else INST_CLIENT
        self.seen: list[tuple[int, str]] = []
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def pause(self) -> None:
        self._paused.set()
        time.sleep(0.05)

    def resume(self) -> None:
        self._paused.clear()

    def names(self) -> list[str]:
        with self._lock:
            return [cmd for _, cmd in self.seen]

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.01)
                continue
            try:
                _st, body = http(self.base, "GET", "/poll", self.key,
                                 query={"peer": self.role, "inst": self.inst})
                new = [(int(c["id"]), str(c["cmd"])) for c in body.get("commands", [])]
                with self._lock:
                    self.seen.extend(new)
                for cid, cmd in new:
                    http(self.base, "POST", "/result", self.key,
                         payload={"id": cid, "ok": 1, "cmd": cmd}, query={"inst": self.inst})
            except Exception:
                pass
            time.sleep(0.01)


class Client:
    def __init__(self, d: "Daemon", platform: str, session_id: str, pid: int) -> None:
        self.d = d
        self.identity = ClientIdentity(platform, pid, 1, "2026-07-15T00:00:00Z", session_id, "lote-j")

    def _post(self, path: str, payload: dict, timeout: float = 5.0):
        body = {"identity": self.identity.to_payload()}
        body.update(payload)
        return http(self.d.base, "POST", path, self.d.key, payload=body, timeout=timeout)

    def session(self, action: str, timeout: float = 5.0, **payload):
        return self._post(f"/session/{action}", payload, timeout)

    def lifecycle(self, action: str, **payload):
        return self._post(f"/lifecycle/{action}", payload)

    def enqueue(self, cmd: str, args: dict, peer: str, lease_token: str | None = None, **extra):
        body = {"cmd": cmd, "args": args, "peer": peer, "lease_token": lease_token,
                "operation_timeout_s": 5.0}
        body.update(extra)
        return self._post("/enqueue", body)

    def box_run(self) -> dict | None:
        _st, status = self.session("status")
        for row in (status.get("box") or {}).get("runs", []):
            if row.get("run_id") == RUN_ID:
                return row
        return None


class Daemon:
    """Production wiring on a loopback port with an isolated LOCALAPPDATA."""

    def __init__(self, tmp: str, *, bind: bool = True, key: str = "jkey") -> None:
        self.key = key
        self.config = ServerConfig(mode="daemon", key=key, port=0, log_sink=lambda _m: None)
        with patch.dict(os.environ, {"LOCALAPPDATA": tmp}), patch.object(
            daemon.orphan_guard, "snapshot_retail_processes",
            return_value={"known": True, "processes": []},
        ), patch.object(daemon, "_ensure_identity_migration", return_value=None):
            self.state = daemon.build_server_state(self.config, key, activate_coordination=True)
        if bind:
            bind_both_peers(self.state)  # RUNNING_IDLE, no owner, processes = this interpreter
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _m: None, reclaim_orphans=False,
            status_provider=daemon.make_status_provider(self.config, self.state))
        self.httpd.daemon_threads = False
        self.port = int(self.httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.peers: list[Peer] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            self.httpd.serve_forever(poll_interval=0.01)
        except Exception:
            pass

    def peer(self, role: str) -> Peer:
        p = Peer(self.base, self.key, role)
        p.start()
        self.peers.append(p)
        return p

    def stop(self) -> None:
        for p in self.peers:
            p.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=2.0)


def wait_until(pred, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


@contextlib.contextmanager
def daemon_fixture(*, bind: bool = True):
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    d = Daemon(tmp.name, bind=bind)
    try:
        yield d
    finally:
        d.stop()
        time.sleep(0.05)
        tmp.cleanup()


def dead_pid() -> int:
    import psutil  # the guard's own dependency

    for candidate in range(4_000_000, 4_004_000, 4):
        if not psutil.pid_exists(candidate):
            return candidate
    raise RuntimeError("no free pid found")


def acquire_active(client: Client, purpose: str = "drive") -> dict:
    st, acq = client.session("acquire", purpose=purpose)
    if st != 200 or acq.get("status") != "active" or not acq.get("lease_token"):
        raise RuntimeError(f"acquire devolvio {st} {acq!r}")
    return acq


CHECKS: list[tuple[str, object]] = []


def register(name: str):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


# ================================================================ A: cleanup survives the fence
@register("JA1-LIMPIEZA-INTERNA-LLEGA-AL-PEER tras session_release con vehiculo activo el peer client recibe vehicle_release")
def ja1() -> None:
    name = "JA1-LIMPIEZA-INTERNA-LLEGA-AL-PEER tras session_release con vehiculo activo el peer client recibe vehicle_release"
    with daemon_fixture() as d:
        client_peer = d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        acq = acquire_active(a)
        tok = acq["lease_token"]
        st, ad = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        if ad.get("ok") is not True:
            raise RuntimeError(f"adopt explicito devolvio {st} {ad!r}")
        st, enq = a.enqueue("vehicle_control", {"throttle": 1.0}, "client", tok)
        if st != 200:
            raise RuntimeError(f"enqueue vehicle_control devolvio {st} {enq!r}")
        if not wait_until(lambda: client_peer.names() == ["vehicle_control"]):
            raise RuntimeError(f"el peer no recibio vehicle_control: {client_peer.names()}")
        st, rel = a.session("release", lease_token=tok)
        cleanup = rel.get("cleanup") or {}
        if st != 200 or cleanup.get("vehicle_release_enqueued") != 1:
            raise RuntimeError(f"release devolvio {st} {rel!r} (vehicle_release_enqueued != 1)")
        delivered = wait_until(lambda: client_peer.names() == ["vehicle_control", "vehicle_release"], 2.5)
        check(name, delivered,
              f"peer client vio {client_peer.names()} tras el release (cleanup={cleanup}) -- la "
              f"limpieza vehicle_release que encola el release del dueno es una orden interna del "
              f"daemon: el cerco P6 protege de sesiones sin dueno, no de la limpieza del propio "
              f"daemon; drenarla deja el vehiculo del juego bajo control fantasma (fb-...-f7af)")


@register("JA2-EL-CERCO-SIGUE-PARA-LO-NORMAL una lectura sobre el run cercado sigue siendo run_not_owned (control positivo: pasa con el cerco parcheado)")
def ja2() -> None:
    name = "JA2-EL-CERCO-SIGUE-PARA-LO-NORMAL una lectura sobre el run cercado sigue siendo run_not_owned (control positivo: pasa con el cerco parcheado)"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        b = Client(d, "claude", SESSION_B, 12)
        tok = acquire_active(a)["lease_token"]
        st, ad = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        if ad.get("ok") is not True:
            raise RuntimeError(f"adopt explicito devolvio {st} {ad!r}")
        st, rel = a.session("release", lease_token=tok)
        if st != 200:
            raise RuntimeError(f"release devolvio {st} {rel!r}")
        st_read, read = b.enqueue("camera_get", {}, "client", None)
        with patch.object(loopback.ServerState, "_run_is_fenced", return_value=False), patch.object(
            loopback.ServerState, "_durable_run_state", return_value="RUNNING"
        ):
            st_ctl, ctl = b.enqueue("camera_get", {}, "client", None)
        fenced = st_read == 409 and read.get("error") == "run_not_owned"
        control = st_ctl == 200
        check(name, fenced and control,
              f"lectura sobre RUNNING_IDLE cercado: {st_read} {read!r} (esperado 409 run_not_owned); "
              f"control positivo con el cerco parcheado: {st_ctl} {ctl!r} (esperado 200) -- si el "
              f"control no pasa, el check no esta observando el cerco")


@register("JA3-DRENA-LO-NORMAL-Y-ENTREGA-LO-INTERNO una mutacion encolada antes del release se descarta run_not_owned y vehicle_release se entrega")
def ja3() -> None:
    name = "JA3-DRENA-LO-NORMAL-Y-ENTREGA-LO-INTERNO una mutacion encolada antes del release se descarta run_not_owned y vehicle_release se entrega"
    with daemon_fixture() as d:
        client_peer = d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        tok = acquire_active(a)["lease_token"]
        st, ad = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        if ad.get("ok") is not True:
            raise RuntimeError(f"adopt explicito devolvio {st} {ad!r}")
        st, enq = a.enqueue("vehicle_control", {"throttle": 1.0}, "client", tok)
        if st != 200:
            raise RuntimeError(f"enqueue #1 devolvio {st} {enq!r}")
        if not wait_until(lambda: client_peer.names() == ["vehicle_control"]):
            raise RuntimeError(f"el peer no recibio vehicle_control #1: {client_peer.names()}")
        client_peer.pause()
        st, enq2 = a.enqueue("vehicle_control", {"throttle": 0.5}, "client", tok)
        if st != 200:
            raise RuntimeError(f"enqueue #2 (peer en pausa) devolvio {st} {enq2!r}")
        id2 = int(enq2["id"])
        st, rel = a.session("release", lease_token=tok)
        cleanup = rel.get("cleanup") or {}
        if st != 200 or cleanup.get("vehicle_release_enqueued") != 1:
            raise RuntimeError(f"release devolvio {st} {rel!r}")
        client_peer.resume()
        delivered = wait_until(lambda: "vehicle_release" in client_peer.names(), 2.5)
        time.sleep(0.2)
        names = client_peer.names()
        st_aw, aw = http(d.base, "GET", "/await", d.key, query={"id": id2})
        result = (aw.get("result") or {}) if isinstance(aw, dict) else {}
        # Calibrated: the owner's own pending mutation is cancelled by the release itself
        # (cancel_owner_pending -> "owner_release") before the fence drains what is left
        # ("run_not_owned"). Either way it must be a failed result, never a delivery.
        discarded = aw.get("status") == "done" and result.get("ok") is False \
            and result.get("error") in {"owner_release", "run_not_owned"}
        check(name, delivered and names == ["vehicle_control", "vehicle_release"] and discarded,
              f"peer vio {names} (esperado [vehicle_control, vehicle_release]); await #{id2}: {st_aw} "
              f"{aw!r} (esperado done/ok False/owner_release|run_not_owned) -- el cerco drena lo que "
              f"NO es limpieza del daemon y entrega lo que si lo es")


@register("JA4-LA-LIMPIEZA-ENTREGADA-NO-ACREDITA-ACTIVIDAD tras entregar vehicle_release el run sigue RUNNING_IDLE y su actividad no es recent")
def ja4() -> None:
    name = "JA4-LA-LIMPIEZA-ENTREGADA-NO-ACREDITA-ACTIVIDAD tras entregar vehicle_release el run sigue RUNNING_IDLE y su actividad no es recent"
    with daemon_fixture() as d:
        client_peer = d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        tok = acquire_active(a)["lease_token"]
        st, ad = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        if ad.get("ok") is not True:
            raise RuntimeError(f"adopt explicito devolvio {st} {ad!r}")
        st, enq = a.enqueue("vehicle_control", {"throttle": 1.0}, "client", tok)
        if st != 200 or not wait_until(lambda: client_peer.names() == ["vehicle_control"]):
            raise RuntimeError(f"vehicle_control no entregado: {st} {enq!r} {client_peer.names()}")
        before = a.box_run()
        st, rel = a.session("release", lease_token=tok)
        if st != 200 or (rel.get("cleanup") or {}).get("vehicle_release_enqueued") != 1:
            raise RuntimeError(f"release devolvio {st} {rel!r}")
        wait_until(lambda: "vehicle_release" in client_peer.names(), 2.5)
        time.sleep(0.3)
        row = a.box_run() or {}
        check(name, row.get("state") == "RUNNING_IDLE" and row.get("activity_state") != "recent",
              f"antes del release: {before}; despues de entregar la limpieza: {row} -- una orden "
              f"interna no es actividad de una sesion: no puede sacar al run de RUNNING_IDLE ni "
              f"pintarlo recent")


@register("JA5-INTERNAL-NO-SE-ACEPTA-POR-HTTP un /enqueue con internal:true sobre el run cercado sigue siendo run_not_owned; internal=True tiene un unico llamador (cleanup_owner)")
def ja5() -> None:
    name = "JA5-INTERNAL-NO-SE-ACEPTA-POR-HTTP un /enqueue con internal:true sobre el run cercado sigue siendo run_not_owned; internal=True tiene un unico llamador (cleanup_owner)"
    src = (TOOLS / "dayz_mcp" / "loopback.py").read_text(encoding="utf-8")
    hits = [m.start() for m in re.finditer(r"internal=True", src)]
    others = []
    for p in sorted((TOOLS / "dayz_mcp").glob("*.py")):
        if p.name != "loopback.py" and "internal=True" in p.read_text(encoding="utf-8"):
            others.append(p.name)
    enclosing = []
    for pos in hits:
        defs = list(re.finditer(r"^    def (\w+)\(", src[:pos], flags=re.M))
        enclosing.append(defs[-1].group(1) if defs else "?")
    static_ok = len(hits) == 1 and enclosing == ["cleanup_owner"] and not others
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        b = Client(d, "claude", SESSION_B, 12)
        tok = acquire_active(a)["lease_token"]
        st, ad = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        if ad.get("ok") is not True:
            raise RuntimeError(f"adopt explicito devolvio {st} {ad!r}")
        st, rel = a.session("release", lease_token=tok)
        if st != 200:
            raise RuntimeError(f"release devolvio {st} {rel!r}")
        st_i, resp = b.enqueue("camera_get", {}, "client", None, internal=True)
        runtime_ok = st_i == 409 and resp.get("error") == "run_not_owned"
    check(name, static_ok and runtime_ok,
          f"internal=True en dayz_mcp: {len(hits)} en loopback.py dentro de {enclosing}, otros ficheros "
          f"{others or 'ninguno'}; /enqueue con internal:true sobre el run cercado: {st_i} {resp!r} "
          f"(esperado 409 run_not_owned) -- la exencion es del daemon, no de quien lo pida")


# ============================================================ B: a granted lease adopts the run
@register("JB1-ADQUIRIR-ADOPTA /session/acquire sobre la caja con un run RUNNING_IDLE sin dueno lo adopta y lo declara en adopted_run")
def jb1() -> None:
    name = "JB1-ADQUIRIR-ADOPTA /session/acquire sobre la caja con un run RUNNING_IDLE sin dueno lo adopta y lo declara en adopted_run"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        st, acq = a.session("acquire", purpose="drive")
        if st != 200 or acq.get("status") != "active":
            raise RuntimeError(f"acquire devolvio {st} {acq!r}")
        adopted = acq.get("adopted_run")
        row = a.box_run() or {}
        expected = {"ok": True, "run_id": RUN_ID, "state": "RUNNING", "dispatchable": True}
        ok = isinstance(adopted, dict) and all(adopted.get(k) == v for k, v in expected.items()) \
            and row.get("state") == "RUNNING" and row.get("owner_session") == SESSION_A[:12]
        check(name, ok,
              f"acquire -> adopted_run={adopted!r}; box run={row} -- sin esto ningun run llega a tener "
              f"dueno con las tools (dayz_test_run lo deja RUNNING_IDLE al soltar su lease interno) y "
              f"P6 estricto lo cerca para siempre: livelock (fb-...-f70b, fb-...-5ca1)")


@register("JB2-DESPACHO-TRAS-ADQUIRIR con el run adoptado por el acquire una lectura y una mutacion se entregan al peer")
def jb2() -> None:
    name = "JB2-DESPACHO-TRAS-ADQUIRIR con el run adoptado por el acquire una lectura y una mutacion se entregan al peer"
    with daemon_fixture() as d:
        client_peer = d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        tok = acquire_active(a)["lease_token"]
        st_r, read = a.enqueue("camera_get", {}, "client", tok)
        st_m, mut = a.enqueue("vehicle_control", {"throttle": 1.0}, "client", tok)
        delivered = wait_until(lambda: client_peer.names() == ["camera_get", "vehicle_control"], 2.5)
        check(name, st_r == 200 and st_m == 200 and delivered,
              f"lectura: {st_r} {read!r}; mutacion: {st_m} {mut!r}; peer vio {client_peer.names()} -- "
              f"el oraculo de las fichas: tras lanzar y adquirir, el puente despacha y run_not_owned "
              f"no sube")


@register("JB3-LA-CONCESION-EN-COLA-ADOPTA el siguiente de la FIFO recibe el lease por /session/wait y adopta el run que el anterior dejo RUNNING_IDLE")
def jb3() -> None:
    name = "JB3-LA-CONCESION-EN-COLA-ADOPTA el siguiente de la FIFO recibe el lease por /session/wait y adopta el run que el anterior dejo RUNNING_IDLE"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        b = Client(d, "claude", SESSION_B, 12)
        tok_a = acquire_active(a)["lease_token"]
        # /session/enqueue validates operation_id (control_client sends a uuid4 per operation).
        st, queued = b.session("enqueue", purpose="next", operation_id=str(uuid.uuid4()))
        ticket = queued.get("ticket")
        if queued.get("status") != "queued" or not ticket:
            raise RuntimeError(f"enqueue de B devolvio {st} {queued!r}")
        st, rel = a.session("release", lease_token=tok_a)
        if st != 200:
            raise RuntimeError(f"release de A devolvio {st} {rel!r}")
        st, granted = b.session("wait", timeout=20.0, ticket=ticket, timeout_s=10.0)
        adopted = granted.get("adopted_run") if isinstance(granted, dict) else None
        row = b.box_run() or {}
        ok = st == 200 and granted.get("status") == "active" and isinstance(adopted, dict) \
            and adopted.get("ok") is True and adopted.get("run_id") == RUN_ID \
            and row.get("owner_session") == SESSION_B[:12] and row.get("state") == "RUNNING"
        check(name, ok,
              f"wait de B -> {st} status={granted.get('status')!r} adopted_run={adopted!r}; box run={row} "
              f"-- la adopcion va donde se CONCEDE el lease, no solo donde se pide: la cola de la caja "
              f"es el camino normal de las sesiones que esperan")


@register("JB4-SIN-RUN-ADOPTABLE-CONCEDE-SIN-ADOPTAR con la caja vacia el acquire concede el lease y declara adopted_run null")
def jb4() -> None:
    name = "JB4-SIN-RUN-ADOPTABLE-CONCEDE-SIN-ADOPTAR con la caja vacia el acquire concede el lease y declara adopted_run null"
    with daemon_fixture(bind=False) as d:
        a = Client(d, "codex", SESSION_A, 11)
        st, acq = a.session("acquire", purpose="launch")
        ok = st == 200 and acq.get("status") == "active" and "adopted_run" in acq and acq["adopted_run"] is None
        check(name, ok,
              f"acquire sin run -> {st} {({k: acq.get(k) for k in ('status', 'adopted_run')})!r} "
              f"(esperado active + adopted_run: null) -- el lease sigue siendo de la caja; la clave "
              f"presente y nula dice 'no habia nada que adoptar', distinto de 'no lo intente'")


@register("JB5-PROCESOS-MUERTOS-CONCEDE-SIN-ADOPTAR un run RUNNING_IDLE cuyos procesos ya no existen no se adopta, el lease se concede igual y adopted_run trae el motivo")
def jb5() -> None:
    name = "JB5-PROCESOS-MUERTOS-CONCEDE-SIN-ADOPTAR un run RUNNING_IDLE cuyos procesos ya no existen no se adopta, el lease se concede igual y adopted_run trae el motivo"
    with daemon_fixture() as d:
        manifest = d.state.lifecycle.manifest
        run = manifest.get(RUN_ID)
        pid = dead_pid()
        run.processes = [ProcessRecord(pid, "2026-08-18T00:00:00.000000Z", "a" * 64, "b" * 64, "server",
                                       identity_scheme="psutil-argv-v2")]
        manifest.replace(run)
        a = Client(d, "codex", SESSION_A, 11)
        st, acq = a.session("acquire", purpose="drive")
        adopted = acq.get("adopted_run")
        row = a.box_run() or {}
        ok = st == 200 and acq.get("status") == "active" and isinstance(adopted, dict) \
            and adopted.get("ok") is False and isinstance(adopted.get("error"), str) and adopted.get("error") \
            and adopted.get("run_id") == RUN_ID and row.get("state") == "RUNNING_IDLE" and row.get("owner_session") is None
        check(name, ok,
              f"acquire con pid muerto {pid} -> {st} status={acq.get('status')!r} adopted_run={adopted!r}; "
              f"box run={row} -- adoptar exige >=1 proceso vivo y propio segun el guard (P19''); el "
              f"lease de la caja no depende de ello y el motivo viaja en la respuesta")


@register("JB6-ADOPT-EXPLICITO-IDEMPOTENTE /lifecycle/adopt por el mismo dueno (misma sesion, mismo lease) devuelve ok/RUNNING/dispatchable en vez de run_not_adoptable")
def jb6() -> None:
    name = "JB6-ADOPT-EXPLICITO-IDEMPOTENTE /lifecycle/adopt por el mismo dueno (misma sesion, mismo lease) devuelve ok/RUNNING/dispatchable en vez de run_not_adoptable"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        acq = acquire_active(a)
        tok = acq["lease_token"]
        st1, first = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        st2, second = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        row = a.box_run() or {}
        ok = all(r.get("ok") is True and r.get("state") == "RUNNING" and r.get("dispatchable") is True
                 for r in (first, second)) and row.get("owner_session") == SESSION_A[:12]
        check(name, ok,
              f"acquire adopted_run={acq.get('adopted_run')!r}; adopt #1 -> {st1} {first!r}; adopt #2 -> "
              f"{st2} {second!r}; box run={row} -- dayz_test_worker adopta explicitamente antes de "
              f"extender (mode=client) o parar (kill): si la adopcion del acquire lo convierte en "
              f"run_not_adoptable, esas dos rutas se rompen")


@register("JB7-UN-AJENO-NO-ADOPTA otra sesion sin lease no puede adoptar el run que el titular posee")
def jb7() -> None:
    name = "JB7-UN-AJENO-NO-ADOPTA otra sesion sin lease no puede adoptar el run que el titular posee"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        b = Client(d, "claude", SESSION_B, 12)
        tok = acquire_active(a)["lease_token"]
        st_a, ad_a = a.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        st_b, ad_b = b.lifecycle("adopt", lease_token=None, run_id=RUN_ID)
        st_c, ad_c = b.lifecycle("adopt", lease_token=tok, run_id=RUN_ID)
        row = a.box_run() or {}
        ok = ad_b.get("ok") is not True and ad_c.get("ok") is not True and row.get("owner_session") == SESSION_A[:12]
        check(name, ok,
              f"adopt de B sin lease -> {st_b} {ad_b!r}; adopt de B con el token de A -> {st_c} {ad_c!r}; "
              f"box run={row} -- la adopcion sigue exigiendo el lease del llamante")


@register("JB8-EL-RELEASE-DEVUELVE-EL-RUN tras adoptar por el acquire, session_release lista el run en runs_released y lo deja RUNNING_IDLE")
def jb8() -> None:
    name = "JB8-EL-RELEASE-DEVUELVE-EL-RUN tras adoptar por el acquire, session_release lista el run en runs_released y lo deja RUNNING_IDLE"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        tok = acquire_active(a)["lease_token"]
        st, rel = a.session("release", lease_token=tok)
        cleanup = rel.get("cleanup") or {}
        row = a.box_run() or {}
        ok = st == 200 and cleanup.get("runs_released") == [RUN_ID] and row.get("state") == "RUNNING_IDLE" \
            and row.get("owner_session") is None
        check(name, ok,
              f"release -> {st} cleanup={cleanup!r}; box run={row} -- runs_released: [] era el sintoma "
              f"que las fichas describen ('el run nunca estuvo asociado')")


# ============================================ round 2: Codex findings F-01 (ALTA), F-02 (MEDIA), F-03 (BAJA)
@register("JB9-MANIFIESTO-ILEGIBLE-NO-ES-NULL si list_runs falla, el acquire concede el lease y adopted_run declara run_state_unavailable (null es solo 'enumeracion valida sin candidato')")
def jb9() -> None:
    name = "JB9-MANIFIESTO-ILEGIBLE-NO-ES-NULL si list_runs falla, el acquire concede el lease y adopted_run declara run_state_unavailable (null es solo 'enumeracion valida sin candidato')"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        manifest = d.state.lifecycle.manifest
        real = manifest.list_runs

        def unreadable():
            raise OSError("manifest unreadable")

        manifest.list_runs = unreadable
        try:
            st, acq = a.session("acquire", purpose="drive")
        finally:
            manifest.list_runs = real
        adopted = acq.get("adopted_run")
        ok = st == 200 and acq.get("status") == "active" and isinstance(adopted, dict) \
            and adopted.get("ok") is False and adopted.get("error") == "run_state_unavailable" \
            and adopted.get("run_id") is None
        check(name, ok,
              f"acquire con manifiesto ilegible -> {st} status={acq.get('status')!r} adopted_run={adopted!r} "
              f"(esperado ok False, error run_state_unavailable, run_id None) -- F-02: 'no pude mirar' y "
              f"'no habia nada' no pueden ser la misma respuesta")


@register("JB10-ADOPT-QUE-REVIENTA-NO-CORTA-LA-CONCESION si adopt_run lanza tras abrir la reserva, la respuesta llega entera (200 + adopted_run ok:false), no queda reserva huerfana y el lease sigue usable")
def jb10() -> None:
    name = "JB10-ADOPT-QUE-REVIENTA-NO-CORTA-LA-CONCESION si adopt_run lanza tras abrir la reserva, la respuesta llega entera (200 + adopted_run ok:false), no queda reserva huerfana y el lease sigue usable"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        manifest = d.state.lifecycle.manifest
        real_get = manifest.get

        def broken_get(run_id):
            # adopt_run must read the run by id after _authorize opened the reservation;
            # the grant helper selects by list_runs, which stays intact.
            raise OSError("manifest read failed inside adopt_run")

        manifest.get = broken_get
        transport = None
        try:
            try:
                st, acq = a.session("acquire", purpose="drive")
            except Exception as exc:  # noqa: BLE001 -- a cut response IS the failure being measured
                st, acq, transport = None, {}, f"{type(exc).__name__}: {exc}"
        finally:
            manifest.get = real_get
        active = getattr(d.state.coordination, "_active", None)
        pending_raw = getattr(active, "pending_authorizations", None) if active is not None else None
        pending = len(pending_raw) if pending_raw is not None else None
        token = acq.get("lease_token")
        st_rel, rel = (a.session("release", lease_token=token) if isinstance(token, str) and token else (None, {}))
        row = a.box_run() or {}
        adopted = acq.get("adopted_run")
        ok = st == 200 and acq.get("status") == "active" and isinstance(adopted, dict) \
            and adopted.get("ok") is False and bool(adopted.get("error")) and pending == 0 \
            and st_rel == 200 and row.get("state") == "RUNNING_IDLE" and row.get("owner_session") is None
        check(name, ok,
              f"acquire con adopt_run reventando -> {st} {({k: acq.get(k) for k in ('status', 'adopted_run')})!r} "
              f"transporte={transport!r}; reservas pendientes en el lease activo={pending!r} (esperado 0); "
              f"release -> {st_rel}; box run={row} -- F-01: el lease ya es del llamante cuando se adopta: "
              f"una excepcion ahi no puede cortar la respuesta ni dejar la reserva abierta")


@register("JC1-EL-NOMBRE-DEL-TEST-DICE-LO-QUE-AFIRMA el test de la limpieza tras publicar idle ya no se llama *_is_gone_* y su nombre dice que sobrevive")
def jc1() -> None:
    name = "JC1-EL-NOMBRE-DEL-TEST-DICE-LO-QUE-AFIRMA el test de la limpieza tras publicar idle ya no se llama *_is_gone_* y su nombre dice que sobrevive"
    src = (TOOLS / "tests" / "test_process_lifecycle.py").read_text(encoding="utf-8")
    gone = "def test_internal_cleanup_enqueued_before_release_is_gone_when_idle_published" in src
    renamed = re.search(r"def test_internal_cleanup_enqueued_before_release_\w*(survives|is_delivered)\w*\(", src)
    check(name, (not gone) and renamed is not None,
          f"nombre viejo presente={gone}; nombre nuevo={renamed.group(0) if renamed else None} -- F-03: un "
          f"mantenedor que elija tests por nombre leeria lo contrario de la asercion")


# ============================================ round 3: Codex delta findings (two MEDIA on the compensator and on malformed rows)
@register("JB11-EL-FALLO-DEL-COMPENSADOR-SE-DECLARA si adopt_run revienta y abort_reservation tambien, la reserva se cierra por otra via o la degradacion viaja en adopted_run.cleanup_degraded; y las degradaciones que devuelve abort_reservation se propagan")
def jb11() -> None:
    name = "JB11-EL-FALLO-DEL-COMPENSADOR-SE-DECLARA si adopt_run revienta y abort_reservation tambien, la reserva se cierra por otra via o la degradacion viaja en adopted_run.cleanup_degraded; y las degradaciones que devuelve abort_reservation se propagan"
    # Scenario 1: manifest.get raises inside adopt_run AND the compensator raises.
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        manifest = d.state.lifecycle.manifest
        coord = d.state.coordination
        real_get, real_abort = manifest.get, coord.abort_reservation

        def broken_get(run_id):
            raise OSError("manifest read failed inside adopt_run")

        def broken_abort(*_a, **_k):
            raise OSError("abort boom")

        manifest.get, coord.abort_reservation = broken_get, broken_abort
        transport1 = None
        try:
            try:
                st1, acq1 = a.session("acquire", purpose="drive")
            except Exception as exc:  # noqa: BLE001
                st1, acq1, transport1 = None, {}, f"{type(exc).__name__}: {exc}"
        finally:
            manifest.get, coord.abort_reservation = real_get, real_abort
        active = getattr(coord, "_active", None)
        pending_raw = getattr(active, "pending_authorizations", None) if active is not None else None
        pending1 = len(pending_raw) if pending_raw is not None else None
        adopted1 = acq1.get("adopted_run") if isinstance(acq1, dict) else None
        degraded1 = list((adopted1 or {}).get("cleanup_degraded") or []) if isinstance(adopted1, dict) else []
        token1 = acq1.get("lease_token") if isinstance(acq1, dict) else None
        st_rel1, _ = (a.session("release", lease_token=token1) if isinstance(token1, str) and token1 else (None, {}))
        ok1 = st1 == 200 and isinstance(adopted1, dict) and adopted1.get("ok") is False \
            and (pending1 == 0 or "reservation_abort_failed" in degraded1) and st_rel1 == 200
    # Scenario 2: the compensator works but reports a degradation -> it must reach adopted_run.
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        manifest = d.state.lifecycle.manifest
        coord = d.state.coordination
        real_get, real_abort = manifest.get, coord.abort_reservation

        def broken_get2(run_id):
            raise OSError("manifest read failed inside adopt_run")

        def degraded_abort(*args, **kwargs):
            base = real_abort(*args, **kwargs)
            return tuple(dict.fromkeys([*base, "audit_failed"]))

        manifest.get, coord.abort_reservation = broken_get2, degraded_abort
        try:
            try:
                st2, acq2 = a.session("acquire", purpose="drive")
            except Exception as exc:  # noqa: BLE001
                st2, acq2 = None, {"transport": f"{type(exc).__name__}: {exc}"}
        finally:
            manifest.get, coord.abort_reservation = real_get, real_abort
        adopted2 = acq2.get("adopted_run") if isinstance(acq2, dict) else None
        degraded2 = list((adopted2 or {}).get("cleanup_degraded") or []) if isinstance(adopted2, dict) else []
        ok2 = st2 == 200 and isinstance(adopted2, dict) and adopted2.get("ok") is False and "audit_failed" in degraded2
    check(name, ok1 and ok2,
          f"[1] get+abort revientan: acquire {st1} transporte={transport1!r} adopted_run={adopted1!r} pendientes={pending1!r} "
          f"release={st_rel1} (esperado 200 + ok False + (pendientes 0 o reservation_abort_failed declarado) + release 200); "
          f"[2] abort degradado: acquire {st2} adopted_run={adopted2!r} (esperado audit_failed en cleanup_degraded) -- un "
          f"cierre que no ocurrio no puede declararse como ocurrido; las degradaciones del coordinador no se tiran")


@register("JB12-FILA-MALFORMADA-NO-ES-NULL una enumeracion con filas sin esquema (state/owner_session_id/run_id) se declara run_state_unavailable, no 'sin candidato'")
def jb12() -> None:
    name = "JB12-FILA-MALFORMADA-NO-ES-NULL una enumeracion con filas sin esquema (state/owner_session_id/run_id) se declara run_state_unavailable, no 'sin candidato'"
    with daemon_fixture() as d:
        d.peer("client")
        a = Client(d, "codex", SESSION_A, 11)
        manifest = d.state.lifecycle.manifest
        real = manifest.list_runs
        manifest.list_runs = lambda: [object()]
        try:
            st, acq = a.session("acquire", purpose="drive")
        finally:
            manifest.list_runs = real
        adopted = acq.get("adopted_run")
        token = acq.get("lease_token")
        if isinstance(token, str) and token:
            a.session("release", lease_token=token)
        ok = st == 200 and acq.get("status") == "active" and isinstance(adopted, dict) \
            and adopted.get("ok") is False and adopted.get("error") == "run_state_unavailable" and adopted.get("run_id") is None
        check(name, ok,
              f"acquire con list_runs -> [object()]: {st} status={acq.get('status')!r} adopted_run={adopted!r} (esperado ok False, "
              f"run_state_unavailable, run_id None) -- una fila que no cumple el esquema no es 'ningun candidato': es una "
              f"lectura que no se puede juzgar")


# ================================================================================ runner
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run only the checks whose name starts with this prefix")
    a = ap.parse_args()
    for name, fn in CHECKS:
        if a.only and not name.startswith(a.only):
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            unmet(name, f"{type(exc).__name__}: {exc}")
    print("=" * 104)
    worst = 0
    for nm, v, dt in RESULTS:
        print(f"[{v:<5}] {nm}")
        if dt:
            print(f"          {dt[:420]}")
        if v != "PASS":
            worst = 1
    print("=" * 104)
    t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
    print(f"ORACULO-TESTS: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
    if worst == 0 and RESULTS:
        print("ORACULO-TESTS-VERDE")
    return worst


if __name__ == "__main__":
    sys.exit(main())
