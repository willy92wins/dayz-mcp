"""Synthetic SEAnim and SEModel files, and a fake DayZATool, for the anim_fk tests (inbox 7695).

Both writers follow the public formats as DayZATool v1.3 writes them; nothing here is game data and nothing here is
the command's own code.

- SEAnim: 'SEAnim' (6 bytes), version int16 1, header size int16 28, then a 26-byte payload: anim type, anim flags,
  data presence flags, data property flags, two reserved bytes (all u8), framerate f32, frame count u32, bone count
  u32, bone modifier count u8, three reserved bytes, note count u32. Bone names follow (NUL-terminated UTF-8), then
  one block per bone (a flags byte, then a location block and a rotation block when their presence bits 1 and 2 are
  set: a key count, then per key the frame and 3 or 4 f32, rotation as x, y, z, w), then the notes when bit 64 is set
  (frame and NUL-terminated name). Frame numbers and key counts take one byte up to 255 frames, two up to 65535,
  four above. Little-endian.
- SEModel: 'SEModel' (7 bytes), version u16 1, header size u16 20, then an 18-byte payload: data presence, bone
  presence and mesh presence flags (u8), bone, mesh and material counts (u32) and three reserved bytes. Bone names
  follow (NUL-terminated UTF-8), then a 73-byte record per bone: flags u8, parent i32 (-1 for the root), global
  position 3 f32, global rotation 4 f32, local position 3 f32, local rotation 4 f32 (x, y, z, w), scale 3 f32.
  DayZATool v1.3 writes data presence 0x07, bone presence 0x07 and mesh presence 0x03, leaves the global fields at
  zero, and puts its mesh and material data after the bones (measured on player_testing.xob on 2026-11-28).

The fake DayZATool is a Python script the tests launch with sys.executable through a patched
anim_fk.dayzatool_argv. It reads the copy it is given and writes the output next to it the way DayZATool v1.3 does
(the copy's name with .seanim or .semodel in place of its extension). The first bytes of the copy pick a behaviour:
b"NOOUT" writes nothing, b"FAIL1" writes the rest and exits 1, b"SLEEP" sleeps 5 s and writes nothing; anything
else is written as it is. Every launch adds a line to a counter file.
"""
from __future__ import annotations

import struct
from pathlib import Path

PRESENCE_LOC = 1
PRESENCE_ROT = 2
PRESENCE_NOTE = 64


def _index_char(count: int) -> str:
    if count <= 0xFF:
        return "B"
    if count <= 0xFFFF:
        return "H"
    return "I"


def seanim_bytes(
    bones,
    frame_count: int,
    *,
    framerate: float = 30.0,
    notes=(),
    anim_type: int = 0,
    version: int = 1,
    magic: bytes = b"SEAnim",
) -> bytes:
    """bones: [(name, [(frame, (x, y, z)), ...], [(frame, (x, y, z, w)), ...]), ...]; notes: [(frame, name), ...]."""
    presence = 0
    if any(pos for _name, pos, _rot in bones):
        presence |= PRESENCE_LOC
    if any(rot for _name, _pos, rot in bones):
        presence |= PRESENCE_ROT
    if notes:
        presence |= PRESENCE_NOTE
    frame_char = _index_char(frame_count)
    payload = struct.pack(
        "<6BfII4BI", anim_type, 0, presence, 0, 0, 0, framerate, frame_count, len(bones), 0, 0, 0, 0, len(notes)
    )
    out = bytearray(magic + struct.pack("<h", version) + struct.pack("<h", len(payload) + 2) + payload)
    for name, _pos, _rot in bones:
        out += name.encode("utf-8") + b"\0"
    for _name, pos, rot in bones:
        out += b"\0"
        if presence & PRESENCE_LOC:
            out += struct.pack("<" + frame_char, len(pos))
            for frame, value in pos:
                out += struct.pack("<" + frame_char + "3f", frame, *value)
        if presence & PRESENCE_ROT:
            out += struct.pack("<" + frame_char, len(rot))
            for frame, value in rot:
                out += struct.pack("<" + frame_char + "4f", frame, *value)
    for frame, name in notes:
        out += struct.pack("<" + frame_char, frame) + name.encode("utf-8") + b"\0"
    return bytes(out)


def semodel_bytes(bones, *, magic: bytes = b"SEModel", version: int = 1, trailing: bytes = b"") -> bytes:
    """bones: [(name, parent, (x, y, z), (x, y, z, w)), ...] with local transforms; globals are written as zero.
    trailing stands for the mesh and material data DayZATool writes after the bones (flags 0x07 / 0x03 then)."""
    data_presence, mesh_presence = (0x07, 0x03) if trailing else (0x01, 0x00)
    payload = struct.pack("<3B3I3B", data_presence, 0x07, mesh_presence, len(bones), 1 if trailing else 0,
                          1 if trailing else 0, 0, 0, 0)
    out = bytearray(magic + struct.pack("<H", version) + struct.pack("<H", len(payload) + 2) + payload)
    for name, _parent, _pos, _rot in bones:
        out += name.encode("utf-8") + b"\0"
    for _name, parent, pos, rot in bones:
        out += struct.pack("<Bi", 0, parent)
        out += struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)
        out += struct.pack("<3f", *pos) + struct.pack("<4f", *rot) + struct.pack("<3f", 1.0, 1.0, 1.0)
    return bytes(out) + trailing


FAKE_DAYZATOOL = '''\
import os
import shutil
import sys
import time

counter, flag, path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(counter, "a", encoding="utf-8") as fh:
    fh.write(flag + " " + os.path.basename(path) + "\\n")
data = open(path, "rb").read()
out = os.path.splitext(path)[0] + (".semodel" if flag == "--extract-mdl" else ".seanim")
if data.startswith(b"NOOUT"):
    sys.exit(0)
if data.startswith(b"SLEEP"):
    time.sleep(5)
    sys.exit(0)
if data.startswith(b"FAIL1"):
    open(out, "wb").write(data[5:])
    sys.exit(1)
open(out, "wb").write(data)
'''


def write_fake_dayzatool(folder: Path) -> Path:
    script = folder / "fake_dayzatool.py"
    script.write_text(FAKE_DAYZATOOL, encoding="utf-8")
    return script
