# -*- coding: utf-8 -*-
"""Oracle for lote I round 2: the TESTS are the product; this measures them.

Run:  cd <ws>/tools && PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 <venv-python> ../gate/oracle_tests.py

Load-bearing design notes:
- T1 imports the candidate's own helper (tests/fence_helpers.py): the helper IS the subject.
  Everything else (lifecycle, coordinator, guard, ServerState) is built here from the frozen
  product, never from tests/.
- T2/T3 are MUTANTS: a copy of dayz_mcp/ + tests/ goes to a temp dir, one anchored text
  mutation is applied (missing anchor => UNMET, never PASS), and the single test under
  measurement runs there in a subprocess. The detector under test must go RED on the mutant.
- A check that cannot reach its subject reports UNMET, never PASS.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def unmet(name: str, detail: str) -> None:
    RESULTS.append((name, "UNMET", detail))


TOOLS = Path.cwd().resolve()
assert (TOOLS / "dayz_mcp").is_dir() and (TOOLS / "tests").is_dir(), "run from <ws>/tools"
PY = sys.executable

from dayz_mcp import loopback  # noqa: E402
from dayz_mcp.native_process_guard import NativeProcessGuard  # noqa: E402
from dayz_mcp.process_lifecycle import ProcessLifecycle, ProcessRecord, RunManifestStore  # noqa: E402
from dayz_mcp.runtime_state import RuntimePaths  # noqa: E402
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator  # noqa: E402

IDENTITY = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "A", "owner")


class Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> bool:
        self.events.append(event)
        return True


class FakeGuard:
    def __init__(self) -> None:
        self.snapshots: dict[int, dict] = {}

    def snapshot(self, pid: int) -> dict:
        return dict(self.snapshots.get(pid, {"error": "identity_unavailable"}))

    def terminate(self, record: ProcessRecord) -> dict:
        return {"terminated": True}


def real_lifecycle(tmp: Path, *, guard, acquire: bool):
    """A real ProcessLifecycle + coordinator + ServerState wired both ways, like daemon.py.
    No start_run: the run must come from the candidate's helper."""
    game = tmp / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "DayZDiag_x64.exe").write_bytes(b"")
    paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                         tmp / "runtime" / "coordination.json", tmp / "runtime" / "runs.json")
    sink = Sink()
    coord = SessionCoordinator(token_fn=lambda: "token-A", id_fn=lambda: "lease-A", audit=sink)
    if acquire:
        status, _acq = coord.acquire(IDENTITY, "lifecycle")
        assert status == 200, f"acquire devolvio {status}"
    state = loopback.ServerState("clave-gate")
    lifecycle = ProcessLifecycle(
        coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=guard,
        retail_probe=lambda: {"known": True, "processes": []},
        diag_probe=lambda: {"known": True, "processes": []},
        game_path=game, launcher=lambda argv, cwd, ws: None, id_fn=lambda: "run-x",
        argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
        bindings=state,
    )
    state.lifecycle = lifecycle
    return lifecycle, state, coord


def helper():
    from tests.fence_helpers import bind_both_peers  # the subject
    return bind_both_peers


def run_of(lifecycle, run_id: str):
    return lifecycle.manifest.get(run_id)


# -- T1b: with the REAL guard, every registered process classifies `owned` ----------------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, state, coord = real_lifecycle(Path(d), guard=NativeProcessGuard(), acquire=True)
        raised = None
        try:
            helper()(state, "test-run")
        except Exception as exc:  # noqa: BLE001
            raised = f"{type(exc).__name__}: {exc}"
        run = run_of(life, "test-run")
        if raised is not None or run is None:
            check("T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real",
                  False, f"sin run que medir: raised={raised!r} run={run!r}")
        else:
            buckets, reason = life._partition_registered_processes(run.processes)
            check("T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real",
                  bool(buckets["owned"]) and not buckets["gone"] and not buckets["foreign"]
                  and not buckets["unknown"],
                  f"buckets={buckets} reason={reason!r} pids={[p.pid for p in run.processes]} -- un "
                  f"run cuyos procesos el guard real no reconoce no es adoptable ni reapeable como "
                  f"en produccion")
    except Exception as exc:  # noqa: BLE001
        unmet("T1b-PROCESOS-OWNED-GUARD-REAL los procesos registrados son nuestros para el guard real",
              f"{type(exc).__name__}: {exc}")

