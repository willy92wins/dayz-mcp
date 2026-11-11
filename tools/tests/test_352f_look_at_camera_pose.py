"""352f (with c616 and cc75): player_look_at names third_person by the camera in use.

PoseError answers third_person when the IsInThirdPerson flag says so, and also
when the camera the engine picked for the player is a third-person one (every
on-foot third-person camera inherits DayZPlayerCamera3rdPerson). When the
client job's time runs out, a look still running ends with the code PoseError
gives for its player, and the report's pose says it; otherwise it ends in
"deadline" with the pose Begin set, as before.

The bodies are the real ones of MCP_LookAt.c, translated and run on fakes
(enforce_subset_helpers). The deadline runs as MCP_PostJobTimeout writes it:
the release of its player_look_at branch, taken from MCPClientBridge.c.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceString, method_body, translate

_SCRIPTS = addon_root() / "scripts"
LOOK_AT = (_SCRIPTS / "4_World" / "MCP_LookAt.c").read_text(encoding="utf-8")
BRIDGE = (_SCRIPTS / "5_Mission" / "MCPClientBridge.c").read_text(encoding="utf-8")
POSE_ERROR = "protected static string PoseError(PlayerBase player)"
RELEASE = "static void Release(string why)"
DISABLE = "static void Disable(PlayerBase player)"
GENERATION = "static int Generation()"
AT_DEADLINE = "static void ReleaseAtDeadline()"
JOB_TIMEOUT = "override void MCP_PostJobTimeout(MCPJob job)"
LOOK_AT_BRANCH = 'if (job.kind == "player_look_at")'
DEADLINE_GATE = "if (MCPLookAtControl.Generation() == job.generation)"
AT_DEADLINE_JOB = "void AtDeadline(MCPJob job)"
CLASS_TYPES = (
    "PlayerBase",
    "HumanInputController",
    "HumanMovementState",
    "ActionManagerBase",
    "DayZPlayerCamera",
    "MCPJob",
)
MEMBERS = (
    "s_Active",
    "s_Finished",
    "s_Converged",
    "s_Termination",
    "s_Pose",
    "s_Generation",
    "s_Player",
)
ERECT = 0
CROUCH = 1


def _s(text: str) -> EnforceString:
    return EnforceString.of(text)


class _Camera:
    def IsInherited(self, typename: type) -> bool:
        return isinstance(self, typename)


class DayZPlayerCamera3rdPerson(_Camera):
    pass


class DayZPlayerCamera3rdPersonErc(DayZPlayerCamera3rdPerson):
    pass


class DayZPlayerCamera1stPerson(_Camera):
    pass


class HumanMovementState:
    def __init__(self) -> None:
        self.m_iMovement = 0
        self.m_iStanceIdx = ERECT


class _Input:
    def __init__(self) -> None:
        self.overrides: list[tuple[str, object, float]] = []

    def CameraIsFreeLook(self) -> bool:
        return False

    def CameraIsTracking(self) -> bool:
        return False

    def OverrideAimChangeX(self, kind: object, value: float) -> None:
        self.overrides.append(("x", kind, value))

    def OverrideAimChangeY(self, kind: object, value: float) -> None:
        self.overrides.append(("y", kind, value))


class _Player:
    """An idle, erect player; only the view flag, camera and consciousness vary."""

    def __init__(
        self, *, third: bool = False, camera: object = None, unconscious: bool = False
    ) -> None:
        self.third = third
        self.camera = camera
        self.unconscious = unconscious
        self.hic = _Input()

    def IsAlive(self) -> bool:
        return True

    def IsUnconscious(self) -> bool:
        return self.unconscious

    def IsRestrained(self) -> bool:
        return False

    def IsInVehicle(self) -> bool:
        return False

    def IsInThirdPerson(self) -> bool:
        return self.third

    def GetCurrentCamera(self) -> object:
        return self.camera

    def IsRaised(self) -> bool:
        return False

    def GetCommand_Action(self) -> None:
        return None

    def GetCommandModifier_Action(self) -> None:
        return None

    def GetActionManager(self) -> None:
        return None

    def GetInputController(self) -> _Input:
        return self.hic

    def GetMovementState(self, _state: HumanMovementState) -> None:
        pass


def _deadline_source() -> str:
    """MCP_PostJobTimeout's player_look_at release, as a method of its own."""
    body = method_body(BRIDGE, JOB_TIMEOUT)
    if body.count(LOOK_AT_BRANCH) != 1 or body.count(DEADLINE_GATE) != 1:
        raise AssertionError("the player_look_at deadline release is not found exactly once")
    branch_end = body.index(LOOK_AT_BRANCH) + len(LOOK_AT_BRANCH)
    start = body.index(DEADLINE_GATE)
    if body[branch_end:start].strip() != "{":
        raise AssertionError("the release is not the first statement of the look_at branch")
    depth = 0
    for index in range(body.index("{", start), len(body)):
        depth += (body[index] == "{") - (body[index] == "}")
        if depth == 0:
            return AT_DEADLINE_JOB + "\n{\n" + body[start : index + 1] + "\n}\n"
    raise AssertionError("unterminated release")


