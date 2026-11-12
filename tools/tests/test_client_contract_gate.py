"""Client contract gate for verbs that changed under the same name (#377, #380).

camera_set_focus moved from Camera.SetFocus to CGame.OverrideDOF with five
values, and chat_read gained per-line visibility and hud_shown, both at bridge
version 12 with the census unchanged. The client bridge now announces
``ach=`` (the hash of bridge_readiness.CLIENT_CONTRACT) and loopback refuses
those two verbs with arg_contract_mismatch to a client without it. The bridge
version stays 12 so tools that predate the gate, which require exactly 12 and
never read the client's ach, keep serving the newer bridge.
"""

from __future__ import annotations

import hashlib
import re
import unittest

from dayz_mcp import bridge_readiness, core, loopback
from dayz_mcp.bridge_errors import _public_enqueue_error
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import _method_body
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bind_both_peers, bound_queue


MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE = MISSION / "MCPClientBridge.c"
MESSAGES = MISSION / "MCPMessages.c"
# The canonical form, written out so a change to the table or to its
# encoding shows here and in the PBO constant together.
CANONICAL = (
    "camera_set_focus=focus_blur,focus_depth_offset,focus_distance,focus_length,focus_length_near\n"
    "chat_read=alpha,hud_shown,replaced,screen_h,screen_w,screen_x,screen_y,shown_at_ms,visible_hierarchy"
)
ACH_WIRE = 'request = request + "&ach=" + CLIENT_CONTRACT_HASH;'
CAPS_WIRE = 'request = request + "&caps=" + m_PollCapabilities;'


def _source(path) -> str:
    return path.read_text(encoding="utf-8")


