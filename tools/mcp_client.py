#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import mcp_capture


PHASE1_CAR_CLASSES = ["Hatchback_02", "Sedan_02", "Offroad_02", "CivilianSedan", "OffroadHatchback"]
MAX_DISPATCH_PER_TICK = 4
EXPECTED_MAX_QUEUE = 64
PHASE2_STATIC_TARGET_CLASS = "Land_Misc_Well_Pump_Blue"
PHASE2_FIXTURE_PATH = "$mission:dayz_mcp/telemetry_fixture.jsonl"
PHASE2_MISSING_FIXTURE_PATH = "$mission:dayz_mcp/missing_fixture.jsonl"
PHASE2_BAD_FIXTURE_PATH = "$mission:dayz_mcp/telemetry_bad.jsonl"
PHASE2_DYNAMIC_OFFSET = [8.0, 0.0, 0.0]
PHASE2_STATIC_OFFSET = [-8.0, 0.0, 0.0]
PHASE2_AMBIGUOUS_OFFSET = [0.0, 0.0, 8.0]
PHASE2_RAY_FROM_Y = 4.0
PHASE2_RAY_TO_Y = -2.0
PHASE2_DISTANCE_TOLERANCE_M = 5.0
PHASE2_POSITION_TOLERANCE_M = 3.0
PHASE2_RAY_RADIUS = 0.05
PHASE3_CAMERA_OFFSET = [3.0, 1.8, 3.0]
PHASE3_CAMERA_ORIENTATION = [0.0, 0.0, 15.0]
PHASE3_CAMERA_FOV = 1.0
PHASE3_MATRIX_TOLERANCE = 0.05
PHASE3_POSITION_TOLERANCE_M = 0.05
PHASE3_FOV_TOLERANCE = 0.001
# A2/A3 content validation: point the client camera at the player (subject) then at empty sky
# (control). A live render that actually follows camera_set yields two visibly different frames; a
# stale grab or a frozen render yields identical ones. delta thresholds separate the ~0.0 stale/frozen
# signature from a genuine view change (live diff is typically 0.1-0.4).
PHASE3_CONTENT_CAM_OFFSET = [2.5, 1.6, 2.5]
PHASE3_SUBJECT_LOOK_DY = 1.1
PHASE3_SKY_LOOK_DY = 40.0
PHASE3_CONTENT_FOV = 0.75
PHASE3_CONTENT_SETTLE_TICKS = 4
PHASE3_CONTENT_FOLLOW_DELTA = 0.02
PHASE3_CONTENT_CENTER_DELTA = 0.02
PHASE3_CONTENT_NONBLACK_MEAN = 16.0
PHASE3_CONTENT_NONBLACK_RATIO = 0.5
PHASE3_CONTENT_PROCESS = "DayZDiag_x64"
PHASE3_CONTENT_LIVENESS_SLEEP_S = 0.4
# camera_set's await returns when the bridge reports the interpolation settled, but the rendered
# frame still needs ~1-2s to be presented to the window surface before the host grab; without this
# the grab catches a transitional (near-black) frame (observed run_20260614_173908: subject
# nonBlackRatio 0.30, delta_live 0.84). Matches the 2s settle gate-mcp.ps1 / the diagnostic use.
PHASE3_CONTENT_GRAB_SETTLE_S = 2.0
# IN-WORLD READINESS GATE (closes the front-end false-pass, run_20260615_183811 + _185736).
# Root cause (confirmed empirically by a 70s grab time-series of a live fase3 client): after connect,
# the client sits on the main-menu / Health&Safety overlay ("CONTINUAR") with the world rendering
# UNLOADED behind it, then (~7-12s) the menu dismisses and the world streams in, settling (~20-22s)
# to a clean in-world 3rd-person view. Throughout, the engine is in-world (MissionGameplay ticks,
# camera_set acks ok=1). So camera_set ok=1 and a bright/non-black frame do NOT prove the clean view
# is presented. Worse, the loading-world-behind-the-menu phase ANIMATES (terrain LOD popping), so a
# pure "frame is animating" check also passes on the menu+loading composite (that was the second
# false-pass at iter1/3.6s).
#
# The robust, no-rebuild signal is the SETTLE SHAPE of the time-series, measured directly:
#   menu (t<=7s):    mean ~117, static between wide samples
#   transition:      one big inter-sample delta (~0.23) as the menu dismisses + world loads
#   loading:         decaying inter-sample deltas (~0.07)
#   clean in-world:  inter-sample deltas tiny (~0.005), STABLE, non-black, world brightness
# Readiness therefore requires ALL of: (a) a minimum settle floor has elapsed (lets the menu dismiss
# and the world load); (b) the view has STABILIZED -- the last N inter-sample deltas (samples spaced
# ~POLL_INTERVAL) are each below STABILITY_MAX (rejects the menu-dismiss/loading phase, which is still
# changing); (c) the last frame is non-black; (d) the mean falls in the settled-world brightness band
# (PHASE3_READY_MENU_MIN/MAX_MEAN). The scene is now pinned deterministically (run-fase3 init.c: fixed
# daytime + frozen overcast), so the world settles into a bounded brightness envelope; the band sits in
# the empty gaps that separate that envelope from BOTH the dim loading splash (below) and the bright
# menu/unloaded composite (above) -- see the band constants for the 223-frame measurement. The final
# verdict stays VISUAL (R22): the orchestrator inspects the PNG; this gate only guarantees past menu+load.
PHASE3_READY_BURST_FRAMES = 3            # frames per sample (for the within-sample liveness diagnostic)
PHASE3_READY_BURST_INTERVAL_S = 0.35
PHASE3_READY_INGAME_LIVE_MIN = 0.015     # within-burst animation tell (diagnostic / liveness-of-grab)
PHASE3_READY_POLL_INTERVAL_S = 3.0       # spacing between samples whose delta drives the stability test
PHASE3_READY_NONBLACK_MIN = 0.10
PHASE3_READY_STABILITY_MAX = 0.02        # settled world: consecutive wide-spaced samples ~0.005; loading ~0.07
PHASE3_READY_STABLE_SAMPLES = 2          # require this many consecutive stable inter-sample deltas
PHASE3_READY_MIN_SETTLE_S = 20.0         # menu dismissed by ~12s, world settled by ~22s in observed runs
# In-world brightness band, re-anchored 2026-06-15 on a 223-frame empirical distribution gathered with
# the scene now PINNED deterministically (run-fase3 init.c: fixed 10:00 date + frozen overcast 0.7 +
# SetTimeMultiplier(0); see its OnUpdate). Pinning removes the real-clock dependence but the CLIENT eye-
# adaptation still varies the settled mean run-to-run within a bounded envelope. Measured signatures
# (meanBrightness, nonBlackRatio) of every observed state:
#   dim loading splash:        ~41        nb ~0.92   (2 frames, the dimmest state)
#   settled in-world world:    ~50 .. ~80 nb ~0.97   (the dense cluster: 89% of in-world frames in [50,79])
#   menu/unloaded composite:   ~91 .. ~95 nb ~0.92   (never settles; a brighter front-end state)
#   menu chrome / disclaimer:  ~108 .. ~152 nb ~0.94 (the caught false-pass subject was 108.6)
# So the world lives in [~50, ~80] and BOTH front-end states sit outside it: loading below, menu above,
# with empty gaps [42,49] and [81,90] separating them. The band is set just inside those gaps:
PHASE3_READY_MENU_MAX_MEAN = 86.0        # reject menu/unloaded composite (>=~91) + disclaimer (~108+);
                                         # admits the settled-world high end (~80). 6 pts headroom over 80.
PHASE3_READY_MENU_MIN_MEAN = 48.0        # reject the dim loading splash (~41); admits the settled-world low
                                         # end (~50). The old floor 54 false-FAILED genuine 50-53 frames.
PHASE3_READY_CAMERA_TIMEOUT_S = 6.0      # short per-poll camera_set await: a loading client may not ack;
                                         # iterate quickly instead of blocking the full 90s default
PHASE3_READY_DEFAULT_TIMEOUT_S = 150.0


def parse_spawn(value: str) -> list[float]:
    parts = value.replace(",", " ").split()
    if len(parts) != 3:
        raise ValueError("--spawn must contain exactly three numbers")
    return [float(part) for part in parts]


def distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def add_pos(pos: list[float], offset: list[float]) -> list[float]:
    return [float(pos[i]) + float(offset[i]) for i in range(3)]


def vector_length(vec: list[float] | None) -> float | None:
    if vec is None:
        return None
    return math.sqrt(sum(float(value) ** 2 for value in vec))


def phase2_vertical_raycast_payload(pos: list[float], method: str) -> tuple[dict[str, Any], list[float], list[float]]:
    ray_from = [pos[0], pos[1] + PHASE2_RAY_FROM_Y, pos[2]]
    ray_to = [pos[0], pos[1] + PHASE2_RAY_TO_Y, pos[2]]
    payload = {
        "from": ray_from,
        "to": ray_to,
        "method": method,
        "ignore": "player",
        "radius": PHASE2_RAY_RADIUS,
    }
    return payload, ray_from, ray_to


def phase2_fixture_summary_from_text(text: str) -> dict[str, Any]:
    lines = text.split("\n") if text else []
    line_count = 0
    last_valid: dict[str, Any] | None = None
    for line in lines:
        line_count += 1
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            return {
                "mode": "fixture_jsonl",
                "found": False,
                "line_count_read": line_count,
                "last_valid": last_valid or {},
                "parse_error": f"JSONDecodeError: {exc.msg}",
            }
        if not isinstance(parsed, dict):
            return {
                "mode": "fixture_jsonl",
                "found": False,
                "line_count_read": line_count,
                "last_valid": last_valid or {},
                "parse_error": "non_object_json",
            }
        last_valid = parsed
    return {
        "mode": "fixture_jsonl",
        "found": True,
        "line_count_read": line_count,
        "last_valid": last_valid or {},
        "parse_error": "",
    }


