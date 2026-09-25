"""One MCP process for the 296b r3 cross-process tests (not a test module).

Reads one JSON command per stdin line and answers one JSON line on stdout. It
talks to the daemon's real loopback handler over HTTP with the same payloads
ControlClient sends, and uses the production helpers of dayz_test_tool: the
snapshot, _open_client_dumps, _bind_client_dumps and the session_status
projection server._attach_revalidated_runs_retired_recently (296b r4).
Nothing is shared with another process except the daemon and the profile.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_tool, server  # noqa: E402
from dayz_mcp.client_steam_bootstrap import snapshot_client_dumps  # noqa: E402


def _post(base: str, key: str, path: str, payload: dict) -> dict:
    url = base + path + "?" + urllib.parse.urlencode({"key": key})
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            raise RuntimeError(f"http_{error.code}:{error.read()!r}") from None
        finally:
            error.close()


class _HttpRuntime:
    """The ClientRuntime calls dayz_test_tool and session_status make, over HTTP."""

    def __init__(self, base: str, key: str, identity: dict) -> None:
        self._base, self._key, self._identity = base, key, identity

    def call(self, path: str, payload: dict) -> dict:
        return _post(self._base, self._key, path, {"identity": self._identity, **payload})

    async def client_dumps_open(self, baseline: dict) -> dict:
        return self.call("/client-dumps", {"op": "open", "baseline": baseline})

    async def client_dumps_bind(self, token: str, run_id: str) -> dict:
        return self.call(
            "/client-dumps", {"op": "bind", "token": token, "run_id": run_id}
        )

    async def client_dumps_get(self, run_ids: list) -> dict:
        return self.call("/client-dumps", {"op": "get", "run_ids": run_ids})


async def _launch(
    runtime: _HttpRuntime, root: str, send_open: bool, bind: bool = True
) -> object:
    # The dayz_test_tool order: the snapshot and open when the launch leaves
    # the queue, then the daemon's /lifecycle/start, then bind to its run_id.
    # bind=False stops before the bind (it follows the lease release) and
    # returns the token too, for a later "bind" command.
    token = None
    if send_open:
        token = await dayz_test_tool._open_client_dumps(
            runtime, snapshot_client_dumps([root])
        )
    started = runtime.call("/lifecycle/start", {"lease_token": "token", "request": {}})
    run_id = started["run_id"]
    if not bind:
        return {"run_id": run_id, "token": token}
    await dayz_test_tool._bind_client_dumps(runtime, token, run_id)
    return run_id


class _PausedAfterFirstGet:
    """The runtime, stopped right after the first client-dumps get returns.

    It tells the test it is paused and waits for one stdin line: the window
    between reading the bindings and scanning the shared profile.
    """

    def __init__(self, runtime: _HttpRuntime) -> None:
        self._runtime = runtime
        self._paused = False

    async def client_dumps_get(self, run_ids: list) -> dict:
        answer = await self._runtime.client_dumps_get(run_ids)
        if not self._paused:
            self._paused = True
            sys.stdout.write(json.dumps({"answer": "paused"}) + "\n")
            sys.stdout.flush()
            sys.stdin.readline()
        return answer


def _project(runtime: _HttpRuntime, client: object = None) -> dict:
    # What the session_status tool does with the daemon's answer.
    status = runtime.call("/session/status", {})
    asyncio.run(
        server._attach_revalidated_runs_retired_recently(client or runtime, status)
    )
    rows = status["runs_retired_recently"]
    return {row["run_id"]: row["client_death_diagnosis"] for row in rows or []}


def main() -> None:
    base, key, session = sys.argv[1], sys.argv[2], sys.argv[3]
    identity = {
        "platform": "claude",
        "pid": os.getpid(),
        "ppid": 1,
        "started_at_utc": "2026-09-25T00:00:00Z",
        "session_id": session,
        "task_label": "296b-r3",
    }
    runtime = _HttpRuntime(base, key, identity)
    for line in sys.stdin:
        command = json.loads(line)
        op = command["op"]
        if op == "launch":
            answer: object = asyncio.run(
                _launch(
                    runtime,
                    command["root"],
                    command.get("open", True),
                    command.get("bind", True),
                )
            )
        elif op == "bind":
            answer = asyncio.run(
                runtime.client_dumps_bind(command["token"], command["run_id"])
            )
        elif op == "dump":
            root = Path(command["root"])
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"ErrorMessage_DayZDiag_x64_2026-09-24_{command['stamp']}.mdmp"
            path.write_bytes(command["body"].encode("latin-1"))
            answer = str(path)
        elif op == "project":
            answer = _project(runtime)
        elif op == "project_paused":
            answer = _project(runtime, _PausedAfterFirstGet(runtime))
        elif op == "pid":
            answer = os.getpid()
        else:
            answer = None
        sys.stdout.write(json.dumps({"answer": answer}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
