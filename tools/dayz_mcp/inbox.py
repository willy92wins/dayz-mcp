from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path


INBOX_DIR = Path(os.environ["LOCALAPPDATA"]) / "DayZ_MCP" / "inbox"
FEEDBACK_PATH = INBOX_DIR / "feedback.jsonl"
KINDS = frozenset({"bug", "request", "tool_contribution", "finding"})
_FEEDBACK_ID_RE = re.compile(r"^fb-\d{8}-\d{6}-[0-9a-f]{4}$")


def _require_str(value: object, field: str = "value") -> str:
    if type(value) is not str:
        raise ValueError(f"bad_args: {field} must be a string")
    return value


def _utc_now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(record: dict) -> None:
    # a unique append <4KB on a local volume = concurrent processes' lines
    # do not interleave; that is why this file is NEVER rewritten, only
    # appended (resolutions are appends too).
    os.makedirs(INBOX_DIR, exist_ok=True)
    payload = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    fd = os.open(FEEDBACK_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def append_feedback(
    kind: str,
    title: str,
    body: str,
    project: str = "",
    platform: str = "",
) -> dict:
    kind = _require_str(kind, "kind")
    title = _require_str(title, "title").strip()
    body = _require_str(body, "body")
    project = _require_str(project, "project").strip()
    platform = _require_str(platform, "platform")
    if kind not in KINDS:
        raise ValueError("bad_args: kind not in bug|request|tool_contribution|finding")
    if not 1 <= len(title) <= 120:
        raise ValueError("bad_args: title > 120 chars" if title else "bad_args: title empty")
    if not 1 <= len(body) <= 8000:
        raise ValueError("bad_args: body > 8000 chars" if body else "bad_args: body empty")
    if len(project) > 64:
        raise ValueError("bad_args: project > 64 chars")
    stamp, ts = _utc_now()
    entry = {
        "id": f"fb-{stamp}-{secrets.token_hex(2)}",
        "ts": ts,
        "kind": kind,
        "title": title,
        "body": body,
        "project": project,
        "platform": platform,
    }
    _append_jsonl(entry)
    result = dict(entry)
    result["path"] = str(FEEDBACK_PATH)
    return result


def append_resolution(
    feedback_id: str,
    resolution: str,
    platform: str = "",
) -> dict:
    feedback_id = _require_str(feedback_id, "feedback_id")
    resolution = _require_str(resolution, "resolution")
    platform = _require_str(platform, "platform")
    if _FEEDBACK_ID_RE.fullmatch(feedback_id) is None:
        raise ValueError("bad_args: feedback_id must match fb-YYYYMMDD-HHMMSS-xxxx")
    if not 1 <= len(resolution) <= 2000:
        raise ValueError(
            "bad_args: resolution > 2000 chars" if resolution else "bad_args: resolution empty"
        )
    _stamp, ts = _utc_now()
    record = {
        "resolves": feedback_id,
        "ts": ts,
        "resolution": resolution,
        "platform": platform,
    }
    _append_jsonl(record)
    return record


def read_inbox(limit: int = 20, kind: str = "", include_resolved: bool = False) -> dict:
    if type(kind) is not str or (kind != "" and kind not in KINDS):
        raise ValueError("bad_args: kind not in bug|request|tool_contribution|finding")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("bad_args: limit not in 1..100")
    if type(include_resolved) is not bool:
        raise ValueError("bad_args: include_resolved must be a bool")

    entries: list[dict] = []
    by_id: dict[str, dict] = {}
    malformed = 0
    if FEEDBACK_PATH.is_file():
        text = FEEDBACK_PATH.read_text(encoding="utf-8")
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if type(obj) is not dict:
                malformed += 1
                continue
            if "resolves" in obj:
                target = obj.get("resolves")
                if type(target) is not str:
                    malformed += 1
                    continue
                if target in by_id:
                    by_id[target]["resolution"] = obj.get("resolution")
                    by_id[target]["resolved_ts"] = obj.get("ts")
                continue
            entry_id = obj.get("id")
            if type(entry_id) is not str:
                malformed += 1
                continue
            item = dict(obj)
            entries.append(item)
            by_id[entry_id] = item

    count_total = len(entries)
    unresolved_total = sum(1 for item in entries if "resolution" not in item)
    ranked: list[tuple[str, int, dict]] = []
    for index, item in enumerate(entries):
        if kind != "" and item.get("kind") != kind:
            continue
        if not include_resolved and "resolution" in item:
            continue
        ranked.append((str(item.get("ts", "")), index, item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return {
        "entries": [row[2] for row in ranked[:limit]],
        "count_total": count_total,
        "unresolved_total": unresolved_total,
        "malformed": malformed,
    }
