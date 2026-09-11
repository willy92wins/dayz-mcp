from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


_HOST_TOOLS: Path | None = None


def _checkout_tools_dir() -> Path:
    # This file lives at tools/tests/fixtures/dayz_mcp/sitecustomize.py.
    return Path(__file__).resolve().parents[3]


def _capture_host_tools() -> Path | None:
    """Tools dir that owns the approved interpreter.

    Independent of ``sys.executable``: an env the parent set, or the
    editable mapping already installed in this process. Using the
    executable under test as the authority would approve any
    ``.venv-mcp/Scripts/python.exe``.
    """
    raw = os.environ.get("DAYZ_MCP_HOST_TOOLS")
    if raw:
        candidate = Path(raw)
        if (candidate / "dayz_mcp" / "daemon.py").is_file():
            return candidate.resolve()
    from tests._tree_identity import editable_mapped_tools_dir

    return editable_mapped_tools_dir()


def _bind_checkout_tree() -> None:
    """Rebind ``dayz_mcp`` onto this checkout before any fixture import.

    Site ``.pth`` files install the editable finder before sitecustomize
    runs. PYTHONPATH here is the fixture directory, which has no
    ``dayz_mcp`` package, so PathFinder misses and the finder serves the
    live tree. Capture the host tools dir from that mapping (or
    ``DAYZ_MCP_HOST_TOOLS``) before dropping it, then pin this checkout.
    """
    global _HOST_TOOLS
    tools_dir = _checkout_tools_dir()
    sys.path.insert(0, str(tools_dir))
    from tests._tree_identity import bind_child_import_tree

    _HOST_TOOLS = _capture_host_tools()
    bind_child_import_tree(tools_dir)


def _install_fixture() -> None:
    _bind_checkout_tree()
    mode = os.environ.get("DAYZ_MCP_FIXTURE_MODE")
    if mode is None:
        return

    from dayz_mcp import daemon
    from dayz_mcp.identity_migration import (
        RunsBackupGateError,
        capture_launch_ancestor_identity,
        scan_dayz_mcp_processes,
    )
    from dayz_mcp.native_process_guard import NativeProcessGuard

    signal = Path(os.environ["DAYZ_MCP_FIXTURE_SIGNAL"])
    migration = Path(os.environ["DAYZ_MCP_FIXTURE_MIGRATION"])

    def die(stage: str) -> None:
        signal.write_text(stage, encoding="ascii")
        os._exit(91)

    original_run_daemon = daemon.run_daemon
    original_ensure_runs_v1_backup = daemon.ensure_runs_v1_backup

    def isolated_ensure_runs_v1_backup(*args: object, **kwargs: object) -> object:
        kwargs["scan_fn"] = lambda *_args, **_kwargs: ()
        kwargs["listener_fn"] = lambda *_args, **_kwargs: False
        return original_ensure_runs_v1_backup(*args, **kwargs)

    daemon.ensure_runs_v1_backup = isolated_ensure_runs_v1_backup

    original_ensure_identity_migration = daemon._ensure_identity_migration

    def ensure_identity_when_copy_has_no_venv(config: object) -> None:
        sibling = (
            Path(daemon.__file__).resolve().parents[1]
            / ".venv-mcp"
            / "Scripts"
            / "python.exe"
        )
        if sibling.is_file():
            return original_ensure_identity_migration(config)
        # The copy has no sibling venv. Relocate Path(__file__) onto the
        # host package so the original lookup finds that tree's interpreter.
        # The host path is captured from DAYZ_MCP_HOST_TOOLS or the editable
        # mapping, never from sys.executable.
        host_daemon = None
        if _HOST_TOOLS is not None:
            candidate = _HOST_TOOLS / "dayz_mcp" / "daemon.py"
            if candidate.is_file():
                host_daemon = candidate
        if host_daemon is None:
            return original_ensure_identity_migration(config)
        previous = daemon.__file__
        daemon.__file__ = str(host_daemon.resolve())
        try:
            return original_ensure_identity_migration(config)
        finally:
            daemon.__file__ = previous

    daemon._ensure_identity_migration = ensure_identity_when_copy_has_no_venv

    def fixture_run_daemon(config: object) -> int:
        config = SimpleNamespace(
            **vars(config),
            _identity_migration_dir=str(migration),
        )
        if mode == "wave":
            start = Path(os.environ["DAYZ_MCP_FIXTURE_START"])
            fixture_pids_path = Path(os.environ["DAYZ_MCP_FIXTURE_PIDS"])
            while not start.exists():
                daemon.time.sleep(0.005)
            fixture_pids = set(
                json.loads(fixture_pids_path.read_text(encoding="utf-8"))
            )

            def fixture_migration(_config: object) -> None:
                guard = NativeProcessGuard()
                identity = guard.snapshot(os.getpid())
                ancestor = capture_launch_ancestor_identity(
                    identity, Path(sys.executable), guard=guard
                )
                blockers = tuple(
                    pid
                    for pid in scan_dayz_mcp_processes(identity, ancestor)
                    if pid in fixture_pids
                )
                if blockers:
                    raise RunsBackupGateError("dayz_mcp_process_present")

            daemon._ensure_identity_migration = fixture_migration
            daemon.MIGRATION_CANDIDATE_DRAIN_S = 5.0
        elif mode == "migration":
            def fault(phase: str) -> None:
                if phase == "after_backup_write":
                    die(mode)

            config._identity_migration_fault_injector = fault
        elif mode == "bind":
            original = daemon._bind_with_reclaim

            def crash_after_bind(*args: object, **kwargs: object) -> object:
                result = original(*args, **kwargs)
                if result is not None:
                    die(mode)
                return result

            daemon._bind_with_reclaim = crash_after_bind
        elif mode == "activation":
            original = daemon._activate_server_coordination

            def crash_after_activation(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                die(mode)

            daemon._activate_server_coordination = crash_after_activation
        elif mode == "status":
            original = daemon._status_accredits_generation

            def crash_after_status(*args: object, **kwargs: object) -> bool:
                result = original(*args, **kwargs)
                if result:
                    die(mode)
                return result

            daemon._status_accredits_generation = crash_after_status
        elif mode != "none":
            raise RuntimeError("invalid_fixture_mode")
        return original_run_daemon(config)

    daemon.run_daemon = fixture_run_daemon


_install_fixture()
