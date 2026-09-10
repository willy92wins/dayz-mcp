"""Private stdin/stdout helper. No host access before its supervisor's start.

No lease/token/account data crosses this pipe. Each writer/invoker requests a
fresh permit from the daemon; EOF, cancellation and the absolute deadline stop
all later operations. The daemon's kill-on-close job contains only this helper.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

from . import steam_preflight as steam
from .steam_launch_guard import (
    PREPARE_BUDGET_S, Preparation, PreparationCancelled, live_identity, prepare,
)


class Channel:
    def __init__(self):
        self.cancel = threading.Event()
        self.messages = queue.Queue(maxsize=8)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while True:
                line = sys.stdin.buffer.readline(4097)
                if not line or len(line) > 4096:
                    return
                value = json.loads(line)
                if value == {"cancel": True}:
                    return
                self.messages.put_nowait(value)
        except Exception:
            pass
        finally:
            self.cancel.set()

    def receive(self, check):
        while True:
            check()
            try:
                return self.messages.get(timeout=0.05)
            except queue.Empty:
                pass

    @staticmethod
    def send(value):
        print(json.dumps(value, separators=(",", ":")), flush=True)


class GuardedProvider:
    def __init__(self, provider, check):
        self.provider, self.check = provider, check

    def _read(self, operation, *args):
        self.check()
        value = operation(*args)
        self.check()
        return value

    def read_active_process(self):
        return self._read(self.provider.read_active_process)

    def process_exists(self, pid):
        return self._read(self.provider.process_exists, pid)

    def process_image_path(self, pid):
        return self._read(self.provider.process_image_path, pid)

    def process_creation_ticks(self, pid):
        return self._read(self.provider.process_creation_ticks, pid)

    def steam_process_pids(self):
        return self._read(self.provider.steam_process_pids)

    def steam_startup_complete(self, pid):
        return self._read(self.provider.steam_startup_complete, pid)


class GuardedHost(steam.WindowsSteamRemediationHost):
    def __init__(self, channel, deadline, consent):
        self.channel, self.deadline, self.consent = channel, deadline, consent
        self.provider = GuardedProvider(steam.WindowsSteamPreflightProvider(), self.checkpoint)
        self.expected_repair_identity = None

    def checkpoint(self):
        if self.channel.cancel.is_set():
            raise PreparationCancelled()
        if time.monotonic() >= self.deadline:
            raise PreparationCancelled("steam_prepare_timeout")

    def sleep(self, seconds):
        self.checkpoint()
        self.channel.cancel.wait(min(seconds, max(0, self.deadline - time.monotonic())))
        self.checkpoint()

    def permit(self):
        self.checkpoint()
        if not self.consent:
            raise PreparationCancelled("steam_session_stale")
        self.channel.send({"mutation": True})
        value = self.channel.receive(self.checkpoint)
        if value != {"permit": True}:
            raise PreparationCancelled("steam_client_active")
        self.checkpoint()

    def write_active_process_pid(self, pid):
        self.permit()
        expected = self.expected_repair_identity
        if expected is None or expected.pid != pid or live_identity(self.provider) != expected:
            raise PreparationCancelled("steam_identity_changed")
        self.checkpoint()
        super().write_active_process_pid(pid)
        self.checkpoint()
        if live_identity(self.provider) != expected:
            raise PreparationCancelled("steam_identity_changed")

    def invoke_steam(self, executable, extra_args):
        self.permit()
        if extra_args == ("-shutdown",):
            if self.expected_repair_identity is None or live_identity(self.provider) != self.expected_repair_identity:
                raise PreparationCancelled("steam_identity_changed")
        elif steam._safe_live_pids(self.provider) != ():
            raise PreparationCancelled("steam_identity_changed")
        self.checkpoint()
        # The already-invoked Steam is allowed to finish booting independently;
        # only the MCP writer/invoker must die with cancellation or daemon exit.
        subprocess.Popen(
            [executable, *extra_args], close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=steam._STEAM_INVOKE_FLAGS | (0x01000000 if os.name == "nt" else 0),
        )
        self.checkpoint()


def main():
    channel = Channel()

    def initial_check():
        if channel.cancel.is_set():
            raise PreparationCancelled()

    try:
        start = channel.receive(initial_check)
        if not isinstance(start, dict) or type(start.get("consent")) is not bool:
            raise PreparationCancelled("steam_prepare_failed")
        budget = start.get("budget_s")
        if type(budget) not in (int, float) or not 0 < budget <= PREPARE_BUDGET_S:
            raise PreparationCancelled("steam_prepare_failed")
        host = GuardedHost(channel, time.monotonic() + budget, start["consent"])
        result = prepare(host.provider, host, start["consent"])
    except PreparationCancelled as exc:
        result = Preparation(error_code=exc.code)
    except Exception:
        result = Preparation(error_code="steam_prepare_failed")
    channel.send({"result": result.payload()})


if __name__ == "__main__":
    main()
