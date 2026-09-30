from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageChops, ImageStat


CHARS_PER_TOKEN = 2.5

# --- Inline token budget (Claude Code MCP cap) --------------------------------
# Claude Code rejects a single MCP tool result above MAX_MCP_OUTPUT_TOKENS (VERIFIED 2026-06-28:
# default 25000, warning at 10000). The cap is on RESULT tokens, not pixels -- raising the client env
# var lets a larger inline image through. The server inherits the same env var by process, so it sizes
# the budget loop to whatever ceiling the client enforces; the two MUST agree or the client rejects the
# whole response (lost capture). TOKEN_SAFETY_MARGIN keeps the loop under the line: it estimates tokens
# as base64-chars/CHARS_PER_TOKEN over the image only, while the client counts real tokens over the
# full envelope (image + wrapper + meta). Reference inline widths on a 1302x776 JPEG q82 frame:
#   25000 -> ~600px (default) . 50000 -> ~860px (2x px) . 75000 -> ~1070px (3x) . 100000 -> ~native
TOKEN_SAFETY_MARGIN = 0.92
DEFAULT_CLIENT_TOKEN_CAP = 25000


def client_token_cap() -> int:
    """The MAX_MCP_OUTPUT_TOKENS ceiling the Claude Code client enforces on one tool result, read live
    from the environment so server and client stay aligned without a rebuild. Falls back to 25000."""
    try:
        cap = int(str(os.environ.get("MAX_MCP_OUTPUT_TOKENS", "")).strip())
    except (TypeError, ValueError):
        return DEFAULT_CLIENT_TOKEN_CAP
    return cap if cap > 0 else DEFAULT_CLIENT_TOKEN_CAP


def default_max_tokens() -> int:
    """Safe inline budget = client cap * fail-closed margin. The capture default."""
    return max(1, int(client_token_cap() * TOKEN_SAFETY_MARGIN))


def resolve_request_budget(requested: object = None) -> int:
    """Clamp a caller-requested inline token budget to the safe cap. requested <= 0 / None / junk ->
    the safe cap (best quality that still fits); a request ABOVE the cap is clamped down so the client
    never rejects the response; a request below is honored (spend less context on a capture)."""
    cap = default_max_tokens()
    try:
        req = int(requested)
    except (TypeError, ValueError):
        return cap
    return cap if req <= 0 else min(req, cap)


DEFAULT_MAX_TOKENS = default_max_tokens()
DEFAULT_FRAME_COUNT = 4
DEFAULT_FRAME_INTERVAL_S = 0.12
# Cold powershell.exe plus Add-Type on a loaded runner can take longer than
# the 8 s grab budget before the script has looked for a window. That wait is
# a start, not a hung capture (fb-20260927-141044-76e2). After the script
# reports {"phase":"started"}, timeout_s is still the capture budget.
CAPTURE_START_BUDGET_S = 60.0
_CAPTURE_STARTED_LINE = '{"phase":"started"}'

# Delivery encoding for the inline ImageContent. The ~25k-token MCP-output ceiling (CONFLICT-1,
# Claude Code issue #9152) is a constraint on the base64 PAYLOAD, not on pixels. A photographic
# DayZ frame as PNG is the worst possible choice: offline calibration on a real grab
# (fase3-evidence-subject.png, native 1302x776) showed PNG only fits ~208 px wide inside 25k tokens,
# so the old SCALE_WIDTHS (small=260/full=320) NEVER fit and the budget loop always shrank to ~208 px
# — that is why captures looked unreadable. Same grab as JPEG q82 fits ~592 px (q70 ~704 px) inside
# the identical budget: ~2.85x linear / ~8x pixels for free. JPEG is the default; PNG stays selectable.
DEFAULT_FORMAT = "jpeg"
DEFAULT_QUALITY = 82
# "tiny"/"small" are HARD width caps for light captures: they never grow with the budget. "full"
# means "as wide as the token budget allows" -- a large start width the budget loop below trims down,
# so raising MAX_MCP_OUTPUT_TOKENS actually buys resolution (25k JPEG ~600px, 50k ~860px, 100k ~native).
# An int scale is its own explicit cap. The budget loop is always the hard ceiling, whatever the start.
SCALE_WIDTHS = {
    "tiny": 320,
    "small": 512,
    "full": 8192,
}

# Canonical host-side grab, sibling of this module (single source of truth: this module, the spike0
# enumerator and the A6 gate all call it). The old embedded CopyFromScreen-of-rect snippet captured
# stale desktop content; mcp-grab.ps1 uses PrintWindow(PW_RENDERFULLCONTENT) -> the window's own
# surface, robust to occlusion. See that file's header for the root cause (2026-06-14).
GRAB_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-grab.ps1")

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS

# --- crop_space: which surface a crop normalizes over -------------------------------------------
# The grab backend hands back the whole top-level window bitmap (title bar and borders included)
# plus the client viewport rectangle inside it: mcp-grab.ps1 Get-CaptureGeometry emits
# ClientRectInWindow as client={left, top, width, height}, relative to that bitmap. "client" (the
# default) selects that viewport before any crop or downscale, so a normalized bbox refers to the
# rendered world and not to the chrome around it. "window" keeps the previous behaviour: the crop
# normalizes over the outer window through the fail-open apply_crop path. The enum is closed.
CROP_SPACE_CLIENT = "client"
CROP_SPACE_WINDOW = "window"
CROP_SPACES = (CROP_SPACE_CLIENT, CROP_SPACE_WINDOW)
DEFAULT_CROP_SPACE = CROP_SPACE_CLIENT

# Error tokens for the crop_space contract. All three are bare ASCII identifiers: the server-side
# wire filter keeps identifier-shaped tokens verbatim and mutes anything else, so these names are
# what a caller can search for. They are never accompanied by an ImageContent and client mode never
# falls back to the window surface on any of them.
#   bad_crop_space                caller error: crop_space outside the closed enum
#   bad_crop                      caller error: crop spec rejected by the strict client parser
#   frame_client_rect_unverified  capture not accreditable: backend client rect missing or invalid
ERROR_BAD_CROP_SPACE = "bad_crop_space"
ERROR_BAD_CROP = "bad_crop"
ERROR_CLIENT_RECT_UNVERIFIED = "frame_client_rect_unverified"

# Bounds of the "center:F" fraction under the strict client grammar (inclusive).
CENTER_FRACTION_MIN = 0.05
CENTER_FRACTION_MAX = 1.0

_RECT_FIELDS = ("left", "top", "width", "height")


def _error(error: str) -> dict[str, Any]:
    return {"isError": True, "error": error}


def _target_width(scale: str | int) -> int:
    if isinstance(scale, int):
        return max(1, min(scale, SCALE_WIDTHS["full"]))
    if isinstance(scale, str) and scale.isdigit():
        return max(1, min(int(scale), SCALE_WIDTHS["full"]))
    return SCALE_WIDTHS.get(str(scale), SCALE_WIDTHS["small"])


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _mime_for(fmt: str) -> str:
    f = str(fmt).lower()
    if f in ("jpeg", "jpg"):
        return "image/jpeg"
    if f == "webp":
        return "image/webp"
    return "image/png"


