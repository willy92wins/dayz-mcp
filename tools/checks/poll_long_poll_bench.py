"""Pickup latency of one queued command, with and without /poll wait_ms.

A stand-in for the game bridge polls GET /poll. Without wait_ms it sleeps
0.2 s after each empty answer (5 Hz, the gap the bridge keeps when a poll
returns before the next tick). With wait_ms=800 it holds one request and,
on an empty answer, polls again at once.

The command is enqueued delay_s after that gap or hold has started (default
0.15 s, inside one 5 Hz gap). Latency is from that enqueue to the response
that carries the command.

Does not launch DayZ, does not bind port 8765, and does not read
%LOCALAPPDATA%.

    .venv-mcp/Scripts/python.exe checks/poll_long_poll_bench.py --delay-s 0.15 --samples 21
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from dayz_mcp import loopback


_GAP_S = 0.2
_WAIT_MS = loopback.POLL_LONG_POLL_MAX_MS


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    count = len(ordered)
    index = min(count - 1, max(0, math.ceil(pct / 100.0 * count) - 1))
    return ordered[index]


def _summarize(latencies: list[float], counts: list[int]) -> dict[str, Any]:
    return {
        "p50_s": round(_percentile(latencies, 50), 4),
        "p95_s": round(_percentile(latencies, 95), 4),
        "request_counts": list(counts),
        "requests_min": min(counts),
        "requests_max": max(counts),
    }


class _Loopback:
    def __init__(self) -> None:
        self.key = "poll-long-poll-bench"
        self.state = loopback.ServerState(self.key)
        self.httpd = loopback.create_http_server(
            0,
            self.state,
            log_sink=lambda _message: None,
            reclaim_orphans=False,
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self.state.cancel_pending()
        self.httpd.shutdown()
        self.thread.join(timeout=3.0)
        self.httpd.server_close()

    def poll(self, query: dict[str, str], timeout: float) -> dict[str, Any]:
        params = dict(query)
        params["key"] = self.key
        url = self.base + "/poll?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError(f"poll body was {body!r}")
        return body


def _enqueue(state: loopback.ServerState) -> int:
    status, payload = state.enqueue_command(
        "camera_get", {}, peer="client", operation_timeout_s=30
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"enqueue failed: {status} {payload!r}")
    command_id = payload.get("id")
    if not isinstance(command_id, int) or isinstance(command_id, bool):
        raise RuntimeError(f"enqueue id missing: {payload!r}")
    return command_id


def _sample_without_wait(loop: _Loopback, delay_s: float) -> tuple[float, int]:
    """5 Hz: one empty poll, then enqueue delay_s into the following gap."""

    gap_started = threading.Event()
    enqueued_at = [0.0]
    errors: list[BaseException] = []
    result: dict[str, tuple[float, int]] = {}

    def peer() -> None:
        try:
            requests = 1
            body = loop.poll({"peer": "client"}, timeout=2.0)
            if body.get("commands"):
                raise RuntimeError("command arrived before enqueue")
            gap_started.set()
            while True:
                if gap_started.wait(0):
                    pass
                time.sleep(_GAP_S)
                requests += 1
                body = loop.poll({"peer": "client"}, timeout=2.0)
                commands = body.get("commands")
                if isinstance(commands, list) and commands:
                    if enqueued_at[0] == 0.0:
                        raise RuntimeError("delivered before enqueue")
                    result["value"] = (time.monotonic() - enqueued_at[0], requests)
                    return
                if requests > 8:
                    raise RuntimeError("without wait_ms: command was not picked up")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=peer)
    worker.start()
    try:
        if not gap_started.wait(2.0):
            raise RuntimeError("peer did not finish its first poll")
        time.sleep(delay_s)
        enqueued_at[0] = time.monotonic()
        _enqueue(loop.state)
        worker.join(timeout=3.0)
        if errors:
            raise errors[0]
        if worker.is_alive():
            raise RuntimeError("without wait_ms: peer did not finish")
        return result["value"]
    finally:
        worker.join(timeout=1.0)


def _arm_hold(state: loopback.ServerState) -> threading.Event:
    """Signal once this client's poll is inside Condition.wait.

    One hook is installed on the condition and reused. Each sample only
    points that hook at a new event. Wrapping the previous hook every sample
    builds a chain that raises RecursionError.
    """

    cv = state._command_cv["client"]
    entered = threading.Event()
    hook = getattr(cv, "_poll_bench_hold", None)
    if hook is None or cv._release_save is not hook:
        original = cv._release_save

        def release_save():
            saved = original()
            current = getattr(cv, "_poll_bench_entered", None)
            if current is not None and not current.is_set():
                current.set()
            return saved

        cv._release_save = release_save
        hook = release_save
        cv._poll_bench_hold = hook
    cv._poll_bench_entered = entered
    return entered


def _sample_with_wait(loop: _Loopback, delay_s: float) -> tuple[float, int]:
    """One held GET. An empty answer polls again at once."""

    entered = _arm_hold(loop.state)
    enqueued_at = [0.0]
    errors: list[BaseException] = []
    result: dict[str, tuple[float, int]] = {}

    def peer() -> None:
        try:
            requests = 0
            while True:
                requests += 1
                body = loop.poll(
                    {"peer": "client", "wait_ms": str(_WAIT_MS)}, timeout=3.0
                )
                commands = body.get("commands")
                if isinstance(commands, list) and commands:
                    if enqueued_at[0] == 0.0:
                        raise RuntimeError("delivered before enqueue")
                    result["value"] = (time.monotonic() - enqueued_at[0], requests)
                    return
                if requests > 4:
                    raise RuntimeError("with wait_ms: command was not picked up")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=peer)
    worker.start()
    try:
        if not entered.wait(2.0):
            raise RuntimeError("poll did not enter the wait")
        time.sleep(delay_s)
        enqueued_at[0] = time.monotonic()
        _enqueue(loop.state)
        worker.join(timeout=3.0)
        if errors:
            raise errors[0]
        if worker.is_alive():
            raise RuntimeError("with wait_ms: peer did not finish")
        return result["value"]
    finally:
        worker.join(timeout=1.0)


def run_bench(delay_s: float = 0.15, samples: int = 21) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if not 0.0 < delay_s < _GAP_S:
        raise ValueError(f"delay_s must sit inside the {_GAP_S:.1f}s gap")
    loop = _Loopback()
    without_latency: list[float] = []
    without_counts: list[int] = []
    with_latency: list[float] = []
    with_counts: list[int] = []
    try:
        for _index in range(samples):
            latency, count = _sample_without_wait(loop, delay_s)
            without_latency.append(latency)
            without_counts.append(count)
        for _index in range(samples):
            latency, count = _sample_with_wait(loop, delay_s)
            with_latency.append(latency)
            with_counts.append(count)
    finally:
        loop.close()
    return {
        "delay_s": delay_s,
        "poll_hz": 5,
        "gap_s": _GAP_S,
        "wait_ms": _WAIT_MS,
        "samples": samples,
        "without_wait_ms": _summarize(without_latency, without_counts),
        "with_wait_ms": _summarize(with_latency, with_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure /poll pickup latency.")
    parser.add_argument("--delay-s", type=float, default=0.15)
    parser.add_argument("--samples", type=int, default=21)
    args = parser.parse_args()
    report = run_bench(delay_s=args.delay_s, samples=args.samples)
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
