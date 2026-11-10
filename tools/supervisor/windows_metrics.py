"""Real Windows host memory reads for the supervisor.

CIM (WMI) is the source of ``FreePhysicalMemory`` (KiB) and of
``PrivatePageCount`` / ``WorkingSetSize`` (bytes) for the declared PIDs.
``psapi!GetPerformanceInfo`` supplies ``CommitTotal`` / ``CommitLimit`` /
``PhysicalAvailable`` in pages; this module multiplies them by ``PageSize``
and never fabricates commit values when GPI is unavailable.

GiB conversion matches E0: ``(KiB * 1024) / 2**30``.
"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Iterator, Sequence

_KIB = 1024
_CIMV2 = r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"


class GpiUnavailable(RuntimeError):
    """psapi!GetPerformanceInfo is not present on this host."""


class PerformanceInformation(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", wintypes.DWORD),
        ("ProcessCount", wintypes.DWORD),
        ("ThreadCount", wintypes.DWORD),
    ]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def _com() -> Iterator[object]:
    import pythoncom
    import win32com.client

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


def read_cim_free_physical_kib() -> int:
    with _com() as client:
        wmi = client.GetObject(_CIMV2)
        rows = list(
            wmi.ExecQuery(
                "SELECT FreePhysicalMemory FROM Win32_OperatingSystem"
            )
        )
        if len(rows) != 1:
            raise RuntimeError("cim_operating_system_rows")
        value = rows[0].FreePhysicalMemory
        del rows, wmi
    try:
        kib = int(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("cim_free_physical_invalid") from error
    if kib <= 0:
        raise RuntimeError("cim_free_physical_invalid")
    return kib


def read_cim_process_memory(
    pids: Sequence[int],
) -> dict[int, tuple[int, int]]:
    """``{pid: (PrivatePageCount, WorkingSetSize)}`` in bytes, all pids required."""
    requested = [int(pid) for pid in pids]
    if not requested:
        return {}
    observed: dict[int, tuple[int, int]] = {}
    with _com() as client:
        wmi = client.GetObject(_CIMV2)
        for pid in requested:
            rows = list(
                wmi.ExecQuery(
                    "SELECT ProcessId, PrivatePageCount, WorkingSetSize "
                    f"FROM Win32_Process WHERE ProcessId = {pid}"
                )
            )
            if len(rows) != 1:
                raise RuntimeError(f"cim_process_missing:{pid}")
            row = rows[0]
            try:
                private = int(row.PrivatePageCount)
                working_set = int(row.WorkingSetSize)
            except (TypeError, ValueError) as error:
                raise RuntimeError(f"cim_process_invalid:{pid}") from error
            if private <= 0 or working_set <= 0:
                raise RuntimeError(f"cim_process_invalid:{pid}")
            observed[pid] = (private, working_set)
            del row, rows
        del wmi
    return observed


def read_performance_info() -> dict[str, int]:
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    function = getattr(psapi, "GetPerformanceInfo", None)
    if function is None:
        raise GpiUnavailable("psapi_GetPerformanceInfo_missing")
    function.argtypes = [
        ctypes.POINTER(PerformanceInformation),
        wintypes.DWORD,
    ]
    function.restype = wintypes.BOOL
    info = PerformanceInformation()
    info.cb = ctypes.sizeof(info)
    ctypes.set_last_error(0)
    if not function(ctypes.byref(info), info.cb):
        error = ctypes.get_last_error()
        if error in {120, 127}:
            raise GpiUnavailable(f"gpi_error_{error}")
        raise OSError(error, "GetPerformanceInfo failed")
    page_size = int(info.PageSize)
    commit_total = int(info.CommitTotal)
    commit_limit = int(info.CommitLimit)
    physical_available = int(info.PhysicalAvailable)
    if (
        page_size <= 0
        or commit_total <= 0
        or commit_limit <= 0
        or commit_total > commit_limit
        or physical_available <= 0
    ):
        raise RuntimeError("gpi_invalid")
    return {
        "commit_total_bytes": commit_total * page_size,
        "commit_limit_bytes": commit_limit * page_size,
        "physical_available_bytes": physical_available * page_size,
        "page_size": page_size,
    }


def kib_to_bytes(kib: int) -> int:
    return int(kib) * _KIB


def bytes_to_gib(value: int) -> float:
    return value / float(1 << 30)
