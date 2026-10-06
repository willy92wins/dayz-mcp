"""Per-test override of the capture frame-state sidecar.

Production resolution is env, then %LOCALAPPDATA%\\DayZ_MCP. A test that only
setdefault's the env var still honours an inherited path and can write the
real profile. setUp must replace DAYZ_MCP_FRAME_STATE_PATH for that test and
restore it afterwards.

OPEN_FRAME_STATE_DIRS is the set of directories this process has installed
for the current test. The regression guard reads it while the writer runs.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import mcp_capture

OPEN_FRAME_STATE_DIRS: list[str] = []


def note_frame_state_dir(directory: str) -> None:
    """Remember an isolated directory that already overrides the sidecar path."""
    root = os.path.abspath(directory)
    if root not in OPEN_FRAME_STATE_DIRS:
        OPEN_FRAME_STATE_DIRS.append(root)


def isolate_capture_frame_state(case: unittest.TestCase) -> str:
    """Point this test's frame-state file at a private temporary directory.

    Replaces any inherited DAYZ_MCP_FRAME_STATE_PATH. addCleanup restores the
    environment and deletes the directory.
    """
    directory = tempfile.TemporaryDirectory(prefix="frame_state_iso_")
    case.addCleanup(directory.cleanup)
    note_frame_state_dir(directory.name)
    root = os.path.abspath(directory.name)
    case.addCleanup(_drop_frame_state_dir, root)
    path = os.path.join(directory.name, mcp_capture.FRAME_STATE_FILENAME)
    patcher = mock.patch.dict(os.environ, {mcp_capture.FRAME_STATE_ENV: path})
    patcher.start()
    case.addCleanup(patcher.stop)
    return path


def _drop_frame_state_dir(root: str) -> None:
    try:
        OPEN_FRAME_STATE_DIRS.remove(root)
    except ValueError:
        pass
