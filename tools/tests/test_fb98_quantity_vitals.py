"""object_inspect reads an item's quantity, query_player_state water and energy (inbox fb98).

action_hold could not show what a drink or an eat consumed: on 1.29 drink and
eat actions whose component is a CAContinuousQuantityRepeat count a cycle only
when the item runs out (completed_cycles 0 for a drink and an apple on 1.29,
reviews/2026-10-06-ticketing/ingame-v12/RESULTS.md:36), and no verb read an
item's quantity or the player's water and energy without changing them.
DispatchObjectInspect with FillInspectQuantity, and BuildPlayerState, are
translated and run against fakes (tests/enforce_subset_helpers.py) that mirror
the constructors pinned here; old and new answers go through the public tools.
"""
from __future__ import annotations

import functools
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from dayz_mcp import server
from dayz_mcp.effective_schema import resolve_effective_schemas
from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, method_body, translate
from tests.mcp_helpers import _content_json

SCRIPTS = addon_root() / "scripts" / "5_Mission"
BRIDGE = (SCRIPTS / "MCPBridge.c").read_text(encoding="utf-8")
MESSAGES = (SCRIPTS / "MCPMessages.c").read_text(encoding="utf-8")
CHANGELOG = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
VANILLA = Path(os.environ.get("DAYZ_MCP_VANILLA_SCRIPTS", "P:/scripts"))
FILL_QUANTITY = "protected void FillInspectQuantity(Object match, MCPObjectInspect inspect)"
DISPATCH_INSPECT = "protected bool DispatchObjectInspect(MCPCommand command, MCPResult result)"
BUILD_STATE = "protected MCPPlayerState BuildPlayerState()"
_ENFORCE_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_CLASS_DECLARATION = re.compile(r"(?m)^\s*(?:modded\s+)?class\s+([A-Za-z_]\w*)")
_CONSTRUCTION = re.compile(r"\bnew\s+([A-Za-z_]\w*)\s*\(\s*\)")
_CALL_NAME = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")
_VECTOR_LOCAL = re.compile(r"\bvector\s+([A-Za-z_]\w*)")
_INDEX = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]")
_MEMBER_RE = re.compile(
    r"(?m)(?<![A-Za-z0-9_>])(?P<type>(?:ref\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*<[^;{}]+>)?)"
    r"\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)


class _Array(list):
    def Insert(self, value: object) -> None:
        self.append(value)

    def Count(self) -> int:
        return len(self)

    def Get(self, index: int) -> object:
        return self[index]

    def Clear(self) -> None:
        self.clear()


class _Object:
    """A world object: its type and its memory points, and nothing else."""

    def __init__(self, type_name: str, points: tuple[str, ...] = ()) -> None:
        self._type = type_name
        self._points = points
        self.points_asked: list[str] = []

    def GetType(self) -> EnforceString:
        return EnforceString.of(self._type)

    def MemoryPointExists(self, name: EnforceString) -> bool:
        self.points_asked.append(name.text)
        return name.text in self._points

    def GetMemoryPointPos(self, _name: EnforceString) -> tuple[float, float, float]:
        return (1.0, 2.0, 3.0)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} is not one of the reads")


class _Item(_Object):
    """An ItemBase that answers the three quantity reads."""

    def __init__(self, has: bool, quantity: float = 0.0, maximum: int = 0, points: tuple[str, ...] = ()) -> None:
        super().__init__("WaterBottle", points)
        self._has = has
        self._quantity = quantity
        self._maximum = maximum

    def HasQuantity(self) -> bool:
        return self._has

    def GetQuantity(self) -> float:
        return self._quantity

    def GetQuantityMax(self) -> int:
        return self._maximum


class _ItemBase:
    @staticmethod
    def Cast(value: object) -> _Item | None:
        return value if isinstance(value, _Item) else None


class _Inspect:
    """MCPObjectInspect as its constructor leaves it; quantity and quantity_max keep Enforce's 0."""

    def __init__(self) -> None:
        self.type = EnforceString(b"")
        self.bounding_center = _Array()
        self.has_bounding_center = False
        self.memory_points = _Array()
        self.has_quantity = False
        self.quantity = 0.0
        self.quantity_max = 0.0


class _Point:
    def __init__(self) -> None:
        self.name = EnforceString(b"")
        self.exists = False
        self.pos = _Array()


