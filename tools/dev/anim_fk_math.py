"""SEAnim and SEModel readers, sampling, forward kinematics, and the ikpose item frame.

Little-endian. SEAnim values are f32, or f64 when dataPropertyFlags bit 0 is set.
SEModel locals are f32. Quaternions are xyzw, Hamilton product. Positions stay in
the centimetres DayZATool already wrote (scale 100). A channel with no keys keeps
the skeleton local. A clip bone the skeleton does not name is ignored.
"""

from __future__ import annotations

import math
import struct

PRESENCE_LOC = 1
PRESENCE_ROT = 2
PRESENCE_SCALE = 4
PRESENCE_NOTE = 64
SEM_GLOBAL = 1
SEM_LOCAL = 2
SEM_SCALE = 4


class FormatError(ValueError):
    """SEAnim or SEModel bytes this reader will not use."""


class BoneTrack:
    """Location and rotation keys for one clip bone. Empty means the channel is absent."""

    def __init__(self) -> None:
        self.pos: list[tuple[int, tuple[float, float, float]]] = []
        self.rot: list[tuple[int, tuple[float, float, float, float]]] = []


class Clip:
    """One absolute SEAnim (anim_type 0)."""

    def __init__(self) -> None:
        self.framerate = 0.0
        self.frame_count = 0
        self.bones: dict[str, BoneTrack] = {}
        self.notes: list[tuple[int, str]] = []