class Client:
    def __init__(
        self,
        port: int,
        key: str,
        timeout_s: float,
        identity: dict[str, Any] | None = None,
        lease_token: str | None = None,
    ) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.key = key
        self.timeout_s = timeout_s
        self.identity = dict(identity) if identity is not None else None
        self.lease_token = lease_token
        self.samples: list[dict[str, Any]] = []

    def request_json(self, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
        params = dict(query or {})
        params["key"] = self.key
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        data = None
        headers = {}

        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)

    def raw_status(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        include_key: bool = False,
        key: str | None = None,
    ) -> int:
        params = dict(query or {})
        if include_key:
            params["key"] = self.key if key is None else key
        elif key is not None:
            params["key"] = key

        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5.0) as response:
                response.read()
                return int(response.status)
        except urllib.error.HTTPError as exc:
            exc.read()
            return int(exc.code)

    def enqueue(self, cmd: str) -> int:
        return self.enqueue_cmd(cmd, {})

    def enqueue_cmd(
        self,
        cmd: str,
        args: dict,
        peer: str | None = None,
        *,
        operation_timeout_s: float = 0.0,
    ) -> int:
        payload: dict[str, Any] = {"cmd": cmd, "args": args}
        if peer is not None:
            payload["peer"] = peer
        if self.identity is not None:
            payload["identity"] = dict(self.identity)
        if self.lease_token is not None:
            payload["lease_token"] = self.lease_token
        if operation_timeout_s:
            payload["operation_timeout_s"] = float(operation_timeout_s)
        response = self.request_json("POST", "/enqueue", payload)
        return int(response["id"])

    def enqueue_cmd_status(self, cmd: str, args: Any, peer: str | None = None) -> dict[str, Any]:
        try:
            command_id = self.enqueue_cmd(cmd, args, peer)
            return {"status": 200, "id": command_id, "error": None}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {}
            status = int(exc.code)
            if status in (400, 429):
                return {"status": status, "id": None, "error": body.get("error")}
            raise

    def await_result(self, command_id: int, timeout_s: float | None = None) -> dict:
        deadline = time.monotonic() + (timeout_s if timeout_s is not None else self.timeout_s)
        query = {"id": command_id}
        if self.identity is not None and self.lease_token is not None:
            query["remove"] = 1
        while time.monotonic() < deadline:
            response = self.request_json("GET", "/await", query=query)
            if response.get("status") == "done":
                result = response["result"]
                if not isinstance(result, dict):
                    raise RuntimeError(f"bad result type for id={command_id}")
                return result
            time.sleep(0.05)
        raise TimeoutError(f"timed out awaiting id={command_id}")

    def query_once(self, label: str, timeout_s: float | None = None) -> dict:
        t0 = time.monotonic()
        command_id = self.enqueue("query_player_state")
        result = self.await_result(command_id, timeout_s)
        t1 = time.monotonic()
        sample = {"label": label, "id": command_id, "client_rtt_s": t1 - t0, "result": result}
        self.samples.append(sample)
        return sample

    def set_poll_delay(self, ms: int) -> None:
        self.request_json("POST", "/set_poll_delay", {"ms": ms})


def read_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        key = handle.read().strip()
    if not key:
        raise ValueError("empty keyfile")
    return key


def state_pos(result: dict) -> list[float]:
    state = result.get("state")
    if not isinstance(state, dict):
        raise ValueError("missing state")
    pos = state.get("pos")
    if not isinstance(pos, list) or len(pos) != 3:
        raise ValueError("bad state.pos")
    return [float(pos[0]), float(pos[1]), float(pos[2])]


def result_tick(result: dict, name: str) -> int:
    value = result.get(name)
    if value is None:
        raise ValueError(f"missing {name}")
    return int(value)


def server_rtt(result: dict) -> float | None:
    meta = result.get("_server")
    if not isinstance(meta, dict):
        return None
    value = meta.get("rtt_s")
    if value is None:
        return None
    return float(value)


def run_a4_security(client: Client) -> dict[str, Any]:
    cases = [
        ("poll_missing_key", "GET", "/poll", None, {}, False, None, 401),
        ("poll_wrong_key", "GET", "/poll", None, {}, False, client.key + "-wrong", 401),
        ("enqueue_missing_key", "POST", "/enqueue", {"cmd": "query_player_state", "args": {}}, {}, False, None, 401),
        ("enqueue_evil_not_whitelisted", "POST", "/enqueue", {"cmd": "evil", "args": {}}, {}, True, None, 400),
    ]
    checks: list[dict[str, Any]] = []

    for name, method, path, payload, query, include_key, key, expected in cases:
        status: int | None = None
        error = ""
        try:
            status = client.raw_status(method, path, payload, query, include_key, key)
        except urllib.error.URLError as exc:
            error = f"URLError: {exc}"
        except OSError as exc:
            error = f"OSError: {exc}"

        checks.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "expected_status": expected,
                "status": status,
                "pass": status == expected,
                "error": error,
            }
        )

    return {"pass": all(item["pass"] for item in checks), "checks": checks}


def run_verdict(args: argparse.Namespace) -> dict:
    spawn = parse_spawn(args.spawn)
    client = Client(args.port, read_key(args.keyfile), args.timeout)

    tests: dict[str, dict[str, Any]] = {}

    tests["A4_fail_closed_security"] = run_a4_security(client)

    a1 = client.query_once("A1")
    a1_result = a1["result"]
    a1_pos = state_pos(a1_result) if a1_result.get("ok") else [0.0, 0.0, 0.0]
    a1_distance = distance(a1_pos, spawn) if a1_result.get("ok") else None
    tests["A1_authoritative_position"] = {
        "pass": bool(a1_result.get("ok")) and a1_distance is not None and a1_distance < 0.5,
        "id": a1["id"],
        "spawn": spawn,
        "pos": a1_pos,
        "distance_m": a1_distance,
        "error": a1_result.get("error"),
    }

    baseline_1 = client.query_once("baseline_1")
    time.sleep(1.0)
    baseline_2 = client.query_once("baseline_2")
    b1_result = baseline_1["result"]
    b2_result = baseline_2["result"]
    tick_delta = result_tick(b2_result, "tick_dispatch") - result_tick(b1_result, "tick_dispatch")
    wall_delta = baseline_2["client_rtt_s"] + 1.0
    baseline_fps = tick_delta / wall_delta if wall_delta > 0 else None

    delay_ms = 600
    client.set_poll_delay(delay_ms)
    a2 = client.query_once("A2_delay", timeout_s=max(args.timeout, 10.0))
    a2_result = a2["result"]
    ticks_in_flight = result_tick(a2_result, "tick_poll_callback") - result_tick(a2_result, "tick_poll_sent")
    tests["A2_async_nonblocking"] = {
        "pass": bool(a2_result.get("ok")) and ticks_in_flight >= 5,
        "id": a2["id"],
        "delay_ms": delay_ms,
        "ticks_in_flight": ticks_in_flight,
        "fps_in_flight": ticks_in_flight / (delay_ms / 1000.0),
        "baseline_tick_delta": tick_delta,
        "baseline_fps_estimate": baseline_fps,
        "error": a2_result.get("error"),
    }

    id1 = client.enqueue("query_player_state")
    id2 = client.enqueue("query_player_state")
    r1 = client.await_result(id1)
    r2 = client.await_result(id2)
    pos1 = state_pos(r1) if r1.get("ok") else [0.0, 0.0, 0.0]
    pos2 = state_pos(r2) if r2.get("ok") else [0.0, 0.0, 0.0]
    tests["A3_correlation_ids"] = {
        "pass": id1 != id2 and int(r1.get("id", -1)) == id1 and int(r2.get("id", -1)) == id2 and bool(r1.get("ok")) and bool(r2.get("ok")),
        "ids": [id1, id2],
        "result_ids": [r1.get("id"), r2.get("id")],
        "pos_distance_m": distance(pos1, pos2) if r1.get("ok") and r2.get("ok") else None,
        "errors": [r1.get("error"), r2.get("error")],
    }

    rtts = []
    for sample in client.samples:
        rtt = server_rtt(sample["result"])
        if rtt is None:
            rtt = sample["client_rtt_s"]
        rtts.append(rtt)

    rtt_summary = {
        "count": len(rtts),
        "min_s": min(rtts) if rtts else None,
        "median_s": statistics.median(rtts) if rtts else None,
        "max_s": max(rtts) if rtts else None,
    }

    return {
        "overall_pass": all(item["pass"] for item in tests.values()),
        "tests": tests,
        "rtt_summary": rtt_summary,
        "samples": client.samples,
    }


