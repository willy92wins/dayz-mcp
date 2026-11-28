# anim_fk

`anim_fk` reads a vanilla skeleton and player animations from disk, converts the
copies with DayZATool v1.3, and prints forward kinematics as one JSON object.
It does not play the game and it does not write animation files of its own.

The command does not simulate `NormalWeaponIK` or the blends in an animation
queue. The pelvis uses the clip keys when they exist, otherwise the SEModel rest pose. Absolute bone orientations are
not treated as reliable. Relative results are: a hand expressed in the item
frame, the requested bone curves, and the distance between the wrists.

The `.agr` / `.asi` graph is out of scope. Every segment therefore sets
`blends` to null and `ik` to false. A later command can fill those fields.
This is a disk calculation, not an MCP tool. In-game clip timing remains
`anim_timeline`.

DayZATool v1.3 has to be installed only on the machine that runs the command.
The process never writes under `--dz`: it copies each source into the work
cache and runs DayZATool on the copy. `--work`, including the default
temporary cache, is rejected with exit 2 if it would resolve under `--dz`,
before anything is written. CI tests use synthetic files only. They
do not call DayZATool and they do not need a vanilla tree.

## Run

From the repository root, or from `tools/`:

```
python tools/dev/anim_fk.py --dz <vanilla root> --dayzatool <DayZATool.exe> --ikpose <rel> --anm <rel> [--anm <rel> ...] [--bone NAME ...]
```

## Flags

- `--dz DIR` — vanilla root. When the flag is omitted, the `DZ_ROOT` environment variable is used. There is no default directory.
- `--dayzatool EXE` — DayZATool v1.3 executable. When the flag is omitted, `DAYZATOOL` is used. There is no default path.
- `--skeleton REL` — skeleton file under `--dz`. Default `characters/bodies/player_testing.xob`.
- `--ikpose REL` — ikpose `.anm` under `--dz`. Required. Supplies the item frame.
- `--anm REL` — one action clip under `--dz`. Repeat the flag once per segment. At least one is required. Segment order follows the flags.
- `--bone NAME` — include this bone's curve. Repeat for several bones. With no `--bone`, `bones` is `{}` and notes, wrist span, and left-wrist offset are still reported.
- `--work DIR` — cache directory. Default is a folder named `anim_fk` under the system temporary directory. A path that resolves under `--dz` is rejected before any write.
- `--timeout SEC` — seconds allowed for one DayZATool invocation. Default `120`. A value that is not a finite number greater than zero is rejected.

Each `REL` is resolved against `--dz` and must be an existing file inside that
tree. Absolute paths are accepted only when they still land inside `--dz`.
Paths in the JSON are the `REL` strings as they were passed, separators included.

The cache key is the SHA-256 of the source bytes. After the copy is made,
its SHA-256 is checked against the source; a mismatch is rewritten
atomically (a temporary file in the same folder, then replace). DayZATool is
not started again when the `.semodel` or `.seanim` output is already beside
the verified copy. A path is treated as inside `--dz` only if it resolves
there and, when both sides exist, is the same physical tree (`os.path.samefile`
or inode and device).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The JSON object was written to stdout. |
| 2 | Arguments: missing `--dz` (and `DZ_ROOT`), missing `--ikpose`, missing `--anm`, `--timeout` not a finite number greater than zero, a `REL` that escapes `--dz` or is not a file, or `--work` (or the default cache) that resolves under `--dz`. |
| 3 | DayZATool cannot be run: no `--dayzatool` and no `DAYZATOOL`, the path is not a file, or the operating system cannot start it. |
| 4 | DayZATool left no `.semodel` or `.seanim` before `--timeout`, including a killed timeout. Stderr names the source `REL`. The tool's own exit code is ignored when the file is present. |
| 5 | Unreadable SEAnim or SEModel (magic, version, sizes, anim type, bone parents, missing locals), the ikpose has no `RightHand_Dummy` or `LeftHandIKTarget`, a result that is not finite JSON, or an `OSError` while reading DayZATool output. |
| 6 | A `--bone` name, or `LeftHand` or `RightHand`, is not in the skeleton. |

Stdout is empty for every code other than 0. Diagnostics go to stderr.
Argument usage errors from the parser also exit 2.

## Output

Schema `anim_fk_v1`. Units are centimetres (`cm`), seconds (`s`), and
quaternions `xyzw`. `scale` is `100`, the value passed to `--extract-anim`.

- `skeleton` — `path`, `bone_count`, `root`.
- `ikpose` — `path`, `item_from_right_hand` (`pos_cm`, `rot_xyzw` of `RightHand_Dummy` at frame 0), `left_target_in_item_cm` (local position of `LeftHandIKTarget` at frame 0).
- `limits` — three strings: no `NormalWeaponIK` and no queue blends are simulated; the pelvis uses the clip keys when they exist, otherwise the SEModel rest pose; absolute orientations are not reliable, relative ones are (hand in the item frame, curves, wrist span).
- `segments` — one object per `--anm`: `anm`, `duration_s` (`(frame_count - 1) / framerate`), `frame_count`, `framerate`, `notes` (`frame`, `name`), `blends` (null), `ik` (false), `bones`, `wrist_span_cm` (`min`, `max`, `per_frame`), `left_wrist_offset_cm` (`max`, `per_frame`).

`wrist_span_cm` is the world distance from `LeftHand` to `RightHand`.
`left_wrist_offset_cm` is the distance, in the item frame, from the left wrist
to `left_target_in_item_cm`. Bone curves use that same item frame, one sample
for each native frame.
