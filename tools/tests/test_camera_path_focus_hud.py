"""camera_path_play (d026), camera_set focus (702f) and hud_set (f427).

Contract, tool, admission and census checks run here. MCP_CameraPath.c and the
play and frame methods of MCPClientBridge.c also run here, translated from the
.c source by tests.enforce_subset_helpers (see _ExecutedCameraPath below);
that runs their branching and arithmetic, not the engine. The other asserts on
Enforce text are wiring only. Neither credits in-game behavior, which the PR
lists for the final round.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import struct
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from dayz_mcp import camera_path, loopback, result_prune, server
from dayz_mcp.bridge_errors import _public_enqueue_error
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.session_coordination import READ_ONLY_COMMANDS, command_requires_lease
from tests._addon_paths import addon_root
from tests.bridge_client_capabilities_helpers import (
    _method_body,
    announced_caps,
    dispatch_census,
)
from tests.enforce_subset_helpers import EnforceString, method_body, translate
from tests.fence_helpers import INST_CLIENT, PID_CLIENT, bind_both_peers, bound_queue
from tests.mcp_helpers import _assert_tool_error, _content_json


MISSION = addon_root() / "scripts" / "5_Mission"
BRIDGE = MISSION / "MCPClientBridge.c"
MESSAGES = MISSION / "MCPMessages.c"
CAMERA_PATH_C = MISSION / "MCP_CameraPath.c"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bridge_capabilities_v1.json"
NEW_COMMANDS = ("camera_path_play", "camera_set_focus", "hud_set")
VALID_WIRE_ARGS: dict[str, dict[str, Any]] = {
    "camera_path_play": {"mode": "state", "path_id": ""},
    "camera_set_focus": {
        "cam_mode": "orient",
        "cam_pos": [0.0, 2.0, 0.0],
        "cam_orientation": [90.0, 0.0, 0.0],
        "fov": 0.0,
        "settle_ticks": 3,
        "focus_distance": 2.0,
        "focus_blur": 1.0,
    },
    "hud_set": {"visible": False},
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Enforce source without // comments, so a comment cannot satisfy an assert."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _flat(text: str) -> str:
    return " ".join(_code(text).split())


def _class_body(source: str, name: str) -> str:
    return _method_body(source, f"class {name}")


def _members(body: str) -> list[tuple[str, str]]:
    found = []
    for line in _code(body).splitlines():
        match = re.fullmatch(r"\s*((?:ref )?[\w<> ]+?) (\w+);\s*", line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def _tool_error_text(exc: BaseException, tool: str) -> str:
    message = str(exc)
    wrapper = f"Error executing tool {tool}: "
    return message[len(wrapper) :] if message.startswith(wrapper) else message


def _key(t: float, **overrides: object) -> dict[str, object]:
    key: dict[str, object] = {"t": t, "cam_pos": [t, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]}
    key.update(overrides)
    return key


def _three_keys() -> list[dict[str, object]]:
    return [_key(0), _key(3.0), _key(6.0)]


# Executed Enforce. Each method body below is translated from the .c source by
# tests.enforce_subset_helpers and run on fakes. Before translation, members
# (m_*) and the sibling methods a body calls are read through `this`, which
# comes first in the signature; `new X()` becomes a call; array<float> locals
# are FloatArray; and each C division a / b is written a * Over(b), whose
# __rmul__ divides and refuses an integer pair (C truncates it) or a zero
# divisor. Numbers are _F32: rounded to a 32-bit float after every operation,
# an overflow an infinity, as the bridge's float. Engine calls are fakes.

_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_PRIMARY = re.compile(r"\d+\.\d+|\d+|[A-Za-z_]\w*")
_SIGNATURE_PARTS = re.compile(r"^(?P<head>.*?)(?P<name>\w+)\s*\((?P<params>[^()]*)\)$")
_CONSTANT = re.compile(r"static const (int|float|string) (\w+) = ([^;]+);")


def _round32(number: float) -> float:
    try:
        return struct.unpack("<f", struct.pack("<f", number))[0]
    except OverflowError:
        return math.copysign(math.inf, number)


class _F32(float):
    """A float the way the bridge's float holds it."""

    __slots__ = ()

    def __new__(cls, value: object) -> "_F32":
        try:
            number = float(value)  # type: ignore[arg-type]
        except OverflowError:
            number = math.inf if value > 0 else -math.inf  # type: ignore[operator]
        return super().__new__(cls, _round32(number))

    @staticmethod
    def _of(other: object) -> float | None:
        if isinstance(other, bool) or not isinstance(other, (int, float)):
            return None
        return float(other)

    def __add__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(float(self) + value)

    __radd__ = __add__

    def __sub__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(float(self) - value)

    def __rsub__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(value - float(self))

    def __mul__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(float(self) * value)

    __rmul__ = __mul__

    def __truediv__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(float(self) / value)

    def __rtruediv__(self, other: object) -> Any:
        value = self._of(other)
        return NotImplemented if value is None else _F32(value / float(self))

    def __neg__(self) -> "_F32":
        return _F32(-float(self))


class _Over:
    """The divisor of a C division a / b, written a * Over(b)."""

    __slots__ = ("divisor",)

    def __init__(self, divisor: object) -> None:
        self.divisor = divisor

    def __rmul__(self, dividend: object) -> Any:
        if isinstance(dividend, int) and isinstance(self.divisor, int):
            raise AssertionError("int / int truncates in Enforce")
        if self.divisor == 0:
            raise AssertionError("division by zero")
        return dividend / self.divisor  # type: ignore[operator]


class _Text(EnforceString):
    """EnforceString plus IndexOf (enstring.c) and the + of a Log line."""

    __slots__ = ()

    def IndexOf(self, sample: EnforceString) -> int:
        return self.data.find(sample.data)

    def __add__(self, other: object) -> "_Text":
        if isinstance(other, EnforceString):
            return _Text(self.data + other.data)
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return _Text(self.data + str(other).encode("ascii"))
        raise TypeError(f"string + {type(other).__name__}")


class _Array(list):
    """An Enforce array: Count, Get inside its range, Insert, Clear; never null."""

    def Count(self) -> int:
        return len(self)

    def Get(self, index: int) -> Any:
        if type(index) is not int or not 0 <= index < len(self):
            raise AssertionError(f"Get({index!r}) outside an array of {len(self)}")
        return self[index]

    def Insert(self, value: object) -> int:
        self.append(value)
        return len(self) - 1

    def Clear(self) -> None:
        self.clear()

    def __bool__(self) -> bool:
        return True


def _closing(code: str, start: int) -> int:
    depth = 0
    for index in range(start, len(code)):
        depth += (code[index] == "(") - (code[index] == ")")
        if depth == 0:
            return index + 1
    raise AssertionError(f"unbalanced parentheses in {code!r}")


def _divisions_as_products(code: str) -> str:
    """Each a / b of code (comments blanked, no string literal) as a * Over(b)."""
    pieces: list[str] = []
    position = 0
    while True:
        slash = code.find("/", position)
        if slash < 0:
            pieces.append(code[position:])
            return "".join(pieces)
        start = slash + 1
        while code[start].isspace():
            start += 1
        if code[start] == "(":
            end = _closing(code, start)
        else:
            primary = _PRIMARY.match(code, start)
            if primary is None:
                raise AssertionError(f"division before {code[start:start + 20]!r}")
            end = primary.end()
            while True:
                if code.startswith("(", end):
                    end = _closing(code, end)
                elif code.startswith(".", end) and re.match(r"[A-Za-z_]", code[end + 1 : end + 2]):
                    end = re.compile(r"\.\w+").match(code, end).end()  # type: ignore[union-attr]
                else:
                    break
        pieces.append(code[position:slash] + "* Over(" + code[start:end] + ")")
        position = end


def _rewrite(code: str, members: str, methods: str) -> str:
    code = code.replace("new array<float>()", "FloatArray()")
    code = re.sub(r"\bnew (\w+)\(\)", r"\1()", code)
    code = re.sub(r"\barray<float>\s+(?=\w+\s*;)", "FloatArray ", code)
    code = re.sub(r"(?<![\w.])(" + members + r")\b", r"this.\1", code)
    if methods:
        code = re.sub(r"(?<![\w.])(" + methods + r")\s*\(", r"this.\1(", code)
    return _divisions_as_products(code)


def _function(
    source: str,
    signature: str,
    *,
    this_type: str = "",
    name: str = "",
    members: str = r"m_\w+",
    methods: str = "",
) -> tuple[str, str]:
    """(signature, source) of one method of source as a function, with `this` first."""
    parts = _SIGNATURE_PARTS.match(signature)
    if parts is None:
        raise AssertionError(f"unsupported signature: {signature}")
    params = parts["params"].strip()
    if this_type:
        params = f"{this_type} this" + (f", {params}" if params else "")
    head = f"{parts['head']}{name or parts['name']}({params})"
    body = method_body(source, signature)
    pieces: list[str] = []
    last = 0
    for literal in _STRING_LITERAL.finditer(body):
        pieces += [_rewrite(body[last : literal.start()], members, methods), literal.group()]
        last = literal.end()
    pieces.append(_rewrite(body[last:], members, methods))
    return head, head + "\n{" + "".join(pieces) + "}\n"


