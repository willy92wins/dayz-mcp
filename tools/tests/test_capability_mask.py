"""Capability bitmask (`cm=`) announcement: Enforce constants, decoder, ingress.

The bridges announce their census as ``cm=<epoch>.<hex>`` instead of the
``caps=`` name list, so the poll URL keeps headroom under the engine limit.
Python (bridge_readiness) is the single source of the bit order; the Enforce
side sends a precomputed constant and computes nothing at runtime.
"""

from __future__ import annotations

import json
import random
import re
import threading
import unittest
import urllib.parse
import urllib.request

from dayz_mcp import loopback
from dayz_mcp.bridge_readiness import (
    CAPABILITY_MASK_MAX_BITS,
    CAPABILITY_MASK_TABLES,
    CLIENT_CAPABILITY_BITS,
    EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH,
    EXPECTED_SERVER_ARG_CONTRACT_HASH,
    SERVER_CAPABILITY_BITS,
    _BRIDGE_COMMAND_TOOLS,
    _with_capability_comparison,
    compute_bridge_ready,
    encode_capability_mask,
    parse_capability_mask,
)
from dayz_mcp.core import EXPECTED_BRIDGE_VERSION
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import dispatch_census as client_dispatch_census
from tests.fence_helpers import INST_CLIENT, INST_SERVER, PID_CLIENT, PID_SERVER, bind_both_peers
from tests.poll_harness_helpers import _bridge_harness, _dispatch_branches

MISSION = addon_root() / "scripts" / "5_Mission"
NAME_RE = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
MASK_GRAMMAR = re.compile(r"\A[1-9][0-9]{0,2}\.[0-9a-f]+\Z")
VERSION = f"{EXPECTED_BRIDGE_VERSION}~1.30"

# Frozen epoch-1 order (T1b): written out here so a reorder of the tables in
# bridge_readiness, or of a census constant in Enforce, fails against a copy
# that does not move with the code under test.
SERVER_EPOCH1 = (
    "action_cursor_ids", "bot_start", "bot_stop", "entities_query", "exec_enforce",
    "hands_attach", "hands_take", "infected_drive", "inventory_attach", "inventory_give",
    "inventory_move", "lease_close", "loadout_apply", "loadout_clear", "loadout_get",
    "nav_path_query", "notify_players", "object_anim", "object_delete", "object_doors",
    "object_inspect", "object_resolve", "object_set_health", "player_godmode", "player_heal",
    "player_kill", "player_teleport", "players_visibility", "query_all_players",
    "query_get_in_condition", "query_player_state", "scene_clear", "scene_entities",
    "scene_raycast", "server_perf", "surface_query", "survivor_drive", "survivor_drive_release",
    "telemetry_read", "vehicle_door", "vehicle_enter", "vehicle_prepare_fixture", "weapon_state",
    "world_spawn", "world_spawn_batch", "world_time_get", "world_time_set", "world_weather_set",
)
CLIENT_EPOCH1 = (
    "action_cursor", "action_hold", "action_hold_cancel", "action_use", "action_use_component",
    "action_use_door", "action_use_target", "actor_emote", "anim_timeline", "camera_get",
    "camera_path_play", "camera_path_release", "camera_set", "camera_set_focus", "chat_read",
    "dev_free_camera_set", "engine_set", "gameplay_state", "hud_set", "input_describe",
    "input_trigger", "key_press", "lease_close_client", "player_look_at", "player_look_at_release",
    "player_move", "player_respawn", "player_trace", "player_view_set", "restore_gameplay",
    "ui_click", "ui_dialog", "ui_focus", "ui_pause", "ui_reload_layout", "ui_set_text", "ui_tree",
    "vehicle_control", "vehicle_get_in_client", "vehicle_release", "vehicle_telemetry",
    "vehicle_trace", "weapon_aim", "weapon_fire", "weapon_raise", "weapon_sights",
)
# Main 576f4c88's wire forms (T3): the server sent its census raw, the client
# sent CLIENT_POLL_CAPS through EncodeQueryValue (commas as %2C).
SERVER_CAPS_MAIN = ",".join(SERVER_EPOCH1)
CLIENT_CAPS_MAIN_WIRE = "%2C".join(CLIENT_EPOCH1)

