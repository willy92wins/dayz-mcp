"""camera_path_play (d026), camera_set focus (702f) and hud_set (f427).

Contract, tool, admission and census checks run here. MCP_CameraPath.c and the
camera_path_play, camera_path_release, camera_set and hud_set dispatch, the
camera_set job, CameraReadError, ApplyCameraSet, ApplyFreeCamera and the frame
method of MCPClientBridge.c also run here, translated from the .c source by
tests.enforce_subset_helpers (see _executed_camera_path below); that runs their
branching and arithmetic, not the engine. The other asserts on Enforce text are
wiring only. Neither credits in-game behavior, which the PR lists for the final
round.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import struct
import unittest
from itertools import permutations
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock, patch

from dayz_mcp import bridge_readiness, camera_path, loopback, result_prune, server
from dayz_mcp.bridge_errors import _public_enqueue_error
from dayz_mcp.server import ServerConfig, ToolError, build_app
from dayz_mcp.session_coordination import (
    READ_ONLY_COMMANDS,
    ClientIdentity,
    SessionCoordinator,
    command_requires_lease,
)
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
# The client census of this PR's round 2 (409803f, MCPClientBridge.c:474):
# camera_path_play without camera_path_release, the command that stops it.
ROUND_TWO_CENSUS = (
    "action_cursor,action_hold,action_hold_cancel,action_use,action_use_component,action_use_door,"
    "action_use_target,anim_timeline,camera_get,camera_path_play,camera_set,camera_set_focus,engine_set,"
    "hud_set,input_describe,input_trigger,key_press,player_look_at,player_look_at_release,player_move,"
    "player_respawn,player_trace,restore_gameplay,ui_click,ui_dialog,ui_focus,ui_reload_layout,"
    "ui_set_text,ui_tree,vehicle_control,vehicle_get_in_client,vehicle_release,vehicle_telemetry,"
    "vehicle_trace,weapon_aim,weapon_fire,weapon_raise,weapon_sights"
).split(",")


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
    for kind, name, value in _CONSTANT.findall(method_body(source, "class MCPCameraPath\n")):
        if kind == "int":
            found[name] = int(value)
        elif kind == "float":
            found[name] = _F32(float(value))
        else:
            found[name] = _Text.of(json.loads(value))
    return found


def _round_half_away(value: float) -> "_F32":
    """Math.Round: to the nearest whole number, a half away from 0, as a float.

    Enforce's Math.Round returns a float, so what it returns is a 32-bit float
    too: 360.0 times it then rounds as the bridge's float does.
    """
    return _F32(math.copysign(math.floor(abs(value) + 0.5), value))


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
    statics: dict[str, Any] = {}
    for signature in _PATH_STATICS:
        head, code = _function(source, signature)
        function = translate(code, head, namespace, class_types=_PATH_LOCALS)
        statics[function.__name__] = staticmethod(function)
    # Also on the class, for a bridge that calls MCPCameraPath.IsValidPathId.
    path_class = type("ExecutedCameraPath", (), statics)
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


def _state_block() -> SimpleNamespace:
    """An empty MCPCameraPathState."""
    return SimpleNamespace(state=None, t_s=None, duration_s=None, path_id=None)


def _block(report: SimpleNamespace) -> dict[str, Any]:
    """A filled MCPCameraPathState as the wire carries it."""
    block = {
        "state": report.state.text,
        "t_s": float(report.t_s),
        "duration_s": float(report.duration_s),
        "path_id": report.path_id.text,
    }
    for name in ("command_id", "replaced_command_id"):
        if hasattr(report, name):
            block[name] = getattr(report, name)
    return block


def _report(path: object) -> dict[str, Any]:
    """What Fill puts in the camera_path block."""
    report = _state_block()
    path.Fill(report)  # type: ignore[attr-defined]
    return _block(report)


class _Camera:
    """A Camera fake: the pose calls a path or an apply makes, SetActive and IsActive.

    IsActive reads what the last SetActive set (or `active`), at once. With
    `shows_after` set, a number of OnTicks, it reads the state from before the
    last SetActive until the bridge's m_Tick (read from `tick`) is shows_after
    past the m_Tick that SetActive was made at; math.inf never shows it.
    """

    def __init__(
        self, log: list[tuple[Any, ...]], name: str, tick: Callable[[], int] | None = None
    ) -> None:
        self.log = log
        self.name = name
        self.active = False
        self.position: tuple[float, ...] | None = None
        self.orientation: tuple[float, ...] | None = None
        self.looked_at: list[tuple[float, ...]] = []
        self.fov: float | None = None
        self.tick = tick
        self.shows_after: float | None = None
        self._before = False
        self._set_at: int | None = None

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

    def SetFocus(self, distance: float, blur: float) -> None:
        self.log.append((self.name, "SetFocus", float(distance), float(blur)))

    def SetTransform(self, matrix: list[tuple[float, ...]]) -> None:
        self.log.append((self.name, "SetTransform", tuple(tuple(float(v) for v in row) for row in matrix)))

    def SetActive(self, active: bool) -> None:
        if self.shows_after is not None:
            self._before = self.IsActive()
            self._set_at = self.tick()  # type: ignore[misc]
        self.active = active
        self.log.append((self.name, "SetActive", active))

    def IsActive(self) -> bool:
        if (
            self.shows_after is not None
            and self._set_at is not None
            and self.tick() < self._set_at + self.shows_after  # type: ignore[misc]
        ):
            return self._before
        return self.active


_BRIDGE_METHODS = (
    "protected bool DispatchCameraPathPlay(MCPCommand command, MCPResult result)",
    "protected bool DispatchCameraPathRelease(MCPCommand command, MCPResult result)",
    "protected void TickCameraPath(float timeslice)",
    "protected void StopCameraPath()",
    "protected void DeleteOwnedCamera()",
)
_BRIDGE_SIBLINGS = (
    "FinishCameraHandoff|SuppressGameplay|DeleteOwnedCamera|RestoreGameplay|StopCameraPath|"
    "HasExclusiveJob|ReleaseCamera|PostResult|Log"
)


class _ClientBridge:
    """The members the path methods use; the rest of the bridge is a log.

    DispatchCameraPathPlay, DispatchCameraPathRelease, TickCameraPath,
    StopCameraPath and DeleteOwnedCamera are the translated ones.
    TickCameraPath runs as OnTick calls it, after m_Tick goes up by one; a
    dispatch reads m_Tick as it stands, as Dispatch does from the poll
    callback or from DrainPending. HasExclusiveJob answers `exclusive`; it,
    FinishCameraHandoff, SuppressGameplay and RestoreGameplay only log their
    call, here with no handoff pending. The engine is a log of calls on fake
    objects, and camera() makes a camera that reads this m_Tick. There is no
    job runner: a dispatch that queued a job would raise.
    """

    def __init__(self, bridge_source: str, path_class: type) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.m_Tick = 40
        self.free_cam = self.camera("free")
        self.next_object: object = None
        self.exclusive = False
        self.player = SimpleNamespace(
            DisableSimulation=lambda off: self.log.append(("player", "DisableSimulation", off))
        )
        self.m_CameraPath = path_class()
        self.m_CameraPathCam: _Camera | None = None
        self.m_CameraPathCommandId = 0
        self.m_CameraPathPlayTick = 0
        self.m_ActiveCam: _Camera | None = None
        self.m_ActiveCamOwned = False
        self.m_PlayerSimulationDisabled = False
        namespace: dict[str, Any] = {
            "_S": _Text,
            "MCPCameraPath": path_class,
            "MCPCameraPathState": _state_block,
            "MCPResult": lambda: SimpleNamespace(),
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
                class_types={"MCPCameraPath", "MCPCameraPathState", "MCPResult", "Object", "Camera", "FreeDebugCamera", "PlayerBase"},
            )
            if method.__name__ == "TickCameraPath":
                self._tick_camera_path = MethodType(method, self)
            else:
                setattr(self, method.__name__, MethodType(method, self))

    def camera(self, name: str) -> _Camera:
        return _Camera(self.log, name, tick=lambda: self.m_Tick)

    def TickCameraPath(self, timeslice: _F32) -> None:
        """One OnTick's TickCameraPath: m_Tick first goes up by one, as OnTick does."""
        self.m_Tick += 1
        self._tick_camera_path(timeslice)

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

    def ReleaseCamera(self) -> None:
        self.log.append(("bridge", "ReleaseCamera"))

    def PostResult(self, result: SimpleNamespace) -> None:
        self.terminal_result = {"id": result.id, "ok": result.ok, "camera_path": _block(result.camera_path)}

    def HasExclusiveJob(self) -> bool:
        self.log.append(("bridge", "HasExclusiveJob"))
        return self.exclusive

    def Log(self, line: _Text) -> None:
        self.log.append(("bridge", "Log", line.text))

    def play(self, wire: dict[str, Any], command_id: int = 7) -> SimpleNamespace:
        """One camera_path_play through DispatchCameraPathPlay, and its answer."""
        return self._dispatch("DispatchCameraPathPlay", command_id, _enforce_args(wire))

    def release(self, play_id: int, command_id: int = 50) -> SimpleNamespace:
        """One camera_path_release naming play_id, and its answer."""
        return self._dispatch("DispatchCameraPathRelease", command_id, SimpleNamespace(command_id=play_id))

    def _dispatch(self, method: str, command_id: int, args: SimpleNamespace) -> SimpleNamespace:
        command = SimpleNamespace(id=command_id, args=args)
        result = SimpleNamespace(ok=False, error=_Text.of(""), camera_path=None, matched=False)
        post_now = getattr(self, method)(command, result)
        # done is the dispatch's postNow: true when the answer leaves with it.
        return SimpleNamespace(
            done=post_now, ok=result.ok, error=result.error, camera_path=result.camera_path, matched=result.matched
        )

    def index(self, entry: tuple[Any, ...]) -> int:
        return self.log.index(entry)


_CAMERA_SET_METHODS = (
    "protected bool DispatchCameraSet(MCPCommand command, MCPResult result)",
    "protected bool ProcessCameraSetJob(MCPJob job)",
    "protected string CameraReadError()",
)
_CAMERA_SET_SIBLINGS = (
    "ValidateCameraArgs|IsValidCameraFocus|HasExclusiveJob|ApplyCameraSet|RestoreGameplay|"
    "ResolveSettleSeconds|CameraReadError|ResolveLiveSeatedTransport|IsClientInGame|Log"
)
_BRIDGE_CONSTANT = re.compile(r"protected const (int|float) (CAMERA_\w+) = ([^;]+);")


