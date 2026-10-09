from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback, result_prune
from dayz_mcp.result_prune import (
    NEVER_FILLED_SCALAR_FIELDS,
    PRUNABLE_FIELDS,
    SEMANTIC_EMPTY_FIELDS,
    prune_unfilled_fields,
)
from tests._addon_paths import addon_root

# Every command the prune can see: call_bridge sends these names.
ALL_COMMANDS = (
    loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS | loopback.EXEC_COMMANDS
)

# fb-20260823-130809-a412: each vehicle scalar and the commands whose dispatch
# writes it (MCPBridge.c / MCPClientBridge.c; tests/test_owned_scalar_census.py
# re-derives the sets from those files). Stated here as the contract the prune
# must keep, so a change to OWNED_SCALAR_FIELDS is visible in this file too.
VEHICLE_SCALAR_OWNERS = {
    "seated": {"vehicle_enter", "vehicle_get_in_client", "vehicle_telemetry"},
    "seat": {"vehicle_enter", "vehicle_get_in_client", "vehicle_telemetry"},
    "vehicle_fixture_ready": {"vehicle_prepare_fixture", "vehicle_get_in_client"},
    "engine_on_server": {"engine_set", "vehicle_control", "vehicle_telemetry"},
    "speedo_max": {"vehicle_telemetry"},
    "gear": {"vehicle_telemetry"},
    "net_strategy": {"vehicle_get_in_client", "vehicle_telemetry"},
    "is_owner": {"vehicle_get_in_client", "vehicle_telemetry"},
    "is_authority_owner": {"vehicle_get_in_client", "vehicle_telemetry"},
    "owner_identity": {"vehicle_get_in_client", "vehicle_telemetry"},
    "net_id_low": {"vehicle_get_in_client", "vehicle_telemetry"},
    "net_id_high": {"vehicle_get_in_client", "vehicle_telemetry"},
}

# The unassigned default of each, as the Enforce serializer sends it.
VEHICLE_SCALAR_DEFAULTS = {
    "seated": 0,
    "seat": "",
    "vehicle_fixture_ready": 0,
    "engine_on_server": 0,
    "speedo_max": 0.0,
    "gear": 0,
    "net_strategy": 0,
    "is_owner": 0,
    "is_authority_owner": 0,
    "owner_identity": "",
    "net_id_low": 0,
    "net_id_high": 0,
}


# A bridge answer as the wire actually carries it: MCPResult is one flat class,
# so every ref member is present even when the verb filled none of them.
def _wire_result(**filled: object) -> dict[str, object]:
    empty: dict[str, object] = {
        "state": {},
        "players": [],
        "raycast": {},
        "telemetry": {},
        "camera": {},
        "applied": {},
        "get_in": {},
        "trace": {},
        "pos_real": [],
        "dialog": {},
        "entities": [],
        "input_describe": {},
        "building_doors": {},
    }
    empty.update(filled)
    return {"ok": 1, "cmd": "fixture", **empty}


# Other classes in MCPMessages.c also declare `ref` members. Only MCPResult
# is the prune contract, so the scan stops at that class's closing brace.
def _mcp_result_ref_members(source: str) -> tuple[str, ...]:
    start = source.index("class MCPResult")
    brace = source.index("{", start)
    depth = 0
    body = ""
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                body = source[brace + 1 : index]
                break
    else:
        raise AssertionError("unterminated class MCPResult")

    names: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        comment = line.find("//")
        if comment >= 0:
            line = line[:comment].rstrip()
        if not line.startswith("ref "):
            continue
        match = re.search(r"(\w+)\s*;\s*$", line)
        if match:
            names.append(match.group(1))
    return tuple(names)