# -- T1c: without an active lease and without an explicit owner, no invented owner ---------------
with tempfile.TemporaryDirectory() as d:
    try:
        life, state, coord = real_lifecycle(Path(d), guard=FakeGuard(), acquire=False)
        raised = None
        try:
            helper()(state, "test-run")
        except Exception as exc:  # noqa: BLE001
            raised = f"{type(exc).__name__}: {exc}"
        run = run_of(life, "test-run")
        invented = run is not None and run.state == "RUNNING" and run.owner_session_id is not None
        check("T1c-SIN-LEASE-NO-INVENTA-DUENO sin lease activo ni owner explicito no hay run con dueno desconocido",
              not invented,
              f"raised={raised!r} run={(run.state, run.owner_session_id, run.owner_lease_id) if run else None} "
              f"-- un dueno que ningun test controla es un estado imposible en produccion")
    except Exception as exc:  # noqa: BLE001
        unmet("T1c-SIN-LEASE-NO-INVENTA-DUENO sin lease activo ni owner explicito no hay run con dueno desconocido",
              f"{type(exc).__name__}: {exc}")


# -- mutant machinery ---------------------------------------------------------------------------
def mutant_tree(tmp: Path) -> Path:
    """dayz_mcp/ + tests/ + the addon/ the test package resolves at import time.

    Calibration 2026-09-04: without addon/ every test in the copy died at import
    (FileNotFoundError from tests/_addon_paths.py) and three mutant checks went
    'red' for a reason that was not the detector's. A setup failure is UNMET."""
    dst = tmp / "tools"
    for sub in ("dayz_mcp", "tests"):
        shutil.copytree(TOOLS / sub, dst / sub, ignore=shutil.ignore_patterns("__pycache__"))
    # test_session_e2e imports the untracked helper package tools/_broker (e2e daemon fixture):
    # without it the positive control dies at import (measured on the round-3 calibration).
    for extra in ("_broker", "_session_coordination"):
        if (TOOLS / extra).is_dir():
            shutil.copytree(TOOLS / extra, dst / extra, ignore=shutil.ignore_patterns("__pycache__"))
    addon = TOOLS.parent / "addon"
    if addon.is_dir():
        shutil.copytree(addon, tmp / "addon", ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def run_single(tree: Path, test_id: str) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=".", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY, "-m", "unittest", test_id], cwd=str(tree), env=env,
                       capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr)[-900:]


def setup_broken(tail: str) -> bool:
    """The copy itself failed to run the test: an import/collection error, not a verdict."""
    return ("FileNotFoundError" in tail or "ModuleNotFoundError" in tail
            or "ImportError" in tail or "_FailedTest" in tail or "Ran 0 tests" in tail)


def mutant_killed(rc: int, tail: str) -> bool:
    """The detector itself went red on the mutant (an assertion, not a broken setup)."""
    return rc != 0 and not setup_broken(tail) and ("AssertionError" in tail or "FAIL:" in tail)


def control_green(tree: Path, test_id: str) -> tuple[bool, str]:
    rc, tail = run_single(tree, test_id)
    return rc == 0 and "OK" in tail and not setup_broken(tail), tail


DETECTOR = "tests.test_instance_fence.ExitedBindingInvariantTest.test_every_exited_path_retires_bindings"
NO_LIFECYCLE = ("tests.test_instance_fence.Round4FenceRegressionTest."
                "test_missing_creation_time_without_lifecycle_does_not_deliver")