def _constants(source: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for kind, name, value in _CONSTANT.findall(method_body(source, "class MCPCameraPath")):
        if kind == "int":
            found[name] = int(value)
        elif kind == "float":
            found[name] = _F32(float(value))
        else:
            found[name] = _Text.of(json.loads(value))
    return found


def _round_half_away(value: float) -> float:
    """Math.Round: to the nearest whole number, a half away from 0."""
    return math.copysign(math.floor(abs(value) + 0.5), value)


_PATH_STATICS = (
    "static bool IsBoundedValue(float value)",
    "static bool IsValidPathId(string pathId)",
    "protected static bool IsBoundedTriple(array<float> values)",
    "protected static bool IsPresent(array<float> values)",
    "protected static float UnwrapAngle(float previous, float angle)",
)
_PATH_METHODS = (
    "string Load(MCPArgs args)",
    "void Begin()",
    "bool IsPlaying()",
    "void Abort()",
    "bool HasPathId(string pathId)",
    "float Duration()",
    "vector StartPosition()",
    "void Advance(float timeslice)",
    "void Fill(MCPCameraPathState report)",
    "void Apply(Camera cam)",
    "protected float Sample(array<float> values, int segment, float s, float h)",
    "protected float ScaledTangent(array<float> values, int index, float h)",
)
_PATH_SIBLINGS = "Duration|Sample|ScaledTangent"
_PATH_LOCALS = {"MCPPathKey", "FloatArray", "vector"}


def _executed_camera_path(source: str) -> type:
    """MCPCameraPath with its constructor and methods translated from source."""
    namespace: dict[str, Any] = {
        "_S": _Text,
        "Math": SimpleNamespace(Round=_round_half_away),
        "vector": SimpleNamespace(
            Zero=(0.0, 0.0, 0.0),
            DistanceSq=lambda a, b: sum((x - y) * (x - y) for x, y in zip(a, b)),
        ),
        "Vector": lambda x, y, z: (x, y, z),
        "Over": _Over,
        "FloatArray": _Array,
        **_constants(source),
    }
    for signature in _PATH_STATICS:
        head, code = _function(source, signature)
        translate(code, head, namespace, class_types=_PATH_LOCALS)
    path_class = type("ExecutedCameraPath", (), {})
    head, code = _function(source, "void MCPCameraPath()", this_type="MCPCameraPath", name="Construct")
    path_class.__init__ = translate(code, head, namespace)  # type: ignore[misc]
    for signature in _PATH_METHODS:
        head, code = _function(source, signature, this_type="MCPCameraPath", methods=_PATH_SIBLINGS)
        method = translate(code, head, namespace, class_types=_PATH_LOCALS)
        setattr(path_class, method.__name__, method)
    return path_class


def _floats(values: object) -> _Array | None:
    """A JSON number array as the bridge's array<float> holds it; absent is null."""
    if values is None:
        return None
    return _Array(_F32(value) for value in values)  # type: ignore[union-attr]


def _enforce_args(wire: dict[str, Any]) -> SimpleNamespace:
    """camera_path_play wire args as MCPArgs: floats of 32 bits, an absent fov 0."""
    keys = None
    if wire.get("keys") is not None:
        keys = _Array(
            SimpleNamespace(
                t=_F32(key.get("t", 0.0)),
                cam_pos=_floats(key.get("cam_pos")),
                cam_orientation=_floats(key.get("cam_orientation")),
                look_at=_floats(key.get("look_at")),
                fov=_F32(key.get("fov", 0.0)),
            )
            for key in wire["keys"]
        )
    return SimpleNamespace(
        mode=_Text.of(wire.get("mode", "")),
        keys=keys,
        easing=_Text.of(wire.get("easing", "")),
        path_id=_Text.of(wire.get("path_id", "")),
    )


def _report(path: object) -> dict[str, Any]:
    """What Fill puts in the camera_path block."""
    report = SimpleNamespace(state=None, t_s=None, duration_s=None, path_id=None)
    path.Fill(report)  # type: ignore[attr-defined]
    return {
        "state": report.state.text,
        "t_s": float(report.t_s),
        "duration_s": float(report.duration_s),
        "path_id": report.path_id.text,
    }


class _Camera:
    """A Camera fake: the pose calls a path makes, SetActive and IsActive."""

    def __init__(self, log: list[tuple[Any, ...]], name: str) -> None:
        self.log = log
        self.name = name
        self.active = False
        self.position: tuple[float, ...] | None = None
        self.orientation: tuple[float, ...] | None = None
        self.looked_at: list[tuple[float, ...]] = []
        self.fov: float | None = None

    def SetPosition(self, position: tuple[float, ...]) -> None:
        self.position = tuple(float(value) for value in position)
        self.log.append((self.name, "SetPosition", self.position))

    def SetOrientation(self, orientation: tuple[float, ...]) -> None:
        self.orientation = tuple(float(value) for value in orientation)
        self.log.append((self.name, "SetOrientation", self.orientation))

    def LookAt(self, point: tuple[float, ...]) -> None:
        self.looked_at.append(tuple(float(value) for value in point))
        self.log.append((self.name, "LookAt", self.looked_at[-1]))

    def SetFOV(self, fov: float) -> None:
        self.fov = float(fov)
        self.log.append((self.name, "SetFOV", self.fov))

    def SetActive(self, active: bool) -> None:
        self.active = active
        self.log.append((self.name, "SetActive", active))

    def IsActive(self) -> bool:
        return self.active


_BRIDGE_METHODS = (
    "protected bool ProcessCameraPathPlayJob(MCPJob job)",
    "protected void TickCameraPath(float timeslice)",
    "protected void StopCameraPath()",
    "protected void DeleteOwnedCamera()",
)
_BRIDGE_SIBLINGS = (
    "FinishCameraHandoff|SuppressGameplay|DeleteOwnedCamera|RestoreGameplay|StopCameraPath|Log"
)


class _ClientBridge:
    """The members the play and frame methods use; the rest of the bridge is a log.

    ProcessCameraPathPlayJob, TickCameraPath, StopCameraPath and
    DeleteOwnedCamera are the translated ones. FinishCameraHandoff,
    SuppressGameplay and RestoreGameplay only log their call, here with no
    handoff pending. The engine is a log of calls on fake objects.
    """

    def __init__(self, bridge_source: str, path_class: type) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.free_cam = _Camera(self.log, "free")
        self.next_object: object = None
        self.player = SimpleNamespace(
            DisableSimulation=lambda off: self.log.append(("player", "DisableSimulation", off))
        )
        self.m_CameraPath = path_class()
        self.m_CameraPathCam: _Camera | None = None
        self.m_ActiveCam: _Camera | None = None
        self.m_ActiveCamOwned = False
        self.m_PlayerSimulationDisabled = False
        namespace: dict[str, Any] = {
            "_S": _Text,
            "MCPCameraPath": path_class,
            "g_Game": SimpleNamespace(CreateObject=self._create, ObjectDelete=self._delete),
            "Camera": SimpleNamespace(Cast=lambda thing: thing if isinstance(thing, _Camera) else None),
            "FreeDebugCamera": SimpleNamespace(GetInstance=lambda: self.free_cam),
            "PlayerBase": SimpleNamespace(Cast=lambda thing: thing),
            "GetGame": lambda: SimpleNamespace(GetPlayer=lambda: self.player),
        }
        for signature in _BRIDGE_METHODS:
            head, code = _function(
                bridge_source, signature, this_type="MCPClientBridge", methods=_BRIDGE_SIBLINGS
            )
            method = translate(
                code,
                head,
                namespace,
                class_types={"MCPCameraPath", "Object", "Camera", "FreeDebugCamera", "PlayerBase"},
            )
            setattr(self, method.__name__, MethodType(method, self))

    def _create(self, kind: _Text, position: tuple[float, ...], local: bool) -> object:
        self.log.append(("engine", "CreateObject", kind.text, tuple(float(v) for v in position), local))
        return self.next_object

    def _delete(self, thing: object) -> None:
        self.log.append(("engine", "ObjectDelete", thing))

    def FinishCameraHandoff(self) -> None:
        self.log.append(("bridge", "FinishCameraHandoff"))

    def SuppressGameplay(self) -> None:
        self.log.append(("bridge", "SuppressGameplay"))

    def RestoreGameplay(self) -> None:
        self.log.append(("bridge", "RestoreGameplay"))

    def Log(self, line: _Text) -> None:
        self.log.append(("bridge", "Log", line.text))

    def play(self, wire: dict[str, Any], job_id: int = 7) -> SimpleNamespace:
        job = SimpleNamespace(id=job_id, args=_enforce_args(wire), error=_Text.of(""))
        job.done = self.ProcessCameraPathPlayJob(job)  # type: ignore[attr-defined]
        return job

    def index(self, entry: tuple[Any, ...]) -> int:
        return self.log.index(entry)


class CameraPathContractTest(unittest.TestCase):
    def test_play_builds_the_exact_wire_args(self) -> None:
        keys = [
            {"t": 0, "cam_pos": [0, 2, 0], "look_at": [0, 1, 9], "fov": 1},
            {"t": 2.5, "cam_pos": [5.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 0.5},
        ]
        args = camera_path.normalize_request("play", keys, "smooth", "orbit-2")
        self.assertEqual(
            args,
            {
                "mode": "play",
                "keys": [
                    {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 1.0},
                    {"t": 2.5, "cam_pos": [5.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0], "fov": 0.5},
                ],
                "easing": "smooth",
                "path_id": "orbit-2",
            },
        )
        # Numbers travel as floats: the Enforce key reads t and fov as float.
        self.assertIsInstance(args["keys"][0]["t"], float)
        self.assertEqual(loopback.validate_command_args("camera_path_play", args), (True, None))

    def test_state_and_stop_carry_mode_and_path_id_only(self) -> None:
        for mode in ("state", "stop"):
            with self.subTest(mode=mode):
                args = camera_path.normalize_request(mode, None, "smooth", "dolly_1")
                self.assertEqual(args, {"mode": mode, "path_id": "dolly_1"})
                self.assertEqual(loopback.validate_command_args("camera_path_play", args), (True, None))

    def test_refusals_name_what_is_wrong(self) -> None:
        cases = (
            (("pause", None, "smooth", ""), "mode must be one of play, state, stop"),
            (("play", [_key(0)], "smooth", ""), "keys must be a list of 2 to 64 keys"),
            (("play", [_key(float(i)) for i in range(65)], "smooth", ""), "2 to 64 keys"),
            (("play", None, "smooth", ""), "keys must be a list"),
            (("play", [_key(0.5), _key(1.0)], "smooth", ""), "keys[0].t must be 0"),
            (("play", [_key(0), _key(2.0), _key(2.0)], "smooth", ""), "keys[2].t must be greater than keys[1].t"),
            (("play", [_key(0), _key(600.5)], "smooth", ""), "keys[1].t must be at most 600 seconds"),
            (("play", [_key(0), _key(True)], "smooth", ""), "keys[1].t must be a finite number"),
            (("play", [_key(0), _key(float("nan"))], "smooth", ""), "keys[1].t must be a finite number"),
            (("play", [_key(0), _key(1.0, cam_pos=[1.0, 2.0])], "smooth", ""), "keys[1].cam_pos must be a list of 3 numbers from -1e9 to 1e9"),
            (("play", [_key(0), _key(1.0, look_at=[0.0, 0.0, 1.0])], "smooth", ""), "keys[1] must carry exactly one of cam_orientation or look_at"),
            (
                ("play", [_key(0), {"t": 1.0, "cam_pos": [0.0, 0.0, 0.0], "look_at": [0.0, 0.0, 1.0]}], "smooth", ""),
                "keys[1] carries look_at and keys[0] carries cam_orientation",
            ),
            (("play", [_key(0, fov=0.9), _key(1.0)], "smooth", ""), "fov goes on every key or on none"),
            (("play", [_key(0, fov=0.0), _key(1.0, fov=0.0)], "smooth", ""), "keys[0].fov must be a number of radians above 0"),
            (("play", [_key(0), _key(1.0, focus_distance=2.0)], "smooth", ""), "keys[1] has unknown fields ['focus_distance']"),
            (("play", [_key(0), [1.0]], "smooth", ""), "keys[1] must be an object"),
            (("play", _three_keys(), "ease", ""), "easing must be linear or smooth"),
            (("play", _three_keys(), "smooth", "a" * 65), "path_id must be up to 64 characters"),
            (("play", _three_keys(), "smooth", "dolly 1"), "path_id must be up to 64 characters"),
            (("state", _three_keys(), "smooth", ""), "keys travel with mode=play only"),
        )
        for args, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ValueError) as caught:
                    camera_path.normalize_request(*args)
                self.assertTrue(str(caught.exception).startswith("bad_args: "), caught.exception)
                self.assertIn(expected, str(caught.exception))

    def test_ingress_answers_the_fixed_token_only(self) -> None:
        refused = dict(VALID_WIRE_ARGS["camera_path_play"], mode="play", keys=[_key(0)], easing="linear")
        self.assertEqual(camera_path.validate_command_args(refused), (False, "bad_args"))
        self.assertEqual(camera_path.validate_command_args(["play"]), (False, "bad_args"))

    def test_numbers_must_survive_the_bridge_float(self) -> None:
        # The bridge reads every number as a 32-bit float: 1.00000001 is 1.0
        # there, 1e100 is past its range and 1e-46 is 0.
        self.assertEqual(camera_path.float32(1.00000001), 1.0)
        self.assertEqual(camera_path.float32(1e-46), 0.0)
        look = {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0]}
        refused = (
            ([_key(0), _key(1.0), _key(1.00000001)], "keys[2].t must be greater than keys[1].t, also as the 32-bit floats"),
            ([_key(0), _key(1e100)], "keys[1].t must be at most 600 seconds"),
            ([_key(0), _key(10**400)], "keys[1].t must be a finite number"),
            ([_key(0), _key(1.0, cam_pos=[1e100, 2.0, 0.0])], "keys[1].cam_pos must be a list of 3 numbers from -1e9 to 1e9"),
            ([_key(0), _key(1.0, cam_pos=[0.0, -1.5e9, 0.0])], "keys[1].cam_pos must be a list of 3 numbers from -1e9 to 1e9"),
            ([_key(0), _key(1.0, cam_pos=[0.0, 2.0, 10**400])], "keys[1].cam_pos must be a list of 3 numbers from -1e9 to 1e9"),
            ([_key(0), _key(1.0, cam_orientation=[1e100, 0.0, 0.0])], "keys[1].cam_orientation must be a list of 3 numbers from -1e9 to 1e9"),
            ([dict(look), dict(look, t=1.0, look_at=[0.0, 1e100, 9.0])], "keys[1].look_at must be a list of 3 numbers from -1e9 to 1e9"),
            ([_key(0, fov=0.5), _key(1.0, fov=1e100)], "keys[1].fov must be a number of radians above 0, also as a 32-bit float, and at most 1e9"),
            ([_key(0, fov=0.5), _key(1.0, fov=1e-46)], "keys[1].fov must be a number of radians above 0, also as a 32-bit float"),
        )
        for keys, expected in refused:
            with self.subTest(expected=expected, keys=keys):
                with self.assertRaises(ValueError) as caught:
                    camera_path.normalize_request("play", keys, "linear", "")
                self.assertIn(expected, str(caught.exception))
                wire = {"mode": "play", "keys": keys, "easing": "linear", "path_id": ""}
                self.assertEqual(loopback.validate_command_args("camera_path_play", wire), (False, "bad_args"))
        accepted = (
            [_key(0), _key(1.0), _key(1.0000002)],
            [_key(0, cam_pos=[1e9, -1e9, 0.0], cam_orientation=[-1e9, 1e9, 0.0]), _key(1.0)],
            [_key(0, fov=1e9), _key(1.0, fov=1e-40)],
        )
        for keys in accepted:
            with self.subTest(keys=keys):
                args = camera_path.normalize_request("play", keys, "linear", "")
                self.assertEqual(loopback.validate_command_args("camera_path_play", args), (True, None))

    def test_the_bounds_mirror_the_bridge(self) -> None:
        code = _code(_source(CAMERA_PATH_C))

        def constant(name: str) -> str:
            match = re.search(rf"static const \w+ {name} = ([^;]+);", code)
            self.assertIsNotNone(match, name)
            return match.group(1)

        self.assertEqual(int(constant("KEYS_MIN")), camera_path.KEYS_MIN)
        self.assertEqual(int(constant("KEYS_MAX")), camera_path.KEYS_MAX)
        self.assertEqual(float(constant("DURATION_MAX_S")), camera_path.DURATION_MAX_S)
        self.assertEqual(int(constant("PATH_ID_MAX")), camera_path.PATH_ID_MAX_CHARS)
        self.assertEqual(float(constant("MAGNITUDE_MAX")), camera_path.MAGNITUDE_MAX)
        # The bound of entity_wait's world positions, which a 32-bit float holds exactly.
        self.assertEqual(camera_path.MAGNITUDE_MAX, 1e9)
        self.assertEqual(camera_path.float32(camera_path.MAGNITUDE_MAX), camera_path.MAGNITUDE_MAX)
        self.assertIn("if any(not (-1e9 <= v <= 1e9) for v in position_value):", Path(server.__file__).read_text(encoding="utf-8"))
        self.assertEqual(camera_path.MAGNITUDE_TEXT, "from -1e9 to 1e9")
        chars = json.loads(constant("PATH_ID_CHARS"))
        self.assertEqual(len(set(chars)), len(chars))
        for code_point in range(128):
            character = chr(code_point)
            with self.subTest(character=character):
                self.assertEqual(camera_path.is_path_id(character), character in chars)
        self.assertEqual((camera_path.KEYS_MIN, camera_path.KEYS_MAX), (2, 64))
        self.assertEqual(camera_path.DURATION_MAX_S, 600.0)
        self.assertEqual(camera_path.PATH_STATES, ("idle", "playing", "finished", "aborted"))


