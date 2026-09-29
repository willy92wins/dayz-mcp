"""Idle warden (250f plan v2.1 §3.6). Ships disabled.

One daemon thread. Each cycle re-reads ``idle-warden.json``. Absent, unreadable
or anything other than ``{"enabled": true}`` is off: no lease, no adopt, no
bridge command, no use_state change, no lease expiry pass, and no touch of
``adoption-unconfirmed.json``. A cycle that was already on and sees the file
go off releases what it holds and closes nothing.

When on, an ``abandoned`` run is warned and closed 60 s later unless someone
reacts. A confirmed-dead client skips the warning. The lease is the normal
queue plus a live wait (D-19), with the coordinator's WAL and audit (D-20).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dayz_mcp.process_lifecycle import (
    RUN_IDLE_CUT_S,
    _ACTIVE_STATES,
    ProcessLifecycle,
    idle_destruction_guard,
)
from dayz_mcp.session_coordination import ClientIdentity, SessionCoordinator

IDLE_WARDEN_SETTINGS_NAME = "idle-warden.json"
WARNING_TEXT = (
    "Se cerrará en 60 s. Mueve el ratón o pulsa una tecla para seguir"
)
WARNING_SHOW_S = 60
COUNTDOWN_S = 60.0
EXIT_WAIT_S = 45.0
WARNING_RETRY_S = 300.0
BRIDGE_TIMEOUT_S = 5.0
WARDEN_INTERVAL_S = 5.0
POLL_S = 1.0

PhaseHook = Callable[[str, "IdleWarden"], None]


def _player_uids(result: object) -> list[str] | None:
    """Uids from a query_all_players body, or None when it is not evidence."""

    if not isinstance(result, dict) or result.get("ok") is False:
        return None
    error = result.get("error")
    if isinstance(error, str) and error:
        return None
    players = result.get("players")
    if not isinstance(players, list) or not players:
        return None
    uids: list[str] = []
    for player in players:
        if not isinstance(player, dict):
            return None
        uid = player.get("uid")
        if not isinstance(uid, str) or not uid:
            return None
        uids.append(uid)
    return uids


def _notify_ok(result: object) -> bool:
    """True only for a confirmed notify: ok true or integer 1, and no error."""

    if not isinstance(result, dict):
        return False
    error = result.get("error")
    if isinstance(error, str) and error:
        return False
    ok = result.get("ok")
    if ok is True:
        return True
    return type(ok) is int and ok == 1


class _Stopped(Exception):
    """The daemon asked the warden to leave before WM_CLOSE."""


def read_idle_warden_enabled(path: Path) -> bool:
    """True only for a JSON object whose sole field is ``enabled: true``.

    Missing, unreadable, invalid, or any other shape is off.
    """

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    if not isinstance(payload, dict) or set(payload) != {"enabled"}:
        return False
    return payload.get("enabled") is True


def idle_warden_identity(
    generation: str,
    *,
    pid: int | None = None,
    ppid: int | None = None,
    started_at_utc: str | None = None,
) -> ClientIdentity:
    started = started_at_utc or (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return ClientIdentity(
        "unknown",
        os.getpid() if pid is None else pid,
        os.getppid() if ppid is None else ppid,
        started,
        f"idle-warden-{generation}",
        "idle_timeout",
    )


class IdleWarden:
    """One pass per ``run_once``. The thread calls it; tests call it directly."""

    def __init__(
        self,
        lifecycle: ProcessLifecycle,
        coordinator: SessionCoordinator,
        bridge: object,
        settings_path: Path,
        *,
        generation: str,
        client: ClientIdentity | None = None,
        wall: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wait_slice_s: float = 0.0,
        poll_s: float = POLL_S,
        bridge_timeout_s: float = BRIDGE_TIMEOUT_S,
        sample_input: Callable[[], object] | None = None,
        phase: PhaseHook | None = None,
        log: Callable[[str], None] = lambda _message: None,
        stop: threading.Event | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.coordinator = coordinator
        self.bridge = bridge
        self.settings_path = Path(settings_path)
        self.generation = generation
        self.client = client or idle_warden_identity(generation)
        self.wall = wall
        self.monotonic = monotonic
        self.sleep = sleep
        self.wait_slice_s = wait_slice_s
        self.poll_s = poll_s
        self.bridge_timeout_s = bridge_timeout_s
        self.sample_input = sample_input
        self.phase = phase
        self.log = log
        self.stop = stop
        self.operation_id: str | None = None
        self.ticket_id: str | None = None
        self.token: str | None = None
        self.lease_id: str | None = None
        self.run_id: str | None = None
        self._launch = ""
        self._dead = False
        self._baseline: tuple[tuple[object, ...], ...] = ()
        self._human_mark: float | None = None
        self._launcher_mark: float | None = None
        self._closed = False
        self._adoption_started = False

    def run_once(self) -> str:
        self._adoption_started = False
        try:
            return self._cycle()
        finally:
            if self._adoption_started:
                self._release_unconfirmed_adoption()

    def _cycle(self) -> str:
        if not read_idle_warden_enabled(self.settings_path):
            if self.token or self.ticket_id:
                self._drop_authority()
                if self.token or self.ticket_id:
                    return "release_pending"
            return "disabled"
        try:
            self.coordinator.expire_due()
        except Exception as exc:
            self.log(f"WARDEN: expire_due failed: {exc}")
            return "expire_failed"
        if self.token:
            self._clear_closing()
            return "released" if self._release() else "release_pending"
        try:
            if self.ticket_id:
                return self._continue()
            candidate = self._candidate()
            if candidate is None:
                return "no_candidate"
            return self._begin(candidate)
        except _Stopped:
            self._clear_closing()
            return self._finish("stopped")

    def _begin(self, candidate: tuple[str, bool, str]) -> str:
        run_id, dead, launch = candidate
        self.run_id = run_id
        self._dead = dead
        self._launch = launch
        self.operation_id = uuid.uuid4().hex
        status, body = self.coordinator.enqueue(
            self.client, "idle_timeout", self.operation_id
        )
        if status != 202 or not isinstance(body, dict):
            self._clear_ticket()
            return "enqueue_failed"
        ticket = body.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            self._clear_ticket()
            return "enqueue_failed"
        self.ticket_id = ticket
        return self._after_enqueue(run_id)

    def _continue(self) -> str:
        run_id = self.run_id
        if not isinstance(run_id, str) or not self._still(run_id):
            if not self._cancel_ticket():
                return "release_pending"
            return "cancelled"
        return self._after_enqueue(run_id)

    def _after_enqueue(self, run_id: str) -> str:
        self._hook("queued")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if not self._still(run_id):
            if not self._cancel_ticket():
                return "release_pending"
            return "cancelled"
        status, body = self.coordinator.wait(
            self.client, self.ticket_id or "", self.wait_slice_s
        )
        if not isinstance(body, dict):
            body = {}
        if status == 202:
            return "queued"
        if status == 409 and body.get("error") == "operation_cancelled":
            self._clear_ticket()
            return "cancelled"
        if status != 200 or not isinstance(body.get("lease_token"), str):
            self._clear_ticket()
            return "wait_failed"
        self.token = str(body["lease_token"])
        lease_id = body.get("lease_id")
        self.lease_id = lease_id if isinstance(lease_id, str) else None
        self._clear_ticket()
        self._hook("granted")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if not self._still(run_id):
            if not self._release():
                return "release_pending"
            return "cancelled"
        return self._own(run_id)

    def _own(self, run_id: str) -> str:
        off = self._switch_off(run_id)
        if off is not None:
            return off
        self._adoption_started = True
        adopted = self.lifecycle.adopt_run(self.client, self.token, run_id)
        if not isinstance(adopted, dict) or adopted.get("ok") is not True:
            # A late human or uncertain sample reverts the owner after the
            # durable write and answers run_protected. That is not a failed
            # warning: the run stays ownerless and this lease is dropped.
            if isinstance(adopted, dict) and adopted.get("error") == "run_protected":
                return self._not_adopted()
            return self._warning_failed(run_id, "adopt_failed")
        if not self._dead and adopted.get("dispatchable") is not True:
            return self._warning_failed(run_id, "not_dispatchable")
        baseline = self.lifecycle.registered_identities(run_id)
        if baseline is None:
            return self._fail(run_id, "run_missing")
        self._baseline = baseline
        self._human_mark = self.lifecycle.human_input_at(run_id)
        self._launcher_mark = self.lifecycle.launcher_request_at(run_id)
        self._hook("adopted")
        if self._reacted(run_id) or self._drift(run_id):
            return self._abort(run_id)
        if self._dead:
            return self._close(run_id)
        self.lifecycle.mark_closing(run_id)
        warned = self._warn(run_id)
        if warned != "ok":
            return warned
        return self._countdown(run_id)

    def _warn(self, run_id: str) -> str:
        self._hook("warning")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if self._reacted(run_id) or self._drift(run_id):
            return self._abort(run_id)
        pin = self._pin(run_id)
        if pin is None:
            return self._warning_failed(run_id, "binding_unavailable")
        queried = self._bridge("query_all_players", {})
        uids = _player_uids(queried)
        if not uids:
            detail = "query_timeout" if queried is None else "no_players"
            if isinstance(queried, dict) and queried.get("error"):
                detail = "query_error"
            return self._warning_failed(run_id, detail)
        for uid in uids:
            self._hook("before_notify")
            off = self._switch_off(run_id)
            if off is not None:
                return off
            if self._reacted(run_id) or self._drift(run_id):
                return self._abort(run_id)
            if self._pin(run_id) != pin:
                return self._warning_failed(run_id, "binding_changed")
            sent = self._bridge(
                "notify_players",
                {"show_time": WARNING_SHOW_S, "title": WARNING_TEXT, "uid": uid},
            )
            if not _notify_ok(sent):
                return self._warning_failed(run_id, "notify_failed")
        return "ok"

    def _countdown(self, run_id: str) -> str:
        deadline = self.monotonic() + COUNTDOWN_S
        self._hook("countdown")
        while self.monotonic() < deadline:
            off = self._switch_off(run_id)
            if off is not None:
                return off
            if not self._heartbeat():
                return self._fail(run_id, "lease_lost")
            if self._reacted(run_id) or self._drift(run_id):
                return self._abort(run_id)
            if not self._dead:
                sample = self._checkpoint(run_id)
                if sample == "human_input":
                    return self._abort(run_id)
                if sample is not None:
                    return self._fail(run_id, sample)
            remaining = deadline - self.monotonic()
            if remaining <= 0.0:
                break
            self._sleep(min(self.poll_s, remaining))
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if self._reacted(run_id) or self._drift(run_id):
            return self._abort(run_id)
        return self._close(run_id)

    def _close(self, run_id: str) -> str:
        self._hook("revalidate_close")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if not self._dead:
            sample = self._checkpoint(run_id)
            if sample == "human_input":
                return self._abort(run_id)
            if sample is not None:
                return self._fail(run_id, sample)
        if self._reacted(run_id):
            return self._abort(run_id)
        if not self._heartbeat():
            return self._fail(run_id, "lease_lost")
        reason = self._gate(run_id, allow_gone=False)
        if reason:
            return self._fail(run_id, reason)
        current = self.lifecycle.registered_identities(run_id)
        if current != self._baseline:
            return self._fail(run_id, "process_changed")
        self.lifecycle.arm_idle_retirement(run_id, "orderly")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if self._reacted(run_id):
            return self._abort(run_id)
        guard = self._destruction_guard(run_id, "close", allow_gone=False)
        with idle_destruction_guard(guard):
            closed = self.lifecycle.close_run(self.client, self.token, run_id)
        if isinstance(closed, dict) and closed.get("outcome") == "partial_close":
            return self._named_partial(run_id, "partial_close", closed)
        if isinstance(closed, dict) and closed.get("error") == "warden_disabled":
            return self._switch_off(run_id)
        if isinstance(closed, dict) and closed.get("error") == "anchor_changed":
            return self._abort(run_id)
        if not isinstance(closed, dict) or "error" in closed:
            self.lifecycle.clear_idle_retirement(run_id)
            detail = "close_failed"
            if isinstance(closed, dict) and isinstance(closed.get("error"), str):
                detail = str(closed["error"])
            return self._fail(run_id, detail)
        self._closed = True
        self._clear_closing()
        return self._after_close(run_id)

    def _after_close(self, run_id: str) -> str:
        deadline = self.monotonic() + EXIT_WAIT_S
        while self.monotonic() < deadline:
            if self._gone(run_id) or self._state(run_id) == "EXITED":
                break
            if not self._heartbeat():
                break
            self._hook("waiting_exit")
            if self._gone(run_id) or self._state(run_id) == "EXITED":
                break
            remaining = deadline - self.monotonic()
            if remaining <= 0.0:
                break
            self.sleep(min(self.poll_s, remaining))
        if self._state(run_id) == "EXITED":
            return self._finish("orderly")
        if self._gone(run_id):
            reaped = self.lifecycle.reap_dead_run(self.client, self.token, run_id)
            if (
                isinstance(reaped, dict) and reaped.get("ok") is True
            ) or self._state(run_id) == "EXITED":
                return self._finish("orderly")
            return self._fail(run_id, "reap_failed")
        self._hook("revalidate_stop")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        if not self._heartbeat():
            return self._fail(run_id, "lease_lost")
        reason = self._gate(run_id, allow_gone=True)
        if reason:
            return self._fail(run_id, reason)
        current = self.lifecycle.registered_identities(run_id)
        if current is None or not set(current) <= set(self._baseline):
            return self._fail(run_id, "process_changed")
        self.lifecycle.arm_idle_retirement(run_id, "fallback_stop")
        off = self._switch_off(run_id)
        if off is not None:
            return off
        guard = self._destruction_guard(run_id, "stop", allow_gone=True)
        with idle_destruction_guard(guard):
            stopped = self.lifecycle.stop_run(self.client, self.token, run_id)
        if isinstance(stopped, dict) and stopped.get("outcome") == "partial_stop":
            return self._named_partial(run_id, "partial_stop", stopped)
        if isinstance(stopped, dict) and stopped.get("error") == "warden_disabled":
            return self._switch_off(run_id)
        if isinstance(stopped, dict) and stopped.get("error") == "anchor_changed":
            return self._abort(run_id)
        if (
            isinstance(stopped, dict)
            and stopped.get("ok") is True
            and stopped.get("state") == "EXITED"
        ):
            return self._finish("fallback_stop")
        detail = "stop_failed"
        if isinstance(stopped, dict) and isinstance(stopped.get("error"), str):
            detail = str(stopped["error"])
        return self._fail(run_id, detail)

    def _candidate(self) -> tuple[str, bool, str] | None:
        if self.lifecycle.retail_quarantined() or not self.lifecycle.scans_known():
            return None
        box = self.lifecycle.box_occupancy(now=self.wall())
        rows = box.get("runs") if isinstance(box, dict) else None
        if not isinstance(rows, list) or len(rows) != 1:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        if row.get("state") != "RUNNING_IDLE" or row.get("use_state") != "abandoned":
            return None
        run_id = row.get("run_id")
        launch = row.get("daemon_generation_at_launch")
        if not isinstance(run_id, str) or not run_id:
            return None
        if not isinstance(launch, str) or not launch:
            return None
        dead = row.get("use_reason") == "client_gone"
        if not dead and (
            launch != self.lifecycle.daemon_generation
            or row.get("generation_changed") is True
        ):
            return None
        if row.get("state") not in _ACTIVE_STATES:
            return None
        return run_id, dead, launch

    def _still(self, run_id: str) -> bool:
        found = self._candidate()
        return found is not None and found[0] == run_id

    def _pin(self, run_id: str) -> tuple[str, str, str] | None:
        try:
            token = self.bridge.bound_instance_token(run_id, "server")
        except Exception:
            return None
        if not isinstance(token, str) or not token:
            return None
        if self.lifecycle.daemon_generation != self.generation:
            return None
        run = self.lifecycle.manifest.get(run_id)
        if (
            run is None
            or run.daemon_generation_at_launch != self._launch
            or run.state != "RUNNING"
        ):
            return None
        return token, self.generation, self._launch

    def _bridge(self, command: str, args: dict[str, object]) -> dict | None:
        bridge = self.bridge
        try:
            enqueue_command = bridge.enqueue_command
            take_result = bridge.take_result
        except AttributeError:
            return None
        if not callable(enqueue_command) or not callable(take_result):
            return None
        try:
            status, payload = bridge.enqueue_command(
                command,
                args,
                "server",
                identity_payload=self.client.to_payload(),
                lease_token=self.token,
                operation_timeout_s=self.bridge_timeout_s,
                internal=False,
            )
        except Exception:
            return None
        if status != 200 or not isinstance(payload, dict):
            return None
        command_id = payload.get("id")
        if isinstance(command_id, bool) or not isinstance(command_id, int):
            return None
        deadline = self.monotonic() + self.bridge_timeout_s
        while True:
            try:
                result = bridge.take_result(command_id, True)
            except TypeError:
                try:
                    result = bridge.take_result(command_id)
                except Exception:
                    result = None
            except Exception:
                result = None
            if isinstance(result, dict):
                return result
            if self.monotonic() >= deadline:
                try:
                    bridge.abandon_command(command_id, "idle_warden_timeout")
                except Exception:
                    pass
                return None
            self._sleep(min(self.poll_s, deadline - self.monotonic()))

    def _checkpoint(self, run_id: str) -> str | None:
        """None when the fresh sample shows no use. ``human_input`` aborts."""

        if self.sample_input is None:
            return "input_sample_unavailable"
        try:
            sample = self.sample_input()
        except Exception:
            return "input_sample_unavailable"
        if sample is None or getattr(sample, "ok", False) is not True:
            return "input_sample_unavailable"
        before = self.lifecycle.human_input_at(run_id)
        try:
            self.lifecycle.record_input_sample(sample)
        except Exception:
            return "input_sample_unavailable"
        after = self.lifecycle.human_input_at(run_id)
        if after is not None and after != before:
            return "human_input"
        return None

    def _reacted(self, run_id: str) -> bool:
        try:
            self.lifecycle.note_queued_launcher_requests(when=self.wall())
        except Exception:
            return True
        human = self.lifecycle.human_input_at(run_id)
        if human is not None and human != self._human_mark:
            return True
        launcher = self.lifecycle.launcher_request_at(run_id)
        if launcher is not None and launcher != self._launcher_mark:
            return True
        return False

    def _drift(self, run_id: str) -> str | None:
        if self.lifecycle.retail_quarantined():
            return "retail_quarantine"
        if not self.lifecycle.scans_known():
            return "scan_unknown"
        if self.lifecycle.daemon_generation != self.generation:
            return "generation_changed"
        kinds = self.lifecycle.registered_process_kinds(run_id)
        if kinds is None:
            return "run_missing"
        if kinds["unknown"] or kinds["foreign"] or kinds["gone"]:
            return "process_changed"
        current = self.lifecycle.registered_identities(run_id)
        if current != self._baseline:
            return "process_changed"
        return None

    def _gate(self, run_id: str, *, allow_gone: bool) -> str | None:
        return self.lifecycle.idle_close_gate(
            run_id,
            allow_gone=allow_gone,
            daemon_generation=self.generation,
            launch_generation=self._launch,
        )

    def _gone(self, run_id: str) -> bool:
        kinds = self.lifecycle.registered_process_kinds(run_id)
        if kinds is None:
            return False
        return (
            kinds["owned"] == 0
            and kinds["foreign"] == 0
            and kinds["unknown"] == 0
            and kinds["gone"] > 0
        )

    def _heartbeat(self) -> bool:
        if not self.token:
            return False
        try:
            status, body = self.coordinator.heartbeat(self.client, self.token)
        except Exception:
            return False
        if status != 200 or not isinstance(body, dict):
            return False
        lease_id = body.get("lease_id")
        if isinstance(lease_id, str) and self.lease_id and lease_id != self.lease_id:
            return False
        refreshed = body.get("lease_token")
        if isinstance(refreshed, str) and refreshed:
            self.token = refreshed
        return True

    def _not_adopted(self) -> str:
        if not self._release():
            return "release_pending"
        return "not_adopted"

    def _release_unconfirmed_adoption(self) -> None:
        """Drop this cycle's adoption-unconfirmed.json when the adopt did not stick.

        Only a cycle that reached ``adopt_run`` may touch the marker. A fresh
        disabled pass leaves a pre-existing marker for startup recovery. The
        settled trace is not deleted.
        """

        if not self._adoption_started:
            return
        lifecycle = self.lifecycle
        try:
            marker = lifecycle._adoption_marker_path()
        except Exception:
            return
        if not isinstance(marker, Path):
            return
        try:
            with lifecycle._operation_lock:
                self._release_unconfirmed_adoption_locked(lifecycle, marker)
        except Exception:
            return

    def _release_unconfirmed_adoption_locked(
        self, lifecycle: ProcessLifecycle, marker: Path
    ) -> None:
        """Caller holds ``_operation_lock``. Deletes only this cycle's marker."""

        if not marker.is_file():
            return
        try:
            document = lifecycle._read_adoption_marker()
        except Exception:
            return
        if (
            not isinstance(document, dict)
            or document.get("run_id") != self.run_id
            or document.get("owner_session_id") != self.client.session_id
        ):
            return
        run_id = document.get("run_id")
        try:
            run = lifecycle.manifest.get(run_id) if isinstance(run_id, str) else None
        except Exception:
            return
        lease_id = document.get("owner_lease_id")
        owns = (
            run is not None
            and run.state == "RUNNING"
            and run.owner_session_id == self.client.session_id
            and run.owner_lease_id == lease_id
        )
        if owns and lifecycle._open_adoption is not None:
            try:
                lifecycle._close_adoption_after_replace(self.client, run)
            except Exception:
                return
            try:
                run = (
                    lifecycle.manifest.get(run_id) if isinstance(run_id, str) else None
                )
            except Exception:
                return
            owns = (
                run is not None
                and run.state == "RUNNING"
                and run.owner_session_id == self.client.session_id
                and run.owner_lease_id == lease_id
            )
        if owns:
            return
        try:
            open_adoption = lifecycle._open_adoption
            if open_adoption is None or (
                isinstance(open_adoption, tuple)
                and bool(open_adoption)
                and open_adoption[0] == run_id
            ):
                lifecycle._drop_open_adoption()
        except Exception:
            pass
        if not marker.is_file():
            return
        try:
            marker.unlink()
        except OSError:
            return

    def _warning_failed(self, run_id: str, detail: str) -> str:
        previous = self.lifecycle.ownerless_since(run_id)
        self._clear_closing()
        if not self._release():
            return "release_pending"
        if previous is None:
            previous = self.wall() - RUN_IDLE_CUT_S
        self.lifecycle.restore_ownerless_since(run_id, previous)
        self.lifecycle.block_warning(
            run_id, max(self.wall(), time.time()) + WARNING_RETRY_S
        )
        _ = detail
        return "warning_failed"

    def _abort(self, run_id: str) -> str:
        self._clear_closing()
        self.lifecycle.clear_idle_retirement(run_id)
        if not self._release():
            return "release_pending"
        self.lifecycle.restore_ownerless_since(run_id, self.wall())
        return "countdown_aborted"

    def _fail(self, run_id: str, detail: str) -> str:
        self._clear_closing()
        self.lifecycle.clear_idle_retirement(run_id)
        self.lifecycle.audit_idle_timeout(self.client, run_id, "failed", detail)
        return self._finish("failed")

    def _switch_off(self, run_id: str | None = None) -> str | None:
        """None while the file still says enabled. Off or unreadable aborts the cycle.

        Does not rewrite the idle clock and does not arm the warning block.
        """

        try:
            enabled = read_idle_warden_enabled(self.settings_path)
        except Exception:
            enabled = False
        if enabled:
            return None
        target = run_id if isinstance(run_id, str) else self.run_id
        if isinstance(target, str):
            self.lifecycle.clear_closing(target)
            self.lifecycle.clear_idle_retirement(target)
        ticket_pending = bool(self.ticket_id) and not self._cancel_ticket()
        if not self._release():
            return "release_pending"
        if ticket_pending or self.ticket_id:
            return "release_pending"
        return "disabled"

    def _finish(self, outcome: str) -> str:
        """A normal outcome waits until the coordinator has confirmed the release."""

        if not self._release():
            return "release_pending"
        return outcome

    def _release(self) -> bool:
        """True only after the coordinator confirms the lease is gone.

        An exception, or any answer that does not confirm the release, keeps
        the token and the lease. The caller reports release_pending and retries.
        A confirmed release whose cleanup worker is saturated has already
        dropped the lease and will not quiesce the run, so this hands the
        owner to ``begin_release_owner``. That handoff is not used for an
        unconfirmed answer.
        """

        token = self.token
        if not token:
            self.token = None
            self.lease_id = None
            return True
        try:
            status, body = self.coordinator.release(
                self.client, token, "owner_release"
            )
        except Exception:
            self.log("WARDEN: release failed")
            return False
        if (
            isinstance(body, dict)
            and status == 200
            and body.get("released") is True
        ):
            lease_id = self.lease_id
            self.token = None
            self.lease_id = None
            degraded = body.get("cleanup_degraded")
            saturated = (
                isinstance(degraded, list)
                and "cleanup_worker_saturated" in degraded
            )
            if isinstance(lease_id, str) and lease_id and saturated:
                try:
                    self.lifecycle.begin_release_owner(
                        self.client.session_id, lease_id
                    )
                except Exception:
                    self.log("WARDEN: direct release failed")
            return True
        return False

    def _cancel_ticket(self) -> bool:
        """True when the cancel is confirmed or the ticket is already terminal.

        Any other answer, including an exception, keeps the ticket id.
        """

        operation_id = self.operation_id
        ticket_id = self.ticket_id
        if not operation_id and not ticket_id:
            self._clear_ticket()
            return True
        try:
            if operation_id:
                status, body = self.coordinator.cancel_operation(
                    self.client, operation_id
                )
            else:
                status, body = self.coordinator.cancel(
                    self.client, ticket_id or ""
                )
        except Exception:
            self.log("WARDEN: cancel failed")
            return False
        if not isinstance(body, dict):
            return False
        confirmed = status == 200 and body.get("cancelled") is True
        terminal = status == 403 and body.get("error") == "ticket_invalid"
        if confirmed or terminal:
            self._clear_ticket()
            return True
        return False

    def _destruction_guard(
        self, run_id: str, operation: str, *, allow_gone: bool
    ) -> dict[str, object]:
        return {
            "expected": self._baseline,
            "allow_gone": allow_gone,
            "human_at": self.lifecycle.human_input_at(run_id),
            "launcher_at": self.lifecycle.launcher_request_at(run_id),
            "wall": self.wall(),
            "operation": operation,
            "session_id": self.client.session_id,
            "run_id": run_id,
            "settings_path": str(self.settings_path),
        }

    def _named_partial(
        self, run_id: str, outcome: str, payload: dict[str, object]
    ) -> str:
        """Record who was acted on, then release. Pending if release is not confirmed."""

        acted = payload.get("acted_pids")
        untouched = payload.get("untouched_pids")
        reasons = payload.get("untouched_reasons")
        if not isinstance(acted, list):
            acted = []
        if not isinstance(untouched, list):
            untouched = []
        detail = f"acted_pids={acted}; untouched_pids={untouched}"
        if isinstance(reasons, list) and reasons:
            detail = f"{detail}; untouched_reasons={reasons}"
        self._closed = True
        self._clear_closing()
        self.lifecycle.clear_idle_retirement(run_id)
        self.lifecycle.audit_idle_timeout(self.client, run_id, outcome, detail)
        return self._finish(outcome)

    def _drop_authority(self) -> None:
        run_id = self.run_id
        if isinstance(run_id, str):
            self.lifecycle.clear_closing(run_id)
            self.lifecycle.clear_idle_retirement(run_id)
        if self.token:
            self._release()
        if self.ticket_id:
            self._cancel_ticket()

    def _clear_ticket(self) -> None:
        self.ticket_id = None
        self.operation_id = None

    def _clear_closing(self) -> None:
        if isinstance(self.run_id, str):
            self.lifecycle.clear_closing(self.run_id)

    def _state(self, run_id: str) -> str | None:
        run = self.lifecycle.manifest.get(run_id)
        if run is None:
            return None
        return run.state

    def _hook(self, name: str) -> None:
        if self.phase is not None:
            self.phase(name, self)

    def _sleep(self, seconds: float) -> None:
        if self.stop is not None and self.stop.is_set():
            raise _Stopped()
        self.sleep(seconds)
        if self.stop is not None and self.stop.is_set():
            raise _Stopped()