def pos3_or_none(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dayz_true(value: Any) -> bool:
    return value is True or value == 1 or value == "1"


def dayz_false(value: Any) -> bool:
    return value is False or value == 0 or value == "0"


def result_error_case(name: str, expected: str, result: dict[str, Any]) -> dict[str, Any]:
    got = result.get("error")
    return {
        "name": name,
        "expected": expected,
        "got": got,
        "pass": dayz_false(result.get("ok")) and got == expected,
        "id": result.get("id"),
        "result": result,
    }


def http_error_case(name: str, expected_status: int, expected_error: str, status_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "expected": f"HTTP {expected_status} {expected_error}",
        "got": f"HTTP {status_result.get('status')} {status_result.get('error')}",
        "pass": status_result.get("status") == expected_status and status_result.get("error") == expected_error,
        "status": status_result.get("status"),
        "id": status_result.get("id"),
        "error": status_result.get("error"),
    }


def phase2_raycast_hit_case(
    name: str,
    result: dict[str, Any],
    expected_type: str,
    ray_from: list[float],
    expected_target_pos: list[float],
) -> dict[str, Any]:
    raycast = result.get("raycast") if isinstance(result.get("raycast"), dict) else {}
    hit_pos = pos3_or_none(raycast.get("pos"))
    normal = pos3_or_none(raycast.get("normal"))
    normal_len = vector_length(normal)
    reported_distance = float_or_none(raycast.get("distance"))
    expected_distance = distance(ray_from, expected_target_pos)
    distance_error = None
    if reported_distance is not None:
        distance_error = abs(reported_distance - expected_distance)
    object_type = raycast.get("object_type") or ""
    parent_type = raycast.get("parent_type") or ""
    hier_level = int_or_none(raycast.get("hier_level")) or 0
    type_ok = object_type == expected_type or parent_type == expected_type
    proxy_parent_ok = True
    if hier_level > 0:
        proxy_parent_ok = parent_type == expected_type
    passed = (
        dayz_true(result.get("ok"))
        and dayz_true(raycast.get("hit"))
        and type_ok
        and normal_len is not None
        and normal_len > 0.001
        and distance_error is not None
        and distance_error <= PHASE2_DISTANCE_TOLERANCE_M
        and proxy_parent_ok
    )
    return {
        "name": name,
        "pass": passed,
        "method": raycast.get("method"),
        "expected_type": expected_type,
        "object_type": object_type,
        "parent_type": parent_type,
        "hier_level": hier_level,
        "proxy_parent_ok": proxy_parent_ok,
        "expected_distance_m": expected_distance,
        "distance_m": reported_distance,
        "distance_error_m": distance_error,
        "normal": normal,
        "normal_len": normal_len,
        "hit_pos": hit_pos,
        "error": result.get("error"),
        "result": result,
    }


def phase2_raycast_empty_case(name: str, result: dict[str, Any]) -> dict[str, Any]:
    raycast = result.get("raycast") if isinstance(result.get("raycast"), dict) else {}
    return {
        "name": name,
        "pass": dayz_true(result.get("ok")) and dayz_false(raycast.get("hit")),
        "ok": result.get("ok"),
        "hit": raycast.get("hit"),
        "error": result.get("error"),
        "result": result,
    }


def phase2_object_at_case(name: str, result: dict[str, Any], expected_type: str, expected_pos: list[float]) -> dict[str, Any]:
    telemetry = result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {}
    pos = pos3_or_none(telemetry.get("pos"))
    pos_error = distance(pos, expected_pos) if pos is not None else None
    health01 = float_or_none(telemetry.get("health01"))
    passed = (
        dayz_true(result.get("ok"))
        and dayz_true(telemetry.get("found"))
        and telemetry.get("type") == expected_type
        and pos_error is not None
        and pos_error <= PHASE2_POSITION_TOLERANCE_M
        and health01 is not None
        and 0.0 <= health01 <= 1.0
    )
    return {
        "name": name,
        "pass": passed,
        "expected_type": expected_type,
        "type": telemetry.get("type"),
        "class_name": telemetry.get("class_name"),
        "expected_pos": expected_pos,
        "pos": pos,
        "pos_error_m": pos_error,
        "health01": health01,
        "attachment_count": telemetry.get("attachment_count"),
        "cargo_count": telemetry.get("cargo_count"),
        "error": result.get("error"),
        "result": result,
    }


def phase2_fixture_case(name: str, result: dict[str, Any]) -> dict[str, Any]:
    telemetry = result.get("telemetry") if isinstance(result.get("telemetry"), dict) else result
    last_valid = telemetry.get("last_valid") if isinstance(telemetry.get("last_valid"), dict) else {}
    parse_error = telemetry.get("parse_error")
    passed = (
        dayz_true(result.get("ok", True))
        and dayz_true(telemetry.get("found"))
        and int_or_none(telemetry.get("line_count_read")) == 2
        and last_valid.get("fixture_id") == "fx2"
        and float_or_none(last_valid.get("value")) == 7.5
        and int_or_none(last_valid.get("seq")) == 1
        and (parse_error is None or parse_error == "")
    )
    return {
        "name": name,
        "pass": passed,
        "found": telemetry.get("found"),
        "line_count_read": telemetry.get("line_count_read"),
        "last_valid": last_valid,
        "parse_error": parse_error,
        "error": result.get("error") if isinstance(result, dict) else None,
        "result": result,
    }


def phase2_found_false_case(name: str, result: dict[str, Any]) -> dict[str, Any]:
    telemetry = result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {}
    return {
        "name": name,
        "pass": dayz_true(result.get("ok")) and dayz_false(telemetry.get("found")),
        "ok": result.get("ok"),
        "found": telemetry.get("found"),
        "error": result.get("error"),
        "result": result,
    }


def phase2_spawn_object(client: Client, obj_type: str, pos: list[float], timeout_s: float) -> tuple[int, dict[str, Any]]:
    return run_result(client, "world_spawn", {"type": obj_type, "pos": pos}, timeout_s=timeout_s)


def await_many(client: Client, command_ids: list[int], timeout_s: float) -> tuple[dict[int, dict[str, Any]], list[int]]:
    deadline = time.monotonic() + timeout_s
    pending = set(command_ids)
    results: dict[int, dict[str, Any]] = {}

    while pending and time.monotonic() < deadline:
        for command_id in list(pending):
            response = client.request_json("GET", "/await", query={"id": command_id})
            if response.get("status") == "done":
                result = response.get("result")
                if isinstance(result, dict):
                    results[command_id] = result
                else:
                    results[command_id] = {"ok": False, "error": "bad_result_type", "id": command_id}
                pending.remove(command_id)
        if pending:
            time.sleep(0.05)

    return results, sorted(pending)


def run_result(
    client: Client,
    cmd: str,
    payload: dict[str, Any],
    timeout_s: float | None = None,
    peer: str | None = None,
) -> tuple[int, dict[str, Any]]:
    operation_timeout_s = client.timeout_s if timeout_s is None else timeout_s
    command_id = client.enqueue_cmd(
        cmd,
        payload,
        peer,
        operation_timeout_s=operation_timeout_s,
    )
    return command_id, client.await_result(command_id, timeout_s)


def phase3_expected_orient_matrix(pos: list[float]) -> list[float]:
    roll = math.radians(PHASE3_CAMERA_ORIENTATION[2])
    c = math.cos(roll)
    s = math.sin(roll)
    # DayZ Math3D.YawPitchRollMatrix convention (observed via SetOrientation->GetTransform):
    # for +roll about the forward axis, right=(c,-s,0) and up=(s,c,0). The opposite handedness
    # (right=(c,+s,0)) does NOT match the engine. In-game the mod passes the orientation straight
    # to SetOrientation, so the engine output is the faithful command; pos/fov/roll-magnitude were
    # exact and only this sign differed (matrix_max_error was exactly 2*sin(roll)=0.5176 at 15deg).
    return [
        c,
        -s,
        0.0,
        s,
        c,
        0.0,
        0.0,
        0.0,
        1.0,
        pos[0],
        pos[1],
        pos[2],
    ]


def max_abs_error(values: list[float], expected: list[float], indices: list[int]) -> float | None:
    if len(values) < max(indices) + 1 or len(expected) < max(indices) + 1:
        return None
    return max(abs(float(values[index]) - float(expected[index])) for index in indices)


def phase3_camera_case(
    name: str,
    result: dict[str, Any],
    expected_pos: list[float],
    expected_matrix: list[float],
    expected_fov: float,
) -> dict[str, Any]:
    camera = result.get("camera") if isinstance(result.get("camera"), dict) else {}
    pos = pos3_or_none(camera.get("pos"))
    matrix_value = camera.get("matrix")
    matrix = [float(value) for value in matrix_value] if isinstance(matrix_value, list) and len(matrix_value) == 12 else []
    fov = float_or_none(camera.get("fov"))
    matrix_indices = [0, 1, 3, 4, 9, 10, 11]
    matrix_error = max_abs_error(matrix, expected_matrix, matrix_indices) if matrix else None
    pos_error = distance(pos, expected_pos) if pos is not None else None
    fov_error = abs(fov - expected_fov) if fov is not None else None
    roll_signal = bool(matrix) and (abs(matrix[1]) > 0.05 or abs(matrix[3]) > 0.05)
    exact_match_suspect = matrix_error == 0.0 and pos_error == 0.0 and fov_error == 0.0

    return {
        "name": name,
        "pass": (
            dayz_true(result.get("ok"))
            and dayz_true(camera.get("ok"))
            and dayz_true(camera.get("viewport_moved"))
            and pos_error is not None
            and pos_error <= PHASE3_POSITION_TOLERANCE_M
            and matrix_error is not None
            and matrix_error <= PHASE3_MATRIX_TOLERANCE
            and fov_error is not None
            and fov_error <= PHASE3_FOV_TOLERANCE
            and roll_signal
        ),
        "id": result.get("id"),
        "ok": result.get("ok"),
        "camera_ok": camera.get("ok"),
        "viewport_moved": camera.get("viewport_moved"),
        "applied_mode": camera.get("applied_mode"),
        "pos_error_m": pos_error,
        "matrix_max_error": matrix_error,
        "matrix_indices_compared": matrix_indices,
        "roll_signal": roll_signal,
        "fov_error": fov_error,
        "exact_match_suspect": exact_match_suspect,
        "error": result.get("error") or camera.get("error"),
        "result": result,
    }


def run_phase3_security(client: Client) -> dict[str, Any]:
    cases = [
        ("client_poll_missing_key", "GET", "/poll", None, {"peer": "client"}, False, None, 401),
        ("client_poll_wrong_key", "GET", "/poll", None, {"peer": "client"}, False, client.key + "-wrong", 401),
        ("enqueue_evil_not_whitelisted", "POST", "/enqueue", {"cmd": "evil", "args": {}, "peer": "client"}, {}, True, None, 400),
    ]
    checks: list[dict[str, Any]] = []

    for name, method, path, payload, query, include_key, key, expected in cases:
        status: int | None = None
        error = ""
        try:
            status = client.raw_status(method, path, payload, query, include_key, key)
        except urllib.error.URLError as exc:
            error = f"URLError: {exc}"
        except OSError as exc:
            error = f"OSError: {exc}"

        checks.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "expected_status": expected,
                "status": status,
                "pass": status == expected,
                "error": error,
            }
        )

    return {"pass": all(item["pass"] for item in checks), "checks": checks}