_PEER_FIXTURE = {
    "server": (INST_SERVER, PID_SERVER),
    "client": (INST_CLIENT, PID_CLIENT),
}


def _const_census(source: str, const_name: str) -> list[str]:
    declarations = re.findall(rf"\b{const_name}\s*=\s*([^;]+);", source)
    if len(declarations) != 1:
        raise AssertionError(f"{const_name} occurs {len(declarations)} times")
    chunks = re.findall(r'"([^"]*)"', declarations[0])
    return "".join(chunks).split(",")


def _mask_constant(source: str, const_name: str) -> str:
    declarations = re.findall(rf'\b{const_name}\s*=\s*"([^"]*)"\s*;', source)
    if len(declarations) != 1:
        raise AssertionError(f"{const_name} occurs {len(declarations)} times")
    return declarations[0]


def _state() -> loopback.ServerState:
    state = loopback.ServerState("k")
    bind_both_peers(state)
    return state


def _poll(state, peer, *, caps=None, cm=None, ach=None, xai=None):
    instance, pid = _PEER_FIXTURE[peer]
    return state.record_poll(
        peer, VERSION, instance=instance, source_pid=pid,
        caps=caps, cm=cm, ach=ach, xai=xai,
    )


def _registered_tools() -> frozenset:
    return frozenset(
        tool
        for mapping in _BRIDGE_COMMAND_TOOLS.values()
        for tool in mapping.values()
        if tool
    )


def _bridge_status(state):
    snapshot = state.status_snapshot()
    payload = {}
    for peer, key in (("server", "server_peer"), ("client", "client_peer")):
        block = dict(snapshot["peers"][peer])
        block["version_state"] = "ok"
        payload[key] = block
    compared = _with_capability_comparison(payload, _registered_tools())
    return compared, compute_bridge_ready(compared)


def _view(state, peer) -> dict:
    view = state._capabilities_view_locked(peer)
    return {**view, "announced_commands": sorted(view["announced_commands"])}


def _snapshot_caps(state) -> dict:
    snapshot = state.status_snapshot()
    return {
        peer: {
            **snapshot["peers"][peer]["capabilities"],
            "announced_commands": sorted(
                snapshot["peers"][peer]["capabilities"]["announced_commands"]
            ),
        }
        for peer in ("server", "client")
    }


class EnforceMaskTieTest(unittest.TestCase):
    """T1: the Enforce mask constant, census and Dispatch() agree bit for bit."""

    def test_server_mask_decodes_to_census_and_dispatch(self) -> None:
        source = (MISSION / "MCPBridge.c").read_text(encoding="utf-8")
        constant = _mask_constant(source, "SERVER_CAPABILITIES_MASK")
        self.assertIsNotNone(MASK_GRAMMAR.match(constant), constant)
        census = _const_census(source, "SERVER_CAPABILITIES")
        names, reason = parse_capability_mask("server", constant)
        self.assertEqual(reason, "ok")
        self.assertEqual(set(names), set(census))
        self.assertEqual(set(names), set(_dispatch_branches(source)))
        self.assertEqual(encode_capability_mask("server", census), constant)

    def test_client_mask_decodes_to_census_and_dispatch(self) -> None:
        source = (MISSION / "MCPClientBridge.c").read_text(encoding="utf-8")
        constant = _mask_constant(source, "CLIENT_POLL_CAPS_MASK")
        self.assertIsNotNone(MASK_GRAMMAR.match(constant), constant)
        census = _const_census(source, "CLIENT_POLL_CAPS")
        names, reason = parse_capability_mask("client", constant)
        self.assertEqual(reason, "ok")
        self.assertEqual(set(names), set(census))
        self.assertEqual(set(names), set(client_dispatch_census(source)))
        self.assertEqual(encode_capability_mask("client", census), constant)


def _translated_poll_names(test: unittest.TestCase, filename: str, source: str, peer: str) -> set:
    ns = _bridge_harness(filename, source=source)
    ns["StartPoll"]()
    test.assertEqual(len(ns["polls"]), 1)
    request = ns["polls"][0].decode("ascii")
    parsed = urllib.parse.parse_qs(
        request.split("?", 1)[1], strict_parsing=True, keep_blank_values=True
    )
    test.assertEqual(len(parsed.get("cm", [])), 1, request)
    names, reason = parse_capability_mask(peer, parsed["cm"][0])
    test.assertEqual(reason, "ok")
    test.assertIsNotNone(names)
    return set(names)