class ResultPruneTest(unittest.TestCase):
    def test_unfilled_reference_fields_are_dropped(self) -> None:
        pruned = prune_unfilled_fields("world_spawn", _wire_result())

        for field in PRUNABLE_FIELDS:
            self.assertNotIn(field, pruned, field)
        self.assertEqual(pruned["ok"], 1)

    def test_filled_fields_survive_for_every_prunable_field(self) -> None:
        # Positive control per field: pruning must never eat a real answer.
        for field in PRUNABLE_FIELDS:
            with self.subTest(field=field):
                value = [1.0] if field in {"pos_real", "players"} else {"a": 1}
                pruned = prune_unfilled_fields("any_command", _wire_result(**{field: value}))
                self.assertEqual(pruned[field], value)
                for other in PRUNABLE_FIELDS:
                    if other != field:
                        self.assertNotIn(other, pruned, other)

    def test_query_all_players_keeps_its_empty_array(self) -> None:
        # The reinforced negative control: empty players IS the success answer
        # for this verb, verified in-game 2026-07-29. Pruning it is a regression.
        pruned = prune_unfilled_fields("query_all_players", _wire_result())

        self.assertIn("players", pruned)
        self.assertEqual(pruned["players"], [])
        # ... while its other unfilled fields still go.
        self.assertNotIn("raycast", pruned)
        self.assertNotIn("telemetry", pruned)

    def test_semantic_exception_is_scoped_to_its_own_command(self) -> None:
        # Same field, different verb -> pruned. Otherwise the exception would be
        # a blanket opt-out and the ambiguity would survive everywhere else.
        pruned = prune_unfilled_fields("query_player_state", _wire_result())
        self.assertNotIn("players", pruned)
        self.assertEqual(
            SEMANTIC_EMPTY_FIELDS,
            frozenset(
                {
                    ("query_all_players", "players"),
                    ("ui_dialog", "dialog"),
                    ("entities_query", "entities"),
                }
            ),
        )

    def test_entities_query_keeps_its_empty_list(self) -> None:
        # Ficha 59d9: the published description says "Absent entities travel
        # as []", and pruning the key made result["entities"] a KeyError on
        # every empty query. count_total survives either way; the list is the
        # contract the description names, and only for this verb.
        pruned = prune_unfilled_fields(
            "entities_query", _wire_result(count_total=0)
        )

        self.assertEqual(pruned["entities"], [])
        self.assertEqual(pruned["count_total"], 0)
        self.assertNotIn("players", pruned)
        self.assertNotIn(
            "entities", prune_unfilled_fields("object_inspect", _wire_result())
        )

    def test_scalars_are_never_pruned_even_when_falsy(self) -> None:
        # False/0/"" are indistinguishable from "never assigned", so they are
        # real answers that must survive: deleted:0 and found:false are results.
        # seated/seat/gear used to stand here too; since a412 they are owned
        # scalars (OWNED_SCALAR_FIELDS) and object_delete does not own them.
        result = _wire_result()
        result.update(
            {"deleted": 0, "found": False, "object_id": 0, "type": "", "phase": 0.0}
        )

        pruned = prune_unfilled_fields("object_delete", result)

        # phase is owned by object_anim (7f27). deleted stays because
        # object_delete writes it. found and object_id stay unmanaged.
        for field in ("deleted", "found", "object_id", "type"):
            self.assertIn(field, pruned, field)
        self.assertNotIn("phase", pruned)

    def test_every_public_verb_keeps_ok_and_loses_only_empty_refs(self) -> None:
        # H9: one assertion per public verb, driven off the real whitelist so a
        # newly added verb cannot silently skip this contract.
        verbs = loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS
        self.assertGreaterEqual(len(verbs), 20)
        for verb in sorted(verbs):
            with self.subTest(verb=verb):
                pruned = prune_unfilled_fields(verb, _wire_result())
                self.assertEqual(pruned["ok"], 1)
                self.assertEqual(pruned["cmd"], "fixture")
                expected = {
                    field
                    for field in PRUNABLE_FIELDS
                    if (verb, field) in SEMANTIC_EMPTY_FIELDS
                }
                self.assertEqual(
                    {field for field in PRUNABLE_FIELDS if field in pruned}, expected
                )

    def test_door_scalars_are_dropped_from_every_command_but_action_use_door(self) -> None:
        # #156 R1 F2: the flat class sends door_index:0 and component_index:0 on
        # every verb, so a plain action_use read component_index 0 although its
        # target used -1. Driven off the real whitelist like the test above.
        verbs = (loopback.SERVER_COMMANDS | loopback.CLIENT_COMMANDS) - {
            "action_use_door",
            "action_hold",
        }
        component_owners = {"action_use_door", "action_use_component", "action_hold"}
        action_owners = result_prune.OWNED_SCALAR_FIELDS["started"]
        self.assertIn("action_use", verbs)
        for verb in sorted(verbs):
            with self.subTest(verb=verb):
                pruned = prune_unfilled_fields(
                    verb,
                    _wire_result(
                        door_index=0, component_index=0, started=False, distance=0.0
                    ),
                )
                self.assertNotIn("door_index", pruned)
                if verb not in component_owners:
                    self.assertNotIn("component_index", pruned)
                # started and distance are owned by the action commands (7f27).
                if verb in action_owners:
                    self.assertIs(pruned["started"], False)
                    self.assertEqual(pruned["distance"], 0.0)
                else:
                    self.assertNotIn("started", pruned)
                    self.assertNotIn("distance", pruned)

    def test_action_use_door_keeps_door_zero_and_component_zero(self) -> None:
        # Door 0 and component 0 are real, and the action_use tool compares the
        # echoed door_index with the request: pruning it would turn a real door 0
        # into door_not_supported.
        self.assertIn("action_use_door", loopback.CLIENT_COMMANDS)
        for door, component in ((0, 0), (3, 17)):
            with self.subTest(door=door, component=component):
                pruned = prune_unfilled_fields(
                    "action_use_door",
                    _wire_result(door_index=door, component_index=component),
                )
                self.assertEqual(pruned["door_index"], door)
                self.assertEqual(pruned["component_index"], component)
        # action_hold door and component selectors echo the same indexes.
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS["door_index"],
            frozenset({"action_use_door", "action_hold"}),
        )
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS["component_index"],
            frozenset({"action_use_door", "action_use_component", "action_hold"}),
        )

    def test_vehicle_scalars_have_exactly_their_writing_commands_as_owners(self) -> None:
        self.assertEqual(
            {
                field: set(owners)
                for field, owners in result_prune.OWNED_SCALAR_FIELDS.items()
                if field in VEHICLE_SCALAR_OWNERS
            },
            VEHICLE_SCALAR_OWNERS,
        )

    def test_vehicle_scalars_are_kept_for_each_owner_even_at_their_default(self) -> None:
        # vehicle_telemetry seated=0 off a car is an answer; so is gear 0.
        for field, owners in sorted(VEHICLE_SCALAR_OWNERS.items()):
            default = VEHICLE_SCALAR_DEFAULTS[field]
            for command in sorted(owners):
                with self.subTest(field=field, command=command):
                    self.assertIn(command, ALL_COMMANDS)
                    pruned = prune_unfilled_fields(
                        command, _wire_result(**{field: default})
                    )
                    self.assertIn(field, pruned)
                    self.assertEqual(pruned[field], default)

    def test_vehicle_scalars_are_dropped_from_every_other_command(self) -> None:
        # The flat class sends every vehicle scalar on every verb. For a command
        # that does not write one, its 0 is the unassigned default, never an
        # answer (a412: vehicle_control read seated=0 gear=0 on a car in gear 2).
        for field, owners in sorted(VEHICLE_SCALAR_OWNERS.items()):
            for command in sorted(ALL_COMMANDS - owners):
                with self.subTest(field=field, command=command):
                    pruned = prune_unfilled_fields(
                        command,
                        _wire_result(**{field: VEHICLE_SCALAR_DEFAULTS[field]}),
                    )
                    self.assertNotIn(field, pruned)
                    self.assertEqual(pruned["ok"], 1)

    def test_pos_delta_is_dropped_from_every_result(self) -> None:
        # dde3: declared in MCPResult, assigned by no command. Its 0.0 reads as
        # "did not move" on every verb, vehicle_telemetry included.
        self.assertEqual(NEVER_FILLED_SCALAR_FIELDS, frozenset({"pos_delta"}))
        for command in sorted(ALL_COMMANDS):
            with self.subTest(command=command):
                pruned = prune_unfilled_fields(command, _wire_result(pos_delta=0.0))
                self.assertNotIn("pos_delta", pruned)
                self.assertEqual(pruned["ok"], 1)

    def test_a412_vehicle_control_reply_keeps_only_what_the_verb_fills(self) -> None:
        # The ticket's reply: ok=1, seated=0, seat="", gear=0, pos_delta=0,
        # engine_on_server=1, is_owner=0. Only engine_on_server is filled.
        wire = _wire_result(**VEHICLE_SCALAR_DEFAULTS)
        wire.update({"engine_on_server": 1, "pos_delta": 0.0})

        pruned = prune_unfilled_fields("vehicle_control", wire)

        self.assertEqual(pruned["engine_on_server"], 1)
        for field in set(VEHICLE_SCALAR_DEFAULTS) - {"engine_on_server"}:
            self.assertNotIn(field, pruned, field)
        self.assertNotIn("pos_delta", pruned)

    def test_dde3_entities_query_carries_no_vehicle_field(self) -> None:
        # The ticket's entities_query reply carried seated, gear, speedo_max,
        # pos_delta ... at 0 beside count_total and entities.
        wire = _wire_result(**VEHICLE_SCALAR_DEFAULTS)
        wire.update({"pos_delta": 0.0, "count_total": 23, "entities": [{"type": "X"}]})

        pruned = prune_unfilled_fields("entities_query", wire)

        self.assertEqual(pruned["count_total"], 23)
        self.assertEqual(pruned["entities"], [{"type": "X"}])
        for field in (*VEHICLE_SCALAR_DEFAULTS, "pos_delta"):
            self.assertNotIn(field, pruned, field)

    def test_vehicle_telemetry_keeps_every_field_it_writes(self) -> None:
        # vehicle_telemetry writes all of them but vehicle_fixture_ready; a real
        # reading of a parked car is mostly zeros and must arrive whole.
        wire = _wire_result(**VEHICLE_SCALAR_DEFAULTS)
        wire.update({"seated": 1, "seat": "driver", "gear": 0, "speedo_max": 0.0})

        pruned = prune_unfilled_fields("vehicle_telemetry", wire)

        for field, owners in VEHICLE_SCALAR_OWNERS.items():
            with self.subTest(field=field):
                if "vehicle_telemetry" in owners:
                    self.assertEqual(pruned[field], wire[field])
                else:
                    self.assertNotIn(field, pruned)

    def test_empty_dialog_is_pruned_except_on_ui_dialog(self) -> None:
        empty_object = prune_unfilled_fields(
            "world_spawn", {**_wire_result(), "dialog": {}}
        )
        self.assertNotIn("dialog", empty_object)
        empty_none = prune_unfilled_fields(
            "query_player_state", {**_wire_result(), "dialog": None}
        )
        self.assertNotIn("dialog", empty_none)
        kept_empty = prune_unfilled_fields(
            "ui_dialog", {**_wire_result(), "dialog": {}}
        )
        self.assertEqual(kept_empty["dialog"], {})
        filled = {"state": "completed", "elapsed_s": 1.0}
        kept_filled = prune_unfilled_fields(
            "ui_dialog", {**_wire_result(), "dialog": filled}
        )
        self.assertEqual(kept_filled["dialog"], filled)

    def test_unknown_keys_and_non_dict_results_pass_through(self) -> None:
        result = _wire_result()
        result["future_field"] = []
        pruned = prune_unfilled_fields("world_spawn", result)
        self.assertEqual(pruned["future_field"], [])
        self.assertEqual(result_prune.prune_unfilled_fields("x", None), None)
        self.assertEqual(result_prune.prune_unfilled_fields("x", []), [])

    def test_prunable_fields_match_the_bridge_result_class(self) -> None:
        # Expectation comes from MCPResult, not from a second Python tuple.
        # That is what fails when the Enforce class gains a `ref` member and
        # PRUNABLE_FIELDS is left behind.
        messages = (
            addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
        ).read_text(encoding="utf-8")
        self.assertEqual(PRUNABLE_FIELDS, _mcp_result_ref_members(messages))


