"""Offline forward kinematics for vanilla DayZ player animations.

Usage:
    python tools/dev/anim_fk.py --dz <vanilla root> --dayzatool <DayZATool.exe> \\
        --ikpose <rel> --anm <rel> [--anm <rel> ...] [--bone NAME ...]

Copies each source into the work directory and runs DayZATool v1.3 on the copy
(--extract-mdl for the skeleton, --extract-anim with scale 100 for every clip).
Prints one JSON object, schema anim_fk_v1, on stdout. Exit 2 bad arguments,
3 DayZATool missing or not startable, 4 no output or timeout, 5 unreadable
SEAnim/SEModel or ikpose grip bones, a non-finite result, or an unreadable
DayZATool output, 6 a requested bone or a hand is absent from the skeleton.
Stdout is empty unless the exit code is 0.

Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import anim_fk_math

LIMITS = [
    "NormalWeaponIK and queue blends are not simulated.",
    "The pelvis uses the clip keys when they exist, otherwise the SEModel rest pose.",
    "Absolute orientations are not reliable; relative ones are (hand in the item frame, curves, wrist span).",
]


class ToolFailure(Exception):
    """A checked failure with an exit code. The message is for stderr."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def dayzatool_argv(exe: str, flag: str, path: str) -> list[str]:
    """Argument list DayZATool v1.3 is started with. No shell."""
    if flag == "--extract-anim":
        return [exe, "--extract-anim", path, "100"]
    if flag == "--extract-mdl":
        return [exe, "--extract-mdl", path]
    raise ValueError(f"unknown DayZATool flag: {flag}")


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anim_fk",
        description="Forward kinematics of DayZ player animations via DayZATool SEAnim/SEModel.",
    )
    parser.add_argument("--dz", help="vanilla root; else DZ_ROOT")
    parser.add_argument("--dayzatool", help="DayZATool executable; else DAYZATOOL")
    parser.add_argument("--skeleton", default="characters/bodies/player_testing.xob")
    parser.add_argument("--ikpose", help="ikpose .anm relative to --dz")
    parser.add_argument("--anm", action="append", dest="anms", help="action .anm relative to --dz")
    parser.add_argument("--bone", action="append", dest="bones", help="bone curve to emit")
    parser.add_argument("--work", help="extraction cache directory")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds per DayZATool run")
    return parser


def _same_file(left: Path, right: Path) -> bool:
    """True when both paths exist and are the same inode (os.path.samefile, or st_ino+st_dev)."""
    try:
        if os.path.samefile(left, right):
            return True
    except OSError:
        pass
    try:
        a = os.stat(left)
        b = os.stat(right)
    except OSError:
        return False
    return bool(a.st_ino) and (a.st_ino, a.st_dev) == (b.st_ino, b.st_dev)


def _physically_under(root: Path, path: Path) -> bool:
    """True if path or an existing ancestor is the same directory as root."""
    cur = path
    seen: set[str] = set()
    while True:
        key = str(cur)
        if key in seen:
            return False
        seen.add(key)
        if _same_file(cur, root):
            return True
        parent = cur.parent
        if parent == cur:
            return False
        cur = parent


