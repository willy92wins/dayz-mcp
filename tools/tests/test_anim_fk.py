"""inbox fb-20260928-141234-7695: anim_fk, an offline forward-kinematics command for vanilla DayZ player animations.

The command (tools/dev/anim_fk.py, with its pure part in tools/dev/anim_fk_math.py) copies a skeleton .xob, an
ikpose .anm and one or more action .anm out of the vanilla tree into a work folder, has DayZATool v1.3 turn the
copies into SEModel and SEAnim, computes the forward kinematics and prints one JSON object (schema anim_fk_v1). It
is written from scratch: the script the ticket points to carries no license and none of its code is used.

No test here runs DayZATool or reads P:\\DZ. Each test's --dz folder holds synthetic SEAnim and SEModel bytes
(tests/anim_fk_helpers.py), and a fake DayZATool, launched with sys.executable through a patched
anim_fk.dayzatool_argv, writes them out next to the copy the way DayZATool v1.3 does. roundtrip_bone.seanim is the
one DayZATool output kept as a fixture, recorded from synthetic data (fixtures/anim_fk/PROVENANCE.txt).

The expected numbers are closed forms on a 5-bone skeleton: Root; RightHand at (5, 0, 0) and LeftHand at
(-5, 0, 0) under Root; RightHand_Dummy under RightHand; LeftHandIKTarget under RightHand_Dummy. The ikpose puts the
item frame at (0, 0, 3) from RightHand, turned 90 degrees about X, and the left wrist target at (-9, -3, 0) in that
frame, so in the item frame RightHand sits at (0, -3, 0) and LeftHand at (-10, -3, 0): 10 cm apart and 1 cm off the
target. When RightHand turns by theta about Z the left wrist is sqrt(181 - 180 cos theta) cm off the target: 1.0 cm
at rest, 3.834 cm at 22.5 degrees, 7.330 cm at 45 degrees.
- "still" moves and turns Root only, so nothing changes in the item frame (this checks p = p_parent + R_parent p).
- "reach" keys LeftHand at the first and the last frame (linear positions, the last index gives the last key).
- "turn" keys RightHand at frames 1 and 3 of 5 and stores the 45-degree key negated: frame 0 holds the first key,
  frame 2 is 22.5 degrees only with the hemisphere fix (18.64 cm off without it), frame 4 holds the last key.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from tests.anim_fk_helpers import seanim_bytes, semodel_bytes, write_fake_dayzatool

TOOLS_DIR = Path(__file__).resolve().parents[1]
DEV_DIR = TOOLS_DIR / "dev"
TOOL = DEV_DIR / "anim_fk.py"
MATH = DEV_DIR / "anim_fk_math.py"
DOC = TOOLS_DIR.parent / "docs" / "anim-fk.md"
ROUNDTRIP = Path(__file__).resolve().parent / "fixtures" / "anim_fk" / "roundtrip_bone.seanim"

SKELETON_REL = "characters/bodies/player_testing.xob"
IKPOSE_REL = "anims/anm/player/ik/test/grip.anm"
STILL_REL = "anims/anm/player/actions/items/still.anm"
REACH_REL = "anims/anm/player/actions/items/reach.anm"
TURN_REL = "anims/anm/player/actions/items/turn.anm"

TOL = 1e-4
S45, C45 = math.sin(math.radians(45.0)), math.cos(math.radians(45.0))
S22, C22 = math.sin(math.radians(22.5)), math.cos(math.radians(22.5))
IDENTITY = (0.0, 0.0, 0.0, 1.0)
RX_90 = (S45, 0.0, 0.0, C45)
RX_MINUS_90 = (-S45, 0.0, 0.0, C45)
RY_90 = (0.0, S45, 0.0, C45)
RZ_45_NEGATED = (0.0, 0.0, -S22, -C22)

SKELETON = [
    ("Root", -1, (0.0, 0.0, 0.0), IDENTITY),
    ("RightHand", 0, (5.0, 0.0, 0.0), IDENTITY),
    ("LeftHand", 0, (-5.0, 0.0, 0.0), IDENTITY),
    ("RightHand_Dummy", 1, (0.0, 0.0, 0.0), IDENTITY),
    ("LeftHandIKTarget", 3, (0.0, 0.0, 0.0), IDENTITY),
]
# Frame 1 of the ikpose holds values that must not be read: the grip is the first key.
IKPOSE = [
    ("RightHand_Dummy", [(0, (0.0, 0.0, 3.0)), (1, (0.0, 0.0, 99.0))], [(0, RX_90), (1, IDENTITY)]),
    ("LeftHandIKTarget", [(0, (-9.0, -3.0, 0.0)), (1, (-50.0, 0.0, 0.0))], []),
]
STILL = [("Root", [(0, (0.0, 0.0, 0.0)), (3, (3.0, 0.0, 0.0))], [(0, IDENTITY), (3, RY_90)])]
REACH = [("LeftHand", [(0, (-5.0, 0.0, 0.0)), (3, (-8.0, 0.0, 0.0))], [])]
TURN = [("RightHand", [], [(1, IDENTITY), (3, RZ_45_NEGATED)])]

TOP_KEYS = {"schema", "units", "scale", "skeleton", "ikpose", "limits", "segments"}
SEGMENT_KEYS = {
    "anm", "duration_s", "frame_count", "framerate", "notes", "blends", "ik", "bones", "wrist_span_cm",
    "left_wrist_offset_cm",
}


def offset_at(theta_deg: float) -> float:
    return math.sqrt(181.0 - 180.0 * math.cos(math.radians(theta_deg)))


def good_tree() -> dict[str, bytes]:
    return {
        SKELETON_REL: semodel_bytes(SKELETON),
        IKPOSE_REL: seanim_bytes(IKPOSE, 2),
        STILL_REL: seanim_bytes(STILL, 4, notes=[(2, "mark")]),
        REACH_REL: seanim_bytes(REACH, 4),
        TURN_REL: seanim_bytes(TURN, 5),
    }


_MODULE = None


def load_tool():
    """tools/dev/anim_fk.py as `python tools/dev/anim_fk.py` sees it: its own folder first on sys.path (only while
    the two modules import, so nothing else in the suite resolves through tools/dev)."""
    global _MODULE
    if _MODULE is None:
        sys.path.insert(0, str(DEV_DIR))
        try:
            importlib.import_module("anim_fk_math")
            _MODULE = importlib.import_module("anim_fk")
        finally:
            sys.path.remove(str(DEV_DIR))
    return _MODULE


class AnimFkCase(unittest.TestCase):
    """A synthetic vanilla tree, a fake DayZATool and an environment without DZ_ROOT or DAYZATOOL."""

    def setUp(self) -> None:
        self.tool = load_tool()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.dz = self.root / "dz"
        self.work = self.root / "work"
        self.counter = self.root / "launches.txt"
        self.fake = write_fake_dayzatool(self.root)
        self.write_tree()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DZ_ROOT", None)
        os.environ.pop("DAYZATOOL", None)
        fake, counter = str(self.fake), str(self.counter)
        self.argv_patch = mock.patch.object(
            self.tool, "dayzatool_argv", lambda exe, flag, path: [sys.executable, "-B", fake, counter, flag, path]
        )
        self.argv_patch.start()
        self.addCleanup(self.argv_patch.stop)

    def write_tree(self) -> None:
        for rel, data in good_tree().items():
            self.put(rel, data)

    def put(self, rel: str, data: bytes) -> None:
        path = self.dz / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def base_args(self, *anm: str, ikpose: str = IKPOSE_REL) -> list[str]:
        args = ["--dz", str(self.dz), "--dayzatool", str(self.fake), "--work", str(self.work), "--ikpose", ikpose]
        for rel in anm or (STILL_REL,):
            args += ["--anm", rel]
        return args

    def run_tool(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.tool.main(args)
            except SystemExit as exc:  # argparse reports a usage error this way
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def ok_json(self, args: list[str]) -> dict:
        code, out, err = self.run_tool(args)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def launches(self) -> int:
        return len(self.counter.read_text(encoding="utf-8").splitlines()) if self.counter.exists() else 0

    def assertVec(self, got, expected, tol: float = TOL) -> None:
        self.assertEqual(len(got), len(expected), f"{got} != {expected}")
        for g, e in zip(got, expected):
            self.assertAlmostEqual(g, e, delta=tol, msg=f"{got} != {expected}")

    def assertQuat(self, got, expected, tol: float = TOL) -> None:
        """q and -q are the same rotation."""
        if sum(g * e for g, e in zip(got, expected)) < 0:
            expected = tuple(-c for c in expected)
        self.assertVec(got, expected, tol)


class PositiveTest(AnimFkCase):
    def test_the_json_matches_the_closed_forms(self) -> None:
        args = self.base_args(STILL_REL, REACH_REL, TURN_REL) + ["--bone", "RightHand", "--bone", "LeftHand"]
        doc = self.ok_json(args)
        self.assertEqual(doc["schema"], "anim_fk_v1")
        self.assertEqual(doc["units"], {"position": "cm", "time": "s", "quaternion": "xyzw"})
        self.assertEqual(doc["scale"], 100)
        self.assertEqual(doc["skeleton"], {"path": SKELETON_REL, "bone_count": 5, "root": "Root"})
        self.assertEqual(doc["ikpose"]["path"], IKPOSE_REL)
        self.assertVec(doc["ikpose"]["item_from_right_hand"]["pos_cm"], (0.0, 0.0, 3.0))
        self.assertQuat(doc["ikpose"]["item_from_right_hand"]["rot_xyzw"], RX_90)
        self.assertVec(doc["ikpose"]["left_target_in_item_cm"], (-9.0, -3.0, 0.0))
        self.assertEqual(len(doc["limits"]), 3)
        self.assertTrue(all(isinstance(text, str) and text for text in doc["limits"]))
        self.assertEqual([segment["anm"] for segment in doc["segments"]], [STILL_REL, REACH_REL, TURN_REL])
        still, reach, turn = doc["segments"]

        self.assertEqual((still["frame_count"], still["framerate"]), (4, 30.0))
        self.assertAlmostEqual(still["duration_s"], 0.1, delta=TOL)
        self.assertEqual(still["notes"], [{"frame": 2, "name": "mark"}])
        self.assertIsNone(still["blends"])
        self.assertIs(still["ik"], False)
        self.assertVec(still["wrist_span_cm"]["per_frame"], [10.0] * 4)
        self.assertAlmostEqual(still["wrist_span_cm"]["min"], 10.0, delta=TOL)
        self.assertAlmostEqual(still["wrist_span_cm"]["max"], 10.0, delta=TOL)
        self.assertVec(still["left_wrist_offset_cm"]["per_frame"], [1.0] * 4)
        self.assertAlmostEqual(still["left_wrist_offset_cm"]["max"], 1.0, delta=TOL)
        for bone, pos in (("RightHand", (0.0, -3.0, 0.0)), ("LeftHand", (-10.0, -3.0, 0.0))):
            frames = still["bones"][bone]["frames"]
            self.assertEqual([frame["i"] for frame in frames], [0, 1, 2, 3])
            for frame in frames:
                self.assertVec(frame["pos_cm"], pos)
                self.assertQuat(frame["rot_xyzw"], RX_MINUS_90)

        self.assertEqual(reach["notes"], [])
        self.assertVec(reach["wrist_span_cm"]["per_frame"], [10.0, 11.0, 12.0, 13.0])
        self.assertAlmostEqual(reach["wrist_span_cm"]["min"], 10.0, delta=TOL)
        self.assertAlmostEqual(reach["wrist_span_cm"]["max"], 13.0, delta=TOL)
        self.assertVec(reach["left_wrist_offset_cm"]["per_frame"], [1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(reach["left_wrist_offset_cm"]["max"], 4.0, delta=TOL)
        for frame, x in zip(reach["bones"]["LeftHand"]["frames"], (-10.0, -11.0, -12.0, -13.0)):
            self.assertVec(frame["pos_cm"], (x, -3.0, 0.0))

        self.assertEqual((turn["frame_count"], turn["framerate"]), (5, 30.0))
        self.assertAlmostEqual(turn["duration_s"], 4.0 / 30.0, delta=TOL)
        self.assertVec(turn["wrist_span_cm"]["per_frame"], [10.0] * 5)
        self.assertVec(turn["left_wrist_offset_cm"]["per_frame"],
                       [offset_at(0.0), offset_at(0.0), offset_at(22.5), offset_at(45.0), offset_at(45.0)])
        self.assertAlmostEqual(turn["left_wrist_offset_cm"]["max"], offset_at(45.0), delta=TOL)
        left = turn["bones"]["LeftHand"]["frames"][3]
        self.assertVec(left["pos_cm"], (-10.0 * C45, -3.0, -10.0 * S45))
        # The item turn applied after the hand: Rx(-90) * Rz(-45), Hamilton product.
        self.assertQuat(left["rot_xyzw"], (-S45 * C22, -S45 * S22, -C45 * S22, C45 * C22))
        for frame in turn["bones"]["RightHand"]["frames"]:
            self.assertVec(frame["pos_cm"], (0.0, -3.0, 0.0))
            self.assertQuat(frame["rot_xyzw"], RX_MINUS_90)

    def test_the_output_has_exactly_the_planned_keys_and_types(self) -> None:
        doc = self.ok_json(self.base_args(STILL_REL) + ["--bone", "LeftHand"])
        self.assertEqual(set(doc), TOP_KEYS)
        self.assertEqual(set(doc["units"]), {"position", "time", "quaternion"})
        self.assertEqual(set(doc["skeleton"]), {"path", "bone_count", "root"})
        self.assertEqual(set(doc["ikpose"]), {"path", "item_from_right_hand", "left_target_in_item_cm"})
        self.assertEqual(set(doc["ikpose"]["item_from_right_hand"]), {"pos_cm", "rot_xyzw"})
        self.assertIsInstance(doc["skeleton"]["bone_count"], int)
        (segment,) = doc["segments"]
        self.assertEqual(set(segment), SEGMENT_KEYS)
        self.assertIsInstance(segment["frame_count"], int)
        self.assertIsInstance(segment["duration_s"], float)
        self.assertIsInstance(segment["framerate"], float)
        self.assertEqual(set(segment["wrist_span_cm"]), {"min", "max", "per_frame"})
        self.assertEqual(set(segment["left_wrist_offset_cm"]), {"max", "per_frame"})
        self.assertEqual(len(segment["wrist_span_cm"]["per_frame"]), segment["frame_count"])
        self.assertEqual(len(segment["left_wrist_offset_cm"]["per_frame"]), segment["frame_count"])
        self.assertEqual(set(segment["notes"][0]), {"frame", "name"})
        self.assertEqual(set(segment["bones"]), {"LeftHand"})
        self.assertEqual(set(segment["bones"]["LeftHand"]), {"frames"})
        frames = segment["bones"]["LeftHand"]["frames"]
        self.assertEqual(len(frames), segment["frame_count"])
        self.assertEqual(set(frames[0]), {"i", "pos_cm", "rot_xyzw"})
        self.assertIsInstance(frames[0]["i"], int)
        self.assertEqual((len(frames[0]["pos_cm"]), len(frames[0]["rot_xyzw"])), (3, 4))

    def test_without_bones_there_are_no_curves_but_the_span_stays(self) -> None:
        doc = self.ok_json(self.base_args(STILL_REL))
        (segment,) = doc["segments"]
        self.assertEqual(segment["bones"], {})
        self.assertVec(segment["wrist_span_cm"]["per_frame"], [10.0] * 4)
        self.assertEqual(segment["notes"], [{"frame": 2, "name": "mark"}])

    def test_the_environment_stands_in_for_dz_and_dayzatool(self) -> None:
        os.environ["DZ_ROOT"] = str(self.dz)
        os.environ["DAYZATOOL"] = str(self.fake)
        code, out, err = self.run_tool(["--work", str(self.work), "--ikpose", IKPOSE_REL, "--anm", STILL_REL])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["schema"], "anim_fk_v1")

    def test_a_clip_past_255_frames_reads_two_byte_frame_numbers(self) -> None:
        rel = "anims/anm/player/actions/items/long.anm"
        self.put(rel, seanim_bytes([("LeftHand", [(0, (-5.0, 0.0, 0.0)), (299, (-8.0, 0.0, 0.0))], [])], 300,
                                   notes=[(280, "late")]))
        doc = self.ok_json(self.base_args(rel))
        (segment,) = doc["segments"]
        self.assertEqual(segment["frame_count"], 300)
        self.assertAlmostEqual(segment["duration_s"], 299.0 / 30.0, delta=TOL)
        self.assertEqual(segment["notes"], [{"frame": 280, "name": "late"}])
        span = segment["wrist_span_cm"]["per_frame"]
        self.assertEqual(len(span), 300)
        self.assertVec([span[0], span[100], span[299]], [10.0, 10.0 + 300.0 / 299.0, 13.0])

    def test_the_recorded_dayzatool_output_reads(self) -> None:
        # DayZATool v1.3 wrote this from a synthetic clip. Its one bone is not in the skeleton, so it moves nothing.
        rel = "anims/anm/player/actions/items/roundtrip.anm"
        self.put(rel, ROUNDTRIP.read_bytes())
        doc = self.ok_json(self.base_args(rel))
        (segment,) = doc["segments"]
        self.assertEqual((segment["frame_count"], segment["framerate"]), (3, 30.0))
        self.assertAlmostEqual(segment["duration_s"], 2.0 / 30.0, delta=TOL)
        self.assertEqual(segment["notes"], [])
        self.assertVec(segment["wrist_span_cm"]["per_frame"], [10.0] * 3)

    def test_dayzatool_style_mesh_data_after_the_bones_is_ignored(self) -> None:
        # DayZATool writes data presence 0x07 and its mesh and material data after the bone records.
        self.put(SKELETON_REL, semodel_bytes(SKELETON, trailing=b"\x5a" * 64))
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(doc["skeleton"]["bone_count"], 5)
        self.assertVec(doc["segments"][0]["wrist_span_cm"]["per_frame"], [10.0] * 4)


class ReadFailureTest(AnimFkCase):
    """Exit 5: an SEAnim or SEModel the readers refuse, or an ikpose without its two bones; stdout stays empty."""

    def test_each_unreadable_file_answers_5(self) -> None:
        good_clip = seanim_bytes(STILL, 4)
        cases = {
            "seanim_magic": (STILL_REL, seanim_bytes(STILL, 4, magic=b"SEAnix")),
            "seanim_version": (STILL_REL, seanim_bytes(STILL, 4, version=2)),
            "seanim_anim_type": (STILL_REL, seanim_bytes(STILL, 4, anim_type=1)),
            "seanim_truncated_header": (STILL_REL, good_clip[:18]),
            "seanim_truncated_keys": (STILL_REL, good_clip[:-6]),
            "semodel_magic": (SKELETON_REL, semodel_bytes(SKELETON, magic=b"SEModex")),
            "semodel_version": (SKELETON_REL, semodel_bytes(SKELETON, version=2)),
            "semodel_parent_after_child": (
                SKELETON_REL, semodel_bytes([SKELETON[0], ("RightHand", 2, (5.0, 0.0, 0.0), IDENTITY)] + SKELETON[2:])
            ),
            "semodel_two_roots": (
                SKELETON_REL, semodel_bytes([SKELETON[0], ("RightHand", -1, (5.0, 0.0, 0.0), IDENTITY)] + SKELETON[2:])
            ),
            "semodel_truncated_records": (SKELETON_REL, semodel_bytes(SKELETON)[:-20]),
            "ikpose_without_left_target": (IKPOSE_REL, seanim_bytes(IKPOSE[:1], 2)),
            "ikpose_without_dummy": (IKPOSE_REL, seanim_bytes(IKPOSE[1:], 2)),
        }
        for label, (rel, data) in cases.items():
            with self.subTest(case=label):
                self.write_tree()
                self.put(rel, data)
                code, out, _err = self.run_tool(self.base_args(STILL_REL))
                self.assertEqual((code, out), (5, ""))


class MissingBoneTest(AnimFkCase):
    """Exit 6: a bone asked for, or one the span needs, is not in the skeleton."""

    def test_an_unknown_bone_answers_6(self) -> None:
        code, out, _err = self.run_tool(self.base_args(STILL_REL) + ["--bone", "NoSuchBone"])
        self.assertEqual((code, out), (6, ""))

    def test_a_skeleton_without_the_left_hand_answers_6(self) -> None:
        self.put(SKELETON_REL, semodel_bytes([
            SKELETON[0], SKELETON[1], ("RightHand_Dummy", 1, (0.0, 0.0, 0.0), IDENTITY),
            ("LeftHandIKTarget", 2, (0.0, 0.0, 0.0), IDENTITY),
        ]))
        code, out, _err = self.run_tool(self.base_args(STILL_REL))
        self.assertEqual((code, out), (6, ""))


class ArgumentTest(AnimFkCase):
    """Exit 2: bad arguments, all checked before the first DayZATool launch."""

    def test_each_bad_argument_answers_2(self) -> None:
        outside = self.root / "outside.anm"
        outside.write_bytes(seanim_bytes(STILL, 4))
        dz, fake, work = str(self.dz), str(self.fake), str(self.work)
        cases = {
            "no_anm": ["--dz", dz, "--dayzatool", fake, "--work", work, "--ikpose", IKPOSE_REL],
            "no_ikpose": ["--dz", dz, "--dayzatool", fake, "--work", work, "--anm", STILL_REL],
            "no_dz": ["--dayzatool", fake, "--work", work, "--ikpose", IKPOSE_REL, "--anm", STILL_REL],
            "second_anm_escapes_dz": self.base_args(STILL_REL, "../outside.anm"),
            "anm_absolute_outside_dz": self.base_args(str(outside)),
            "ikpose_escapes_dz": self.base_args(STILL_REL, ikpose="../outside.anm"),
            "skeleton_escapes_dz": self.base_args(STILL_REL) + ["--skeleton", "../outside.anm"],
            "timeout_zero": self.base_args(STILL_REL) + ["--timeout", "0"],
            "timeout_negative": self.base_args(STILL_REL) + ["--timeout=-1"],
        }
        for label, args in cases.items():
            with self.subTest(case=label):
                code, out, _err = self.run_tool(args)
                self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.launches(), 0)


    def test_a_non_finite_timeout_answers_2(self) -> None:
        for label, value in (("inf", "inf"), ("nan", "nan")):
            with self.subTest(case=label):
                code, out, _err = self.run_tool(self.base_args(STILL_REL) + ["--timeout", value])
                self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.launches(), 0)

    def test_the_script_run_without_arguments_answers_2(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in ("DZ_ROOT", "DAYZATOOL")}
        completed = subprocess.run([sys.executable, "-B", str(TOOL)], capture_output=True, text=True, timeout=60,
                                   env=env)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stdout, "")


class DayzaToolMissingTest(AnimFkCase):
    """Exit 3: no DayZATool to run."""

    def test_no_flag_and_no_environment_answers_3(self) -> None:
        args = ["--dz", str(self.dz), "--work", str(self.work), "--ikpose", IKPOSE_REL, "--anm", STILL_REL]
        code, out, _err = self.run_tool(args)
        self.assertEqual((code, out), (3, ""))

    def test_a_path_that_is_not_a_file_answers_3(self) -> None:
        for label, path in (("missing", self.root / "no" / "DayZATool.exe"), ("folder", self.root)):
            with self.subTest(case=label):
                args = self.base_args(STILL_REL)
                args[args.index("--dayzatool") + 1] = str(path)
                code, out, _err = self.run_tool(args)
                self.assertEqual((code, out), (3, ""))
        self.assertEqual(self.launches(), 0)

    def test_a_file_the_system_cannot_start_answers_3(self) -> None:
        self.argv_patch.stop()  # the real list: the OS is asked to start this text file
        not_a_program = self.root / "DayZATool.txt"
        not_a_program.write_text("not a program", encoding="utf-8")
        args = self.base_args(STILL_REL)
        args[args.index("--dayzatool") + 1] = str(not_a_program)
        code, out, _err = self.run_tool(args)
        self.assertEqual((code, out), (3, ""))


class ExtractionFailureTest(AnimFkCase):
    """Exit 4: DayZATool left no output; its own exit code does not count."""

    def test_no_output_answers_4_and_names_the_source(self) -> None:
        self.put(STILL_REL, b"NOOUT" + seanim_bytes(STILL, 4))
        code, out, err = self.run_tool(self.base_args(STILL_REL))
        self.assertEqual((code, out), (4, ""))
        self.assertIn(STILL_REL, err.replace("\\", "/"))

    def test_an_output_with_a_failing_exit_code_still_reads(self) -> None:
        self.put(STILL_REL, b"FAIL1" + seanim_bytes(STILL, 4, notes=[(2, "mark")]))
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(doc["segments"][0]["notes"], [{"frame": 2, "name": "mark"}])

    def test_a_dayzatool_past_its_timeout_answers_4(self) -> None:
        self.put(STILL_REL, b"SLEEP")
        code, out, _err = self.run_tool(self.base_args(STILL_REL) + ["--timeout", "0.5"])
        self.assertEqual((code, out), (4, ""))


class CacheTest(AnimFkCase):
    """The work folder caches by content, and the vanilla tree is never written."""

    def _snapshot(self) -> dict:
        return {str(p.relative_to(self.dz)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sorted(self.dz.rglob("*")) if p.is_file()}

    def test_a_second_run_reuses_every_extraction_and_dz_is_untouched(self) -> None:
        before = self._snapshot()
        first = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(self.launches(), 3)  # skeleton, ikpose, one clip
        second = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(self.launches(), 3)
        self.assertEqual(first, second)
        self.assertEqual(self._snapshot(), before)

    def test_changed_bytes_are_extracted_again(self) -> None:
        self.ok_json(self.base_args(STILL_REL))
        self.put(STILL_REL, seanim_bytes(STILL, 4, notes=[(1, "other")]))
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(self.launches(), 4)
        self.assertEqual(doc["segments"][0]["notes"], [{"frame": 1, "name": "other"}])


class RunnerContractTest(unittest.TestCase):
    """The list the runner launches, and where the subprocess lives."""

    def test_dayzatool_argv_is_the_documented_list(self) -> None:
        tool = load_tool()
        self.assertEqual(tool.dayzatool_argv("X.exe", "--extract-anim", "c.anm"),
                         ["X.exe", "--extract-anim", "c.anm", "100"])
        self.assertEqual(tool.dayzatool_argv("X.exe", "--extract-mdl", "c.xob"), ["X.exe", "--extract-mdl", "c.xob"])

    def test_no_shell_and_no_subprocess_in_the_math(self) -> None:
        cli = TOOL.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", cli)
        self.assertNotIn("os.system", cli)
        self.assertNotIn("subprocess", MATH.read_text(encoding="utf-8"))


class DocTest(unittest.TestCase):
    def test_the_usage_doc_names_the_schema_and_the_flags(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for token in ("anim_fk_v1", "DayZATool", "--dz", "--dayzatool", "--skeleton", "--ikpose", "--anm", "--bone",
                      "--work", "--timeout"):
            self.assertIn(token, text)


class WorkUnderDzTest(AnimFkCase):
    """Exit 2: --work, or the default temp cache, must not resolve under --dz, and nothing is written."""

    def _dz_snapshot(self) -> dict:
        return {str(p.relative_to(self.dz)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sorted(self.dz.rglob("*")) if p.is_file()}

    @staticmethod
    def _link_dir(target: Path, link: Path) -> None:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise OSError(completed.stderr or completed.stdout or "mklink /J failed")
            return
        os.symlink(target, link, target_is_directory=True)

    def test_work_under_dz_answers_2_and_writes_nothing(self) -> None:
        before = self._dz_snapshot()
        work = self.dz / "cache"
        args = self.base_args(STILL_REL)
        args[args.index("--work") + 1] = str(work)
        code, out, _err = self.run_tool(args)
        self.assertEqual((code, out), (2, ""))
        self.assertFalse(work.exists())
        self.assertEqual(self._dz_snapshot(), before)
        self.assertEqual(self.launches(), 0)

    def test_default_temp_under_dz_answers_2_and_writes_nothing(self) -> None:
        before = self._dz_snapshot()
        args = self.base_args(STILL_REL)
        i = args.index("--work")
        del args[i:i + 2]
        for key in ("TMPDIR", "TEMP", "TMP"):
            os.environ[key] = str(self.dz)
        with mock.patch.object(
            self.tool.tempfile, "gettempdir", side_effect=AssertionError("gettempdir must not be called")
        ):
            code, out, _err = self.run_tool(args)
        self.assertEqual((code, out), (2, ""))
        self.assertFalse((self.dz / "anim_fk").exists())
        self.assertEqual(self._dz_snapshot(), before)
        self.assertEqual(self.launches(), 0)

    def test_a_cache_folder_that_points_into_dz_answers_2(self) -> None:
        target = self.dz / "leaked_cache"
        target.mkdir()
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            for rel in (SKELETON_REL, IKPOSE_REL, STILL_REL):
                digest = hashlib.sha256((self.dz / rel).read_bytes()).hexdigest()
                self._link_dir(target, self.work / digest)
        except OSError as exc:
            self.skipTest(f"cannot create a directory link: {exc}")
        before = self._dz_snapshot()
        code, out, _err = self.run_tool(self.base_args(STILL_REL))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self._dz_snapshot(), before)
        self.assertEqual(self.launches(), 0)
        self.assertFalse(any(target.iterdir()), "no cache files written through the link")

    def test_work_in_a_distinct_sibling_is_kept_when_relative_to_folds_case(self) -> None:
        sibling_work = self.root / "other_work"
        sibling_work.mkdir()
        real = Path.relative_to

        def folded(self_path, other):
            try:
                return real(self_path, other)
            except ValueError:
                return real(self_path, sibling_work.resolve())

        args = self.base_args(STILL_REL)
        args[args.index("--work") + 1] = str(sibling_work)
        with mock.patch.object(Path, "relative_to", folded):
            doc = self.ok_json(args)
        self.assertEqual(doc["schema"], "anim_fk_v1")
        self.assertGreater(self.launches(), 0)


class CacheHashTest(AnimFkCase):
    """A cache entry whose bytes do not match the source key is rewritten before DayZATool runs."""

    def test_a_stale_cache_copy_is_rewritten_to_the_source_hash(self) -> None:
        src = (self.dz / STILL_REL).read_bytes()
        digest = hashlib.sha256(src).hexdigest()
        folder = self.work / digest
        folder.mkdir(parents=True)
        stale = folder / Path(STILL_REL).name
        stale.write_bytes(b"STALE" + src)
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(hashlib.sha256(stale.read_bytes()).digest(), hashlib.sha256(src).digest())
        self.assertEqual(doc["segments"][0]["notes"], [{"frame": 2, "name": "mark"}])

    def test_a_stale_copy_with_a_leftover_output_is_reextracted(self) -> None:
        src = (self.dz / STILL_REL).read_bytes()
        digest = hashlib.sha256(src).hexdigest()
        folder = self.work / digest
        folder.mkdir(parents=True)
        stale = folder / Path(STILL_REL).name
        stale.write_bytes(b"STALE" + src)
        leftover = stale.with_suffix(".seanim")
        leftover.write_bytes(seanim_bytes(STILL, 4, notes=[(1, "other")]))
        self.assertEqual(self.launches(), 0)
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertEqual(doc["segments"][0]["notes"], [{"frame": 2, "name": "mark"}])
        self.assertEqual(self.launches(), 3)
        self.assertEqual(hashlib.sha256(stale.read_bytes()).digest(), hashlib.sha256(src).digest())


class PhysicalMembershipTest(AnimFkCase):
    """A sibling tree is not inside --dz just because Path.relative_to folds case (NTFS)."""

    def test_a_sibling_tree_is_refused_even_if_relative_to_accepts_it(self) -> None:
        sibling = self.root / "other_tree"
        path = sibling / STILL_REL
        path.parent.mkdir(parents=True)
        path.write_bytes((self.dz / STILL_REL).read_bytes())
        real = Path.relative_to

        def folded(self_path, other):
            try:
                return real(self_path, other)
            except ValueError:
                return real(self_path, sibling.resolve())

        with mock.patch.object(Path, "relative_to", folded):
            code, out, _err = self.run_tool(self.base_args(str(path)))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.launches(), 0)

    def test_membership_uses_samefile_and_inode_identity(self) -> None:
        source = TOOL.read_text(encoding="utf-8")
        self.assertIn("os.path.samefile", source)
        self.assertIn("st_ino", source)
        self.assertIn("st_dev", source)


class FiniteOutputTest(AnimFkCase):
    """Exit 5: non-finite JSON, or an OSError while reading a cached DayZATool output."""

    def test_a_non_finite_channel_answers_5(self) -> None:
        rel = "anims/anm/player/actions/items/nan.anm"
        self.put(rel, seanim_bytes([("LeftHand", [(0, (float("nan"), 0.0, 0.0))], [])], 2))
        code, out, _err = self.run_tool(self.base_args(rel))
        self.assertEqual((code, out), (5, ""))

    def test_an_unreadable_cached_output_answers_5(self) -> None:
        self.ok_json(self.base_args(STILL_REL))
        seanim = next(self.work.rglob("still.seanim")).resolve()
        real_read = Path.read_bytes

        def injected(self_path):
            if Path(self_path).resolve() == seanim:
                raise PermissionError("injected")
            return real_read(self_path)

        with mock.patch.object(Path, "read_bytes", injected):
            try:
                code, out, _err = self.run_tool(self.base_args(STILL_REL))
            except OSError:
                self.fail("OSError escaped main; expected exit 5")
        self.assertEqual((code, out), (5, ""))


class PelvisTextTest(AnimFkCase):
    """limits and the usage doc follow the code: clip keys win when they exist."""

    def test_limits_and_docs_say_clip_keys_win_for_the_pelvis(self) -> None:
        doc = self.ok_json(self.base_args(STILL_REL))
        self.assertNotIn("The pelvis comes from the SEModel rest pose.", doc["limits"])
        self.assertTrue(
            any("clip keys when they exist" in item for item in doc["limits"]),
            doc["limits"],
        )
        text = DOC.read_text(encoding="utf-8")
        self.assertNotIn("The pelvis stays on the SEModel rest pose.", text)
        self.assertIn("The pelvis uses the clip keys when they exist, otherwise the SEModel rest pose.", text)



if __name__ == "__main__":
    unittest.main()