def _bridge_constants(source: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for kind, name, value in _BRIDGE_CONSTANT.findall(_code(source)):
        found[name] = int(value) if kind == "int" else _F32(float(value))
    return found


def _new_job() -> SimpleNamespace:
    """An MCPJob with the fields camera_set reads and writes, at their defaults."""
    return SimpleNamespace(
        id=0,
        kind=_Text.of(""),
        args=None,
        camera_focus=False,
        phase=0,
        sample_s_target=_F32(0.0),
        sample_start_s=_F32(0.0),
        deadline_s=_F32(0.0),
        error=_Text.of(""),
        tick_poll_sent=0,
        tick_poll_callback=0,
        tick_dispatch=0,
    )


class _CameraSetBridge:
    """DispatchCameraSet, ProcessCameraSetJob and CameraReadError translated;
    what they call is a log.

    ApplyCameraSet stands for the whole apply (path stop, SuppressGameplay, the
    camera, SetFOV, SetFocus; ExecutedApplyTest runs the real one) and logs
    whether it was asked for focus; it answers `apply_ok` and, when it
    applies, turns m_ActiveCam on, as every apply does last. m_ActiveCam is a
    fake camera on the same log that reads this m_Tick, so a camera call from
    the job would show. The player is in game, on foot and has no parent. The
    job runner keeps the queued jobs and answers `elapsed`; tick() is one
    OnTick's pass of the job runner: m_Tick and the elapsed time go up, then
    the job is processed. The dispatch reads m_Tick as Dispatch does.
    """

    def __init__(self, bridge_source: str) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.apply_ok = True
        self.exclusive = False
        self.elapsed = _F32(10.0)
        self.m_Tick = 3
        self.jobs: list[SimpleNamespace] = []
        self.m_ActiveCam = _Camera(self.log, "scripted", tick=lambda: self.m_Tick)
        self.m_JobRunner = SimpleNamespace(GetElapsedS=lambda: self.elapsed, AddJob=self._add_job)
        self.constants = _bridge_constants(bridge_source)
        player = SimpleNamespace(GetParent=lambda: None)
        namespace: dict[str, Any] = {
            "_S": _Text,
            "MCPJob": _new_job,
            "PlayerBase": SimpleNamespace(Cast=lambda thing: thing),
            "GetGame": lambda: SimpleNamespace(GetPlayer=lambda: player),
            **self.constants,
        }
        for signature in _CAMERA_SET_METHODS:
            head, code = _function(
                bridge_source, signature, this_type="MCPClientBridge", methods=_CAMERA_SET_SIBLINGS
            )
            method = translate(
                code, head, namespace, class_types={"MCPCameraValidation", "MCPJob", "PlayerBase"}
            )
            setattr(self, method.__name__, MethodType(method, self))

    def _add_job(self, job: SimpleNamespace) -> None:
        self.log.append(("runner", "AddJob", job.phase))
        self.jobs.append(job)

    def ValidateCameraArgs(self, args: object) -> SimpleNamespace:
        return SimpleNamespace(ok=True, error=_Text.of(""))

    def IsValidCameraFocus(self, args: object) -> bool:
        return True

    def HasExclusiveJob(self) -> bool:
        self.log.append(("bridge", "HasExclusiveJob"))
        return self.exclusive

    def ApplyCameraSet(self, job: SimpleNamespace) -> bool:
        self.log.append(("bridge", "ApplyCameraSet", job.camera_focus))
        if not self.apply_ok:
            job.error = _Text.of("camera_create_failed")
            return False
        self.m_ActiveCam.SetActive(True)
        return True

    def RestoreGameplay(self) -> None:
        self.log.append(("bridge", "RestoreGameplay"))

    def ResolveSettleSeconds(self, args: object) -> _F32:
        return _F32(0.15)

    def IsClientInGame(self) -> bool:
        return True

    def ResolveLiveSeatedTransport(self, player: object) -> None:
        return None

    def Log(self, line: _Text) -> None:
        self.log.append(("bridge", "Log", line.text))

    def dispatch(self, cmd: str, command_id: int = 21) -> tuple[bool, SimpleNamespace]:
        args = SimpleNamespace(focus_distance=_F32(2.0), focus_blur=_F32(1.0))
        command = SimpleNamespace(id=command_id, cmd=_Text.of(cmd), args=args)
        result = SimpleNamespace(
            ok=False, error=_Text.of(""), tick_poll_sent=1, tick_poll_callback=2, tick_dispatch=self.m_Tick
        )
        return self.DispatchCameraSet(command, result), result  # type: ignore[attr-defined]

    def tick(self, elapsed: float) -> bool:
        """One OnTick's job runner pass over the one queued job."""
        self.m_Tick += 1
        self.elapsed = _F32(elapsed)
        [job] = self.jobs
        return self.ProcessCameraSetJob(job)  # type: ignore[attr-defined]


_APPLY_METHODS = (
    "protected bool ApplyCameraSet(MCPJob job)",
    "protected bool ApplyFreeCamera(MCPJob job, MCPCameraValidation validation)",
    "protected void DeleteOwnedCamera()",
)
_APPLY_SIBLINGS = (
    "ValidateCameraArgs|StopCameraPath|FinishCameraHandoff|SuppressGameplay|ApplyFreeCamera|"
    "DeleteOwnedCamera|ArrayToMatrix"
)
# ApplyCameraSet's static array, which the translator does not parse, becomes
# the list of four rows that the ArrayToMatrix fake fills, as its out
# parameter does.
_MATRIX_LOCAL = ("vector matrix[4];", "MatrixRows matrix = MatrixRows();")


class _ApplyBridge:
    """ApplyCameraSet, ApplyFreeCamera and DeleteOwnedCamera translated, on fakes.

    ValidateCameraArgs answers `validation`. StopCameraPath,
    FinishCameraHandoff and SuppressGameplay only log. CreateObject logs and
    makes the `static` camera, FreeDebugCamera.GetInstance answers the `free`
    one, both on the bridge's log, and ArrayToMatrix fills the matrix with the
    rows of cam_matrix, three numbers each.
    """

    def __init__(self, bridge_source: str) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.static_cam = _Camera(self.log, "static")
        self.free_cam = _Camera(self.log, "free")
        self.player = SimpleNamespace(
            DisableSimulation=lambda off: self.log.append(("player", "DisableSimulation", off))
        )
        self.validation: SimpleNamespace | None = None
        self.m_ActiveCam: _Camera | None = None
        self.m_ActiveCamOwned = False
        self.m_PlayerSimulationDisabled = False
        self.constants = _bridge_constants(bridge_source)
        namespace: dict[str, Any] = {
            "_S": _Text,
            "g_Game": SimpleNamespace(CreateObject=self._create, ObjectDelete=self._delete),
            "Camera": SimpleNamespace(Cast=lambda thing: thing if isinstance(thing, _Camera) else None),
            "FreeDebugCamera": SimpleNamespace(GetInstance=lambda: self.free_cam),
            "PlayerBase": SimpleNamespace(Cast=lambda thing: thing),
            "GetGame": lambda: SimpleNamespace(GetPlayer=lambda: self.player),
            "MatrixRows": lambda: [None, None, None, None],
            **self.constants,
        }
        for signature in _APPLY_METHODS:
            head, code = _function(bridge_source, signature, this_type="MCPClientBridge", methods=_APPLY_SIBLINGS)
            if signature == _APPLY_METHODS[0]:
                if code.count(_MATRIX_LOCAL[0]) != 1:
                    raise AssertionError(f"{_MATRIX_LOCAL[0]} not found once in ApplyCameraSet")
                code = code.replace(*_MATRIX_LOCAL)
            method = translate(
                code,
                head,
                namespace,
                class_types={"MCPCameraValidation", "Object", "Camera", "FreeDebugCamera", "PlayerBase", "MatrixRows"},
            )
            setattr(self, method.__name__, MethodType(method, self))

    def _create(self, kind: _Text, position: tuple[float, ...], local: bool) -> object:
        self.log.append(("engine", "CreateObject", kind.text, tuple(float(v) for v in position), local))
        return self.static_cam

    def _delete(self, thing: object) -> None:
        self.log.append(("engine", "ObjectDelete", thing))

    def ValidateCameraArgs(self, args: object) -> SimpleNamespace | None:
        return self.validation

    def StopCameraPath(self) -> None:
        self.log.append(("bridge", "StopCameraPath"))

    def FinishCameraHandoff(self) -> None:
        self.log.append(("bridge", "FinishCameraHandoff"))

    def SuppressGameplay(self) -> None:
        self.log.append(("bridge", "SuppressGameplay"))

    def ArrayToMatrix(self, values: list[float] | None, matrix: list[Any]) -> bool:
        if values is None or len(values) != 12:
            return False
        for row in range(4):
            matrix[row] = tuple(values[3 * row : 3 * row + 3])
        return True


_HUD_FLAGS = SimpleNamespace(HUD_HIDE="HUD_HIDE", QUICKBAR_HIDE="QUICKBAR_HIDE")


class _Hud:
    """IngameHud and its IngameHudVisibility on one set of context flags.

    ShowHudPlayer and ShowQuickbarPlayer set HUD_HIDE and QUICKBAR_HIDE to
    !show (ingamehud.c:897-906, as the bridge cites it), and the
    visibility's IsContextFlagActive reads the set.
    """

    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log
        self.flags: set[str] = set()
        self.visibility: SimpleNamespace | None = SimpleNamespace(IsContextFlagActive=lambda flag: flag in self.flags)

    def GetHudVisibility(self) -> SimpleNamespace | None:
        return self.visibility

    def _show(self, flag: str, show: bool) -> None:
        if show:
            self.flags.discard(flag)
        else:
            self.flags.add(flag)

    def ShowHudPlayer(self, show: bool) -> None:
        self.log.append(("hud", "ShowHudPlayer", show))
        self._show(_HUD_FLAGS.HUD_HIDE, show)

    def ShowQuickbarPlayer(self, show: bool) -> None:
        self.log.append(("hud", "ShowQuickbarPlayer", show))
        self._show(_HUD_FLAGS.QUICKBAR_HIDE, show)


class _HudBridge:
    """DispatchHudSet translated, on a fake mission whose HUD is a _Hud."""

    def __init__(self, bridge_source: str) -> None:
        self.log: list[tuple[Any, ...]] = []
        self.hud: object = _Hud(self.log)
        self.mission: SimpleNamespace | None = SimpleNamespace(GetHud=lambda: self.hud)
        namespace: dict[str, Any] = {
            "_S": _Text,
            "GetGame": lambda: SimpleNamespace(GetMission=lambda: self.mission),
            "IngameHud": SimpleNamespace(Cast=lambda thing: thing if isinstance(thing, _Hud) else None),
            "EHudContextFlags": _HUD_FLAGS,
            "MCPHudState": lambda: SimpleNamespace(visible=None, hud_hide=None, quickbar_hide=None),
        }
        signature = "protected bool DispatchHudSet(MCPCommand command, MCPResult result)"
        head, code = _function(bridge_source, signature, this_type="MCPClientBridge")
        method = translate(
            code, head, namespace, class_types={"Mission", "IngameHud", "IngameHudVisibility", "MCPHudState"}
        )
        self.DispatchHudSet = MethodType(method, self)

    def set(self, args: SimpleNamespace | None) -> tuple[bool, SimpleNamespace]:
        command = SimpleNamespace(id=31, args=args)
        result = SimpleNamespace(ok=False, error=_Text.of(""), hud=None)
        return self.DispatchHudSet(command, result), result


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
            "and so do stop, the engine reporting the path's camera inactive and vanilla's developer free "
            "camera turned on",
            "another camera turned on from outside the bridge ends it only if the engine then reports the "
            "path's camera inactive",
            "When the lease that played it ends, the daemon queues an internal camera_path_release for the "
            "client, which ends the path aborted when the client runs it; nothing confirms that stop",
            "the client bridge bound to the run does not announce both camera_path_play and camera_path_release",
            "With no client bound, the loopback's binding fence refuses the call first (legacy_unbound",
            "there is no Camera.InterpolateTo",
            "Catmull-Rom",
            "the short way round",
            "camera_path {state, t_s, duration_s, path_id}",
            "idle (no play yet), playing, finished or aborted",
            "Read the pose with camera_get",
            "replaced_command_id",
            "path_not_found",
            "client_not_in_game",
            "camera_create_failed",
            "bridge_capability_missing",
            "left with restore_gameplay",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, description)
        self.assertNotIn("path_busy", description)


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
            (
                {"focus_distance": 0.0, "focus_blur": 1.0},
                "bad_args: focus_distance 0.0 must be a number of metres above 0, also as a 32-bit float, "
                "and at most 1e9",
            ),
            ({"focus_distance": -2.0, "focus_blur": 1.0}, "bad_args: focus_distance -2.0"),
            ({"focus_distance": math.inf, "focus_blur": 1.0}, "bad_args: focus_distance inf"),
            ({"focus_distance": math.nan, "focus_blur": 1.0}, "bad_args: focus_distance nan"),
            # Finite here, but infinite and 0 as the bridge's 32-bit float.
            ({"focus_distance": 1e100, "focus_blur": 1.0}, "bad_args: focus_distance 1e+100"),
            ({"focus_distance": 1e-46, "focus_blur": 1.0}, "bad_args: focus_distance 1e-46"),
            ({"focus_distance": 1.5e9, "focus_blur": 1.0}, "bad_args: focus_distance 1500000000.0"),
            (
                {"focus_distance": 2.0, "focus_blur": -0.5},
                "bad_args: focus_blur -0.5 must be 0, or above 0 also as a 32-bit float, and at most 1e9",
            ),
            ({"focus_distance": 2.0, "focus_blur": math.inf}, "bad_args: focus_blur inf"),
            ({"focus_distance": 2.0, "focus_blur": 1e100}, "bad_args: focus_blur 1e+100"),
            ({"focus_distance": 2.0, "focus_blur": 1e-46}, "bad_args: focus_blur 1e-46"),
            ({"focus_distance": True, "focus_blur": 1.0}, "valid number"),
            ({"focus_distance": 2.0, "focus_blur": "1"}, "valid number"),
        ):
            with self.subTest(extra=extra):
                self.assertIn(expected, await self._refused("camera_set", dict(_ORIENT, **extra)))

    async def test_focus_at_the_float_bounds_goes_out_as_given(self) -> None:
        for distance, blur in ((1e9, 1e9), (1e-45, 0.0), (2.0, 1e-45)):
            with self.subTest(distance=distance, blur=blur):
                args, _result = await self._forwarded(
                    "camera_set", dict(_ORIENT, focus_distance=distance, focus_blur=blur), _camera_answer()
                )
                self.assertEqual((args[1]["focus_distance"], args[1]["focus_blur"]), (distance, blur))
                self.assertEqual(loopback.validate_command_args(args[0], args[1]), (True, None))

    def test_the_ingress_checks_focus_as_the_tool_does(self) -> None:
        wire = dict(VALID_WIRE_ARGS["camera_set_focus"])
        for field, value in (
            ("focus_distance", 1e100),
            ("focus_distance", 1e-46),
            ("focus_distance", 0.0),
            ("focus_distance", 1.5e9),
            ("focus_distance", 10**400),
            ("focus_blur", 1e100),
            ("focus_blur", 1e-46),
            ("focus_blur", -0.5),
            ("focus_blur", 10**400),
        ):
            with self.subTest(field=field, value=value):
                self.assertEqual(
                    loopback.validate_command_args("camera_set_focus", dict(wire, **{field: value})),
                    (False, "bad_args"),
                )
                checker = camera_path.focus_distance if field == "focus_distance" else camera_path.focus_blur
                self.assertIsNone(checker(value))
        for field, value in (("focus_distance", 1e9), ("focus_distance", 1e-45), ("focus_blur", 0.0), ("focus_blur", 1e-45)):
            with self.subTest(field=field, value=value):
                self.assertEqual(
                    loopback.validate_command_args("camera_set_focus", dict(wire, **{field: value})), (True, None)
                )
        # The values the bridge's IsValidCameraFocus would refuse, as Codex measured.
        self.assertEqual(camera_path.float32(1e-46), 0.0)
        self.assertGreater(camera_path.float32(1e-45), 0.0)

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
            "focus_distance (metres, above 0, also as the 32-bit float the bridge reads) and focus_blur "
            "(0, or above 0 also as that float), each at most 1e9, go together",
            "A timeout does not prove the camera was not applied: the bridge applies it when the command "
            "arrives, before the settle.",
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

    def _enqueue_release(self) -> tuple[int, dict]:
        # As enqueue_camera_path_releases_for_lease sends it, internal.
        return self.state.enqueue_command(
            "camera_path_release", {"command_id": 5}, peer="client", internal=True
        )

    def test_a_census_without_the_release_is_refused_the_play(self) -> None:
        # Round 2's client announced camera_path_play but not the release that
        # stops it at the lease end: it is handed neither, and its other verbs
        # stay admitted.
        self.assertIn("camera_path_play", ROUND_TWO_CENSUS)
        self.assertNotIn("camera_path_release", ROUND_TWO_CENSUS)
        self._announce(ROUND_TWO_CENSUS)
        self.assertEqual(self._enqueue("camera_path_play"), (409, {"error": "bridge_capability_missing"}))
        self.assertEqual(self._enqueue_release(), (409, {"error": "bridge_capability_missing"}))
        for command in ("camera_set_focus", "hud_set"):
            with self.subTest(command=command):
                status, body = self._enqueue(command)
                self.assertEqual(status, 200, body)
        self.assertEqual([row["cmd"] for row in bound_queue(self.state, "client")], ["camera_set_focus", "hud_set"])

    def test_the_current_census_admits_the_play_and_its_release(self) -> None:
        self._announce(self.full)
        play_status, play = self._enqueue("camera_path_play")
        release_status, release = self._enqueue_release()
        self.assertEqual((play_status, release_status), (200, 200), (play, release))
        status, poll = self._announce(self.full)
        self.assertEqual(status, 200)
        self.assertEqual(
            [(command["id"], command["cmd"]) for command in poll["commands"]],
            [(play["id"], "camera_path_play"), (release["id"], "camera_path_release")],
        )

    def test_delivery_refuses_the_play_and_the_release_to_that_census(self) -> None:
        self._announce(self.full)
        _status, play = self._enqueue("camera_path_play")
        _status, release = self._enqueue_release()
        status, poll = self._announce(ROUND_TWO_CENSUS)
        self.assertEqual((status, poll["commands"]), (200, []))
        for command_id in (play["id"], release["id"]):
            with self.subTest(command_id=command_id):
                self.assertEqual(
                    self.state._results[command_id],
                    {"id": command_id, "ok": False, "error": "bridge_capability_missing"},
                )
        self.assertEqual(bound_queue(self.state, "client"), [])

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
    def test_readiness_requires_release_alongside_play(self) -> None:
        census = set(announced_caps(_source(BRIDGE)))
        registered = frozenset(
            tool for tool in bridge_readiness._BRIDGE_COMMAND_TOOLS["client"].values() if tool
        )
        for release_present in (False, True):
            with self.subTest(release_present=release_present):
                commands = sorted(census if release_present else census - {"camera_path_release"})
                result = bridge_readiness._compare_bridge_capabilities(
                    "client", {"state": "announced", "announced_commands": commands}, registered
                )
                self.assertEqual(result["state"], "match" if release_present else "mismatch")
                self.assertEqual(
                    result["registered_without_announced_command"],
                    [] if release_present else ["camera_path_play"],
                )

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

    def test_the_release_is_an_internal_client_command(self) -> None:
        source = _source(BRIDGE)
        self.assertIn("camera_path_release", announced_caps(source))
        self.assertIn("camera_path_release", dispatch_census(source))
        self.assertIn("camera_path_release", loopback.CLIENT_COMMANDS)
        self.assertNotIn("camera_path_release", loopback.SERVER_COMMANDS)
        self.assertIn("camera_path_release", loopback._OWNER_CLEANUP_COMMANDS)
        # Internal, yet checked against the census like the verbs, and a play
        # needs it announced too (OlderBridgeAdmissionTest).
        self.assertEqual(loopback._CLIENT_ANNOUNCED_COMMANDS["camera_path_release"], "bridge_capability_missing")
        self.assertEqual(loopback._CLIENT_ANNOUNCED_WITH, {"camera_path_play": "camera_path_release"})
        self.assertTrue(command_requires_lease("camera_path_release"))
        # Not a tool: the readiness map and its fixture name none.
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["peers"]["client"]
        self.assertIn("camera_path_release", fixture)
        self.assertIsNone(fixture["camera_path_release"])
        self.assertIsNone(server._BRIDGE_COMMAND_TOOLS["client"]["camera_path_release"])
        registry = {source: (target, build) for source, target, build in loopback.RELEASE_REGISTRY}
        target, build = registry["camera_path_play"]
        self.assertEqual(target, "camera_path_release")
        self.assertEqual(build(42, {"mode": "play"}), {"command_id": 42})
        self.assertEqual(loopback.validate_command_args("camera_path_release", {"command_id": 42}), (True, None))
        for refused in ({}, {"command_id": 0}, {"command_id": True}, {"command_id": 1.0}, {"command_id": 1, "x": 1}):
            with self.subTest(args=refused):
                self.assertEqual(loopback.validate_command_args("camera_path_release", refused), (False, "bad_args"))
        self.assertEqual(
            result_prune.OWNED_SCALAR_FIELDS["matched"], frozenset({"action_hold_cancel", "camera_path_release"})
        )
        answer = result_prune.prune_unfilled_fields("camera_path_release", {"ok": 1, "matched": 0, "camera_path": {}})
        self.assertEqual(answer, {"ok": 1, "matched": 0})

    def test_the_version_and_the_server_arg_contract_hash_stay(self) -> None:
        for command in (*NEW_COMMANDS, "camera_path_release"):
            self.assertNotIn(command, server.SERVER_ARG_CONTRACT)
        self.assertEqual(server.EXPECTED_SERVER_ARG_CONTRACT_HASH, "0401bed390754a55")
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

    def test_fov_stays_linear_under_smooth_easing(self) -> None:
        # Three keys at uneven times, smooth easing. Between two keys that both
        # hold 0.5 a linear FOV is 0.5; a Catmull-Rom FOV would overshoot to
        # 0.453125 at t = 2.5 (the tangent at t = 1 sees the 1.0 before it).
        keys = [_key(0, fov=1.0), _key(1.0, fov=0.5), _key(4.0, fov=0.5)]
        path = self._playing(keys, easing="smooth")
        path.Advance(_F32(0.5))
        self.assertEqual(self._pose(path).fov, 0.75)
        path.Advance(_F32(2.0))
        self.assertEqual(_report(path)["t_s"], 2.5)
        self.assertEqual(self._pose(path).fov, 0.5)

    def test_a_far_angle_unwraps_in_the_bridge_float(self) -> None:
        # yaw 0 to 1e9 over 2 s. Independent oracle, each step a 32-bit float:
        # delta / 360 = 2777777.75, Math.Round of it 2777778 (a float), 360 times
        # that 1000000080 rounded to 1000000064, so the stored yaw is
        # 1e9 - 1000000064 = -64 and the middle of the segment -32.
        f32 = camera_path.float32
        quotient = f32(f32(1e9) / 360.0)
        self.assertEqual(quotient, 2777777.75)
        rounded = f32(math.floor(quotient + 0.5))
        self.assertEqual(rounded, 2777778.0)
        product = f32(360.0 * rounded)
        self.assertEqual(product, 1000000064.0)
        stored = f32(f32(1e9) - product)
        self.assertEqual(stored, -64.0)
        keys = [_key(0), _key(2.0, cam_orientation=[1e9, 0.0, 0.0])]
        camera_path.normalize_request("play", keys, "linear", "")
        path = self._playing(keys)
        self.assertEqual(float(path.m_AimX.Get(1)), stored)
        path.Advance(_F32(1.0))
        self.assertEqual(self._pose(path).orientation, (stored / 2, 0.0, 0.0))

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