def _members(class_body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in class_body.splitlines():
        match = re.fullmatch(r"\s*((?:ref )?[\w<> ]+?) (\w+);\s*", line.split("//", 1)[0])
        if match:
            found[match.group(2)] = match.group(1)
    return found


class ClientContractHashTest(unittest.TestCase):
    def test_python_and_the_pbo_announce_the_same_hash(self) -> None:
        self.assertEqual(bridge_readiness.client_contract_canonical(), CANONICAL)
        expected = hashlib.sha256(CANONICAL.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(bridge_readiness.EXPECTED_CLIENT_CONTRACT_HASH, expected)
        found = re.findall(r'protected const string CLIENT_CONTRACT_HASH = "([0-9a-f]{16})";', _source(BRIDGE))
        self.assertEqual(found, [expected])
        self.assertNotEqual(expected, bridge_readiness.EXPECTED_SERVER_ARG_CONTRACT_HASH)

    def test_every_poll_announces_it_after_the_census(self) -> None:
        start_poll = _method_body(_source(BRIDGE), "protected void StartPoll()")
        self.assertEqual(start_poll.count(ACH_WIRE), 1)
        self.assertLess(start_poll.index(CAPS_WIRE), start_poll.index(ACH_WIRE))
        self.assertLess(start_poll.index(ACH_WIRE), start_poll.index("m_PollCtx.GET(m_PollCallback, request);"))
        before = start_poll[: start_poll.index(ACH_WIRE)]
        self.assertEqual(before.count("{"), before.count("}"), "ach= must not sit inside a condition")

    def test_the_contract_names_what_the_bridge_reads_and_answers(self) -> None:
        messages = _source(MESSAGES)
        args = _members(_method_body(messages, "class MCPArgs"))
        for name in bridge_readiness.CLIENT_CONTRACT["camera_set_focus"]:
            with self.subTest(arg=name):
                self.assertEqual(args.get(name), "float")
        reply = {
            **_members(_method_body(messages, "class MCPChatLine")),
            **_members(_method_body(messages, "class MCPChatRead")),
        }
        for name in bridge_readiness.CLIENT_CONTRACT["chat_read"]:
            with self.subTest(field=name):
                self.assertIn(name, reply)
        self.assertEqual(
            loopback._CLIENT_CONTRACT_COMMANDS, frozenset(bridge_readiness.CLIENT_CONTRACT)
        )
        for command in loopback._CLIENT_CONTRACT_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, loopback._CLIENT_ANNOUNCED_COMMANDS)

    def test_the_bridge_version_is_not_what_tells_them_apart(self) -> None:
        # The older bridges polled with "12~<game>" (9810aafd, 3e5c2460) and
        # this one polls with the version the tools expect. Older tools
        # require that version exactly, so the gate is the client ach, which
        # they never read, rather than a version bump that would refuse this
        # bridge to them.
        self.assertIn(
            f'const string MCP_BRIDGE_VERSION = "{core.EXPECTED_BRIDGE_VERSION}";', _source(MESSAGES)
        )
        self.assertEqual(
            core.version_state_for("12~1.29", require_version=True, expected_game_version=None),
            ("ok", "version accepted"),
        )


class OlderClientChatReadTest(unittest.TestCase):
    """#380: a client that announces chat_read without the visibility contract
    is refused it, instead of answering it without the fields."""

    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        # The client bridge of 3e5c2460, before chat_read's visibility: the
        # same census at bridge version 12, with no ach=.
        self.census = sorted(bridge_readiness._BRIDGE_COMMAND_TOOLS["client"])
        self.assertIn("chat_read", self.census)

    def _announce(self, ach: str | None) -> tuple[int, dict]:
        return self.state.record_poll(
            "client",
            "12~1.29",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(self.census),
            ach=ach,
        )

    def _enqueue(self, command: str) -> tuple[int, dict]:
        return self.state.enqueue_command(command, {}, peer="client", operation_timeout_s=30.0)

    def test_the_older_bridge_is_refused_chat_read_before_anything_is_queued(self) -> None:
        for ach in (None, "", "deadbeefdeadbeef", bridge_readiness.EXPECTED_SERVER_ARG_CONTRACT_HASH):
            with self.subTest(ach=ach):
                self.setUp()
                self._announce(ach)
                self.assertEqual(self._enqueue("chat_read"), (409, {"error": "arg_contract_mismatch"}))
                self.assertEqual(bound_queue(self.state, "client"), [])
                # A verb whose contract did not change still goes to it.
                status, body = self._enqueue("gameplay_state")
                self.assertEqual(status, 200, body)
        self.assertEqual(_public_enqueue_error({"error": "arg_contract_mismatch"}), "arg_contract_mismatch")

    def test_the_current_bridge_is_handed_chat_read(self) -> None:
        self._announce(bridge_readiness.EXPECTED_CLIENT_CONTRACT_HASH)
        status, body = self._enqueue("chat_read")
        self.assertEqual(status, 200, body)
        status, poll = self._announce(bridge_readiness.EXPECTED_CLIENT_CONTRACT_HASH)
        self.assertEqual(status, 200)
        self.assertEqual([(command["id"], command["cmd"]) for command in poll["commands"]], [(body["id"], "chat_read")])

    def test_delivery_checks_the_contract_again(self) -> None:
        self._announce(bridge_readiness.EXPECTED_CLIENT_CONTRACT_HASH)
        status, body = self._enqueue("chat_read")
        self.assertEqual(status, 200, body)
        status, poll = self._announce(None)
        self.assertEqual((status, poll["commands"]), (200, []))
        self.assertEqual(
            self.state._results[body["id"]],
            {"id": body["id"], "ok": False, "error": "arg_contract_mismatch"},
        )

    def test_readiness_does_not_change_with_the_client_hash(self) -> None:
        # The gate is admission of the two verbs, not client readiness: the
        # client's other verbs keep working with either bridge.
        registered = frozenset(
            tool for tool in bridge_readiness._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        for ach in (None, bridge_readiness.EXPECTED_CLIENT_CONTRACT_HASH):
            with self.subTest(ach=ach):
                block = {"state": "announced", "reason": "ok", "announced_commands": self.census}
                if ach is not None:
                    block["announced_arg_contract_hash"] = ach
                result = bridge_readiness._compare_bridge_capabilities("client", block, registered)
                self.assertEqual((result["state"], result["reason"]), ("match", "ok"))


if __name__ == "__main__":
    unittest.main()
