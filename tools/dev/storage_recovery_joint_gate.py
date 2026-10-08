"""Real-joint Windows gate for a storage_recovery_required start (item 855c).

This process does not build, register, pack, reseal, or start a daemon. Run it
only in the reseal window, after the dayz-test launcher has been resealed from
the fixed worker and an accredited daemon is already up. The implementation
workspace cannot satisfy that, so a launch without ``--run`` exits 2.

Procedure (exclusive instance, lease, policy-admitted test mission):

1. Inventory the mission storage directory and require no journal-name conflict.
2. Plant ``STORAGE_1.modset.rotation.0123456789abcdef0123456789abcdef.json``
   (the canonical journal spelling is different; this name is the invalid one
   the recovery vocabulary calls ``journal_name_invalid``).
3. Resolve the policy alias of project ``DayZ_MCP`` whose directory is the
   supplied mission, and call the public tool
   ``dayz_test_run(project="DayZ_MCP", mode="all", mission=<that alias>)``
   through the MCP SDK stdio client. Do not substitute ``Broker.invoke``,
   ``_lifecycle_main``, the HTTP transport, ``start_run``, ``_worker_main``,
   the terminal parser, or the result builder.
4. Require ``failed``, ``storage_recovery_required``, ``journal_name_invalid``,
   the canonical remediation text, and ``run_id=null`` after a confirmed cleanup.
5. Read the audit log of the MCP instance this command targets
   (``RuntimePaths.for_token`` on ``--instance``, otherwise the default
   ``DayZ_MCP`` root). New lines for this call only. Require exactly one
   start attempt (``lifecycle_start`` or ``lifecycle_start_rejected``), one
   ``lifecycle_storage_recovery_required`` and one ``lifecycle_start_outcome``
   for the same ``run_id``, the same client session on the start and the
   outcome, reason ``journal_name_invalid``, no ``launch_identity_conflict``,
   and no spawn field (``pid``, ``owned_pids``, ``processes``, or
   ``state=RUNNING``). A second rejected start counts. Retired-run counts are
   not that evidence. The storage tree besides the planted file stays
   byte-identical.
6. Delete only the planted file and read public ``session_status``.

Mutants, each with a validly built and accredited launcher (a red seal is not
a pass):

- Restore the replay condition in ``dayz_test_worker.py`` (the ``not in
  STEAM_PREPARATION_REJECTION_CODES`` form). The gate must fail on the
  functional assertions above.
- Separately, delete the two assignments of ``storage_recovery_reason`` and
  ``storage_recovery_hint`` in ``process_lifecycle.py`` (the settled-result
  pair). The gate must fail because the public reason or remediation is absent.

Usage after reseal::

    python tools/dev/storage_recovery_joint_gate.py --run ^
        --mission C:\\path\\to\\exclusive\\mission ^
        --mcp-command "<already-running-client-command>"

``--mcp-command`` is a stdio MCP server command line. This script uses the MCP
SDK client (newline-delimited JSON, ``initialize``, bounded ``tools/call``).
It does not spawn DayZ itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path


PLANTED_NAME = (
    "STORAGE_1.modset.rotation.0123456789abcdef0123456789abcdef.json"
)
PROJECT = "DayZ_MCP"
_INIT_TIMEOUT_S = 30.0
_CALL_TIMEOUT_S = 120.0


def _policy_path() -> Path:
    """The sealed request policy the public server admits projects from."""
    return (
        Path(__file__).resolve().parents[1]
        / "native-launchers"
        / "dayz-test-v1"
        / "request-policy.json"
    )


def _same_dir(mission: Path, raw: str) -> bool:
    other = Path(raw)
    if mission.exists() and other.exists():
        try:
            return mission.resolve() == other.resolve()
        except OSError:
            return False
    return os.path.normcase(os.path.normpath(str(mission))) == os.path.normcase(
        os.path.normpath(raw)
    )


def resolve_policy_mission(
    mission: Path, policy_path: Path, project: str = PROJECT
) -> str:
    """Return the single alias of ``project`` whose path is ``mission``."""
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"launcher policy is not readable: {exc}") from exc
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        raise SystemExit("launcher policy has no projects")
    selected = next(
        (
            item
            for item in projects
            if isinstance(item, dict) and item.get("mod") == project
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise SystemExit(f"project {project} is not in the launcher policy")
    aliases = selected.get("mission_aliases")
    if not isinstance(aliases, dict) or not aliases:
        raise SystemExit(f"project {project} has no mission_aliases")
    matches = [
        token
        for token, raw in aliases.items()
        if isinstance(token, str) and token and isinstance(raw, str) and _same_dir(mission, raw)
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"supplied mission is not exactly one policy alias of {project}: {matches}"
        )
    return matches[0]


def _inventory(mission: Path) -> dict[str, tuple[str, int | None]]:
    """Relative path to (kind, sha256) so a change inside a directory is visible."""
    found: dict[str, tuple[str, int | None]] = {}
    for item in sorted(mission.rglob("*")):
        relative = item.relative_to(mission).as_posix()
        if item.is_dir():
            found[relative] = ("dir", None)
        elif item.is_file():
            digest = hashlib.sha256(item.read_bytes()).hexdigest()
            found[relative] = ("file", digest)
        else:
            found[relative] = ("other", None)
    return found


def canonical_remediation() -> str:
    tools = Path(__file__).resolve().parents[1]
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from dayz_mcp.dayz_test_storage import storage_recovery_hint

    hint = storage_recovery_hint("journal_name_invalid")
    if not isinstance(hint, str) or not hint:
        raise SystemExit("canonical journal_name_invalid remediation is missing")
    return hint


def _structured(result: object) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result")
        if isinstance(inner, dict) and "ok" not in structured:
            return inner
        return structured
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if getattr(item, "type", "") == "text" and isinstance(text, str):
            parts.append(text)
    if not parts:
        raise SystemExit("tool result had no structured content")
    try:
        parsed = json.loads("\n".join(parts))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"tool result was not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("tool result JSON was not an object")
    inner = parsed.get("result")
    if isinstance(inner, dict) and "ok" not in parsed:
        return inner
    return parsed


def _assert_public(payload: dict[str, object], remediation: str) -> None:
    if payload.get("status") != "failed":
        raise SystemExit(f"status is not failed: {payload.get('status')!r}")
    if payload.get("error_code") != "storage_recovery_required":
        raise SystemExit(f"error_code is not storage_recovery_required: {payload.get('error_code')!r}")
    if payload.get("storage_recovery_reason") != "journal_name_invalid":
        raise SystemExit(
            f"storage_recovery_reason is not journal_name_invalid: {payload.get('storage_recovery_reason')!r}"
        )
    if payload.get("remediation") != remediation:
        raise SystemExit("remediation is not the canonical storage-recovery hint")
    if payload.get("run_id") is not None:
        raise SystemExit(f"run_id was not null: {payload.get('run_id')!r}")
    if payload.get("cleanup_degraded") is not False:
        raise SystemExit(f"cleanup was not confirmed: {payload.get('cleanup_degraded')!r}")
    if payload.get("error_code") == "launch_identity_conflict" or payload.get("reason") == "launch_identity_conflict":
        raise SystemExit("launch_identity_conflict replaced the refusal")
    for field in ("server_alive", "client_alive", "process_alive"):
        if payload.get(field) is True:
            raise SystemExit(f"DayZ spawn reported in {field}")


def instance_token_from_mcp_command(mcp_command: str) -> str | None:
    """The ``--instance`` token on the MCP server command, or None for the default."""
    tools = Path(__file__).resolve().parents[1]
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from dayz_mcp.server_cli import validate_entry_selector

    parts = shlex.split(mcp_command, posix=(os.name != "nt"))
    token, _game_path = validate_entry_selector(parts)
    return token


def audit_events_path(token: str | None = None, env: os._Environ[str] | dict[str, str] | None = None) -> Path:
    """Audit file for this instance. Same root the daemon writes.

    ``token`` is the validated ``--instance`` value. Omission is the default
    ``DayZ_MCP`` store, not a hard-coded path beside a named instance.
    """
    tools = Path(__file__).resolve().parents[1]
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from dayz_mcp.runtime_state import RuntimePaths

    try:
        paths = RuntimePaths.for_token(token, env)
    except RuntimeError as exc:
        raise SystemExit(f"audit evidence is unavailable: {exc}") from exc
    return paths.audit_dir / "events.jsonl"


def _audit_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_audit_lines(lines: list[str]) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            parsed.append(item)
    return parsed


def _client_session(item: dict[str, object]) -> str | None:
    client = item.get("client")
    if not isinstance(client, dict):
        return None
    session = client.get("session")
    if isinstance(session, str) and session:
        return session
    return None


def _assert_operation(events: list[dict[str, object]], expected_session: str) -> None:
    """One start request for this call, correlated, no spawn.

    Admitted ``lifecycle_start`` and rejected ``lifecycle_start_rejected``
    both count. Recovery and outcome must share one ``run_id``. The start and
    the outcome must share one client session. A start from another session,
    or an outcome for another run, is a different operation.
    """
    attempts = [
        item
        for item in events
        if item.get("event") in {"lifecycle_start", "lifecycle_start_rejected"}
    ]
    if len(attempts) != 1:
        raise SystemExit(f"expected exactly one lifecycle start request, saw {len(attempts)}")
    if attempts[0].get("event") != "lifecycle_start":
        raise SystemExit("the only start request was rejected before admission")
    session = _client_session(attempts[0])
    if session is None:
        raise SystemExit("lifecycle_start has no client session")
    if session != expected_session:
        raise SystemExit("the audited start request belongs to another client session")
    recoveries = [
        item
        for item in events
        if item.get("event") == "lifecycle_storage_recovery_required"
    ]
    outcomes = [
        item
        for item in events
        if item.get("event") == "lifecycle_start_outcome"
    ]
    if len(recoveries) != 1 or recoveries[0].get("reason") != "journal_name_invalid":
        raise SystemExit("audit is missing one journal_name_invalid storage recovery")
    if len(outcomes) != 1 or outcomes[0].get("reason") != "storage_recovery_required":
        raise SystemExit("audit is missing one storage_recovery_required start outcome")
    run_id = recoveries[0].get("run_id")
    if not isinstance(run_id, str) or not run_id or outcomes[0].get("run_id") != run_id:
        raise SystemExit("storage recovery and start outcome are not the same run")
    if attempts[0].get("run_id") not in (None, run_id):
        raise SystemExit("lifecycle_start run_id does not match the recovery")
    if _client_session(outcomes[0]) != session:
        raise SystemExit("start outcome is not the same client session as the start")
    for item in events:
        if item.get("state") == "RUNNING":
            raise SystemExit("audit records a running DayZ process")
        for key, value in item.items():
            if key in {"pid", "owned_pids", "processes"} and value not in (None, [], ()):
                raise SystemExit(f"audit records a spawn in {key}")
            if value == "launch_identity_conflict":
                raise SystemExit("audit records launch_identity_conflict")
            if isinstance(value, str) and "launch_identity_conflict" in value:
                raise SystemExit("audit records launch_identity_conflict")


def _assert_storage(
    before: dict[str, tuple[str, int | None]],
    after: dict[str, tuple[str, int | None]],
) -> None:
    planted = {PLANTED_NAME}
    def _without(tree: dict[str, tuple[str, int | None]]) -> dict[str, tuple[str, int | None]]:
        return {key: value for key, value in tree.items() if key not in planted and not key.startswith(PLANTED_NAME + "/")}

    left = _without(before)
    right = _without(after)
    if left != right:
        raise SystemExit("storage tree changed besides the planted journal")


async def _own_client_session(session: object) -> str:
    """This caller's own client session, as the daemon records it in the audit.

    The lease owner block of session_status names the holder's client session; the gate takes the lease only
    to read it and releases it before dayz_test_run, which must be called without a lease.
    """
    acquired = await _call(session, "session_acquire_wait", {"purpose": "storage recovery joint gate: caller identity"})
    token = acquired.get("lease_token")
    if not isinstance(token, str) or not token:
        raise SystemExit("could not acquire a lease to read this caller's session")
    try:
        status = await _call(session, "session_status", {})
    finally:
        await _call(session, "session_release", {"lease_token": token})
    owner = status.get("owner")
    client = owner.get("client") if isinstance(owner, dict) else None
    caller = client.get("session") if isinstance(client, dict) else None
    if not isinstance(caller, str) or not caller:
        raise SystemExit("session_status does not name this caller's client session")
    return caller


async def _call(session: object, name: str, arguments: dict[str, object]) -> dict[str, object]:
    from datetime import timedelta

    try:
        result = await asyncio.wait_for(
            session.call_tool(  # type: ignore[attr-defined]
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=_CALL_TIMEOUT_S),
            ),
            timeout=_CALL_TIMEOUT_S + 5.0,
        )
    except TimeoutError as exc:
        raise SystemExit(f"{name} exceeded {_CALL_TIMEOUT_S}s") from exc
    if getattr(result, "isError", False):
        raise SystemExit(f"{name} returned isError")
    return _structured(result)


async def _run_async(
    mission: Path,
    mcp_command: str,
    policy_path: Path,
    audit_path: Path | None = None,
) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    alias = resolve_policy_mission(mission, policy_path, PROJECT)
    parts = shlex.split(mcp_command, posix=(os.name != "nt"))
    if not parts:
        raise SystemExit("mcp command is empty")
    remediation = canonical_remediation()
    before = _inventory(mission)
    conflict = [
        name
        for name in before
        if "rotation" in Path(name).name.casefold() and Path(name).name != PLANTED_NAME
    ]
    if conflict:
        raise SystemExit(f"mission already has rotation names: {conflict}")
    planted = mission / PLANTED_NAME
    if planted.exists():
        raise SystemExit("planted journal already exists; refusing to overwrite")
    server = StdioServerParameters(command=parts[0], args=parts[1:])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            try:
                await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT_S)
            except TimeoutError as exc:
                raise SystemExit("initialize exceeded the bounded wait") from exc
            audit_file = (
                audit_path
                if audit_path is not None
                else audit_events_path(instance_token_from_mcp_command(mcp_command))
            )
            caller_session = await _own_client_session(session)
            audit_before = _audit_lines(audit_file)
            planted.write_text("{}\n", encoding="utf-8")
            try:
                called = await _call(
                    session,
                    "dayz_test_run",
                    {"project": PROJECT, "mode": "all", "mission": alias},
                )
                _assert_public(called, remediation)
                audit_after = _audit_lines(audit_file)
                if audit_after[: len(audit_before)] != audit_before:
                    raise SystemExit("audit log rotated during the call; evidence is incomplete")
                _assert_operation(
                    _parse_audit_lines(audit_after[len(audit_before) :]), caller_session
                )
                after = _inventory(mission)
                _assert_storage(before, after)
            finally:
                if planted.is_file():
                    planted.unlink()
            status = await _call(session, "session_status", {})
            print(json.dumps({"mission": alias, "dayz_test_run": called, "session_status": status}, indent=2))
    print("joint gate assertions passed")
    return 0


def run_gate(
    mission: Path,
    mcp_command: str,
    policy_path: Path | None = None,
    audit_path: Path | None = None,
) -> int:
    if not mission.is_dir():
        raise SystemExit(f"mission is not a directory: {mission}")
    path = policy_path if policy_path is not None else _policy_path()
    return asyncio.run(_run_async(mission, mcp_command, path, audit_path))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--mcp-command")
    parser.add_argument("--policy", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        print(__doc__)
        print("INCONCLUSO: resealed launcher and live daemon were not provided.")
        return 2
    if args.mission is None or not args.mcp_command:
        print("A --run needs --mission and --mcp-command.", file=sys.stderr)
        return 2
    if os.environ.get("DAYZ_MCP_STORAGE_RECOVERY_JOINT") != "1":
        print(
            "Refusing: set DAYZ_MCP_STORAGE_RECOVERY_JOINT=1 only in the reseal window.",
            file=sys.stderr,
        )
        return 2
    return run_gate(args.mission, args.mcp_command, args.policy)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
