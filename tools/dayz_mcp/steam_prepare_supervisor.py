"""Daemon-owned exclusivity and bounded supervision of the Steam helper."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

# Include the child source in the daemon's loaded-source freshness snapshot.
# Importing it performs no host I/O; its entry point runs only under __main__.
from . import steam_prepare_helper
from .steam_launch_guard import (
    CLEANUP_BUDGET_S, PREPARE_BUDGET_S, Preparation, SteamIdentity, final_check,
)


class HelperJob:
    """Exact helper handle; its Steam children explicitly break away.

    Win32 contract: learn.microsoft.com/windows/win32/api/jobapi2/
    nf-jobapi2-assignprocesstojobobject. Types shared with the sealed launcher.
    """
    def __init__(self, process):
        self.handle = None
        if os.name != "nt":
            return
        from .native_launcher_backend import JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("helper_job_create_failed")
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x800  # kill-on-close, breakaway
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("helper_job_limits_failed")
        if not kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            self.close()
            raise OSError("helper_job_assign_failed")

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Helper:
    def __init__(self, argv=None, *, job_factory=HelperJob):
        self.process = subprocess.Popen(
            argv or [sys.executable, "-m", "dayz_mcp.steam_prepare_helper"],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True,
        )
        self.job = None
        self.messages = queue.Queue(maxsize=8)
        self.broken = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        # Constructor returns its exact handle even if job setup fails, allowing
        # the supervisor to fence a helper whose termination cannot be verified.
        self.ready = False
        try:
            self.job = job_factory(self.process)
            self.ready = True
        except Exception:
            pass

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(4097)
                if not line or len(line) > 4096:
                    break
                self.messages.put_nowait(json.loads(line))
        except Exception:
            pass
        finally:
            self.broken.set()
            try:
                self.messages.put_nowait({"eof": True})
            except queue.Full:
                pass

    def send(self, value):
        self.process.stdin.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return {"eof": True} if self.broken.is_set() else None

    def drain(self, budget=CLEANUP_BUDGET_S):
        deadline = time.monotonic() + budget
        try:
            if self.process.poll() is None:
                self.send({"cancel": True})
                try:
                    self.process.wait(timeout=min(0.2, budget))
                except subprocess.TimeoutExpired:
                    self.process.terminate()  # Popen's accredited handle, never PID lookup
            self.process.wait(timeout=max(0, deadline - time.monotonic()))
        except Exception:
            if self.process.poll() is None:
                return False
        if self.job is not None:
            self.job.close()
        self.reader.join(timeout=max(0, deadline - time.monotonic()))
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()
        return True


class SteamPreparationGate:
    def __init__(self, *, helper_factory=Helper, budget_s=PREPARE_BUDGET_S):
        self._lock = threading.Lock()
        self._stranded = None
        self._probe_slot = threading.BoundedSemaphore(1)
        self.helper_factory = helper_factory
        self.budget_s = min(budget_s, PREPARE_BUDGET_S)

    def claim(self):
        if not self._lock.acquire(blocking=False):
            return False
        if self._stranded is not None:
            if not self._drained(self._stranded, 0):
                self._lock.release()
                return False
            self._stranded = None
        return True

    def release(self):
        self._lock.release()

    @property
    def degraded(self):
        return self._stranded is not None

    @staticmethod
    def _drained(helper, budget):
        try:
            return helper.drain(budget) is True
        except Exception:
            return False

    def prepare(self, *, consent, authority_active, mutation_allowed):
        deadline = time.monotonic() + self.budget_s
        helper = None
        result = Preparation(error_code="steam_prepare_failed")
        try:
            if not authority_active():
                return Preparation(error_code="steam_prepare_cancelled")
            helper = self.helper_factory()
            if not helper.ready:
                raise OSError("helper_job_unavailable")
            helper.send({"consent": consent, "budget_s": max(0, deadline - time.monotonic())})
            pending_check = None
            while True:
                if not authority_active():
                    result = Preparation(error_code="steam_prepare_cancelled")
                    break
                if time.monotonic() >= deadline:
                    result = Preparation(error_code="steam_prepare_timeout")
                    break
                if pending_check is not None:
                    # Host client/identity probes can stall. They are read-only
                    # and never send permits; this authority monitor must keep
                    # running while they wait for I/O or the operation lock.
                    try:
                        allowed = pending_check.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    pending_check = None
                    if allowed == "steam_prepare_busy":
                        result = Preparation(error_code="steam_prepare_busy")
                        break
                    if allowed is not True:
                        result = Preparation(error_code="steam_client_active")
                        break
                    if not authority_active():
                        result = Preparation(error_code="steam_prepare_cancelled")
                        break
                    if time.monotonic() >= deadline:
                        result = Preparation(error_code="steam_prepare_timeout")
                        break
                    helper.send({"permit": True})
                    continue
                value = helper.receive(min(0.05, max(0, deadline - time.monotonic())))
                if value is None:
                    continue
                if time.monotonic() >= deadline:
                    result = Preparation(error_code="steam_prepare_timeout")
                    break
                if value == {"mutation": True}:
                    if not consent or not authority_active():
                        result = Preparation(error_code="steam_client_active")
                        break
                    if not self._probe_slot.acquire(blocking=False):
                        result = Preparation(error_code="steam_probe_pending")
                        break
                    pending_check = queue.Queue(maxsize=1)
                    def probe(destination=pending_check):
                        try:
                            allowed = mutation_allowed()
                        except Exception:
                            allowed = False
                        finally:
                            self._probe_slot.release()
                        destination.put_nowait(allowed)
                    try:
                        threading.Thread(target=probe, daemon=True).start()
                    except Exception:
                        self._probe_slot.release()
                        raise
                elif isinstance(value, dict) and isinstance(value.get("result"), dict):
                    raw = value["result"]
                    identity = raw.pop("identity", None)
                    result = Preparation(**raw, identity=SteamIdentity(**identity) if identity else None)
                    if not authority_active():
                        result = Preparation(error_code="steam_prepare_cancelled")
                    break
                else:
                    break
        except Exception:
            result = Preparation(error_code="steam_prepare_failed")
        finally:
            if helper is not None and not self._drained(helper, CLEANUP_BUDGET_S):
                self._stranded = helper
                result = Preparation(error_code="steam_cleanup_degraded", cleanup_degraded=True)
        return result

    @staticmethod
    def final_check(prepared):
        return final_check(prepared)
