#!/usr/bin/env python3
"""Vehicle-protocol site certification gate v2 (G0 re-spec, fb-20260824-025758-2509).

Two phases:

SCAN (no lease, no player proximity): surface_query is the only remote
instrument that answers truthfully everywhere (fb-20260824-123204-638e:
entities_query far from every player returns 0 or cap-saturated rows — it
is only complete inside a player's streamed area). A coarse surface grid
over the airfield rectangles finds the actual pavement (blind coordinate
guesses missed the runways on rounds 1-3, and "open" grids landed in the
sea, which scans clear: seabed, zero entities, negative y). Candidates are
pavement cells ranked by consecutive pavement to the north.

CERTIFY (session lease): for the most open finalists, the place_safely
canopy gate (scene_raycast geom from y+30 to y-5 must land within 0.05 m of
the surface — CALIBRATED 2026-08-19) runs at the PLAYER point and the CAR
point before any teleport (fb-20260824-115220-1bc1: a raw [x,0,z] teleport
buried the player inside a building). Then the full drive ladder from the
G0 ABBA library: spawn -> fixture -> get-in -> engine -> traced
full-throttle drive -> finish, requiring delta_2s_xz > 1.0 m (product-spec
G0 contract), delta_5s_xz >= 10 m, trace gates green, and a verified
teardown: finish_site "OK" or "OK_FORCED_DELETE" (fixture delete with the
ejection confirmed). The get-out action itself is site-independently
blocked on this stand (fb-20260824-133301-ecf5) and is not a site
criterion.

Certified canonical 2026-08-24 (round 13, this reviewed gate): NWAF
[4200.0, 0.0, 10650.0] — delta_2s_xz 3.216 m, 65.2 m span, heading
[0.003, 1.0], trace gates PASS, release confirmed. See
docs/VEHICLE_TESTING.md for the alternates and their demotion reasons.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import g0_abba_gate as g

# Surface grids: (name, x0, x1, xstep, z0, z1, zstep), bounds inclusive.
# Wide rectangles around the two Chernarus airfields; surface_query is
# cheap and truthful remotely, so the grid finds the real pavement.
SURFACE_GRIDS = [
    ("NWAF", 3800.0, 5000.0, 50.0, 9800.0, 11200.0, 50.0),
    ("BALOTA", 4200.0, 5400.0, 50.0, 2350.0, 2600.0, 50.0),
]
PAVEMENT_MARKERS = ("asphalt", "concrete", "tarmac", "runway")
CANDIDATE_MIN_SPACING_M = 100.0
MIN_SURFACE_Y_M = 1.0
MAX_FINALISTS = 8
# Drive envelope measured 2026-08-24: ~91 m total (5 s traced full throttle +
# braking); 160 m forward keeps the >=150 m protocol bar with margin, and
# 25 m half-width absorbs the ~8 degree terrain-inherited heading spread.
# Round 3 showed the stricter 190x80 m box matches 1/326 CE seeds.
CORRIDOR_HALF_WIDTH_M = 25.0
CORRIDOR_BACK_M = 10.0
CORRIDOR_FORWARD_M = 160.0
# The corridor is enumerated with three small spheres instead of one r=200
# ball: a 200 m sphere near any built-up area saturates the 128-row cap
# (round 5: every NWAF finalist rejected on truncation) while covering 15x
# the area that matters. Three r=65 spheres at dz 20/85/150 cover every
# band point within ~41 m of a center; rows are nearest-first
# (MCPMessages.c:458), so a truncated sphere still fully enumerates the
# band when its farthest returned row is >= 45 m out.
CORRIDOR_QUERY_POINTS = (20.0, 85.0, 150.0)
CORRIDOR_QUERY_RADIUS_M = 65.0
CORRIDOR_COVERAGE_NEEDED_M = 45.0
PLAYER_OFFSET_SOUTH_M = 8.0
ARRIVAL_TOLERANCE_M = 30.0
DELTA2_MIN_M = 1.0
DELTA5_MIN_M = 10.0
CANOPY_DY_M = 0.05
ENTITY_LIMIT = 128
SCAN_ABORT_AFTER_ERRORS = 10
SURFACE_CACHE_VERSION = 2
# Canonical site first (round-13 certification, 2026-08-24), then measured
# alternates. Each must still resolve against the current pavement scan
# before it is certified — the list is an ordering, not evidence.
PRIORITY_SITES = ((4200.0, 10650.0), (4300.0, 10500.0), (4350.0, 10400.0))
PRIORITY_PAVEMENT_MAX_DIST_M = 75.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entity_rows(result: dict) -> list[dict]:
    rows = result.get("entities")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _is_blocking(row: dict) -> bool:
    rtype = str(row.get("type", ""))
    classname = str(row.get("classname", ""))
    if rtype == "Man" or "Survivor" in rtype or "Survivor" in classname:
        return False
    if rtype.startswith("BushSoft") or classname.startswith("BushSoft"):
        return False
    # Config-less ground props: GetType() is empty and ClassName() is the
    # bare engine class. Measured on the NWAF runway (round 6): 83-88 of
    # ~85 in-band rows are these, at terrain height, densely everywhere —
    # clutter, not car-stoppers. Anything real carries a config type. The
    # DRIVE + EXIT stages remain the physical judges either way.
    if rtype == "" and classname == "Object":
        return False
    return True


def _corridor_blockers(rows: list[dict], x: float, z: float) -> list[dict]:
    hits: list[dict] = []
    for row in rows:
        if not _is_blocking(row):
            continue
        pos = row.get("pos")
        if not (isinstance(pos, list) and len(pos) == 3):
            continue
        dx = float(pos[0]) - x
        dz = float(pos[2]) - z
        if abs(dx) <= CORRIDOR_HALF_WIDTH_M and -CORRIDOR_BACK_M <= dz <= CORRIDOR_FORWARD_M:
            hits.append(
                {
                    "type": row.get("type"),
                    "classname": row.get("classname"),
                    "pos": pos,
                    "dx": dx,
                    "dz": dz,
                }
            )
    return hits


def corridor_entities(
    daemon: g.Daemon, x: float, z: float
) -> tuple[list[dict], bool, list[dict]]:
    """Enumerate the corridor band with three small spheres.

    Fail-closed: a sphere counts as unverified (coverage_incomplete) when
    its surface probe or entity query fails, when the reply is not ok, when
    count_total is missing, or when count_total exceeds the returned rows
    and the farthest returned row (3D distance, MCPBridge sorts
    nearest-first) is under the coverage threshold. Each sphere queries at
    its own local surface y so the 3D distances stay comparable to the
    planar coverage math (the 45 m threshold includes a +/-15 m vertical
    envelope over the 41.0 m worst-case planar assignment).

    Returns (deduped rows, coverage_incomplete, per-sphere detail)."""

    all_rows: list[dict] = []
    seen: set[tuple] = set()
    coverage_incomplete = False
    detail: list[dict] = []
    for dz in CORRIDOR_QUERY_POINTS:
        info: dict[str, object] = {"dz": dz}
        local = daemon.run("surface_query", {"x": x, "z": z + dz}, "server", 20.0)
        if local.get("_timeout") or not g._is_true(local.get("ok")) or not g._is_number(local.get("y")):
            info["error"] = "surface_failed"
            coverage_incomplete = True
            detail.append(info)
            continue
        y_local = float(local.get("y"))
        info["y_local"] = y_local
        ents = daemon.run(
            "entities_query",
            {
                "pos": [x, y_local, z + dz],
                "radius": CORRIDOR_QUERY_RADIUS_M,
                "limit": ENTITY_LIMIT,
            },
            "server",
            30.0,
        )
        rows = _entity_rows(ents)
        count_total = ents.get("count_total")
        info["rows"] = len(rows)
        info["count_total"] = count_total
        if (
            ents.get("_timeout")
            or not g._is_true(ents.get("ok"))
            or type(count_total) is not int
            or count_total < 0
        ):
            info["error"] = "query_failed"
            coverage_incomplete = True
            detail.append(info)
            continue
        if count_total > len(rows):
            dists = [
                float(row.get("distance"))
                for row in rows
                if g._is_number(row.get("distance"))
            ]
            farthest = max(dists) if dists else 0.0
            info["truncated_farthest_m"] = farthest
            if farthest < CORRIDOR_COVERAGE_NEEDED_M:
                info["coverage_incomplete"] = True
                coverage_incomplete = True
        for row in rows:
            pos = row.get("pos")
            key = (
                row.get("type"),
                row.get("classname"),
                tuple(pos) if isinstance(pos, list) else None,
            )
            if key in seen:
                continue
            seen.add(key)
            all_rows.append(row)
        detail.append(info)
    return all_rows, coverage_incomplete, detail


def _is_pavement(surface_type: object) -> bool:
    lowered = str(surface_type or "").lower()
    return any(marker in lowered for marker in PAVEMENT_MARKERS)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def expected_grid_cells() -> int:
    total = 0
    for _name, x0, x1, xstep, z0, z1, zstep in SURFACE_GRIDS:
        total += (int(round((x1 - x0) / xstep)) + 1) * (int(round((z1 - z0) / zstep)) + 1)
    return total


def surface_scan(daemon: g.Daemon) -> dict[tuple[int, int], dict]:
    """Scan the surface grids; abort on a run of consecutive failures.

    Ten consecutive failed probes mean the transport or the game is gone —
    continuing would burn the per-cell timeout across the whole grid and
    cache a garbage map."""

    cells: dict[tuple[int, int], dict] = {}
    consecutive_errors = 0
    for name, x0, x1, xstep, z0, z1, zstep in SURFACE_GRIDS:
        x = x0
        while x <= x1 + 1e-6:
            z = z0
            while z <= z1 + 1e-6:
                try:
                    surface = daemon.run("surface_query", {"x": x, "z": z}, "server", 15.0)
                except Exception as error:
                    surface = {"_error": str(error)[:120]}
                cell: dict[str, object] = {"area": name}
                if g._is_true(surface.get("ok")) and g._is_number(surface.get("y")):
                    cell["y"] = float(surface["y"])
                    cell["type"] = str(surface.get("type") or "")
                    consecutive_errors = 0
                else:
                    cell["type"] = ""
                    cell["error"] = surface.get("_error") or "surface_failed"
                    consecutive_errors += 1
                    if consecutive_errors >= SCAN_ABORT_AFTER_ERRORS:
                        raise RuntimeError(
                            "surface scan aborted: "
                            + str(consecutive_errors)
                            + " consecutive probe failures"
                        )
                cells[(int(round(x)), int(round(z)))] = cell
                z += zstep
            x += xstep
    return cells


def pavement_candidates(cells: dict[tuple[int, int], dict]) -> list[dict]:
    step = 50
    candidates: list[dict] = []
    for (x, z), cell in cells.items():
        if not _is_pavement(cell.get("type")):
            continue
        y = cell.get("y")
        if not (isinstance(y, float) and y >= MIN_SURFACE_Y_M):
            continue
        run_m = 0
        zz = z + step
        while (x, zz) in cells and _is_pavement(cells[(x, zz)].get("type")):
            run_m += step
            zz += step
        candidates.append(
            {
                "area": cell.get("area"),
                "x": float(x),
                "z": float(z),
                "y": y,
                "surface_type": cell.get("type"),
                "pavement_north_m": run_m,
            }
        )
    candidates.sort(key=lambda c: (-c["pavement_north_m"], c["x"], c["z"]))
    spaced: list[dict] = []
    for cand in candidates:
        if all(
            math.hypot(cand["x"] - kept["x"], cand["z"] - kept["z"]) >= CANDIDATE_MIN_SPACING_M
            for kept in spaced
        ):
            spaced.append(cand)
    return spaced


def canopy_gate(daemon: g.Daemon, x: float, z: float) -> dict:
    record: dict[str, object] = {"x": x, "z": z, "status": "FAIL"}
    surface = daemon.run("surface_query", {"x": x, "z": z}, "server", 20.0)
    if surface.get("_timeout") or not g._is_true(surface.get("ok")) or not g._is_number(surface.get("y")):
        record["reason"] = "surface_failed"
        return record
    y = float(surface.get("y"))
    record["surface_y"] = y
    ray = daemon.run(
        "scene_raycast",
        {
            "from": [x, y + 30.0, z],
            "to": [x, y - 5.0, z],
            "method": "rvproxy",
            # Ignore players: the player often stands inside the probed column
            # (left there by a previous phase) and the ray hits their head at
            # dy ~1.671 m. That signature demoted x4300 in round 13.
            "ignore": "player",
            "radius": 0.05,
            "intersect": "geom",
        },
        "server",
        20.0,
    )
    raycast = ray.get("raycast") if isinstance(ray.get("raycast"), dict) else {}
    record["hit"] = raycast.get("hit")
    hit_pos = raycast.get("pos")
    if not g._is_true(raycast.get("hit")) or not (
        isinstance(hit_pos, list) and len(hit_pos) == 3 and g._is_number(hit_pos[1])
    ):
        record["reason"] = "no_ground_hit"
        return record
    dy = abs(float(hit_pos[1]) - y)
    record["dy"] = dy
    if dy > CANOPY_DY_M:
        record["reason"] = "canopy_or_roof"
        return record
    record["status"] = "PASS"
    return record


def precheck(daemon: g.Daemon, label: str, x: float, z: float) -> dict:
    record: dict[str, object] = {"status": "FAIL"}

    canopy_player = canopy_gate(daemon, x, z - PLAYER_OFFSET_SOUTH_M)
    record["canopy_player"] = canopy_player
    if canopy_player.get("status") != "PASS":
        record["reason"] = "canopy_player"
        return record
    canopy_car = canopy_gate(daemon, x, z)
    record["canopy_car"] = canopy_car
    if canopy_car.get("status") != "PASS":
        record["reason"] = "canopy_car"
        return record

    y_player = float(canopy_player["surface_y"])
    tp = daemon.run(
        "player_teleport",
        {"pos": [x, y_player, z - PLAYER_OFFSET_SOUTH_M]},
        "server",
        30.0,
    )
    record["teleport_ok"] = tp.get("ok")
    if tp.get("_timeout") or not g._is_true(tp.get("ok")):
        record["reason"] = "teleport_failed"
        return record
    arrived = None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        state = daemon.run("query_player_state", {}, "server", 15.0)
        pos = g.extract_pos(state)
        if pos is not None and math.hypot(
            pos[0] - x, pos[2] - (z - PLAYER_OFFSET_SOUTH_M)
        ) <= ARRIVAL_TOLERANCE_M:
            arrived = pos
            break
        time.sleep(1.0)
    record["player_pos"] = arrived
    if arrived is None:
        record["reason"] = "player_never_arrived"
        return record

    surfaces: dict[str, object] = {}
    for name, sz in (("mid", z + 90.0), ("end", z + 180.0)):
        probe = daemon.run("surface_query", {"x": x, "z": sz}, "server", 30.0)
        surfaces[name] = {"ok": probe.get("ok"), "y": probe.get("y"), "type": probe.get("type")}
        if probe.get("_timeout") or not g._is_true(probe.get("ok")):
            record["surfaces"] = surfaces
            record["reason"] = "surface_" + name + "_failed"
            return record
    record["surfaces"] = surfaces

    rows, coverage_incomplete, detail = corridor_entities(daemon, x, z)
    record["corridor_query_detail"] = detail
    record["entity_rows_total"] = len(rows)
    if coverage_incomplete:
        record["reason"] = "corridor_unverified"
        return record
    blockers = _corridor_blockers(rows, x, z)
    record["corridor_blockers"] = blockers
    if blockers:
        record["reason"] = "corridor_blocked"
        return record
    record["status"] = "PASS"
    return record


def drive_metrics(cell: dict) -> dict:
    metrics: dict[str, object] = {"status": "FAIL"}
    deltas = cell.get("deltas")
    metrics["deltas"] = deltas
    trace_gates = cell.get("trace_gates") or {}
    metrics["trace_gates_status"] = trace_gates.get("status")
    samples = cell.get("samples") or []
    if len(samples) >= 2:
        first = g._position(samples[0])
        last = g._position(samples[-1])
        dx = last[0] - first[0]
        dz = last[2] - first[2]
        span = math.hypot(dx, dz)
        metrics["trace_span_xz_m"] = span
        if span > 1e-6:
            metrics["direction_xz"] = [dx / span, dz / span]
    if not isinstance(deltas, dict):
        metrics["reason"] = "no_deltas"
        return metrics
    if trace_gates.get("status") != "PASS":
        metrics["reason"] = "trace_gates"
        return metrics
    d2 = deltas.get("delta_2s_xz")
    d5 = deltas.get("delta_5s_xz")
    if not (g._is_number(d2) and float(d2) > DELTA2_MIN_M):
        metrics["reason"] = "delta_2s_below_contract"
        return metrics
    if not (g._is_number(d5) and float(d5) >= DELTA5_MIN_M):
        metrics["reason"] = "delta_5s_below_bar"
        return metrics
    metrics["status"] = "PASS"
    return metrics


def probe_candidate(daemon: g.Daemon, label: str, x: float, z: float, out: Path) -> dict:
    result: dict[str, object] = {
        "label": label,
        "site": [x, 0.0, z],
        "verdict": "FAIL",
        "started_utc": _now(),
    }
    try:
        pre = precheck(daemon, label, x, z)
    except Exception as error:
        result["precheck"] = {
            "status": "FAIL",
            "reason": "exception:" + str(error)[:300],
        }
        result["fail_stage"] = "PRECHECK"
        return result
    result["precheck"] = pre
    if pre.get("status") != "PASS":
        result["fail_stage"] = "PRECHECK"
        return result

    cell: dict[str, object] = {"label": label, "site_pos": [x, 0.0, z]}
    finish: dict[str, object] | None = None
    try:
        g.prepare_site(daemon, cell)
        g.run_cell(daemon, cell)
    except g.GateFailure as failure:
        result["gate_failure"] = failure.to_dict()
        result["fail_stage"] = "DRIVE_SETUP"
    except Exception as error:
        result["gate_failure"] = {
            "code": type(error).__name__,
            "detail": str(error)[:500],
        }
        result["fail_stage"] = "DRIVE_SETUP"
    finally:
        if type(cell.get("object_id")) is int and not cell.get("finished"):
            try:
                finish = g.finish_site(daemon, cell)
            except Exception as error:
                finish = {
                    "status": "CLEANUP_DEGRADED",
                    "reason": "finish_exception:" + str(error)[:300],
                }
        result["finish"] = finish

    try:
        if "fail_stage" not in result:
            metrics = drive_metrics(cell)
            result["drive"] = metrics
            finish_status = (finish or {}).get("status")
            result["finish_status"] = finish_status
            # Exit criterion: verified teardown. "OK" is the action
            # get-out; "OK_FORCED_DELETE" is the fixture delete with the
            # ejection verified (finish_site only emits it after
            # EJECT_CHECK reads not_seated). The action get-out was
            # measured site-independent-impossible on this stand
            # (fb-20260824-133301-ecf5: the server replica never moves and
            # the injected door action's server half is inert, so
            # CrewCanGetThrough never clears) — it does not discriminate
            # sites and cannot be a site criterion.
            if metrics.get("status") != "PASS":
                result["fail_stage"] = "DRIVE"
            elif finish_status not in ("OK", "OK_FORCED_DELETE"):
                result["fail_stage"] = "EXIT"
            else:
                result["verdict"] = "PASS"
        else:
            result["drive"] = drive_metrics(cell) if cell.get("samples") else None
            result["finish_status"] = (finish or {}).get("status")

        samples = cell.pop("samples", None)
        if samples:
            sidecar = out.with_name(out.stem + "." + label + ".samples.json")
            _atomic_write_text(sidecar, json.dumps(samples))
            result["samples_sidecar"] = str(sidecar)
        result["evidence"] = {
            "spawn_pos_real": cell.get("spawn_pos_real"),
            "net_id": cell.get("net_id"),
            "owner_identity_present": bool(cell.get("owner_identity")),
            "deltas": cell.get("deltas"),
            "trace_gates": cell.get("trace_gates"),
            "door": cell.get("door"),
            "S0": cell.get("S0"),
            "S2": cell.get("S2"),
            "S5": cell.get("S5"),
        }
    except Exception as error:
        # Post-processing must never certify or abort the whole gate: a
        # malformed sample or a failed sidecar write is an evidence
        # problem of THIS candidate.
        result["verdict"] = "FAIL"
        result["fail_stage"] = "EVIDENCE"
        result["evidence_error"] = type(error).__name__ + ": " + str(error)[:300]
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="vehicle protocol site gate v2")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", default=str(SCRIPT_DIR / ".dayz_mcp.key"))
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().with_name("_site_protocol_verdict.json")),
    )
    parser.add_argument(
        "--pbo-sha256",
        required=True,
        help="SHA-256 of the deployed bridge PBO; a certified row must be "
        "attributable to one exact build (docs/VEHICLE_TESTING.md).",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9A-Fa-f]{64}", args.pbo_sha256):
        parser.error("--pbo-sha256 must be 64 hex characters")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    key = Path(args.keyfile).read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("keyfile is empty")
    daemon = g.Daemon(args.port, key)
    daemon.identity["task_label"] = "vehicle protocol site certification"

    report: dict[str, object] = {
        "generated_utc": _now(),
        "pbo_sha256": args.pbo_sha256.upper(),
        "contract": {
            "delta_2s_xz_min_m": DELTA2_MIN_M,
            "delta_5s_xz_min_m": DELTA5_MIN_M,
            "corridor_half_width_m": CORRIDOR_HALF_WIDTH_M,
            "corridor_forward_m": CORRIDOR_FORWARD_M,
            "canopy_dy_m": CANOPY_DY_M,
            "bushsoft_passable": True,
            "clean_exit_required": True,
        },
        "candidates": [],
        "canonical": None,
        "row": "NO_SITE_CERTIFIED",
    }

    bridge = daemon.bridge_status()
    ready = bridge.get("ready")
    report["bridge_ready"] = ready
    if not (isinstance(ready, dict) and ready.get("ready") is True):
        report["row"] = "SETUP_FAILED"
        _atomic_write_text(out, json.dumps(report, indent=1, default=str))
        print("[site-gate] bridge not ready", flush=True)
        return 2

    print("[site-gate] === SURFACE SCAN ===", flush=True)
    map_cache = out.with_name(out.stem + ".surface_map.json")
    grids_fingerprint = [list(grid) for grid in SURFACE_GRIDS]
    expected = expected_grid_cells()
    cells: dict[tuple[int, int], dict] = {}
    if map_cache.exists():
        # The cache is only trusted when it is a complete, error-free scan
        # of exactly these grids: terrain is static, but a partial or
        # foreign map silently narrows the candidate space.
        try:
            payload = json.loads(map_cache.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("version") == SURFACE_CACHE_VERSION
                and payload.get("grids") == grids_fingerprint
                and isinstance(payload.get("cells"), list)
                and len(payload["cells"]) == expected
                and not any("error" in entry for entry in payload["cells"])
            ):
                for entry in payload["cells"]:
                    cells[(int(entry["x"]), int(entry["z"]))] = {
                        key: value
                        for key, value in entry.items()
                        if key not in ("x", "z")
                    }
                print(
                    "[scan] surface map loaded from cache (" + str(len(cells)) + " cells)",
                    flush=True,
                )
            else:
                print("[scan] cache rejected (schema/completeness); rescanning", flush=True)
        except (OSError, ValueError, KeyError, TypeError):
            cells = {}
    if not cells:
        try:
            cells = surface_scan(daemon)
        except RuntimeError as error:
            report["row"] = "SETUP_FAILED"
            report["scan_error"] = str(error)
            _atomic_write_text(out, json.dumps(report, indent=1, default=str))
            print("[site-gate] " + str(error), flush=True)
            return 2
        error_cells = sum(1 for cell in cells.values() if "error" in cell)
        if error_cells or len(cells) != expected:
            report["row"] = "SETUP_FAILED"
            report["scan_error"] = (
                "incomplete scan: " + str(len(cells)) + "/" + str(expected)
                + " cells, " + str(error_cells) + " errors"
            )
            _atomic_write_text(out, json.dumps(report, indent=1, default=str))
            print("[site-gate] " + str(report["scan_error"]), flush=True)
            return 2
    histogram: dict[str, int] = {}
    for cell in cells.values():
        key = str(cell.get("type"))
        histogram[key] = histogram.get(key, 0) + 1
    report["surface_scan"] = {
        "cells": len(cells),
        "type_histogram": histogram,
    }
    _atomic_write_text(
        map_cache,
        json.dumps(
            {
                "version": SURFACE_CACHE_VERSION,
                "grids": grids_fingerprint,
                "cells": [
                    {"x": key[0], "z": key[1], **value}
                    for key, value in sorted(cells.items())
                ],
            }
        ),
    )
    report["surface_map_sidecar"] = str(map_cache)
    print("[scan] type histogram: " + json.dumps(histogram, ensure_ascii=False), flush=True)

    candidates = pavement_candidates(cells)
    report["pavement_candidates"] = candidates
    _atomic_write_text(out, json.dumps(report, indent=1, default=str))
    if not candidates:
        print("[site-gate] no pavement found in the scanned rectangles", flush=True)
        return 3
    for cand in candidates[:10]:
        print(
            "[scan] PAVEMENT " + str(cand["area"]) + " x=" + str(cand["x"])
            + " z=" + str(cand["z"]) + " north_m=" + str(cand["pavement_north_m"])
            + " type=" + str(cand["surface_type"]),
            flush=True,
        )

    # The frozen canonical goes first so a green run is a canonical
    # RE-certification; alternates and fresh pavement candidates only run
    # when an earlier entry fails. A priority site must still resolve
    # against the current pavement scan — the tuple is an ordering, not
    # pavement evidence.
    priority: list[dict] = []
    skipped_priority: list[list[float]] = []
    for px, pz in PRIORITY_SITES:
        if any(
            math.hypot(px - cand["x"], pz - cand["z"]) <= PRIORITY_PAVEMENT_MAX_DIST_M
            for cand in candidates
        ):
            priority.append({"area": "NWAF_CANON", "x": px, "z": pz})
        else:
            skipped_priority.append([px, pz])
    if skipped_priority:
        report["priority_skipped_no_pavement"] = skipped_priority
        print(
            "[site-gate] priority sites without current pavement evidence: "
            + json.dumps(skipped_priority),
            flush=True,
        )
    rest = [
        cand
        for cand in candidates
        if all(
            math.hypot(cand["x"] - p["x"], cand["z"] - p["z"]) >= CANDIDATE_MIN_SPACING_M
            for p in priority
        )
    ]
    finalists = (priority + rest)[:MAX_FINALISTS]
    daemon.session_acquire_wait("vehicle protocol site certification", 300.0)
    release_confirmed = False
    try:
        for point in finalists:
            x = float(point["x"])
            z = float(point["z"])
            label = str(point["area"]) + "_x" + str(int(x)) + "_z" + str(int(z))
            print("[site-gate] === candidate " + label + " ===", flush=True)
            if daemon.lease_token:
                daemon.session_heartbeat(daemon.lease_token)
            candidate = probe_candidate(daemon, label, x, z, out)
            report["candidates"].append(candidate)
            _atomic_write_text(out, json.dumps(report, indent=1, default=str))
            print(
                "[site-gate] " + label + " -> " + str(candidate.get("verdict"))
                + " (" + str(candidate.get("fail_stage", "PASS")) + ")",
                flush=True,
            )
            if candidate.get("verdict") == "PASS":
                report["canonical"] = {"label": label, "site": [x, 0.0, z]}
                report["row"] = "PROTOCOL_SITE_CERTIFIED"
                break
            if candidate.get("finish_status") == "CLEANUP_DEGRADED":
                # The library's degraded-cleanup branches can leave the
                # claim or the fixture behind; further candidates would run
                # on contaminated state and their evidence would be void.
                report["aborted"] = "cleanup_degraded"
                print("[site-gate] aborting: degraded cleanup", flush=True)
                break
    finally:
        if daemon.lease_token:
            try:
                release = daemon.session_release(daemon.lease_token)
                report["release"] = g._redact(release)
                release_confirmed = release.get("released") is True
            except Exception as error:
                report["release_error"] = str(error)
        try:
            report["final_session_status"] = g._redact(daemon.session_status())
        except Exception as error:
            report["final_session_status_error"] = str(error)

    report["release_confirmed"] = release_confirmed
    report["finished_utc"] = _now()
    _atomic_write_text(out, json.dumps(report, indent=1, default=str))
    print(
        "[site-gate] row=" + str(report["row"]) + " canonical=" + json.dumps(report["canonical"])
        + " release_confirmed=" + str(release_confirmed),
        flush=True,
    )
    if report["row"] != "PROTOCOL_SITE_CERTIFIED":
        return 3
    if not release_confirmed:
        # A certification whose session close is unconfirmed is not a clean
        # green: the site row stands in the JSON, but the exit code demands
        # an operator look (lifecycle_cleanup_pending or a lost release).
        return 5
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