# 7f27: scalars whose writers the census names, and one value each.
GENERIC_SCALAR_OWNERS = {
    "clicked": ({"ui_click"}, 0),
    "handler": ({"ui_click"}, ""),
    "user_id": ({"ui_click"}, 0),
    "delivered": ({"key_press"}, 0),
    "dik": ({"key_press"}, 0),
    "requested": ({"player_respawn"}, 0),
    "action": (
        {
            "action_use",
            "action_use_door",
            "action_use_target",
            "action_use_component",
            "action_hold",
        },
        "",
    ),
    "target": (
        {
            "action_use",
            "action_use_door",
            "action_use_target",
            "action_use_component",
            "action_hold",
        },
        "",
    ),
    "distance": (
        {
            "action_use",
            "action_use_door",
            "action_use_target",
            "action_use_component",
            "action_hold",
        },
        0.0,
    ),
    "started": (
        {
            "action_use",
            "action_use_door",
            "action_use_target",
            "action_use_component",
            "action_hold",
        },
        False,
    ),
    "confirmed": ({"hands_take", "weapon_state"}, 0),
    "sent": ({"exec_enforce", "notify_players"}, 0),
    "deleted": ({"object_delete"}, 0),
    "y": ({"surface_query"}, 0.0),
    "phase": ({"object_anim"}, 0.0),
    "source": ({"object_anim"}, ""),
    "deferred": ({"inventory_give"}, 0),
    "count_total": ({"entities_query"}, 0),
    "component_index": ({"action_use_door", "action_use_component", "action_hold"}, 0),
}