# The owner of the lease in the release probes below.
_PATH_OWNER = {
    "platform": "codex",
    "pid": 4343,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:01Z",
    "session_id": "session-path",
    "task_label": "path",
}


class ExecutedDispatchTest(unittest.TestCase):
    """The camera_path_play, camera_path_release and camera_set dispatch, the
    camera_set job and TickCameraPath of MCPClientBridge.c run on fakes. Every
    change is made in a dispatch; a job only waits and reports (d026)."""

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
        camera = self.client.camera("path")
        self.client.next_object = camera
        return camera

    def _played(self) -> _Camera:
        camera = self._new_camera()
        job = self.client.play(self._wire())
        self.assertEqual((job.done, job.error.text), (True, ""))
        return camera

    def _in_order(self, entries: list[tuple[Any, ...]]) -> None:
        positions = [self.client.index(entry) for entry in entries]
        self.assertEqual(positions, sorted(positions))

    def test_a_play_answers_at_its_dispatch_with_the_path_playing(self) -> None:
        # postNow: the answer leaves with the dispatch, and no job is queued
        # (this fake bridge has no job runner, so a queued job would raise).
        camera = self._new_camera()
        answer = self.client.play(self._wire(), command_id=11)
        self.assertEqual((answer.done, answer.ok, answer.error.text), (True, True, ""))
        self.assertEqual(
            _block(answer.camera_path), {
                "state": "playing", "t_s": 0.0, "duration_s": 6.0, "path_id": "line6",
                "command_id": 11, "replaced_command_id": 0,
            }
        )
        self.assertEqual(self.client.m_CameraPathCommandId, 11)
        self.assertTrue(camera.active)
        # Its pose is on the camera before the dispatch returns; the frame moves it next.
        self.assertEqual(camera.position, (0.0, 2.0, 0.0))

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
        self._in_order(
            [
                ("bridge", "HasExclusiveJob"),
                create,
                ("bridge", "FinishCameraHandoff"),
                ("bridge", "SuppressGameplay"),
                ("path", "SetPosition", (0.0, 2.0, 0.0)),
                ("path", "SetActive", True),
            ]
        )
        # From the player view nothing touches the free camera or the simulation.
        for entry in self.client.log:
            self.assertNotIn(entry[0], ("free", "player"))
        self.assertNotIn(("bridge", "RestoreGameplay"), self.client.log)

    def test_a_play_from_camera_set_free_leaves_only_its_camera_on(self) -> None:
        self._after_camera_set_free()
        camera = self._played()
        self.assertFalse(self.client.free_cam.active)
        self.assertTrue(camera.active)
        self._in_order(
            [
                ("engine", "CreateObject", "staticcamera", (0.0, 2.0, 0.0), True),
                ("bridge", "FinishCameraHandoff"),
                ("bridge", "SuppressGameplay"),
                ("free", "SetActive", False),
                ("player", "DisableSimulation", False),
                ("path", "SetPosition", (0.0, 2.0, 0.0)),
                ("path", "SetActive", True),
            ]
        )
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
                    self.assertEqual((job.done, job.ok, job.error.text), (True, False, "camera_create_failed"))
                    self.assertIsNone(job.camera_path)
                    # Only the made object goes; nothing else is called.
                    expected = [
                        ("bridge", "HasExclusiveJob"),
                        ("engine", "CreateObject", "staticcamera", (0.0, 2.0, 0.0), True),
                    ]
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
        self.assertEqual(self.client.m_CameraPathCommandId, 0)

    def test_play_replaces_and_late_release_does_not_stop_successor(self) -> None:
        first = self._played()
        old_path = self.client.m_CameraPath
        second = self._new_camera()
        answer = self.client.play(self._wire("second"), command_id=8)
        self.assertTrue(answer.ok)
        self.assertEqual(_block(answer.camera_path)["replaced_command_id"], 7)
        self.assertEqual(_report(old_path)["state"], "aborted")
        self.assertIn(("engine", "ObjectDelete", first), self.client.log)
        self.assertFalse(self.client.release(7).matched)
        self.assertEqual(self.client.m_CameraPathCommandId, 8)
        self.assertTrue(second.active)
        self.assertTrue(self.client.release(8).matched)
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
        self.assertEqual(self.client.m_CameraPathCommandId, 0)
        self.assertIn(("bridge", "ReleaseCamera"), self.client.log)
        self.assertIn(("bridge", "RestoreGameplay"), self.client.log)

    def test_natural_end_restores_and_settles_even_after_play_result_consumed(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "finished")
        self._report_play(state, play)
        state.take_result(play["id"], remove=True)
        self.client.log.clear()
        self.client.TickCameraPath(_F32(6.0))
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "finished")
        self.assertEqual(self.client.m_CameraPathCommandId, 0)
        self.assertIsNone(self.client.m_CameraPathCam)
        self.assertIn(("bridge", "ReleaseCamera"), self.client.log)
        self.assertIn(("bridge", "RestoreGameplay"), self.client.log)
        self.assertEqual(self.client.terminal_result["camera_path"]["command_id"], play["id"])
        state.store_result(self.client.terminal_result, instance=INST_CLIENT)
        self.assertFalse(self.client.release(play["id"]).matched)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued", 0), 0)
        self.assertEqual(self._sent(state), [])

    def test_terminal_report_before_play_reply_does_not_rearm_cleanup(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "finished")
        reply = self._execute_path(play)
        self.client.TickCameraPath(_F32(6.0))
        state.store_result(self.client.terminal_result, instance=INST_CLIENT)
        # Independent HTTP callbacks can deliver the immediate reply last.
        state.store_result(reply, instance=INST_CLIENT)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued", 0), 0)
        self.assertEqual(self._sent(state), [])

    def test_finished_predecessor_does_not_retire_its_successor(self) -> None:
        state, coordinator, client, token = self._leased()
        first = self._deliver_play(state, token, "first")
        self._report_play(state, first)
        self.client.TickCameraPath(_F32(6.0))
        terminal = self.client.terminal_result
        second = self._deliver_play(state, token, "second")
        self._report_play(state, second)
        state.store_result(terminal, instance=INST_CLIENT)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        self.assertEqual(self._sent(state), self._releases(second["id"]))

    def test_unverified_finished_report_keeps_cleanup_armed(self) -> None:
        for failure in ("wrong_id", "boolean_id", "wrong_instance"):
            with self.subTest(failure=failure):
                self.setUp()
                state, coordinator, client, token = self._leased()
                play = self._deliver_play(state, token, "running")
                self._report_play(state, play)
                terminal = {"id": play["id"], "ok": True,
                            "camera_path": {"command_id": play["id"], "state": "finished"}}
                if failure == "wrong_id":
                    terminal["camera_path"]["command_id"] += 1
                elif failure == "boolean_id":
                    terminal["camera_path"]["command_id"] = True
                instance = "unbound-client" if failure == "wrong_instance" else INST_CLIENT
                state.store_result(terminal, instance=instance)
                status, released = coordinator.release(client, token)
                self.assertEqual(status, 200)
                self.assertEqual(released["cleanup"].get("camera_path_release_enqueued", 0), 1)

    def test_a_busy_play_changes_nothing(self) -> None:
        self.client.exclusive = True
        self._new_camera()
        job = self.client.play(self._wire())
        self.assertEqual((job.done, job.ok, job.error.text), (True, False, "busy"))
        self.assertEqual(self.client.log, [("bridge", "HasExclusiveJob")])
        self.assertIsNone(self.client.m_CameraPathCam)
        self.assertEqual(self.client.m_CameraPathCommandId, 0)
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "idle")

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

    def test_the_developer_free_camera_turned_on_aborts_the_path(self) -> None:
        camera = self._played()
        self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(camera.position, (1.0, 2.0, 0.0))
        # Vanilla's developer free camera turns the FreeDebugCamera singleton on
        # from outside the bridge (developerfreecamera.c:36-40). Nothing promises
        # that the path's camera then reads inactive (camera.c:45, :61), so here
        # it stays active and stays m_ActiveCam.
        self.client.free_cam.active = True
        self.assertTrue(camera.active)
        self.assertIs(self.client.m_ActiveCam, camera)
        frames = len(self.client.log)
        for _ in range(2):
            self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(self.client.log[frames:], [])
        self.assertEqual(
            _report(self.client.m_CameraPath), {"state": "aborted", "t_s": 1.0, "duration_s": 6.0, "path_id": "line6"}
        )
        self.assertIsNone(self.client.m_CameraPathCam)

    def test_a_play_reads_no_camera_state_in_its_first_frame(self) -> None:
        # Camera.SetActive and IsActive (camera.c:45, :61) do not say when an
        # activation shows. Here it shows a frame late: not in the first OnTick
        # after the dispatch, whose frame a dispatch from the poll callback can
        # share with the SetActive, but from the second on.
        for late in ("path", "free"):
            with self.subTest(late=late):
                self.setUp()
                if late == "free":
                    # From camera_set free: the free camera the play turns off
                    # still reads on in that first frame.
                    self._after_camera_set_free()
                    self.client.free_cam.shows_after = 2
                camera = self._new_camera()
                if late == "path":
                    camera.shows_after = 2
                answer = self.client.play(self._wire())
                self.assertEqual(_block(answer.camera_path)["state"], "playing")
                late_camera = camera if late == "path" else self.client.free_cam
                self.assertEqual(late_camera.IsActive(), late == "free")
                self.client.TickCameraPath(_F32(0.5))
                self.assertEqual(late_camera.IsActive(), late == "free")
                self.assertEqual(_report(self.client.m_CameraPath)["t_s"], 0.5)
                self.client.TickCameraPath(_F32(0.5))
                self.assertEqual(late_camera.IsActive(), late == "path")
                self.assertEqual(
                    _report(self.client.m_CameraPath),
                    {"state": "playing", "t_s": 1.0, "duration_s": 6.0, "path_id": "line6"},
                )
                self.assertEqual(camera.position, (1.0, 2.0, 0.0))

    def test_an_activation_that_never_shows_aborts_the_path_a_frame_later(self) -> None:
        for late in ("path", "free"):
            with self.subTest(late=late):
                self.setUp()
                if late == "free":
                    self._after_camera_set_free()
                    self.client.free_cam.shows_after = math.inf
                camera = self._new_camera()
                if late == "path":
                    camera.shows_after = math.inf
                self.client.play(self._wire())
                self.client.TickCameraPath(_F32(0.5))
                self.assertEqual(_report(self.client.m_CameraPath)["state"], "playing")
                frames = len(self.client.log)
                self.client.TickCameraPath(_F32(0.5))
                self.assertEqual(self.client.log[frames:], [])
                self.assertEqual(
                    _report(self.client.m_CameraPath),
                    {"state": "aborted", "t_s": 0.5, "duration_s": 6.0, "path_id": "line6"},
                )
                self.assertIsNone(self.client.m_CameraPathCam)

    def test_a_release_stops_only_the_play_it_names(self) -> None:
        camera = self._played()
        self.client.TickCameraPath(_F32(1.0))
        other = self.client.release(8)
        self.assertEqual((other.done, other.ok, other.matched), (True, True, False))
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "playing")
        self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(camera.position, (2.0, 2.0, 0.0))
        named = self.client.release(7)
        self.assertEqual((named.done, named.ok, named.matched), (True, True, True))
        self.assertEqual(
            _report(self.client.m_CameraPath), {"state": "aborted", "t_s": 2.0, "duration_s": 6.0, "path_id": "line6"}
        )
        frames = len(self.client.log)
        self.client.TickCameraPath(_F32(1.0))
        self.assertEqual(self.client.log[frames:], [])
        self.assertEqual(camera.position, (2.0, 2.0, 0.0))
        # A late release of a path that already ended matches nothing.
        again = self.client.release(7)
        self.assertEqual((again.ok, again.matched), (True, False))
        refused = self.client.release(0)
        self.assertEqual((refused.done, refused.ok, refused.error.text), (True, False, "bad_args"))

    @staticmethod
    def _poll(state: loopback.ServerState, census: list[str] | None = None) -> dict[str, Any]:
        """One client poll, with the current census unless another is given."""
        if census is None:
            census = sorted(server._BRIDGE_COMMAND_TOOLS["client"])
        status, body = state.record_poll(
            "client",
            "12~1.29",
            instance=INST_CLIENT,
            source_pid=PID_CLIENT,
            caps=",".join(census),
        )
        if status != 200:
            raise AssertionError(body)
        return body

    def _leased(
        self, clock: Callable[[], float] | None = None
    ) -> tuple[loopback.ServerState, SessionCoordinator, ClientIdentity, str]:
        """A bound client and a held lease. Releasing it runs the loopback's own
        cleanup_owner and then raises the owner fence on the run, the order the
        daemon's cleanup_begin and begin_release_owner run them in; nothing else
        of the lifecycle and no process. clock, if given, is the loopback's."""
        state = loopback.ServerState("test-key", time_fn=clock)
        bind_both_peers(state)

        def cleanup(session_id: str, lease_id: str, reason: str, vehicle_active: bool) -> dict[str, object]:
            result = state.cleanup_owner(session_id, lease_id, reason, vehicle_active)
            state.fence_runs(["test-run"])
            return result

        coordinator = SessionCoordinator(
            token_fn=lambda: "path-token",
            id_fn=lambda: "path-lease",
            audit=lambda _event: True,
            cleanup=cleanup,
        )
        state.coordination = coordinator
        state.retail_probe = lambda: {"known": True, "processes": []}
        self._poll(state)
        client = ClientIdentity.from_payload(_PATH_OWNER)
        status, active = coordinator.acquire(client, "path")
        self.assertEqual(status, 200, active)
        return state, coordinator, client, str(active["lease_token"])

    def _deliver_play(
        self, state: loopback.ServerState, token: str, path_id: str, timeout_s: float = 30.0
    ) -> dict[str, Any]:
        wire = {"mode": "play", "keys": _three_keys(), "easing": "linear", "path_id": path_id}
        status, queued = state.enqueue_command(
            "camera_path_play",
            wire,
            peer="client",
            identity_payload=_PATH_OWNER,
            lease_token=token,
            operation_timeout_s=timeout_s,
        )
        self.assertEqual(status, 200, queued)
        delivered = self._poll(state)["commands"]
        self.assertEqual(delivered, [{"id": queued["id"], "cmd": "camera_path_play", "args": wire}])
        return delivered[0]

    def _execute_path(self, command: dict) -> dict:
        """Execute the actual .c dispatcher and serialize its reply, not an id-order model."""
        self._new_camera()
        answer = self.client.play(command["args"], command_id=command["id"])
        self.assertTrue(answer.done)
        body = {"id": command["id"], "ok": answer.ok, "error": answer.error.text}
        if answer.camera_path is not None:
            body["camera_path"] = _block(answer.camera_path)
        return body

    def _report_play(self, state: loopback.ServerState, command: dict, ok: object = True) -> tuple:
        body = self._execute_path(command)
        self.assertTrue(body["ok"], body)
        body["ok"] = ok
        return state.store_result(body, instance=INST_CLIENT)

    def test_releasing_the_lease_stops_the_path_it_played(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "lease_case")
        camera = self._new_camera()
        answer = self.client.play(play["args"], command_id=play["id"])
        self.assertTrue(answer.ok)
        state.store_result({"id": play["id"], "ok": 1, "camera_path": _block(answer.camera_path)}, instance=INST_CLIENT)
        self.client.TickCameraPath(_F32(0.5))
        self.assertEqual(camera.position, (0.5, 2.0, 0.0))
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertTrue(released["released"])
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        self.assertFalse(released.get("cleanup_degraded"))
        # The lease is gone and the run fenced; the release still goes out.
        self.assertTrue(state._run_is_fenced("test-run"))
        [release] = self._poll(state)["commands"]
        self.assertEqual((release["cmd"], release["args"]), ("camera_path_release", {"command_id": play["id"]}))
        answer = self.client.release(release["args"]["command_id"], command_id=release["id"])
        self.assertEqual((answer.ok, answer.matched), (True, True))
        frames = len(self.client.log)
        for _ in range(2):
            self.client.TickCameraPath(_F32(0.5))
        self.assertEqual(self.client.log[frames:], [])
        self.assertEqual(camera.position, (0.5, 2.0, 0.0))
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
        self.assertEqual(self._poll(state)["commands"], [])

    def test_abandoning_a_delivered_play_queues_its_release(self) -> None:
        state, _coordinator, _client, token = self._leased()
        play = self._deliver_play(state, token, "abandoned")
        abandoned = state.abandon_command(play["id"], "cancelled")
        self.assertEqual(abandoned.get("status"), "release_queued", abandoned)
        self.assertEqual(
            self._poll(state)["commands"],
            [{"id": abandoned["release_id"], "cmd": "camera_path_release", "args": {"command_id": play["id"]}}],
        )

    def test_a_lease_released_under_quarantine_leaves_the_path_unconfirmed(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "quarantined")
        state.retail_probe = lambda: {"known": True, "processes": [{"name": "DayZ_x64.exe"}]}
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        degraded = released.get("cleanup_degraded") or []
        self.assertIn("retail_quarantine", degraded)
        self.assertIn("camera_path_release_unconfirmed", degraded)
        self.assertNotIn("camera_path_release_enqueued", released["cleanup"])
        self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {play["id"]}})
        self.assertEqual(self._poll(state)["commands"], [])  # still quarantined
        state.retail_probe = lambda: {"known": True, "processes": []}
        self.assertEqual(self._poll(state)["commands"], [])  # retry queues it
        self.assertEqual(self._sent(state), self._releases(play["id"]))

    def test_a_lease_end_queues_no_release_the_client_does_not_announce(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "census")
        state.store_result({"id": play["id"], "ok": True}, instance=INST_CLIENT)
        # The client then polls with round 2's census, which has no release.
        self.assertEqual(self._poll(state, ROUND_TWO_CENSUS)["commands"], [])
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertIn("camera_path_release_unconfirmed", released.get("cleanup_degraded") or [])
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 0)
        self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {play["id"]}})
        self.assertEqual(self._poll(state, ROUND_TWO_CENSUS)["commands"], [])
        self.assertEqual(self._poll(state)["commands"], [])  # new census allows retry
        self.assertEqual(self._sent(state), self._releases(play["id"]))

    def test_a_queued_release_is_checked_against_the_census_at_delivery(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "census")
        state.store_result({"id": play["id"], "ok": True}, instance=INST_CLIENT)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        # Queued for the current census, it is not handed to round 2's.
        self.assertEqual(self._poll(state, ROUND_TWO_CENSUS)["commands"], [])
        self.assertEqual(self._poll(state)["commands"], [])

    def test_abandoning_a_play_queues_no_release_the_client_does_not_announce(self) -> None:
        state, _coordinator, _client, token = self._leased()
        play = self._deliver_play(state, token, "abandoned")
        self.assertEqual(self._poll(state, ROUND_TWO_CENSUS)["commands"], [])
        abandoned = state.abandon_command(play["id"], "cancelled")
        self.assertEqual(abandoned, {"status": "cleanup_degraded", "cause": "bridge_capability_missing"})
        self.assertEqual(self._poll(state)["commands"], [])

    def _sent(self, state: loopback.ServerState) -> list[tuple[str, dict[str, Any]]]:
        return [(command["cmd"], command["args"]) for command in self._poll(state)["commands"]]

    @staticmethod
    def _releases(*play_ids: int) -> list[tuple[str, dict[str, Any]]]:
        return [("camera_path_release", {"command_id": play_id}) for play_id in play_ids]

    def test_plays_past_the_queue_size_end_in_one_release(self) -> None:
        # Each play that starts replaces the path before it, so however many
        # played under the lease, its end sends the client one release.
        state, coordinator, client, token = self._leased()
        play_ids = []
        for index in range(loopback.MAX_QUEUE + 5):
            play = self._deliver_play(state, token, f"take{index}")
            self.client.play({"mode": "stop", "path_id": ""})
            self._report_play(state, play)
            state.take_result(play["id"], remove=True)
            play_ids.append(play["id"])
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        self.assertNotIn("camera_path_release_unconfirmed", released.get("cleanup_degraded") or [])
        self.assertEqual(self._sent(state), self._releases(play_ids[-1]))

    def test_a_busy_play_leaves_the_release_to_the_path_that_plays(self) -> None:
        state, coordinator, client, token = self._leased()
        playing = self._deliver_play(state, token, "playing")
        self._new_camera()
        answer = self.client.play(playing["args"], command_id=playing["id"])
        state.store_result(
            {"id": playing["id"], "ok": answer.ok, "camera_path": _block(answer.camera_path)}, instance=INST_CLIENT
        )
        busy = self._deliver_play(state, token, "busy")
        self.client.exclusive = True
        answer = self.client.play(busy["args"], command_id=busy["id"])
        self.assertEqual((answer.ok, answer.error.text), (False, "busy"))
        state.store_result({"id": busy["id"], "ok": answer.ok, "error": answer.error.text}, instance=INST_CLIENT)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        [release] = self._poll(state)["commands"]
        self.assertEqual([(release["cmd"], release["args"])], self._releases(playing["id"]))
        stopped = self.client.release(release["args"]["command_id"], command_id=release["id"])
        self.assertEqual((stopped.ok, stopped.matched), (True, True))
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")

    def test_the_lease_releases_its_started_play_and_one_unanswered(self) -> None:
        state, coordinator, client, token = self._leased()
        replaced = self._deliver_play(state, token, "replaced")
        self._report_play(state, replaced)
        self.client.play({"mode": "stop", "path_id": ""})
        started = self._deliver_play(state, token, "started")
        self._report_play(state, started)
        # Its answer is not back when the lease ends, so it may be the one playing.
        unanswered = self._deliver_play(state, token, "unanswered")
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 2)
        self.assertEqual(self._sent(state), self._releases(started["id"], unanswered["id"]))

    def test_a_play_answered_after_its_deadline_is_still_released(self) -> None:
        now = [100.0]
        state, coordinator, client, token = self._leased(clock=lambda: now[0])
        replaced = self._deliver_play(state, token, "replaced")
        self._report_play(state, replaced)
        self.client.play({"mode": "stop", "path_id": ""})
        late = self._deliver_play(state, token, "late", timeout_s=0.5)
        now[0] += 1.0
        # The loopback discards an answer past its deadline, but the path started.
        status, stored = self._report_play(state, late)
        self.assertEqual((status, stored.get("discarded")), (200, True), stored)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        self.assertEqual(self._sent(state), self._releases(late["id"]))

    def test_a_play_that_never_started_is_not_released(self) -> None:
        state, coordinator, client, token = self._leased()
        play = self._deliver_play(state, token, "refused")
        # No camera object to make: the play is refused and changes nothing.
        answer = self.client.play(play["args"], command_id=play["id"])
        self.assertEqual((answer.ok, answer.error.text), (False, "camera_create_failed"))
        state.store_result({"id": play["id"], "ok": answer.ok, "error": answer.error.text}, instance=INST_CLIENT)
        self.assertEqual(state._camera_path_play_by_lease, {})
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertNotIn("camera_path_release_enqueued", released["cleanup"])
        self.assertFalse(released.get("cleanup_degraded"))
        self.assertEqual(self._poll(state)["commands"], [])

    def test_an_ok_of_one_counts_as_a_started_play(self) -> None:
        state, coordinator, client, token = self._leased()
        first = self._deliver_play(state, token, "first")
        self._report_play(state, first, ok=1)
        self.client.play({"mode": "stop", "path_id": ""})
        second = self._deliver_play(state, token, "second")
        self._report_play(state, second, ok=1)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"].get("camera_path_release_enqueued"), 1)
        self.assertEqual(self._sent(state), self._releases(second["id"]))

    def _release_after_path_order(self, order: tuple[int, ...]) -> None:
        for reverse_results in (False, True):
            with self.subTest(order=order, reverse_results=reverse_results):
                self.client = _ClientBridge(self.bridge_source, self.Path)
                state, coordinator, client, token = self._leased()
                state._next_id = 6
                for command_id in (6, 7, 8):
                    wire = {"mode": "stop", "path_id": ""} if command_id == 7 else self._wire()
                    status, queued = state.enqueue_command(
                        "camera_path_play", wire, peer="client", identity_payload=_PATH_OWNER,
                        lease_token=token, operation_timeout_s=30.0,
                    )
                    self.assertEqual((status, queued.get("id")), (200, command_id))
                delivered = {command["id"]: command for command in self._poll(state)["commands"]}
                self.assertEqual(set(delivered), {6, 7, 8})
                replies = [self._execute_path(delivered[command_id]) for command_id in order]
                self.assertTrue(all(reply["ok"] for reply in replies), replies)
                self.assertEqual(replies[-1]["camera_path"]["replaced_command_id"], order[0])
                self.assertEqual(self.client.m_CameraPathCommandId, order[-1])
                for reply in reversed(replies) if reverse_results else replies:
                    status, stored = state.store_result(reply, instance=INST_CLIENT)
                    self.assertEqual(status, 200, stored)
                self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {order[-1]}})
                status, released = coordinator.release(client, token)
                self.assertEqual(status, 200, released)
                self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 1)
                [release] = self._poll(state)["commands"]
                self.assertEqual(release["args"], {"command_id": order[-1]})
                answer = self.client.release(release["args"]["command_id"], release["id"])
                self.assertTrue(answer.matched)
                self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")

    def test_lease_cleanup_tracks_execution_order_8_7_6(self) -> None:
        self._release_after_path_order((8, 7, 6))

    def test_lease_cleanup_tracks_plain_order_6_7_8(self) -> None:
        self._release_after_path_order((6, 7, 8))

    def test_retired_play_replies_preserve_the_replacement_chain(self) -> None:
        for reply_order in permutations((1, 2, 3)):
            with self.subTest(reply_order=reply_order):
                self.client = _ClientBridge(self.bridge_source, self.Path)
                state, coordinator, client, token = self._leased()
                plays = [self._deliver_play(state, token, f"take{i}") for i in range(3)]
                self.assertEqual([play["id"] for play in plays], [1, 2, 3])
                replies = {}
                for play in reversed(plays):
                    self.assertTrue(self.client.play({"mode": "stop", "path_id": ""}).ok)
                    replies[play["id"]] = self._execute_path(play)
                    self.assertTrue(replies[play["id"]]["ok"])
                self.assertEqual(
                    [replies[i]["camera_path"]["replaced_command_id"] for i in (3, 2, 1)], [0, 3, 2]
                )
                for command_id in reply_order:
                    status, stored = state.store_result(replies[command_id], instance=INST_CLIENT)
                    self.assertEqual(status, 200, stored)
                    state.take_result(command_id, remove=True)
                    if reply_order[0] == 1:
                        # Even before the missing middle reply arrives, an
                        # unrelated stale reply cannot displace confirmed 1.
                        self.assertEqual(state._camera_path_active_by_lease, {"path-lease": 1})
                self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {1}})
                self.assertEqual(state._camera_path_active_by_lease, {"path-lease": 1})
                self.assertEqual(self.client.m_CameraPathCommandId, 1)
                status, released = coordinator.release(client, token)
                self.assertEqual(status, 200, released)
                self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 1)
                [release] = self._poll(state)["commands"]
                self.assertEqual(release["args"], {"command_id": 1})
                self.assertTrue(self.client.release(1, release["id"]).matched)
                self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
                self.assertEqual(state._camera_path_successor_by_lease, {})

    def test_stale_66_then_retired_65_cannot_delay_active_1_cleanup(self) -> None:
        self.assertEqual(loopback.MAX_QUEUE, 64)
        state, coordinator, client, token = self._leased()
        # After the two confirmed predecessors retire, 65 plays remain held:
        # enough to overflow the release queue even with correct pruning.
        plays = [self._deliver_play(state, token, f"take{i}") for i in range(67)]
        self.assertEqual([play["id"] for play in plays], list(range(1, 68)))
        replies = {}
        for command_id in (66, 65, 1):
            self.assertTrue(self.client.play({"mode": "stop", "path_id": ""}).ok)
            replies[command_id] = self._execute_path(plays[command_id - 1])
            self.assertTrue(replies[command_id]["ok"])
        self.assertEqual(
            [replies[i]["camera_path"]["replaced_command_id"] for i in (66, 65, 1)], [0, 66, 65]
        )
        for command_id in (1, 66, 65):
            status, stored = state.store_result(replies[command_id], instance=INST_CLIENT)
            self.assertEqual(status, 200, stored)
            state.take_result(command_id, remove=True)
        self.assertEqual(self.client.m_CameraPathCommandId, 1)
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 64)
        self.assertIn("camera_path_release_unconfirmed", released.get("cleanup_degraded", []))
        first_batch = self._poll(state)["commands"]
        # Observe the actual release order, not just the broker's active marker.
        self.assertEqual(first_batch[0]["args"], {"command_id": 1})
        answer = self.client.release(1, first_batch[0]["id"])
        self.assertTrue(answer.matched)
        self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
        expected = [1, 67, *range(64, 1, -1)]
        self.assertEqual([row["args"]["command_id"] for row in first_batch], expected[:64])
        self.assertEqual(self._sent(state), self._releases(expected[-1]))
        self.assertEqual(state._camera_path_play_by_lease, {})
        self.assertEqual(state._camera_path_active_by_lease, {})
        self.assertEqual(state._camera_path_release_pending_leases, set())
        self.assertEqual(state._camera_path_successor_by_lease, {})
        self.assertEqual(self._sent(state), [])

    def test_new_play_does_not_drop_an_unexecuted_lower_id(self) -> None:
        state, coordinator, client, token = self._leased()
        pending = self._deliver_play(state, token, "pending")
        started = self._deliver_play(state, token, "started")
        self._report_play(state, started)
        self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {pending["id"], started["id"]}})
        status, released = coordinator.release(client, token)
        self.assertEqual(status, 200, released)
        self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 2)
        self.assertEqual(self._sent(state), self._releases(started["id"], pending["id"]))

    def test_missing_or_invalid_execution_echo_keeps_other_plays(self) -> None:
        for echo in (None, {}, {"command_id": True, "state": "playing", "replaced_command_id": 1}):
            with self.subTest(echo=echo):
                state, coordinator, client, token = self._leased()
                first = self._deliver_play(state, token, "first")
                second = self._deliver_play(state, token, "second")
                state.store_result({"id": second["id"], "ok": True, "camera_path": echo}, instance=INST_CLIENT)
                self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {first["id"], second["id"]}})
                status, released = coordinator.release(client, token)
                self.assertEqual(status, 200, released)
                self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 2)
                self.assertEqual(self._sent(state), self._releases(second["id"], first["id"]))

    def test_65_plays_prioritize_active_or_newest_and_retry_full_queue(self) -> None:
        self.assertEqual(loopback.MAX_QUEUE, 64)
        for known_active in (False, True):
            with self.subTest(known_active=known_active):
                self.client = _ClientBridge(self.bridge_source, self.Path)
                state, coordinator, client, token = self._leased()
                plays = []
                for index in range(65):
                    play = self._deliver_play(state, token, f"take{index}")
                    plays.append(play)
                    if known_active and index == 0:
                        self._report_play(state, play)
                active = plays[0] if known_active else plays[-1]
                if not known_active:
                    self.assertTrue(self._execute_path(active)["ok"])  # reply not back yet
                status, released = coordinator.release(client, token)
                self.assertEqual(status, 200, released)
                self.assertEqual(released["cleanup"]["camera_path_release_enqueued"], 64)
                self.assertIn("camera_path_release_unconfirmed", released.get("cleanup_degraded", []))
                expected = [active["id"]] + [play["id"] for play in reversed(plays) if play is not active]
                self.assertEqual(state._camera_path_play_by_lease, {"path-lease": {expected[-1]}})
                # Another lease-end attempt while still full must neither lie nor forget it.
                self.assertEqual(state.enqueue_camera_path_releases_for_lease("path-lease"), {"enqueued": 0, "failed": 1})
                first_batch = self._poll(state)["commands"]
                self.assertEqual([row["args"]["command_id"] for row in first_batch], expected[:64])
                first = first_batch[0]
                answer = self.client.release(first["args"]["command_id"], first["id"])
                self.assertTrue(answer.matched)
                self.assertEqual(_report(self.client.m_CameraPath)["state"], "aborted")
                # Draining the full queue queued the failed release for the next poll.
                self.assertEqual(self._sent(state), self._releases(expected[-1]))
                self.assertEqual(state._camera_path_play_by_lease, {})
                self.assertEqual(state._camera_path_release_pending_leases, set())
                self.assertEqual(state._camera_path_active_by_lease, {})
                self.assertEqual(self._sent(state), [])

    def test_camera_set_applies_at_its_dispatch_and_its_job_only_settles(self) -> None:
        bridge = _CameraSetBridge(self.bridge_source)
        post_now, result = bridge.dispatch("camera_set_focus")
        # The answer waits for the settle, so the dispatch posts nothing yet.
        self.assertFalse(post_now)
        self.assertEqual(
            bridge.log[:4],
            [
                ("bridge", "HasExclusiveJob"),
                ("bridge", "ApplyCameraSet", True),
                ("scripted", "SetActive", True),
                ("runner", "AddJob", 1),
            ],
        )
        [job] = bridge.jobs
        settle = bridge.constants["CAMERA_PHASE_SETTLE"]
        report = bridge.constants["CAMERA_PHASE_REPORT"]
        self.assertEqual(job.phase, settle)
        self.assertEqual((job.id, job.kind.text, job.camera_focus), (21, "camera_set", True))
        self.assertEqual(float(job.sample_start_s), 10.0)
        self.assertEqual(job.sample_s_target, _F32(0.15))
        self.assertEqual(job.deadline_s, _F32(10.0) + _F32(0.15) + bridge.constants["CAMERA_JOB_TIMEOUT_S"])
        self.assertEqual((job.tick_poll_sent, job.tick_poll_callback, job.tick_dispatch), (1, 2, 3))
        # The job only waits out the settle: no apply, no camera call, no
        # restore. Its first OnTick reads nothing (see the next test).
        frames = len(bridge.log)
        self.assertFalse(bridge.tick(10.05))
        self.assertEqual(job.phase, settle)
        self.assertFalse(bridge.tick(10.1))
        self.assertEqual(job.phase, settle)
        self.assertTrue(bridge.tick(10.25))
        self.assertEqual(job.phase, report)
        self.assertEqual(bridge.log[frames:], [])

    def test_camera_set_reads_no_camera_state_in_its_first_frame(self) -> None:
        # The apply's SetActive shows a frame late, as in
        # test_a_play_reads_no_camera_state_in_its_first_frame. The first
        # OnTick neither reads nor reports, whether its frame is short or long
        # enough for the whole settle; the report then reads the scripted
        # camera, the one BuildCameraResult answers view scripted for, where
        # an early one would have read it inactive and answered the player's.
        for first, ends in ((10.05, (10.1, 10.2)), (10.5, (10.55,))):
            with self.subTest(first=first):
                bridge = _CameraSetBridge(self.bridge_source)
                bridge.m_ActiveCam.shows_after = 2
                bridge.dispatch("camera_set")
                [job] = bridge.jobs
                self.assertFalse(bridge.tick(first))
                self.assertEqual(job.phase, bridge.constants["CAMERA_PHASE_SETTLE"])
                inactive = bridge.CameraReadError().text  # type: ignore[attr-defined]
                self.assertEqual(inactive, "camera_unavailable_inactive")
                *settling, last = ends
                for elapsed in settling:
                    self.assertFalse(bridge.tick(elapsed))
                self.assertTrue(bridge.tick(last))
                self.assertEqual(job.phase, bridge.constants["CAMERA_PHASE_REPORT"])
                self.assertEqual(bridge.CameraReadError().text, "")  # type: ignore[attr-defined]

    def test_camera_set_whose_camera_never_shows_reports_a_frame_later(self) -> None:
        # As before the wait, the job reports on the camera read inactive (the
        # player's view), now in the second OnTick after the dispatch.
        bridge = _CameraSetBridge(self.bridge_source)
        bridge.m_ActiveCam.shows_after = math.inf
        bridge.dispatch("camera_set")
        [job] = bridge.jobs
        self.assertFalse(bridge.tick(10.05))
        self.assertEqual(job.phase, bridge.constants["CAMERA_PHASE_SETTLE"])
        self.assertTrue(bridge.tick(10.1))
        self.assertEqual(job.phase, bridge.constants["CAMERA_PHASE_REPORT"])
        self.assertEqual(bridge.CameraReadError().text, "camera_unavailable_inactive")  # type: ignore[attr-defined]

    def test_a_failed_camera_set_apply_answers_at_once(self) -> None:
        bridge = _CameraSetBridge(self.bridge_source)
        bridge.apply_ok = False
        post_now, result = bridge.dispatch("camera_set", command_id=22)
        self.assertTrue(post_now)
        self.assertEqual((result.ok, result.error.text), (False, "camera_create_failed"))
        self.assertEqual(bridge.jobs, [])
        self.assertEqual(
            bridge.log,
            [("bridge", "HasExclusiveJob"), ("bridge", "ApplyCameraSet", False), ("bridge", "RestoreGameplay")],
        )
        bridge = _CameraSetBridge(self.bridge_source)
        bridge.exclusive = True
        post_now, result = bridge.dispatch("camera_set")
        self.assertEqual((post_now, result.ok, result.error.text), (True, False, "busy"))
        self.assertEqual(bridge.log, [("bridge", "HasExclusiveJob")])

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