def phase3_capture_cases(capture: dict[str, Any], max_tokens: int, content: dict[str, Any] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    data = capture.get("data") if isinstance(capture.get("data"), str) else ""
    budget_chars = int(max_tokens * mcp_capture.CHARS_PER_TOKEN)
    job_id_present = any(key in capture for key in ("job_id", "jobId", "poll"))
    image_ok = (
        capture.get("type") == "image"
        and capture.get("mimeType") in ("image/png", "image/jpeg")
        and bool(data)
        and capture.get("isError") is not True
    )
    stats: dict[str, Any] = {}
    stats_error = ""
    if image_ok:
        try:
            stats = mcp_capture.image_content_stats(capture)
        except Exception as exc:
            stats_error = f"{type(exc).__name__}: {exc}"

    mean_brightness = float_or_none(stats.get("meanBrightness"))
    non_black_ratio = float_or_none(stats.get("nonBlackRatio"))
    base = {
        "capture_is_error": capture.get("isError") is True,
        "error": capture.get("error") or stats_error,
        "type": capture.get("type"),
        "mimeType": capture.get("mimeType"),
        "base64_len": len(data),
        "budget_chars": budget_chars,
    }
    tests = {
        "D2_capture_nonblack": {
            **base,
            "pass": image_ok and mean_brightness is not None and mean_brightness > 16.0 and non_black_ratio is not None and non_black_ratio > 0.5,
            "meanBrightness": mean_brightness,
            "nonBlackRatio": non_black_ratio,
            "thresholds": {"meanBrightness_gt": 16.0, "nonBlackRatio_gt": 0.5},
        },
        "D2_capture_budget": {
            **base,
            "pass": image_ok and len(data) <= budget_chars,
        },
        "D2_capture_synchronous": {
            **base,
            "pass": image_ok and not job_id_present,
            "job_id_present": job_id_present,
        },
    }
    if content is not None:
        delta_follow = float_or_none(content.get("delta_follow"))
        delta_center = float_or_none(content.get("delta_center"))
        delta_live = float_or_none(content.get("delta_live"))
        subject_mean = float_or_none((content.get("subject_stats") or {}).get("meanBrightness"))
        subject_nonblack = float_or_none((content.get("subject_stats") or {}).get("nonBlackRatio"))
        subject_present = (
            subject_mean is not None and subject_mean > PHASE3_CONTENT_NONBLACK_MEAN
            and subject_nonblack is not None and subject_nonblack > PHASE3_CONTENT_NONBLACK_RATIO
        )
        camera_follows = (
            delta_follow is not None and delta_follow >= PHASE3_CONTENT_FOLLOW_DELTA
            and delta_center is not None and delta_center >= PHASE3_CONTENT_CENTER_DELTA
        )
        # LOAD-BEARING gate that closes the front-end false-pass (run_20260615_183811): the in-world
        # VIEW must have been confirmed presented (sustained pixel animation) before the grabs.
        # subject_inworld_live is False for a static/one-shot launch overlay, so a menu/loading/
        # disclaimer frame can no longer pass even though it is bright (subject_present) and the two
        # grabs differ (camera_follows) — both of which the front-end satisfied spuriously. The old
        # checks remain as supporting evidence; readiness is what makes "D2 passed" == "the player was
        # rendered in the world when we captured".
        readiness = content.get("readiness") if isinstance(content.get("readiness"), dict) else {}
        subject_inworld_live = bool(content.get("subject_inworld_live"))
        tests["D2_capture_content"] = {
            "pass": (
                subject_inworld_live
                and bool(content.get("subject_capture_ok"))
                and bool(content.get("control_capture_ok"))
                and camera_follows
                and subject_present
            ),
            "subject_inworld_live": subject_inworld_live,
            "readiness_iterations": readiness.get("iterations"),
            "readiness_elapsed_s": readiness.get("elapsed_s"),
            "readiness_max_neighbor_delta": (readiness.get("last_burst") or {}).get("max_neighbor_delta"),
            "ingame_live_min": readiness.get("ingame_live_min"),
            "subject_capture_ok": content.get("subject_capture_ok"),
            "control_capture_ok": content.get("control_capture_ok"),
            "subject_camera_set_ok": content.get("subject_camera_set_ok"),
            "control_camera_set_ok": content.get("control_camera_set_ok"),
            "delta_follow": delta_follow,
            "delta_center": delta_center,
            "delta_live": delta_live,
            "subject_meanBrightness": subject_mean,
            "subject_nonBlackRatio": subject_nonblack,
            "camera_follows": camera_follows,
            "subject_present": subject_present,
            "stale_frame_suspect": delta_follow == 0.0 or delta_live == 0.0,
            "grab_method": content.get("grab_method"),
            "thresholds": {"follow_delta_gte": PHASE3_CONTENT_FOLLOW_DELTA, "center_delta_gte": PHASE3_CONTENT_CENTER_DELTA, "ingame_live_min": PHASE3_READY_INGAME_LIVE_MIN},
            "error": content.get("error"),
        }
    summary = {
        **base,
        "image_ok": image_ok,
        "stats": stats,
        "job_id_present": job_id_present,
        "content": {k: v for k, v in content.items() if k != "subject_capture"} if isinstance(content, dict) else None,
    }
    return tests, summary


def camera_lookat_command(
    client: Client,
    cam_pos: list[float],
    look_at: list[float],
    args: argparse.Namespace,
    fov: float = PHASE3_CONTENT_FOV,
    settle_ticks: int = PHASE3_CONTENT_SETTLE_TICKS,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    payload = {
        "cam_mode": "lookat",
        "cam_pos": [float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])],
        "look_at": [float(look_at[0]), float(look_at[1]), float(look_at[2])],
        "fov": fov,
        "settle_ticks": settle_ticks,
    }
    to = timeout_s if timeout_s is not None else max(args.timeout, 20.0)
    _, result = run_result(client, "camera_set", payload, timeout_s=to, peer="client")
    return result


def grab_burst_max_liveness(
    dest_dir: str,
    prefix: str,
    client_pid: int,
    frames: int = PHASE3_READY_BURST_FRAMES,
    interval_s: float = PHASE3_READY_BURST_INTERVAL_S,
    cmdline_match: str = "",
) -> dict[str, Any]:
    """Burst-grab the fase3 client window and report the MAX neighbour pixel delta across the burst.
    A continuously animating in-world view spikes this (>=0.015); a static front-end overlay stays
    near 0. Returns {ok, frames:[paths], max_neighbor_delta, last_path, last_nonblack, grabs:[...]}."""
    paths: list[str] = []
    grabs: list[dict[str, Any]] = []
    for k in range(frames):
        fp = os.path.join(dest_dir, f"{prefix}_{k:02d}.png")
        g = mcp_capture.grab_window_to_file(fp, process_name=PHASE3_CONTENT_PROCESS, method="auto", client_pid=client_pid, cmdline_match=cmdline_match)
        grabs.append({
            key: g.get(key)
            for key in (
                "ok",
                "error",
                "method",
                "window",
                "stats",
                "client",
                "clientStats",
                "sha256",
            )
        })
        if g.get("ok") is True and os.path.exists(fp):
            paths.append(fp)
        if k + 1 < frames:
            time.sleep(interval_s)

    out: dict[str, Any] = {
        "ok": len(paths) >= 1,
        "frames": paths,
        "grabs": grabs,
        "max_neighbor_delta": 0.0,
        "last_path": paths[-1] if paths else None,
        "last_nonblack": None,
    }
    if not paths:
        return out

    imgs = [mcp_capture.load_rgb(p) for p in paths]
    if len(imgs) >= 2:
        neigh = [mcp_capture.mean_abs_pixel_delta(imgs[i - 1], imgs[i]) for i in range(1, len(imgs))]
        out["max_neighbor_delta"] = max(neigh) if neigh else 0.0
        out["neighbor_deltas"] = [round(x, 5) for x in neigh]
    out["last_nonblack"] = mcp_capture.image_stats_from_image(imgs[-1])["nonBlackRatio"]
    return out