# -- T2b: order reversed in _commit_retirement (preservation: red today already) ----------------
with tempfile.TemporaryDirectory() as d:
    name = "T2b-MUTANTE-ORDEN-INVERTIDO el detector muere si _commit_retirement persiste antes de retirar"
    try:
        tree = mutant_tree(Path(d))
        ok_ctrl, ctrl_tail = control_green(tree, DETECTOR)
        if not ok_ctrl:
            raise RuntimeError(f"control positivo rojo (copia sin mutar): {ctrl_tail[-300:]!r}")
        src = tree / "dayz_mcp" / "process_lifecycle.py"
        text = src.read_text(encoding="utf-8")
        old = ("        self._retire_run_bindings(run.run_id, binding_reason)\n"
               "        try:\n"
               "            self.manifest.replace(run)\n"
               "        except Exception:\n"
               "            return False\n")
        new = ("        try:\n"
               "            self.manifest.replace(run)\n"
               "        except Exception:\n"
               "            return False\n"
               "        self._retire_run_bindings(run.run_id, binding_reason)\n")
        if text.count(old) != 1:
            unmet(name, f"ancla de _commit_retirement encontrada {text.count(old)} veces")
        else:
            src.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
            rc, tail = run_single(tree, DETECTOR)
            check(name, mutant_killed(rc, tail), f"rc={rc} -- el orden retirar->persistir tiene que ser observable; cola: {tail[-200:]!r}")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T3: the `lifecycle is None` branch of _lookup_creation_time raises -> the test must die -------
with tempfile.TemporaryDirectory() as d:
    name = "T3-MUTANTE-RAMA-SIN-LIFECYCLE el test without_lifecycle muere si la rama lifecycle-is-None lanza"
    try:
        tree = mutant_tree(Path(d))
        ok_ctrl, ctrl_tail = control_green(tree, NO_LIFECYCLE)
        if not ok_ctrl:
            raise RuntimeError(f"control positivo rojo (copia sin mutar): {ctrl_tail[-300:]!r}")
        src = tree / "dayz_mcp" / "loopback.py"
        text = src.read_text(encoding="utf-8")
        i = text.find("    def _lookup_creation_time(")
        old = "        lifecycle = self.lifecycle\n        if lifecycle is None:\n            return None\n"
        j = text.find(old, i) if i >= 0 else -1
        if i < 0 or j < 0:
            unmet(name, "ancla de _lookup_creation_time no encontrada: el mutante no se pudo aplicar")
        else:
            new = ("        lifecycle = self.lifecycle\n        if lifecycle is None:\n"
                   "            raise AssertionError(\"mutant: lifecycle-is-None branch\")\n")
            src.write_text(text[:j] + new + text[j + len(old):], encoding="utf-8", newline="\n")
            rc, tail = run_single(tree, NO_LIFECYCLE)
            check(name, mutant_killed(rc, tail),
                  f"rc={rc} -- rc 0 significa que el test que promete 'without lifecycle' ya no pasa "
                  f"por esa rama; cola: {tail[-240:]!r}")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T4: the name says what the body proves ---------------------------------------------------
try:
    text = (TOOLS / "tests" / "test_instance_fence.py").read_text(encoding="utf-8")
    viejo = len(re.findall(r"def test_release_owner_retires_bindings\(", text))
    nuevo = len(re.findall(r"def test_release_owner_fences_bound_binding_until_adopt\(", text))
    check("T4-NOMBRE-RELEASE el test del release se llama por lo que prueba (cerca hasta adoptar)",
          viejo == 0 and nuevo == 1,
          f"retires_bindings={viejo} fences_bound_binding_until_adopt={nuevo}")
except Exception as exc:  # noqa: BLE001
    unmet("T4-NOMBRE-RELEASE el test del release se llama por lo que prueba (cerca hasta adoptar)",
          f"{type(exc).__name__}: {exc}")