class AppendRetireRegressionTest(unittest.TestCase):
    """Append/retire variants: set ties accept them; numbering stays in T1b/T4."""

    def test_append_client_ui_menu_set_is_accepted(self) -> None:
        source = (MISSION / "MCPClientBridge.c").read_text(encoding="utf-8")
        branch = (
            '\t\telse if (command.cmd == "ui_dialog")\n'
            "\t\t{\n"
            "\t\t\tpostNow = DispatchUiDialog(command, result);\n"
            "\t\t}\n"
        )
        self.assertEqual(source.count(branch), 1)
        variant = source.replace(
            branch,
            branch
            + '\t\telse if (command.cmd == "ui_menu_set")\n'
            + "\t\t{\n"
            + "\t\t\tpostNow = DispatchUiDialog(command, result);\n"
            + "\t\t}\n",
            1,
        )
        self.assertEqual(variant.count("ui_focus,ui_pause"), 1)
        variant = variant.replace("ui_focus,ui_pause", "ui_focus,ui_menu_set,ui_pause", 1)
        self.assertEqual(variant.count('CLIENT_POLL_CAPS_MASK = "1.3fffffffffff"'), 1)
        variant = variant.replace(
            'CLIENT_POLL_CAPS_MASK = "1.3fffffffffff"',
            'CLIENT_POLL_CAPS_MASK = "1.7fffffffffff"',
            1,
        )
        self.assertNotEqual(variant, source)
        original = CAPABILITY_MASK_TABLES["client"][1]
        CAPABILITY_MASK_TABLES["client"][1] = CLIENT_CAPABILITY_BITS + ("ui_menu_set",)
        try:
            constant = _mask_constant(variant, "CLIENT_POLL_CAPS_MASK")
            self.assertEqual(constant, "1.7fffffffffff")
            census = _const_census(variant, "CLIENT_POLL_CAPS")
            names, reason = parse_capability_mask("client", constant)
            self.assertEqual(reason, "ok")
            self.assertEqual(set(names), set(census))
            self.assertEqual(set(names), set(client_dispatch_census(variant)))
            self.assertEqual(len(names), 47)
            self.assertEqual(encode_capability_mask("client", census), constant)
            self.assertEqual(
                _translated_poll_names(self, "MCPClientBridge.c", variant, "client"),
                set(census),
            )
        finally:
            CAPABILITY_MASK_TABLES["client"][1] = original
        self.assertEqual(CAPABILITY_MASK_TABLES["client"][1], CLIENT_CAPABILITY_BITS)

    def test_retire_server_bot_start_is_accepted(self) -> None:
        source = (MISSION / "MCPBridge.c").read_text(encoding="utf-8")
        branch = (
            '\t\telse if (command.cmd == "bot_start")\n'
            "\t\t{\n"
            "\t\t\tpostNow = DispatchBotStart(command, result);\n"
            "\t\t}\n"
        )
        self.assertEqual(source.count(branch), 1)
        variant = source.replace(branch, "", 1)
        self.assertEqual(variant.count("action_cursor_ids,bot_start,bot_stop"), 1)
        variant = variant.replace(
            "action_cursor_ids,bot_start,bot_stop", "action_cursor_ids,bot_stop", 1
        )
        self.assertEqual(variant.count('SERVER_CAPABILITIES_MASK = "1.ffffffffffff"'), 1)
        variant = variant.replace(
            'SERVER_CAPABILITIES_MASK = "1.ffffffffffff"',
            'SERVER_CAPABILITIES_MASK = "1.fffffffffffd"',
            1,
        )
        self.assertNotEqual(variant, source)
        # Table unchanged: bot_start keeps its slot, it is simply never set.
        self.assertEqual(CAPABILITY_MASK_TABLES["server"][1], SERVER_CAPABILITY_BITS)
        constant = _mask_constant(variant, "SERVER_CAPABILITIES_MASK")
        self.assertEqual(constant, "1.fffffffffffd")
        census = _const_census(variant, "SERVER_CAPABILITIES")
        names, reason = parse_capability_mask("server", constant)
        self.assertEqual(reason, "ok")
        self.assertEqual(set(names), set(census))
        self.assertEqual(set(names), set(_dispatch_branches(variant)))
        self.assertEqual(len(names), 47)
        self.assertIn("bot_stop", names)
        self.assertNotIn("bot_start", names)
        self.assertEqual(encode_capability_mask("server", census), constant)
        self.assertEqual(
            _translated_poll_names(self, "MCPBridge.c", variant, "server"),
            set(census),
        )