def encode_bytes(img: Image.Image, fmt: str = DEFAULT_FORMAT, quality: int = DEFAULT_QUALITY) -> bytes:
    """Encode to the delivery format. JPEG (default) is ~5-8x smaller than PNG for a photographic game
    frame at the same dimensions, which is what buys the resolution back inside the token budget. WEBP
    (opt-in via fmt='webp') is ~15% smaller again at q80/method6, but Claude Code has known webp MIME
    bugs (#39146/#15807) that can 400-brick the conversation, so it is never the default."""
    buf = io.BytesIO()
    f = str(fmt).lower()
    if f in ("jpeg", "jpg"):
        img.convert("RGB").save(buf, format="JPEG", quality=int(quality), optimize=True)
    elif f == "webp":
        img.convert("RGB").save(buf, format="WEBP", quality=int(quality), method=6)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _legacy_crop_box(size: tuple[int, int], crop: str) -> tuple[int, int, int, int] | None:
    """Box selected by the legacy fail-open crop grammar over an image of `size`, or None when the
    spec is empty, invalid or degenerate (the caller keeps the whole image). Same arithmetic as the
    original apply_crop, exposed so window mode can publish the rectangle it actually selected."""
    spec = (crop or "").strip().lower()
    if not spec:
        return None
    w, h = size
    try:
        if spec.startswith("center"):
            frac = 0.5
            if ":" in spec:
                frac = float(spec.split(":", 1)[1])
            frac = min(1.0, max(0.05, frac))
            cw, ch = max(1, int(w * frac)), max(1, int(h * frac))
            x0, y0 = (w - cw) // 2, (h - ch) // 2
            return (x0, y0, x0 + cw, y0 + ch)
        l, t, r, b = (float(v) for v in spec.split(","))
        l, t, r, b = max(0.0, l), max(0.0, t), min(1.0, r), min(1.0, b)
        box = (int(l * w), int(t * h), int(r * w), int(b * h))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        return box
    except (ValueError, IndexError):
        return None


def apply_crop(img: Image.Image, crop: str) -> Image.Image:
    """Crop the frame BEFORE the budget downscale so the whole budget is spent on the subject
    (effective zoom). Accepts:
      ""                      -> no crop
      "center" / "center:F"   -> centered box covering fraction F of each axis (default 0.5)
      "l,t,r,b"               -> normalized bbox in [0,1] (e.g. "0.25,0.1,0.75,0.9")
    Invalid/degenerate specs return the image unchanged (fail-open: never lose the frame).
    This is the window-space path; client mode uses the strict parser below and never reaches it."""
    box = _legacy_crop_box(img.size, crop)
    return img if box is None else img.crop(box)


def _strict_crop_box(size: tuple[int, int], crop: str | None) -> tuple[int, int, int, int] | None:
    """Box selected by `crop` over a surface of `size` under the strict client grammar, or None when
    the spec must be rejected as bad_crop. Same three forms as apply_crop, no clamps and no
    fail-open:
      "" / None       -> the whole surface
      "center"        -> centered box covering 0.5 of each axis
      "center:F"      -> F finite and inside [CENTER_FRACTION_MIN, CENTER_FRACTION_MAX]
      "l,t,r,b"       -> exactly four finite values in [0, 1] with l < r and t < b
    Any syntax, arity, NaN/Inf, out-of-range or degenerate spec (empty after pixel rounding)
    returns None. Whitespace is trimmed and the spec is case-folded, nothing else is repaired."""
    if crop is None:
        spec = ""
    elif isinstance(crop, str):
        spec = crop.strip().lower()
    else:
        return None
    w, h = size
    if not spec:
        return (0, 0, w, h)
    if spec == "center" or spec.startswith("center:"):
        frac = 0.5
        if spec != "center":
            try:
                frac = float(spec[len("center:"):])
            except ValueError:
                return None
            if not math.isfinite(frac) or frac < CENTER_FRACTION_MIN or frac > CENTER_FRACTION_MAX:
                return None
        cw, ch = int(w * frac), int(h * frac)
        if cw < 1 or ch < 1:
            return None
        x0, y0 = (w - cw) // 2, (h - ch) // 2
        return (x0, y0, x0 + cw, y0 + ch)
    parts = spec.split(",")
    if len(parts) != 4:
        return None
    try:
        l, t, r, b = (float(v) for v in parts)
    except ValueError:
        return None
    for value in (l, t, r, b):
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            return None
    if not (l < r and t < b):
        return None
    box = (int(l * w), int(t * h), int(r * w), int(b * h))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _rect_dict(left: int, top: int, width: int, height: int) -> dict[str, int]:
    """Wire shape of a rectangle, same field names the grab backend uses."""
    return {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}


