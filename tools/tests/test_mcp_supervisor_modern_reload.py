"""A 2026-07-28 client cannot be recycled by the supervisor: refuse before anything moves.

The supervisor brings a replacement worker up by replaying the host's `initialize`; a modern
(stateless) client never sends one, so a recycle would retire the old worker and then fail.
"""

from __future__ import annotations

import json
import unittest

from dayz_mcp.mcp_supervisor import RELOAD_TOOL_NAME
from tests.test_mcp_supervisor import SupervisorTestBase, reload_call, result_text

_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"


def _reload(ident: object, meta: dict | None) -> bytes:
    params: dict = {"name": RELOAD_TOOL_NAME, "arguments": {}}
    if meta is not None:
        params["_meta"] = meta
    return (
        json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": params}) + "\n"
    ).encode("utf-8")


class ModernReloadTest(SupervisorTestBase):
    def _assert_refused_and_untouched(self, ident: int) -> None:
        message = self.host.wait_for(lambda m: m.get("id") == ident)
        self.assertTrue(message["result"]["isError"])
        self.assertEqual(result_text(message)["error"], "modern_protocol_unsupported")
        self.assertEqual(len(self.workers), 1, "no replacement worker may be started")
        first = self.workers[0]
        self.assertFalse(first.killed)
        self.assertFalse(first.stdin_closed.is_set(), "the serving worker must not be retired")
        # nothing is draining: the next ordinary call still reaches the same worker
        self.supervisor.handle_host_line(
            b'{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"ping"}}\n'
        )
        first.wait_for_sent(lambda m: m.get("id") == 12)

    def test_a_modern_reload_after_a_legacy_handshake_is_refused_before_draining(self) -> None:
        self.start()
        self.handshake()
        self.supervisor.handle_host_line(_reload(11, {_VERSION_KEY: "2026-07-28"}))
        self._assert_refused_and_untouched(11)

    def test_a_modern_reload_with_no_handshake_at_all_is_refused_the_same_way(self) -> None:
        self.start()
        self.supervisor.handle_host_line(_reload(11, {_VERSION_KEY: "2026-07-28"}))
        self._assert_refused_and_untouched(11)

    def test_a_legacy_reload_still_recycles_even_with_unrelated_meta(self) -> None:
        self.start()
        self.handshake()
        self.supervisor.handle_host_line(_reload(7, {"progressToken": "t"}))
        message = self.host.wait_for(lambda m: m.get("id") == 7)
        self.assertFalse(message["result"]["isError"])
        self.assertEqual(result_text(message)["status"], "recycled")
        self.assertEqual(len(self.workers), 2)

    def test_a_plain_legacy_reload_still_recycles(self) -> None:
        self.start()
        self.handshake()
        self.supervisor.handle_host_line(reload_call(8))
        message = self.host.wait_for(lambda m: m.get("id") == 8)
        self.assertEqual(result_text(message)["status"], "recycled")


if __name__ == "__main__":
    unittest.main()
