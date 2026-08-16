from __future__ import annotations

from dataclasses import dataclass


DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_BREAKPOINT = 0x80000003


class NativeDebugStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NativeDebugEvent:
    kind: str
    pid: int
    tid: int
    file_handle: int = 0
    process_handle: int = 0
    thread_handle: int = 0
    image_approved: bool | None = None
    exception_code: int = 0
    first_chance: bool = False
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class NativeDebugDecision:
    continue_status: int
    close_handles: tuple[int, ...] = ()
    close_job_first: bool = False
    failure_code: str | None = None


@dataclass(slots=True)
class _PendingEvent:
    event: NativeDebugEvent
    retire_thread: tuple[int, int] | None = None
    retire_process: int | None = None


class NativeDebugState:
    def __init__(self, *, creator_thread_id: int) -> None:
        if (
            not isinstance(creator_thread_id, int)
            or isinstance(creator_thread_id, bool)
            or creator_thread_id <= 0
        ):
            raise NativeDebugStateError("invalid_debug_creator_thread")
        self._creator_thread_id = creator_thread_id
        self._process_handles: dict[int, int] = {}
        self._thread_handles: dict[tuple[int, int], int] = {}
        self._initial_breakpoint_seen: set[int] = set()
        self._pending: _PendingEvent | None = None
        self._pending_close_handles: set[int] = set()
        self._failed = False
        self._continue_count = 0

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def active_zero(self) -> bool:
        return not self._process_handles

    @property
    def open_handle_count(self) -> int:
        return (
            len(self._process_handles)
            + len(self._thread_handles)
            + len(self._pending_close_handles)
        )

    @property
    def continue_count(self) -> int:
        return self._continue_count

    def _require_creator_thread(self, current_thread_id: int) -> None:
        if current_thread_id != self._creator_thread_id:
            raise NativeDebugStateError("debug_thread_mismatch")

    def _owned_handle_values(self) -> set[int]:
        return (
            set(self._process_handles.values())
            | set(self._thread_handles.values())
            | self._pending_close_handles
        )

    @staticmethod
    def _positive_plain_int(value: object) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def _event_ids_known(self, event: NativeDebugEvent) -> bool:
        return (
            event.pid in self._process_handles
            and (event.pid, event.tid) in self._thread_handles
        )

    def _set_pending(
        self,
        event: NativeDebugEvent,
        decision: NativeDebugDecision,
        *,
        retire_thread: tuple[int, int] | None = None,
        retire_process: int | None = None,
    ) -> NativeDebugDecision:
        self._pending = _PendingEvent(event, retire_thread, retire_process)
        self._pending_close_handles.update(decision.close_handles)
        return decision

    def _failure(
        self,
        event: NativeDebugEvent,
        code: str,
        *,
        continue_status: int = DBG_CONTINUE,
        close_handles: tuple[int, ...] = (),
    ) -> NativeDebugDecision:
        first_failure = not self._failed
        self._failed = True
        return self._set_pending(
            event,
            NativeDebugDecision(
                continue_status,
                close_handles,
                first_failure,
                code,
            ),
        )

    def begin_event(
        self, event: NativeDebugEvent, *, current_thread_id: int
    ) -> NativeDebugDecision:
        self._require_creator_thread(current_thread_id)
        if self._pending is not None:
            raise NativeDebugStateError("debug_event_pending")
        if not isinstance(event, NativeDebugEvent):
            raise NativeDebugStateError("malformed_debug_event")

        kind = event.kind
        close_file = (
            (event.file_handle,)
            if self._positive_plain_int(event.file_handle)
            else ()
        )

        if kind == "CREATE_PROCESS":
            valid_shape = (
                self._positive_plain_int(event.pid)
                and self._positive_plain_int(event.tid)
                and self._positive_plain_int(event.process_handle)
                and self._positive_plain_int(event.thread_handle)
                and event.process_handle != event.thread_handle
                and event.pid not in self._process_handles
                and (event.pid, event.tid) not in self._thread_handles
                and event.process_handle not in self._owned_handle_values()
                and event.thread_handle not in self._owned_handle_values()
            )
            if not valid_shape:
                return self._failure(
                    event,
                    "malformed_debug_event",
                    close_handles=close_file,
                )
            self._process_handles[event.pid] = event.process_handle
            self._thread_handles[(event.pid, event.tid)] = event.thread_handle
            if event.image_approved is not True:
                return self._failure(
                    event,
                    "unapproved_debug_image",
                    close_handles=close_file,
                )
            return self._set_pending(
                event,
                NativeDebugDecision(DBG_CONTINUE, close_file),
            )

        if kind == "CREATE_THREAD":
            valid_shape = (
                event.pid in self._process_handles
                and self._positive_plain_int(event.tid)
                and self._positive_plain_int(event.thread_handle)
                and (event.pid, event.tid) not in self._thread_handles
                and event.thread_handle not in self._owned_handle_values()
            )
            if not valid_shape:
                return self._failure(event, "malformed_debug_event")
            self._thread_handles[(event.pid, event.tid)] = event.thread_handle
            return self._set_pending(event, NativeDebugDecision(DBG_CONTINUE))

        if kind == "LOAD_DLL":
            if not self._event_ids_known(event) or event.image_approved is not True:
                code = (
                    "unapproved_debug_image"
                    if self._event_ids_known(event)
                    else "malformed_debug_event"
                )
                return self._failure(event, code, close_handles=close_file)
            return self._set_pending(
                event,
                NativeDebugDecision(DBG_CONTINUE, close_file),
            )

        if kind in {"UNLOAD_DLL", "OUTPUT_DEBUG_STRING"}:
            if not self._event_ids_known(event):
                return self._failure(event, "malformed_debug_event")
            return self._set_pending(event, NativeDebugDecision(DBG_CONTINUE))

        if kind == "EXCEPTION":
            if (
                not self._event_ids_known(event)
                or not self._positive_plain_int(event.exception_code)
                or not isinstance(event.first_chance, bool)
            ):
                return self._failure(event, "malformed_debug_event")
            if not event.first_chance:
                return self._failure(
                    event,
                    "second_chance_exception",
                    continue_status=DBG_EXCEPTION_NOT_HANDLED,
                )
            if (
                event.exception_code == EXCEPTION_BREAKPOINT
                and event.pid not in self._initial_breakpoint_seen
            ):
                self._initial_breakpoint_seen.add(event.pid)
                return self._set_pending(
                    event,
                    NativeDebugDecision(DBG_CONTINUE),
                )
            return self._set_pending(
                event,
                NativeDebugDecision(DBG_EXCEPTION_NOT_HANDLED),
            )

        if kind == "EXIT_THREAD":
            key = (event.pid, event.tid)
            if key not in self._thread_handles:
                return self._failure(event, "malformed_debug_event")
            return self._set_pending(
                event,
                NativeDebugDecision(DBG_CONTINUE),
                retire_thread=key,
            )

        if kind == "EXIT_PROCESS":
            if event.pid not in self._process_handles:
                return self._failure(event, "malformed_debug_event")
            return self._set_pending(
                event,
                NativeDebugDecision(DBG_CONTINUE),
                retire_process=event.pid,
            )

        if kind == "RIP_EVENT":
            if not self._event_ids_known(event):
                return self._failure(event, "malformed_debug_event")
            return self._failure(event, "debug_rip_event")

        return self._failure(event, "malformed_debug_event", close_handles=close_file)

    def acknowledge_closed_handles(self, handles: tuple[int, ...]) -> None:
        if self._pending is None:
            raise NativeDebugStateError("debug_event_not_pending")
        if (
            len(set(handles)) != len(handles)
            or any(handle not in self._pending_close_handles for handle in handles)
        ):
            raise NativeDebugStateError("debug_handle_close_mismatch")
        self._pending_close_handles.difference_update(handles)

    def complete_continue(
        self, *, current_thread_id: int, succeeded: bool
    ) -> None:
        self._require_creator_thread(current_thread_id)
        if self._pending is None:
            raise NativeDebugStateError("debug_event_not_pending")
        if self._pending_close_handles:
            raise NativeDebugStateError("debug_handle_not_closed")
        pending = self._pending
        self._pending = None
        self._continue_count += 1
        if not succeeded:
            self._failed = True
            return
        if pending.retire_thread is not None:
            self._thread_handles.pop(pending.retire_thread, None)
        if pending.retire_process is not None:
            pid = pending.retire_process
            self._process_handles.pop(pid, None)
            for key in tuple(self._thread_handles):
                if key[0] == pid:
                    self._thread_handles.pop(key, None)
            self._initial_breakpoint_seen.discard(pid)