def install_idle_warden(
    lifecycle: ProcessLifecycle,
    coordinator: SessionCoordinator,
    bridge: object,
    settings_path: Path,
    *,
    generation: str,
    log: Callable[[str], None] = lambda _message: None,
    stop: threading.Event | None = None,
    interval_s: float = WARDEN_INTERVAL_S,
    sample_input: Callable[[], object] | None = None,
    wall: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> threading.Thread:
    """Daemon thread, same shape as the run reaper. Re-reads the switch."""

    warden = IdleWarden(
        lifecycle,
        coordinator,
        bridge,
        settings_path,
        generation=generation,
        log=log,
        stop=stop,
        sample_input=sample_input,
        wall=wall,
        monotonic=monotonic,
        sleep=sleep,
    )

    def _loop() -> None:
        while True:
            if (
                stop is not None
                and stop.is_set()
                and not warden.token
                and not warden.ticket_id
            ):
                return
            try:
                warden.run_once()
            except Exception as exc:
                log(f"WARDEN: pass failed: {exc}")
            held = bool(warden.token or warden.ticket_id)
            if stop is not None and stop.is_set():
                if held:
                    time.sleep(interval_s)
                    continue
                return
            if stop is not None:
                if stop.wait(interval_s):
                    if warden.token or warden.ticket_id:
                        continue
                    return
            else:
                time.sleep(interval_s)

    thread = threading.Thread(target=_loop, name="dayz-mcp-idle-warden", daemon=True)
    thread.start()
    return thread