# -- T5: an EXPIRED lease is not an owner (F-05) ---------------------------------------------------
# The round-2 helper read `coordinator._active` raw, without the lock and without `_expire_due()`,
# so a lease past SESSION_TTL_S still became a RUNNING owner. Reproduced by Codex with a controlled
# clock; encoded here the same way. Owner resolution must go through a coordinator operation that
# expires under its lock, or the run stays RUNNING_IDLE / unregistered.
from dayz_mcp.session_coordination import SESSION_TTL_S  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    name = "T5-LEASE-CADUCADO-NO-ES-DUENO un lease vencido no se convierte en dueno RUNNING"
    try:
        clock = {"t": 1000.0}
        tmp = Path(d)
        game = tmp / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                             tmp / "runtime" / "coordination.json", tmp / "runtime" / "runs.json")
        sink = Sink()
        coord = SessionCoordinator(time_fn=lambda: clock["t"], token_fn=lambda: "token-A",
                                   id_fn=lambda: "lease-A", audit=sink)
        status, _acq = coord.acquire(IDENTITY, "lifecycle")
        assert status == 200, f"acquire devolvio {status}"
        state = loopback.ServerState("clave-gate")
        life = ProcessLifecycle(
            coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=FakeGuard(),
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game, launcher=lambda argv, cwd, ws: None, id_fn=lambda: "run-x",
            argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
            bindings=state,
        )
        state.lifecycle = life
        clock["t"] += SESSION_TTL_S + 1.0                       # A's lease is due, nobody expired it yet
        raised = None
        try:
            helper()(state, "test-run")
        except Exception as exc:  # noqa: BLE001
            raised = f"{type(exc).__name__}: {exc}"
        run = run_of(life, "test-run")
        vencido_es_dueno = run is not None and run.state == "RUNNING" and run.owner_session_id is not None
        check(name, not vencido_es_dueno,
              f"ttl={SESSION_TTL_S} raised={raised!r} run="
              f"{(run.state, run.owner_session_id, run.owner_lease_id) if run else None} -- un dueno "
              f"cuyo lease ya vencio es un run que ningun release va a cercar: el helper leyo el "
              f"titular sin pasar por el coordinador")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T7: the e2e adopt must observe `dispatchable` (F-07) -----------------------------------------
E2E_ADOPT = ("tests.test_session_e2e.SessionE2ETest."
             "test_parallel_reads_and_mutations_are_fifo_non_interleaved")
with tempfile.TemporaryDirectory() as d:
    name = "T7-MUTANTE-DISPATCHABLE-FALSE el e2e que adopta muere si adopt publica dispatchable False"
    try:
        tree = mutant_tree(Path(d))
        ok_ctrl, ctrl_tail = control_green(tree, E2E_ADOPT)
        if not ok_ctrl:
            raise RuntimeError(f"control positivo rojo (copia sin mutar): {ctrl_tail[-300:]!r}")
        src = tree / "dayz_mcp" / "process_lifecycle.py"
        text = src.read_text(encoding="utf-8")
        old = '                "dispatchable": dispatchable,\n'
        if text.count(old) != 1:
            unmet(name, f"ancla del payload de adopt encontrada {text.count(old)} veces")
        else:
            src.write_text(text.replace(old, '                "dispatchable": False,\n'),
                           encoding="utf-8", newline="\n")
            rc, tail = run_single(tree, E2E_ADOPT)
            check(name, mutant_killed(rc, tail),
                  f"rc={rc} -- rc 0 significa que el e2e adopta y sigue verde aunque la respuesta "
                  f"declare que el run no despacha (C4/P-I2 sin observar); cola: {tail[-240:]!r}")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T1a': the helper never writes an owner; ownership comes from adopt_run (P-I10) -----------