class AppendOnlyTableTest(unittest.TestCase):
    """T1b: the epoch-1 prefix is frozen; tables stay valid and bounded."""

    def test_tables_start_with_the_frozen_epoch1_order(self) -> None:
        for table, frozen in (
            (SERVER_CAPABILITY_BITS, SERVER_EPOCH1),
            (CLIENT_CAPABILITY_BITS, CLIENT_EPOCH1),
        ):
            self.assertEqual(table[: len(frozen)], frozen)
            self.assertEqual(len(set(table)), len(table))
            self.assertLessEqual(len(table), CAPABILITY_MASK_MAX_BITS)
            for name in table:
                self.assertIsNotNone(NAME_RE.match(name), name)
        self.assertEqual(CAPABILITY_MASK_MAX_BITS, 256)
        self.assertEqual(CAPABILITY_MASK_TABLES["server"][1], SERVER_CAPABILITY_BITS)
        self.assertEqual(CAPABILITY_MASK_TABLES["client"][1], CLIENT_CAPABILITY_BITS)


class EquivalenceTest(unittest.TestCase):
    """T2: cm= and caps= leave identical stored, viewed and compared censuses."""

    def _run_pair(self, *, xai: str | None):
        legacy = _state()
        masked = _state()
        server_ach = EXPECTED_SERVER_ARG_CONTRACT_HASH
        _poll(legacy, "server", caps=SERVER_CAPS_MAIN, ach=server_ach, xai=xai)
        _poll(legacy, "client", caps=",".join(CLIENT_EPOCH1))
        _poll(masked, "server", cm="1.ffffffffffff", ach=server_ach, xai=xai)
        _poll(masked, "client", cm="1.3fffffffffff")
        return legacy, masked

    def test_cm_and_caps_are_equivalent(self) -> None:
        for xai in (None, f"{EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH}.31"):
            with self.subTest(xai=xai):
                legacy, masked = self._run_pair(xai=xai)
                for peer in ("server", "client"):
                    self.assertEqual(
                        set(legacy._peer_caps[peer]["commands"]),
                        set(masked._peer_caps[peer]["commands"]),
                    )
                    self.assertEqual(_view(legacy, peer), _view(masked, peer))
                self.assertEqual(_snapshot_caps(legacy), _snapshot_caps(masked))
                legacy_status, legacy_ready = _bridge_status(legacy)
                masked_status, masked_ready = _bridge_status(masked)
                for key in ("server_peer", "client_peer"):
                    self.assertEqual(
                        legacy_status[key]["capabilities"],
                        masked_status[key]["capabilities"],
                    )
                self.assertEqual(legacy_ready, masked_ready)
                self.assertEqual(masked_ready, {"ready": True, "reason": "ready"})
                for key in ("server_peer", "client_peer"):
                    self.assertEqual(
                        masked_status[key]["capabilities"]["state"], "match"
                    )