class Skeleton:
    """SEModel bones in file order. parent[i] is -1 only for the single root at index 0."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.parent: list[int] = []
        self.pos: list[tuple[float, float, float]] = []
        self.rot: list[tuple[float, float, float, float]] = []
        self.index: dict[str, int] = {}

    @property
    def root(self) -> str:
        return self.names[0]


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise FormatError("short read")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def unpack(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def text(self) -> str:
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            raise FormatError("unterminated string")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FormatError("string is not UTF-8") from exc


def _index_fmt(count: int) -> str:
    if count <= 0xFF:
        return "<B"
    if count <= 0xFFFF:
        return "<H"
    return "<I"


def _unit(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n == 0.0:
        raise FormatError("zero quaternion")
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate(q, v):
    x, y, z = v
    r = mul(mul(q, (x, y, z, 0.0)), conj(q))
    return (r[0], r[1], r[2])


def _read_keys(cur: _Cursor, frame_fmt: str, nvalues: int, wide: bool):
    (count,) = cur.unpack(frame_fmt)
    value_fmt = "<" + ("d" if wide else "f") * nvalues
    keys = []
    for _ in range(count):
        (frame,) = cur.unpack(frame_fmt)
        values = cur.unpack(value_fmt)
        keys.append((frame, values))
    keys.sort(key=lambda item: item[0])
    return keys


def load_seanim(data: bytes) -> Clip:
    """Parse an absolute SEAnim. Raises FormatError on a short or illegal buffer."""
    if len(data) < 6 or data[0:6] != b"SEAnim":
        raise FormatError("SEAnim magic is not SEAnim")
    cur = _Cursor(data)
    cur.pos = 6
    (version,) = cur.unpack("<h")
    if version != 1:
        raise FormatError(f"SEAnim version {version} is not 1")
    (header_size,) = cur.unpack("<h")
    if header_size < 28 or cur.pos + 26 > len(data):
        raise FormatError("SEAnim header is short")
    (
        anim_type,
        _anim_flags,
        presence,
        prop_flags,
        _r0,
        _r1,
        framerate,
        frame_count,
        bone_count,
        modifier_count,
        _r2,
        _r3,
        _r4,
        note_count,
    ) = struct.unpack_from("<6BfII4BI", data, 10)
    if anim_type != 0:
        raise FormatError(f"SEAnim anim_type {anim_type} is not absolute")
    if not math.isfinite(framerate) or framerate <= 0.0 or frame_count < 1:
        raise FormatError("SEAnim frame range is empty")
    name_at = 6 + 2 + header_size
    if name_at < 36 or name_at > len(data):
        raise FormatError("SEAnim bone names are out of range")
    cur.pos = name_at
    names = [cur.text() for _ in range(bone_count)]
    bone_fmt = _index_fmt(bone_count)
    for _ in range(modifier_count):
        cur.unpack(bone_fmt)
        cur.unpack("<B")
    wide = bool(prop_flags & 1)
    frame_fmt = _index_fmt(frame_count)
    clip = Clip()
    clip.framerate = float(framerate)
    clip.frame_count = int(frame_count)
    for name in names:
        cur.unpack("<B")
        track = BoneTrack()
        if presence & PRESENCE_LOC:
            track.pos = [(f, (v[0], v[1], v[2])) for f, v in _read_keys(cur, frame_fmt, 3, wide)]
        if presence & PRESENCE_ROT:
            track.rot = [(f, (v[0], v[1], v[2], v[3])) for f, v in _read_keys(cur, frame_fmt, 4, wide)]
        if presence & PRESENCE_SCALE:
            _read_keys(cur, frame_fmt, 3, wide)
        clip.bones[name] = track
    if presence & PRESENCE_NOTE:
        for _ in range(note_count):
            (frame,) = cur.unpack(frame_fmt)
            clip.notes.append((int(frame), cur.text()))
    return clip


def load_semodel(data: bytes) -> Skeleton:
    """Parse a DayZATool SEModel bone list. Mesh bytes after the records are ignored."""
    if len(data) < 7 or data[0:7] != b"SEModel":
        raise FormatError("SEModel magic is not SEModel")
    cur = _Cursor(data)
    cur.pos = 7
    (version,) = cur.unpack("<H")
    if version != 1:
        raise FormatError(f"SEModel version {version} is not 1")
    (header_size,) = cur.unpack("<H")
    if header_size < 20 or cur.pos + 18 > len(data):
        raise FormatError("SEModel header is short")
    (
        _data_presence,
        bone_presence,
        _mesh_presence,
        bone_count,
        _mesh_count,
        _material_count,
        _r0,
        _r1,
        _r2,
    ) = struct.unpack_from("<3B3I3B", data, 11)
    if bone_count < 1:
        raise FormatError("SEModel has no root")
    if (bone_presence & SEM_LOCAL) == 0:
        raise FormatError("SEModel has no local transforms")
    name_at = 7 + 2 + header_size
    if name_at < 29 or name_at > len(data):
        raise FormatError("SEModel bone names are out of range")
    cur.pos = name_at
    skel = Skeleton()
    skel.names = [cur.text() for _ in range(bone_count)]
    for i, name in enumerate(skel.names):
        cur.unpack("<B")
        (parent,) = cur.unpack("<i")
        if bone_presence & SEM_GLOBAL:
            cur.unpack("<3f")
            cur.unpack("<4f")
        pos = cur.unpack("<3f")
        rot = cur.unpack("<4f")
        if bone_presence & SEM_SCALE:
            cur.unpack("<3f")
        if i == 0:
            if parent != -1:
                raise FormatError("SEModel bone 0 is not the root")
        elif not (0 <= parent < i):
            raise FormatError("SEModel parent does not precede its child")
        skel.parent.append(int(parent))
        skel.pos.append((float(pos[0]), float(pos[1]), float(pos[2])))
        skel.rot.append(_unit((float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3]))))
        skel.index.setdefault(name, i)
    return skel


def _interp_pos(keys, frame: int):
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    lo, hi = 0, len(keys) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, p0 = keys[lo]
    f1, p1 = keys[hi]
    if frame == f0 or f1 == f0:
        return p0
    t = (frame - f0) / (f1 - f0)
    return tuple((1.0 - t) * a + t * b for a, b in zip(p0, p1))


def _interp_rot(keys, frame: int):
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    lo, hi = 0, len(keys) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, q0 = keys[lo]
    f1, q1 = keys[hi]
    if frame == f0 or f1 == f0:
        return q0
    if _dot(q0, q1) < 0.0:
        q1 = (-q1[0], -q1[1], -q1[2], -q1[3])
    t = (frame - f0) / (f1 - f0)
    return _unit(tuple((1.0 - t) * a + t * b for a, b in zip(q0, q1)))


def sample_local(clip: Clip, skeleton: Skeleton, name: str, frame: int):
    """Local position and rotation. Clip keys win; a missing channel uses the skeleton."""
    idx = skeleton.index[name]
    pos = skeleton.pos[idx]
    rot = skeleton.rot[idx]
    track = clip.bones.get(name)
    if track is not None:
        if track.pos:
            pos = _interp_pos(track.pos, frame)
        if track.rot:
            rot = _interp_rot(track.rot, frame)
    return pos, _unit(rot)


def _sample_track(keys, frame: int, rest):
    if keys:
        return _interp_pos(keys, frame) if len(keys[0][1]) == 3 else _interp_rot(keys, frame)
    if rest is None:
        raise FormatError("ikpose channel has no keys and no skeleton local")
    return rest


def world_poses(clip: Clip, skeleton: Skeleton, frame: int) -> dict[str, tuple]:
    """World position and rotation in skeleton order. Root world is its local."""
    world: dict[str, tuple] = {}
    for i, name in enumerate(skeleton.names):
        local_pos, local_rot = sample_local(clip, skeleton, name, frame)
        parent = skeleton.parent[i]
        if parent < 0:
            world[name] = (local_pos, local_rot)
            continue
        parent_pos, parent_rot = world[skeleton.names[parent]]
        turned = rotate(parent_rot, local_pos)
        world[name] = (
            (parent_pos[0] + turned[0], parent_pos[1] + turned[1], parent_pos[2] + turned[2]),
            mul(parent_rot, local_rot),
        )
    return world


def grip_frame(clip: Clip, skeleton: Skeleton):
    """Item frame from the ikpose at frame 0: dummy local pose, left target in that frame."""
    if "RightHand_Dummy" not in clip.bones:
        raise FormatError("ikpose lacks RightHand_Dummy")
    if "LeftHandIKTarget" not in clip.bones:
        raise FormatError("ikpose lacks LeftHandIKTarget")
    if clip.frame_count < 1:
        raise FormatError("ikpose has no frames")

    def rest(name: str, kind: str):
        idx = skeleton.index.get(name)
        if idx is None:
            return None
        return skeleton.pos[idx] if kind == "pos" else skeleton.rot[idx]

    dummy = clip.bones["RightHand_Dummy"]
    target = clip.bones["LeftHandIKTarget"]
    origin = _sample_track(dummy.pos, 0, rest("RightHand_Dummy", "pos"))
    rotation = _unit(_sample_track(dummy.rot, 0, rest("RightHand_Dummy", "rot")))
    left = _sample_track(target.pos, 0, rest("LeftHandIKTarget", "pos"))
    return origin, rotation, left


def to_item(pos, rot, hand_pos, hand_rot, origin, origin_rot):
    """Bone world pose in the item frame of this frame's RightHand."""
    delta = (pos[0] - hand_pos[0], pos[1] - hand_pos[1], pos[2] - hand_pos[2])
    shifted = rotate(conj(hand_rot), delta)
    shifted = (shifted[0] - origin[0], shifted[1] - origin[1], shifted[2] - origin[2])
    item_pos = rotate(conj(origin_rot), shifted)
    item_rot = mul(mul(conj(origin_rot), conj(hand_rot)), rot)
    return item_pos, item_rot


def distance(a, b) -> float:
    dx, dy, dz = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)