# Three rounds of owner INFERENCE fell to three different attacks (invented owner, expired lease,
# truncated public session / prefix match). LL-421: the question was wrong. A fixture cannot
# know the durable identity; the product can. So: bind -> RUNNING_IDLE with owned processes, and
# adopt_run(A) is the only way the run becomes A's.
with tempfile.TemporaryDirectory() as d:
    name = "T1a-PROPIEDAD-SOLO-POR-ADOPT el helper deja RUNNING_IDLE y adopt_run(A) es quien acredita al dueno"
    try:
        life, state, coord = real_lifecycle(Path(d), guard=FakeGuard(), acquire=True)
        raised = None
        try:
            helper()(state, "test-run")
        except Exception as exc:  # noqa: BLE001
            raised = f"{type(exc).__name__}: {exc}"
        run = run_of(life, "test-run")
        if raised is not None or run is None:
            check(name, False, f"el helper no dejo un run registrado: raised={raised!r} run={run!r}")
        elif run.state != "RUNNING_IDLE" or run.owner_session_id is not None or run.owner_lease_id is not None:
            check(name, False,
                  f"tras el helper: state={run.state!r} owner=({run.owner_session_id!r}, "
                  f"{run.owner_lease_id!r}) -- el helper sigue escribiendo un dueno; la propiedad solo "
                  f"puede venir de una operacion del producto")
        else:
            adoptado = life.adopt_run(IDENTITY, "token-A", "test-run")
            despues = run_of(life, "test-run")
            check(name,
                  isinstance(adoptado, dict) and adoptado.get("ok") is True
                  and adoptado.get("dispatchable") is True
                  and despues is not None and despues.state == "RUNNING"
                  and despues.owner_session_id == IDENTITY.session_id
                  and despues.owner_lease_id == "lease-A",
                  f"adopt={adoptado!r} durable=({despues.state!r}, {despues.owner_session_id!r}, "
                  f"{despues.owner_lease_id!r}) -- adoptar con la identidad real tiene que dejar al "
                  f"dueno completo y despachable")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T9: a session id longer than the public payload round-trips (F-08 by construction) ---------
LONG = ClientIdentity("codex", 11, 1, "2026-07-15T00:00:00Z", "abcdefghijkl-ACTUAL-SESSION", "owner")
with tempfile.TemporaryDirectory() as d:
    name = "T9-SESION-LARGA-RELEASE-ENCUENTRA-EL-RUN con un session_id de mas de 12 caracteres, adopt y release casan"
    try:
        tmp = Path(d)
        game = tmp / "game"
        game.mkdir(parents=True, exist_ok=True)
        (game / "DayZDiag_x64.exe").write_bytes(b"")
        paths = RuntimePaths(tmp / "runtime", tmp / "runtime" / "audit",
                             tmp / "runtime" / "coordination.json", tmp / "runtime" / "runs.json")
        sink = Sink()
        coord = SessionCoordinator(token_fn=lambda: "token-L", id_fn=lambda: "lease-L", audit=sink)
        status, _acq = coord.acquire(LONG, "lifecycle")
        assert status == 200, f"acquire devolvio {status}"
        state = loopback.ServerState("clave-gate")
        life = ProcessLifecycle(
            coordinator=coord, manifest=RunManifestStore(paths), audit=sink, guard=FakeGuard(),
            retail_probe=lambda: {"known": True, "processes": []},
            diag_probe=lambda: {"known": True, "processes": []},
            game_path=game, launcher=lambda argv, cwd, ws: None, id_fn=lambda: "run-x",
            argv_of=lambda pid: [str(game / "DayZDiag_x64.exe"), "-mission=test"],
            bindings=state,
        )
        state.lifecycle = life
        helper()(state, "test-run")
        adoptado = life.adopt_run(LONG, "token-L", "test-run")
        run = run_of(life, "test-run")
        liberados = life.release_owner(LONG.session_id, "lease-L") if run is not None else None
        despues = run_of(life, "test-run")
        check(name,
              isinstance(adoptado, dict) and adoptado.get("ok") is True
              and run is not None and run.owner_session_id == LONG.session_id
              and liberados == ["test-run"] and despues is not None and despues.state == "RUNNING_IDLE",
              f"adopt={adoptado!r} owner_tras_adopt={(run.owner_session_id if run else None)!r} "
              f"release={liberados!r} estado_final={(despues.state if despues else None)!r} -- si el "
              f"release no encuentra el run, la identidad del manifiesto no es la del titular (truncada "
              f"o inventada) y C2 queda escondido")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T10: every LITERAL EXITED site maps to a NAMED runtime order test (P-I11, honest scope) -----