class _ToolCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app, self.runtime = build_app(
            ServerConfig(key="test-key", port=0, log_sink=lambda _message: None)
        )
        self.tools = {tool.name: tool for tool in self.app._tool_manager.list_tools()}

    async def _forwarded(
        self, tool: str, arguments: dict[str, Any], answer: dict[str, Any] | None = None
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        bridge = AsyncMock(return_value=dict(answer or {"ok": 1}))
        with patch.object(self.runtime, "call_bridge", new=bridge):
            result = _content_json(await self.app.call_tool(tool, arguments))
        bridge.assert_awaited_once()
        return bridge.await_args.args, result

    async def _refused(self, tool: str, arguments: dict[str, Any]) -> str:
        bridge = AsyncMock(return_value={"ok": 1})
        with patch.object(self.runtime, "call_bridge", new=bridge):
            with self.assertRaises(Exception) as caught:
                await self.app.call_tool(tool, arguments)
        bridge.assert_not_awaited()
        _assert_tool_error(self, caught.exception)
        return _tool_error_text(caught.exception, tool)

    async def _assert_waits_for_the_tool_lock(self, tool: str, arguments: dict[str, Any]) -> None:
        """The call reaches the bridge only once the shared runtime.tool_lock is free."""
        held: list[bool] = []

        async def answer(*_args: object) -> dict[str, Any]:
            held.append(self.runtime.tool_lock.locked())
            return {"ok": 1}

        bridge = AsyncMock(side_effect=answer)
        with patch.object(self.runtime, "call_bridge", new=bridge):
            await self.runtime.tool_lock.acquire()
            call = asyncio.ensure_future(self.app.call_tool(tool, arguments))
            try:
                for _ in range(100):
                    await asyncio.sleep(0)
                waited = not call.done() and bridge.await_count == 0
            finally:
                self.runtime.tool_lock.release()
                await call
        self.assertTrue(waited, "the call did not wait for runtime.tool_lock")
        bridge.assert_awaited_once()
        self.assertEqual(held, [True])


class CameraPathToolTest(_ToolCase):
    async def test_every_mode_forwards_its_wire_args_to_the_client(self) -> None:
        args, _result = await self._forwarded(
            "camera_path_play",
            {"mode": "play", "keys": _three_keys(), "easing": "linear", "path_id": "dolly_1", "timeout_s": 2.0},
        )
        self.assertEqual(
            args,
            (
                "camera_path_play",
                {
                    "mode": "play",
                    "keys": [
                        {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                        {"t": 3.0, "cam_pos": [3.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                        {"t": 6.0, "cam_pos": [6.0, 2.0, 0.0], "cam_orientation": [0.0, 0.0, 0.0]},
                    ],
                    "easing": "linear",
                    "path_id": "dolly_1",
                },
                "client",
                2.0,
            ),
        )
        args, _result = await self._forwarded("camera_path_play", {"mode": "play", "keys": _three_keys()})
        self.assertEqual(args[1]["easing"], "smooth")
        self.assertEqual(args[1]["path_id"], "")
        self.assertEqual(args[3], server.DEFAULT_TOOL_TIMEOUT_S)
        args, _result = await self._forwarded("camera_path_play", {"mode": "state"})
        self.assertEqual(args[:3], ("camera_path_play", {"mode": "state", "path_id": ""}, "client"))
        args, _result = await self._forwarded("camera_path_play", {"mode": "stop", "path_id": "dolly_1"})
        self.assertEqual(args[:3], ("camera_path_play", {"mode": "stop", "path_id": "dolly_1"}, "client"))

    async def test_the_path_state_comes_back_as_the_bridge_read_it(self) -> None:
        answer = {
            "id": 7,
            "ok": 1,
            "camera_path": {"state": "playing", "t_s": 1.25, "duration_s": 6.0, "path_id": "dolly_1"},
        }
        _args, result = await self._forwarded("camera_path_play", {"mode": "state"}, answer)
        self.assertEqual(result["camera_path"], answer["camera_path"])

    async def test_bad_requests_never_reach_the_bridge(self) -> None:
        for arguments, expected in (
            ({"mode": "play", "keys": [_key(0)]}, "bad_args: keys must be a list of 2 to 64 keys"),
            ({"mode": "play", "keys": [_key(0), _key(3.0), _key(2.0)]}, "bad_args: keys[2].t must be greater"),
            ({"mode": "play", "keys": [_key(1.0), _key(2.0)]}, "bad_args: keys[0].t must be 0"),
            ({"mode": "play"}, "bad_args: keys must be a list"),
            ({"mode": "play", "keys": _three_keys(), "easing": "ease_in"}, "bad_args: easing must be linear or smooth"),
            ({"mode": "state", "keys": _three_keys()}, "bad_args: keys travel with mode=play only"),
            ({"mode": "stop", "path_id": "x" * 65}, "bad_args: path_id must be up to 64 characters"),
            ({"mode": "rewind"}, "bad_args: mode must be one of play, state, stop"),
            ({"mode": 1}, "valid string"),
            ({"mode": "play", "keys": "dolly"}, "valid list"),
            ({"mode": "state", "path_id": 5}, "valid string"),
            ({"mode": "state", "timeout_s": 0.0}, "bad_timeout"),
        ):
            with self.subTest(arguments=arguments):
                self.assertIn(expected, await self._refused("camera_path_play", arguments))

    async def test_every_mode_waits_for_the_shared_tool_lock(self) -> None:
        for arguments in ({"mode": "play", "keys": _three_keys()}, {"mode": "state"}, {"mode": "stop"}):
            with self.subTest(mode=arguments["mode"]):
                await self._assert_waits_for_the_tool_lock("camera_path_play", arguments)

    def test_the_description_states_the_contract(self) -> None:
        description = self.tools["camera_path_play"].description or ""
        self.assertTrue(description.startswith(server.LEASE_TOOL_LINE))
        for phrase in (
            "2 to 64",
            "at most 600",
            "strictly increasing (also as the 32-bit floats the bridge reads)",
            "fov in radians above 0 (also as a 32-bit float)",
            "every cam_pos, cam_orientation, look_at and fov number from -1e9 to 1e9",
            "camera_create_failed (the staticcamera could not be made; nothing changed)",
            "and so do stop and another camera turned on in the engine",
            "the client bridge bound to the run does not announce camera_path_play",
            "With no client bound, the loopback's binding fence refuses the call first (legacy_unbound",
            "there is no Camera.InterpolateTo",
            "Catmull-Rom",
            "the short way round",
            "camera_path {state, t_s, duration_s, path_id}",
            "idle (no play yet), playing, finished or aborted",
            "Read the pose with camera_get",
            "path_busy",
            "path_not_found",
            "client_not_in_game",
            "camera_create_failed",
            "bridge_capability_missing",
            "left with restore_gameplay",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


def _camera_answer(**overrides: object) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "id": 1,
        "ok": 1,
        "camera": {"ok": 1, "view": "scripted", "applied_mode": "orient", "viewport_moved": 1, "fov": 0.0},
    }
    answer.update(overrides)
    return answer


_ORIENT = {"cam_mode": "orient", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [10.0, 0.0, 0.0]}


class CameraSetFocusTest(_ToolCase):
    async def test_focus_goes_out_on_camera_set_focus_and_is_echoed(self) -> None:
        for label, arguments in (
            ("orient", dict(_ORIENT)),
            ("lookat", {"cam_mode": "lookat", "cam_pos": [1.0, 2.0, 3.0], "look_at": [4.0, 5.0, 6.0]}),
            ("matrix", {"cam_mode": "matrix", "cam_matrix": [1.0] * 12}),
            ("free", {"cam_mode": "free", "cam_pos": [1.0, 2.0, 3.0], "cam_orientation": [0.0, 0.0, 0.0]}),
        ):
            with self.subTest(cam_mode=label):
                args, result = await self._forwarded(
                    "camera_set",
                    dict(arguments, focus_distance=2.0, focus_blur=0.0, timeout_s=1.0),
                    _camera_answer(),
                )
                self.assertEqual(args[0], "camera_set_focus")
                self.assertEqual(args[1]["focus_distance"], 2.0)
                self.assertEqual(args[1]["focus_blur"], 0.0)
                self.assertEqual(loopback.validate_command_args(args[0], args[1]), (True, None))
                self.assertEqual(result["focus_applied"], {"distance": 2.0, "blur": 0.0})
                self.assertNotIn("focus_applied", result["camera"])

    async def test_omitted_focus_stays_camera_set_and_echoes_null(self) -> None:
        args, result = await self._forwarded("camera_set", dict(_ORIENT, fov=1.1), _camera_answer())
        self.assertEqual(args[0], "camera_set")
        self.assertNotIn("focus_distance", args[1])
        self.assertNotIn("focus_blur", args[1])
        self.assertIn("focus_applied", result)
        self.assertIsNone(result["focus_applied"])
        self.assertEqual(result["fov_applied"], 1.1)

    async def test_bad_focus_never_reaches_the_bridge(self) -> None:
        for extra, expected in (
            ({"focus_distance": 2.0}, "bad_args: focus_distance and focus_blur go together"),
            ({"focus_blur": 1.0}, "bad_args: focus_distance and focus_blur go together"),
            ({"focus_distance": 0.0, "focus_blur": 1.0}, "bad_args: focus_distance 0.0 must be a finite number of metres above 0"),
            ({"focus_distance": -2.0, "focus_blur": 1.0}, "bad_args: focus_distance -2.0"),
            ({"focus_distance": math.inf, "focus_blur": 1.0}, "bad_args: focus_distance inf"),
            ({"focus_distance": math.nan, "focus_blur": 1.0}, "bad_args: focus_distance nan"),
            ({"focus_distance": 2.0, "focus_blur": -0.5}, "bad_args: focus_blur -0.5 must be a finite number of 0 or more"),
            ({"focus_distance": 2.0, "focus_blur": math.inf}, "bad_args: focus_blur inf"),
            ({"focus_distance": True, "focus_blur": 1.0}, "valid number"),
            ({"focus_distance": 2.0, "focus_blur": "1"}, "valid number"),
        ):
            with self.subTest(extra=extra):
                self.assertIn(expected, await self._refused("camera_set", dict(_ORIENT, **extra)))

    async def test_a_failed_or_illegible_apply_claims_no_focus(self) -> None:
        bridge = AsyncMock(side_effect=ToolError("camera_create_failed"))
        with patch.object(self.runtime, "call_bridge", new=bridge):
            with self.assertRaises(ToolError) as caught:
                await self.app.call_tool("camera_set", dict(_ORIENT, focus_distance=2.0, focus_blur=1.0))
        self.assertIn("camera_create_failed", str(caught.exception))
        self.assertNotIn("focus_applied", str(caught.exception))
        illegible = _camera_answer(camera={"ok": 0, "view": "", "error": "camera_unavailable_no_scripted_camera"})
        _args, result = await self._forwarded(
            "camera_set", dict(_ORIENT, focus_distance=2.0, focus_blur=1.0), illegible
        )
        self.assertNotIn("focus_applied", result)

    def test_the_description_names_the_echo(self) -> None:
        description = self.tools["camera_set"].description or ""
        for phrase in (
            "focus_distance (metres, finite, above 0) and focus_blur (finite, 0 or more) go together",
            "Camera.SetFocus(focus_distance, focus_blur)",
            "focus_applied {distance, blur}",
            "focus_applied is null",
            "an echo of the values sent, not a read-back",
            "depth-of-field video option, which is not measured",
            "bridge_capability_missing",
            "the client bridge bound to the run does not announce camera_set_focus",
            "with no client bound, the loopback's binding fence refuses it first (legacy_unbound",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


class HudSetToolTest(_ToolCase):
    async def test_visible_is_forwarded_as_a_bool(self) -> None:
        for visible in (False, True):
            with self.subTest(visible=visible):
                args, _result = await self._forwarded("hud_set", {"visible": visible})
                self.assertEqual(args, ("hud_set", {"visible": visible}, "client", server.DEFAULT_TOOL_TIMEOUT_S))
        args, _result = await self._forwarded("hud_set", {"visible": True, "timeout_s": 2.0})
        self.assertEqual(args[3], 2.0)

    async def test_the_flags_come_back_as_the_bridge_read_them(self) -> None:
        answer = {"id": 3, "ok": 1, "hud": {"visible": 0, "hud_hide": 1, "quickbar_hide": 1}}
        _args, result = await self._forwarded("hud_set", {"visible": False}, answer)
        self.assertEqual(result["hud"], answer["hud"])

    async def test_both_full_answers_survive_pruning(self) -> None:
        # The bridge's bools arrive as 0 and 1; a hidden HUD is all of one and
        # none of the other, and pruning must keep it like a shown one.
        for hud in (
            {"visible": 1, "hud_hide": 0, "quickbar_hide": 0},
            {"visible": 0, "hud_hide": 1, "quickbar_hide": 1},
            {"visible": True, "hud_hide": False, "quickbar_hide": False},
            {"visible": False, "hud_hide": True, "quickbar_hide": True},
        ):
            with self.subTest(hud=hud):
                answer = {"id": 4, "ok": 1, "hud": dict(hud)}
                self.assertEqual(result_prune.prune_unfilled_fields("hud_set", json.loads(json.dumps(answer))), answer)
                state = SimpleNamespace(take_result=lambda *_args, **_kwargs: json.loads(json.dumps(answer)))
                with patch.object(self.runtime, "loopback", SimpleNamespace(state=state)):
                    result = await self.runtime.wait_for_result("hud_set", 4, "client", 1.0)
                self.assertEqual(result, answer)

    async def test_the_call_waits_for_the_shared_tool_lock(self) -> None:
        for visible in (False, True):
            with self.subTest(visible=visible):
                await self._assert_waits_for_the_tool_lock("hud_set", {"visible": visible})

    async def test_a_value_that_is_not_a_bool_never_reaches_the_bridge(self) -> None:
        for arguments, expected in (
            ({}, ("\nvisible\n", "Field required")),
            ({"visible": 1}, ("\nvisible\n", "valid boolean")),
            ({"visible": "false"}, ("\nvisible\n", "valid boolean")),
            ({"visible": None}, ("\nvisible\n", "valid boolean")),
            ({"visible": True, "timeout_s": 0.0}, ("bad_timeout",)),
        ):
            with self.subTest(arguments=arguments):
                text = await self._refused("hud_set", arguments)
                for part in expected:
                    self.assertIn(part, text)

    def test_the_description_states_the_contract(self) -> None:
        description = self.tools["hud_set"].description or ""
        self.assertTrue(description.startswith(server.LEASE_TOOL_LINE))
        for phrase in (
            "without touching the controls",
            "HUD_HIDE",
            "QUICKBAR_HIDE",
            "HUD_DISABLE, the options menu's switch, is not touched",
            "neither is the HUD hiding of camera_set's scripted camera",
            "hud {visible, hud_hide, quickbar_hide}",
            "read back with IsContextFlagActive",
            "not an echo",
            "restore_gameplay calls neither setter, so hud_set(visible=true) is what brings the HUD back",
            "client_not_in_game",
            "hud_unavailable",
            "bridge_capability_missing",
            "the client bridge bound to the run does not announce hud_set",
            "With no client bound, the loopback's binding fence refuses the call first (legacy_unbound",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)


class OlderBridgeAdmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = loopback.ServerState("test-key")
        bind_both_peers(self.state)
        self.full = sorted(server._BRIDGE_COMMAND_TOOLS["client"])
        self.older = [name for name in self.full if name not in NEW_COMMANDS]

    def test_with_no_client_bound_the_binding_fence_answers_first(self) -> None:
        # bridge_capability_missing is for a bound client whose census lacks the
        # verb. With no client bound, the fence refuses before the census is
        # read, as it refuses every mutating command (_admit_enqueue_locked).
        unbound = loopback.ServerState("test-key")
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                status, body = unbound.enqueue_command(
                    command, dict(VALID_WIRE_ARGS[command]), peer="client", operation_timeout_s=30.0
                )
                self.assertEqual(status, 409)
                self.assertEqual(body["error"], "legacy_unbound")
        self.assertEqual(unbound._legacy_queues["client"], [])
        self.assertEqual(bound_queue(unbound, "client"), [])

    def _announce(self, names: list[str]) -> tuple[int, dict]:
        return self.state.record_poll(
            "client", "12~1.29", instance=INST_CLIENT, source_pid=PID_CLIENT, caps=",".join(names)
        )

    def _enqueue(self, command: str) -> tuple[int, dict]:
        return self.state.enqueue_command(
            command, dict(VALID_WIRE_ARGS[command]), peer="client", operation_timeout_s=30.0
        )

    def test_an_older_census_refuses_each_verb_before_anything_is_queued(self) -> None:
        self._announce(self.older)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self._enqueue(command), (409, {"error": "bridge_capability_missing"}))
        self.assertEqual(bound_queue(self.state, "client"), [])
        # The tool sees the code itself, not remote_error.
        self.assertEqual(
            _public_enqueue_error({"error": "bridge_capability_missing"}), "bridge_capability_missing"
        )

    def test_the_current_census_admits_them(self) -> None:
        self._announce(self.full)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                status, body = self._enqueue(command)
                self.assertEqual(status, 200, body)
        self.assertEqual([row["cmd"] for row in bound_queue(self.state, "client")], list(NEW_COMMANDS))

    def test_delivery_checks_the_census_again(self) -> None:
        # Each verb is queued for the full census, then the client polls with an
        # older one: none of them is handed over.
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.setUp()
                self._announce(self.full)
                status, body = self._enqueue(command)
                self.assertEqual(status, 200, body)
                status, poll = self._announce(self.older)
                self.assertEqual(status, 200)
                self.assertEqual(poll["commands"], [])
                self.assertEqual(
                    self.state._results[body["id"]],
                    {"id": body["id"], "ok": False, "error": "bridge_capability_missing"},
                )

    def test_action_hold_keeps_its_own_code(self) -> None:
        self._announce([name for name in self.full if name != "action_hold"])
        status, body = self.state.enqueue_command(
            "action_hold",
            {"action": "ActionDeployObject", "selector": "world", "hold_timeout_s": 1.0},
            peer="client",
            operation_timeout_s=30.0,
        )
        self.assertEqual((status, body), (409, {"error": "hold_not_supported"}))

    def test_every_new_verb_needs_a_lease(self) -> None:
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(command_requires_lease(command))
                self.assertNotIn(command, READ_ONLY_COMMANDS)
                self.assertIn(command, loopback.CLIENT_COMMANDS)
                self.assertNotIn(command, loopback.SERVER_COMMANDS)


class CensusTest(unittest.TestCase):
    def test_the_client_announces_and_dispatches_each_command(self) -> None:
        source = _source(BRIDGE)
        announced = announced_caps(source)
        census = dispatch_census(source)
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(command, announced)
                self.assertIn(command, census)

    def test_the_fixture_and_the_readiness_map_name_their_tools(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["peers"]["client"]
        expected = {"camera_path_play": "camera_path_play", "camera_set_focus": "camera_set", "hud_set": "hud_set"}
        for command, tool in expected.items():
            with self.subTest(command=command):
                self.assertEqual(fixture[command], tool)
                self.assertEqual(server._BRIDGE_COMMAND_TOOLS["client"][command], tool)

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        for command in NEW_COMMANDS:
            self.assertNotIn(command, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "4f90284258023d98")
        self.assertIn('const string MCP_BRIDGE_VERSION = "12";', _source(MESSAGES))


class ExecutedCameraPathTest(unittest.TestCase):
    """MCP_CameraPath.c run on fakes: Load, the frame clock and each frame's pose."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.Path = _executed_camera_path(_source(CAMERA_PATH_C))

    def _load(self, keys: list[dict[str, object]], easing: str = "linear", path_id: str = "") -> tuple[object, str]:
        path = self.Path()
        error = path.Load(_enforce_args({"mode": "play", "keys": keys, "easing": easing, "path_id": path_id}))
        return path, error.text

    def _playing(self, keys: list[dict[str, object]], easing: str = "linear", path_id: str = "") -> object:
        path, error = self._load(keys, easing, path_id)
        self.assertEqual(error, "")
        path.Begin()
        return path

    def _pose(self, path: object) -> _Camera:
        camera = _Camera([], "path")
        path.Apply(camera)
        return camera

    def test_linear_moves_between_the_keys_by_the_frame_clock(self) -> None:
        path = self._playing(_three_keys(), path_id="line6")
        self.assertEqual(_report(path), {"state": "playing", "t_s": 0.0, "duration_s": 6.0, "path_id": "line6"})
        self.assertEqual(self._pose(path).position, (0.0, 2.0, 0.0))
        for t, x in ((1.5, 1.5), (3.0, 3.0), (4.5, 4.5)):
            path.Advance(_F32(1.5))
            with self.subTest(t=t):
                self.assertEqual(_report(path)["t_s"], t)
                self.assertEqual(self._pose(path).position, (x, 2.0, 0.0))
        # A timeslice of 0 or below does not move the clock.
        path.Advance(_F32(0.0))
        path.Advance(_F32(-1.0))
        self.assertEqual(_report(path)["t_s"], 4.5)
        # The frame that reaches the last key finishes the path there.
        path.Advance(_F32(5.0))
        self.assertEqual(_report(path), {"state": "finished", "t_s": 6.0, "duration_s": 6.0, "path_id": "line6"})
        self.assertEqual(self._pose(path).position, (6.0, 2.0, 0.0))
        path.Advance(_F32(1.0))
        self.assertEqual(_report(path)["t_s"], 6.0)

    def test_the_clock_is_the_sum_of_the_timeslices(self) -> None:
        path = self._playing(_three_keys())
        for frames in range(1, 5):
            path.Advance(_F32(0.25))
            self.assertEqual(_report(path)["t_s"], 0.25 * frames)
        self.assertEqual(_report(path)["state"], "playing")

    def test_angles_take_the_short_way_round(self) -> None:
        keys = [
            _key(0, cam_orientation=[350.0, 10.0, -170.0]),
            _key(2.0, cam_orientation=[10.0, -10.0, 170.0]),
        ]
        path = self._playing(keys)
        path.Advance(_F32(1.0))
        # 350 to 10 goes through 360 (U6 would pass 180); -170 to 170 through -180.
        self.assertEqual(self._pose(path).orientation, (360.0, 0.0, -180.0))
        path.Advance(_F32(1.0))
        # A finished path holds the last key as given, not unwrapped.
        self.assertEqual(self._pose(path).orientation, (10.0, -10.0, 170.0))

    def test_look_at_is_skipped_only_within_a_millimetre(self) -> None:
        def look(t: float, x: float, offset: float) -> dict[str, object]:
            return {"t": t, "cam_pos": [x, 0.0, 0.0], "look_at": [x + offset, 0.0, 0.0]}

        for offset, looks in ((0.1, True), (0.002, True), (0.0005, False), (0.0, False)):
            with self.subTest(offset=offset):
                camera = self._pose(self._playing([look(0.0, 0.0, offset), look(1.0, 1.0, offset)]))
                self.assertEqual(camera.position, (0.0, 0.0, 0.0))
                self.assertEqual(len(camera.looked_at), 1 if looks else 0)
                if looks:
                    self.assertAlmostEqual(camera.looked_at[0][0], offset, places=6)
                # Under the threshold the frame keeps the orientation it had.
                self.assertIsNone(camera.orientation)

    def test_smooth_is_catmull_rom_by_time(self) -> None:
        keys = [_key(0, cam_pos=[0.0, 0.0, 0.0]), _key(1.0, cam_pos=[2.0, 0.0, 0.0]), _key(4.0, cam_pos=[2.0, 0.0, 0.0])]
        path = self._playing(keys, easing="smooth")
        self.assertEqual(self._pose(path).position, (0.0, 0.0, 0.0))
        path.Advance(_F32(1.0))
        self.assertEqual(self._pose(path).position, (2.0, 0.0, 0.0))
        path.Advance(_F32(1.5))
        # Segment 1 to 4 at s = 0.5: tangents 0.5 and 0 per second, times h = 3.
        self.assertEqual(self._pose(path).position, (2.1875, 0.0, 0.0))

    def test_fov_is_linear_when_the_keys_carry_one(self) -> None:
        path = self._playing([_key(0, fov=1.0), _key(2.0, fov=0.5)])
        path.Advance(_F32(1.0))
        self.assertEqual(self._pose(path).fov, 0.75)
        self.assertIsNone(self._pose(self._playing(_three_keys())).fov)

    def test_a_short_segment_cannot_overflow_the_smooth_curve(self) -> None:
        # 1e-45 s is the smallest gap a 32-bit float keeps above 0, and both
        # sides accept it. A rate across it is past the float range; h times it is not.
        keys = [
            _key(0, cam_pos=[0.0, 0.0, 0.0]),
            _key(1e-45, cam_pos=[1e9, 0.0, 0.0]),
            _key(1.0, cam_pos=[-1e9, 0.0, 0.0]),
        ]
        camera_path.normalize_request("play", keys, "smooth", "")
        path = self._playing(keys, easing="smooth")
        for timeslice in (0.0, 0.5, 0.25):
            path.Advance(_F32(timeslice))
            with self.subTest(t=_report(path)["t_s"]):
                self.assertTrue(all(math.isfinite(value) for value in self._pose(path).position))

    def test_stop_keeps_the_time_and_a_refused_load_keeps_the_path(self) -> None:
        path = self._playing(_three_keys(), path_id="kept")
        path.Advance(_F32(2.0))
        path.Abort()
        path.Advance(_F32(2.0))
        self.assertEqual(_report(path), {"state": "aborted", "t_s": 2.0, "duration_s": 6.0, "path_id": "kept"})
        self.assertEqual(path.Load(_enforce_args({"mode": "play", "keys": [_key(0)], "easing": "linear", "path_id": "x"})).text, "bad_args")
        self.assertEqual(_report(path), {"state": "aborted", "t_s": 2.0, "duration_s": 6.0, "path_id": "kept"})
        self.assertTrue(path.HasPathId(_Text.of("kept")))
        self.assertFalse(self.Path().HasPathId(_Text.of("")))

    def test_the_bridge_load_refuses_what_the_contract_refuses(self) -> None:
        # The keys reach Load as 32-bit floats (an overflow an infinity). The
        # zero FOV read as absent is a known difference and is left out here.
        look = {"t": 0.0, "cam_pos": [0.0, 2.0, 0.0], "look_at": [0.0, 1.0, 9.0]}
        cases = {
            "three_keys": _three_keys(),
            "two_keys_at_600": [_key(0), _key(600.0)],
            "t_distinct_as_float32": [_key(0), _key(1.0), _key(1.0000002)],
            "t_gap_of_1e-45": [_key(0), _key(1e-45), _key(1.0)],
            "magnitude_at_the_bound": [_key(0, cam_pos=[1e9, -1e9, 0.0], cam_orientation=[-1e9, 1e9, 0.0]), _key(1.0)],
            "fov_on_every_key": [_key(0, fov=1e9), _key(1.0, fov=1e-40)],
            "look_at": [dict(look), dict(look, t=1.0)],
            "one_key": [_key(0)],
            "first_t_not_zero": [_key(0.5), _key(1.0)],
            "t_collapses_as_float32": [_key(0), _key(1.0), _key(1.00000001)],
            "t_past_600": [_key(0), _key(600.5)],
            "t_huge_int": [_key(0), _key(10**400)],
            "cam_pos_past_the_bound": [_key(0, cam_pos=[1.5e9, 0.0, 0.0]), _key(1.0)],
            "cam_pos_1e100": [_key(0), _key(1.0, cam_pos=[1e100, 2.0, 0.0])],
            "orientation_1e100": [_key(0), _key(1.0, cam_orientation=[0.0, 1e100, 0.0])],
            "look_at_1e100": [dict(look), dict(look, t=1.0, look_at=[0.0, 0.0, 1e100])],
            "fov_1e100": [_key(0, fov=0.5), _key(1.0, fov=1e100)],
            "fov_collapses_on_one_key": [_key(0, fov=0.5), _key(1.0, fov=1e-46)],
            "fov_on_one_key": [_key(0, fov=0.5), _key(1.0)],
            "both_aims": [_key(0), _key(1.0, look_at=[0.0, 0.0, 9.0])],
        }
        accepted = set()
        for name, keys in cases.items():
            with self.subTest(case=name):
                try:
                    camera_path.normalize_request("play", keys, "linear", "")
                    contract = True
                except ValueError:
                    contract = False
                _path, error = self._load(keys)
                self.assertEqual(error == "", contract, error)
                if contract:
                    accepted.add(name)
        self.assertEqual(
            accepted,
            {"three_keys", "two_keys_at_600", "t_distinct_as_float32", "t_gap_of_1e-45", "magnitude_at_the_bound", "fov_on_every_key", "look_at"},
        )


class ExecutedPlayJobTest(unittest.TestCase):
    """ProcessCameraPathPlayJob and TickCameraPath of MCPClientBridge.c run on fakes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_source = _source(BRIDGE)
        cls.Path = _executed_camera_path(_source(CAMERA_PATH_C))

    def setUp(self) -> None:
        self.client = _ClientBridge(self.bridge_source, self.Path)

    @staticmethod
    def _wire(path_id: str = "line6") -> dict[str, Any]:
        return {"mode": "play", "keys": _three_keys(), "easing": "linear", "path_id": path_id}

    def _after_camera_set_free(self) -> None:
        # What ApplyFreeCamera leaves: the free camera on, not owned, and the
        # player simulation off.
        self.client.free_cam.active = True
        self.client.m_ActiveCam = self.client.free_cam
        self.client.m_ActiveCamOwned = False
        self.client.m_PlayerSimulationDisabled = True

    def _new_camera(self) -> _Camera:
        camera = _Camera(self.client.log, "path")
        self.client.next_object = camera
        return camera

    def _played(self) -> _Camera:
        camera = self._new_camera()
        job = self.client.play(self._wire())
        self.assertEqual((job.done, job.error.text), (True, ""))
        return camera

    def test_a_play_installs_the_loaded_path_on_a_new_camera(self) -> None:
        camera = self._new_camera()
        job = self.client.play(self._wire())
        self.assertEqual((job.done, job.error.text), (True, ""))
        self.assertEqual(
            _report(self.client.m_CameraPath),
            {"state": "playing", "t_s": 0.0, "duration_s": 6.0, "path_id": "line6"},
        )
        self.assertIs(self.client.m_CameraPathCam, camera)
        self.assertIs(self.client.m_ActiveCam, camera)
        self.assertTrue(self.client.m_ActiveCamOwned)
        self.assertTrue(camera.active)
        self.assertEqual((camera.position, camera.orientation), ((0.0, 2.0, 0.0), (0.0, 0.0, 0.0)))
        create = ("engine", "CreateObject", "staticcamera", (0.0, 2.0, 0.0), True)
        order = [create, ("bridge", "FinishCameraHandoff"), ("bridge", "SuppressGameplay"), ("path", "SetActive", True)]
        self.assertEqual([self.client.index(entry) for entry in order], sorted(self.client.index(entry) for entry in order))
        # From the player view nothing touches the free camera or the simulation.
        for entry in self.client.log:
            self.assertNotIn(entry[0], ("free", "player"))
        self.assertNotIn(("bridge", "RestoreGameplay"), self.client.log)

    def test_a_play_from_camera_set_free_leaves_only_its_camera_on(self) -> None:
        self._after_camera_set_free()
        camera = self._played()
        self.assertFalse(self.client.free_cam.active)
        self.assertTrue(camera.active)
        self.assertLess(self.client.index(("free", "SetActive", False)), self.client.index(("path", "SetActive", True)))
        self.assertIn(("player", "DisableSimulation", False), self.client.log)
        self.assertFalse(self.client.m_PlayerSimulationDisabled)
        self.assertIs(self.client.m_ActiveCam, camera)
        self.assertTrue(self.client.m_ActiveCamOwned)
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "playing")

    def test_a_failed_create_leaves_everything_as_it_was(self) -> None:
        old = self.Path()
        self.assertEqual(old.Load(_enforce_args(self._wire("old"))).text, "")
        old.Begin()
        old.Advance(_F32(9.0))
        before = _report(old)
        self.assertEqual(before["state"], "finished")
        for made in (None, object()):
            for start in ("free", "static"):
                with self.subTest(made=made, start=start):
                    self.setUp()
                    if start == "free":
                        self._after_camera_set_free()
                        on_screen = self.client.free_cam
                    else:
                        on_screen = _Camera(self.client.log, "static")
                        on_screen.active = True
                        self.client.m_ActiveCam = on_screen
                        self.client.m_ActiveCamOwned = True
                    self.client.m_CameraPath = old
                    self.client.next_object = made
                    job = self.client.play(self._wire())
                    self.assertEqual((job.done, job.error.text), (True, "camera_create_failed"))
                    # Only the made object goes; nothing else is called.
                    expected = [("engine", "CreateObject", "staticcamera", (0.0, 2.0, 0.0), True)]
                    if made is not None:
                        expected.append(("engine", "ObjectDelete", made))
                    self.assertEqual(self.client.log, expected)
                    self.assertTrue(on_screen.active)
                    self.assertIs(self.client.m_ActiveCam, on_screen)
                    self.assertEqual(self.client.m_ActiveCamOwned, start == "static")
                    self.assertEqual(self.client.m_PlayerSimulationDisabled, start == "free")
                    self.assertIsNone(self.client.m_CameraPathCam)
                    self.assertIs(self.client.m_CameraPath, old)
                    self.assertEqual(_report(old), before)

    def test_a_refused_play_changes_nothing(self) -> None:
        job = self.client.play({"mode": "play", "keys": [_key(0)], "easing": "linear", "path_id": ""})
        self.assertEqual((job.done, job.error.text), (True, "bad_args"))
        self._played()
        self.client.log.clear()
        job = self.client.play(self._wire("second"))
        self.assertEqual((job.done, job.error.text), (True, "path_busy"))
        self.assertEqual(self.client.log, [])
        self.assertEqual(_report(self.client.m_CameraPath)["path_id"], "line6")

    def test_each_frame_moves_the_path_while_its_camera_is_on(self) -> None:
        camera = self._played()
        self.client.TickCameraPath(_F32(0.5))
        self.client.TickCameraPath(_F32(0.5))
        self.assertEqual(_report(self.client.m_CameraPath)["t_s"], 1.0)
        self.assertEqual(camera.position, (1.0, 2.0, 0.0))

    def test_a_camera_turned_on_from_outside_aborts_the_path(self) -> None:
        camera = self._played()
        self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(camera.position, (1.0, 2.0, 0.0))
        # Another camera takes the screen without the bridge knowing (vanilla's
        # developer free camera): m_ActiveCam is still the path's camera, but
        # the engine no longer reports it active.
        camera.active = False
        self.assertIs(self.client.m_ActiveCam, camera)
        frames = len(self.client.log)
        for _ in range(2):
            self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(self.client.log[frames:], [])
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
        self.assertEqual(_report(self.client.m_CameraPath)["t_s"], 1.0)
        self.assertIsNone(self.client.m_CameraPathCam)

    def test_a_camera_set_or_a_release_aborts_the_path(self) -> None:
        for replaced_by in ("camera_set", "release"):
            with self.subTest(replaced_by=replaced_by):
                self.setUp()
                self._played()
                self.client.m_ActiveCam = _Camera(self.client.log, "other") if replaced_by == "camera_set" else None
                frames = len(self.client.log)
                self.client.TickCameraPath(_F32(1.0))
                self.assertEqual(self.client.log[frames:], [])
                self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")


class EnforceWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = _source(BRIDGE)
        cls.messages = _source(MESSAGES)
        cls.path = _source(CAMERA_PATH_C)

    def test_the_wire_classes(self) -> None:
        self.assertEqual(
            _members(_class_body(self.messages, "MCPPathKey")),
            [
                ("float", "t"),
                ("ref array<float>", "cam_pos"),
                ("ref array<float>", "cam_orientation"),
                ("ref array<float>", "look_at"),
                ("float", "fov"),
            ],
        )
        self.assertEqual(
            _members(_class_body(self.messages, "MCPCameraPathState")),
            [("string", "state"), ("float", "t_s"), ("float", "duration_s"), ("string", "path_id")],
        )
        self.assertEqual(
            _members(_class_body(self.messages, "MCPHudState")),
            [("bool", "visible"), ("bool", "hud_hide"), ("bool", "quickbar_hide")],
        )
        args = _members(_class_body(self.messages, "MCPArgs"))
        for member in (
            ("ref array<ref MCPPathKey>", "keys"),
            ("string", "easing"),
            ("string", "path_id"),
            ("float", "focus_distance"),
            ("float", "focus_blur"),
            ("bool", "visible"),
        ):
            self.assertIn(member, args)
        result = _members(_class_body(self.messages, "MCPResult"))
        self.assertEqual(result[-2:], [("ref MCPCameraPathState", "camera_path"), ("ref MCPHudState", "hud")])
        self.assertIn(("bool", "camera_focus"), _members(_class_body(self.messages, "MCPJob")))

    def test_the_blocks_are_prunable_and_kept_when_filled(self) -> None:
        self.assertEqual(result_prune.PRUNABLE_FIELDS[-2:], ("camera_path", "hud"))
        path = {"state": "idle", "t_s": 0.0, "duration_s": 0.0, "path_id": ""}
        kept = result_prune.prune_unfilled_fields("camera_path_play", {"ok": 1, "camera_path": dict(path), "hud": {}})
        self.assertEqual(kept["camera_path"], path)
        self.assertNotIn("hud", kept)
        hud = {"visible": 1, "hud_hide": 0, "quickbar_hide": 0}
        kept = result_prune.prune_unfilled_fields("hud_set", {"ok": 1, "hud": dict(hud), "camera_path": {}})
        self.assertEqual(kept["hud"], hud)
        self.assertNotIn("camera_path", kept)

    def test_a_play_is_checked_before_anything_moves(self) -> None:
        body = _flat(_method_body(self.bridge, "protected bool DispatchCameraPathPlay("))
        order = [
            "error = candidate.Load(command.args);",
            'result.error = "path_busy";',
            'result.error = "busy";',
            "m_JobRunner.AddJob(job);",
        ]
        positions = [body.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        query = [
            "if (!MCPCameraPath.IsValidPathId(command.args.path_id))",
            'result.error = "path_not_found";',
            "StopCameraPath();",
            "m_CameraPath.Fill(result.camera_path);",
        ]
        positions = [body.index(step) for step in query]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('if (mode != "state" && mode != "stop")', body)

    def test_the_path_moves_per_frame_on_the_scripted_camera(self) -> None:
        # What the frame and the play do runs in ExecutedPlayJobTest; here, that
        # OnTick moves the path before the job runner and that nothing
        # interpolates in the engine or blocks the tick.
        tick = _flat(_method_body(self.bridge, "void OnTick(float timeslice)"))
        self.assertLess(tick.index("TickCameraPath(timeslice);"), tick.index("m_JobRunner.Tick(timeslice, this);"))
        frame = _flat(_method_body(self.bridge, "protected void TickCameraPath(float timeslice)"))
        play = _flat(_method_body(self.bridge, "protected bool ProcessCameraPathPlayJob(MCPJob job)"))
        self.assertIn('else if (job.kind == "camera_path_play") { return ProcessCameraPathPlayJob(job); }', _flat(self.bridge))
        for text in (_code(self.path), play, frame):
            self.assertNotIn("InterpolateTo", text)
            self.assertNotIn("_now(", text)

    def test_camera_set_and_restore_end_a_playing_path(self) -> None:
        apply = _flat(_method_body(self.bridge, "protected bool ApplyCameraSet(MCPJob job)"))
        self.assertLess(apply.index("StopCameraPath();"), apply.index("SuppressGameplay();"))
        restore = _flat(_method_body(self.bridge, "protected void RestoreGameplay()"))
        self.assertLess(restore.index("StopCameraPath();"), restore.index("if (!GetGame())"))
        stop = _flat(_method_body(self.bridge, "protected void StopCameraPath()"))
        self.assertEqual(stop, "if (m_CameraPath) { m_CameraPath.Abort(); } m_CameraPathCam = null;")
        abort = _flat(_method_body(self.path, "void Abort()"))
        self.assertEqual(abort, 'if (m_State == "playing") { m_State = "aborted"; }')

    def test_focus_is_set_only_for_camera_set_focus(self) -> None:
        dispatch = _flat(_method_body(self.bridge, "protected bool DispatchCameraSet("))
        self.assertIn('bool focus = command.cmd == "camera_set_focus";', dispatch)
        self.assertLess(dispatch.index("!IsValidCameraFocus(command.args)"), dispatch.index("HasExclusiveJob()"))
        self.assertIn("job.camera_focus = focus;", dispatch)
        for signature, camera in (
            ("protected bool ApplyCameraSet(MCPJob job)", "cam"),
            ("protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)", "freeCam"),
        ):
            with self.subTest(apply=signature):
                body = _flat(_method_body(self.bridge, signature))
                self.assertIn(
                    f"if (job.camera_focus) {{ {camera}.SetFocus(job.args.focus_distance, job.args.focus_blur); }}",
                    body,
                )
                self.assertEqual(body.count("SetFocus("), 1)
        check = _flat(_method_body(self.bridge, "protected bool IsValidCameraFocus(MCPArgs args)"))
        self.assertIn("if (distance != distance || blur != blur)", check)
        self.assertIn("if (distance <= 0.0 || distance >= float.MAX)", check)
        self.assertIn("if (blur < 0.0 || blur >= float.MAX)", check)

    def test_hud_set_uses_the_hide_key_flags_and_reads_them_back(self) -> None:
        body = _flat(_method_body(self.bridge, "protected bool DispatchHudSet(MCPCommand command, MCPResult result)"))
        self.assertLess(body.index("hud.ShowHudPlayer(command.args.visible);"), body.index("state.hud_hide ="))
        self.assertIn("hud.ShowQuickbarPlayer(command.args.visible);", body)
        self.assertIn("state.hud_hide = visibility.IsContextFlagActive(EHudContextFlags.HUD_HIDE);", body)
        self.assertIn(
            "state.quickbar_hide = visibility.IsContextFlagActive(EHudContextFlags.QUICKBAR_HIDE);", body
        )
        self.assertIn("state.visible = !state.hud_hide && !state.quickbar_hide;", body)
        self.assertIn('result.error = "hud_unavailable";', body)
        for absent in ("HUD_DISABLE", ".Show(", "PlayerControl", "SetContextFlag"):
            self.assertNotIn(absent, body)

    def test_a_client_out_of_game_is_refused_before_any_handler(self) -> None:
        dispatch = _method_body(self.bridge, "protected void Dispatch(MCPCommand command)")
        gate = dispatch.index('result.error = "client_not_in_game";')
        for command in NEW_COMMANDS:
            with self.subTest(command=command):
                self.assertLess(gate, dispatch.index(f'if (command.cmd == "{command}")'))


if __name__ == "__main__":
    unittest.main()