def classify_inworld_settle(
    deltas: list[float],
    elapsed_s: float,
    last_mean: float | None,
    last_nonblack: float | None,
    live_seen: bool,
) -> bool:
    """Readiness verdict from the settle time-series (see PHASE3_READY_* header for the empirical
    shape). True only when the view has reached the clean settled in-world state and we are past the
    menu/loading phase. Each clause rejects a distinct OBSERVED false state:
      - elapsed < MIN_SETTLE        -> too early (still on the menu / mid-load); the dim loading
                                       splash only exists in this early window, never after settle
      - last frame black            -> not rendering
      - last mean outside [MENU_MIN_MEAN, MENU_MAX_MEAN] (=[48,86]) -> a front-end state, not the world.
                                       Empirically (223 frames, pinned scene) the settled world is ~50-80
                                       (nb ~0.97); below it sits the dim loading splash (~41); above it the
                                       menu/unloaded composite (~91-95, nb ~0.92) and menu chrome /
                                       disclaimer (~108-152). The band sits in the empty gaps [42,49] and
                                       [81,90] that separate the world from both front-end states
      - < STABLE_SAMPLES deltas, or any recent inter-sample delta >= STABILITY_MAX -> still changing
                                       (menu dismiss / world streaming in)
    `live_seen` (an observed within-burst animation spike) is reported but NOT required: a fully idle
    player in a still scene yields a static clean in-world view (within-burst deltas ~0.004), so
    requiring liveness false-rejected real in-world frames (run_20260615_192951, a clean player shot).
    The brightness band + stability + min-settle reject every front-end state seen in practice."""
    if elapsed_s < PHASE3_READY_MIN_SETTLE_S:
        return False
    if last_nonblack is None or last_nonblack < PHASE3_READY_NONBLACK_MIN:
        return False
    if last_mean is None or last_mean < PHASE3_READY_MENU_MIN_MEAN or last_mean > PHASE3_READY_MENU_MAX_MEAN:
        return False
    if len(deltas) < PHASE3_READY_STABLE_SAMPLES:
        return False
    recent = deltas[-PHASE3_READY_STABLE_SAMPLES:]
    return all(d < PHASE3_READY_STABILITY_MAX for d in recent)


def wait_for_inworld_render(
    client: Client,
    cam_pos: list[float],
    look_at: list[float],
    args: argparse.Namespace,
    client_pid: int,
    timeout_s: float,
) -> dict[str, Any]:
    """Poll until the clean in-world VIEW is actually PRESENTED to the window (not merely the engine
    in-world). The client sits on the main-menu/Health&Safety overlay with the world loading behind it
    for ~7-22s after connect (camera_set acks ok=1 the whole time), then settles to the clean view.
    We sample the window every ~POLL_INTERVAL (re-issuing lookat(player) each time) and track the
    inter-sample deltas + brightness; readiness fires only once the series has SETTLED past the
    menu/loading phase (classify_inworld_settle). A frozen/menu/loading frame never satisfies this, so
    the content gate is fail-closed. Returns {inworld, attempts, samples, elapsed_s, ...}."""
    tmp_dir = tempfile.mkdtemp(prefix="phase3_ready_")
    deadline = time.monotonic() + max(timeout_s, PHASE3_READY_MIN_SETTLE_S + 10.0)
    t0 = time.monotonic()
    attempts: list[dict[str, Any]] = []
    deltas: list[float] = []
    prev_img = None
    last_burst: dict[str, Any] = {}
    last_mean: float | None = None
    last_nonblack: float | None = None
    live_seen = False
    inworld = False
    iteration = 0

    while time.monotonic() < deadline:
        iteration += 1
        # Short await: a still-loading client polls sparsely and may not ack within a few seconds. Do
        # not block the full default timeout here -- iterate and let the settle-shape decide over time.
        try:
            cam = camera_lookat_command(client, cam_pos, look_at, args, timeout_s=PHASE3_READY_CAMERA_TIMEOUT_S)
            cam_ok = bool(dayz_true(cam.get("ok")))
        except TimeoutError:
            cam_ok = False
        # A tiny within-sample burst gives the liveness tell: in-world ambient/idle-sway animates the
        # frame within the burst; a frozen static screen does not. Latched across the whole wait.
        burst = grab_burst_max_liveness(tmp_dir, f"ready_{iteration:02d}", client_pid, cmdline_match=getattr(args, "client_cmdline_match", ""))
        last_burst = burst
        within_live = burst.get("max_neighbor_delta", 0.0)
        if within_live >= PHASE3_READY_INGAME_LIVE_MIN:
            live_seen = True
        sample_path = burst.get("last_path")
        inter_delta = None
        if sample_path and os.path.exists(sample_path):
            img = mcp_capture.load_rgb(sample_path)
            stats = mcp_capture.image_stats_from_image(img)
            last_mean = float(stats["meanBrightness"])
            last_nonblack = float(stats["nonBlackRatio"])
            if prev_img is not None:
                inter_delta = mcp_capture.mean_abs_pixel_delta(prev_img, img)
                deltas.append(inter_delta)
            prev_img = img

        elapsed = time.monotonic() - t0
        settled = classify_inworld_settle(deltas, elapsed, last_mean, last_nonblack, live_seen)
        attempts.append({
            "iteration": iteration,
            "elapsed_s": round(elapsed, 1),
            "camera_set_ok": cam_ok,
            "grab_ok": burst.get("ok") is True,
            "within_burst_delta": round(within_live, 5),
            "live_seen": live_seen,
            "inter_sample_delta": None if inter_delta is None else round(inter_delta, 5),
            "mean": None if last_mean is None else round(last_mean, 1),
            "nonblack": None if last_nonblack is None else round(last_nonblack, 4),
            "settled": settled,
        })
        if settled:
            inworld = True
            break
        time.sleep(PHASE3_READY_POLL_INTERVAL_S)

    return {
        "inworld": inworld,
        "iterations": iteration,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "timeout_s": timeout_s,
        "live_seen": live_seen,
        "inter_sample_deltas": [round(d, 5) for d in deltas],
        "last_mean": None if last_mean is None else round(last_mean, 1),
        "last_nonblack": None if last_nonblack is None else round(last_nonblack, 4),
        "attempts": attempts,
        "last_burst": {k: v for k, v in last_burst.items() if k != "frames"},
        "thresholds": {
            "min_settle_s": PHASE3_READY_MIN_SETTLE_S,
            "stability_max": PHASE3_READY_STABILITY_MAX,
            "stable_samples": PHASE3_READY_STABLE_SAMPLES,
            "menu_max_mean": PHASE3_READY_MENU_MAX_MEAN,
            "nonblack_min": PHASE3_READY_NONBLACK_MIN,
        },
    }