class _Man:
    """A Man the server lists: where it is and who it is, and nothing else."""

    def GetPosition(self) -> tuple[float, float, float]:
        return _Vector((4.0, 5.0, 6.0))

    def GetIdentity(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"{name} is not one of the reads")


class _Body(_Man):
    """A PlayerBase, with the stats FillPlayerVitals reads."""

    def __init__(self, water: float, energy: float) -> None:
        self.water = water
        self.energy = energy


class _Vector(tuple):
    def At(self, index: int) -> float:
        return self[index]


class _State:
    """MCPPlayerState as its constructor leaves it; water and energy keep Enforce's 0."""

    def __init__(self) -> None:
        self.name = EnforceString(b"")
        self.pos = _Array()
        self.godmode = False
        self.water = 0.0
        self.energy = 0.0


class _Vitals:
    """MCPPlayerVitals, which has no constructor."""

    def __init__(self) -> None:
        self.water = 0.0
        self.energy = 0.0


def _translated(
    signature: str, namespace: dict[str, Any], class_types: tuple[str, ...], body: str | None = None
) -> Any:
    """One MCPBridge method, or ``body`` in its place, adapted for the translator.
    A class its run builds needs a fake in the namespace."""
    built = frozenset(name for name, fake in namespace.items() if isinstance(fake, type))
    source = method_body(BRIDGE, signature) if body is None else body
    adapted = _adapted(source, _addon_classes() | built)
    return translate(signature + "\n{" + adapted + "}\n", signature, namespace, class_types=class_types)


@functools.lru_cache(maxsize=1)
def _addon_classes() -> frozenset[str]:
    """The classes the addon's scripts declare."""
    return frozenset(
        name
        for path in sorted((addon_root() / "scripts").rglob("*.c"))
        for name in _CLASS_DECLARATION.findall(path.read_text(encoding="utf-8"))
    )


def _adapted(body: str, classes: frozenset[str]) -> str:
    """``body`` with `new X()` read as `X()` for a class in ``classes``, and an index
    `v[i]` of a local it declares `vector` read as `v.At(i)`, outside its string
    literals, which the translator does not read. Any other `new` or index stays
    for the translator to refuse.

    The source is checked first (Codex H6, H8): it may not call `.At(`, which
    Enforce's vector does not declare, nor call a class in ``classes`` without new.
    """
    pieces: list[str] = []
    last = 0
    for literal in _ENFORCE_STRING.finditer(body):
        pieces += [body[last : literal.start()], literal.group()]
        last = literal.end()
    pieces.append(body[last:])
    vectors = frozenset(name for code in pieces[0::2] for name in _VECTOR_LOCAL.findall(code))
    return "".join(
        piece if index % 2 else _rewrite(piece, classes, vectors) for index, piece in enumerate(pieces)
    )


def _rewrite(code: str, classes: frozenset[str], vectors: frozenset[str]) -> str:
    if re.search(r"\.\s*At\s*\(", code):
        raise AssertionError("the source calls .At(), which only a rewritten vector index may")
    for call in _CALL_NAME.finditer(code):
        if call.group(1) in classes and not re.search(r"\bnew\s+$", code[: call.start()]):
            raise AssertionError(f"the source calls {call.group(1)}() without new")
    code = _CONSTRUCTION.sub(
        lambda found: f"{found.group(1)}()" if found.group(1) in classes else found.group(), code
    )
    return _INDEX.sub(
        lambda found: f"{found.group(1)}.At({found.group(2)})" if found.group(1) in vectors else found.group(),
        code,
    )


def _dispatched(target: _Object, *wants: str) -> SimpleNamespace:
    namespace: dict[str, Any] = {
        "ItemBase": _ItemBase,
        "MCPObjectInspect": _Inspect,
        "MCPMemoryPoint": _Point,
        "ResolveCommandObject": lambda _args, _error: target,
        "VectorToArray": lambda vector, out: out.extend(vector),
    }
    _translated(FILL_QUANTITY, namespace, ("ItemBase",))
    dispatch = _translated(
        DISPATCH_INSPECT, namespace, ("Object", "MCPObjectInspect", "MCPTelemetry", "MCPMemoryPoint", "vector")
    )
    command = SimpleNamespace(
        args=SimpleNamespace(want=_Array(EnforceString.of(want) for want in wants), object_id=7)
    )
    result = SimpleNamespace(ok=False, error=EnforceString(b""), telemetry=None, inspect=None, type=None, object_id=0)
    if dispatch(command, result) is not True:
        raise AssertionError("DispatchObjectInspect did not handle the command")
    return result


