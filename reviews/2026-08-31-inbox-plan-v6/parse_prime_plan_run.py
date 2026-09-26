from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


GENERATION = "20260831-ledger-v3"
GRAPH = "inbox-20260831-v6"
AUTHORITY_SHA = "ca3939dc2f9ab456598688837ff09feb842888b7cf99fcd04989d8c1cc370512"
MANIFEST_SHA = "e32fa44e4875f07817ed2c2962f414fea033168dad47cf55d06a1a67fe54647d"
PROVIDER = "anthropic"
MODEL = "claude-opus-5"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TABLE_ROW = re.compile(
    r"^\| `(?P<id>fb-[^`]+)` \| `(?P<path>[^`]+)` \| `(?P<sha>[0-9a-f]{64})` \|$"
)
FORBIDDEN_TOOL_PATTERNS = (
    (re.compile(r"(^|[\n;])\s*!?ls(?=\s*(?:$|[|&;])|\s+[^=])"), "workspace listing via ls"),
    (re.compile(r"(^|[\n;])\s*!?find(?=\s*(?:$|[|&;])|\s+[^=])"), "workspace listing via find"),
    (re.compile(r"(^|[\n;])\s*!?tree(?=\s*(?:$|[|&;])|\s+[^=])"), "workspace listing via tree"),
    (re.compile(r"\bos\.listdir\s*\("), "workspace listing via os.listdir"),
    (re.compile(r"\.iterdir\s*\("), "workspace listing via Path.iterdir"),
    (re.compile(r"\.(?:r?glob)\s*\("), "workspace listing via glob"),
    (re.compile(r"\bos\.walk\s*\("), "workspace traversal via os.walk"),
    (re.compile(r"(?:^|[/'\"])(?:GATES\.md|gates/|reports/|reviews/)"), "forbidden review/gate/report path"),
    (re.compile(r"(^|[\n;])\s*git\s+(?:log|diff|show|status|branch|rev-list)\b"), "forbidden git history/diff"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = ANSI.sub("", raw_line).strip().rstrip("\r")
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return events


def assistant_messages(event: dict) -> list[dict]:
    messages: list[dict] = []
    message = event.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        messages.append(message)
    for candidate in event.get("messages", []):
        if isinstance(candidate, dict) and candidate.get("role") == "assistant":
            messages.append(candidate)
    return messages


def message_text(message: dict) -> str:
    chunks: list[str] = []
    for part in message.get("content", []):
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return "".join(chunks).strip()


def tool_code(event: dict) -> list[str]:
    codes: list[str] = []
    if event.get("type") not in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
        return codes
    args = event.get("args")
    if isinstance(args, dict) and isinstance(args.get("code"), str):
        codes.append(args["code"])
    return codes


def parse_turn(path: Path) -> dict:
    events = load_events(path)
    result: dict = {
        "raw_path": str(path),
        "raw_sha256": sha256(path),
        "session_id": None,
        "providers": [],
        "models": [],
        "turn_stop_reason": None,
        "agent_stop_reason": None,
        "error_messages": [],
        "contamination": [],
        "output": None,
    }
    session = next((event for event in events if event.get("type") == "session"), None)
    if session:
        result["session_id"] = session.get("id")

    providers: set[str] = set()
    models: set[str] = set()
    for event in events:
        for message in assistant_messages(event):
            if isinstance(message.get("provider"), str):
                providers.add(message["provider"])
            if isinstance(message.get("model"), str):
                models.add(message["model"])
            if isinstance(message.get("errorMessage"), str) and message["errorMessage"]:
                result["error_messages"].append(message["errorMessage"])
        for code in tool_code(event):
            for pattern, label in FORBIDDEN_TOOL_PATTERNS:
                if pattern.search(code):
                    result["contamination"].append(label)

    result["providers"] = sorted(providers)
    result["models"] = sorted(models)
    result["contamination"] = sorted(set(result["contamination"]))

    turn_ends = [event for event in events if event.get("type") == "turn_end"]
    if turn_ends:
        message = turn_ends[-1].get("message", {})
        result["turn_stop_reason"] = message.get("stopReason")

    agent_ends = [event for event in events if event.get("type") == "agent_end"]
    final_message: dict | None = None
    if agent_ends:
        candidates = agent_ends[-1].get("messages", [])
        if candidates and isinstance(candidates[-1], dict):
            final_message = candidates[-1]
            result["agent_stop_reason"] = final_message.get("stopReason")
    if final_message is None and turn_ends:
        candidate = turn_ends[-1].get("message")
        if isinstance(candidate, dict):
            final_message = candidate

    if final_message is not None:
        text = message_text(final_message)
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, dict):
            result["output"] = value
    return result


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


def validate_output(value: object, group: str, expected: list[dict], calibration: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["assistant final text is not one JSON object"]
    exact = {
        "schema_version": 1,
        "generation": GENERATION,
        "graph_version": GRAPH,
        "group": group.upper(),
        "authority_bundle_sha256": AUTHORITY_SHA,
        "plan_manifest_sha256": MANIFEST_SHA,
    }
    for key, expected_value in exact.items():
        if value.get(key) != expected_value:
            errors.append(f"{key} mismatch")
    if calibration and value.get("calibration_result") != "CONFIRMED":
        errors.append("calibration_result is not CONFIRMED")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != len(expected):
        errors.append("entry count mismatch")
        entries = []
    for index, expected_entry in enumerate(expected):
        if index >= len(entries) or not isinstance(entries[index], dict):
            continue
        entry = entries[index]
        for key, expected_value in expected_entry.items():
            if entry.get(key) != expected_value:
                errors.append(f"entry {index} {key} mismatch")
        verdict = entry.get("verdict")
        if verdict not in {"PASS", "REVISE", "INCONCLUSIVE"}:
            errors.append(f"entry {index} invalid verdict")
        findings = entry.get("findings")
        open_findings = entry.get("open_findings")
        if not isinstance(findings, list) or not isinstance(open_findings, int):
            errors.append(f"entry {index} findings schema invalid")
        elif open_findings != len(findings):
            errors.append(f"entry {index} open_findings mismatch")
        if verdict == "PASS" and (open_findings != 0 or findings != []):
            errors.append(f"entry {index} PASS has findings")
        if not isinstance(entry.get("why"), str) or not entry["why"].strip():
            errors.append(f"entry {index} missing why")
    verdicts = [entry.get("verdict") for entry in entries if isinstance(entry, dict)]
    expected_group = "PASS" if len(verdicts) == len(expected) and all(v == "PASS" for v in verdicts) else (
        "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts else "REVISE"
    )
    if value.get("group_verdict") != expected_group:
        errors.append("group_verdict inconsistent with entries")
    return errors


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--group-prompt", type=Path, required=True)
    parser.add_argument("--raw-review", type=Path, required=True)
    parser.add_argument("--raw-calibration", type=Path, required=True)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args()

    args.dest.mkdir(parents=True, exist_ok=False)
    copied_review = args.dest / "review-turn.raw"
    copied_calibration = args.dest / "calibration-turn.raw"
    copied_session = args.dest / "session.jsonl"
    shutil.copyfile(args.raw_review, copied_review)
    shutil.copyfile(args.raw_calibration, copied_calibration)
    shutil.copyfile(args.session_file, copied_session)

    expected = expected_entries(args.group_prompt)
    review = parse_turn(copied_review)
    calibration = parse_turn(copied_calibration)
    errors: list[str] = []
    if len(expected) != 5:
        errors.append(f"expected table has {len(expected)} rows")
    for label, turn in (("review", review), ("calibration", calibration)):
        if turn["providers"] != [PROVIDER]:
            errors.append(f"{label} provider set mismatch: {turn['providers']}")
        if turn["models"] != [MODEL]:
            errors.append(f"{label} model set mismatch: {turn['models']}")
        if turn["turn_stop_reason"] != "stop":
            errors.append(f"{label} turn stopReason is {turn['turn_stop_reason']!r}")
        if turn["agent_stop_reason"] != "stop":
            errors.append(f"{label} agent stopReason is {turn['agent_stop_reason']!r}")
        if turn["error_messages"]:
            errors.append(f"{label} has assistant errorMessage")
        if turn["contamination"]:
            errors.append(f"{label} contamination: {turn['contamination']}")
    if not review["session_id"] or review["session_id"] != calibration["session_id"]:
        errors.append("session id missing or differs across turns")
    errors.extend(f"review output: {item}" for item in validate_output(review["output"], args.group, expected, False))
    errors.extend(
        f"calibration output: {item}"
        for item in validate_output(calibration["output"], args.group, expected, True)
    )

    if isinstance(review["output"], dict):
        write_json(args.dest / "review-output.json", review["output"])
    if isinstance(calibration["output"], dict):
        write_json(args.dest / "calibration-output.json", calibration["output"])
        shutil.copyfile(args.dest / "calibration-output.json", args.dest / "final-review.json")

    final_value = calibration["output"] if isinstance(calibration["output"], dict) else {}
    metadata = {
        "schema_version": 1,
        "generation": GENERATION,
        "group": args.group.upper(),
        "attempt": args.attempt,
        "session_id": review["session_id"],
        "calibration_session_id": calibration["session_id"],
        "provider": PROVIDER,
        "model": MODEL,
        "review_turn_sha256": sha256(copied_review),
        "calibration_turn_sha256": sha256(copied_calibration),
        "session_sha256": sha256(copied_session),
        "review_stop_reason": review["turn_stop_reason"],
        "review_agent_stop_reason": review["agent_stop_reason"],
        "calibration_stop_reason": calibration["turn_stop_reason"],
        "calibration_agent_stop_reason": calibration["agent_stop_reason"],
        "calibration_result": final_value.get("calibration_result"),
        "group_verdict": final_value.get("group_verdict"),
        "open_findings": sum(
            entry.get("open_findings", 0)
            for entry in final_value.get("entries", [])
            if isinstance(entry, dict) and isinstance(entry.get("open_findings", 0), int)
        ),
        "valid_transport_and_schema": not errors,
        "errors": errors,
    }
    if (args.dest / "final-review.json").exists():
        metadata["final_review_sha256"] = sha256(args.dest / "final-review.json")
    write_json(args.dest / "metadata.json", metadata)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