def run_phase3_content(client: Client, spawn: list[float], args: argparse.Namespace, readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    """A2/A3 end-to-end content validation, GATED ON IN-WORLD READINESS.

    Step 1 (readiness): poll until the window has SETTLED into the clean in-world view, past the
    main-menu/Health&Safety overlay and the world-loading phase that follow connect (see
    wait_for_inworld_render / PHASE3_READY_* header). This closes the front-end false-pass: camera_set
    acks ok=1 and a bright/non-black frame appear while the menu+loading composite is still shown, so
    the old "camera ok + bright frame + frame changed" check passed on it; the settle-shape gate does
    not. If the clean view never presents, readiness is False and the content gate fails closed.

    Step 2 (content): only after readiness, lookat(player) -> subject grab, lookat(sky) -> control
    grab, and diff the pixels. subject_inworld_live carries the readiness verdict so the gate is
    fail-closed."""
    client_pid = int(getattr(args, "client_pid", 0) or 0)
    cmdline_match = getattr(args, "client_cmdline_match", "")
    cam_pos = add_pos(spawn, PHASE3_CONTENT_CAM_OFFSET)
    subject_target = [spawn[0], spawn[1] + PHASE3_SUBJECT_LOOK_DY, spawn[2]]
    sky_target = [cam_pos[0], cam_pos[1] + PHASE3_SKY_LOOK_DY, cam_pos[2]]
    evidence_dir = os.path.dirname(os.path.abspath(args.output))
    subject_path = os.path.join(evidence_dir, "fase3-evidence-subject.png")
    control_path = os.path.join(evidence_dir, "fase3-evidence-control.png")
    tmp_dir = tempfile.mkdtemp(prefix="phase3_content_")
    subject_live_path = os.path.join(tmp_dir, "subject_live.png")

    out: dict[str, Any] = {
        "cam_pos": cam_pos,
        "subject_target": subject_target,
        "sky_target": sky_target,
        "client_pid": client_pid,
        "evidence_subject_png": subject_path,
        "evidence_control_png": control_path,
    }

    # Readiness is computed at the START of run_phase3 (before the D1 camera tests, which otherwise
    # time out while the client is still loading) and passed in; recompute only if absent.
    if readiness is None:
        ready_timeout = max(getattr(args, "ready_timeout", PHASE3_READY_DEFAULT_TIMEOUT_S), 10.0)
        readiness = wait_for_inworld_render(client, cam_pos, subject_target, args, client_pid, ready_timeout)
    out["readiness"] = readiness
    out["subject_inworld_live"] = bool(readiness.get("inworld"))
    if not readiness.get("inworld"):
        # In-world view never presented (front-end overlay or load never cleared). Still grab once so
        # the evidence PNG shows WHAT was on screen (for diagnosis), but the content gate fails closed.
        subj_grab = mcp_capture.grab_window_to_file(subject_path, process_name=PHASE3_CONTENT_PROCESS, method="auto", client_pid=client_pid, cmdline_match=cmdline_match)
        out["subject_capture_ok"] = subj_grab.get("ok") is True
        out["grab_method"] = subj_grab.get("method")
        out["subject_grab"] = subj_grab
        out["control_capture_ok"] = False
        out["error"] = "inworld_render_not_ready"
        if out["subject_capture_ok"]:
            out["subject_capture"] = mcp_capture.image_content_from_file(subject_path, scale=args.capture_scale, max_tokens=args.capture_max_tokens)
        return out

    subj_cam = camera_lookat_command(client, cam_pos, subject_target, args)
    out["subject_camera_set_ok"] = bool(dayz_true(subj_cam.get("ok")))
    out["subject_camera_error"] = subj_cam.get("error")
    time.sleep(PHASE3_CONTENT_GRAB_SETTLE_S)
    subj_grab = mcp_capture.grab_window_to_file(subject_path, process_name=PHASE3_CONTENT_PROCESS, method="auto", client_pid=client_pid, cmdline_match=cmdline_match)
    out["subject_capture_ok"] = subj_grab.get("ok") is True
    out["grab_method"] = subj_grab.get("method")
    out["subject_grab"] = subj_grab

    time.sleep(PHASE3_CONTENT_LIVENESS_SLEEP_S)
    subj_live_grab = mcp_capture.grab_window_to_file(subject_live_path, process_name=PHASE3_CONTENT_PROCESS, method="auto", client_pid=client_pid, cmdline_match=cmdline_match)
    out["subject_live_capture_ok"] = subj_live_grab.get("ok") is True

    ctrl_cam = camera_lookat_command(client, cam_pos, sky_target, args)
    out["control_camera_set_ok"] = bool(dayz_true(ctrl_cam.get("ok")))
    out["control_camera_error"] = ctrl_cam.get("error")
    time.sleep(PHASE3_CONTENT_GRAB_SETTLE_S)
    ctrl_grab = mcp_capture.grab_window_to_file(control_path, process_name=PHASE3_CONTENT_PROCESS, method="auto", client_pid=client_pid, cmdline_match=cmdline_match)
    out["control_capture_ok"] = ctrl_grab.get("ok") is True
    out["control_grab"] = ctrl_grab

    if out["subject_capture_ok"] and out["control_capture_ok"]:
        liveness = subject_live_path if out.get("subject_live_capture_ok") else None
        out.update(mcp_capture.compare_captures(subject_path, control_path, liveness_path=liveness))
        out["subject_capture"] = mcp_capture.image_content_from_file(subject_path, scale=args.capture_scale, max_tokens=args.capture_max_tokens)
    else:
        out["error"] = "content_capture_failed"
        if out["subject_capture_ok"]:
            out["subject_capture"] = mcp_capture.image_content_from_file(subject_path, scale=args.capture_scale, max_tokens=args.capture_max_tokens)

    return out


def run_phase3(args: argparse.Namespace) -> dict:
    spawn = parse_spawn(args.spawn)
    client = Client(args.port, read_key(args.keyfile), args.timeout)
    tests: dict[str, dict[str, Any]] = {}

    tests["D1_fail_closed_client_peer"] = run_phase3_security(client)

    client_pid = int(getattr(args, "client_pid", 0) or 0)
    # Wait for the client to be in-world and settled BEFORE the D1 camera tests. run-fase3.ps1 starts
    # this suite as soon as the SERVER reports the player ready, but the CLIENT is still loading the
    # world then -- it polls only a few sparse ticks, so the D1 camera_set await times out
    # (run_20260615_190536: id=10 TimeoutError). The readiness wait (lookat(player) + settle-shape
    # gate) blocks until the client polls reliably AND the clean view is presented, then D1/D2 run
    # against a ready client. The readiness verdict is reused by run_phase3_content.
    ready_cam_pos = add_pos(spawn, PHASE3_CONTENT_CAM_OFFSET)
    ready_target = [spawn[0], spawn[1] + PHASE3_SUBJECT_LOOK_DY, spawn[2]]
    ready_timeout = max(getattr(args, "ready_timeout", PHASE3_READY_DEFAULT_TIMEOUT_S), 10.0)
    readiness = wait_for_inworld_render(client, ready_cam_pos, ready_target, args, client_pid, ready_timeout)

    camera_pos = add_pos(spawn, PHASE3_CAMERA_OFFSET)
    expected_matrix = phase3_expected_orient_matrix(camera_pos)
    set_payload = {
        "cam_mode": "orient",
        "cam_pos": camera_pos,
        "cam_orientation": PHASE3_CAMERA_ORIENTATION,
        "fov": PHASE3_CAMERA_FOV,
        "settle_ticks": 3,
    }

    if readiness.get("inworld"):
        _, set_result = run_result(client, "camera_set", set_payload, timeout_s=max(args.timeout, 20.0), peer="client")
        tests["D1_camera_set_orient_roll"] = phase3_camera_case(
            "camera_set_orient_roll",
            set_result,
            camera_pos,
            expected_matrix,
            PHASE3_CAMERA_FOV,
        )

        _, get_result = run_result(client, "camera_get", {}, timeout_s=max(args.timeout, 20.0), peer="client")
        tests["D1_camera_get_orient_roll"] = phase3_camera_case(
            "camera_get_orient_roll",
            get_result,
            camera_pos,
            expected_matrix,
            PHASE3_CAMERA_FOV,
        )
    else:
        # Client never reached the clean in-world view -> the camera peer is not reliably polling;
        # issuing camera_set here would just block out the await timeout per command. Fail fast.
        not_ready = {"pass": False, "error": "inworld_render_not_ready", "readiness_elapsed_s": readiness.get("elapsed_s")}
        tests["D1_camera_set_orient_roll"] = dict(not_ready, name="camera_set_orient_roll")
        tests["D1_camera_get_orient_roll"] = dict(not_ready, name="camera_get_orient_roll")

    content = run_phase3_content(client, spawn, args, readiness=readiness)
    subject_capture = content.get("subject_capture")
    if not isinstance(subject_capture, dict):
        subject_capture = mcp_capture.capture_screenshot(
            scale=args.capture_scale,
            max_tokens=args.capture_max_tokens,
            client_pid=int(getattr(args, "client_pid", 0) or 0),
            cmdline_match=getattr(args, "client_cmdline_match", ""),
        )
    d2_tests, capture_summary = phase3_capture_cases(subject_capture, args.capture_max_tokens, content=content)
    tests.update(d2_tests)

    return {
        "overall_pass": all(item.get("pass") is True for item in tests.values()),
        "tests": tests,
        "scene": {
            "spawn": spawn,
            "camera_pos": camera_pos,
            "camera_orientation": PHASE3_CAMERA_ORIENTATION,
            "camera_fov": PHASE3_CAMERA_FOV,
            "expected_matrix": expected_matrix,
            "client_pid": content.get("client_pid"),
            "content_cam_pos": content.get("cam_pos"),
            "content_subject_target": content.get("subject_target"),
            "content_sky_target": content.get("sky_target"),
            "evidence_subject_png": content.get("evidence_subject_png"),
            "inworld_readiness": content.get("readiness"),
            "d2_capture": capture_summary,
        },
    }


def interpret_b3_probe(result: dict[str, Any]) -> str:
    if not dayz_true(result.get("ok")):
        return f"probe failed: {result.get('error')}"

    fixture_ready = bool(result.get("vehicle_fixture_ready"))
    if not fixture_ready:
        return "inconclusive: fixture not ready; do NOT read speedo"

    engine_on_server = bool(result.get("engine_on_server"))
    speedo_max = float_or_none(result.get("speedo_max"))
    pos_delta = float_or_none(result.get("pos_delta"))
    net_strategy = int_or_none(result.get("net_strategy"))
    speedo_zero = speedo_max is None or abs(speedo_max) <= 0.1

    if net_strategy == 2 and (not engine_on_server or speedo_zero):
        return "client-authoritative: B3 server-side no mueve; decidir client-peer/diferir"
    if (speedo_max is not None and speedo_max > 1.0) or (pos_delta is not None and pos_delta > 0.5):
        return "server-authoritative: el coche se movio server-side"
    return "inconclusive: fixture ready but movement evidence below threshold"


def b3_decision_data_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "vehicle_fixture_ready": bool(result.get("vehicle_fixture_ready")) if dayz_true(result.get("ok")) else False,
        "engine_on_server": result.get("engine_on_server") if dayz_true(result.get("ok")) else None,
        "speedo_max": float_or_none(result.get("speedo_max")),
        "pos_delta": float_or_none(result.get("pos_delta")),
        "net_strategy": int_or_none(result.get("net_strategy")),
        "interpretation": interpret_b3_probe(result),
    }


def run_phase1_backpressure(client: Client, timeout_s: float) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    queue_full_429_seen = False
    ok_before_429 = 0
    bridge_queue_full_count = 0
    accepted_pending: list[int] = []
    accepted_resolved = 0

    for attempt in range(1, 4):
        client.set_poll_delay(5000)
        accepted_ids: list[int] = []
        statuses: list[dict[str, Any]] = []

        bait = client.enqueue_cmd_status("query_player_state", {})
        statuses.append({"phase": "bait", **bait})
        if bait.get("status") == 200 and bait.get("id") is not None:
            accepted_ids.append(int(bait["id"]))

        time.sleep(0.5)
        attempt_ok_before_429 = 0
        attempt_429_seen = False
        for _ in range(EXPECTED_MAX_QUEUE + 20):
            status = client.enqueue_cmd_status("query_player_state", {})
            statuses.append({"phase": "burst", **status})
            if status.get("status") == 200 and status.get("id") is not None:
                accepted_ids.append(int(status["id"]))
                attempt_ok_before_429 += 1
                continue
            if status.get("status") == 429:
                attempt_429_seen = True
            break

        results, pending = await_many(client, accepted_ids, timeout_s)
        attempt_bridge_full = sum(
            1 for result in results.values() if dayz_false(result.get("ok")) and result.get("error") == "bridge_queue_full"
        )
        attempts.append(
            {
                "attempt": attempt,
                "queue_full_429_seen": attempt_429_seen,
                "ok_before_429": attempt_ok_before_429,
                "accepted_count": len(accepted_ids),
                "resolved_count": len(results),
                "pending_ids": pending,
                "bridge_queue_full_count": attempt_bridge_full,
                "statuses": statuses,
            }
        )

        if attempt_429_seen:
            queue_full_429_seen = True
            ok_before_429 = attempt_ok_before_429
            bridge_queue_full_count = attempt_bridge_full
            accepted_pending = pending
            accepted_resolved = len(results)
            break

        time.sleep(0.5)

    cap_statuses: list[dict[str, Any]] = []
    cap_ids: list[int] = []
    for _ in range(12):
        status = client.enqueue_cmd_status("query_player_state", {})
        cap_statuses.append(status)
        if status.get("status") == 200 and status.get("id") is not None:
            cap_ids.append(int(status["id"]))

    cap_results, cap_pending = await_many(client, cap_ids, max(timeout_s, 30.0))
    dispatch_counts: dict[int, int] = {}
    for result in cap_results.values():
        tick = int_or_none(result.get("tick_dispatch"))
        if tick is None:
            continue
        dispatch_counts[tick] = dispatch_counts.get(tick, 0) + 1

    dispatch_cap_max = max(dispatch_counts.values()) if dispatch_counts else 0
    dispatch_cap_ok = len(cap_ids) == 12 and not cap_pending and dispatch_cap_max <= MAX_DISPATCH_PER_TICK
    s4_pass = queue_full_429_seen and bridge_queue_full_count >= 1 and not accepted_pending and dispatch_cap_ok

    return {
        "pass": s4_pass,
        "queue_full_429_seen": queue_full_429_seen,
        "ok_before_429": ok_before_429,
        "bridge_queue_full_count": bridge_queue_full_count,
        "accepted_resolved_count": accepted_resolved,
        "accepted_pending_ids": accepted_pending,
        "dispatch_cap_max_per_tick": dispatch_cap_max,
        "dispatch_cap_ok": dispatch_cap_ok,
        "dispatch_counts": dispatch_counts,
        "dispatch_pending_ids": cap_pending,
        "dispatch_statuses": cap_statuses,
        "attempts": attempts,
    }