def _state(
    listed: list[_Man | None], body: str | None = None
) -> tuple[_State | None, list[tuple[object, object]]]:
    filled: list[tuple[object, object]] = []

    def fill(target: _Body, vitals: _Vitals) -> None:
        filled.append((target, vitals))
        vitals.water = target.water
        vitals.energy = target.energy

    namespace: dict[str, Any] = {
        "m_Players": _Array(),
        "GetGame": lambda: SimpleNamespace(GetPlayers=lambda out: out.extend(listed)),
        "MCPGodmode": SimpleNamespace(IsOn=lambda _player: False),
        "MCPPlayerState": _State,
        "MCPPlayerVitals": _Vitals,
        "PlayerBase": SimpleNamespace(Cast=lambda man: man if isinstance(man, _Body) else None),
        "FillPlayerVitals": fill,
    }
    build = _translated(
        BUILD_STATE,
        namespace,
        ("Man", "vector", "PlayerIdentity", "PlayerBase", "MCPPlayerState", "MCPPlayerVitals"),
        body,
    )
    return build(), filled


def _statements(body: str) -> list[str]:
    return [" ".join(part.split()) for part in body.split(";") if part.strip()]


@functools.lru_cache(maxsize=1)
def _published() -> dict[str, dict]:
    return resolve_effective_schemas()


def _class_members(source: str, class_name: str) -> list[tuple[str, str]]:
    body = method_body(source, f"class {class_name}")
    return [(item.group("type"), item.group("name")) for item in _MEMBER_RE.finditer(body)]


class InspectQuantityTest(unittest.TestCase):
    def test_want_quantity_reads_the_item_and_is_no_memory_point(self) -> None:
        bottle = _Item(True, 350.0, 1000, points=("spout",))
        result = _dispatched(bottle, "quantity", "spout")

        self.assertIs(result.ok, True)
        inspect = result.inspect
        self.assertEqual((inspect.has_quantity, inspect.quantity, inspect.quantity_max), (True, 350.0, 1000))
        self.assertEqual([(point.name.text, point.exists) for point in inspect.memory_points], [("spout", True)])
        self.assertEqual(bottle.points_asked, ["spout"])

    def test_anything_else_answers_false_and_zero(self) -> None:
        for label, target in (
            ("item without a quantity", _Item(False, 350.0, 1000)),
            ("not an item", _Object("Land_House")),
        ):
            with self.subTest(target=label):
                result = _dispatched(target, "quantity")
                # An answer, not an error (Codex H7).
                self.assertEqual((result.ok, result.error.text), (True, ""))
                inspect = result.inspect
                self.assertEqual((inspect.has_quantity, inspect.quantity, inspect.quantity_max), (False, 0.0, 0.0))
                self.assertEqual((list(inspect.memory_points), target.points_asked), ([], []))

    def test_the_inspect_answer_carries_the_three_fields(self) -> None:
        self.assertEqual(
            _class_members(MESSAGES, "MCPObjectInspect"),
            [
                ("string", "type"),
                ("ref array<float>", "bounding_center"),
                ("bool", "has_bounding_center"),
                ("ref array<ref MCPMemoryPoint>", "memory_points"),
                ("bool", "has_quantity"),
                ("float", "quantity"),
                ("float", "quantity_max"),
            ],
        )
        # _Inspect is this constructor.
        self.assertEqual(
            _statements(method_body(MESSAGES, "void MCPObjectInspect()")),
            [
                "bounding_center = new array<float>()",
                "memory_points = new array<ref MCPMemoryPoint>()",
                "has_bounding_center = false",
                "has_quantity = false",
            ],
        )


