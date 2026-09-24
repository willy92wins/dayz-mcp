"""WMI through COM (pywin32): the real HKCU and process creation outside the caller's app container.

Claude Desktop and Codex are MSIX apps. Every process they start -- an MCP client
included -- inherits the app's registry virtualization without having a package
identity: HKCU writes land in the app's private hive and shadow the real key for
every process of that app from then on (measured 2026-09-23 on Steam's
ActiveProcess: pid 0 inside the Claude app, the live Steam outside). WMI runs in
WmiPrvSE, outside that virtualization and outside any job of the caller: StdRegProv
reads the user's real HKCU, and Win32_Process.Create starts a process there, as the
same user in the same interactive session, with the user's default environment.

Everything here is Windows only and imports pywin32 lazily, so importing this module
never fails; callers treat any exception as "WMI unavailable" and fall back.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

_CIMV2 = r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"
_STD_REG_PROV = r"winmgmts:{impersonationLevel=impersonate}!\\.\root\default:StdRegProv"
HKEY_CURRENT_USER = 0x80000001
_SW_HIDE = 0
_CREATE_NEW_PROCESS_GROUP = 0x00000200


@contextmanager
def _com() -> Iterator[Any]:
    """This thread's COM, initialized for the call when it was not. A thread already
    in another apartment model keeps it. Callers keep COM objects in an inner frame
    so they are released before COM is torn down."""
    import pythoncom  # noqa: PLC0415 - pywin32, Windows only
    import win32com.client  # noqa: PLC0415

    initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        initialized = True
    except pythoncom.com_error:
        pass
    try:
        yield win32com.client
    finally:
        if initialized:
            pythoncom.CoUninitialize()


def _exec_create(client: Any, command_line: str, cwd: str | None) -> tuple[int, int]:
    """Win32_Process.Create by ExecMethod_ (late binding reads Create as a property)."""
    wmi = client.GetObject(_CIMV2)
    startup = wmi.Get("Win32_ProcessStartup").SpawnInstance_()
    startup.Properties_.Item("ShowWindow").Value = _SW_HIDE
    startup.Properties_.Item("CreateFlags").Value = _CREATE_NEW_PROCESS_GROUP
    process_class = wmi.Get("Win32_Process")
    params = process_class.Methods_.Item("Create").InParameters.SpawnInstance_()
    params.Properties_.Item("CommandLine").Value = command_line
    if cwd:
        params.Properties_.Item("CurrentDirectory").Value = cwd
    params.Properties_.Item("ProcessStartupInformation").Value = startup
    result = process_class.ExecMethod_("Create", params)
    return (
        int(result.Properties_.Item("ReturnValue").Value),
        int(result.Properties_.Item("ProcessId").Value or 0),
    )


def create_process(command_line: str, cwd: str | None) -> tuple[int, int]:
    """(ReturnValue, ProcessId) of a hidden Win32_Process.Create in its own group."""
    with _com() as client:
        return _exec_create(client, command_line, cwd)


def _exec_get_dword(client: Any, subkey: str, name: str) -> tuple[int, object]:
    registry = client.GetObject(_STD_REG_PROV)
    params = registry.Methods_.Item("GetDWORDValue").InParameters.SpawnInstance_()
    params.Properties_.Item("hDefKey").Value = HKEY_CURRENT_USER
    params.Properties_.Item("sSubKeyName").Value = subkey
    params.Properties_.Item("sValueName").Value = name
    result = registry.ExecMethod_("GetDWORDValue", params)
    return (
        int(result.Properties_.Item("ReturnValue").Value),
        result.Properties_.Item("uValue").Value,
    )


def read_hkcu_dword(subkey: str, name: str) -> int | None:
    """A REG_DWORD of the user's real HKCU, or None when the value is not there."""
    with _com() as client:
        return_value, value = _exec_get_dword(client, subkey, name)
    if return_value != 0 or value is None:
        return None
    return int(value)