def _verified_client_rect(rect: object, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Client viewport as (left, top, width, height) when the backend payload accredits it against
    a window bitmap of `size`, else None. Strict on purpose: every field must be a plain int (bool,
    float and numeric strings are rejected), origin >= 0, extent > 0 and the box must fit inside
    the bitmap. A missing or malformed rect is not "no crop": it is a capture that cannot be
    accredited, and client mode reports it instead of degrading to the window surface."""
    if not isinstance(rect, dict):
        return None
    values: list[int] = []
    for field in _RECT_FIELDS:
        value = rect.get(field)
        if type(value) is not int:
            return None
        values.append(value)
    left, top, width, height = values
    if left < 0 or top < 0 or width <= 0 or height <= 0:
        return None
    if left + width > size[0] or top + height > size[1]:
        return None
    return (left, top, width, height)


def _encode_to_budget(
    rgb: Image.Image,
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
) -> dict[str, Any]:
    """Downscale-to-budget and encode tail shared by every delivery path. Receives the RGB surface
    already selected (whole window, client viewport or a crop of either) and never crops: the crop
    decision belongs to the caller, which is what lets client mode reach this point without going
    through apply_crop."""
    target_chars = max(1, int(max_tokens * CHARS_PER_TOKEN))
    width = min(rgb.width, _target_width(scale))

    while True:
        height = max(1, round(rgb.height * width / rgb.width))
        resized = rgb.resize((width, height), LANCZOS) if (width, height) != rgb.size else rgb
        data = encode_bytes(resized, fmt=fmt, quality=quality)
        encoded = base64.b64encode(data).decode("ascii")
        if len(encoded) <= target_chars or width <= 1:
            return {"type": "image", "data": encoded, "mimeType": _mime_for(fmt)}
        ratio = max(0.25, min(0.92, (target_chars / len(encoded)) ** 0.5))
        width = max(1, int(width * ratio))


def image_content_from_image(
    img: Image.Image,
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
) -> dict[str, Any]:
    """Legacy window-space delivery: fail-open apply_crop over the frame received, then the shared
    encode tail. capture_dual(crop_space="window") composes the same two steps; client mode does
    not use this wrapper."""
    return _encode_to_budget(apply_crop(img.convert("RGB"), crop), scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality)


def image_content_from_png_bytes(
    data: bytes,
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
) -> dict[str, Any]:
    with Image.open(io.BytesIO(data)) as img:
        return image_content_from_image(img, scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality, crop=crop)


def image_stats_from_image(img: Image.Image) -> dict[str, Any]:
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    histogram = gray.histogram()
    non_black = sum(histogram[9:])
    total = max(1, gray.width * gray.height)
    return {
        "width": gray.width,
        "height": gray.height,
        "meanBrightness": float(stat.mean[0]),
        "nonBlackRatio": float(non_black / total),
    }


def image_content_stats(content: dict[str, Any]) -> dict[str, Any]:
    data = content.get("data")
    if not isinstance(data, str):
        raise ValueError("missing image data")
    raw = base64.b64decode(data.encode("ascii"), validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        return image_stats_from_image(img)


def _decode_image_content(content: dict[str, Any]) -> Image.Image:
    """The pixels a consumer of this ImageContent will actually see: decoded from the base64
    payload, as RGB. Used to measure the delivered surface on the same bytes that are returned."""
    data = content.get("data")
    if not isinstance(data, str):
        raise ValueError("missing image data")
    raw = base64.b64decode(data.encode("ascii"), validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        return img.convert("RGB").copy()


def _pixel_sha256(img: Image.Image) -> str:
    """SHA-256 of the raw RGB bytes of a surface, independent of any encoding."""
    return hashlib.sha256(img.tobytes()).hexdigest()


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _surface_record(rect: tuple[int, int, int, int], rgb: Image.Image, rect_key: str) -> dict[str, Any]:
    return {
        rect_key: _rect_dict(*rect),
        "pixel_sha256": _pixel_sha256(rgb),
        "stats": image_stats_from_image(rgb),
    }


def mean_abs_pixel_delta(a: Image.Image, b: Image.Image) -> float:
    width = min(a.width, b.width, 160)
    ah = max(1, round(a.height * width / a.width))
    bh = max(1, round(b.height * width / b.width))
    left = a.convert("RGB").resize((width, ah), LANCZOS)
    right = b.convert("RGB").resize((width, bh), LANCZOS)
    if left.size != right.size:
        right = right.resize(left.size, LANCZOS)
    diff = ImageChops.difference(left, right)
    means = ImageStat.Stat(diff).mean
    return float(sum(means) / (len(means) * 255.0))


def center_region_delta(a: Image.Image, b: Image.Image, frac: float = 0.5) -> float:
    """Mean abs pixel delta over the centered fraction of the frame (the region the commanded
    subject occupies under a lookat). Used to assert the subject — not just any pixel — changed."""
    width = min(a.width, b.width, 160)
    ah = max(1, round(a.height * width / a.width))
    bh = max(1, round(b.height * width / b.width))
    left = a.convert("RGB").resize((width, ah), LANCZOS)
    right = b.convert("RGB").resize((width, bh), LANCZOS)
    if left.size != right.size:
        right = right.resize(left.size, LANCZOS)
    w, h = left.size
    cw = max(1, int(w * frac))
    ch = max(1, int(h * frac))
    x0 = (w - cw) // 2
    y0 = (h - ch) // 2
    box = (x0, y0, x0 + cw, y0 + ch)
    diff = ImageChops.difference(left.crop(box), right.crop(box))
    means = ImageStat.Stat(diff).mean
    return float(sum(means) / (len(means) * 255.0))


def load_rgb(path: str) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGB").copy()


def image_content_from_file(
    path: str,
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
) -> dict[str, Any]:
    with Image.open(path) as img:
        return image_content_from_image(img, scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality, crop=crop)


def compare_captures(subject_path: str, control_path: str, liveness_path: str | None = None) -> dict[str, Any]:
    """Content comparison for the camera->render validation. delta_follow = subject(lookat player) vs
    control(lookat sky); a live render that follows camera_set makes these visibly differ. delta_live
    = two grabs of the same view; > ~0 proves the grab is live (not a cached desktop frame)."""
    subject = load_rgb(subject_path)
    control = load_rgb(control_path)
    out: dict[str, Any] = {
        "delta_follow": mean_abs_pixel_delta(subject, control),
        "delta_center": center_region_delta(subject, control),
        "subject_stats": image_stats_from_image(subject),
        "control_stats": image_stats_from_image(control),
    }
    if liveness_path is not None and os.path.exists(liveness_path):
        out["delta_live"] = mean_abs_pixel_delta(subject, load_rgb(liveness_path))
    return out


def _adjacent_pair_deltas(frames: list[Image.Image]) -> list[float]:
    """mean_abs_pixel_delta of every adjacent pair, in order; empty for a single frame. Split out so
    the caller can publish the numbers the stability choice was already made on instead of paying
    for the same downscale-and-diff twice."""
    return [mean_abs_pixel_delta(frames[i - 1], frames[i]) for i in range(1, len(frames))]


def _stable_frame_index(frames: list[Image.Image], pair_deltas: list[float]) -> int:
    """Index of the frame whose closest neighbour moved least. Same arithmetic as before, taking
    the deltas as an argument."""
    if len(frames) == 1:
        return 0
    scores: list[float] = []
    for index in range(len(frames)):
        adjacent: list[float] = []
        if index > 0:
            adjacent.append(pair_deltas[index - 1])
        if index < len(pair_deltas):
            adjacent.append(pair_deltas[index])
        scores.append(min(adjacent))
    return min(range(len(scores)), key=lambda idx: scores[idx])


def choose_stable_frame(frames: list[Image.Image]) -> Image.Image:
    if not frames:
        raise ValueError("no frames captured")
    if len(frames) == 1:
        return frames[0]
    return frames[_stable_frame_index(frames, _adjacent_pair_deltas(frames))]


# --- Frozen frame: the same picture twice, declared as a fact ------------------------------------
# A capture can hand back byte-identical pixels for reasons that look alike from outside: a client
# that stopped drawing (the failure this answers), a paused sim (SetTimeMultiplier(0) freezes
# animations too), an open menu, a still scene. So the repeat is published as a FLAG with its
# evidence and never as an error -- the image is always delivered and the diagnosis stays with the
# caller, who knows whether it asked for the pause. Two independent signals travel together:
#   intra-call   distinct_frames / max_adjacent_delta over the N frames this call already grabs.
#                No stored state, so it works on the FIRST capture; it only sees ~0.36 s.
#   cross-call   the sidecar below: "has the render advanced since the last capture of this window,
#                by anyone?" -- the question two captures with a camera_set between them ask.
# The cross-call store is a file rather than a module global because in --client mode every MCP
# session is its own process and the capture stays local (CLAUDE.md, Modos de ejecucion), so process
# memory could only ever compare a session against itself, and would lose its baseline on restart.
#
# THE ONE THING THIS MUST NEVER DO IS INVENT A FREEZE. A true has to mean that the pixels measured
# now equal pixels some earlier capture really stored, for a window and a surface that are still the
# same one. Three things defend that and each was a defect first:
#   - the whole read-compare-prune-write cycle runs under an inter-process lock. Without it a writer
#     holding an old snapshot replaces a newer file and RESURRECTS a hash a later capture then
#     reports as a freeze that never happened.
#   - the comparison identity carries geometry, not just the label "client"/"window": _pixel_sha256
#     hashes the byte stream alone, so a 10x20 and a 20x10 block of one colour hash the same and a
#     resize would read as a frozen frame.
#   - a capture whose window cannot be identified at all does not fall into a shared bucket: it
#     publishes null. Two different windows in one "unknown" record compare as one window.
FRAME_STATE_ENV = "DAYZ_MCP_FRAME_STATE_PATH"
FRAME_STATE_FILENAME = "capture-frame-state.json"
FRAME_STATE_VERSION = 2
# Bound on the store: keys accumulate (a fresh pid every run), and a cache file that only grows is a
# slow leak. Evicting the oldest record can only ever cost a comparison -- the next capture of that
# window reports frame_stale null, never a wrong true.
FRAME_STATE_MAX_WINDOWS = 32
FRAME_STATE_TMP_PREFIX = ".capture-frame-state-"
FRAME_STATE_TMP_SUFFIX = ".tmp"
# Waiting for the lock is bounded and short: the critical section is a few milliseconds, and a
# capture is worth more than the flag, so a lock that does not come free degrades to "unavailable"
# instead of holding the capture. A lock older than STALE belonged to a process that died holding
# it; breaking it costs, at worst, one capture's worth of the round-1 race.
FRAME_STATE_LOCK_SUFFIX = ".lock"
FRAME_STATE_LOCK_TIMEOUT_S = 0.5
FRAME_STATE_LOCK_POLL_S = 0.005
FRAME_STATE_LOCK_STALE_S = 30.0
FRAME_STATE_TMP_STALE_S = 60.0
SURFACE_CLIENT = "client"
SURFACE_WINDOW = "window"
KEY_KIND_CMDLINE = "cmdline"
KEY_KIND_PID = "pid"
KEY_KIND_UNACCREDITED = "unaccredited"
STATE_BACKEND_SIDECAR = "sidecar"
STATE_BACKEND_UNAVAILABLE = "unavailable"


def frame_state_path() -> str:
    r"""Where the cross-call frame identity lives: $DAYZ_MCP_FRAME_STATE_PATH >
    %LOCALAPPDATA%\DayZ_MCP\capture-frame-state.json -- the same "env var wins" shape as
    resolve_capture_dir. The LOCALAPPDATA root is replicated here, not imported from
    dayz_mcp.runtime_state, because this module is published on its own (publish/boundary.py) and
    must not depend on the package. A host without LOCALAPPDATA falls back to the temp dir instead
    of failing: the flag is never worth a lost capture."""
    chosen = os.environ.get(FRAME_STATE_ENV, "").strip()
    if chosen:
        return os.path.abspath(chosen)
    base = os.environ.get("LOCALAPPDATA", "").strip() or tempfile.gettempdir()
    return os.path.abspath(os.path.join(base, "DayZ_MCP", FRAME_STATE_FILENAME))


class _FrameStateLock:
    """Inter-process mutex over the sidecar, so read-compare-prune-write is one transaction.

    Exclusive creation of a sibling file is the mechanism: atomic on NTFS and on POSIX, no
    dependency, and visible to every process that shares the store. Acquisition is bounded by
    FRAME_STATE_LOCK_TIMEOUT_S and raises TimeoutError when it expires, which the report turns into
    state_backend "unavailable" -- the capture never waits on the flag."""

    def __init__(self, path: str) -> None:
        self.path = path + FRAME_STATE_LOCK_SUFFIX
        self._fd: int | None = None

    def __enter__(self) -> "_FrameStateLock":
        deadline = time.time() + FRAME_STATE_LOCK_TIMEOUT_S
        while True:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                return self
            except FileExistsError:
                self._break_if_stale()
                if time.time() >= deadline:
                    raise TimeoutError("frame_state_locked") from None
                time.sleep(FRAME_STATE_LOCK_POLL_S)

    def _break_if_stale(self) -> None:
        """A holder killed mid-write would block every later capture for good, so a lock older than
        FRAME_STATE_LOCK_STALE_S is removed. The critical section is milliseconds, so a live holder
        can never reach that age."""
        try:
            age = time.time() - os.stat(self.path).st_mtime
        except OSError:
            return
        if age > FRAME_STATE_LOCK_STALE_S:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _sweep_stale_temporaries(directory: str, now: float) -> None:
    """Drop sidecar temporaries left by a process killed between the write and the replace. Called
    only with the lock held and only for files past FRAME_STATE_TMP_STALE_S, so the temporary a live
    writer is using is never touched. Every failure is ignored: leftover litter is not a reason to
    lose a capture."""
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(FRAME_STATE_TMP_PREFIX) or not entry.name.endswith(FRAME_STATE_TMP_SUFFIX):
            continue
        try:
            if now - entry.stat().st_mtime > FRAME_STATE_TMP_STALE_S:
                os.unlink(entry.path)
        except OSError:
            pass


def _read_frame_state(path: str) -> tuple[dict[str, Any], str | None]:
    """(state, reset_reason). A missing file is the first capture, not an error. A file that does
    not parse, or does not carry the expected shape or version, resets the state and says so: a
    sidecar truncated by a host kill must not cost every later capture its flag. An OSError
    propagates on purpose -- the caller degrades the report to state_backend "unavailable" rather
    than overwriting records it could not read."""
    fresh: dict[str, Any] = {"version": FRAME_STATE_VERSION, "windows": {}}
    if not os.path.exists(path):
        return (fresh, None)
    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    try:
        state = json.loads(raw)
    except ValueError as exc:
        return (fresh, f"state_reset_unreadable: {type(exc).__name__}")
    if not isinstance(state, dict) or not isinstance(state.get("windows"), dict):
        return (fresh, "state_reset_bad_shape")
    if state.get("version") != FRAME_STATE_VERSION:
        return (fresh, f"state_reset_version: {state.get('version')!r}")
    return (state, None)


def _write_frame_state(path: str, state: dict[str, Any]) -> None:
    """Replace the sidecar atomically -- sibling temp file plus os.replace -- so a capture killed
    mid-write leaves either the old file or the new one and never a half-written one. Written as
    bytes, like every other state file under this root, so the newlines do not depend on the
    platform. Raises on any store failure; the caller turns that into state_backend "unavailable"
    and still returns the image."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    handle_fd, tmp_path = tempfile.mkstemp(prefix=FRAME_STATE_TMP_PREFIX, suffix=FRAME_STATE_TMP_SUFFIX, dir=directory)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _frame_state_key(cmdline_match: str, window: object) -> tuple[str, str]:
    """(key, kind) of the window whose frames are compared.

    cmdline_match identifies the live run's client (the server derives it from that run's _client
    profiles dir) and is the only identity that survives a process restart, so it wins. A bare pid
    is second best and is published as such: the OS reuses pids, so "pid:77" is not proof that two
    captures saw the same window -- the geometric identity below has to agree as well. With neither,
    the capture is UNACCREDITED and gets no comparison at all: a shared "unknown" bucket would
    compare two different windows as if they were one, which is exactly the false freeze this
    contract forbids."""
    text = str(cmdline_match or "").strip()
    if text:
        return (text, KEY_KIND_CMDLINE)
    if isinstance(window, dict):
        pid = window.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return (f"pid:{pid}", KEY_KIND_PID)
    return ("unknown", KEY_KIND_UNACCREDITED)


def _utc_stamp(epoch_s: float) -> str:
    """ISO-8601 UTC to the millisecond, the shape both the sidecar and the meta publish."""
    return datetime.fromtimestamp(epoch_s, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_stamp(stamp: object) -> float | None:
    """Epoch seconds of a stamp written by _utc_stamp, or None when it cannot be read. The trailing
    Z is rewritten by hand so the published module keeps working on runtimes whose fromisoformat
    does not accept it."""
    if not isinstance(stamp, str) or not stamp:
        return None
    text = stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _comparison_surface(window_rgb: Image.Image, client_rect: object, window: object) -> tuple[str, str, str]:
    """(surface, identity, sha256) of the surface staleness is decided on.

    The surface is the client viewport when the backend rect accredits it and the whole window
    bitmap otherwise; the viewport is preferred because the title bar repaints when the window gains
    or loses focus, and that repaint would report "the frame moved" in exactly the focus scenario
    this is meant to catch.

    identity is what must MATCH for two captures to be comparable at all: the surface kind, its
    exact rectangle and the window's own class and bitmap size. The label alone is not enough --
    _pixel_sha256 hashes the byte stream and nothing else, so a 10x20 and a 20x10 block of one
    colour produce the same hash and a resize would read as a frozen frame."""
    rect = _verified_client_rect(client_rect, window_rgb.size)
    if rect is None:
        surface, rgb = SURFACE_WINDOW, window_rgb
        box = (0, 0, window_rgb.width, window_rgb.height)
    else:
        left, top, width, height = rect
        surface, rgb = SURFACE_CLIENT, window_rgb.crop((left, top, left + width, top + height))
        box = rect
    window_class = window.get("class") if isinstance(window, dict) else None
    identity = "{}:{},{},{},{}:{}:{}x{}".format(
        surface, box[0], box[1], box[2], box[3],
        str(window_class) if isinstance(window_class, str) else "",
        window_rgb.width, window_rgb.height,
    )
    return (surface, identity, _pixel_sha256(rgb))


def _frame_evidence(frames: list[Image.Image], pair_deltas: list[float]) -> dict[str, Any]:
    """What this single call saw, with no stored state: how many frames were grabbed, how many of
    them differ pixel-wise, and how far the largest adjacent step moved. distinct_frames == 1 over
    DEFAULT_FRAME_COUNT frames means nothing changed in DEFAULT_FRAME_INTERVAL_S * (N-1) seconds."""
    digests = [_pixel_sha256(frame) for frame in frames]
    return {
        "frames": len(frames),
        "distinct_frames": len(set(digests)),
        "max_adjacent_delta": max(pair_deltas) if pair_deltas else 0.0,
    }


RENDER_FROZEN_SIGNAL = "render_frozen_signal"
# Below this max_adjacent_delta the frames of one call are one picture even when their sha differ
# (f47b). The delta is mean_abs_pixel_delta: mean per-channel difference / 255 on a <=160 px
# LANCZOS downscale, so 1/255 ~= 0.0039 is one grey level on every pixel. DayZDiag 1.29, runs
# ad4aaa5f and 151b98d0 (2026-09-30): a render frozen on the last scripted frame gave 0.0,
# 8.7e-08, 1.39e-06 and 4.69e-04 (distinct_frames up to 5); live renders gave 0.004 (a still
# scripted view), 0.0079, 0.009, 0.013, 0.080, 0.114 and 0.119. 1e-3 sits in that gap, 2.1x above
# the largest frozen value and 4x below the smallest live one. The wider margin is on the live side
# because a still scripted view is what every camera_set is checked with. It stays a warning, not
# a verdict: a genuinely still live view can fall under it too.
RENDER_FROZEN_DELTA_EPS = 1e-3


def _is_metric_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _is_render_frozen_signal(detail: object) -> bool:
    if not isinstance(detail, dict):
        return False
    frames = detail.get("frames")
    distinct = detail.get("distinct_frames")
    delta = detail.get("max_adjacent_delta")
    if not (
        _is_metric_number(frames)
        and _is_metric_number(distinct)
        and _is_metric_number(delta)
    ):
        return False
    # distinct_frames == 1 with delta 0 (byte-identical frames) is the bottom of the band, so the
    # sha count no longer decides. A negative mean absolute difference is not evidence of anything.
    return frames >= 2 and 0 <= delta < RENDER_FROZEN_DELTA_EPS


def _annotate_render_frozen_signal(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach render_frozen_signal once when intra-call metrics say the render did not move."""
    if not _is_render_frozen_signal(payload.get("frame_stale_detail")):
        return payload
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        warnings = list(warnings)
    else:
        warnings = []
    if RENDER_FROZEN_SIGNAL not in warnings:
        warnings.append(RENDER_FROZEN_SIGNAL)
    payload["warnings"] = warnings
    return payload


def _frame_stale_report(
    key: str,
    surface: str,
    current_sha256: str,
    evidence: dict[str, Any],
    key_kind: str = KEY_KIND_CMDLINE,
    identity: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Compare this frame against the last one recorded for the same window, record the new one, and
    return {stale, detail} for the meta.

    stale is None -- never False -- whenever no comparison was possible: the first capture of a
    window, a window that cannot be identified, a record taken over a different surface or geometry,
    or a store that could not be read or written. stale is True only when the sha measured now
    equals a sha an earlier capture really stored under the same key AND the same identity. The
    stored timestamp is the FIRST capture of a run of identical frames, so age_s answers "frozen
    since when" and not "how long ago was the previous call".

    On an all-black frame the second capture has the same sha by construction (black == black),
    so stale=True whether the host failed to compose or the client failed to draw. stale is not
    the discriminant there: detail.distinct_frames / max_adjacent_delta is.

    The whole cycle runs inside _FrameStateLock: read, compare, prune and write are one transaction
    across processes. Serialising it is not tidiness -- without it a writer carrying an older
    snapshot replaces a newer file, resurrects a hash that a later capture then matches, and
    publishes a freeze that never happened.

    Never raises: every store failure, lock timeout included, degrades to state_backend
    "unavailable" with the reason in state_error, because a capture without the flag beats no
    capture at all."""
    moment = time.time() if now is None else now
    detail: dict[str, Any] = {
        "key": key,
        "key_kind": key_kind,
        "surface": surface,
        "current_sha256": current_sha256,
        "previous_sha256": None,
        "same_as_capture_ts": None,
        "age_s": None,
        "repeat_count": 1,
        **evidence,
        "state_backend": STATE_BACKEND_SIDECAR,
        "state_error": None,
    }
    if key_kind == KEY_KIND_UNACCREDITED:
        # No window identity, no comparison and no record: one shared bucket would compare two
        # different windows as if they were the same one.
        detail["state_error"] = "key_unaccredited"
        return {"stale": None, "detail": detail}
    stale: bool | None = None
    try:
        path = frame_state_path()
        with _FrameStateLock(path):
            _sweep_stale_temporaries(os.path.dirname(path) or ".", moment)
            state, reset_reason = _read_frame_state(path)
            detail["state_error"] = reset_reason
            record = state["windows"].get(key)
            first_seen = moment
            if isinstance(record, dict):
                previous_sha256 = record.get("sha256")
                if isinstance(previous_sha256, str) and previous_sha256:
                    detail["previous_sha256"] = previous_sha256
                    # Both halves, and not just the identity string: the label is what a caller
                    # passing no identity still gets checked on, and the identity is what catches a
                    # resize that keeps the label.
                    if record.get("surface") != surface or record.get("identity") != identity:
                        detail["state_error"] = "surface_changed"
                    elif previous_sha256 == current_sha256:
                        stale = True
                        stored_ts = record.get("ts")
                        if isinstance(stored_ts, str) and stored_ts:
                            detail["same_as_capture_ts"] = stored_ts
                        seen_at = _parse_stamp(stored_ts)
                        if seen_at is not None:
                            first_seen = seen_at
                            detail["age_s"] = float(max(0.0, moment - seen_at))
                        count = record.get("repeat_count")
                        valid = isinstance(count, int) and not isinstance(count, bool) and count > 0
                        detail["repeat_count"] = count + 1 if valid else 2
                    else:
                        stale = False
            windows = state["windows"]
            windows[key] = {
                "surface": surface,
                "identity": identity,
                "sha256": current_sha256,
                "ts": _utc_stamp(first_seen),
                "repeat_count": detail["repeat_count"],
            }
            if len(windows) > FRAME_STATE_MAX_WINDOWS:
                def _record_ts(name: str) -> str:
                    entry = windows.get(name)
                    return str(entry.get("ts") or "") if isinstance(entry, dict) else ""

                newest = sorted(windows, key=_record_ts, reverse=True)[:FRAME_STATE_MAX_WINDOWS]
                keep = set(newest) | {key}
                state["windows"] = {name: record for name, record in windows.items() if name in keep}
            _write_frame_state(path, state)
    except Exception as exc:
        stale = None
        detail["state_backend"] = STATE_BACKEND_UNAVAILABLE
        detail["state_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return {"stale": stale, "detail": detail}


# printwindow first, never ForceForeground: AttachThreadInput+SetForegroundWindow
# has killed the live DayZ client (fb-20260904-025027-8f76).
DEFAULT_GRAB_METHOD = "printwindow"
_DESKTOP_SWITCHDESKTOP = 0x0100


def probe_input_desktop() -> str:
    """Whether the interactive input desktop is reachable.

    Windows: OpenInputDesktop(DESKTOP_SWITCHDESKTOP). A NULL handle means the
    session is locked or on the secure desktop; a live handle is closed.
    Non-Windows hosts and any exception are unknown.
    """
    if sys.platform != "win32":
        return "unknown"
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        user32.CloseDesktop.restype = ctypes.c_int
        handle = user32.OpenInputDesktop(0, False, _DESKTOP_SWITCHDESKTOP)
        if not handle:
            return "locked"
        user32.CloseDesktop(handle)
        return "unlocked"
    except Exception:
        return "unknown"


# fb-20260918-134756-05a0 / c0e5: refuse a capture tandem before the first
# run when the host desktop is locked or the framebuffer is all-black.
PRERUN_DESKTOP_TIMEOUT_S = 30.0
PRERUN_DESKTOP_POLL_S = 1.0
PRERUN_BRIGHTNESS_TIMEOUT_S = 2.0
DESKTOP_BLACK_MEAN = 1.0
DESKTOP_BLACK_NONBLACK = 0.01
SESSION_LOCKED = "session_locked"
DESKTOP_ALL_BLACK = "desktop_all_black"
DESKTOP_PROBE_TIMEOUT = "desktop_probe_timeout"
DESKTOP_PROBE_FAILED = "desktop_probe_failed"
DESKTOP_PROBE_UNSUPPORTED = "desktop_probe_unsupported"
REMEDIATION_SESSION_LOCKED = (
    "Unlock the Windows session (Win+L lock screen / sleep / closed lid). "
    "Window-grab cannot see pixels until the interactive desktop is reachable; "
    "a capture tandem would return frame_client_all_black."
)
REMEDIATION_DESKTOP_ALL_BLACK = (
    "Host desktop screenshot is all-black. Wake the display or unlock the "
    "session before starting a capture tandem; otherwise capture_screenshot "
    "will return frame_client_all_black."
)
REMEDIATION_DESKTOP_PROBE_TIMEOUT = (
    "Desktop brightness probe did not finish in time. The host display may be "
    "asleep or the session locked; unlock/wake and retry before a capture tandem."
)
REMEDIATION_DESKTOP_PROBE_FAILED = (
    "Desktop brightness probe failed on this Windows host. The interactive "
    "display could not be measured, so the launch is refused rather than risk "
    "frame_client_all_black. Check the session/display and retry."
)
DESKTOP_PROBE_JOIN_MIN_S = 0.05


@dataclass(frozen=True, slots=True)
class PrerunDesktopResult:
    """Verdict of the pre-run desktop unlock + brightness gate."""

    error_code: str | None
    desktop: str
    mean_brightness: float | None
    nonblack_ratio: float | None
    waited_s: float
    remediation: str


def _desktop_is_black(mean_brightness: float, nonblack_ratio: float) -> bool:
    return (
        mean_brightness <= DESKTOP_BLACK_MEAN
        and nonblack_ratio <= DESKTOP_BLACK_NONBLACK
    )


def probe_desktop_brightness(
    timeout_s: float = PRERUN_BRIGHTNESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Cheap host-desktop grab. No DayZ window required.

    A confirmed image uses the same black thresholds as grab_stable_frame
    (meanBrightness <= 1 and nonBlackRatio <= 0.01). Non-Windows hosts return
    desktop_probe_unsupported so the pre-run gate does not block them.

    ImageGrab runs on a daemon thread. A hung Win32 grab cannot be cancelled,
    but this function returns at ``timeout_s`` without joining the worker
    (ThreadPoolExecutor.shutdown(wait=True) would wait forever).
    """
    if sys.platform != "win32":
        return {"ok": False, "error": DESKTOP_PROBE_UNSUPPORTED}

    box: list[dict[str, Any]] = []

    def _grab() -> None:
        try:
            from PIL import ImageGrab

            image = ImageGrab.grab()
            stats = image_stats_from_image(image)
            box.append(
                {
                    "ok": True,
                    "mean_brightness": float(stats["meanBrightness"]),
                    "nonblack_ratio": float(stats["nonBlackRatio"]),
                }
            )
        except Exception:
            box.append({"ok": False, "error": DESKTOP_PROBE_FAILED})

    worker = threading.Thread(
        target=_grab,
        name="mcp-desktop-brightness-probe",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=max(DESKTOP_PROBE_JOIN_MIN_S, float(timeout_s)))
    if worker.is_alive():
        return {"ok": False, "error": DESKTOP_PROBE_TIMEOUT}
    if box:
        return box[0]
    return {"ok": False, "error": DESKTOP_PROBE_FAILED}


def run_prerun_desktop_gate(
    *,
    timeout_s: float = PRERUN_DESKTOP_TIMEOUT_S,
    poll_s: float = PRERUN_DESKTOP_POLL_S,
    wait: bool = True,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
    probe_desktop: Any = probe_input_desktop,
    probe_brightness: Any = probe_desktop_brightness,
    brightness_timeout_s: float = PRERUN_BRIGHTNESS_TIMEOUT_S,
) -> PrerunDesktopResult:
    """Wait up to timeout_s for an unlocked desktop and a non-black screenshot.

    Locked sessions never call the brightness grab (ImageGrab can hang there).
    A confirmed all-black framebuffer is desktop_all_black. Unsupported
    (non-Windows) does not block. On Windows, a failed or hung grab retries
    inside the remaining budget and then aborts as desktop_probe_failed or
    desktop_probe_timeout. A bright measurement that finishes after the
    deadline is rejected.
    """
    started = float(clock())
    deadline = started + max(0.0, float(timeout_s))
    last_error = DESKTOP_PROBE_FAILED
    last_desktop = "unknown"
    last_mean: float | None = None
    last_nonblack: float | None = None
    last_remediation = REMEDIATION_DESKTOP_PROBE_FAILED

    def _remaining(now: float) -> float:
        return deadline - now

    def _finish(now: float) -> PrerunDesktopResult:
        return PrerunDesktopResult(
            error_code=last_error,
            desktop=last_desktop,
            mean_brightness=last_mean,
            nonblack_ratio=last_nonblack,
            waited_s=round(now - started, 3),
            remediation=last_remediation,
        )

    while True:
        now = float(clock())
        remaining = _remaining(now)
        if wait and remaining <= 0.0:
            return _finish(now)

        desktop = str(probe_desktop() or "unknown")
        last_desktop = desktop
        if desktop == "locked":
            last_error = SESSION_LOCKED
            last_remediation = REMEDIATION_SESSION_LOCKED
            last_mean = None
            last_nonblack = None
        else:
            now = float(clock())
            remaining = _remaining(now)
            if wait and remaining <= 0.0:
                return _finish(now)
            if wait:
                probe_timeout = min(float(brightness_timeout_s), remaining)
            else:
                probe_timeout = float(brightness_timeout_s)
            if probe_timeout <= 0.0:
                last_error = DESKTOP_PROBE_TIMEOUT
                last_remediation = REMEDIATION_DESKTOP_PROBE_TIMEOUT
                return _finish(now)
            bright = probe_brightness(timeout_s=probe_timeout)
            now = float(clock())
            over_deadline = bool(wait) and now >= deadline
            if not isinstance(bright, dict):
                bright = {"ok": False, "error": DESKTOP_PROBE_FAILED}
            if bright.get("ok") is True:
                mean = float(bright["mean_brightness"])
                nonblack = float(bright["nonblack_ratio"])
                last_mean = mean
                last_nonblack = nonblack
                if not _desktop_is_black(mean, nonblack):
                    if over_deadline:
                        last_error = DESKTOP_PROBE_TIMEOUT
                        last_remediation = REMEDIATION_DESKTOP_PROBE_TIMEOUT
                        return _finish(now)
                    return PrerunDesktopResult(
                        error_code=None,
                        desktop=desktop,
                        mean_brightness=mean,
                        nonblack_ratio=nonblack,
                        waited_s=round(now - started, 3),
                        remediation="",
                    )
                last_error = DESKTOP_ALL_BLACK
                last_remediation = REMEDIATION_DESKTOP_ALL_BLACK
            else:
                err = str(bright.get("error") or DESKTOP_PROBE_FAILED)
                last_mean = None
                last_nonblack = None
                if err == DESKTOP_PROBE_UNSUPPORTED:
                    return PrerunDesktopResult(
                        error_code=None,
                        desktop=desktop,
                        mean_brightness=None,
                        nonblack_ratio=None,
                        waited_s=round(now - started, 3),
                        remediation="",
                    )
                last_error = err
                if err == DESKTOP_PROBE_TIMEOUT:
                    last_remediation = REMEDIATION_DESKTOP_PROBE_TIMEOUT
                elif err == DESKTOP_PROBE_FAILED:
                    last_remediation = REMEDIATION_DESKTOP_PROBE_FAILED
                else:
                    last_remediation = REMEDIATION_DESKTOP_ALL_BLACK
            if over_deadline:
                return _finish(now)

        now = float(clock())
        if (not wait) or now >= deadline:
            return _finish(now)
        remaining = _remaining(now)
        sleeper(min(max(0.0, float(poll_s)), remaining))


def _grab_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for mcp-grab.ps1.

    Git Bash (and some WSL/MSYS shells) inherit a Unix ``PSModulePath`` that
    poisons Windows PowerShell so core cmdlets are ``command not found``
    (fb-20260915-011312-ba70). Drop the variable and let PowerShell use its
    own default module path.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.casefold() == "psmodulepath":
            env.pop(key, None)
    return env


def _capture_reported_started(line: str) -> bool:
    return line.strip() == _CAPTURE_STARTED_LINE


def _capture_payload_from_output(stdout: str, stderr: str, output_path: str) -> dict[str, Any]:
    stdout_lines = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not _capture_reported_started(line)
    ]
    payload: dict[str, Any] = {}
    if stdout_lines:
        try:
            parsed = json.loads(stdout_lines[-1])
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}
    if not payload:
        detail = stderr.strip()
        return {"ok": False, "error": f"capture_backend_failed: {detail or 'no_json'}"}
    if payload.get("ok") is True and not os.path.exists(output_path):
        return {"ok": False, "error": "capture_backend_failed: missing_png"}
    return payload


def _kill_capture_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return


def _read_capture_stdout(
    stream: Any, lines: list[str], started: threading.Event
) -> None:
    try:
        for line in stream:
            lines.append(line)
            if _capture_reported_started(line):
                started.set()
    except Exception:
        return


def _read_capture_stderr(stream: Any, chunks: list[str]) -> None:
    try:
        text = stream.read()
        if text:
            chunks.append(text)
    except Exception:
        return


def _run_window_capture(output_path: str, process_name: str, timeout_s: float, method: str = DEFAULT_GRAB_METHOD, client_pid: int = 0, cmdline_match: str = "") -> dict[str, Any]:
    if probe_input_desktop() == "locked":
        return {"ok": False, "error": "session_locked"}
    if not os.path.exists(GRAB_SCRIPT):
        return {"ok": False, "error": f"capture_backend_failed: grab script missing {GRAB_SCRIPT}"}
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        GRAB_SCRIPT,
        "-ProcessName",
        process_name,
        "-CapturePng",
        output_path,
        "-Method",
        method,
    ]
    # Prefer cmdline_match (robust to the launcher-pid != window-pid mismatch); fall back to client_pid.
    # Both eliminate the multi-client window collision by restricting the candidate windows.
    if cmdline_match:
        cmd += ["-CmdLineMatch", str(cmdline_match)]
    elif client_pid and int(client_pid) > 0:
        cmd += ["-ClientPid", str(int(client_pid))]
    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "env": _grab_subprocess_env(),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except FileNotFoundError:
        return {"ok": False, "error": "capture_backend_failed:command_not_found"}
    except OSError as exc:
        return {"ok": False, "error": f"capture_backend_failed: {exc}"}

    stdout_lines: list[str] = []
    stderr_chunks: list[str] = []
    started = threading.Event()
    readers = [
        threading.Thread(
            target=_read_capture_stdout,
            args=(proc.stdout, stdout_lines, started),
            daemon=True,
        ),
        threading.Thread(
            target=_read_capture_stderr,
            args=(proc.stderr, stderr_chunks),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    try:
        start_deadline = time.monotonic() + CAPTURE_START_BUDGET_S
        while not started.is_set() and proc.poll() is None:
            remaining = start_deadline - time.monotonic()
            if remaining <= 0.0:
                break
            started.wait(min(0.05, remaining))
        if proc.poll() is None and not started.is_set():
            _kill_capture_process(proc)
            return {"ok": False, "error": "capture_start_timeout"}
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_capture_process(proc)
                return {"ok": False, "error": "capture_timeout"}
        for reader in readers:
            reader.join(timeout=2.0)
        return _capture_payload_from_output(
            "".join(stdout_lines), "".join(stderr_chunks), output_path
        )
    finally:
        for reader in readers:
            reader.join(timeout=2.0)
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass


def grab_window_to_file(output_path: str, process_name: str = "DayZDiag_x64", method: str = DEFAULT_GRAB_METHOD, timeout_s: float = 8.0, client_pid: int = 0, cmdline_match: str = "") -> dict[str, Any]:
    """Single host-side grab to a PNG file. Returns the backend payload
    ({ok, method, error, window, stats, client, clientStats, sha256}). Public so content-validation
    harnesses can grab twice (subject vs control) and diff the actual pixels. cmdline_match (preferred) or client_pid
    restrict the grab to the target client's render window so a second DayZ client (e.g. LFQuad) cannot
    be captured by mistake. cmdline_match is robust to DayZDiag's launcher-pid != window-pid mismatch."""
    return _run_window_capture(output_path, process_name=process_name, timeout_s=timeout_s, method=method, client_pid=client_pid, cmdline_match=cmdline_match)


def grab_stable_frame(
    frames: int = DEFAULT_FRAME_COUNT,
    process_name: str = "DayZDiag_x64",
    method: str = DEFAULT_GRAB_METHOD,
    client_pid: int = 0,
    cmdline_match: str = "",
) -> Image.Image | dict[str, Any]:
    """Grab N frames, return the most stable full-resolution RGB frame (native window size, no
    downscale), or an error dict ({isError, error}) on capture failure / unverifiable or all-black
    client-area. Split out from
    capture_screenshot so the dual channel (full-res to disk + inline thumbnail) shares one grab.

    Every frame that reaches this function is recorded in the frozen-frame sidecar BEFORE the
    client-area gates, so a rejected capture still counts: "black since T, N captures in a row" is
    only countable if the rejected frames are recorded too, and the error payload returned below
    carries no meta of its own. The report rides on the returned frame as
    info["frame_stale_report"], which capture_dual publishes."""
    frame_count = max(1, min(int(frames), 5))
    with tempfile.TemporaryDirectory(prefix="mcp_capture_") as tmp_dir:
        captured: list[Image.Image] = []
        capture_results: list[dict[str, Any]] = []
        for index in range(frame_count):
            output_path = os.path.join(tmp_dir, f"frame_{index}.png")
            result = _run_window_capture(output_path, process_name=process_name, timeout_s=8.0, method=method, client_pid=client_pid, cmdline_match=cmdline_match)
            if result.get("ok") is not True:
                return _error(str(result.get("error") or "window_capture_failed"))
            with Image.open(output_path) as img:
                frame = img.convert("RGB").copy()
                captured.append(frame)
                capture_results.append(result)
            if index + 1 < frame_count:
                time.sleep(DEFAULT_FRAME_INTERVAL_S)

        pair_deltas = _adjacent_pair_deltas(captured)
        chosen_index = _stable_frame_index(captured, pair_deltas)
        chosen = captured[chosen_index]
        chosen_result = capture_results[chosen_index]
        chosen_window = chosen_result.get("window")
        state_key, key_kind = _frame_state_key(cmdline_match, chosen_window)
        surface, surface_identity, surface_sha256 = _comparison_surface(
            chosen, chosen_result.get("client"), chosen_window
        )
        frame_stale_report = _frame_stale_report(
            key=state_key,
            surface=surface,
            current_sha256=surface_sha256,
            evidence=_frame_evidence(captured, pair_deltas),
            key_kind=key_kind,
            identity=surface_identity,
        )
        client_stats = chosen_result.get("clientStats")
        if not isinstance(client_stats, dict):
            return _error("frame_client_area_unverified")
        mean = client_stats.get("meanBrightness")
        nonblack = client_stats.get("nonBlackRatio")
        if (
            not isinstance(mean, (int, float))
            or isinstance(mean, bool)
            or not math.isfinite(float(mean))
            or not isinstance(nonblack, (int, float))
            or isinstance(nonblack, bool)
            or not math.isfinite(float(nonblack))
        ):
            return _error("frame_client_area_unverified")
        if float(mean) <= 1.0 and float(nonblack) <= 0.01:
            payload = _error("frame_client_all_black")
            # black == black by construction: stale cannot tell a frozen host
            # from a client that is not drawing. The discriminant is
            # detail.distinct_frames / max_adjacent_delta, not stale.
            payload["frame_stale_report"] = frame_stale_report
            payload["frame_stale_note"] = (
                "on an all-black frame, stale is not the discriminant "
                "(identical sha by construction); use detail.distinct_frames "
                "/ max_adjacent_delta"
            )
            return payload
        chosen.info["window"] = chosen_result.get("window")
        chosen.info["sha256"] = chosen_result.get("sha256")
        # Client viewport of the SAME chosen frame, verified later by the consumer that needs it.
        chosen.info["client"] = chosen_result.get("client")
        chosen.info["frame_stale_report"] = frame_stale_report
        return chosen


def capture_screenshot(
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    frames: int = DEFAULT_FRAME_COUNT,
    process_name: str = "DayZDiag_x64",
    method: str = DEFAULT_GRAB_METHOD,
    client_pid: int = 0,
    cmdline_match: str = "",
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
) -> dict[str, Any]:
    chosen = grab_stable_frame(frames=frames, process_name=process_name, method=method, client_pid=client_pid, cmdline_match=cmdline_match)
    if isinstance(chosen, dict):  # error payload from grab_stable_frame
        return chosen
    return image_content_from_image(chosen, scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality, crop=crop)


def resolve_capture_dir(save_dir: str = "") -> str:
    """Where full-res frames land. Explicit arg > $DAYZ_MCP_CAPTURE_DIR > <temp>/dayz_mcp_captures.
    Returned path is absolute so the agent can Read it directly (the dual channel that sidesteps the
    ~25k inline token budget — the full-res file is delivered through the normal image-read path)."""
    chosen = (save_dir or "").strip() or os.environ.get("DAYZ_MCP_CAPTURE_DIR", "").strip()
    if not chosen:
        chosen = os.path.join(tempfile.gettempdir(), "dayz_mcp_captures")
    return os.path.abspath(chosen)


def write_fullres(img: Image.Image, save_dir: str = "", quality: int = 92) -> str:
    """Persist the native-resolution frame as high-quality JPEG and return its absolute path."""
    out_dir = resolve_capture_dir(save_dir)
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"capture_{stamp}_{int(time.time() * 1000) % 1000:03d}.jpg"
    path = os.path.join(out_dir, name)
    img.convert("RGB").save(path, format="JPEG", quality=int(quality), optimize=True)
    return path


def capture_dual(
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    frames: int = DEFAULT_FRAME_COUNT,
    process_name: str = "DayZDiag_x64",
    method: str = DEFAULT_GRAB_METHOD,
    client_pid: int = 0,
    cmdline_match: str = "",
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
    save_fullres: bool = False,
    save_dir: str = "",
    fullres_quality: int = 92,
    crop_space: str = DEFAULT_CROP_SPACE,
) -> dict[str, Any]:
    """Single grab -> inline budget-fit ImageContent (always) + optional full-res frame on disk.

    crop_space selects the surface `crop` normalizes over, and the surface delivered when crop is
    empty. "client" (default) is the rendered viewport accredited by the backend client rect of the
    SAME chosen frame, cut out before any downscale; "window" is the whole window bitmap through the
    legacy fail-open apply_crop. Client mode is fail-closed: an unverifiable rect returns
    frame_client_rect_unverified and a rejected crop returns bad_crop; a crop_space outside the
    closed enum returns bad_crop_space before any grab. None of those degrades to the window
    surface or carries an image.

    meta publishes an auditable surface map next to the legacy fields:
      window_surface     {rect, pixel_sha256, stats} over the whole window RGB (rect is the bitmap
                         itself, origin 0,0; meta.window keeps the on-screen geometry)
      client_surface     {rect_window, pixel_sha256, stats} over the native client viewport, or
                         None in window mode when the backend rect does not verify
      effective_surface  {rect_window, native_*, delivered_*}: the region actually selected by
                         crop_space + crop; native_* is measured before the downscale and
                         delivered_* on the decoded pixels of the ImageContent returned
    meta.window keeps its legacy {pid, class, title, left, top, width, height}; meta.frame_sha256
    keeps the SHA-256 of the full window RGB and equals window_surface.pixel_sha256;
    meta.native_width/native_height keep the whole-window dimensions of the chosen frame. The
    fullres file, when requested, is the native effective surface (after crops, before downscale),
    with its file hash in meta.fullres_file_sha256.

    meta.frame_stale (bool | None) and meta.frame_stale_detail declare whether this frame repeats
    the previous capture of the same window: True = identical pixels, False = the render advanced,
    None = no comparison was possible (first capture, an unidentifiable window, a record over a
    different surface or geometry, or an unusable state store). It is a fact about pixels, not a
    diagnosis -- a paused sim, an open menu and a still scene all produce it legitimately -- so it
    never turns a capture into an error. The detail also carries the intra-call evidence (frames,
    distinct_frames, max_adjacent_delta), which needs no stored state and is therefore available on
    the very first capture, and key_kind, which says how strong the window identity behind the
    comparison is.

    Returns {inline, fullres_path, meta} on success or {isError, error} on failure."""
    if not isinstance(crop_space, str) or crop_space not in CROP_SPACES:
        return _error(ERROR_BAD_CROP_SPACE)
    chosen = grab_stable_frame(frames=frames, process_name=process_name, method=method, client_pid=client_pid, cmdline_match=cmdline_match)
    if isinstance(chosen, dict):  # error payload
        return chosen

    window_rgb = chosen
    window_box = (0, 0, window_rgb.width, window_rgb.height)
    window_hash = _pixel_sha256(window_rgb)
    # Written by grab_stable_frame on the frame it selected; an empty dict only if a caller hands in
    # a frame from somewhere else, in which case both keys publish as null rather than failing.
    frame_stale_report: dict[str, Any] = chosen.info.get("frame_stale_report") or {}

    client_rect = _verified_client_rect(chosen.info.get("client"), window_rgb.size)
    client_rgb: Image.Image | None = None
    client_surface: dict[str, Any] | None = None
    if client_rect is not None:
        left, top, width, height = client_rect
        client_rgb = window_rgb.crop((left, top, left + width, top + height))
        client_surface = _surface_record(client_rect, client_rgb, "rect_window")

    if crop_space == CROP_SPACE_CLIENT:
        if client_rect is None or client_rgb is None:
            return _error(ERROR_CLIENT_RECT_UNVERIFIED)
        box = _strict_crop_box(client_rgb.size, crop)
        if box is None:
            return _error(ERROR_BAD_CROP)
        effective_native = client_rgb if box == (0, 0, client_rgb.width, client_rgb.height) else client_rgb.crop(box)
        effective_rect = (client_rect[0] + box[0], client_rect[1] + box[1], box[2] - box[0], box[3] - box[1])
    else:
        # Window space: the legacy fail-open path, apply_crop -> encode tail, unchanged semantics.
        box = _legacy_crop_box(window_rgb.size, crop) or window_box
        effective_native = apply_crop(window_rgb, crop)
        effective_rect = (box[0], box[1], box[2] - box[0], box[3] - box[1])

    inline = _encode_to_budget(effective_native, scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality)
    delivered = _decode_image_content(inline)

    meta: dict[str, Any] = {
        "native_width": chosen.width,
        "native_height": chosen.height,
        "crop": crop or "",
        "crop_space": crop_space,
        # True only when the delivered surface is the accredited client viewport
        # (ficha 268a): title-bar / border chrome cannot be in that bitmap.
        "chrome_excluded": crop_space == CROP_SPACE_CLIENT,
        "inline_mimeType": inline.get("mimeType"),
        "inline_base64_len": len(inline.get("data") or ""),
        "window": chosen.info.get("window"),
        "backend_sha256": chosen.info.get("sha256"),
        # Hash the selected full-resolution window RGB pixels, independent of inline encoding.
        "frame_sha256": window_hash,
        "window_surface": {
            "rect": _rect_dict(*window_box),
            "pixel_sha256": window_hash,
            "stats": image_stats_from_image(window_rgb),
        },
        "client_surface": client_surface,
        "effective_surface": {
            "rect_window": _rect_dict(*effective_rect),
            "native_width": effective_native.width,
            "native_height": effective_native.height,
            "native_pixel_sha256": _pixel_sha256(effective_native),
            "native_stats": image_stats_from_image(effective_native),
            "delivered_width": delivered.width,
            "delivered_height": delivered.height,
            "delivered_pixel_sha256": _pixel_sha256(delivered),
            "delivered_stats": image_stats_from_image(delivered),
        },
        "frame_stale": frame_stale_report.get("stale"),
        "frame_stale_detail": frame_stale_report.get("detail"),
        "fullres_file_sha256": None,
    }
    out: dict[str, Any] = {"inline": inline, "fullres_path": None, "meta": meta}
    if save_fullres:
        path = write_fullres(effective_native, save_dir=save_dir, quality=fullres_quality)
        out["fullres_path"] = path
        meta["fullres_file_sha256"] = _file_sha256(path)
    _annotate_render_frozen_signal(meta)
    return out