class PlayerStateVitalsTest(unittest.TestCase):
    # The members of MCPPlayerState are checked with the rest of
    # query_player_state in tests/test_player_heal_godmode.py.
    def test_a_body_answers_its_own_water_and_energy(self) -> None:
        body = _Body(water=1234.5, energy=2345.5)
        state, filled = _state([body])

        self.assertEqual((state.water, state.energy), (1234.5, 2345.5))
        ((target, vitals),) = filled
        self.assertIs(target, body)
        self.assertIsInstance(vitals, _Vitals)

    def test_a_player_that_is_no_body_answers_zero(self) -> None:
        state, filled = _state([_Man()])

        self.assertEqual((state.water, state.energy), (0.0, 0.0))
        self.assertEqual(filled, [])

    def test_no_player_answers_null(self) -> None:
        # Codex H3: a null first player, which the `!player` guard catches.
        for label, listed in (("none listed", []), ("a null one", [None])):
            with self.subTest(players=label):
                state, filled = _state(listed)

                self.assertIsNone(state)
                self.assertEqual(filled, [])

    def test_the_state_constructor_sets_only_pos(self) -> None:
        # _State is this constructor.
        self.assertEqual(_statements(method_body(MESSAGES, "void MCPPlayerState()")), ["pos = new array<float>()"])


def _spliced(body: str, found: re.Match[str], text: str) -> str:
    return body[: found.start()] + text + body[found.end() :]


class AdaptationTest(unittest.TestCase):
    """The translator's rewrites refuse a source that already reads their way (Codex H6,
    H8) and read its equivalent forms the same way (Codex H9)."""

    def test_each_construction_and_vector_index_already_rewritten_is_refused(self) -> None:
        built: list[str] = []
        indexed: list[str] = []
        for signature in (DISPATCH_INSPECT, FILL_QUANTITY, BUILD_STATE):
            body = method_body(BRIDGE, signature)
            vectors = _VECTOR_LOCAL.findall(body)
            for found in _CONSTRUCTION.finditer(body):
                built.append(found.group(1))
                with self.subTest(signature=signature, source=found.group()):
                    with self.assertRaises(AssertionError):
                        _adapted(_spliced(body, found, f"{found.group(1)}()"), _addon_classes())
            for found in _INDEX.finditer(body):
                self.assertIn(found.group(1), vectors)
                indexed.append(found.group(2))
                with self.subTest(signature=signature, source=found.group()):
                    with self.assertRaises(AssertionError):
                        _adapted(_spliced(body, found, f"{found.group(1)}.At({found.group(2)})"), _addon_classes())
            self.assertNotRegex(_adapted(body, _addon_classes()), r"\bnew\b|\[")
        self.assertEqual(
            sorted(built), ["MCPMemoryPoint", "MCPObjectInspect", "MCPPlayerState", "MCPPlayerVitals", "MCPTelemetry"]
        )
        self.assertEqual(indexed, ["0", "1", "2"])

    def test_another_vector_name_and_more_spaces_read_the_same(self) -> None:
        body = method_body(BRIDGE, BUILD_STATE)
        equivalent = _CONSTRUCTION.sub(
            lambda found: f"new  {found.group(1)} ( )", re.sub(r"(?<![\w.])pos\b", "where", body)
        )
        self.assertNotIn("pos[", equivalent)
        self.assertIn("new  MCPPlayerState ( )", equivalent)
        for label, listed in (("body", [_Body(water=1234.5, energy=2345.5)]), ("man", [_Man()]), ("none", [])):
            with self.subTest(players=label):
                expected, _filled = _state(listed)
                seen, _filled = _state(listed, equivalent)
                self.assertEqual(vars(seen) if seen else seen, vars(expected) if expected else expected)