class MixedPeersTest(unittest.TestCase):
    """T2b: each peer is decoded by its own form on the new daemon."""

    def _assert_admitted_then_refused(self, state, *, client_cm: bool) -> None:
        status, body = state.enqueue_command("gameplay_state", {}, peer="client")
        self.assertEqual(status, 200, body)
        reduced = [name for name in CLIENT_EPOCH1 if name != "gameplay_state"]
        if client_cm:
            _poll(state, "client", cm=encode_capability_mask("client", reduced))
        else:
            _poll(state, "client", caps=",".join(reduced))
        self.assertEqual(
            state.enqueue_command("gameplay_state", {}, peer="client"),
            (409, {"error": "bridge_capability_missing"}),
        )

    def test_server_caps_client_cm(self) -> None:
        state = _state()
        _poll(state, "server", caps=SERVER_CAPS_MAIN, ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
        _poll(state, "client", cm="1.3fffffffffff")
        compared, ready = _bridge_status(state)
        self.assertEqual(ready, {"ready": True, "reason": "ready"})
        for key in ("server_peer", "client_peer"):
            self.assertEqual(compared[key]["capabilities"]["state"], "match")
        self._assert_admitted_then_refused(state, client_cm=True)

    def test_server_cm_client_caps(self) -> None:
        state = _state()
        _poll(state, "server", cm="1.ffffffffffff", ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
        _poll(state, "client", caps=",".join(CLIENT_EPOCH1))
        compared, ready = _bridge_status(state)
        self.assertEqual(ready, {"ready": True, "reason": "ready"})
        for key in ("server_peer", "client_peer"):
            self.assertEqual(compared[key]["capabilities"]["state"], "match")
        self._assert_admitted_then_refused(state, client_cm=False)


class OldFormatCompatibilityTest(unittest.TestCase):
    """T3: main's caps= wire forms give exactly today's census and verdicts."""

    def test_main_census_strings_still_announce(self) -> None:
        state = _state()
        client_caps = urllib.parse.parse_qs(
            f"caps={CLIENT_CAPS_MAIN_WIRE}", keep_blank_values=True
        )["caps"][0]
        self.assertEqual(client_caps, ",".join(CLIENT_EPOCH1))
        _poll(state, "server", caps=SERVER_CAPS_MAIN, ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
        _poll(state, "client", caps=client_caps)
        for peer, count in (("server", 48), ("client", 46)):
            view = _view(state, peer)
            self.assertEqual(view["state"], "announced")
            self.assertEqual(view["reason"], "ok")
            self.assertEqual(len(view["announced_commands"]), count)
        compared, ready = _bridge_status(state)
        self.assertEqual(ready, {"ready": True, "reason": "ready"})
        for key in ("server_peer", "client_peer"):
            self.assertEqual(compared[key]["capabilities"]["state"], "match")
        status, body = state.enqueue_command("gameplay_state", {}, peer="client")
        self.assertEqual(status, 200, body)

    def test_legacy_census_missing_a_client_verb_still_refuses_it(self) -> None:
        state = _state()
        _poll(state, "server", caps=SERVER_CAPS_MAIN, ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
        reduced = ",".join(name for name in CLIENT_EPOCH1 if name != "gameplay_state")
        _poll(state, "client", caps=reduced)
        self.assertEqual(
            state.enqueue_command("gameplay_state", {}, peer="client"),
            (409, {"error": "bridge_capability_missing"}),
        )


class DecoderTest(unittest.TestCase):
    """T4: known-answer vectors, per-position vectors and the reason set."""

    def test_known_answer_vectors(self) -> None:
        vectors = (
            ("client", "1.1", ("action_cursor",)),
            ("client", "1.2", ("action_hold",)),
            ("client", "1.8", ("action_use",)),
            ("client", "1.200000000000", ("weapon_sights",)),
            ("server", "1.1", ("action_cursor_ids",)),
            ("server", "1.800000000000", ("world_weather_set",)),
        )
        for peer, raw, names in vectors:
            with self.subTest(peer=peer, raw=raw):
                self.assertEqual(parse_capability_mask(peer, raw), (names, "ok"))
                self.assertEqual(encode_capability_mask(peer, names), raw)

    def test_every_epoch1_position(self) -> None:
        for peer, frozen in (("server", SERVER_EPOCH1), ("client", CLIENT_EPOCH1)):
            for index, name in enumerate(frozen):
                raw = "1." + format(1 << index, "x")
                with self.subTest(peer=peer, index=index):
                    self.assertEqual(parse_capability_mask(peer, raw), ((name,), "ok"))
                    self.assertEqual(encode_capability_mask(peer, (name,)), raw)

    def test_random_subsets_round_trip(self) -> None:
        rng = random.Random(20261113)
        for peer, frozen in (("server", SERVER_EPOCH1), ("client", CLIENT_EPOCH1)):
            for _ in range(40):
                subset = tuple(rng.sample(frozen, rng.randint(1, len(frozen))))
                names, reason = parse_capability_mask(
                    peer, encode_capability_mask(peer, subset)
                )
                self.assertEqual(reason, "ok")
                self.assertEqual(set(names), set(subset))
                self.assertEqual(list(names), [n for n in frozen if n in subset])

    def test_reasons(self) -> None:
        malformed = (
            "1.A", "1.01", "1." + "f" * 65, "1", "1.1.1", "1.", "1.g",
            "0.1", "01.1", "", "1.ffffffffffff ",
        )
        for raw in malformed:
            with self.subTest(raw=raw):
                self.assertEqual(parse_capability_mask("server", raw), (None, "malformed"))
        self.assertEqual(parse_capability_mask("server", "2.1"), (None, "unknown_epoch"))
        self.assertEqual(parse_capability_mask("client", "999.1"), (None, "unknown_epoch"))
        self.assertEqual(parse_capability_mask("server", "1.0"), (None, "empty"))
        # Unknown high bits are ignored; a known bit beside them still reads ok.
        raw = "1." + format((1 << 100) | 1, "x")
        self.assertEqual(parse_capability_mask("server", raw), (("action_cursor_ids",), "ok"))
        self.assertEqual(
            parse_capability_mask("server", "1." + format(1 << 100, "x")),
            (None, "empty"),
        )
        with self.assertRaises(ValueError):
            encode_capability_mask("server", ("no_such_verb",))

    def test_ambiguous_when_both_forms_arrive(self) -> None:
        state = _state()
        _poll(state, "server", caps=SERVER_CAPS_MAIN, cm="1.ffffffffffff",
              ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
        view = _view(state, "server")
        self.assertEqual((view["state"], view["reason"]), ("unknown", "ambiguous"))

    def test_unaccredited_cm_stores_nothing(self) -> None:
        state = loopback.ServerState("k")
        state.record_poll("server", VERSION, cm="1.ffffffffffff")
        view = _view(state, "server")
        self.assertEqual((view["state"], view["reason"]), ("unknown", "unaccredited"))
        self.assertEqual(view["announced_commands"], [])


class HeadroomPast64Test(unittest.TestCase):
    """T4b: a cm census is bounded by its table, not by the legacy 64 cap."""

    def test_extended_table_with_xai_stores_past_64(self) -> None:
        extended = SERVER_EPOCH1 + tuple(f"extra_verb_{i}" for i in range(22))
        self.assertEqual(len(extended), 70)
        original = CAPABILITY_MASK_TABLES["server"][1]
        CAPABILITY_MASK_TABLES["server"][1] = extended
        try:
            state = _state()
            cm = "1." + format((1 << 70) - 1, "x")
            _poll(state, "server", cm=cm, ach=EXPECTED_SERVER_ARG_CONTRACT_HASH,
                  xai=f"{EXPECTED_EXPANSION_AI_ARG_CONTRACT_HASH}.31")
            view = _view(state, "server")
            self.assertEqual((view["state"], view["reason"]), ("announced", "ok"))
            self.assertEqual(len(view["announced_commands"]), 75)
            _poll(state, "server", caps=",".join(extended),
                  ach=EXPECTED_SERVER_ARG_CONTRACT_HASH)
            view = _view(state, "server")
            self.assertEqual((view["state"], view["reason"]), ("unknown", "too_many"))
        finally:
            CAPABILITY_MASK_TABLES["server"][1] = original


class HandlerTest(unittest.TestCase):
    """T5: a real GET /poll with cm= reaches the store and logs census/plen."""

    def test_poll_handler_with_cm(self) -> None:
        logs: list[str] = []
        state = loopback.ServerState("test-key")
        bind_both_peers(state)
        httpd = loopback.create_http_server(0, state, log_sink=logs.append)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            host, port = httpd.server_address
            query = urllib.parse.urlencode({
                "key": "test-key",
                "ver": VERSION,
                "inst": INST_SERVER,
                "cm": "1.ffffffffffff",
                "ach": EXPECTED_SERVER_ARG_CONTRACT_HASH,
            })
            with urllib.request.urlopen(
                f"http://{host}:{port}/poll?{query}", timeout=2.0
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read().decode("utf-8")), {"commands": []})
        finally:
            httpd.shutdown()
            thread.join(timeout=2.0)
            httpd.server_close()
        view = _view(state, "server")
        self.assertEqual((view["state"], view["reason"]), ("announced", "ok"))
        self.assertEqual(len(view["announced_commands"]), 48)
        hits = [line for line in logs if line.startswith("HIT GET /poll ")]
        self.assertEqual(len(hits), 1, logs)
        self.assertIn(" census=cm plen=", hits[0])


if __name__ == "__main__":
    unittest.main()
