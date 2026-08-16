from __future__ import annotations

import base64
import io
import json
import math
import os
import subprocess
import tempfile
import time
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

# Canonical host-side grab lives in spike0/mcp-grab.ps1 (single source of truth: this module, the
# spike0 enumerator and the A6 gate all call it). The old embedded CopyFromScreen-of-rect snippet
# captured stale desktop content; mcp-grab.ps1 uses PrintWindow(PW_RENDERFULLCONTENT) -> the window's
# own surface, robust to occlusion. See that file's header for the root cause (2026-06-14).
GRAB_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike0", "mcp-grab.ps1")

try:
    LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    LANCZOS = Image.LANCZOS


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


def apply_crop(img: Image.Image, crop: str) -> Image.Image:
    """Crop the frame BEFORE the budget downscale so the whole budget is spent on the subject
    (effective zoom). Accepts:
      ""                      -> no crop
      "center" / "center:F"   -> centered box covering fraction F of each axis (default 0.5)
      "l,t,r,b"               -> normalized bbox in [0,1] (e.g. "0.25,0.1,0.75,0.9")
    Invalid/degenerate specs return the image unchanged (fail-open: never lose the frame)."""
    spec = (crop or "").strip().lower()
    if not spec:
        return img
    w, h = img.size
    try:
        if spec.startswith("center"):
            frac = 0.5
            if ":" in spec:
                frac = float(spec.split(":", 1)[1])
            frac = min(1.0, max(0.05, frac))
            cw, ch = max(1, int(w * frac)), max(1, int(h * frac))
            x0, y0 = (w - cw) // 2, (h - ch) // 2
            box = (x0, y0, x0 + cw, y0 + ch)
        else:
            l, t, r, b = (float(v) for v in spec.split(","))
            l, t, r, b = max(0.0, l), max(0.0, t), min(1.0, r), min(1.0, b)
            box = (int(l * w), int(t * h), int(r * w), int(b * h))
            if box[2] <= box[0] or box[3] <= box[1]:
                return img
    except (ValueError, IndexError):
        return img
    return img.crop(box)


def image_content_from_image(
    img: Image.Image,
    scale: str | int = "small",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    fmt: str = DEFAULT_FORMAT,
    quality: int = DEFAULT_QUALITY,
    crop: str = "",
) -> dict[str, Any]:
    rgb = apply_crop(img.convert("RGB"), crop)
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


def choose_stable_frame(frames: list[Image.Image]) -> Image.Image:
    if not frames:
        raise ValueError("no frames captured")
    if len(frames) == 1:
        return frames[0]

    pair_deltas = [mean_abs_pixel_delta(frames[i - 1], frames[i]) for i in range(1, len(frames))]
    scores: list[float] = []
    for index in range(len(frames)):
        adjacent: list[float] = []
        if index > 0:
            adjacent.append(pair_deltas[index - 1])
        if index < len(pair_deltas):
            adjacent.append(pair_deltas[index])
        scores.append(min(adjacent))
    best_index = min(range(len(scores)), key=lambda idx: scores[idx])
    return frames[best_index]


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
    capture_screenshot so the dual channel (full-res to disk + inline thumbnail) shares one grab."""
    frame_count = max(1, min(int(frames), 5))
    with tempfile.TemporaryDirectory(prefix="mcp_capture_") as tmp_dir:
        captured: list[Image.Image] = []
        capture_results: list[tuple[Image.Image, dict[str, Any]]] = []
        for index in range(frame_count):
            output_path = os.path.join(tmp_dir, f"frame_{index}.png")
            result = _run_window_capture(output_path, process_name=process_name, timeout_s=8.0, method=method, client_pid=client_pid, cmdline_match=cmdline_match)
            if result.get("ok") is not True:
                return _error(str(result.get("error") or "window_capture_failed"))
            with Image.open(output_path) as img:
                frame = img.convert("RGB").copy()
                captured.append(frame)
                capture_results.append((frame, result))
            if index + 1 < frame_count:
                time.sleep(DEFAULT_FRAME_INTERVAL_S)

        chosen = choose_stable_frame(captured)
        chosen_result = next(result for frame, result in capture_results if frame is chosen)
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
) -> dict[str, Any]:
    """Single grab -> inline budget-fit ImageContent (always) + optional full-res frame on disk.
    Returns {inline, fullres_path, meta} on success or {isError, error} on failure."""
    chosen = grab_stable_frame(frames=frames, process_name=process_name, method=method, client_pid=client_pid, cmdline_match=cmdline_match)
    if isinstance(chosen, dict):  # error payload
        return chosen
    inline = image_content_from_image(chosen, scale=scale, max_tokens=max_tokens, fmt=fmt, quality=quality, crop=crop)
    out: dict[str, Any] = {
        "inline": inline,
        "fullres_path": None,
        "meta": {
            "native_width": chosen.width,
            "native_height": chosen.height,
            "crop": crop or "",
            "inline_mimeType": inline.get("mimeType"),
            "inline_base64_len": len(inline.get("data") or ""),
        },
    }
    if save_fullres:
        out["fullres_path"] = write_fullres(chosen, save_dir=save_dir, quality=fullres_quality)
    return out