class PublicAnswerTest(unittest.IsolatedAsyncioTestCase):
    """Old and new bridge answers through wait_for_result, the prune and the public tool."""

    async def _payloads(self, tool: str, arguments: dict[str, Any], *answers: dict[str, Any]) -> list[dict[str, Any]]:
        app, runtime = server.build_app(
            server.ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        runtime.start_loopback()
        self.addCleanup(runtime.stop_loopback)
        real_enqueue = runtime.state.enqueue_command
        planted: dict[str, Any] = {}

        def enqueue(cmd, args, peer="server", operation_timeout_s=0.0, **_kwargs):
            status, body = real_enqueue(cmd, args, peer=peer, operation_timeout_s=operation_timeout_s)
            if status == 200:
                # As the bridge posts it: JSON, so the answer shares nothing with the test's copy.
                answer = json.dumps({"id": body["id"], "ok": True, "error": "", "cmd": cmd, **planted})
                runtime.state.store_result(json.loads(answer))
            return status, body

        payloads = []
        with patch("dayz_mcp.server._world_read_not_ready", return_value=None):
            with patch.object(runtime.state, "enqueue_command", enqueue):
                for answer in answers:
                    planted.clear()
                    planted.update(answer)
                    payloads.append(_content_json(await app.call_tool(tool, {**arguments, "timeout_s": 2.0})))
        return payloads

    async def test_object_inspect_passes_the_quantity_on_and_adds_none(self) -> None:
        inspect = {"type": "WaterBottle", "bounding_center": [], "has_bounding_center": False}
        new = {**inspect, "memory_points": [], "has_quantity": True, "quantity": 350.0, "quantity_max": 1000.0}
        old = {**inspect, "memory_points": [{"name": "quantity", "exists": False, "pos": []}]}
        answers = [{"type": "WaterBottle", "object_id": 7, "state": {}, "inspect": body} for body in (new, old)]

        payloads = await self._payloads("object_inspect", {"object_id": 7, "want": ["quantity"]}, *answers)
        for label, answer, payload in zip(("new", "older PBO"), answers, payloads):
            with self.subTest(answer=label):
                self.assertEqual(payload["inspect"], answer["inspect"])
                self.assertNotIn("state", payload)

    async def test_query_player_state_passes_water_and_energy_on_and_adds_none(self) -> None:
        state = {"name": "Survivor", "pos": [4.0, 5.0, 6.0], "godmode": False}
        answers = [{"inspect": {}, "state": body} for body in ({**state, "water": 1234.5, "energy": 2345.5}, state)]

        payloads = await self._payloads("query_player_state", {}, *answers)
        for label, answer, payload in zip(("new", "older PBO"), answers, payloads):
            with self.subTest(answer=label):
                self.assertEqual(payload["state"], answer["state"])
                self.assertNotIn("inspect", payload)


class DescriptionsTest(unittest.TestCase):
    def test_each_tool_says_what_it_reads(self) -> None:
        for tool, sentence in (
            ("object_inspect", "want 'quantity' reads an item's quantity without changing it"),
            ("object_inspect", "an older PBO has no has_quantity"),
            ("query_player_state", "water and energy are the body's stats, 0 to 5000, as player_heal reads them"),
            ("query_player_state", "an older PBO leaves them out"),
            (
                "action_hold",
                "On 1.29 drink and eat actions whose component is a CAContinuousQuantityRepeat, such as "
                "ActionDrink and ActionEatFruit, count a cycle only when the item runs out, so "
                "completed_cycles stays 0 while they consume",
            ),
            ("action_hold", "read the item with object_inspect(want=['quantity'])"),
        ):
            with self.subTest(tool=tool, sentence=sentence):
                self.assertIn(sentence, _published()[tool]["description"])

    def test_the_changelog_says_the_same(self) -> None:
        unreleased = CHANGELOG[CHANGELOG.index("## [Unreleased]") : CHANGELOG.index("## [1.4]")]
        for sentence in (
            "`object_inspect(want=['quantity'])` reads an item's quantity without changing it",
            "`quantity` is no longer read as a memory point",
            "`query_player_state` adds the body's `water` and `energy`, 0 to 5000, read as `player_heal` reads them",
            "there drink and eat actions whose component is a `CAContinuousQuantityRepeat`, such as "
            "`ActionDrink` and `ActionEatFruit`, count a cycle only when the item runs out",
            "an older PBO answers without `has_quantity`, `water` and `energy`",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(sentence, unreleased)


@unittest.skipUnless(VANILLA.is_dir(), f"vanilla scripts tree not present: {VANILLA}")
class VanillaPremiseTest(unittest.TestCase):
    """The actions action_hold's description names consume through a CAContinuousQuantityRepeat."""

    def _vanilla(self, name: str) -> str:
        paths = list(VANILLA.rglob(name))
        self.assertEqual(len(paths), 1, f"{name} under {VANILLA}: {paths}")
        return paths[0].read_text(encoding="utf-8", errors="replace")

    def test_drink_and_eat_fruit_consume_through_a_quantity_repeat(self) -> None:
        for name in ("actiondrink.c", "actioneatfruit.c"):
            with self.subTest(file=name):
                self.assertRegex(self._vanilla(name), r"\bnew\s+CAContinuousQuantityEdible\s*\(")
        self.assertRegex(
            self._vanilla("cacontinuousquantityedible.c"),
            r"\bclass\s+CAContinuousQuantityEdible\s*:\s*CAContinuousQuantityRepeat\b",
        )


if __name__ == "__main__":
    unittest.main()