def run_phase1(args: argparse.Namespace) -> dict:
    client = Client(args.port, read_key(args.keyfile), args.timeout)
    tests: dict[str, dict[str, Any]] = {}

    s0_id, s0_result = run_result(client, "query_player_state", {})
    player_pos = state_pos(s0_result) if dayz_true(s0_result.get("ok")) else None
    pos_requested = [player_pos[0] + 2.0, player_pos[1], player_pos[2]] if player_pos else None

    selected_car_class = None
    car_pos_real = None
    b1_result: dict[str, Any] = {}
    b1_attempts: list[dict[str, Any]] = []

    if pos_requested:
        for car_class in PHASE1_CAR_CLASSES:
            command_id, result = run_result(client, "world_spawn", {"type": car_class, "pos": pos_requested})
            b1_result = result
            b1_attempts.append(
                {
                    "car_class": car_class,
                    "id": command_id,
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "type": result.get("type"),
                    "found": result.get("found"),
                }
            )
            if dayz_true(result.get("ok")):
                selected_car_class = car_class
                car_pos_real = pos3_or_none(result.get("pos_real"))
                break
            if result.get("error") not in ("unknown_type", "spawn_failed"):
                break

    b1_pass = (
        selected_car_class is not None
        and dayz_true(b1_result.get("ok"))
        and dayz_true(b1_result.get("found"))
        and b1_result.get("type") == selected_car_class
        and car_pos_real is not None
    )
    tests["B1_world_spawn"] = {
        "pass": b1_pass,
        "car_class": selected_car_class,
        "pos_requested": pos_requested,
        "pos_real": car_pos_real,
        "found": b1_result.get("found"),
        "error": b1_result.get("error") if b1_result else ("s0_player_state_failed" if not player_pos else "world_spawn_not_attempted"),
        "s0_player_state": {"id": s0_id, "ok": s0_result.get("ok"), "pos": player_pos, "error": s0_result.get("error")},
        "attempts": b1_attempts,
    }

    negative_cases: list[dict[str, Any]] = []
    if selected_car_class and pos_requested:
        _, result = run_result(client, "world_spawn", {"type": "__NOPE__", "pos": pos_requested})
        negative_cases.append(result_error_case("unknown_type", "unknown_type", result))

        _, result = run_result(client, "world_spawn", {"type": selected_car_class, "pos": [1, 2]})
        negative_cases.append(result_error_case("bad_pos_short", "bad_pos", result))

        _, result = run_result(client, "world_spawn", {"type": selected_car_class})
        negative_cases.append(result_error_case("bad_pos_missing", "bad_pos", result))

        _, result = run_result(client, "world_spawn", {"type": selected_car_class, "pos": pos_requested, "flags": 999})
        negative_cases.append(result_error_case("bad_flags", "bad_flags", result))

        status_result = client.enqueue_cmd_status("world_spawn", "x")
        negative_cases.append(http_error_case("args_no_dict", 400, "bad_args", status_result))
    else:
        negative_cases.append({"name": "skipped", "expected": "selected_car_class", "got": None, "pass": False})

    tests["B1_negatives"] = {"pass": all(case.get("pass") is True for case in negative_cases), "cases": negative_cases}

    b2_result: dict[str, Any] = {}
    if car_pos_real:
        _, b2_result = run_result(client, "vehicle_enter", {"pos": car_pos_real}, timeout_s=max(args.timeout, 20.0))

    tests["B2_vehicle_enter"] = {
        "pass": dayz_true(b2_result.get("ok")) and dayz_true(b2_result.get("seated")) and b2_result.get("seat") == "driver",
        "seated": b2_result.get("seated"),
        "seat": b2_result.get("seat"),
        "error": b2_result.get("error") if b2_result else "B1_world_spawn_failed",
        "result": b2_result,
    }

    b3_result: dict[str, Any] = {}
    if tests["B2_vehicle_enter"]["pass"]:
        _, b3_result = run_result(client, "vehicle_drive", {"throttle": 1.0, "duration": 2.0}, timeout_s=max(args.timeout, 40.0))
    else:
        b3_result = {"ok": False, "error": "B2_vehicle_enter_failed"}

    b3_decision_data = b3_decision_data_from_result(b3_result)
    tests["B3_drive_probe"] = {
        "probe_collected": dayz_true(b3_result.get("ok")),
        **b3_decision_data,
        "error": b3_result.get("error"),
        "result": b3_result,
    }

    tests["S4_backpressure"] = run_phase1_backpressure(client, timeout_s=max(args.timeout, 70.0))

    overall_pass = (
        tests["B1_world_spawn"]["pass"] is True
        and tests["B1_negatives"]["pass"] is True
        and tests["B2_vehicle_enter"]["pass"] is True
        and tests["S4_backpressure"]["pass"] is True
    )

    return {
        "overall_pass": overall_pass,
        "tests": tests,
        "b3_decision_data": b3_decision_data,
    }