def _resolves_under(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _physical_under_conclusive(root: Path, path: Path) -> bool | None:
    """True/False when root and an existing ancestor of path can be statted; otherwise None."""
    try:
        os.stat(root)
    except OSError:
        return None
    if _physically_under(root, path):
        return True
    cur = path
    while True:
        try:
            os.stat(cur)
            return False
        except OSError:
            parent = cur.parent
            if parent == cur:
                return None
            cur = parent


def _writes_under_dz(dz: Path, work: Path) -> bool:
    """True if the cache would physically sit under --dz.

    Junctions and symlinks are followed, so a cache folder that points into
    --dz is refused even when the link itself sits outside. A case-folded
    Path.resolve()/relative_to match is not enough when both sides exist
    and are different inodes (a sibling dz/DZ tree).
    """
    try:
        resolved_dz = dz.resolve()
        resolved_work = work.resolve()
    except OSError:
        resolved_dz = None
        resolved_work = None
    if resolved_dz is not None and resolved_work is not None:
        verdict = _physical_under_conclusive(resolved_dz, resolved_work)
        if verdict is True:
            return True
        if verdict is False:
            lexical = _physical_under_conclusive(dz, work)
            return lexical is True
    lexical = _physical_under_conclusive(dz, work)
    if lexical is True:
        return True
    if lexical is False:
        return False
    try:
        return _resolves_under(dz, work) or (
            resolved_dz is not None
            and resolved_work is not None
            and _resolves_under(resolved_dz, resolved_work)
        )
    except OSError:
        return _resolves_under(dz, work)


def _under_dz(dz: Path, rel: str) -> Path | None:
    try:
        root = dz.resolve()
        path = (Path(dz) / rel).resolve()
    except OSError:
        return None
    if not _resolves_under(root, path):
        return None
    # Path.relative_to can accept a sibling dz/DZ tree on case-sensitive NTFS.
    # When both sides exist and no ancestor is the same inode as --dz, refuse.
    try:
        path.stat()
        root.stat()
        if not _physically_under(root, path):
            return None
    except OSError:
        pass
    if not path.is_file():
        return None
    return path


def _ensure_cache_copy(data: bytes, dest: Path) -> bool:
    """Leave dest as an exact copy of data. Return True if it already matched.

    When the hash does not match, dest is rewritten atomically (a temporary
    file in the same folder, then replace). The caller must then invalidate
    any sibling DayZATool output and run the converter again.
    """
    expected = hashlib.sha256(data).digest()

    def current_ok() -> bool:
        try:
            return dest.is_file() and hashlib.sha256(dest.read_bytes()).digest() == expected
        except OSError:
            return False

    if current_ok():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    try:
        os.write(fd, data)
        os.close(fd)
        fd = -1
        os.replace(tmp_name, dest)
    except OSError:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    if not current_ok():
        raise ToolFailure(5, f"cached copy does not match the source: {dest.name}")
    return False


def _invalidate_sibling_outputs(copy: Path) -> None:
    """Remove leftover .seanim/.semodel next to a rewritten (or newly written) copy."""
    for suffix in (".seanim", ".semodel"):
        leftover = copy.with_suffix(suffix)
        try:
            leftover.unlink(missing_ok=True)
        except OSError as exc:
            raise ToolFailure(5, f"cannot invalidate cached output {leftover.name}: {exc}") from exc


def _extract(src: Path, rel: str, flag: str, work: Path, exe: str, timeout: float, dz: Path) -> bytes:
    """Copy src into work/<sha256>/ and return DayZATool's sibling output. Launch is skipped when it exists."""
    data = src.read_bytes()
    folder = work / hashlib.sha256(data).hexdigest()
    try:
        folder.lstat()
        folder_present = True
    except OSError:
        folder_present = False
    if folder_present and _writes_under_dz(dz, folder):
        raise ToolFailure(2, "cache folder resolves under --dz")
    folder.mkdir(parents=True, exist_ok=True)
    if _writes_under_dz(dz, folder):
        raise ToolFailure(2, "cache folder resolves under --dz")
    copy = folder / src.name
    matched = _ensure_cache_copy(data, copy)
    suffix = ".semodel" if flag == "--extract-mdl" else ".seanim"
    output = copy.with_suffix(suffix)
    if not matched:
        _invalidate_sibling_outputs(copy)
    if not output.is_file():
        try:
            subprocess.run(
                dayzatool_argv(exe, flag, str(copy)),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolFailure(4, f"DayZATool timed out for {rel}") from exc
        except OSError as exc:
            raise ToolFailure(3, f"cannot start DayZATool: {exc}") from exc
        if not output.is_file():
            raise ToolFailure(4, f"DayZATool wrote no output for {rel}")
    try:
        return output.read_bytes()
    except OSError as exc:
        raise ToolFailure(5, f"cannot read DayZATool output for {rel}: {exc}") from exc


def _segment(rel: str, clip, skeleton, bone_names: list[str], grip) -> dict:
    origin, origin_rot, target = grip
    spans: list[float] = []
    offsets: list[float] = []
    curves = {name: [] for name in bone_names}
    for frame in range(clip.frame_count):
        world = anim_fk_math.world_poses(clip, skeleton, frame)
        hand_pos, hand_rot = world["RightHand"]
        left_pos, left_rot = world["LeftHand"]
        spans.append(anim_fk_math.distance(left_pos, hand_pos))
        left_item, _left_item_rot = anim_fk_math.to_item(
            left_pos, left_rot, hand_pos, hand_rot, origin, origin_rot
        )
        offsets.append(anim_fk_math.distance(left_item, target))
        for name in bone_names:
            pos, rot = world[name]
            item_pos, item_rot = anim_fk_math.to_item(pos, rot, hand_pos, hand_rot, origin, origin_rot)
            curves[name].append(
                {
                    "i": frame,
                    "pos_cm": [float(v) for v in item_pos],
                    "rot_xyzw": [float(v) for v in item_rot],
                }
            )
    return {
        "anm": rel,
        "duration_s": float(clip.frame_count - 1) / float(clip.framerate),
        "frame_count": clip.frame_count,
        "framerate": float(clip.framerate),
        "notes": [{"frame": frame, "name": name} for frame, name in clip.notes],
        "blends": None,
        "ik": False,
        "bones": {name: {"frames": curves[name]} for name in bone_names},
        "wrist_span_cm": {"min": float(min(spans)), "max": float(max(spans)), "per_frame": [float(v) for v in spans]},
        "left_wrist_offset_cm": {"max": float(max(offsets)), "per_frame": [float(v) for v in offsets]},
    }


def _default_work_dir() -> Path:
    """Default cache folder, without calling tempfile.gettempdir() (it may write a probe)."""
    for key in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            return Path(value) / "anim_fk"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Temp" / "anim_fk"
        windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
        return Path(windir) / "Temp" / "anim_fk"
    return Path("/tmp") / "anim_fk"


def _run(args: argparse.Namespace) -> int:
    dz_text = args.dz or os.environ.get("DZ_ROOT") or ""
    if not dz_text:
        _err("--dz is required when DZ_ROOT is unset")
        return 2
    if not args.ikpose:
        _err("--ikpose is required")
        return 2
    anms = list(args.anms or [])
    if not anms:
        _err("at least one --anm is required")
        return 2
    if not (math.isfinite(args.timeout) and args.timeout > 0.0):
        _err("--timeout must be a finite number greater than zero")
        return 2
    dz = Path(dz_text)
    rels = [args.skeleton, args.ikpose, *anms]
    paths: list[Path] = []
    for rel in rels:
        path = _under_dz(dz, rel)
        if path is None:
            _err(f"path is outside --dz or is not a file: {rel}")
            return 2
        paths.append(path)
    work = Path(args.work) if args.work else _default_work_dir()
    if _writes_under_dz(dz, work):
        _err("--work resolves under --dz and would write in the vanilla tree")
        return 2
    exe = args.dayzatool or os.environ.get("DAYZATOOL") or ""
    if not exe:
        _err("--dayzatool is required when DAYZATOOL is unset")
        return 3
    if not Path(exe).is_file():
        _err(f"DayZATool is not a file: {exe}")
        return 3
    skel_path, ik_path, *anm_paths = paths
    skeleton = anim_fk_math.load_semodel(
        _extract(skel_path, args.skeleton, "--extract-mdl", work, exe, args.timeout, dz)
    )
    bone_names = list(args.bones or [])
    for name in ("LeftHand", "RightHand", *bone_names):
        if name not in skeleton.index:
            _err(f"bone is not in the skeleton: {name}")
            return 6
    ik_clip = anim_fk_math.load_seanim(
        _extract(ik_path, args.ikpose, "--extract-anim", work, exe, args.timeout, dz)
    )
    grip = anim_fk_math.grip_frame(ik_clip, skeleton)
    segments = []
    for rel, path in zip(anms, anm_paths):
        clip = anim_fk_math.load_seanim(_extract(path, rel, "--extract-anim", work, exe, args.timeout, dz))
        segments.append(_segment(rel, clip, skeleton, bone_names, grip))
    origin, origin_rot, target = grip
    document = {
        "schema": "anim_fk_v1",
        "units": {"position": "cm", "time": "s", "quaternion": "xyzw"},
        "scale": 100,
        "skeleton": {"path": args.skeleton, "bone_count": len(skeleton.names), "root": skeleton.root},
        "ikpose": {
            "path": args.ikpose,
            "item_from_right_hand": {
                "pos_cm": [float(v) for v in origin],
                "rot_xyzw": [float(v) for v in origin_rot],
            },
            "left_target_in_item_cm": [float(v) for v in target],
        },
        "limits": list(LIMITS),
        "segments": segments,
    }
    try:
        text = json.dumps(document, allow_nan=False)
    except (TypeError, ValueError) as exc:
        _err(f"result is not finite JSON: {exc}")
        return 5
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ToolFailure as exc:
        _err(str(exc))
        return exc.code
    except anim_fk_math.FormatError as exc:
        _err(str(exc))
        return 5


if __name__ == "__main__":
    sys.exit(main())