# Downgraded after Codex delta 3: a variable/AnnAssign/tuple/setattr/replace spelling is NOT
# detected; this is a known-spellings heuristic. Exhaustive form = one terminal API (backlog).
# The static detector stops claiming path sensitivity (bypass/split/loop mutants fell each round).
# What it can prove: the exact set of functions that assign EXITED, and that each is covered by a
# runtime test that observes retire-before-persist on its real paths.
import ast  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    name = "T10-SITIOS-EXITED-LITERALES-CON-TEST-DE-ORDEN cada sitio LITERAL que asigna EXITED esta declarado y tiene su test runtime (deletreos conocidos, no inventario exhaustivo)"
    try:
        prod = ast.parse((TOOLS / "dayz_mcp" / "process_lifecycle.py").read_text(encoding="utf-8"))
        stack, sites = [], set()

        def can_be_exited(v):
            if isinstance(v, ast.Constant):
                return v.value == "EXITED"
            if isinstance(v, ast.IfExp):
                return can_be_exited(v.body) or can_be_exited(v.orelse)
            return False

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, n):
                stack.append(n.name); self.generic_visit(n); stack.pop()
            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Assign(self, n):
                # A value CAN be EXITED through a conditional expression too: admin_reconcile
                # assigns `"RUNNING_IDLE" if survivors else "EXITED"` (measured round 4).
                if stack and any(isinstance(t, ast.Attribute) and t.attr == "state" for t in n.targets):
                    if can_be_exited(n.value):
                        sites.add(".".join(stack))
                self.generic_visit(n)

        V().visit(prod)
        ttext = (TOOLS / "tests" / "test_instance_fence.py").read_text(encoding="utf-8")
        ttree = ast.parse(ttext)
        declared = None
        for node in ast.walk(ttree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "RETIREMENT_TESTED_EXITED_SITES" for t in node.targets
            ):
                declared = ast.literal_eval(node.value)
        if not isinstance(declared, dict):
            check(name, False, f"no hay dict RETIREMENT_TESTED_EXITED_SITES en test_instance_fence.py; sitios EXITED reales={sorted(sites)}")
        else:
            lifecycle_tests = (TOOLS / "tests" / "test_process_lifecycle.py").read_text(encoding="utf-8")
            faltan = sorted(sites - set(declared))
            sobran = sorted(set(declared) - sites)
            sin_test = sorted(k for k, v in declared.items()
                              if v is not None and f"def {v}(" not in lifecycle_tests)
            check(name, not faltan and not sobran and not sin_test,
                  f"sitios_reales={sorted(sites)} declarados={sorted(declared)} faltan={faltan} "
                  f"sobran={sobran} sin_test_runtime={sin_test} -- un sitio EXITED sin test de orden en "
                  f"tiempo de ejecucion es una invariante que nadie observa")
    except Exception as exc:  # noqa: BLE001
        unmet(name, f"{type(exc).__name__}: {exc}")

# -- T11: nothing left to forge -- bind_both_peers has no `owner` parameter ---------------------
import inspect  # noqa: E402

try:
    sig = inspect.signature(helper())
    check("T11-SIN-PARAMETRO-OWNER bind_both_peers ya no acepta un dueno de fixture",
          "owner" not in sig.parameters,
          f"firma={sig} -- un dueno que pone el fixture es un dueno que el coordinador no conoce")
except Exception as exc:  # noqa: BLE001
    unmet("T11-SIN-PARAMETRO-OWNER bind_both_peers ya no acepta un dueno de fixture",
          f"{type(exc).__name__}: {exc}")


print("=" * 104)
worst = 0
for nm, v, dt in RESULTS:
    print(f"[{v:<5}] {nm}")
    if dt:
        print(f"          {dt[:300]}")
    if v != "PASS":
        worst = 1
print("=" * 104)
t = {x: sum(1 for _, y, _ in RESULTS if y == x) for x in ("PASS", "FAIL", "UNMET")}
print(f"ORACULO-TESTS: PASS={t['PASS']} FAIL={t['FAIL']} UNMET={t['UNMET']} de {len(RESULTS)}")
if worst == 0:
    print("ORACULO-TESTS-VERDE")
sys.exit(worst)