# Deliberately not owned. A stale 0 here is still not dropped.
UNRESOLVED_RESIDUAL_FIELDS = ("accepted", "found", "object_id")


class GenericScalarOwnershipTest(unittest.TestCase):
    def test_each_added_field_is_kept_for_owners_and_removed_from_nonowners(self) -> None:
        for field, (owners, default) in sorted(GENERIC_SCALAR_OWNERS.items()):
            self.assertEqual(result_prune.OWNED_SCALAR_FIELDS[field], frozenset(owners))
            for command in sorted(owners):
                with self.subTest(field=field, command=command, role="owner"):
                    self.assertIn(command, ALL_COMMANDS)
                    pruned = prune_unfilled_fields(command, _wire_result(**{field: default}))
                    self.assertIn(field, pruned)
                    self.assertEqual(pruned[field], default)
            for command in sorted(ALL_COMMANDS - owners):
                with self.subTest(field=field, command=command, role="nonowner"):
                    pruned = prune_unfilled_fields(
                        command, _wire_result(**{field: default, field + "_set": True})
                    )
                    self.assertNotIn(field, pruned)
                    # A non-default value is still noise on a command that
                    # never writes the field.
                    pruned_set = prune_unfilled_fields(
                        command, _wire_result(**{field: default or "filled"})
                    )
                    self.assertNotIn(field, pruned_set)

    def test_residual_fields_survive_on_a_command_that_does_not_write_them(self) -> None:
        pruned = prune_unfilled_fields(
            "vehicle_telemetry",
            _wire_result(accepted=0, found=False, object_id=0, clicked=0, handler="", user_id=0),
        )
        for field in UNRESOLVED_RESIDUAL_FIELDS:
            self.assertIn(field, pruned, field)
        self.assertNotIn("clicked", pruned)
        self.assertNotIn("handler", pruned)
        self.assertNotIn("user_id", pruned)
        for field in UNRESOLVED_RESIDUAL_FIELDS:
            self.assertNotIn(field, result_prune.OWNED_SCALAR_FIELDS)


