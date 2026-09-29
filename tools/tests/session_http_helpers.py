"""Loopback session server shared by the HTTP contract tests and the carrier tests.

The handoff reload test needs the same server SessionHttpTest starts. It lives
here so those modules do not import each other (test_suite_structure).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from dayz_mcp import loopback
from dayz_mcp.session_coordination import SessionCoordinator
from tests.fence_helpers import bind_both_peers
from tests.lease_helpers import SnapshotStore


def _http(base, method, path, key, payload=None, query=None, timeout=2.0):
    params = dict(query or {})
    if key is not None:
        params["key"] = key
    url = base + path + "?" + urllib.parse.urlencode(params)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), json.loads(exc.read().decode("utf-8") or "{}")
        finally:
            exc.close()


class SessionLoopback:
    """One loopback daemon with a session coordinator, started like SessionHttpTest."""

    def setUp(self) -> None:
        self.key = "session-key"
        self.audit_fails = False
        self.audit_events: list[dict[str, object]] = []
        self.store = SnapshotStore()
        self.state = loopback.ServerState(self.key)
        bind_both_peers(self.state)

        def audit(event: dict[str, object]) -> bool:
            self.audit_events.append(event)
            return not self.audit_fails

        self.coordinator = SessionCoordinator(
            audit=audit,
            cleanup=lambda session_id, lease_id, reason, vehicle_active: self.state.cleanup_owner(
                session_id, lease_id, reason, vehicle_active
            ),
        )
        # Task 3 adds these as daemon-composition attributes. Assigning them here
        # keeps RED focused on the missing routes instead of constructor TypeError.
        self.state.coordination = self.coordinator  # type: ignore[attr-defined]
        self.state.retail_probe = lambda: {"known": True, "processes": []}
        self.state.coordination_store = self.store  # type: ignore[attr-defined]
        self.state.daemon_generation = "generation-test"  # type: ignore[attr-defined]
        self.httpd = loopback.create_http_server(
            0, self.state, log_sink=lambda _message: None, reclaim_orphans=False
        )
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def request(self, path: str, payload: dict, *, key: str | None = None) -> tuple[int, dict]:
        return _http(self.base, "POST", path, self.key if key is None else key, payload)
