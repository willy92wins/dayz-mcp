from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import subprocess
import tempfile
import time
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
DEFAULT_STABILITY_THRESHOLD = 0.03

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


def _run_window_capture(output_path: str, process_name: str, timeout_s: float, method: str = "auto", client_pid: int = 0, cmdline_match: str = "") -> dict[str, Any]:
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
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        payload: dict[str, Any] = {}
        if stdout_lines:
            try:
                parsed = json.loads(stdout_lines[-1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}
        if not payload:
            stderr = proc.stderr.strip()
            return {"ok": False, "error": f"capture_backend_failed: {stderr or 'no_json'}"}
        if payload.get("ok") is True and not os.path.exists(output_path):
            return {"ok": False, "error": "capture_backend_failed: missing_png"}
        return payload
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "capture_timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"capture_backend_failed: {exc}"}


def grab_window_to_file(output_path: str, process_name: str = "DayZDiag_x64", method: str = "auto", timeout_s: float = 8.0, client_pid: int = 0, cmdline_match: str = "") -> dict[str, Any]:
    """Single host-side grab to a PNG file. Returns the backend payload
    ({ok, method, error, window, stats, client, clientStats, sha256}). Public so content-validation
    harnesses can grab twice (subject vs control) and diff the actual pixels. cmdline_match (preferred) or client_pid
    restrict the grab to the target client's render window so a second DayZ client (e.g. LFQuad) cannot
    be captured by mistake. cmdline_match is robust to DayZDiag's launcher-pid != window-pid mismatch."""
    return _run_window_capture(output_path, process_name=process_name, timeout_s=timeout_s, method=method, client_pid=client_pid, cmdline_match=cmdline_match)


def grab_stable_frame(
    frames: int = DEFAULT_FRAME_COUNT,
    process_name: str = "DayZDiag_x64",
    method: str = "auto",
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
            return _error("frame_client_all_black")
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
    method: str = "auto",
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
    method: str = "auto",
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
    return out
