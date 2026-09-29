"""Environment block for a DayZ or Steam child of this process."""

from __future__ import annotations

import os

# fb-20260926-023533-66ea: the daemon inherits the agent session, and a crash
# dump of the child keeps that block. ProcessLifecycle._launch and the Steam
# restarts keep only these names. Match is case-insensitive; the child keeps
# the parent's casing. A name that is absent stays absent. Nothing is added.
DAYZ_CHILD_ENVIRONMENT_WHITELIST: tuple[str, ...] = (
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

_WHITELIST_CASEFOLD = frozenset(
    name.casefold() for name in DAYZ_CHILD_ENVIRONMENT_WHITELIST
)


def whitelisted_child_environment() -> dict[str, str]:
    """Copy of ``os.environ`` limited to ``DAYZ_CHILD_ENVIRONMENT_WHITELIST``."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.casefold() in _WHITELIST_CASEFOLD
    }
