from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


GENERATION = "20260831-ledger-v3"
GRAPH = "inbox-20260831-v6"
AUTHORITY_SHA = "ca3939dc2f9ab456598688837ff09feb842888b7cf99fcd04989d8c1cc370512"
MANIFEST_SHA = "e32fa44e4875f07817ed2c2962f414fea033168dad47cf55d06a1a67fe54647d"
MODEL_DISPLAY = "Cursor Grok 4.6 Medium"
TABLE_ROW = re.compile(
    r"^\| `(?P<id>fb-[^`]+)` \| `(?P<path>[^`]+)` \| `(?P<sha>[0-9a-f]{64})` \|$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def assistant_text(event: dict) -> str | None:
    if event.get("type") != "assistant":
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    chunks = [
        part.get("text", "")
        for part in message.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    text = "".join(chunks).strip()
    return text or None


def expected_entries(group_prompt: Path) -> list[dict]:
    entries: list[dict] = []
    for line in group_prompt.read_text(encoding="utf-8").splitlines():
        match = TABLE_ROW.match(line)
        if match:
            entries.append(
                {
                    "feedback_id": match.group("id"),
                    "plan_path": match.group("path"),
                    "plan_sha256": match.group("sha"),
                }
            )
    return entries


def validate_output(value: object, group: str, expected: list[dict]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["result is not one JSON object"]
    exact = {
        "schema_version": 1,
        "generation": GENERATION,
        "graph_version": GRAPH,
        "group": group.upper(),
        "authority_bundle_sha256": AUTHORITY_SHA,
        "plan_manifest_sha256": MANIFEST_SHA,
    }
    for key, wanted in exact.items():
        if value.get(key) != wanted:
            errors.append(f"{key} mismatch")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != len(expected):
        errors.append("entry count mismatch")
        entries = []
    for index, wanted in enumerate(expected):
        if index >= len(entries) or not isinstance(entries[index], dict):
            continue
        entry = entries[index]
        for key, wanted_value in wanted.items():
            if entry.get(key) != wanted_value:
                errors.append(f"entry {index} {key} mismatch")
        verdict = entry.get("verdict")
        findings = entry.get("findings")
        count = entry.get("open_findings")
        if verdict not in {"PASS", "REVISE", "INCONCLUSIVE"}:
            errors.append(f"entry {index} invalid verdict")
        if not isinstance(findings, list) or not isinstance(count, int) or count != len(findings):
            errors.append(f"entry {index} findings schema invalid")
        if verdict == "PASS" and (count != 0 or findings != []):
            errors.append(f"entry {index} PASS has findings")
        if not isinstance(entry.get("why"), str) or not entry["why"].strip():
            errors.append(f"entry {index} missing why")
    verdicts = [entry.get("verdict") for entry in entries if isinstance(entry, dict)]
    group_verdict = (
        "PASS"
        if len(verdicts) == len(expected) and all(item == "PASS" for item in verdicts)
        else "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts else "REVISE"
    )
    if value.get("group_verdict") != group_verdict:
        errors.append("group_verdict inconsistent")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--group-prompt", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--stream", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--process-rc", type=int, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()

    events = load_events(args.stream)
    errors: list[str] = []
    init = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    results = [event for event in events if event.get("type") == "result"]
    if args.process_rc != 0:
        errors.append(f"cursor process rc={args.process_rc}")
    if len(init) != 1:
        errors.append(f"init event count={len(init)}")
    if len(results) != 1:
        errors.append(f"result event count={len(results)}")
    session_id = init[0].get("session_id") if init else None
    model = init[0].get("model") if init else None
    if model != MODEL_DISPLAY:
        errors.append(f"model mismatch: {model!r}")
    result_event = results[-1] if results else {}
    if result_event.get("subtype") != "success" or result_event.get("is_error") is not False:
        errors.append("cursor result is not success/is_error=false")
    if not session_id or result_event.get("session_id") != session_id:
        errors.append("session id missing or differs")

    started_calls = [
        event.get("tool_call", {})
        for event in events
        if event.get("type") == "tool_call" and event.get("subtype") == "started"
    ]
    first_read = (
        started_calls[0].get("readToolCall", {}).get("args", {}).get("path")
        if started_calls else None
    )
    if first_read != r"P:\DayZ_MCP_dev\product-spec.md":
        errors.append(f"first filesystem operation mismatch: {first_read!r}")
    forbidden_path = re.compile(r"(?i)(?:^|[\\/])(?:reviews|reports|gates)(?:[\\/])|GATES\.md")
    for index, call in enumerate(started_calls):
        if forbidden_path.search(json.dumps(call, ensure_ascii=False)):
            errors.append(f"tool call {index} crossed forbidden context boundary")
    allowed_kinds = {"readToolCall", "grepToolCall", "shellToolCall"}
    for index, call in enumerate(started_calls):
        kinds = {key for key in call if key.endswith("ToolCall")}
        if not kinds or not kinds.issubset(allowed_kinds):
            errors.append(f"tool call {index} has non-read kind: {sorted(kinds)}")
        shell = call.get("shellToolCall", {}).get("args", {}).get("command")
        if shell is not None and not shell.startswith("Get-FileHash -Algorithm SHA256 -Path "):
            errors.append(f"tool call {index} has non-hash shell command")

    expected = expected_entries(args.group_prompt)
    if len(expected) != 5:
        errors.append(f"expected table has {len(expected)} rows")
    for entry in expected:
        plan = args.repo / entry["plan_path"]
        if not plan.is_file() or sha256(plan) != entry["plan_sha256"]:
            errors.append(f"plan drift: {entry['plan_path']}")

    value: object = None
    assistant_outputs = [text for event in events if (text := assistant_text(event)) is not None]
    if assistant_outputs:
        try:
            value = json.loads(assistant_outputs[-1])
        except json.JSONDecodeError:
            errors.append("last assistant message is not strict JSON")
    else:
        errors.append("assistant message missing")
    errors.extend(validate_output(value, args.group, expected))

    if isinstance(value, dict):
        (args.dest / "final-review.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    metadata = {
        "schema_version": 1,
        "attempt": args.attempt,
        "group": args.group.upper(),
        "route": "cursor-agent",
        "requested_model": "cursor-grok-4.6-medium",
        "served_model": model,
        "session_id": session_id,
        "process_rc": args.process_rc,
        "result_subtype": result_event.get("subtype"),
        "is_error": result_event.get("is_error"),
        "prompt_sha256": sha256(args.prompt),
        "stream_sha256": sha256(args.stream),
        "stderr_sha256": sha256(args.stderr),
        "group_verdict": value.get("group_verdict") if isinstance(value, dict) else None,
        "open_findings": sum(
            item.get("open_findings", 0)
            for item in value.get("entries", [])
            if isinstance(value, dict) and isinstance(item, dict)
        ) if isinstance(value, dict) else None,
        "errors": errors,
        "valid_transport_and_schema": not errors,
    }
    (args.dest / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