class _LookAt:
    """PoseError, Release, Disable, Generation and the job timeout's release
    over one namespace, MCPLookAtControl's statics as its globals."""

    def __init__(self) -> None:
        ns: dict = {}

        class Control:
            def __getattr__(self, name: str) -> object:
                return ns[name]

        ns.update(
            _S=EnforceString,
            MCPLookAtControl=Control(),
            HumanMovementState=HumanMovementState,
            DayZPlayerCamera3rdPerson=DayZPlayerCamera3rdPerson,
            DayZPlayerCamera1stPerson=DayZPlayerCamera1stPerson,
            DayZPlayerConstants=SimpleNamespace(STANCEIDX_ERECT=ERECT, STANCEIDX_CROUCH=CROUCH),
            HumanInputControllerOverrideType=SimpleNamespace(DISABLED="DISABLED"),
            MCPWeaponControl=SimpleNamespace(IsBusy=lambda: False),
            # Release tells the server's copy of the look (33c5, c616, cc75);
            # test_player_look_mirror.py runs that part.
            EndServerLook=lambda converged: None,
            s_Active=False,
            s_Finished=False,
            s_Converged=False,
            s_Termination=_s(""),
            s_Pose=_s(""),
            s_Generation=0,
            s_Player=None,
        )
        signatures = [POSE_ERROR, RELEASE, DISABLE, GENERATION]
        # Before 352f the timeout called Release itself.
        if AT_DEADLINE in LOOK_AT:
            signatures.append(AT_DEADLINE)
        for signature in signatures:
            translate(LOOK_AT, signature, ns, class_types=CLASS_TYPES, members=MEMBERS)
        translate(_deadline_source(), AT_DEADLINE_JOB, ns, class_types=CLASS_TYPES)
        self.ns = ns

    def pose_error(self, player: _Player) -> str:
        return self.ns["PoseError"](player).text

    def look(self, player: _Player | None) -> None:
        """A look running for player, as Begin leaves it."""
        self.ns.update(
            s_Active=True,
            s_Finished=False,
            s_Converged=False,
            s_Termination=_s(""),
            s_Pose=_s("first_idle"),
            s_Player=player,
        )

    def deadline(self) -> tuple[str, str]:
        self.ns["AtDeadline"](SimpleNamespace(generation=self.ns["s_Generation"]))
        return self.ns["s_Termination"].text, self.ns["s_Pose"].text


class PoseErrorCameraTest(unittest.TestCase):
    def test_a_third_person_camera_is_third_person_whatever_the_flag(self) -> None:
        player = _Player(camera=DayZPlayerCamera3rdPersonErc())
        self.assertEqual(_LookAt().pose_error(player), "third_person")

    def test_the_flag_still_says_third_person(self) -> None:
        for camera in (None, DayZPlayerCamera1stPerson()):
            with self.subTest(camera=type(camera).__name__):
                player = _Player(third=True, camera=camera)
                self.assertEqual(_LookAt().pose_error(player), "third_person")

    def test_a_first_person_or_missing_camera_refuses_nothing_new(self) -> None:
        for camera in (None, DayZPlayerCamera1stPerson()):
            with self.subTest(camera=type(camera).__name__):
                self.assertEqual(_LookAt().pose_error(_Player(camera=camera)), "")


class LookAtDeadlineTest(unittest.TestCase):
    def test_a_refused_pose_at_the_deadline_names_the_end_and_the_pose(self) -> None:
        for code, player in (
            ("third_person", _Player(camera=DayZPlayerCamera3rdPersonErc())),
            ("third_person", _Player(third=True)),
            ("player_unconscious", _Player(unconscious=True)),
        ):
            with self.subTest(code=code, flag=player.third):
                look_at = _LookAt()
                look_at.look(player)
                self.assertEqual(look_at.deadline(), (code, code))
                self.assertEqual(
                    (look_at.ns["s_Active"], look_at.ns["s_Finished"], look_at.ns["s_Converged"]),
                    (False, True, False),
                )
                self.assertEqual([axis for axis, _kind, _value in player.hic.overrides], ["x", "y"])

    def test_a_good_pose_or_no_player_still_ends_in_deadline(self) -> None:
        for player in (_Player(), None):
            with self.subTest(player=player is not None):
                look_at = _LookAt()
                look_at.look(player)
                self.assertEqual(look_at.deadline(), ("deadline", "first_idle"))
                self.assertIs(look_at.ns["s_Finished"], True)

    def test_a_look_that_already_ended_keeps_its_report(self) -> None:
        look_at = _LookAt()
        look_at.look(_Player(camera=DayZPlayerCamera3rdPersonErc()))
        look_at.ns.update(s_Active=False, s_Finished=True, s_Converged=True, s_Termination=_s("converged"))
        self.assertEqual(look_at.deadline(), ("converged", "first_idle"))
        self.assertIs(look_at.ns["s_Converged"], True)


if __name__ == "__main__":
    unittest.main()
