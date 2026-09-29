"""Shared checks for the DayZ/Steam child environment whitelist."""

from __future__ import annotations

import unittest

from dayz_mcp.child_environment import DAYZ_CHILD_ENVIRONMENT_WHITELIST

# The names DayZ actually ran with (fb-20260926-023533-66ea). The production
# constant has to stay this tuple: a wider list would let a secret through
# the same assertion that only compares against the constant.
FICHA_CHILD_ENVIRONMENT_WHITELIST = (
    "SystemRoot",
    "windir",
    "Path",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "APPDATA",
    "LOCALAPPDATA",
    "ProgramData",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
    "CommonProgramW6432",
    "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "OS",
    "HOMEDRIVE",
    "HOMEPATH",
    "PUBLIC",
    "ALLUSERSPROFILE",
    "ComSpec",
)


def poisoned_child_environment() -> dict[str, str]:
    """A plain mapping. Windows ``os.environ`` cannot hold ``path`` and ``Path`` apart."""
    return {
        "path": r"C:\Windows\system32",
        "SystemRoot": r"C:\Windows",
        "USERNAME": "Ada",
        "FAKE_API_KEY": "sk-test",
        "PERSONAL_PASS": "secret-value",
        "DAYZ_MCP_DAEMON_SPAWN_MARKER": "create_breakaway_from_job_ok:1",
    }


def assert_whitelisted_child_environment(test: unittest.TestCase, env: object) -> None:
    test.assertEqual(
        DAYZ_CHILD_ENVIRONMENT_WHITELIST, FICHA_CHILD_ENVIRONMENT_WHITELIST
    )
    test.assertEqual(
        env,
        {
            "path": r"C:\Windows\system32",
            "SystemRoot": r"C:\Windows",
            "USERNAME": "Ada",
        },
    )
