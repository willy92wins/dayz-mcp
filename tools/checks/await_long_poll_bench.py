"""Latency and request count of GET /await, with and without wait_ms.

Starts a loopback daemon on an ephemeral port. A stand-in for the game
publishes one result after delay_s. The before path sends no wait_ms and
sleeps 0.05s between polls, which is what a client does against a daemon
that does not wait. The after path is ClientRuntime._await_result.

Does not launch DayZ, does not bind port 8765, and does not read
%LOCALAPPDATA%.

    .venv-mcp/Scripts/python.exe checks/await_long_poll_bench.py --delay-s 1 --samples 21
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from dayz_mcp import loopback
from dayz_mcp.server import POLL_INTERVAL_S, ClientRuntime


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
        self.key = "await-long-poll-bench"
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

    def get(self, query: dict[str, str], timeout: float) -> dict[str, Any]:
        params = dict(query)
        params["key"] = self.key
        url = self.base + "/await?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise RuntimeError(f"await body was {body!r}")
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


def _publish_later(
    state: loopback.ServerState, command_id: int, delay_s: float
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def publish() -> None:
        try:
            time.sleep(delay_s)
            code, stored = state.store_result({"id": command_id, "ok": 1})
            if code != 200 or not isinstance(stored, dict) or stored.get("discarded"):
                raise RuntimeError(f"store_result failed: {code} {stored!r}")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=publish)
    thread.start()
    return thread, errors


def _sample_before(loop: _Loopback, delay_s: float) -> tuple[float, int]:
    command_id = _enqueue(loop.state)
    deadline = time.monotonic() + delay_s + 3.0
    publisher, errors = _publish_later(loop.state, command_id, delay_s)
    started = time.monotonic()
    requests = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise RuntimeError("before path timed out")
            requests += 1
            body = loop.get(
                {"id": str(command_id), "remove": "1"},
                timeout=min(remaining, 1.0),
            )
            if body.get("status") == "done":
                return time.monotonic() - started, requests
            time.sleep(min(POLL_INTERVAL_S, max(0.0, deadline - time.monotonic())))
    finally:
        publisher.join(timeout=delay_s + 3.0)
        if errors:
            raise errors[0]


async def _await_one(loop: _Loopback, delay_s: float) -> tuple[float, int]:
    command_id = _enqueue(loop.state)
    requests = 0

    def _call(
        method: str,
        path: str,
        payload: dict | None = None,
        query: dict | None = None,
        timeout: float = 5.0,
        deadline: float | None = None,
    ) -> tuple[int, dict]:
        del method, payload, deadline
        nonlocal requests
        if path != "/await":
            raise AssertionError(path)
        requests += 1
        body = loop.get(dict(query or {}), timeout=max(float(timeout), 0.1))
        return 200, body

    async def _liveness(_peer: str) -> str:
        return "bench"

    runtime = object.__new__(ClientRuntime)
    runtime._time_fn = time.monotonic
    runtime._broker_admits = {}
    runtime._call = _call
    runtime._liveness_message = _liveness
    publisher, errors = _publish_later(loop.state, command_id, delay_s)
    started = time.monotonic()
    try:
        await runtime._await_result(
            "camera_get", command_id, "client", delay_s + 4.0
        )
        return time.monotonic() - started, requests
    finally:
        publisher.join(timeout=delay_s + 5.0)
        if errors:
            raise errors[0]


async def run_bench(delay_s: float = 1.0, samples: int = 21) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if delay_s <= 0.0:
        raise ValueError("delay_s must be > 0")
    loop = _Loopback()
    before_latency: list[float] = []
    before_counts: list[int] = []
    after_latency: list[float] = []
    after_counts: list[int] = []
    try:
        for _index in range(samples):
            latency, count = _sample_before(loop, delay_s)
            before_latency.append(latency)
            before_counts.append(count)
        for _index in range(samples):
            latency, count = await _await_one(loop, delay_s)
            after_latency.append(latency)
            after_counts.append(count)
    finally:
        loop.close()
    return {
        "delay_s": delay_s,
        "samples": samples,
        "before": _summarize(before_latency, before_counts),
        "after": _summarize(after_latency, after_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure /await long-poll latency.")
    parser.add_argument("--delay-s", type=float, default=1.0)
    parser.add_argument("--samples", type=int, default=21)
    args = parser.parse_args()
    report = asyncio.run(run_bench(delay_s=args.delay_s, samples=args.samples))
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