def run_phase2(args: argparse.Namespace) -> dict:
    spawn = parse_spawn(args.spawn)
    client = Client(args.port, read_key(args.keyfile), args.timeout)
    tests: dict[str, dict[str, Any]] = {}
    normal_comparison: dict[str, Any] = {}
    scene: dict[str, Any] = {
        "spawn": spawn,
        "dynamic_offset": PHASE2_DYNAMIC_OFFSET,
        "static_offset": PHASE2_STATIC_OFFSET,
        "ambiguous_offset": PHASE2_AMBIGUOUS_OFFSET,
        "static_target_class": PHASE2_STATIC_TARGET_CLASS,
        "fixture_path": PHASE2_FIXTURE_PATH,
        "bad_fixture_path": PHASE2_BAD_FIXTURE_PATH,
    }

    smoke_payload = {
        "from": [spawn[0], spawn[1] + 2.0, spawn[2]],
        "to": [spawn[0], spawn[1] - 10.0, spawn[2]],
        "method": "rvproxy",
        "ignore": "player",
        "radius": PHASE2_RAY_RADIUS,
    }
    _, smoke_result = run_result(client, "scene_raycast", smoke_payload, timeout_s=max(args.timeout, 20.0))
    smoke_raycast = smoke_result.get("raycast") if isinstance(smoke_result.get("raycast"), dict) else {}
    smoke_pass = dayz_true(smoke_result.get("ok")) and dayz_true(smoke_raycast.get("hit"))
    if smoke_pass:
        tests["C1_smoke_setup"] = {"pass": True, "payload": smoke_payload, "result": smoke_result}
    else:
        tests["C1_smoke_setup"] = {"status": "raycast_setup_fail", "payload": smoke_payload, "result": smoke_result}

    vehicle_pos = add_pos(spawn, PHASE2_DYNAMIC_OFFSET)
    static_pos = add_pos(spawn, PHASE2_STATIC_OFFSET)
    ambiguous_pos = add_pos(spawn, PHASE2_AMBIGUOUS_OFFSET)
    scene["vehicle_pos_expected"] = vehicle_pos
    scene["static_pos_expected"] = static_pos
    scene["ambiguous_pos_expected"] = ambiguous_pos

    selected_car_class = None
    vehicle_spawn_result: dict[str, Any] = {}
    car_spawn_attempts = []
    for car_class in PHASE1_CAR_CLASSES:
        _, spawn_result = phase2_spawn_object(client, car_class, vehicle_pos, max(args.timeout, 30.0))
        car_spawn_attempts.append({"type": car_class, "ok": spawn_result.get("ok"), "error": spawn_result.get("error"), "result": spawn_result})
        if dayz_true(spawn_result.get("ok")):
            selected_car_class = car_class
            vehicle_spawn_result = spawn_result
            break
    scene["dynamic_type"] = selected_car_class
    tests["setup_dynamic_spawn"] = {"pass": selected_car_class is not None, "attempts": car_spawn_attempts}

    _, static_spawn_result = phase2_spawn_object(client, PHASE2_STATIC_TARGET_CLASS, static_pos, max(args.timeout, 30.0))
    tests["setup_static_spawn"] = {
        "pass": dayz_true(static_spawn_result.get("ok")),
        "type": PHASE2_STATIC_TARGET_CLASS,
        "pos": static_pos,
        "result": static_spawn_result,
    }

    ambiguous_spawn_results = []
    for offset in ([0.0, 0.0, 0.0], [0.35, 0.0, 0.0]):
        _, ambiguous_result = phase2_spawn_object(client, PHASE2_STATIC_TARGET_CLASS, add_pos(ambiguous_pos, offset), max(args.timeout, 30.0))
        ambiguous_spawn_results.append({"pos": add_pos(ambiguous_pos, offset), "result": ambiguous_result})
    tests["setup_ambiguous_spawn"] = {
        "pass": all(dayz_true(item["result"].get("ok")) for item in ambiguous_spawn_results),
        "type": PHASE2_STATIC_TARGET_CLASS,
        "results": ambiguous_spawn_results,
    }

    c1_aborted = not smoke_pass
    raycast_cases = []
    if not c1_aborted and selected_car_class is not None and tests["setup_static_spawn"].get("pass") is True:
        dynamic_cases = []
        static_cases = []
        for label, target_type, target_pos, target_cases in (
            ("dynamic", selected_car_class, vehicle_pos, dynamic_cases),
            ("static", PHASE2_STATIC_TARGET_CLASS, static_pos, static_cases),
        ):
            normal_comparison[label] = {}
            for method in ("rvproxy", "bullet"):
                payload, ray_from, _ = phase2_vertical_raycast_payload(target_pos, method)
                _, result = run_result(client, "scene_raycast", payload, timeout_s=max(args.timeout, 20.0))
                case = phase2_raycast_hit_case(f"{label}_{method}", result, target_type, ray_from, target_pos)
                case["payload"] = payload
                target_cases.append(case)
                raycast_cases.append(case)
                normal_comparison[label][method] = {
                    "normal": case.get("normal"),
                    "normal_len": case.get("normal_len"),
                    "object_type": case.get("object_type"),
                    "parent_type": case.get("parent_type"),
                    "distance_m": case.get("distance_m"),
                }
        tests["C1_dynamic"] = {"pass": all(case.get("pass") is True for case in dynamic_cases), "cases": dynamic_cases}
        tests["C1_static"] = {"pass": all(case.get("pass") is True for case in static_cases), "cases": static_cases}

        proxy_cases = [case for case in raycast_cases if int_or_none(case.get("hier_level")) and int_or_none(case.get("hier_level")) > 0]
        tests["C1_proxy_parent"] = {
            "pass": all(case.get("proxy_parent_ok") is True for case in proxy_cases),
            "evaluated": len(proxy_cases),
            "cases": proxy_cases,
        }

        empty_payload = {
            "from": [spawn[0], spawn[1] + 30.0, spawn[2]],
            "to": [spawn[0], spawn[1] + 80.0, spawn[2]],
            "method": "rvproxy",
            "ignore": "player",
            "radius": PHASE2_RAY_RADIUS,
        }
        _, empty_result = run_result(client, "scene_raycast", empty_payload, timeout_s=max(args.timeout, 20.0))
        empty_case = phase2_raycast_empty_case("empty_sky", empty_result)
        empty_case["payload"] = empty_payload
        tests["C1_negative_empty"] = empty_case
    else:
        tests["C1_aborted_after_smoke"] = {
            "status": "raycast_setup_fail" if c1_aborted else "spawn_setup_fail",
            "smoke_pass": smoke_pass,
            "dynamic_spawn_pass": selected_car_class is not None,
            "static_spawn_pass": tests["setup_static_spawn"].get("pass"),
        }

    c2_cases_ready = selected_car_class is not None
    if c2_cases_ready:
        _, object_result = run_result(
            client,
            "telemetry_read",
            {"mode": "object_at", "type": selected_car_class, "pos": vehicle_pos, "radius": 3.0},
            timeout_s=max(args.timeout, 20.0),
        )
        tests["C2_object_at"] = phase2_object_at_case("vehicle_object_at", object_result, selected_car_class, vehicle_pos)
    else:
        tests["C2_object_at"] = {"pass": False, "error": "dynamic_spawn_failed", "spawn_result": vehicle_spawn_result}

    _, fixture_result = run_result(
        client,
        "telemetry_read",
        {"mode": "fixture_jsonl", "path": PHASE2_FIXTURE_PATH, "max_lines": 64},
        timeout_s=max(args.timeout, 20.0),
    )
    tests["C2_fixture_jsonl"] = phase2_fixture_case("fixture_jsonl", fixture_result)

    negative_cases = []
    _, bad_path_result = run_result(client, "telemetry_read", {"mode": "fixture_jsonl", "path": "$profile:telemetry_fixture.jsonl"})
    negative_cases.append(result_error_case("path_not_allowlisted", "bad_args", bad_path_result))

    _, missing_result = run_result(client, "telemetry_read", {"mode": "fixture_jsonl", "path": PHASE2_MISSING_FIXTURE_PATH})
    negative_cases.append(result_error_case("fixture_not_found", "fixture_not_found", missing_result))

    _, bad_json_result = run_result(client, "telemetry_read", {"mode": "fixture_jsonl", "path": PHASE2_BAD_FIXTURE_PATH})
    negative_cases.append(result_error_case("parse_error", "parse_error", bad_json_result))

    _, not_found_result = run_result(
        client,
        "telemetry_read",
        {"mode": "object_at", "type": "__NOPE__", "pos": vehicle_pos, "radius": 1.0},
        timeout_s=max(args.timeout, 20.0),
    )
    negative_cases.append(phase2_found_false_case("type_not_found", not_found_result))

    _, ambiguous_result = run_result(
        client,
        "telemetry_read",
        {"mode": "object_at", "type": PHASE2_STATIC_TARGET_CLASS, "pos": ambiguous_pos, "radius": 2.0},
        timeout_s=max(args.timeout, 20.0),
    )
    negative_cases.append(result_error_case("ambiguous_fixture", "ambiguous_fixture", ambiguous_result))
    tests["C2_negatives"] = {"pass": all(case.get("pass") is True for case in negative_cases), "cases": negative_cases}

    required_names = [
        "C1_smoke_setup",
        "setup_dynamic_spawn",
        "setup_static_spawn",
        "setup_ambiguous_spawn",
        "C1_dynamic",
        "C1_static",
        "C1_proxy_parent",
        "C1_negative_empty",
        "C2_object_at",
        "C2_fixture_jsonl",
        "C2_negatives",
    ]
    overall_pass = all(tests.get(name, {}).get("pass") is True for name in required_names)

    return {
        "overall_pass": overall_pass,
        "tests": tests,
        "scene": scene,
        "normal_comparison": normal_comparison,
    }


def write_verdict(path: str, verdict: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(verdict, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, path)
    with open(path, "r", encoding="utf-8") as handle:
        json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="DayZ MCP POC verdict client")
    parser.add_argument("--mode", choices=("poc", "phase1", "phase2", "phase3"), default="poc", help="client mode (default: poc)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--keyfile", required=True)
    parser.add_argument("--spawn")
    parser.add_argument("--output")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--capture-scale", choices=("tiny", "small", "full"), default="small")
    parser.add_argument("--capture-max-tokens", type=int, default=mcp_capture.DEFAULT_MAX_TOKENS)
    # Identify the fase3 client window for the host grab so it is not confused with another DayZ
    # client (e.g. LFQuad). --client-cmdline-match (a substring of the client's command line, e.g. its
    # unique profiles path) is preferred and robust to DayZDiag's launcher-pid != window-pid mismatch;
    # --client-pid is a fallback.
    parser.add_argument("--client-pid", type=int, default=0)
    parser.add_argument("--client-cmdline-match", default="")
    # max seconds to wait for the in-world VIEW to be presented (client world-load + the launch
    # Health&Safety overlay clearing lags the server-side player-ready by tens of seconds).
    parser.add_argument("--ready-timeout", type=float, default=PHASE3_READY_DEFAULT_TIMEOUT_S)
    args = parser.parse_args()

    if args.output is None:
        name = "fase1-verdict.json" if args.mode == "phase1" else "fase2-verdict.json" if args.mode == "phase2" else "fase3-verdict.json" if args.mode == "phase3" else "poc-verdict.json"
        args.output = os.path.join(os.path.dirname(__file__), name)
    if args.mode in ("poc", "phase2", "phase3") and not args.spawn:
        parser.error("--spawn is required in poc, phase2 and phase3 modes")

    try:
        if args.mode == "phase1":
            verdict = run_phase1(args)
        elif args.mode == "phase2":
            verdict = run_phase2(args)
        elif args.mode == "phase3":
            verdict = run_phase3(args)
        else:
            verdict = run_verdict(args)
    except Exception as exc:
        verdict = {
            "overall_pass": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    write_verdict(args.output, verdict)

    label = "FASE1_VERDICT" if args.mode == "phase1" else "FASE2_VERDICT" if args.mode == "phase2" else "FASE3_VERDICT" if args.mode == "phase3" else "POC_VERDICT"
    print(label + " " + ("PASS" if verdict.get("overall_pass") else "FAIL"))
    tests = verdict.get("tests", {})
    if isinstance(tests, dict):
        for name, result in tests.items():
            if isinstance(result, dict):
                if "pass" in result:
                    print(f"{name} {'PASS' if result.get('pass') else 'FAIL'}")
                elif "status" in result:
                    print(f"{name} {result.get('status')}")
                elif args.mode == "phase1" and name == "B3_drive_probe":
                    print(f"{name} DATA")

    return 0 if verdict.get("overall_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())