class ExecutedApplyTest(unittest.TestCase):
    """ApplyCameraSet and ApplyFreeCamera of MCPClientBridge.c run on fakes:
    camera_set_focus sets Camera.SetFocus with its distance and blur on the
    camera it applies, before turning it on, and camera_set sets none (702f)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_source = _source(BRIDGE)

    def _applied(self, mode: str, focus: bool) -> _ApplyBridge:
        bridge = _ApplyBridge(self.bridge_source)
        mode_id = bridge.constants[f"CAMERA_MODE_{mode.upper()}"]
        bridge.validation = SimpleNamespace(
            ok=True,
            error=_Text.of(""),
            mode_id=mode_id,
            pos=(1.0, 2.0, 3.0),
            orient=(10.0, 0.0, 0.0),
            look_at=(4.0, 5.0, 6.0),
            fov=_F32(0.5),
        )
        job = _new_job()
        job.camera_focus = focus
        job.args = SimpleNamespace(
            cam_matrix=_floats([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0]),
            look_at=_floats([4.0, 5.0, 6.0]) if mode in ("lookat", "free") else None,
            focus_distance=_F32(2.5),
            focus_blur=_F32(0.75),
        )
        self.assertTrue(bridge.ApplyCameraSet(job))  # type: ignore[attr-defined]
        self.assertEqual(job.error.text, "")
        return bridge

    def test_each_mode_applies_its_pose_and_sets_focus_only_when_asked(self) -> None:
        create = ("engine", "CreateObject", "staticcamera", (1.0, 2.0, 3.0), True)
        poses = {
            "orient": [
                create,
                ("static", "SetPosition", (1.0, 2.0, 3.0)),
                ("static", "SetOrientation", (10.0, 0.0, 0.0)),
            ],
            "lookat": [create, ("static", "SetPosition", (1.0, 2.0, 3.0)), ("static", "LookAt", (4.0, 5.0, 6.0))],
            "matrix": [
                create,
                ("static", "SetTransform", ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 2.0, 3.0))),
            ],
            "free": [
                ("player", "DisableSimulation", True),
                ("free", "SetPosition", (1.0, 2.0, 3.0)),
                ("free", "LookAt", (4.0, 5.0, 6.0)),
            ],
        }
        for mode, pose in poses.items():
            for focus in (True, False):
                with self.subTest(mode=mode, focus=focus):
                    bridge = self._applied(mode, focus)
                    camera = bridge.free_cam if mode == "free" else bridge.static_cam
                    expected = [
                        ("bridge", "StopCameraPath"),
                        ("bridge", "FinishCameraHandoff"),
                        ("bridge", "SuppressGameplay"),
                        *pose,
                        (camera.name, "SetFOV", 0.5),
                    ]
                    if focus:
                        expected.append((camera.name, "SetFocus", 2.5, 0.75))
                    expected.append((camera.name, "SetActive", True))
                    self.assertEqual(bridge.log, expected)
                    self.assertIs(bridge.m_ActiveCam, camera)
                    self.assertEqual(bridge.m_ActiveCamOwned, mode != "free")
                    self.assertEqual(bridge.m_PlayerSimulationDisabled, mode == "free")


class ExecutedHudSetTest(unittest.TestCase):
    """DispatchHudSet of MCPClientBridge.c run on a fake HUD that keeps its
    hide flags: the setters change them and the answer reads them back (f427)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge_source = _source(BRIDGE)

    def test_visible_false_sets_both_hide_flags_and_true_clears_them(self) -> None:
        bridge = _HudBridge(self.bridge_source)
        both = {_HUD_FLAGS.HUD_HIDE, _HUD_FLAGS.QUICKBAR_HIDE}
        # Hide, hide again (the flags stay set), then show.
        for step, (visible, flags, answer) in enumerate(
            (
                (False, both, {"visible": False, "hud_hide": True, "quickbar_hide": True}),
                (False, both, {"visible": False, "hud_hide": True, "quickbar_hide": True}),
                (True, set(), {"visible": True, "hud_hide": False, "quickbar_hide": False}),
            )
        ):
            with self.subTest(step=step, visible=visible):
                bridge.log.clear()
                done, result = bridge.set(SimpleNamespace(visible=visible))
                self.assertEqual((done, result.ok, result.error.text), (True, True, ""))
                self.assertEqual(bridge.hud.flags, flags)  # type: ignore[attr-defined]
                self.assertEqual(vars(result.hud), answer)
                self.assertEqual(
                    bridge.log, [("hud", "ShowHudPlayer", visible), ("hud", "ShowQuickbarPlayer", visible)]
                )

    def test_without_a_hud_or_its_args_nothing_is_set(self) -> None:
        for missing in ("mission", "hud", "visibility", "args"):
            with self.subTest(missing=missing):
                bridge = _HudBridge(self.bridge_source)
                args: SimpleNamespace | None = SimpleNamespace(visible=False)
                if missing == "mission":
                    bridge.mission = None
                elif missing == "hud":
                    bridge.hud = object()
                elif missing == "visibility":
                    bridge.hud.visibility = None  # type: ignore[attr-defined]
                else:
                    args = None
                done, result = bridge.set(args)
                error = "bad_args" if missing == "args" else "hud_unavailable"
                self.assertEqual((done, result.ok, result.error.text, result.hud), (True, False, error, None))
                self.assertEqual(bridge.log, [])


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
            [
                ("string", "state"),
                ("float", "t_s"),
                ("float", "duration_s"),
                ("string", "path_id"),
                ("int", "command_id"),
                ("int", "replaced_command_id"),
            ],
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
            'result.error = "busy";',
            'cameraObject = g_Game.CreateObject("staticcamera", candidate.StartPosition(), true);',
            "cam = Camera.Cast(cameraObject);",
            "FinishCameraHandoff();",
            "SuppressGameplay();",
            "freeCam.SetActive(false);",
            "player.DisableSimulation(false);",
            "DeleteOwnedCamera();",
            "candidate.Begin();",
            "candidate.Apply(cam);",
            "cam.SetActive(true);",
            "m_ActiveCam = cam;",
            "m_CameraPath = candidate;",
            "m_CameraPathCam = cam;",
            "m_CameraPathCommandId = command.id;",
            "m_CameraPathPlayTick = m_Tick;",
            "m_CameraPath.Fill(result.camera_path);",
        ]
        positions = [body.index(step) for step in order]
        self.assertEqual(positions, sorted(positions))
        # Applied here: no job is queued for any mode.
        self.assertNotIn("AddJob", body)
        self.assertNotIn("MCPJob", body)
        query = [
            "if (!MCPCameraPath.IsValidPathId(command.args.path_id))",
            'result.error = "path_not_found";',
            "StopCameraPath();",
            "m_CameraPath.Fill(result.camera_path);",
        ]
        # The play answers with its own Fill: the query's checks start after it.
        tail = body[body.index('if (mode != "state" && mode != "stop")') :]
        positions = [tail.index(step) for step in query]
        self.assertEqual(positions, sorted(positions))

    def test_the_path_moves_per_frame_on_the_scripted_camera(self) -> None:
        # What the frame and the play do runs in ExecutedDispatchTest; here, that
        # OnTick moves the path before the job runner and that nothing
        # interpolates in the engine or blocks the tick.
        tick = _flat(_method_body(self.bridge, "void OnTick(float timeslice)"))
        self.assertLess(tick.index("TickCameraPath(timeslice);"), tick.index("m_JobRunner.Tick(timeslice, this);"))
        frame = _flat(_method_body(self.bridge, "protected void TickCameraPath(float timeslice)"))
        play = _flat(_method_body(self.bridge, "protected bool DispatchCameraPathPlay("))
        # No job runs a play (d026): nothing routes, processes or posts one.
        self.assertNotIn("ProcessCameraPathPlayJob", self.bridge)
        self.assertNotIn('job.kind == "camera_path_play"', _flat(self.bridge))
        self.assertNotIn('kind = "camera_path_play"', _flat(self.bridge))
        for text in (_code(self.path), play, frame):
            self.assertNotIn("InterpolateTo", text)
            self.assertNotIn("_now(", text)

    def test_camera_set_and_restore_end_a_playing_path(self) -> None:
        apply = _flat(_method_body(self.bridge, "protected bool ApplyCameraSet(MCPJob job)"))
        self.assertLess(apply.index("StopCameraPath();"), apply.index("SuppressGameplay();"))
        # RestoreGameplay does not stop a path: the vehicle jobs call it, and no
        # job changes anything (d026). Its callers outside a job stop it first.
        restore = _flat(_method_body(self.bridge, "protected void RestoreGameplay()"))
        self.assertNotIn("StopCameraPath", restore)
        dispatch = _flat(_method_body(self.bridge, "protected void Dispatch(MCPCommand command)"))
        branch = dispatch[dispatch.index('else if (command.cmd == "restore_gameplay")') :]
        self.assertLess(branch.index("StopCameraPath();"), branch.index("RestoreGameplay();"))
        shutdown = _flat(_method_body(self.bridge, "void Shutdown()"))
        self.assertLess(shutdown.index("StopCameraPath();"), shutdown.index("RestoreGameplay();"))
        # ApplyCameraSet takes the job's fields but runs from DispatchCameraSet
        # only, its one caller: the jobs are the Process methods.
        self.assertEqual(_code(self.bridge).count("ApplyCameraSet("), 2)
        self.assertIn("ApplyCameraSet(", _code(_method_body(self.bridge, "protected bool DispatchCameraSet(")))
        jobs = re.findall(r"protected bool (Process\w+)\(MCPJob job\)", _code(self.bridge))
        restoring = []
        for name in jobs:
            body = _code(_method_body(self.bridge, f"protected bool {name}(MCPJob job)"))
            with self.subTest(job=name):
                self.assertNotIn("StopCameraPath", body)
            if "RestoreGameplay();" in body:
                restoring.append(name)
        # The vehicle get-in, and the drive probe twice, its fixture included.
        self.assertIn("ProcessVehicleGetInClientPrep", restoring)
        self.assertIn("ProcessDriveProbeClientPrep", restoring)
        stop = _flat(_method_body(self.bridge, "protected void StopCameraPath()"))
        self.assertEqual(stop, "if (m_CameraPath) { m_CameraPath.Abort(); } m_CameraPathCam = null;")
        abort = _flat(_method_body(self.path, "void Abort()"))
        self.assertEqual(abort, 'if (m_State == "playing") { m_State = "aborted"; }')

    def test_focus_is_set_only_for_camera_set_focus(self) -> None:
        dispatch = _flat(_method_body(self.bridge, "protected bool DispatchCameraSet("))
        self.assertIn('bool focus = command.cmd == "camera_set_focus";', dispatch)
        self.assertLess(dispatch.index("!IsValidCameraFocus(command.args)"), dispatch.index("HasExclusiveJob()"))
        self.assertIn("job.camera_focus = focus;", dispatch)
        # The apply, focus included, runs in the dispatch; the job only settles.
        steps = ["job.camera_focus = focus;", "bool applied = ApplyCameraSet(job);", "m_JobRunner.AddJob(job);"]
        positions = [dispatch.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("ApplyCameraSet", _code(_method_body(self.bridge, "protected bool ProcessCameraSetJob(MCPJob job)")))
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