class PruneResultPathTest(unittest.IsolatedAsyncioTestCase):
    """The four call_bridge result paths all prune before the caller sees the dict."""

    async def test_embedded_wait_and_probe_drop_unrelated_click_defaults(self) -> None:
        from types import SimpleNamespace

        from dayz_mcp.server import ServerConfig, build_app

        _app, runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        wire = {"ok": 1, "clicked": 0, "handler": "", "user_id": 0, "count_total": 3}
        runtime.loopback = SimpleNamespace(
            state=SimpleNamespace(take_result=lambda *_args, **_kwargs: dict(wire))
        )
        waited = await runtime.wait_for_result("entities_query", 1, "server", 1.0)
        probed = await runtime.probe_bridge_result("entities_query", 2, "server")
        for result in (waited, probed):
            self.assertEqual(result["count_total"], 3)
            self.assertNotIn("clicked", result)
            self.assertNotIn("handler", result)
            self.assertNotIn("user_id", result)

    async def test_client_wait_and_probe_drop_unrelated_click_defaults(self) -> None:
        import time

        from dayz_mcp.server import ClientRuntime

        runtime = object.__new__(ClientRuntime)
        runtime._time_fn = time.monotonic
        wire = {"ok": 1, "clicked": 0, "handler": "", "user_id": 0, "y": 12.5}

        def _call(*_args, **_kwargs):
            return 200, {"status": "done", "result": dict(wire)}

        runtime._call = _call
        waited = await runtime._await_result("surface_query", 1, "server", 1.0)
        probed = await runtime.probe_bridge_result("surface_query", 2, "server")
        for result in (waited, probed):
            self.assertEqual(result["y"], 12.5)
            self.assertNotIn("clicked", result)


if __name__ == "__main__":
    unittest.main()
