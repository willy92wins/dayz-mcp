from __future__ import annotations

import hashlib
import importlib
import io
import json
import math
import pathlib
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

from PIL import Image


# The smoke sits next to mcp_client.py in tools/, not in a scripts/ subdir as
# it did in DayZ_Tooling; parents[1] IS the directory to import from here.
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import task9_build_a_smoke as smoke


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _process(pid: int, command_line: str, name: str = "DayZDiag_x64.exe") -> smoke.ProcessRecord:
    return smoke.ProcessRecord(pid=pid, name=name, command_line=command_line)


def _owned_snapshot() -> smoke.OwnershipSnapshot:
    server = (
        '"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ\\DayZDiag_x64.exe" '
        '-server -port=2302 '
        '-profiles="C:\\Users\\guill\\OneDrive\\Documentos\\DayZ Projects\\'
        'MERCEDES_AMGLF_dev\\_server\\profiles" '
        '-mod="@CF;@MERCEDES_AMGLF;@DayZ_MCP"'
    )
    client = (
        '"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ\\DayZDiag_x64.exe" '
        '-connect=127.0.0.1 -port=2302 '
        '-profiles="C:\\Users\\guill\\OneDrive\\Documentos\\DayZ Projects\\'
        'MERCEDES_AMGLF_dev\\_client\\profiles" '
        '-mod="@CF;@MERCEDES_AMGLF;@DayZ_MCP"'
    )
    daemon = (
        '"C:\\Users\\guill\\OneDrive\\Documentos\\DayZ Projects\\DayZ_MCP_dev\\tools\\'
        '.venv-mcp\\Scripts\\python.exe" -m dayz_mcp --daemon --port 8765 '
        '--keyfile="C:\\Users\\guill\\OneDrive\\Documentos\\DayZ Projects\\'
        'DayZ_MCP_dev\\tools\\.dayz_mcp.key" --require-version --idle-timeout 1800.0'
    )
    return smoke.OwnershipSnapshot(
        processes=(
            _process(101, server),
            _process(202, client),
            _process(303, daemon, "python.exe"),
        ),
        ports=(
            smoke.PortBinding(protocol="UDP", local_port=2302, pid=101),
            smoke.PortBinding(protocol="TCP", local_port=8765, pid=303),
        ),
    )


def _foreign_snapshot() -> smoke.OwnershipSnapshot:
    owned = _owned_snapshot()
    foreign = _process(
        404,
        'DayZDiag_x64.exe -server -port=2318 -profiles="C:\\LF_VStorage_dev\\_server\\profiles"',
    )
    return smoke.OwnershipSnapshot(
        processes=(*owned.processes, foreign),
        ports=owned.ports,
    )


def _replace_process_command(
    snapshot: smoke.OwnershipSnapshot, pid: int, old: str, new: str
) -> smoke.OwnershipSnapshot:
    return smoke.OwnershipSnapshot(
        processes=tuple(
            _process(process.pid, process.command_line.replace(old, new), process.name)
            if process.pid == pid
            else process
            for process in snapshot.processes
        ),
        ports=snapshot.ports,
    )


def _replace_process_mods(
    snapshot: smoke.OwnershipSnapshot, pid: int, components: tuple[str, ...]
) -> smoke.OwnershipSnapshot:
    return _replace_process_command(
        snapshot,
        pid,
        '-mod="@CF;@MERCEDES_AMGLF;@DayZ_MCP"',
        f'-mod="{";".join(components)}"',
    )


class SequenceProvider:
    def __init__(self, *items: object, events: list[str] | None = None) -> None:
        self.items = list(items)
        self.calls = 0
        self.events = events

    def snapshot(self) -> smoke.OwnershipSnapshot:
        self.calls += 1
        if self.events is not None:
            self.events.append("snapshot")
        item = self.items[min(self.calls - 1, len(self.items) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]


class CostedSequenceProvider(SequenceProvider):
    def __init__(self, clock: "FakeClock", costs: tuple[float, ...], *items: object) -> None:
        super().__init__(*items)
        self.clock = clock
        self.costs = costs

    def snapshot(self) -> smoke.OwnershipSnapshot:
        self.clock.advance(self.costs[self.calls % len(self.costs)])
        return super().snapshot()


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.monotonic_calls = 0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.monotonic_calls += 1
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _peer_status(
    *,
    queue_depth: int = 0,
    poll_age_s: float = 0.1,
    generation: str = "generation-a",
    version_state: str = "ok",
) -> dict[str, object]:
    return {
        "daemon_generation": generation,
        "server_peer": {
            "last_poll_age_s": poll_age_s,
            "queue_depth": queue_depth,
            "version_state": version_state,
        },
    }


class SpawnProtocolClient:
    def __init__(
        self,
        statuses: list[dict[str, object]],
        awaits: list[dict[str, object]],
        command_id: int = 901,
    ) -> None:
        self.statuses = list(statuses)
        self.awaits = list(awaits)
        self.command_id = command_id
        self.enqueue_calls: list[dict[str, object]] = []
        self.request_calls: list[dict[str, object]] = []

    def enqueue_cmd(
        self,
        cmd: str,
        args: dict[str, object],
        peer: str | None = None,
        *,
        operation_timeout_s: float = 0.0,
    ) -> int:
        self.enqueue_calls.append(
            {
                "cmd": cmd,
                "args": dict(args),
                "peer": peer,
                "operation_timeout_s": operation_timeout_s,
            }
        )
        return self.command_id

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.request_calls.append(
            {
                "method": method,
                "path": path,
                "payload": payload,
                "query": dict(query or {}),
            }
        )
        if method == "GET" and path == "/status":
            if not self.statuses:
                raise AssertionError("unexpected_status_request")
            return self.statuses.pop(0)
        if method == "GET" and path == "/await":
            if not self.awaits:
                raise AssertionError("unexpected_await_request")
            return self.awaits.pop(0)
        raise AssertionError(f"unexpected_request:{method}:{path}")


class FakeWin32Call:
    def __init__(self, implementation: object) -> None:
        self.implementation = implementation
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.implementation(*args)  # type: ignore[operator]


class FakeUser32:
    def __init__(
        self,
        foreground_hwnds: list[int] | None = None,
        windows: dict[int, dict[str, object]] | None = None,
        send_result: int = 2,
        set_foreground_result: int = 1,
        dpi_enter_result: int = 0x1111,
        dpi_restore_result: int = 0xFFFFFFFFFFFFFFFD,
    ) -> None:
        self.windows = windows or {
            0x2020: {
                "pid": 202,
                "class": "DayZ",
                "title": "DayZ",
                "rect": (100, 50, 2042, 1186),
                "visible": True,
            },
            0x3030: {
                "pid": 303,
                "class": "Chrome_WidgetWin_1",
                "title": "Claude",
                "rect": (0, 0, 1280, 720),
                "visible": True,
            },
        }
        self.foreground_hwnds = list(foreground_hwnds or [0x3030])
        self.current_foreground = self.foreground_hwnds[0] if self.foreground_hwnds else 0
        self.send_result = send_result
        self.set_foreground_result = set_foreground_result
        self.dpi_enter_result = dpi_enter_result
        self.dpi_restore_result = dpi_restore_result
        self.cursor_moves: list[tuple[int, int]] = []
        self.sent_flags: list[int] = []
        self.sent_keyboard: list[tuple[int, int]] = []
        self.sent_keyboard_batches: list[list[tuple[int, int]]] = []
        self.sent_input_sizes: list[int] = []
        self.show_window_commands: list[tuple[int, int]] = []
        self.set_window_pos_calls: list[tuple[int, int, int, int, int, int, int]] = []
        self.set_foreground_calls: list[int] = []
        self.attach_calls: list[tuple[int, int, bool]] = []
        self.dpi_context_calls: list[int] = []
        self.events: list[tuple[str, int]] = []
        self.SetThreadDpiAwarenessContext = FakeWin32Call(
            self._set_thread_dpi_awareness_context
        )
        self.EnumWindows = FakeWin32Call(self._enum_windows)
        self.IsWindowVisible = FakeWin32Call(self._is_window_visible)
        self.GetWindowThreadProcessId = FakeWin32Call(self._window_pid)
        self.GetWindowTextW = FakeWin32Call(self._window_text)
        self.GetClassNameW = FakeWin32Call(self._class_name)
        self.GetWindowRect = FakeWin32Call(self._window_rect)
        self.GetForegroundWindow = FakeWin32Call(self._foreground_window)
        self.GetCurrentThreadId = FakeWin32Call(lambda: 777)
        self.AttachThreadInput = FakeWin32Call(self._attach_thread_input)
        self.ShowWindow = FakeWin32Call(self._show_window)
        self.SetWindowPos = FakeWin32Call(self._set_window_pos)
        self.BringWindowToTop = FakeWin32Call(lambda _hwnd: 1)
        self.SetForegroundWindow = FakeWin32Call(self._set_foreground_window)
        self.GetCursorPos = FakeWin32Call(self._get_cursor)
        self.SetCursorPos = FakeWin32Call(self._set_cursor)
        self.SendInput = FakeWin32Call(self._send_input)

    def _enum_windows(self, callback: object, lparam: int) -> int:
        self.events.append(("enum", 0))
        for hwnd in self.windows:
            if not callback(hwnd, lparam):  # type: ignore[operator]
                return 0
        return 1

    def _set_thread_dpi_awareness_context(self, context: object) -> int:
        raw = getattr(context, "value", context)
        value = int(raw)
        self.dpi_context_calls.append(value)
        self.events.append(("dpi", value))
        if len(self.dpi_context_calls) == 1:
            return self.dpi_enter_result
        return self.dpi_restore_result

    def _is_window_visible(self, hwnd: int) -> int:
        return int(bool(self.windows.get(hwnd, {}).get("visible")))

    def _window_pid(self, hwnd: int, pointer: object) -> int:
        window = self.windows.get(int(hwnd))
        if window is None:
            return 0
        smoke.ctypes.cast(
            pointer, smoke.ctypes.POINTER(smoke.wintypes.DWORD)
        ).contents.value = int(window["pid"])
        return int(window["pid"]) + 1000

    def _window_text(self, hwnd: int, buffer: object, _length: int) -> int:
        value = str(self.windows.get(int(hwnd), {}).get("title", ""))
        buffer.value = value  # type: ignore[attr-defined]
        return len(value)

    def _class_name(self, hwnd: int, buffer: object, _length: int) -> int:
        value = str(self.windows.get(int(hwnd), {}).get("class", ""))
        buffer.value = value  # type: ignore[attr-defined]
        return len(value)

    def _window_rect(self, hwnd: int, pointer: object) -> int:
        window = self.windows.get(int(hwnd))
        if window is None:
            return 0
        left, top, right, bottom = window["rect"]  # type: ignore[misc]
        rect = smoke.ctypes.cast(
            pointer, smoke.ctypes.POINTER(smoke.wintypes.RECT)
        ).contents
        rect.left = int(left)
        rect.top = int(top)
        rect.right = int(right)
        rect.bottom = int(bottom)
        return 1

    def _foreground_window(self) -> int:
        if self.foreground_hwnds:
            self.current_foreground = self.foreground_hwnds.pop(0)
        return self.current_foreground

    def _show_window(self, hwnd: int, command: int) -> int:
        self.show_window_commands.append((int(hwnd), int(command)))
        return 1

    def _set_window_pos(
        self,
        hwnd: int,
        insert_after: int,
        x: int,
        y: int,
        width: int,
        height: int,
        flags: int,
    ) -> int:
        call = (
            int(hwnd),
            int(insert_after or 0),
            int(x),
            int(y),
            int(width),
            int(height),
            int(flags),
        )
        self.set_window_pos_calls.append(call)
        window = self.windows.get(int(hwnd))
        if window is not None:
            left, top, right, bottom = window["rect"]  # type: ignore[misc]
            current_width = int(right) - int(left)
            current_height = int(bottom) - int(top)
            if int(flags) & 0x0001:
                target_width = current_width
                target_height = current_height
            else:
                target_width = int(width)
                target_height = int(height)
            window["rect"] = (
                int(x),
                int(y),
                int(x) + target_width,
                int(y) + target_height,
            )
        return 1

    def _attach_thread_input(self, source: int, target: int, attach: int) -> int:
        self.attach_calls.append((source, target, bool(attach)))
        return 1

    def _set_foreground_window(self, hwnd: int) -> int:
        self.set_foreground_calls.append(int(hwnd))
        if self.set_foreground_result:
            self.current_foreground = int(hwnd)
        return self.set_foreground_result

    def _get_cursor(self, pointer: object) -> int:
        point = smoke.ctypes.cast(
            pointer, smoke.ctypes.POINTER(smoke.wintypes.POINT)
        ).contents
        point.x = 50
        point.y = 60
        return 1

    def _set_cursor(self, x: int, y: int) -> int:
        self.cursor_moves.append((x, y))
        return 1

    def _send_input(self, count: int, inputs: object, size: int) -> int:
        self.sent_input_sizes.append(int(size))
        if count and int(inputs[0].type) == 1:  # type: ignore[index]
            self.sent_keyboard = [
                (int(inputs[index].ki.wVk), int(inputs[index].ki.dwFlags))  # type: ignore[index]
                for index in range(count)
            ]
            self.sent_keyboard_batches.append(list(self.sent_keyboard))
        else:
            self.sent_flags = [
                int(inputs[index].mi.dwFlags)  # type: ignore[index]
                for index in range(count)
            ]
        return self.send_result


def _task9_owned_window() -> dict[str, object]:
    return {
        "pid": 202,
        "class": "DayZ",
        "title": "DayZ",
        "left": 100,
        "top": 50,
        "width": 1942,
        "height": 1136,
    }


def _task9_black_inspection() -> dict[str, object]:
    return {
        "ok": False,
        "detected": False,
        "reason": "continue_client_stats_invalid",
        "client_pid": 202,
        "window": _task9_owned_window(),
    }


class FakeRuntime:
    server_profile_root: pathlib.Path | None = None
    client_profile_root: pathlib.Path | None = None

    def __init__(self, clock: FakeClock | None = None, events: list[str] | None = None) -> None:
        self.clock = clock or FakeClock()
        self.events = events
        self.client_factory_calls = 0
        self.query_calls = 0
        self.query_timeouts: list[float] = []
        self.query_durations: list[float] = []
        self.query_started_at: list[float] = []
        self.player_state_results: list[dict[str, object]] = [
            {"ok": True, "pos": [1000.0, 50.0, 2000.0]}
        ]
        self.readiness_calls: list[dict[str, object]] = []
        self.readiness_results: list[dict[str, object]] | None = None
        self.frontend_inspection_calls: list[dict[str, object]] = []
        self.frontend_inspection_results: list[dict[str, object]] | None = None
        self.frontend_activation_calls: list[dict[str, object]] = []
        self.frontend_resume_calls: list[dict[str, object]] = []
        self.raycast_calls: list[dict[str, object]] = []
        self.telemetry_calls: list[dict[str, object]] = []
        self.spawn_calls: list[dict[str, object]] = []
        self.prepare_calls: list[dict[str, object]] = []
        self.client_settlement_calls: list[dict[str, object]] = []
        self.camera_calls: list[dict[str, object]] = []
        self.camera_ok_by_view: dict[str, object] = {}
        self.camera_result_by_view: dict[str, dict[str, object]] = {}
        self.capture_calls: list[dict[str, object]] = []
        self.capture_identical = False
        self.cleanup_calls: list[int] = []
        self.readiness_result: dict[str, object] = {
            "inworld": True,
            "iterations": 4,
            "elapsed_s": 12.5,
        }
        self.frontend_inspection_result: dict[str, object] = {
            "ok": True,
            "detected": False,
            "reason": "continue_overlay_absent",
        }
        self.frontend_resume_result: dict[str, object] = {
            "ok": True,
            "attempted": True,
            "reason": "continue_clicked",
        }
        self.frontend_activation_result: dict[str, object] = {
            "ok": True,
            "attempted": True,
            "reason": "foreground_activated",
            "foreground_pid": 202,
        }
        self.raycast_result: dict[str, object] = {
            "ok": True,
            "raycast": {
                "hit": True,
                "pos": [1004.0, 49.75, 2004.0],
                "normal": [0.0, 1.0, 0.0],
                "object_type": "",
                "parent_type": "",
            },
        }
        self.spawn_result: object = {
            "ok": True,
            "object_id": 77,
            "pos": [1004.0, 49.75, 2004.0],
        }
        self.telemetry_results: list[dict[str, object]] = [
            {"ok": True, "telemetry": {"found": False}},
            {
                "ok": True,
                "telemetry": {
                    "found": True,
                    "type": smoke.OBJECT_TYPE,
                    "class_name": smoke.OBJECT_TYPE,
                    "pos": [1004.0, 49.75, 2004.0],
                },
            },
            {"ok": True, "telemetry": {"found": False}},
        ]
        self.prepare_result: dict[str, object] = {
            "ok": True,
            "vehicle_fixture_ready": True,
            "telemetry": {
                "found": True,
                "type": smoke.OBJECT_TYPE,
                "class_name": smoke.OBJECT_TYPE,
                "wheel_count": 4,
                "attachment_count": 4,
                "items": ["MERCEDES_AMGLF_Wheel"] * 4,
            },
        }
        self.client_settlement_result: dict[str, object] = {
            "ready": True,
            "samples_required": 5,
            "samples_observed": 5,
            "last_poll_age_s": 0.1,
        }
        self.spawn_exception: BaseException | None = None
        self.cleanup_ok = True
        self.cleanup_result: dict[str, object] = {"ok": True, "deleted": 1}
        self.capture_error_view = ""
        self.capture_invalid_magic = False
        self.capture_corrupt_jpeg_body = False
        self.log_records_override: list[dict[str, object]] | None = None
        if self.server_profile_root is None or self.client_profile_root is None:
            raise RuntimeError("FakeRuntime.trusted_log_roots_not_configured")

    def client_factory(self) -> object:
        self.client_factory_calls += 1
        return object()

    def query_player_state(
        self, client: object, timeout_s: float = 30.0
    ) -> dict[str, object]:
        index = min(self.query_calls, len(self.player_state_results) - 1)
        duration = (
            self.query_durations[self.query_calls]
            if self.query_calls < len(self.query_durations)
            else 0.0
        )
        self.query_timeouts.append(timeout_s)
        self.query_started_at.append(self.clock.monotonic())
        self.query_calls += 1
        self.clock.advance(duration)
        return dict(self.player_state_results[index])

    def wait_for_readiness(
        self,
        client: object,
        camera_position: list[float],
        look_at: list[float],
        client_pid: int,
        client_cmdline_match: str,
        timeout_s: float,
    ) -> dict[str, object]:
        call_index = len(self.readiness_calls)
        self.readiness_calls.append(
            {
                "camera_position": camera_position,
                "look_at": look_at,
                "client_pid": client_pid,
                "client_cmdline_match": client_cmdline_match,
                "timeout_s": timeout_s,
            }
        )
        if self.readiness_results is not None:
            index = min(call_index, len(self.readiness_results) - 1)
            return dict(self.readiness_results[index])
        return dict(self.readiness_result)

    def inspect_frontend_overlay(
        self,
        client_pid: int,
        client_cmdline_match: str,
        evidence_filename: str = "task9-continue-preaction.png",
    ) -> dict[str, object]:
        call_index = len(self.frontend_inspection_calls)
        self.frontend_inspection_calls.append(
            {
                "client_pid": client_pid,
                "client_cmdline_match": client_cmdline_match,
                "evidence_filename": evidence_filename,
            }
        )
        if self.events is not None:
            self.events.append("frontend_inspect")
        if self.frontend_inspection_results is not None:
            index = min(call_index, len(self.frontend_inspection_results) - 1)
            return dict(self.frontend_inspection_results[index])
        return dict(self.frontend_inspection_result)

    def resume_frontend_overlay(
        self, client_pid: int, inspection: dict[str, object]
    ) -> dict[str, object]:
        self.frontend_resume_calls.append(
            {"client_pid": client_pid, "inspection": dict(inspection)}
        )
        if self.events is not None:
            self.events.append("frontend_resume")
        return dict(self.frontend_resume_result)

    def activate_frontend_window(
        self, client_pid: int, inspection: dict[str, object]
    ) -> dict[str, object]:
        self.frontend_activation_calls.append(
            {"client_pid": client_pid, "inspection": dict(inspection)}
        )
        if self.events is not None:
            self.events.append("foreground_activate")
        return dict(self.frontend_activation_result)

    def raycast(self, client: object, start: list[float], end: list[float]) -> dict[str, object]:
        self.raycast_calls.append({"start": start, "end": end})
        return dict(self.raycast_result)

    def telemetry_object_at(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, object]:
        self.telemetry_calls.append({"position": list(position), "radius": radius})
        if self.events is not None:
            self.events.append("telemetry")
        if self.cleanup_calls:
            index = len(self.telemetry_results) - 1
        else:
            index = min(len(self.telemetry_calls) - 1, len(self.telemetry_results) - 1)
        return dict(self.telemetry_results[index])

    def spawn(self, client: object, object_type: str, position: list[float], flags: int) -> object:
        self.spawn_calls.append(
            {"object_type": object_type, "position": position, "flags": flags}
        )
        if self.events is not None:
            self.events.append("spawn")
        if self.spawn_exception is not None:
            raise self.spawn_exception
        if isinstance(self.spawn_result, dict):
            return dict(self.spawn_result)
        return self.spawn_result

    def prepare_vehicle_fixture(
        self, client: object, position: list[float], radius: float
    ) -> dict[str, object]:
        self.prepare_calls.append({"position": list(position), "radius": radius})
        if self.events is not None:
            self.events.append("prepare")
        return dict(self.prepare_result)

    def wait_for_client_peer_settlement(
        self, client: object, timeout_s: float
    ) -> dict[str, object]:
        self.client_settlement_calls.append({"timeout_s": timeout_s})
        if self.events is not None:
            self.events.append("client_settle")
        return dict(self.client_settlement_result)

    def set_camera(
        self,
        client: object,
        view: str,
        camera_position: list[float],
        look_at: list[float],
    ) -> dict[str, object]:
        self.camera_calls.append(
            {"view": view, "camera_position": camera_position, "look_at": look_at}
        )
        if self.events is not None:
            self.events.append(f"camera:{view}")
        if view in self.camera_result_by_view:
            return dict(self.camera_result_by_view[view])
        ok = self.camera_ok_by_view.get(view, True)
        return {
            "ok": ok,
            "camera": {
                "ok": ok,
                "viewport_moved": ok,
                "applied_mode": "lookat",
                "pos": list(camera_position),
            },
        }

    def capture(
        self,
        view: str,
        destination: pathlib.Path,
        client_pid: int,
        client_cmdline_match: str,
    ) -> dict[str, object]:
        self.capture_calls.append(
            {
                "view": view,
                "destination": destination,
                "client_pid": client_pid,
                "client_cmdline_match": client_cmdline_match,
            }
        )
        if view == self.capture_error_view:
            return {"isError": True, "error": "capture_failed"}
        if self.capture_invalid_magic:
            destination.write_bytes(b"not-a-jpeg")
        elif self.capture_corrupt_jpeg_body:
            destination.write_bytes(b"\xff\xd8\xff\xe0corrupt-body\xff\xd9")
        else:
            view_index = 0 if self.capture_identical else list(smoke.VIEW_SPECS).index(view)
            with Image.new("RGB", (2, 2), color=(32 + (view_index * 16), 64, 96)) as image:
                image.save(destination, format="JPEG")
        return {
            "fullres_path": str(destination),
            "meta": {"native_width": 1920, "native_height": 1080},
        }

    def cleanup(self, client: object, object_id: int) -> dict[str, object]:
        if self.events is not None:
            self.events.append("cleanup")
        self.cleanup_calls.append(object_id)
        if not self.cleanup_ok:
            return {"ok": False, "deleted": 0}
        return dict(self.cleanup_result)

    def collect_logs(self) -> list[dict[str, object]]:
        if self.events is not None:
            self.events.append("logs")
        if self.log_records_override is not None:
            return [dict(record) for record in self.log_records_override]
        records: list[dict[str, object]] = []
        for source, profile_path in (
            ("server", self.server_profile_root),
            ("client", self.client_profile_root),
        ):
            profile_path.mkdir(parents=True, exist_ok=True)
            log_path = profile_path / f"{source}.RPT"
            log_path.write_text(f"{source} smoke log\n", encoding="utf-8")
            records.append(
                {
                    "source": source,
                    "profile_path": str(profile_path.resolve()),
                    "path": str(log_path.resolve()),
                    "sha256": _sha256(log_path),
                }
            )
        return records

    def monotonic(self) -> float:
        return self.clock.monotonic()

    def sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)


class Task9SmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.server_profiles = self.root / "trusted-server-profiles"
        self.client_profiles = self.root / "trusted-client-profiles"
        FakeRuntime.server_profile_root = self.server_profiles
        FakeRuntime.client_profile_root = self.client_profiles
        self.host = self.root / "host.p3d"
        self.pbo = self.root / "vehicle.pbo"
        self.audit = self.root / "audit.json"
        self.backup = self.root / "backup-manifest.json"
        self.active_mission_init = self.root / "active-init.c"
        self.stock_mission_init = self.root / "stock-init.c"
        neutral_init = b"void main()\n{\n}\n"
        self.active_mission_init.write_bytes(neutral_init)
        self.stock_mission_init.write_bytes(neutral_init)
        for path, data in (
            (self.host, b"host"),
            (self.pbo, b"pbo"),
            (self.audit, b"audit"),
            (self.backup, b"backup"),
        ):
            path.write_bytes(data)

    def config(self, name: str = "run") -> smoke.SmokeConfig:
        output_dir = self.root / name
        return smoke.SmokeConfig(
            output_dir=output_dir,
            artifacts={
                "host_p3d": smoke.ArtifactSpec(self.host, _sha256(self.host)),
                "pbo": smoke.ArtifactSpec(self.pbo, _sha256(self.pbo)),
            },
            server_profiles=self.server_profiles,
            client_profiles=self.client_profiles,
            active_mission_init=self.active_mission_init,
            stock_mission_init=self.stock_mission_init,
            hold_seconds=30.0,
            hold_interval=5.0,
        )

    def task9_default_runtime(self, name: str) -> smoke.DefaultRuntime:
        runtime = smoke.DefaultRuntime(self.config(name))
        runtime.mcp_client = mock.Mock()
        return runtime

    def task9_readiness_fixture(
        self,
        runtime: smoke.DefaultRuntime,
        *,
        size: tuple[int, int] = (200, 100),
        red_run_global_fraction: float = 0.0,
        filename: str = "ready_04_02.png",
    ) -> tuple[pathlib.Path, dict[str, object]]:
        temp = tempfile.TemporaryDirectory(prefix="phase3_ready_")
        self.addCleanup(temp.cleanup)
        path = pathlib.Path(temp.name) / filename
        with Image.new("RGB", size, color=(114, 114, 114)) as image:
            if red_run_global_fraction > 0.0:
                line_x0 = int(image.width * (1431 / 1942))
                line_y = int(image.height * (903 / 1136))
                run_width = max(1, math.ceil(image.width * red_run_global_fraction))
                image.paste(
                    (220, 0, 0),
                    (
                        line_x0,
                        line_y,
                        min(image.width, line_x0 + run_width),
                        min(image.height, line_y + 2),
                    ),
                )
            image.save(path, format="PNG")
        image = runtime.mcp_capture.load_rgb(str(path))
        try:
            stats = runtime.mcp_capture.image_stats_from_image(image)
        finally:
            image.close()
        return path, {
            "inworld": False,
            "elapsed_s": 90.0,
            "inter_sample_deltas": [0.004, 0.003],
            "last_mean": round(float(stats["meanBrightness"]), 1),
            "last_nonblack": round(float(stats["nonBlackRatio"]), 4),
            "last_burst": {
                "ok": True,
                "grabs": [
                    {
                        "ok": True,
                        "error": "",
                        "method": "printwindow",
                        "window": {
                            "pid": 202,
                            "class": "DayZ",
                            "title": "DayZ",
                            "left": 10,
                            "top": 20,
                            "width": size[0],
                            "height": size[1],
                        },
                        "stats": {
                            "meanBrightness": stats["meanBrightness"],
                            "nonBlackRatio": stats["nonBlackRatio"],
                        },
                        "client": {
                            "left": 0,
                            "top": 0,
                            "width": size[0],
                            "height": size[1],
                        },
                        "clientStats": {
                            "meanBrightness": stats["meanBrightness"],
                            "nonBlackRatio": stats["nonBlackRatio"],
                        },
                        "sha256": _sha256(path),
                    }
                ],
                "last_path": str(path),
            },
            "thresholds": {
                "min_settle_s": 20.0,
                "stability_max": 0.02,
                "stable_samples": 2,
                "menu_max_mean": 86.0,
                "nonblack_min": 0.10,
            },
        }

    def task9_real_client_black_fixture(
        self, runtime: smoke.DefaultRuntime
    ) -> tuple[pathlib.Path, dict[str, object]]:
        path, payload = self.task9_readiness_fixture(runtime)
        size = (200, 100)
        client_left = 80
        with Image.new("RGB", size, color=(0, 0, 0)) as image:
            image.paste((255, 255, 255), (0, 0, client_left, size[1]))
            image.save(path, format="PNG")
        image = runtime.mcp_capture.load_rgb(str(path))
        try:
            stats = runtime.mcp_capture.image_stats_from_image(image)
        finally:
            image.close()
        payload["last_mean"] = round(float(stats["meanBrightness"]), 1)
        payload["last_nonblack"] = round(float(stats["nonBlackRatio"]), 4)
        selected_grab = payload["last_burst"]["grabs"][-1]
        selected_grab["stats"] = {
            "meanBrightness": stats["meanBrightness"],
            "nonBlackRatio": stats["nonBlackRatio"],
        }
        selected_grab["client"] = {
            "left": client_left,
            "top": 0,
            "width": size[0] - client_left,
            "height": size[1],
        }
        selected_grab["clientStats"] = {
            "meanBrightness": 114.0,
            "nonBlackRatio": 1.0,
        }
        selected_grab["sha256"] = _sha256(path)
        return path, payload

    def task9_continue_overlay_fixture(
        self, runtime: smoke.DefaultRuntime
    ) -> tuple[pathlib.Path, dict[str, object]]:
        path = self.root / "task9-continue-overlay.png"
        size = (1942, 1136)
        with Image.new("RGB", size, color=(96, 96, 96)) as image:
            image.paste((45, 48, 52), (1428, 775, 1834, 997))
            image.paste((220, 0, 0), (1431, 902, 1831, 904))
            for x in range(1470, 1797, 16):
                image.paste((235, 235, 235), (x, 933, min(x + 8, 1797), 974))
            image.save(path, format="PNG")
        return path, {
            "ok": True,
            "error": "",
            "method": "foreground",
            "window": {
                "pid": 202,
                "class": "DayZ",
                "title": "DayZ",
                "left": 100,
                "top": 50,
                "width": size[0],
                "height": size[1],
            },
            "client": {
                "left": 11,
                "top": 45,
                "width": 1920,
                "height": 1080,
            },
            "clientStats": {
                "meanBrightness": 91.9,
                "nonBlackRatio": 0.996,
            },
            "sha256": _sha256(path),
        }

    @staticmethod
    def task9_clone_readiness(payload: dict[str, object]) -> dict[str, object]:
        return json.loads(json.dumps(payload))

    def task9_wait_for_readiness(
        self,
        runtime: smoke.DefaultRuntime,
        payload: object,
    ) -> object:
        runtime.mcp_client.wait_for_inworld_render.return_value = payload
        return runtime.wait_for_readiness(
            object(),
            [1000.0, 51.8, 2000.0],
            [1004.0, 49.75, 2004.0],
            202,
            "DayZDiag_x64.exe",
            90.0,
        )

    @staticmethod
    def http_error(
        code: int,
        body: bytes,
        fp: object | None = None,
    ) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url="http://127.0.0.1:8765/enqueue",
            code=code,
            msg="Conflict",
            hdrs=None,
            fp=fp if fp is not None else io.BytesIO(body),
        )

    def _assert_version_blocked_state(self, body: object, state: str) -> None:
        self.assertIs(dict, type(body))
        assert isinstance(body, dict)
        self.assertEqual("version_blocked", body.get("error"))
        self.assertEqual(state, body.get("state"))
        self.assertEqual(
            set(),
            set(body) - {"error", "state", "expected", "got", "detail"},
        )

    @staticmethod
    def bind_default_player_query(runtime: FakeRuntime) -> None:
        runtime.query_player_state = smoke.DefaultRuntime.query_player_state.__get__(
            runtime, FakeRuntime
        )

    def finalize(self, config: smoke.SmokeConfig, decision: str) -> smoke.RunOutcome:
        return smoke.finalize(
            config.verdict_path,
            decision,
            trusted_log_roots={
                "server": config.server_profiles,
                "client": config.client_profiles,
            },
        )

    def run_success(
        self,
        name: str = "success",
        provider: SequenceProvider | None = None,
        runtime: FakeRuntime | None = None,
    ) -> tuple[smoke.RunOutcome, smoke.SmokeConfig, SequenceProvider, FakeRuntime]:
        provider = provider or SequenceProvider(_owned_snapshot())
        runtime = runtime or FakeRuntime()
        config = self.config(name)
        outcome = smoke.collect(config, provider, lambda _: runtime)
        return outcome, config, provider, runtime

    def test_foreign_dayz_stops_before_runtime_client_bridge_camera_readiness_capture_or_spawn(self) -> None:
        provider = SequenceProvider(_foreign_snapshot())
        runtime = FakeRuntime()
        runtime_factory_calls = 0

        def runtime_factory(config: smoke.SmokeConfig) -> FakeRuntime:
            nonlocal runtime_factory_calls
            runtime_factory_calls += 1
            return runtime

        outcome = smoke.collect(self.config("foreign"), provider, runtime_factory)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual("STOP", outcome.payload["result"])
        self.assertIn("foreign_dayz", outcome.payload["stop_reason"])
        self.assertEqual(0, runtime_factory_calls)
        self.assertEqual(0, runtime.client_factory_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.camera_calls)
        self.assertEqual([], runtime.capture_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_autospawn_mission_stops_before_runtime_factory(self) -> None:
        self.active_mission_init.write_text(
            'void main() { GetGame().CreateObjectEx("MERCEDES_AMGLF", "0 0 0", 0); }\n',
            encoding="utf-8",
        )
        runtime_factory = mock.Mock(return_value=FakeRuntime())

        outcome = smoke.collect(
            self.config("autospawn-mission"), SequenceProvider(_owned_snapshot()), runtime_factory
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("active_mission_init_autospawn", outcome.payload["stop_reason"])
        runtime_factory.assert_not_called()

    def test_nonstock_neutral_mission_and_missing_inputs_fail_closed(self) -> None:
        cases = (
            (
                "divergent",
                b'void main() { Print("different"); }\n',
                True,
                "active_mission_init_not_stock",
            ),
            ("active-missing", b"", False, "active_mission_init_missing"),
        )
        for name, content, active_exists, reason in cases:
            with self.subTest(case=name):
                if self.active_mission_init.exists():
                    self.active_mission_init.unlink()
                if active_exists:
                    self.active_mission_init.write_bytes(content)
                runtime_factory = mock.Mock(return_value=FakeRuntime())
                outcome = smoke.collect(
                    self.config(f"mission-{name}"),
                    SequenceProvider(_owned_snapshot()),
                    runtime_factory,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn(reason, outcome.payload["stop_reason"])
                runtime_factory.assert_not_called()
                self.active_mission_init.write_bytes(self.stock_mission_init.read_bytes())

    def test_missing_stock_mission_stops_before_runtime_factory(self) -> None:
        stock_bytes = self.stock_mission_init.read_bytes()
        self.stock_mission_init.unlink()
        try:
            runtime_factory = mock.Mock(return_value=FakeRuntime())

            outcome = smoke.collect(
                self.config("stock-missing"),
                SequenceProvider(_owned_snapshot()),
                runtime_factory,
            )

            self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
            self.assertIn("stock_mission_init_missing", outcome.payload["stop_reason"])
            runtime_factory.assert_not_called()
        finally:
            self.stock_mission_init.write_bytes(stock_bytes)

    def test_exact_owned_pair_udp_server_and_tcp_daemon_pass_preflight(self) -> None:
        result = smoke.validate_ownership(_owned_snapshot())

        self.assertTrue(result.ok)
        self.assertEqual(101, result.server_pid)
        self.assertEqual(202, result.client_pid)
        self.assertEqual(303, result.daemon_pid)

    def test_dayz_tools_launcher_is_not_counted_as_a_dayz_runtime_process(self) -> None:
        owned = _owned_snapshot()
        snapshot = smoke.OwnershipSnapshot(
            processes=(
                *owned.processes,
                _process(
                    404,
                    '"C:\\Program Files (x86)\\Steam\\steamapps\\common\\DayZ Tools\\Bin\\Launcher\\DayZToolsLauncher.exe"',
                    "DayZToolsLauncher.exe",
                ),
            ),
            ports=owned.ports,
        )

        result = smoke.validate_ownership(snapshot)

        self.assertTrue(result.ok, result.reason)

    @unittest.skipUnless(pathlib.Path("P:/").exists(), "P: project mapping unavailable")
    def test_subst_profile_paths_match_their_canonical_project_paths(self) -> None:
        snapshot = _replace_process_command(
            _owned_snapshot(),
            101,
            r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF_dev\_server\profiles",
            r"P:\MERCEDES_AMGLF_dev\_server\profiles",
        )
        snapshot = _replace_process_command(
            snapshot,
            202,
            r"C:\Users\guill\OneDrive\Documentos\DayZ Projects\MERCEDES_AMGLF_dev\_client\profiles",
            r"P:\MERCEDES_AMGLF_dev\_client\profiles",
        )

        result = smoke.validate_ownership(snapshot)

        self.assertTrue(result.ok, result.reason)

    def test_shared_daemon_requires_bare_daemon_and_require_version_switches(self) -> None:
        cases = (
            (" --daemon", "", "daemon_command_mismatch"),
            (" --require-version", "", "daemon_command_mismatch"),
        )
        for old, new, expected_reason in cases:
            with self.subTest(missing=old.strip()):
                snapshot = _replace_process_command(_owned_snapshot(), 303, old, new)

                result = smoke.validate_ownership(snapshot)

                self.assertFalse(result.ok)
                self.assertEqual(expected_reason, result.reason)

    def test_default_runtime_client_uses_exact_broker_identity_and_lease_from_environment(
        self,
    ) -> None:
        runtime = self.task9_default_runtime("broker-client")
        identity = {
            "platform": "codex",
            "pid": 42001,
            "ppid": 42000,
            "started_at_utc": "2026-07-16T20:00:00Z",
            "session_id": "task9-driver",
            "task_label": "MercedesAMGLF Task 9",
        }
        with mock.patch.dict(
            smoke.os.environ,
            {
                "DAYZ_MCP_CLIENT_ID_JSON": json.dumps(identity, separators=(",", ":")),
                "DAYZ_MCP_LEASE_TOKEN": "test-driver-lease",
            },
            clear=False,
        ):
            client = runtime.client_factory()

        self.assertIs(client, runtime.mcp_client.Client.return_value)
        runtime.mcp_client.Client.assert_called_once_with(
            port=8765,
            key=self.config("broker-client").keyfile.read_text(encoding="utf-8").strip(),
            timeout_s=30.0,
            identity=identity,
            lease_token="test-driver-lease",
        )

    def test_default_runtime_client_fails_closed_without_broker_session_environment(
        self,
    ) -> None:
        runtime = self.task9_default_runtime("broker-client-missing-session")
        with mock.patch.dict(
            smoke.os.environ,
            {
                "DAYZ_MCP_CLIENT_ID_JSON": "",
                "DAYZ_MCP_LEASE_TOKEN": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(smoke.StopRun, "broker_session_environment_missing"):
                runtime.client_factory()

    def test_default_runtime_client_accepts_in_process_broker_session(self) -> None:
        config = self.config("broker-client-direct")
        identity = {
            "platform": "codex",
            "pid": 42001,
            "ppid": 42000,
            "started_at_utc": "2026-07-23T03:00:00Z",
            "session_id": "task9-driver-direct",
            "task_label": "MercedesAMGLF Task 9 evidence",
        }
        runtime = smoke.DefaultRuntime(
            config,
            broker_identity_json=json.dumps(identity, separators=(",", ":")),
            lease_token="direct-test-lease",
        )
        runtime.mcp_client = mock.Mock()
        with mock.patch.dict(
            smoke.os.environ,
            {
                "DAYZ_MCP_CLIENT_ID_JSON": "",
                "DAYZ_MCP_LEASE_TOKEN": "",
            },
            clear=False,
        ):
            client = runtime.client_factory()

        self.assertIs(client, runtime.mcp_client.Client.return_value)
        runtime.mcp_client.Client.assert_called_once_with(
            port=8765,
            key=config.keyfile.read_text(encoding="utf-8").strip(),
            timeout_s=30.0,
            identity=identity,
            lease_token="direct-test-lease",
        )

    def test_default_runtime_rejects_partial_in_process_broker_session(self) -> None:
        with self.assertRaisesRegex(ValueError, "incomplete_broker_session"):
            smoke.DefaultRuntime(
                self.config("broker-client-partial"),
                broker_identity_json="{}",
            )

    def test_absolute_exact_mod_components_are_accepted_for_server_and_client(self) -> None:
        absolute_components = (
            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@MERCEDES_AMGLF",
            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@DayZ_MCP",
        )
        for pid in (101, 202):
            with self.subTest(pid=pid):
                snapshot = _replace_process_mods(
                    _owned_snapshot(), pid, ("@CF", *absolute_components)
                )

                result = smoke.validate_ownership(snapshot)

                self.assertTrue(result.ok, result.reason)
                self.assertEqual("owned", result.reason)

    def test_bare_exact_mod_components_remain_accepted_for_server_and_client(self) -> None:
        result = smoke.validate_ownership(_owned_snapshot())

        self.assertTrue(result.ok)
        self.assertEqual("owned", result.reason)

    def test_absolute_near_match_mod_basenames_are_rejected_for_server_and_client(self) -> None:
        mercedes = (
            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@MERCEDES_AMGLF"
        )
        dayz_mcp = (
            r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\!Workshop\@DayZ_MCP"
        )
        cases = (
            (101, (f"{mercedes}_OLD", dayz_mcp), "server_mod_mismatch"),
            (101, (mercedes, f"{dayz_mcp}_backup"), "server_mod_mismatch"),
            (202, (f"{mercedes}_OLD", dayz_mcp), "client_mod_mismatch"),
            (202, (mercedes, f"{dayz_mcp}_backup"), "client_mod_mismatch"),
        )
        for pid, required_components, expected_reason in cases:
            with self.subTest(pid=pid, components=required_components):
                snapshot = _replace_process_mods(
                    _owned_snapshot(), pid, ("@CF", *required_components)
                )

                result = smoke.validate_ownership(snapshot)

                self.assertFalse(result.ok)
                self.assertEqual(expected_reason, result.reason)

    def test_mod_component_order_and_allowed_extras_remain_accepted(self) -> None:
        snapshot = _replace_process_mods(
            _owned_snapshot(),
            101,
            ("@ServerExtra", "@DayZ_MCP", "@CF", "@MERCEDES_AMGLF"),
        )
        snapshot = _replace_process_mods(
            snapshot,
            202,
            ("@MERCEDES_AMGLF", "@ClientExtra", "@DayZ_MCP", "@CF"),
        )

        result = smoke.validate_ownership(snapshot)

        self.assertTrue(result.ok)
        self.assertEqual("owned", result.reason)

    def test_near_match_arguments_profiles_mod_components_and_daemon_path_are_rejected(self) -> None:
        cases = (
            (101, "-port=2302", "-port=23020"),
            (202, "-port=2302", "-port=23020"),
            (202, "-connect=127.0.0.1", "-connect=127.0.0.10"),
            (101, "_server\\profiles", "_server\\profiles-old"),
            (202, "_client\\profiles", "_client\\profiles-old"),
            (101, "@MERCEDES_AMGLF", "@MERCEDES_AMGLF_OLD"),
            (202, "@DayZ_MCP", "@DayZ_MCP_OLD"),
            (303, "DayZ_MCP_dev\\tools\\.dayz_mcp.key", "DayZ_MCP_dev-old\\tools\\.dayz_mcp.key"),
            (303, "--port 8765", "--port 87650"),
            (303, "-m dayz_mcp", "-m dayz_mcp_old"),
        )
        for pid, old, new in cases:
            with self.subTest(pid=pid, replacement=new):
                snapshot = _replace_process_command(_owned_snapshot(), pid, old, new)
                self.assertFalse(smoke.validate_ownership(snapshot).ok)

    def test_windows_adversarial_backslash_quote_command_line_is_rejected(self) -> None:
        snapshot = _replace_process_command(
            _owned_snapshot(),
            101,
            "DayZ Projects\\MERCEDES_AMGLF_dev",
            'DayZ Projects\\\\""MERCEDES_AMGLF_dev',
        )

        self.assertFalse(smoke.validate_ownership(snapshot).ok)

    def test_unauthorized_dayz_executable_and_process_name_are_rejected(self) -> None:
        owned = _owned_snapshot()
        snapshot = smoke.OwnershipSnapshot(
            processes=tuple(
                _process(
                    process.pid,
                    process.command_line.replace("DayZDiag_x64.exe", "DayZNotReally.exe"),
                    "DayZNotReally.exe",
                )
                if process.pid == 101
                else process
                for process in owned.processes
            ),
            ports=owned.ports,
        )

        self.assertFalse(smoke.validate_ownership(snapshot).ok)

    def test_server_with_connect_switch_is_rejected_as_contradictory_role(self) -> None:
        snapshot = _replace_process_command(
            _owned_snapshot(), 101, "-server", "-server -connect=8.8.8.8"
        )

        self.assertFalse(smoke.validate_ownership(snapshot).ok)

    def test_peer_reconnect_flush_settles_before_readiness_and_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [
            {"ok": False, "error": "peer_reconnect_flush"},
            {"ok": False, "error": "peer_reconnect_flush"},
            {"ok": True, "pos": [1000.0, 50.0, 2000.0]},
        ]
        config = self.config("peer-settles")
        config.ready_timeout = 5.0

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(3, runtime.query_calls)
        self.assertEqual([5.0, 4.0, 3.0], runtime.query_timeouts)
        self.assertEqual([1.0, 1.0], runtime.clock.sleeps[:2])
        self.assertEqual(3.0, runtime.readiness_calls[0]["timeout_s"])
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_initial_player_query_receives_full_remaining_readiness_budget(self) -> None:
        runtime = FakeRuntime()
        config = self.config("full-player-query-budget")
        config.ready_timeout = 180.0

        outcome = smoke.collect(
            config,
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual([180.0], runtime.query_timeouts)
        self.assertEqual(180.0, runtime.readiness_calls[0]["timeout_s"])
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_exact_legacy_blocked_http_409_is_normalized_with_state_preserved(self) -> None:
        runtime = smoke.DefaultRuntime.__new__(smoke.DefaultRuntime)
        runtime.mcp_client = mock.Mock()
        runtime.mcp_client.run_result.side_effect = self.http_error(
            409,
            b'{"error":"version_blocked","state":"legacy_blocked"}',
        )

        result = runtime.query_player_state(object(), timeout_s=7.5)

        self.assertEqual(
            {"ok": False, "error": "version_blocked", "state": "legacy_blocked"},
            result,
        )
        runtime.mcp_client.run_result.assert_called_once_with(
            mock.ANY,
            "query_player_state",
            {},
            timeout_s=7.5,
            peer="server",
        )

    def test_task9_bright_readiness_recovers_without_mutating_upstream_payload(self) -> None:
        runtime = self.task9_default_runtime("readiness-bright")
        path, payload = self.task9_readiness_fixture(runtime)
        original = self.task9_clone_readiness(payload)
        self.assertNotIn("frames", payload["last_burst"])

        result = self.task9_wait_for_readiness(runtime, payload)

        self.assertEqual(original, payload)
        self.assertIsNot(result, payload)
        self.assertIs(result["inworld"], True)
        self.assertEqual(
            {
                "applied": True,
                "reason": "bright_settled_frame_without_menu_red_line",
                "last_path": str(path.resolve()),
                "last_mean": payload["last_mean"],
                "last_nonblack": payload["last_nonblack"],
                "menu_red_line_present": False,
            },
            result["task9_readiness_recovery"],
        )

    def test_task9_client_black_readiness_stays_false(self) -> None:
        runtime = self.task9_default_runtime("readiness-client-black")
        _, payload = self.task9_readiness_fixture(runtime)
        payload["last_burst"]["grabs"][-1]["clientStats"] = {
            "meanBrightness": 0.0,
            "nonBlackRatio": 0.0,
        }

        result = self.task9_wait_for_readiness(runtime, payload)

        self.assertIs(result, payload)
        self.assertIs(result["inworld"], False)
        self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_client_black_collect_stops_before_raycast_and_spawn(self) -> None:
        recovery_runtime = self.task9_default_runtime("readiness-client-black-collect")
        _, payload = self.task9_readiness_fixture(recovery_runtime)
        payload["last_burst"]["grabs"][-1]["clientStats"] = {
            "meanBrightness": 0.0,
            "nonBlackRatio": 0.0,
        }
        recovery_runtime.mcp_client.wait_for_inworld_render.return_value = payload
        runtime = FakeRuntime()
        runtime.wait_for_readiness = recovery_runtime.wait_for_readiness

        outcome = smoke.collect(
            self.config("client-black-collect"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(payload, outcome.payload["readiness"])
        self.assertEqual([], runtime.raycast_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_task9_real_client_black_stays_false_despite_live_metadata(self) -> None:
        runtime = self.task9_default_runtime("readiness-real-client-black")
        path, payload = self.task9_real_client_black_fixture(runtime)
        selected_grab = payload["last_burst"]["grabs"][-1]
        image = runtime.mcp_capture.load_rgb(str(path))
        client_image = image.crop((80, 0, 200, 100))
        try:
            whole_stats = runtime.mcp_capture.image_stats_from_image(image)
            client_stats = runtime.mcp_capture.image_stats_from_image(client_image)
        finally:
            client_image.close()
            image.close()
        self.assertEqual(102.0, whole_stats["meanBrightness"])
        self.assertEqual(0.4, whole_stats["nonBlackRatio"])
        self.assertEqual(0.0, client_stats["meanBrightness"])
        self.assertEqual(0.0, client_stats["nonBlackRatio"])
        self.assertEqual(
            {"meanBrightness": 114.0, "nonBlackRatio": 1.0},
            selected_grab["clientStats"],
        )
        self.assertEqual(_sha256(path), selected_grab["sha256"])

        result = self.task9_wait_for_readiness(runtime, payload)

        self.assertIs(result, payload)
        self.assertIs(result["inworld"], False)
        self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_real_client_black_collect_stops_before_raycast_and_spawn(self) -> None:
        recovery_runtime = self.task9_default_runtime("readiness-real-client-black-collect")
        _, payload = self.task9_real_client_black_fixture(recovery_runtime)
        recovery_runtime.mcp_client.wait_for_inworld_render.return_value = payload
        runtime = FakeRuntime()
        runtime.wait_for_readiness = recovery_runtime.wait_for_readiness

        outcome = smoke.collect(
            self.config("real-client-black-collect"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(payload, outcome.payload["readiness"])
        self.assertEqual([], runtime.raycast_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_task9_readiness_recovery_requires_valid_selected_client_evidence(self) -> None:
        runtime = self.task9_default_runtime("readiness-client-evidence")
        _, base = self.task9_readiness_fixture(runtime)

        cases: list[tuple[str, dict[str, object]]] = []

        missing_client = self.task9_clone_readiness(base)
        del missing_client["last_burst"]["grabs"][-1]["client"]
        cases.append(("missing-client", missing_client))

        for name, value in (
            ("client-not-dict", [0, 0, 200, 100]),
            ("client-missing-height", {"left": 0, "top": 0, "width": 200}),
            (
                "client-bool-coordinate",
                {"left": False, "top": 0, "width": 200, "height": 100},
            ),
            (
                "client-outside-image",
                {"left": 1, "top": 0, "width": 200, "height": 100},
            ),
        ):
            payload = self.task9_clone_readiness(base)
            payload["last_burst"]["grabs"][-1]["client"] = value
            cases.append((name, payload))

        missing_stats = self.task9_clone_readiness(base)
        del missing_stats["last_burst"]["grabs"][-1]["clientStats"]
        cases.append(("missing-client-stats", missing_stats))

        for name, value in (
            ("client-stats-not-dict", [114.0, 1.0]),
            ("client-stats-missing-ratio", {"meanBrightness": 114.0}),
            (
                "client-stats-bool",
                {"meanBrightness": True, "nonBlackRatio": 1.0},
            ),
            (
                "client-stats-nan",
                {"meanBrightness": float("nan"), "nonBlackRatio": 1.0},
            ),
            (
                "client-stats-infinite",
                {"meanBrightness": 114.0, "nonBlackRatio": float("inf")},
            ),
        ):
            payload = self.task9_clone_readiness(base)
            payload["last_burst"]["grabs"][-1]["clientStats"] = value
            cases.append((name, payload))

        for name, payload in cases:
            with self.subTest(case=name):
                result = self.task9_wait_for_readiness(runtime, payload)
                self.assertIs(result, payload)
                self.assertIs(result["inworld"], False)
                self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_readiness_recovery_binds_last_successful_grab_path_and_hash(self) -> None:
        runtime = self.task9_default_runtime("readiness-selected-grab")
        _, base = self.task9_readiness_fixture(runtime)

        trailing_failure = self.task9_clone_readiness(base)
        trailing_failure["last_burst"]["grabs"].append(
            {"ok": False, "error": "capture_failed"}
        )
        recovered = self.task9_wait_for_readiness(runtime, trailing_failure)
        self.assertIs(recovered["inworld"], True)

        cases: list[tuple[str, dict[str, object]]] = []

        missing_grabs = self.task9_clone_readiness(base)
        del missing_grabs["last_burst"]["grabs"]
        cases.append(("missing-grabs", missing_grabs))

        wrong_hash = self.task9_clone_readiness(base)
        wrong_hash["last_burst"]["grabs"][-1]["sha256"] = "0" * 64
        cases.append(("selected-grab-hash-mismatch", wrong_hash))

        earlier_live_last_black = self.task9_clone_readiness(base)
        live_grab = earlier_live_last_black["last_burst"]["grabs"][-1]
        black_grab = self.task9_clone_readiness(base)["last_burst"]["grabs"][-1]
        black_grab["clientStats"] = {
            "meanBrightness": 0.0,
            "nonBlackRatio": 0.0,
        }
        earlier_live_last_black["last_burst"]["grabs"] = [live_grab, black_grab]
        cases.append(("last-successful-grab-client-black", earlier_live_last_black))

        for name, payload in cases:
            with self.subTest(case=name):
                result = self.task9_wait_for_readiness(runtime, payload)
                self.assertIs(result, payload)
                self.assertIs(result["inworld"], False)
                self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_menu_red_line_preserves_false_for_native_and_scaled_frames(self) -> None:
        runtime = self.task9_default_runtime("readiness-menu")
        for size in ((1942, 1136), (971, 568)):
            with self.subTest(size=size):
                _, payload = self.task9_readiness_fixture(
                    runtime,
                    size=size,
                    red_run_global_fraction=400 / 1942,
                )

                result = self.task9_wait_for_readiness(runtime, payload)

                self.assertIs(result, payload)
                self.assertIs(result["inworld"], False)
                self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_short_red_near_match_does_not_veto_bright_recovery(self) -> None:
        runtime = self.task9_default_runtime("readiness-short-red")
        _, payload = self.task9_readiness_fixture(
            runtime,
            size=(1942, 1136),
            red_run_global_fraction=24 / 1942,
        )

        result = self.task9_wait_for_readiness(runtime, payload)

        self.assertIs(result["inworld"], True)
        self.assertIs(result["task9_readiness_recovery"]["menu_red_line_present"], False)

    def test_task9_continue_overlay_exact_signature_yields_owned_click_point(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-signature")
        path, capture = self.task9_continue_overlay_fixture(runtime)

        result = smoke._inspect_task9_continue_overlay(
            path, capture, 202, runtime.mcp_capture
        )

        self.assertIs(result["ok"], True)
        self.assertIs(result["detected"], True)
        self.assertEqual("continue_overlay_exact", result["reason"])
        self.assertEqual([1731, 999], result["click_screen"])
        self.assertEqual(capture["window"], result["window"])
        self.assertEqual(_sha256(path), result["sha256"])
        self.assertGreaterEqual(result["button_dark_ratio"], 0.80)
        self.assertGreaterEqual(result["label_white_ratio"], 0.08)

    def test_task9_continue_overlay_accepts_proven_1280x720_client_geometry(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-1280x720")
        path, capture = self.task9_continue_overlay_fixture(runtime)
        capture["client"]["width"] = 1280
        capture["client"]["height"] = 720

        result = smoke._inspect_task9_continue_overlay(
            path, capture, 202, runtime.mcp_capture
        )

        self.assertIs(result["ok"], True)
        self.assertIs(result["detected"], True)
        self.assertEqual("continue_overlay_exact", result["reason"])

    def test_task9_continue_overlay_absent_is_read_only_and_partial_signature_fails_closed(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-controls")
        path, base = self.task9_continue_overlay_fixture(runtime)

        with Image.new("RGB", (1942, 1136), color=(96, 96, 96)) as image:
            image.save(path, format="PNG")
        absent = self.task9_clone_readiness(base)
        absent["sha256"] = _sha256(path)
        absent_result = smoke._inspect_task9_continue_overlay(
            path, absent, 202, runtime.mcp_capture
        )
        self.assertEqual(
            {"ok": True, "detected": False, "reason": "continue_overlay_absent"},
            absent_result,
        )

        with Image.new("RGB", (1942, 1136), color=(96, 96, 96)) as image:
            image.paste((45, 48, 52), (1428, 775, 1834, 997))
            image.paste((220, 0, 0), (1431, 902, 1831, 904))
            image.save(path, format="PNG")
        partial = self.task9_clone_readiness(base)
        partial["sha256"] = _sha256(path)
        partial_result = smoke._inspect_task9_continue_overlay(
            path, partial, 202, runtime.mcp_capture
        )
        self.assertIs(partial_result["ok"], False)
        self.assertIs(partial_result["detected"], True)
        self.assertEqual("continue_overlay_signature_partial", partial_result["reason"])

    def test_task9_continue_overlay_metadata_near_matches_fail_closed(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-metadata")
        path, base = self.task9_continue_overlay_fixture(runtime)
        cases: list[tuple[str, dict[str, object]]] = []
        for name, mutate in (
            ("wrong-method", lambda payload: payload.__setitem__("method", "auto")),
            ("wrong-pid", lambda payload: payload["window"].__setitem__("pid", 303)),
            ("wrong-width", lambda payload: payload["client"].__setitem__("width", 1919)),
            ("wrong-hash", lambda payload: payload.__setitem__("sha256", "0" * 64)),
            (
                "invalid-stats-schema",
                lambda payload: payload.__setitem__(
                    "clientStats",
                    {"meanBrightness": True, "nonBlackRatio": 0.0},
                ),
            ),
        ):
            payload = self.task9_clone_readiness(base)
            mutate(payload)
            cases.append((name, payload))

        for name, payload in cases:
            with self.subTest(case=name):
                result = smoke._inspect_task9_continue_overlay(
                    path, payload, 202, runtime.mcp_capture
                )
                self.assertIs(result["ok"], False)
                self.assertIs(result["detected"], False)
                if name == "invalid-stats-schema":
                    self.assertNotIn("client_pid", result)
                    self.assertNotIn("window", result)

    def test_default_runtime_inspects_foreground_capture_and_sends_only_prevalidated_click(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-runtime")
        fixture_path, capture = self.task9_continue_overlay_fixture(runtime)
        clock = FakeClock()
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(fixture_path.read_bytes())
            payload = self.task9_clone_readiness(capture)
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ) as grab:
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["detected"], True)
        self.assertEqual(1, inspection["capture_attempts"])
        self.assertEqual(0.0, inspection["capture_elapsed_s"])
        self.assertEqual([], clock.sleeps)
        grab.assert_called_once_with(
            str(runtime.config.evidence_dir / "task9-continue-preaction.png"),
            process_name="DayZDiag_x64",
            method="foreground",
            client_pid=202,
            cmdline_match="DayZDiag_x64.exe",
        )

        send_result = {
            "ok": True,
            "attempted": True,
            "reason": "continue_clicked",
            "foreground_pid": 202,
            "events_sent": 2,
        }
        with mock.patch.object(
            smoke, "_send_task9_owned_click", return_value=send_result
        ) as sender:
            resumed = runtime.resume_frontend_overlay(202, inspection)

        self.assertEqual(send_result, resumed)
        sender.assert_called_once_with(202, 1731, 999, _task9_owned_window())

    def test_default_runtime_recaptures_only_black_client_until_exact_overlay(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-black-then-exact")
        fixture_path, capture = self.task9_continue_overlay_fixture(runtime)
        black = self.task9_clone_readiness(capture)
        black["clientStats"] = {"meanBrightness": 0.0, "nonBlackRatio": 0.0}
        clock = FakeClock()
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]
        destinations: list[pathlib.Path] = []

        foreground_calls = 0

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            nonlocal foreground_calls
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            destinations.append(output)
            if kwargs["method"] == "printwindow":
                with Image.new("RGB", (1942, 1136), color=(0, 0, 0)) as image:
                    image.save(output, format="PNG")
                payload = self.task9_clone_readiness(black)
                payload["method"] = "printwindow"
            else:
                foreground_calls += 1
                if foreground_calls == 1:
                    with Image.new("RGB", (1942, 1136), color=(0, 0, 0)) as image:
                        image.save(output, format="PNG")
                    payload = self.task9_clone_readiness(black)
                else:
                    output.write_bytes(fixture_path.read_bytes())
                    payload = self.task9_clone_readiness(capture)
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ):
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["ok"], True)
        self.assertIs(inspection["detected"], True)
        self.assertEqual("continue_overlay_exact", inspection["reason"])
        self.assertEqual(2, inspection["capture_attempts"])
        self.assertEqual(1, inspection["printwindow_attempts"])
        self.assertEqual("foreground", inspection["capture_channel"])
        self.assertEqual(2.0, inspection["capture_elapsed_s"])
        self.assertEqual([2.0], clock.sleeps)
        self.assertEqual(
            [
                runtime.config.evidence_dir / "task9-continue-preaction.png",
                runtime.config.evidence_dir / "task9-continue-preaction-printwindow.png",
                runtime.config.evidence_dir / "task9-continue-preaction-retry-01.png",
            ],
            destinations,
        )

    def test_default_runtime_accepts_printwindow_only_when_overlay_is_absent(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-printwindow-absent")
        _, capture = self.task9_continue_overlay_fixture(runtime)
        black = self.task9_clone_readiness(capture)
        black["clientStats"] = {"meanBrightness": 0.0, "nonBlackRatio": 0.0}
        clock = FakeClock()
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            if kwargs["method"] == "foreground":
                with Image.new("RGB", (1942, 1136), color=(0, 0, 0)) as image:
                    image.save(output, format="PNG")
                payload = self.task9_clone_readiness(black)
            else:
                with Image.new("RGB", (1942, 1136), color=(80, 80, 80)) as image:
                    image.save(output, format="PNG")
                payload = self.task9_clone_readiness(capture)
                payload["method"] = "printwindow"
                payload["clientStats"] = {
                    "meanBrightness": 80.0,
                    "nonBlackRatio": 1.0,
                }
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ) as grab:
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["ok"], True)
        self.assertIs(inspection["detected"], False)
        self.assertEqual("continue_overlay_absent", inspection["reason"])
        self.assertEqual("printwindow", inspection["capture_channel"])
        self.assertEqual(1, inspection["capture_attempts"])
        self.assertEqual(1, inspection["printwindow_attempts"])
        self.assertEqual([], clock.sleeps)
        self.assertEqual(2, grab.call_count)

    def test_default_runtime_never_clicks_printwindow_only_overlay(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-printwindow-exact")
        fixture_path, capture = self.task9_continue_overlay_fixture(runtime)
        black = self.task9_clone_readiness(capture)
        black["clientStats"] = {"meanBrightness": 0.0, "nonBlackRatio": 0.0}

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            if kwargs["method"] == "foreground":
                with Image.new("RGB", (1942, 1136), color=(0, 0, 0)) as image:
                    image.save(output, format="PNG")
                payload = self.task9_clone_readiness(black)
            else:
                output.write_bytes(fixture_path.read_bytes())
                payload = self.task9_clone_readiness(capture)
                payload["method"] = "printwindow"
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ):
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["ok"], False)
        self.assertIs(inspection["detected"], True)
        self.assertEqual("continue_overlay_foreground_unverified", inspection["reason"])
        self.assertEqual("printwindow", inspection["capture_channel"])
        self.assertEqual(1, inspection["capture_attempts"])
        self.assertEqual(1, inspection["printwindow_attempts"])
        self.assertNotIn("click_screen", inspection)
        with mock.patch.object(smoke, "_send_task9_owned_click") as sender:
            resumed = runtime.resume_frontend_overlay(202, inspection)
        self.assertIs(resumed["attempted"], False)
        sender.assert_not_called()

    def test_default_runtime_black_client_timeout_is_bounded_and_fail_closed(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-black-timeout")
        _, capture = self.task9_continue_overlay_fixture(runtime)
        black = self.task9_clone_readiness(capture)
        black["clientStats"] = {"meanBrightness": 0.0, "nonBlackRatio": 0.0}
        clock = FakeClock()
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]
        destinations: list[pathlib.Path] = []

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            destinations.append(output)
            with Image.new("RGB", (1942, 1136), color=(0, 0, 0)) as image:
                image.save(output, format="PNG")
            payload = self.task9_clone_readiness(black)
            payload["method"] = kwargs["method"]
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            smoke, "TASK9_CONTINUE_CAPTURE_TIMEOUT_S", 4.0, create=True
        ), mock.patch.object(
            smoke, "TASK9_CONTINUE_CAPTURE_INTERVAL_S", 2.0, create=True
        ), mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ):
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["ok"], False)
        self.assertEqual("continue_client_stats_invalid", inspection["reason"])
        self.assertEqual(3, inspection["capture_attempts"])
        self.assertEqual(3, inspection["printwindow_attempts"])
        self.assertEqual(4.0, inspection["capture_elapsed_s"])
        self.assertEqual([2.0, 2.0], clock.sleeps)
        self.assertEqual(6, len(destinations))
        self.assertEqual(len(destinations), len(set(destinations)))
        self.assertEqual(202, inspection["client_pid"])
        self.assertEqual(_task9_owned_window(), inspection["window"])

    def test_default_runtime_presentation_recovery_accepts_only_authoritative_black_inspection(self) -> None:
        runtime = self.task9_default_runtime("focus-only-contract")
        expected = {
            "ok": True,
            "attempted": True,
            "reason": "foreground_activated",
            "foreground_pid": 202,
        }
        with mock.patch.object(
            smoke, "_activate_task9_owned_window", return_value=expected
        ) as activate:
            result = runtime.activate_frontend_window(202, _task9_black_inspection())

        self.assertEqual(expected, result)
        activate.assert_called_once_with(
            202, _task9_owned_window(), toggle_presentation=True
        )

        near_matches = (
            {**_task9_black_inspection(), "reason": "continue_overlay_absent"},
            {**_task9_black_inspection(), "client_pid": 303},
            {
                **_task9_black_inspection(),
                "window": {**_task9_owned_window(), "unexpected": 1},
            },
        )
        for inspection in near_matches:
            with self.subTest(inspection=inspection), mock.patch.object(
                smoke, "_activate_task9_owned_window"
            ) as rejected_activate:
                rejected = runtime.activate_frontend_window(202, inspection)
            self.assertIs(rejected["ok"], False)
            self.assertIs(rejected["attempted"], False)
            self.assertEqual("black_inspection_not_authoritative", rejected["reason"])
            rejected_activate.assert_not_called()

    def test_default_runtime_nonblack_metadata_failure_does_not_retry(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-metadata-no-retry")
        fixture_path, capture = self.task9_continue_overlay_fixture(runtime)
        invalid = self.task9_clone_readiness(capture)
        invalid["client"]["width"] = 1919
        clock = FakeClock()
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]

        def capture_side_effect(destination: str, **kwargs: object) -> dict[str, object]:
            output = pathlib.Path(destination)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(fixture_path.read_bytes())
            payload = self.task9_clone_readiness(invalid)
            payload["sha256"] = _sha256(output)
            return payload

        with mock.patch.object(
            runtime.mcp_capture,
            "grab_window_to_file",
            side_effect=capture_side_effect,
        ) as grab:
            inspection = runtime.inspect_frontend_overlay(202, "DayZDiag_x64.exe")

        self.assertIs(inspection["ok"], False)
        self.assertEqual("continue_client_geometry_invalid", inspection["reason"])
        self.assertEqual(1, inspection["capture_attempts"])
        self.assertEqual(0.0, inspection["capture_elapsed_s"])
        self.assertEqual([], clock.sleeps)
        self.assertEqual(1, grab.call_count)

    def test_owned_window_operations_scope_and_restore_per_monitor_dpi(self) -> None:
        per_monitor_context = int(smoke.ctypes.c_void_p(-3).value)
        cases = (
            (
                "activate",
                lambda: smoke._activate_task9_owned_window(
                    202, _task9_owned_window()
                ),
            ),
            (
                "click",
                lambda: smoke._send_task9_owned_click(
                    202, 1731, 999, _task9_owned_window()
                ),
            ),
        )

        for name, operation in cases:
            user32 = FakeUser32([0x3030])
            with self.subTest(case=name), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ):
                result = operation()

            self.assertIs(result["ok"], True)
            self.assertEqual(
                [per_monitor_context, user32.dpi_enter_result],
                user32.dpi_context_calls,
            )
            self.assertLess(
                user32.events.index(("dpi", per_monitor_context)),
                user32.events.index(("enum", 0)),
            )
            self.assertLess(
                user32.events.index(("enum", 0)),
                user32.events.index(("dpi", user32.dpi_enter_result)),
            )

    def test_owned_window_operations_fail_closed_when_dpi_context_unavailable(self) -> None:
        cases = (
            (
                "activate",
                lambda: smoke._activate_task9_owned_window(
                    202, _task9_owned_window()
                ),
            ),
            (
                "click",
                lambda: smoke._send_task9_owned_click(
                    202, 1731, 999, _task9_owned_window()
                ),
            ),
        )

        for name, operation in cases:
            user32 = FakeUser32([0x3030], dpi_enter_result=0)
            with self.subTest(case=name), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ):
                result = operation()

            self.assertEqual(
                {
                    "ok": False,
                    "attempted": False,
                    "reason": "dpi_context_unavailable",
                },
                result,
            )
            self.assertNotIn(("enum", 0), user32.events)
            self.assertEqual([], user32.set_foreground_calls)
            self.assertEqual([], user32.cursor_moves)
            self.assertEqual([], user32.sent_flags)

    def test_owned_window_operations_report_dpi_restore_failure(self) -> None:
        cases = (
            (
                "activate",
                lambda: smoke._activate_task9_owned_window(
                    202, _task9_owned_window()
                ),
                "foreground_repositioned",
            ),
            (
                "click",
                lambda: smoke._send_task9_owned_click(
                    202, 1731, 999, _task9_owned_window()
                ),
                "continue_clicked",
            ),
        )

        for name, operation, operation_reason in cases:
            user32 = FakeUser32([0x3030], dpi_restore_result=0)
            with self.subTest(case=name), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ):
                result = operation()

            self.assertEqual(
                {
                    "ok": False,
                    "attempted": True,
                    "reason": "dpi_context_restore_failed",
                    "operation_reason": operation_reason,
                },
                result,
            )

    def test_task9_dpi_context_restores_before_propagating_operation_exception(self) -> None:
        user32 = FakeUser32()

        def fail() -> dict[str, object]:
            raise RuntimeError("dpi-operation-boom")

        with mock.patch.object(
            smoke.ctypes, "WinDLL", return_value=user32
        ), self.assertRaisesRegex(RuntimeError, "dpi-operation-boom"):
            smoke._run_task9_per_monitor_dpi(fail)

        self.assertEqual(
            [int(smoke.ctypes.c_void_p(-3).value), user32.dpi_enter_result],
            user32.dpi_context_calls,
        )

    def test_owned_focus_activation_reacquires_exact_window_without_input(self) -> None:
        user32 = FakeUser32([0x3030])

        with mock.patch.object(
            smoke.ctypes, "WinDLL", return_value=user32
        ), mock.patch.object(smoke.time, "sleep") as sleep:
            result = smoke._activate_task9_owned_window(202, _task9_owned_window())

        self.assertEqual(
            {
                "ok": True,
                "attempted": True,
                "reason": "foreground_repositioned",
                "foreground_pid": 202,
                "original_window_rect": [100, 50, 1942, 1136],
                "activated_window_rect": [0, 0, 1942, 1136],
            },
            result,
        )
        self.assertEqual([(0x2020, 9)], user32.show_window_commands)
        self.assertEqual(
            [
                (0x2020, 0, 0, 0, 1941, 1135, 0x44),
                (0x2020, 0, 0, 0, 1942, 1136, 0x44),
            ],
            user32.set_window_pos_calls,
        )
        self.assertEqual([0x2020], user32.set_foreground_calls)
        self.assertEqual([(777, 1303, True), (777, 1303, False)], user32.attach_calls)
        self.assertEqual([], user32.cursor_moves)
        self.assertEqual([], user32.sent_flags)
        sleep.assert_not_called()

    def test_owned_black_frontend_recovery_emits_one_exact_alt_enter_chord(self) -> None:
        user32 = FakeUser32([0x3030], send_result=4)

        with mock.patch.object(
            smoke.ctypes, "WinDLL", return_value=user32
        ), mock.patch.object(smoke.time, "sleep") as sleep:
            result = smoke._activate_task9_owned_window(
                202, _task9_owned_window(), toggle_presentation=True
            )

        self.assertIs(result["ok"], True)
        self.assertEqual(
            "foreground_repositioned_and_presentation_toggled", result["reason"]
        )
        self.assertEqual(
            {
                "events_sent": 4,
                "keys": ["ALT_DOWN", "ENTER_DOWN", "ENTER_UP", "ALT_UP"],
            },
            result["presentation_toggle"],
        )
        self.assertEqual(
            [(0x12, 0), (0x0D, 0), (0x0D, 0x0002), (0x12, 0x0002)],
            user32.sent_keyboard,
        )
        self.assertEqual(
            [40 if smoke.ctypes.sizeof(smoke.ctypes.c_void_p) == 8 else 28],
            user32.sent_input_sizes,
        )
        self.assertEqual([], user32.sent_flags)
        sleep.assert_called_once_with(0.15)

    def test_owned_black_frontend_partial_chord_releases_enter_and_alt(self) -> None:
        user32 = FakeUser32([0x3030], send_result=2)

        with mock.patch.object(
            smoke.ctypes, "WinDLL", return_value=user32
        ), mock.patch.object(smoke.time, "sleep") as sleep:
            result = smoke._activate_task9_owned_window(
                202, _task9_owned_window(), toggle_presentation=True
            )

        self.assertIs(result["ok"], False)
        self.assertIs(result["attempted"], True)
        self.assertEqual("presentation_toggle_input_partial", result["reason"])
        self.assertEqual(2, result["events_sent"])
        self.assertEqual(2, result["release_events_sent"])
        self.assertEqual(
            [
                [(0x12, 0), (0x0D, 0), (0x0D, 0x0002), (0x12, 0x0002)],
                [(0x0D, 0x0002), (0x12, 0x0002)],
            ],
            user32.sent_keyboard_batches,
        )
        self.assertEqual(
            [40 if smoke.ctypes.sizeof(smoke.ctypes.c_void_p) == 8 else 28] * 2,
            user32.sent_input_sizes,
        )
        sleep.assert_not_called()

    def test_owned_focus_activation_observes_transient_null_without_reactivation(self) -> None:
        user32 = FakeUser32([0x3030, 0, 0x2020])

        with mock.patch.object(
            smoke.ctypes, "WinDLL", return_value=user32
        ), mock.patch.object(smoke.time, "sleep") as sleep:
            result = smoke._activate_task9_owned_window(202, _task9_owned_window())

        self.assertEqual(
            {
                "ok": True,
                "attempted": True,
                "reason": "foreground_repositioned",
                "foreground_pid": 202,
                "original_window_rect": [100, 50, 1942, 1136],
                "activated_window_rect": [0, 0, 1942, 1136],
            },
            result,
        )
        self.assertEqual([(0x2020, 9)], user32.show_window_commands)
        self.assertEqual(
            [
                (0x2020, 0, 0, 0, 1941, 1135, 0x44),
                (0x2020, 0, 0, 0, 1942, 1136, 0x44),
            ],
            user32.set_window_pos_calls,
        )
        self.assertEqual([0x2020], user32.set_foreground_calls)
        self.assertEqual([], user32.cursor_moves)
        self.assertEqual([], user32.sent_flags)
        sleep.assert_called_once_with(0.05)

    def test_owned_focus_activation_confirms_post_reposition_geometry(self) -> None:
        user32 = FakeUser32([0x3030])
        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32):
            result = smoke._activate_task9_owned_window(202, _task9_owned_window())

        self.assertIs(result["ok"], True)
        self.assertEqual("foreground_repositioned", result["reason"])
        self.assertEqual([100, 50, 1942, 1136], result["original_window_rect"])
        self.assertEqual([0, 0, 1942, 1136], result["activated_window_rect"])
        self.assertEqual([(0x2020, 9)], user32.show_window_commands)
        self.assertEqual(
            [
                (0x2020, 0, 0, 0, 1941, 1135, 0x44),
                (0x2020, 0, 0, 0, 1942, 1136, 0x44),
            ],
            user32.set_window_pos_calls,
        )

    def test_owned_focus_activation_near_matches_fail_closed_without_input(self) -> None:
        duplicate = {
            **FakeUser32().windows,
            0x2121: {
                "pid": 202,
                "class": "DayZ",
                "title": "DayZ",
                "rect": (100, 50, 2042, 1186),
                "visible": True,
            },
        }
        moved = FakeUser32().windows
        moved[0x2020] = {**moved[0x2020], "rect": (101, 50, 2043, 1186)}
        cases = (
            (FakeUser32(windows=duplicate), "owned_window_match_ambiguous"),
            (FakeUser32(windows=moved), "owned_window_match_missing"),
            (FakeUser32(set_foreground_result=0), "foreground_activation_failed"),
        )

        for user32, reason in cases:
            with self.subTest(reason=reason), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ), mock.patch.object(smoke.time, "sleep") as sleep:
                result = smoke._activate_task9_owned_window(
                    202, _task9_owned_window()
                )

            self.assertEqual(reason, result["reason"])
            self.assertIs(
                result["attempted"], reason == "foreground_activation_failed"
            )
            self.assertEqual([], user32.cursor_moves)
            self.assertEqual([], user32.sent_flags)
            sleep.assert_not_called()

    def test_owned_focus_activation_detach_or_post_activation_drift_fails_closed(self) -> None:
        detach_failure = FakeUser32([0x3030])

        def attach_then_fail_detach(source: int, target: int, attach: int) -> int:
            detach_failure.attach_calls.append((source, target, bool(attach)))
            return int(bool(attach))

        detach_failure.AttachThreadInput = FakeWin32Call(attach_then_fail_detach)
        foreground_drift = FakeUser32([0x3030, 0x3030])
        cases = (
            (detach_failure, "foreground_detach_failed"),
            (foreground_drift, "foreground_activation_not_confirmed"),
        )

        for user32, reason in cases:
            with self.subTest(reason=reason), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ), mock.patch.object(smoke.time, "sleep") as sleep:
                result = smoke._activate_task9_owned_window(
                    202, _task9_owned_window()
                )

            self.assertEqual(reason, result["reason"])
            self.assertIs(result["attempted"], True)
            self.assertEqual([], user32.cursor_moves)
            self.assertEqual([], user32.sent_flags)
            sleep.assert_not_called()

    def test_owned_click_reacquires_exact_window_and_emits_exact_down_up_pair(self) -> None:
        user32 = FakeUser32([0x3030])

        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual(
            {
                "ok": True,
                "attempted": True,
                "reason": "continue_clicked",
                "foreground_pid": 202,
                "events_sent": 2,
                "cursor_restored": True,
            },
            result,
        )
        self.assertEqual([(1731, 999), (50, 60)], user32.cursor_moves)
        self.assertEqual([0x0002, 0x0004], user32.sent_flags)
        self.assertEqual([0x2020], user32.set_foreground_calls)
        self.assertEqual([(777, 1303, True), (777, 1303, False)], user32.attach_calls)
        sleep.assert_called_once_with(0.15)

    def test_owned_click_observes_transient_null_then_emits_one_exact_pair(self) -> None:
        user32 = FakeUser32([0x3030, 0, 0x2020, 0x2020])

        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual(
            {
                "ok": True,
                "attempted": True,
                "reason": "continue_clicked",
                "foreground_pid": 202,
                "events_sent": 2,
                "cursor_restored": True,
            },
            result,
        )
        self.assertEqual([0x2020], user32.set_foreground_calls)
        self.assertEqual([(1731, 999), (50, 60)], user32.cursor_moves)
        self.assertEqual([0x0002, 0x0004], user32.sent_flags)
        self.assertEqual([mock.call(0.05), mock.call(0.15)], sleep.call_args_list)

    def test_owned_click_exhausts_null_foreground_budget_before_input(self) -> None:
        user32 = FakeUser32([0x3030, *([0] * 10)])

        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual("foreground_activation_not_confirmed", result["reason"])
        self.assertIs(result["attempted"], False)
        self.assertEqual(0, result["foreground_pid"])
        self.assertEqual([0x2020], user32.set_foreground_calls)
        self.assertEqual([], user32.cursor_moves)
        self.assertEqual([], user32.sent_flags)
        self.assertEqual([mock.call(0.05)] * 9, sleep.call_args_list)

    def test_owned_click_geometry_drift_during_null_confirmation_fails_immediately(self) -> None:
        user32 = FakeUser32([0x3030, 0, 0x2020])
        rect_calls = 0

        def drifting_rect(hwnd: int, pointer: object) -> int:
            nonlocal rect_calls
            if hwnd == 0x2020:
                rect_calls += 1
                if rect_calls == 2:
                    user32.windows[0x2020]["rect"] = (101, 50, 2043, 1186)
            return user32._window_rect(hwnd, pointer)

        user32.GetWindowRect = FakeWin32Call(drifting_rect)
        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual("foreground_activation_not_confirmed", result["reason"])
        self.assertIs(result["attempted"], False)
        self.assertEqual(2, rect_calls)
        self.assertEqual([], user32.cursor_moves)
        self.assertEqual([], user32.sent_flags)
        sleep.assert_not_called()

    def test_owned_click_foreground_drift_fails_before_send_and_restores_cursor(self) -> None:
        user32 = FakeUser32([0x3030, 0x2020, 0x3030])

        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual("foreground_window_changed_before_input", result["reason"])
        self.assertIs(result["attempted"], False)
        self.assertEqual([], user32.sent_flags)
        self.assertEqual([(1731, 999), (50, 60)], user32.cursor_moves)
        sleep.assert_not_called()

    def test_owned_click_geometry_drift_fails_before_send_and_restores_cursor(self) -> None:
        user32 = FakeUser32([0x3030])
        rect_calls = 0

        def drifting_rect(hwnd: int, pointer: object) -> int:
            nonlocal rect_calls
            if hwnd == 0x2020:
                rect_calls += 1
                if rect_calls == 3:
                    user32.windows[0x2020]["rect"] = (101, 50, 2043, 1186)
            return user32._window_rect(hwnd, pointer)

        user32.GetWindowRect = FakeWin32Call(drifting_rect)
        with mock.patch.object(smoke.ctypes, "WinDLL", return_value=user32), mock.patch.object(
            smoke.time, "sleep"
        ) as sleep:
            result = smoke._send_task9_owned_click(
                202, 1731, 999, _task9_owned_window()
            )

        self.assertEqual("owned_window_geometry_changed_before_input", result["reason"])
        self.assertIs(result["attempted"], False)
        self.assertEqual([], user32.sent_flags)
        self.assertEqual([(1731, 999), (50, 60)], user32.cursor_moves)
        sleep.assert_not_called()

    def test_owned_click_window_match_and_activation_near_matches_fail_before_input(self) -> None:
        duplicate = {
            **FakeUser32().windows,
            0x2121: {
                "pid": 202,
                "class": "DayZ",
                "title": "DayZ",
                "rect": (100, 50, 2042, 1186),
                "visible": True,
            },
        }
        moved = FakeUser32().windows
        moved[0x2020] = {**moved[0x2020], "rect": (101, 50, 2043, 1186)}
        cases = (
            ("ambiguous", FakeUser32(windows=duplicate), "owned_window_match_ambiguous"),
            ("geometry", FakeUser32(windows=moved), "owned_window_match_missing"),
            (
                "activation",
                FakeUser32(set_foreground_result=0),
                "foreground_activation_failed",
            ),
        )

        for name, user32, reason in cases:
            with self.subTest(case=name), mock.patch.object(
                smoke.ctypes, "WinDLL", return_value=user32
            ), mock.patch.object(smoke.time, "sleep") as sleep:
                result = smoke._send_task9_owned_click(
                    202, 1731, 999, _task9_owned_window()
                )

            self.assertEqual(reason, result["reason"])
            self.assertIs(result["attempted"], False)
            self.assertEqual([], user32.sent_flags)
            self.assertEqual([], user32.cursor_moves)
            sleep.assert_not_called()

    def test_default_runtime_rejects_non_authoritative_window_contract(self) -> None:
        runtime = self.task9_default_runtime("continue-overlay-window-contract")
        inspection = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "client_pid": 202,
            "click_screen": [1731, 999],
            "window": {**_task9_owned_window(), "unexpected": 1},
        }

        with mock.patch.object(smoke, "_send_task9_owned_click") as sender:
            result = runtime.resume_frontend_overlay(202, inspection)

        self.assertEqual("continue_inspection_not_authoritative", result["reason"])
        self.assertIs(result["attempted"], False)
        sender.assert_not_called()

    def test_task9_readiness_recovery_fails_closed_for_payload_near_matches(self) -> None:
        runtime = self.task9_default_runtime("readiness-near-matches")
        _, base = self.task9_readiness_fixture(runtime)

        cases: list[tuple[str, object]] = []
        for field in (
            "inworld",
            "elapsed_s",
            "inter_sample_deltas",
            "last_mean",
            "last_nonblack",
            "last_burst",
            "thresholds",
        ):
            payload = self.task9_clone_readiness(base)
            del payload[field]
            cases.append((f"missing-{field}", payload))

        for field in (
            "min_settle_s",
            "stability_max",
            "stable_samples",
            "menu_max_mean",
            "nonblack_min",
        ):
            payload = self.task9_clone_readiness(base)
            del payload["thresholds"][field]
            cases.append((f"missing-threshold-{field}", payload))

        for field in ("ok", "last_path"):
            payload = self.task9_clone_readiness(base)
            del payload["last_burst"][field]
            cases.append((f"missing-burst-{field}", payload))

        scalar_mutations = (
            ("elapsed-bool", "elapsed_s", True),
            ("elapsed-string", "elapsed_s", "90.0"),
            ("elapsed-nan", "elapsed_s", float("nan")),
            ("elapsed-inf", "elapsed_s", float("inf")),
            ("mean-bool", "last_mean", True),
            ("mean-string", "last_mean", "114.0"),
            ("mean-nan", "last_mean", float("nan")),
            ("mean-inf", "last_mean", float("inf")),
            ("nonblack-bool", "last_nonblack", True),
            ("nonblack-string", "last_nonblack", "1.0"),
            ("nonblack-nan", "last_nonblack", float("nan")),
            ("nonblack-inf", "last_nonblack", float("inf")),
            ("settle-insufficient", "elapsed_s", 19.99),
            ("nonblack-insufficient", "last_nonblack", 0.09),
            ("mean-not-upper-only", "last_mean", 86.0),
            ("mean-mismatch", "last_mean", float(base["last_mean"]) + 0.1),
            ("nonblack-mismatch", "last_nonblack", 0.9999),
        )
        for name, field, value in scalar_mutations:
            payload = self.task9_clone_readiness(base)
            payload[field] = value
            cases.append((name, payload))

        for name, deltas in (
            ("deltas-insufficient", [0.003]),
            ("deltas-unstable", [0.003, 0.02]),
            ("deltas-negative", [0.003, -0.001]),
            ("deltas-bool", [0.003, False]),
            ("deltas-string", [0.003, "0.004"]),
            ("deltas-nan", [0.003, float("nan")]),
            ("deltas-inf", [0.003, float("inf")]),
        ):
            payload = self.task9_clone_readiness(base)
            payload["inter_sample_deltas"] = deltas
            cases.append((name, payload))

        for name, field, value in (
            ("threshold-min-settle", "min_settle_s", 20.1),
            ("threshold-stability", "stability_max", 0.021),
            ("threshold-samples", "stable_samples", 3),
            ("threshold-menu-max", "menu_max_mean", 86.1),
            ("threshold-nonblack", "nonblack_min", 0.11),
            ("threshold-bool", "stable_samples", True),
            ("threshold-string", "menu_max_mean", "86.0"),
        ):
            payload = self.task9_clone_readiness(base)
            payload["thresholds"][field] = value
            cases.append((name, payload))

        for field in (
            "min_settle_s",
            "stability_max",
            "stable_samples",
            "menu_max_mean",
            "nonblack_min",
        ):
            for label, value in (("bool", True), ("string", "near-match")):
                payload = self.task9_clone_readiness(base)
                payload["thresholds"][field] = value
                cases.append((f"threshold-{field}-{label}", payload))

        for name, field, value in (
            ("burst-ok-bool-near", "ok", 1),
            ("burst-ok-false", "ok", False),
            ("path-string-near", "last_path", 123),
        ):
            payload = self.task9_clone_readiness(base)
            payload["last_burst"][field] = value
            cases.append((name, payload))

        outside_path = self.root / "ready_04_02.png"
        with Image.new("RGB", (200, 100), color=(114, 114, 114)) as image:
            image.save(outside_path, format="PNG")
        payload = self.task9_clone_readiness(base)
        payload["last_burst"]["last_path"] = str(outside_path)
        cases.append(("path-outside-phase3-temp", payload))

        _, wrong_name = self.task9_readiness_fixture(runtime, filename="ready_4_02.png")
        cases.append(("path-name-near-match", wrong_name))

        corrupt_temp = tempfile.TemporaryDirectory(prefix="phase3_ready_")
        self.addCleanup(corrupt_temp.cleanup)
        corrupt_path = pathlib.Path(corrupt_temp.name) / "ready_04_02.png"
        corrupt_path.write_bytes(b"not-a-png")
        payload = self.task9_clone_readiness(base)
        payload["last_burst"]["last_path"] = str(corrupt_path)
        cases.append(("image-illegible", payload))

        _, invalid_geometry = self.task9_readiness_fixture(runtime, size=(1, 1))
        cases.append(("image-invalid-geometry", invalid_geometry))

        cases.extend(
            (
                ("not-exact-dict", [("inworld", False)]),
                (
                    "dict-subclass",
                    type("ReadinessDict", (dict,), {})(self.task9_clone_readiness(base)),
                ),
                ("inworld-bool-near", {**self.task9_clone_readiness(base), "inworld": 0}),
            )
        )

        for name, payload in cases:
            with self.subTest(case=name):
                result = self.task9_wait_for_readiness(runtime, payload)
                self.assertIs(result, payload)
                if type(result) is dict:
                    self.assertNotIn("task9_readiness_recovery", result)

    def test_task9_upstream_true_passes_through_without_image_inspection(self) -> None:
        runtime = self.task9_default_runtime("readiness-upstream-true")
        runtime.mcp_capture = mock.Mock()
        payload = {"inworld": True, "elapsed_s": 23.0}

        result = self.task9_wait_for_readiness(runtime, payload)

        self.assertIs(result, payload)
        runtime.mcp_capture.load_rgb.assert_not_called()

    def test_legacy_blocked_http_409_then_ok_reaches_readiness_and_exactly_one_spawn(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.mcp_client = mock.Mock()
        runtime.mcp_client.run_result.side_effect = [
            self.http_error(
                409,
                b'{"error":"version_blocked","state":"legacy_blocked"}',
            ),
            (None, {"ok": True, "pos": [1000.0, 50.0, 2000.0]}),
        ]
        self.bind_default_player_query(runtime)
        config = self.config("legacy-blocked-settles")
        config.ready_timeout = 5.0

        outcome = smoke.collect(
            config,
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(2, runtime.mcp_client.run_result.call_count)
        self.assertEqual([1.0], runtime.clock.sleeps[:1])
        self.assertEqual(4.0, runtime.readiness_calls[0]["timeout_s"])
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_persistent_exact_legacy_blocked_http_409_times_out_with_exact_reason(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.mcp_client = mock.Mock()
        runtime.mcp_client.run_result.side_effect = lambda *args, **kwargs: (_ for _ in ()).throw(
            self.http_error(
                409,
                b'{"error":"version_blocked","state":"legacy_blocked"}',
            )
        )
        self.bind_default_player_query(runtime)
        config = self.config("legacy-blocked-timeout")
        config.ready_timeout = 2.5

        outcome = smoke.collect(
            config,
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "player_state_settlement_timeout:version_blocked:legacy_blocked",
            outcome.payload["stop_reason"],
        )
        self.assertEqual(3, runtime.mcp_client.run_result.call_count)
        self.assertEqual([1.0, 1.0, 0.5], runtime.clock.sleeps)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_nonexact_http_errors_fail_closed_before_readiness_or_spawn(self) -> None:
        exact_body = b'{"error":"version_blocked","state":"legacy_blocked"}'

        class RaisingBody:
            def read(self, *args: object, **kwargs: object) -> bytes:
                raise OSError("body_read_failed")

            def close(self) -> None:
                pass

        cases = (
            (
                "version-mismatch",
                lambda: self.http_error(
                    409,
                    b'{"error":"version_blocked","state":"version_mismatch"}',
                ),
            ),
            (
                "unknown-state",
                lambda: self.http_error(
                    409,
                    b'{"error":"version_blocked","state":"legacy_blocked_old"}',
                ),
            ),
            (
                "unknown-error",
                lambda: self.http_error(
                    409,
                    b'{"error":"version_blocked_old","state":"legacy_blocked"}',
                ),
            ),
            (
                "missing-error",
                lambda: self.http_error(409, b'{"state":"legacy_blocked"}'),
            ),
            (
                "missing-state",
                lambda: self.http_error(409, b'{"error":"version_blocked"}'),
            ),
            (
                "extra-key",
                lambda: self.http_error(
                    409,
                    b'{"error":"version_blocked","state":"legacy_blocked","extra":true}',
                ),
            ),
            ("malformed-json", lambda: self.http_error(409, b'{"error":')),
            ("nonobject-json", lambda: self.http_error(409, b'[]')),
            ("unicode-error", lambda: self.http_error(409, b'\xff')),
            (
                "body-read-error",
                lambda: self.http_error(409, b"", fp=RaisingBody()),
            ),
            ("http-400", lambda: self.http_error(400, exact_body)),
            ("http-429", lambda: self.http_error(429, exact_body)),
            ("http-500", lambda: self.http_error(500, exact_body)),
        )

        for index, (label, error_factory) in enumerate(cases):
            with self.subTest(case=label):
                runtime = FakeRuntime()
                runtime.mcp_client = mock.Mock()
                http_error = error_factory()
                runtime.mcp_client.run_result.side_effect = http_error
                self.bind_default_player_query(runtime)

                outcome = smoke.collect(
                    self.config(f"nonexact-http-error-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                http_error.close()

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn("runtime_before_spawn_stop:", outcome.payload["stop_reason"])
                self.assertEqual(1, runtime.mcp_client.run_result.call_count)
                self.assertEqual([], runtime.readiness_calls)
                self.assertEqual([], runtime.spawn_calls)

    def test_near_match_transient_envelopes_stop_before_readiness_or_spawn(self) -> None:
        near_matches = (
            {"ok": False, "error": "version_blocked", "state": "version_mismatch"},
            {"ok": False, "error": "version_blocked", "state": "legacy_blocked_old"},
            {"ok": False, "error": "version_blocked_old", "state": "legacy_blocked"},
            {"ok": False, "error": "version_blocked"},
            {"ok": False, "state": "legacy_blocked"},
        )

        for index, envelope in enumerate(near_matches):
            with self.subTest(envelope=envelope):
                runtime = FakeRuntime()
                runtime.player_state_results = [envelope]

                outcome = smoke.collect(
                    self.config(f"near-transient-envelope-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual(1, runtime.query_calls)
                self.assertEqual([], runtime.readiness_calls)
                self.assertEqual([], runtime.spawn_calls)

    def test_real_server_state_only_legacy_blocked_is_transient_and_mismatch_is_hard(
        self,
    ) -> None:
        tools_dir = str(smoke.DEFAULT_TOOLS_DIR)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
            self.addCleanup(sys.path.remove, tools_dir)
        core = importlib.import_module("dayz_mcp.core")
        loopback = importlib.import_module("dayz_mcp.loopback")

        def version_validator(version: str | None) -> str:
            state, _ = core.version_state_for(
                version,
                require_version=True,
                expected_game_version="game",
            )
            return state

        server_state = loopback.ServerState(
            key="test-key",
            version_validator=version_validator,
        )

        legacy_status, legacy_body = server_state.enqueue_command(
            "query_player_state", {}, peer="server"
        )
        self.assertEqual(409, legacy_status)
        self._assert_version_blocked_state(legacy_body, "legacy_blocked")

        expected_bridge = core.EXPECTED_BRIDGE_VERSION
        match_poll = f"{expected_bridge}~game"
        mismatch_bridge = "4" if expected_bridge != "4" else "3"
        mismatch_poll = f"{mismatch_bridge}~game"

        poll_match_status, _ = server_state.record_poll("server", match_poll)
        accepted_status, accepted_body = server_state.enqueue_command(
            "query_player_state", {}, peer="server"
        )
        self.assertEqual(200, poll_match_status)
        self.assertEqual(200, accepted_status)
        self.assertEqual("query_player_state", accepted_body["cmd"])

        poll_mismatch_status, _ = server_state.record_poll("server", mismatch_poll)
        mismatch_status, mismatch_body = server_state.enqueue_command(
            "query_player_state", {}, peer="server"
        )
        self.assertEqual(200, poll_mismatch_status)
        self.assertEqual(409, mismatch_status)
        self._assert_version_blocked_state(mismatch_body, "version_mismatch")

        transient_runtime = FakeRuntime()
        transient_runtime.mcp_client = mock.Mock()
        live_legacy_error = self.http_error(
            legacy_status,
            json.dumps(legacy_body, separators=(",", ":")).encode("utf-8"),
        )
        transient_runtime.mcp_client.run_result.side_effect = [
            live_legacy_error,
            (None, {"ok": True, "pos": [1000.0, 50.0, 2000.0]}),
        ]
        self.bind_default_player_query(transient_runtime)
        transient_config = self.config("real-server-state-legacy-blocked")
        transient_config.ready_timeout = 5.0
        transient_outcome = smoke.collect(
            transient_config,
            SequenceProvider(_owned_snapshot()),
            lambda _: transient_runtime,
        )
        live_legacy_error.close()
        self.assertEqual(smoke.EXIT_OK, transient_outcome.exit_code)
        self.assertEqual(1, len(transient_runtime.spawn_calls))

        runtime = FakeRuntime()
        runtime.mcp_client = mock.Mock()
        mismatch_error = self.http_error(
            mismatch_status,
            json.dumps(mismatch_body, separators=(",", ":")).encode("utf-8"),
        )
        runtime.mcp_client.run_result.side_effect = mismatch_error
        self.bind_default_player_query(runtime)

        outcome = smoke.collect(
            self.config("real-server-state-version-mismatch"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )
        mismatch_error.close()

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("HTTP Error 409", outcome.payload["stop_reason"])
        self.assertEqual(1, runtime.mcp_client.run_result.call_count)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_no_players_settles_with_real_envelope_before_readiness_and_one_spawn(self) -> None:
        runtime = FakeRuntime()
        player_position = [13556.85, 2.57, 6378.41]
        runtime.player_state_results = [
            {"ok": False, "error": "no_players"},
            {"ok": False, "error": "no_players"},
            {
                "ok": 1,
                "error": "",
                "state": {"name": "Dev", "pos": player_position},
            },
        ]
        config = self.config("no-players-settles")
        config.ready_timeout = 5.0

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(3, runtime.query_calls)
        self.assertEqual([5.0, 4.0, 3.0], runtime.query_timeouts)
        self.assertEqual([1.0, 1.0], runtime.clock.sleeps[:2])
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual(3.0, runtime.readiness_calls[0]["timeout_s"])
        self.assertEqual(player_position, runtime.readiness_calls[0]["look_at"])
        self.assertEqual(
            [13559.85, 4.57, 6381.41],
            runtime.readiness_calls[0]["camera_position"],
        )
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_real_player_state_envelope_reaches_readiness_and_exactly_one_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [
            {
                "ok": 1,
                "error": "",
                "state": {
                    "name": "Dev",
                    "pos": [13556.85, 2.57, 6378.41],
                },
            }
        ]

        outcome = smoke.collect(
            self.config("real-player-state-envelope"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, runtime.query_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual(
            [13556.85, 2.57, 6378.41],
            runtime.readiness_calls[0]["look_at"],
        )
        self.assertEqual(
            [13559.85, 4.57, 6381.41],
            runtime.readiness_calls[0]["camera_position"],
        )
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_player_state_ok_scalar_near_matches_stop_before_readiness_or_spawn(self) -> None:
        for index, near_match in enumerate((1.0, "1", 2)):
            with self.subTest(ok=near_match):
                runtime = FakeRuntime()
                runtime.player_state_results = [
                    {
                        "ok": near_match,
                        "error": "",
                        "state": {
                            "name": "Dev",
                            "pos": [13556.85, 2.57, 6378.41],
                        },
                    }
                ]

                outcome = smoke.collect(
                    self.config(f"player-state-ok-near-match-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn("player_state_failed:not_ok", outcome.payload["stop_reason"])
                self.assertEqual(1, runtime.query_calls)
                self.assertEqual([], runtime.readiness_calls)
                self.assertEqual([], runtime.spawn_calls)

    def test_persistent_peer_reconnect_flush_times_out_before_readiness_or_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [{"ok": False, "error": "peer_reconnect_flush"}]
        config = self.config("peer-timeout")
        config.ready_timeout = 2.5

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("player_state_settlement_timeout", outcome.payload["stop_reason"])
        self.assertEqual(3, runtime.query_calls)
        self.assertEqual([2.5, 1.5, 0.5], runtime.query_timeouts)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_persistent_no_players_times_out_before_readiness_or_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [{"ok": False, "error": "no_players"}]
        config = self.config("no-players-timeout")
        config.ready_timeout = 2.5
        deadline = runtime.clock.value + config.ready_timeout

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "player_state_settlement_timeout:no_players",
            outcome.payload["stop_reason"],
        )
        self.assertEqual(3, runtime.query_calls)
        self.assertEqual([2.5, 1.5, 0.5], runtime.query_timeouts)
        self.assertEqual([1.0, 1.0, 0.5], runtime.clock.sleeps)
        self.assertEqual(deadline, runtime.clock.value)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_player_state_queries_respect_remaining_budget_and_deadline(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [{"ok": False, "error": "peer_reconnect_flush"}]
        runtime.query_durations = [0.75, 0.75]
        config = self.config("peer-query-budget")
        config.ready_timeout = 2.5
        deadline = runtime.clock.value + config.ready_timeout

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("player_state_settlement_timeout", outcome.payload["stop_reason"])
        self.assertEqual(2, runtime.query_calls)
        self.assertEqual([2.5, 0.75], runtime.query_timeouts)
        self.assertTrue(all(start < deadline for start in runtime.query_started_at))
        self.assertEqual(deadline, runtime.clock.value)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_nontransient_player_state_error_stops_after_one_query(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [{"ok": False, "error": "permission_denied"}]

        outcome = smoke.collect(
            self.config("player-state-hard-error"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("player_state_failed:permission_denied", outcome.payload["stop_reason"])
        self.assertEqual(1, runtime.query_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_ownership_change_after_readiness_stops_before_raycast_and_spawn(self) -> None:
        provider = SequenceProvider(_owned_snapshot(), _foreign_snapshot())
        runtime = FakeRuntime()

        outcome = smoke.collect(self.config("ownership-change"), provider, lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.raycast_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_readiness_false_stops_without_spawn_and_without_model_evaluation(self) -> None:
        runtime = FakeRuntime()
        runtime.readiness_result = {"inworld": False, "elapsed_s": 90.0}

        outcome = smoke.collect(
            self.config("overlay"), SequenceProvider(_owned_snapshot()), lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual("NOT_EVALUATED", outcome.payload["model_verdict"])
        self.assertEqual(runtime.readiness_result, outcome.payload["readiness"])
        self.assertEqual([], runtime.raycast_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_initial_black_frontend_activates_once_before_true_readiness_without_input(
        self,
    ) -> None:
        events: list[str] = []
        runtime = FakeRuntime(events=events)
        initial_black = _task9_black_inspection()
        runtime.frontend_inspection_result = initial_black

        outcome = smoke.collect(
            self.config("initial-black-readiness-true"),
            SequenceProvider(_owned_snapshot(), events=events),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, len(runtime.frontend_inspection_calls))
        self.assertEqual(1, len(runtime.frontend_activation_calls))
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertLess(events.index("frontend_inspect"), events.index("snapshot", 1))
        self.assertLess(events.index("snapshot", 1), events.index("foreground_activate"))
        self.assertLess(events.index("foreground_activate"), events.index("spawn"))
        self.assertEqual(initial_black, outcome.payload["frontend_resume"]["inspection"])
        self.assertEqual(
            runtime.frontend_activation_result,
            outcome.payload["frontend_resume"]["action"],
        )

    def test_initial_black_frontend_can_recover_only_through_late_exact_overlay(self) -> None:
        runtime = FakeRuntime()
        initial_black = _task9_black_inspection()
        late_exact = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "click_screen": [1731, 999],
            "sha256": "A" * 64,
        }
        runtime.frontend_inspection_results = [initial_black, late_exact]
        runtime.readiness_results = [
            {"inworld": False, "elapsed_s": 189.52},
            {"inworld": True, "elapsed_s": 24.0},
        ]

        outcome = smoke.collect(
            self.config("initial-black-late-exact"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(2, len(runtime.frontend_inspection_calls))
        self.assertEqual(1, len(runtime.frontend_activation_calls))
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual(2, len(runtime.readiness_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertEqual(late_exact, outcome.payload["late_frontend_resume"]["inspection"])

    def test_initial_black_frontend_and_late_black_stop_without_input_or_spawn(self) -> None:
        runtime = FakeRuntime()
        black = _task9_black_inspection()
        runtime.frontend_inspection_results = [black, black]
        runtime.readiness_result = {"inworld": False, "elapsed_s": 189.52}

        outcome = smoke.collect(
            self.config("initial-black-late-black"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "late_frontend_inspection_failed:continue_client_stats_invalid",
            outcome.payload["stop_reason"],
        )
        self.assertEqual(2, len(runtime.frontend_inspection_calls))
        self.assertEqual(1, len(runtime.frontend_activation_calls))
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.spawn_calls)

    def test_initial_black_frontend_ownership_drift_stops_before_activation_and_readiness(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_result = _task9_black_inspection()
        drifted = _replace_process_command(
            _owned_snapshot(), 202, "@MERCEDES_AMGLF", "@DRIFTED"
        )

        outcome = smoke.collect(
            self.config("initial-black-ownership-drift"),
            SequenceProvider(_owned_snapshot(), drifted),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "ownership_changed_before_frontend_activation",
            outcome.payload["stop_reason"],
        )
        self.assertEqual([], runtime.frontend_activation_calls)
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_initial_black_frontend_activation_failure_stops_before_readiness(self) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_result = _task9_black_inspection()
        runtime.frontend_activation_result = {
            "ok": False,
            "attempted": False,
            "reason": "owned_window_match_missing",
        }

        outcome = smoke.collect(
            self.config("initial-black-activation-failed"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "frontend_activation_failed:owned_window_match_missing",
            outcome.payload["stop_reason"],
        )
        self.assertEqual(1, len(runtime.frontend_activation_calls))
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_initial_nonblack_frontend_invalidity_still_stops_before_readiness(self) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_result = {
            "ok": False,
            "detected": False,
            "reason": "continue_client_geometry_invalid",
        }

        outcome = smoke.collect(
            self.config("initial-invalid-geometry"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "frontend_inspection_failed:continue_client_geometry_invalid",
            outcome.payload["stop_reason"],
        )
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)

    def test_late_exact_continue_overlay_clicks_once_then_reaches_readiness_and_one_spawn(
        self,
    ) -> None:
        runtime = FakeRuntime()
        initial_absent = dict(runtime.frontend_inspection_result)
        late_exact = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "click_screen": [1731, 999],
            "sha256": "A" * 64,
        }
        runtime.frontend_inspection_results = [initial_absent, late_exact]
        runtime.readiness_results = [
            {"inworld": False, "elapsed_s": 189.52},
            {"inworld": True, "elapsed_s": 24.0},
        ]
        config = self.config("late-continue-overlay")
        config.ready_timeout = 300.0
        provider = SequenceProvider(_owned_snapshot(), _owned_snapshot(), _owned_snapshot())

        outcome = smoke.collect(config, provider, lambda _: runtime)

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(2, len(runtime.frontend_inspection_calls))
        self.assertEqual(
            "task9-continue-preaction.png",
            runtime.frontend_inspection_calls[0]["evidence_filename"],
        )
        self.assertEqual(
            "task9-continue-late.png",
            runtime.frontend_inspection_calls[1]["evidence_filename"],
        )
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual(2, len(runtime.readiness_calls))
        self.assertEqual(1, len(runtime.raycast_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertEqual(300.0, runtime.readiness_calls[1]["timeout_s"])
        self.assertEqual(initial_absent, outcome.payload["frontend_resume"]["inspection"])
        self.assertEqual(late_exact, outcome.payload["late_frontend_resume"]["inspection"])
        self.assertEqual(
            runtime.frontend_resume_result,
            outcome.payload["late_frontend_resume"]["action"],
        )
        self.assertEqual(
            runtime.readiness_results[0],
            outcome.payload["late_frontend_resume"]["readiness_before_resume"],
        )
        self.assertEqual(
            runtime.readiness_results[1],
            outcome.payload["late_frontend_resume"]["readiness_after_resume"],
        )

    def test_late_continue_absent_preserves_original_stop_without_input_or_retry(self) -> None:
        runtime = FakeRuntime()
        runtime.readiness_result = {"inworld": False, "elapsed_s": 189.52}

        outcome = smoke.collect(
            self.config("late-continue-absent"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("readiness_not_inworld", outcome.payload["stop_reason"])
        self.assertEqual(2, len(runtime.frontend_inspection_calls))
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.raycast_calls)
        self.assertEqual([], runtime.spawn_calls)
        self.assertEqual(
            "continue_overlay_absent",
            outcome.payload["late_frontend_resume"]["action"]["reason"],
        )

    def test_late_continue_partial_signature_stops_before_input_second_readiness_or_spawn(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.readiness_result = {"inworld": False, "elapsed_s": 189.52}
        runtime.frontend_inspection_results = [
            dict(runtime.frontend_inspection_result),
            {
                "ok": False,
                "detected": True,
                "reason": "continue_overlay_signature_partial",
            },
        ]

        outcome = smoke.collect(
            self.config("late-continue-partial"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "late_frontend_inspection_failed:continue_overlay_signature_partial",
            outcome.payload["stop_reason"],
        )
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.spawn_calls)

    def test_late_continue_ownership_drift_stops_before_input_second_readiness_or_spawn(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.readiness_result = {"inworld": False, "elapsed_s": 189.52}
        runtime.frontend_inspection_results = [
            dict(runtime.frontend_inspection_result),
            {
                "ok": True,
                "detected": True,
                "reason": "continue_overlay_exact",
                "click_screen": [1731, 999],
            },
        ]

        outcome = smoke.collect(
            self.config("late-continue-ownership-drift"),
            SequenceProvider(_owned_snapshot(), _foreign_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "ownership_changed_before_late_frontend_resume",
            outcome.payload["stop_reason"],
        )
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.spawn_calls)

    def test_late_continue_partial_input_stops_before_second_readiness_or_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.readiness_result = {"inworld": False, "elapsed_s": 189.52}
        runtime.frontend_inspection_results = [
            dict(runtime.frontend_inspection_result),
            {
                "ok": True,
                "detected": True,
                "reason": "continue_overlay_exact",
                "click_screen": [1731, 999],
            },
        ]
        runtime.frontend_resume_result = {
            "ok": False,
            "attempted": True,
            "reason": "send_input_partial",
            "events_sent": 1,
        }

        outcome = smoke.collect(
            self.config("late-continue-input-partial"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn(
            "late_frontend_resume_failed:send_input_partial",
            outcome.payload["stop_reason"],
        )
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual([], runtime.spawn_calls)

    def test_late_continue_second_readiness_false_stops_after_one_click_without_spawn(
        self,
    ) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_results = [
            dict(runtime.frontend_inspection_result),
            {
                "ok": True,
                "detected": True,
                "reason": "continue_overlay_exact",
                "click_screen": [1731, 999],
            },
        ]
        runtime.readiness_results = [
            {"inworld": False, "elapsed_s": 189.52},
            {"inworld": False, "elapsed_s": 300.0},
        ]

        outcome = smoke.collect(
            self.config("late-continue-second-readiness-false"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("readiness_not_inworld", outcome.payload["stop_reason"])
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual(2, len(runtime.readiness_calls))
        self.assertEqual([], runtime.spawn_calls)

    def test_exact_continue_overlay_clicks_once_before_readiness_and_spawn(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(events=events)
        runtime.frontend_inspection_result = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "click_screen": [1731, 999],
            "sha256": "A" * 64,
        }
        provider = SequenceProvider(
            _owned_snapshot(), _owned_snapshot(), events=events
        )

        outcome = smoke.collect(
            self.config("continue-overlay-click"), provider, lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, len(runtime.frontend_inspection_calls))
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertLess(events.index("frontend_inspect"), events.index("frontend_resume"))
        self.assertLess(events.index("frontend_resume"), events.index("spawn"))
        self.assertEqual(
            runtime.frontend_inspection_result,
            outcome.payload["frontend_resume"]["inspection"],
        )
        self.assertEqual(
            runtime.frontend_resume_result,
            outcome.payload["frontend_resume"]["action"],
        )

    def test_continue_overlay_ownership_drift_stops_before_input_readiness_and_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_result = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "click_screen": [1731, 999],
        }
        provider = SequenceProvider(_owned_snapshot(), _foreign_snapshot())

        outcome = smoke.collect(
            self.config("continue-overlay-ownership-drift"),
            provider,
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("ownership_changed_before_frontend_resume", outcome.payload["stop_reason"])
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)
        self.assertEqual(
            runtime.frontend_inspection_result,
            outcome.payload["frontend_resume"]["inspection"],
        )

    def test_continue_overlay_input_failure_stops_before_readiness_and_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.frontend_inspection_result = {
            "ok": True,
            "detected": True,
            "reason": "continue_overlay_exact",
            "click_screen": [1731, 999],
        }
        runtime.frontend_resume_result = {
            "ok": False,
            "attempted": True,
            "reason": "send_input_partial",
            "events_sent": 1,
        }

        outcome = smoke.collect(
            self.config("continue-overlay-input-failure"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("frontend_resume_failed:send_input_partial", outcome.payload["stop_reason"])
        self.assertEqual(1, len(runtime.frontend_resume_calls))
        self.assertEqual([], runtime.readiness_calls)
        self.assertEqual([], runtime.spawn_calls)
        self.assertEqual(
            runtime.frontend_resume_result,
            outcome.payload["frontend_resume"]["action"],
        )

    def test_absent_continue_overlay_emits_no_input_and_preserves_normal_readiness(self) -> None:
        runtime = FakeRuntime()

        outcome = smoke.collect(
            self.config("continue-overlay-absent"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, len(runtime.frontend_inspection_calls))
        self.assertEqual([], runtime.frontend_resume_calls)
        self.assertEqual(1, len(runtime.readiness_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertEqual(
            {
                "inspection": runtime.frontend_inspection_result,
                "action": {
                    "ok": True,
                    "attempted": False,
                    "reason": "continue_overlay_absent",
                },
            },
            outcome.payload["frontend_resume"],
        )

    def test_readiness_true_permits_one_raycast_and_spawn_and_records_exact_identity(self) -> None:
        outcome, _, _, runtime = self.run_success("one-spawn")

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, len(runtime.raycast_calls))
        self.assertEqual(1, len(runtime.spawn_calls))
        self.assertEqual("MERCEDES_AMGLF", runtime.spawn_calls[0]["object_type"])
        self.assertEqual(1028, runtime.spawn_calls[0]["flags"])
        self.assertEqual([1004.0, 49.75, 2004.0], runtime.spawn_calls[0]["position"])
        self.assertEqual(77, outcome.payload["spawn"]["object_id"])
        self.assertEqual([1004.0, 49.75, 2004.0], outcome.payload["spawn"]["position"])

    def test_spawn_flags_keep_physics_and_trace_but_exclude_pathgraph(self) -> None:
        self.assertEqual(1024 | 4, smoke.SPAWN_FLAGS)
        self.assertEqual(0, smoke.SPAWN_FLAGS & 32)

    def test_real_raycast_integer_flags_reach_exactly_one_spawn(self) -> None:
        runtime = FakeRuntime()
        runtime.raycast_result["ok"] = 1
        runtime.raycast_result["raycast"]["hit"] = 1

        outcome = smoke.collect(
            self.config("real-raycast-integer-flags"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(1, len(runtime.raycast_calls))
        self.assertEqual(1, len(runtime.spawn_calls))

    def test_raycast_flag_scalar_near_matches_stop_before_spawn(self) -> None:
        cases = (
            ("ok", 1.0),
            ("ok", "1"),
            ("ok", 2),
            ("hit", 1.0),
            ("hit", "1"),
            ("hit", 2),
        )
        for index, (flag, near_match) in enumerate(cases):
            with self.subTest(flag=flag, value=near_match):
                runtime = FakeRuntime()
                if flag == "ok":
                    runtime.raycast_result["ok"] = near_match
                else:
                    runtime.raycast_result["raycast"]["hit"] = near_match

                outcome = smoke.collect(
                    self.config(f"raycast-{flag}-near-match-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn("raycast_no_hit", outcome.payload["stop_reason"])
                self.assertEqual(1, len(runtime.raycast_calls))
                self.assertEqual([], runtime.spawn_calls)

    def test_spawn_and_cleanup_ok_accept_only_true_or_exact_integer_one(self) -> None:
        scalar_cases = (
            ("bool_true", True),
            ("int_one", 1),
            ("float_one", 1.0),
            ("string_one", "1"),
            ("int_two", 2),
            ("bool_false", False),
            ("none", None),
        )
        accepted = {"bool_true", "int_one"}
        actual_spawn: dict[str, int] = {}
        for index, (label, ok_value) in enumerate(scalar_cases):
            runtime = FakeRuntime()
            runtime.spawn_result = {
                "ok": ok_value,
                "object_id": 71,
                "pos": [1004.0, 49.75, 2004.0],
            }
            outcome = smoke.collect(
                self.config(f"spawn-ok-scalar-{index}"),
                SequenceProvider(_owned_snapshot()),
                lambda _: runtime,
            )
            actual_spawn[label] = outcome.exit_code

        actual_invalid_object_ids: dict[str, int] = {}
        for index, (label, object_id) in enumerate(
            (
                ("zero", 0),
                ("negative", -1),
                ("bool", True),
                ("float", 71.0),
                ("string", "71"),
                ("none", None),
            )
        ):
            runtime = FakeRuntime()
            runtime.spawn_result = {
                "ok": True,
                "object_id": object_id,
                "pos": [1004.0, 49.75, 2004.0],
            }
            outcome = smoke.collect(
                self.config(f"spawn-object-id-{index}"),
                SequenceProvider(_owned_snapshot()),
                lambda _: runtime,
            )
            actual_invalid_object_ids[label] = outcome.exit_code

        actual_cleanup: dict[str, dict[str, object]] = {}
        for label, ok_value in scalar_cases:
            runtime = smoke.DefaultRuntime.__new__(smoke.DefaultRuntime)
            runtime.mcp_client = mock.Mock()
            runtime.mcp_client.run_result.return_value = (
                None,
                {"ok": ok_value, "deleted": 1},
            )
            actual_cleanup[label] = runtime.cleanup(object(), 71)

        self.maxDiff = None
        self.assertEqual(
            {
                "spawn": {
                    label: smoke.EXIT_OK if label in accepted else smoke.EXIT_STOP
                    for label, _ in scalar_cases
                },
                "invalid_object_ids": {
                    label: smoke.EXIT_STOP for label in actual_invalid_object_ids
                },
                "cleanup": {
                    label: {"ok": ok_value, "deleted": 1}
                    for label, ok_value in scalar_cases
                },
            },
            {
                "spawn": actual_spawn,
                "invalid_object_ids": actual_invalid_object_ids,
                "cleanup": actual_cleanup,
            },
        )

    def test_positive_spawn_id_is_cleaned_when_response_position_is_invalid(self) -> None:
        runtime = FakeRuntime()
        runtime.spawn_result = {"ok": True, "object_id": 77, "pos": [1.0, 2.0]}
        config = self.config("invalid-spawn-position")

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual([77], runtime.cleanup_calls)
        self.assertTrue(config.verdict_path.is_file())
        self.assertEqual(77, outcome.payload["cleanup"]["object_id"])
        self.assertTrue(outcome.payload["cleanup"]["attempted"])
        self.assertTrue(outcome.payload["cleanup"]["ok"])
        self.assertEqual("DELETED_AND_ABSENT", outcome.payload["cleanup"]["outcome"])
        self.assertFalse(outcome.payload["cleanup"]["possible_orphan"])

    def test_spawn_timeout_after_enqueue_marks_possible_orphan_and_blocks_retry(self) -> None:
        runtime = FakeRuntime()
        runtime.spawn_exception = TimeoutError("after_enqueue_unknown_outcome")
        config = self.config("spawn-after-enqueue-timeout")

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual([], runtime.cleanup_calls)
        self.assertEqual(
            {
                "attempted": False,
                "ok": False,
                "outcome": "NOT_ATTEMPTED_NO_OBJECT_ID",
                "object_id": 0,
                "possible_orphan": True,
            },
            outcome.payload["cleanup"],
        )
        self.assertTrue(outcome.payload["automatic_retry_blocked"])
        self.assertIn("after_enqueue_unknown_outcome", outcome.payload["stop_reason"])

    def test_evidence_directory_failure_precedes_spawn_and_still_publishes_stop(self) -> None:
        runtime = FakeRuntime()
        config = self.config("evidence-dir-failure")
        mkdir = smoke.pathlib.Path.mkdir

        def fail_evidence_dir(path: pathlib.Path, *args: object, **kwargs: object) -> None:
            if path == config.evidence_dir:
                raise OSError("evidence_dir_denied")
            mkdir(path, *args, **kwargs)

        with mock.patch.object(smoke.pathlib.Path, "mkdir", autospec=True, side_effect=fail_evidence_dir):
            outcome = smoke.collect(
                config, SequenceProvider(_owned_snapshot()), lambda _: runtime
            )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual([], runtime.spawn_calls)
        self.assertEqual([], runtime.cleanup_calls)
        self.assertTrue(config.verdict_path.is_file())

    def test_bool_or_nonfinite_vectors_stop_before_runtime_side_effects(self) -> None:
        bool_runtime = FakeRuntime()
        bool_runtime.query_player_state = mock.Mock(
            return_value={"ok": True, "pos": [True, 50.0, 2000.0]}
        )
        bool_config = self.config("bool-player-vector")

        bool_outcome = smoke.collect(
            bool_config, SequenceProvider(_owned_snapshot()), lambda _: bool_runtime
        )

        self.assertEqual(smoke.EXIT_STOP, bool_outcome.exit_code)
        self.assertEqual([], bool_runtime.readiness_calls)
        self.assertEqual([], bool_runtime.raycast_calls)
        self.assertEqual([], bool_runtime.spawn_calls)
        self.assertTrue(bool_config.verdict_path.is_file())

        nonfinite_runtime = FakeRuntime()
        nonfinite_runtime.raycast_result["raycast"]["pos"] = [float("inf"), 49.75, 2004.0]
        nonfinite_config = self.config("nonfinite-raycast-vector")

        nonfinite_outcome = smoke.collect(
            nonfinite_config,
            SequenceProvider(_owned_snapshot()),
            lambda _: nonfinite_runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, nonfinite_outcome.exit_code)
        self.assertEqual([], nonfinite_runtime.spawn_calls)
        self.assertFalse(nonfinite_config.evidence_dir.exists())
        self.assertTrue(nonfinite_config.verdict_path.is_file())

    def test_malformed_spawn_payload_stops_durably_and_blocks_retry(self) -> None:
        for index, malformed in enumerate((None, ["not", "a", "mapping"])):
            with self.subTest(payload=malformed):
                runtime = FakeRuntime()
                runtime.spawn_result = malformed
                config = self.config(f"malformed-spawn-{index}")

                outcome = smoke.collect(
                    config, SequenceProvider(_owned_snapshot()), lambda _: runtime
                )

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn("spawn_payload_invalid", outcome.payload["stop_reason"])
                self.assertEqual(1, len(runtime.spawn_calls))
                self.assertEqual([], runtime.cleanup_calls)
                self.assertEqual(
                    {
                        "attempted": False,
                        "ok": False,
                        "outcome": "NOT_ATTEMPTED_NO_OBJECT_ID",
                        "object_id": 0,
                        "possible_orphan": True,
                    },
                    outcome.payload["cleanup"],
                )
                self.assertTrue(outcome.payload["automatic_retry_blocked"])
                self.assertTrue(config.verdict_path.is_file())

    def test_blocked_or_no_hit_raycast_stops_without_spawn(self) -> None:
        cases = (
            {"ok": True, "raycast": {"hit": False}},
            {
                "ok": True,
                "raycast": {
                    "hit": True,
                    "pos": [1.0, 2.0, 3.0],
                    "object_type": "TreeHard_t_PiceaAbies_3f",
                },
            },
        )
        for index, raycast_result in enumerate(cases):
            with self.subTest(index=index):
                runtime = FakeRuntime()
                runtime.raycast_result = raycast_result
                outcome = smoke.collect(
                    self.config(f"raycast-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual([], runtime.spawn_calls)

    def test_task9_v2_orders_isolation_unique_fixture_before_exact_eight_views(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(events=events)

        outcome, _, _, _ = self.run_success("v2-order", runtime=runtime)

        views = [
            "front",
            "rear",
            "left",
            "right",
            "front_left",
            "front_right",
            "rear_left",
            "rear_right",
        ]
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code, outcome.payload["stop_reason"])
        self.assertEqual(100.0, runtime.telemetry_calls[0]["radius"])
        self.assertEqual([1000.0, 50.0, 2000.0], runtime.telemetry_calls[0]["position"])
        self.assertEqual(3, len(runtime.telemetry_calls))
        self.assertEqual(1, len(runtime.prepare_calls))
        self.assertEqual(views, [call["view"] for call in runtime.camera_calls])
        self.assertEqual(views, [call["view"] for call in runtime.capture_calls])
        prepare_index = events.index("prepare")
        self.assertTrue(
            all(events.index(f"camera:{view}") > prepare_index for view in views)
        )
        self.assertEqual("BASELINE_ROLLBACK_6DB34BDC", outcome.payload["build_id"])
        self.assertTrue(outcome.payload["isolation"]["pre_spawn_absent"])
        self.assertTrue(outcome.payload["unique_vehicle"]["verified"])
        self.assertEqual(4, outcome.payload["fixture"]["wheel_count"])
        self.assertEqual(4, outcome.payload["fixture"]["wheel_item_count"])

    def test_dayz_bridge_numeric_booleans_are_accepted_end_to_end(self) -> None:
        runtime = FakeRuntime()
        runtime.player_state_results = [
            {"ok": 1, "pos": [1000.0, 50.0, 2000.0]}
        ]
        runtime.raycast_result["ok"] = 1
        runtime.raycast_result["raycast"]["hit"] = 1
        runtime.spawn_result["ok"] = 1
        runtime.telemetry_results[0] = {"ok": 1, "telemetry": {"found": 0}}
        runtime.telemetry_results[1]["ok"] = 1
        runtime.telemetry_results[1]["telemetry"]["found"] = 1
        runtime.telemetry_results[2] = {"ok": 1, "telemetry": {"found": 0}}
        runtime.prepare_result["ok"] = 1
        runtime.prepare_result["vehicle_fixture_ready"] = 1
        runtime.prepare_result["telemetry"]["found"] = 1
        runtime.cleanup_result = {"ok": 1, "deleted": 1}
        runtime.camera_ok_by_view = {view: 1 for view in smoke.VIEW_SPECS}

        outcome = smoke.collect(
            self.config("numeric-dayz-booleans"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code, outcome.payload["stop_reason"])
        self.assertTrue(outcome.payload["isolation"]["pre_spawn_absent"])
        self.assertTrue(outcome.payload["unique_vehicle"]["verified"])
        self.assertTrue(outcome.payload["cleanup"]["ok"])

    def test_pre_isolation_rejects_values_outside_dayz_boolean_contract(self) -> None:
        cases = (
            {"ok": True, "telemetry": {"found": True}},
            {"ok": False, "error": "bridge_error", "telemetry": {"found": False}},
            {"ok": 2, "telemetry": {"found": False}},
            {"ok": True, "telemetry": {"found": 2}},
            {"ok": True, "telemetry": "not-a-dict"},
        )
        for index, result in enumerate(cases):
            with self.subTest(index=index):
                runtime = FakeRuntime()
                runtime.telemetry_results[0] = result
                outcome = smoke.collect(
                    self.config(f"pre-isolation-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual([], runtime.spawn_calls)
                self.assertEqual([], runtime.prepare_calls)
                self.assertEqual([], runtime.capture_calls)

    def test_post_spawn_requires_unique_exact_type_and_class_before_prepare(self) -> None:
        cases = (
            {"ok": True, "telemetry": {"found": False}},
            {"ok": False, "error": "ambiguous", "telemetry": {"found": True}},
            {
                "ok": True,
                "telemetry": {
                    "found": True,
                    "type": "MERCEDES_AMGLF_OLD",
                    "class_name": smoke.OBJECT_TYPE,
                },
            },
            {
                "ok": True,
                "telemetry": {
                    "found": True,
                    "type": smoke.OBJECT_TYPE,
                    "class_name": "MERCEDES_AMGLF_OLD",
                },
            },
        )
        for index, result in enumerate(cases):
            with self.subTest(index=index):
                runtime = FakeRuntime()
                runtime.telemetry_results[1] = result
                outcome = smoke.collect(
                    self.config(f"post-unique-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual(1, len(runtime.spawn_calls))
                self.assertEqual([], runtime.prepare_calls)
                self.assertEqual([], runtime.capture_calls)

    def test_fixture_requires_root_ready_exact_counts_and_case_sensitive_wheels(self) -> None:
        valid_telemetry = dict(FakeRuntime().prepare_result["telemetry"])
        cases: list[tuple[str, dict[str, object]]] = []
        for value in (3, 5, True, 4.0, "4"):
            telemetry = dict(valid_telemetry)
            telemetry["wheel_count"] = value
            cases.append((f"wheel-{value!r}", {"ok": True, "vehicle_fixture_ready": True, "telemetry": telemetry}))
        for value in (3, True, 4.0, "4"):
            telemetry = dict(valid_telemetry)
            telemetry["attachment_count"] = value
            cases.append((f"attachment-{value!r}", {"ok": True, "vehicle_fixture_ready": True, "telemetry": telemetry}))
        for wheels in (
            ["MERCEDES_AMGLF_Wheel"] * 3,
            ["mercedes_amglf_wheel"] * 4,
            ["MERCEDES_AMGLF_Wheel_OLD"] * 4,
            ["MERCEDES_AMGLF_Wheel"] * 4 + ["MERCEDES_AMGLF_Wheel"],
        ):
            telemetry = dict(valid_telemetry)
            telemetry["items"] = wheels
            cases.append((f"items-{len(cases)}", {"ok": True, "vehicle_fixture_ready": True, "telemetry": telemetry}))
        cases.extend(
            (
                ("ready-nested-only", {"ok": True, "telemetry": {**valid_telemetry, "vehicle_fixture_ready": True}}),
                ("ready-invalid-int", {"ok": True, "vehicle_fixture_ready": 2, "telemetry": valid_telemetry}),
                ("ok-invalid-int", {"ok": 2, "vehicle_fixture_ready": True, "telemetry": valid_telemetry}),
            )
        )
        for index, (label, result) in enumerate(cases):
            with self.subTest(case=label):
                runtime = FakeRuntime()
                runtime.prepare_result = result
                outcome = smoke.collect(
                    self.config(f"fixture-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual([], runtime.camera_calls)
                self.assertEqual([], runtime.capture_calls)

        runtime = FakeRuntime()
        runtime.prepare_result["telemetry"] = {
            **valid_telemetry,
            "attachment_count": 5,
            "items": ["MERCEDES_AMGLF_Wheel"] * 4 + ["MERCEDES_AMGLF_DriverDoor"],
        }
        outcome = smoke.collect(
            self.config("fixture-four-wheel-occurrences-with-extra-attachment"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code, outcome.payload["stop_reason"])

    def test_cleanup_requires_exact_deleted_one_and_post_delete_absence(self) -> None:
        for index, deleted in enumerate((0, True, 1.0, "1", 2)):
            with self.subTest(deleted=deleted):
                runtime = FakeRuntime()
                runtime.cleanup_result = {"ok": True, "deleted": deleted}
                outcome = smoke.collect(
                    self.config(f"cleanup-deleted-{index}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertTrue(outcome.payload["cleanup"]["possible_orphan"])
                self.assertTrue(outcome.payload["automatic_retry_blocked"])
        runtime = FakeRuntime()
        runtime.telemetry_results[2] = {
            "ok": True,
            "telemetry": {
                "found": True,
                "type": smoke.OBJECT_TYPE,
                "class_name": smoke.OBJECT_TYPE,
            },
        }
        outcome = smoke.collect(
            self.config("cleanup-post-found"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )
        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertTrue(outcome.payload["cleanup"]["possible_orphan"])

    def test_collection_requests_eight_distinct_native_exterior_views(self) -> None:
        outcome, _, _, runtime = self.run_success("views")

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        expected = list(smoke.VIEW_SPECS)
        self.assertEqual(8, len(expected))
        self.assertEqual(expected, [c["view"] for c in runtime.camera_calls])
        self.assertEqual(expected, [c["view"] for c in runtime.capture_calls])
        self.assertEqual(set(expected), set(outcome.payload["evidence"]["views"]))
        self.assertFalse(any("BUILD_A" in item["visual_gate"] for item in outcome.payload["evidence"]["views"].values()))

    def test_camera_ok_accepts_only_true_or_exact_integer_one_for_each_view(self) -> None:
        view_order = list(smoke.VIEW_SPECS)
        accepted = (("bool_true", True), ("int_one", 1))
        rejected = (
            ("float_one", 1.0),
            ("string_one", "1"),
            ("int_two", 2),
            ("bool_false", False),
            ("none", None),
        )

        for label, ok_value in accepted:
            with self.subTest(accepted=label):
                runtime = FakeRuntime()
                runtime.camera_ok_by_view = {view: ok_value for view in view_order}

                outcome = smoke.collect(
                    self.config(f"camera-ok-{label}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )

                self.assertEqual(
                    smoke.EXIT_OK, outcome.exit_code, outcome.payload["stop_reason"]
                )
                self.assertEqual(view_order, [call["view"] for call in runtime.camera_calls])
                self.assertEqual(view_order, [call["view"] for call in runtime.capture_calls])
                self.assertEqual(view_order, list(outcome.payload["evidence"]["views"]))
                self.assertEqual([77], runtime.cleanup_calls)
                self.assertEqual("DELETED_AND_ABSENT", outcome.payload["cleanup"]["outcome"])

        for failed_index, failed_view in enumerate(view_order):
            for label, ok_value in rejected:
                with self.subTest(failed_view=failed_view, rejected=label):
                    runtime = FakeRuntime()
                    runtime.camera_ok_by_view[failed_view] = ok_value

                    outcome = smoke.collect(
                        self.config(f"camera-{failed_view}-{label}"),
                        SequenceProvider(_owned_snapshot()),
                        lambda _: runtime,
                    )

                    completed_views = view_order[:failed_index]
                    self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                    self.assertEqual(
                        f"camera_failed:{failed_view}", outcome.payload["stop_reason"]
                    )
                    self.assertEqual(
                        view_order[: failed_index + 1],
                        [call["view"] for call in runtime.camera_calls],
                    )
                    self.assertEqual(
                        completed_views, [call["view"] for call in runtime.capture_calls]
                    )
                    self.assertEqual(
                        completed_views, list(outcome.payload["evidence"]["views"])
                    )
                    self.assertEqual([77], runtime.cleanup_calls)
                    self.assertEqual("DELETED_AND_ABSENT", outcome.payload["cleanup"]["outcome"])

    def test_camera_result_requires_moved_lookat_viewport_at_requested_position(self) -> None:
        invalid_results = {
            "camera_missing": {"ok": True},
            "camera_not_ok": {
                "ok": True,
                "camera": {
                    "ok": False,
                    "viewport_moved": True,
                    "applied_mode": "lookat",
                    "pos": [1004.0, 52.0, 2011.0],
                },
            },
            "viewport_not_moved": {
                "ok": True,
                "camera": {
                    "ok": True,
                    "viewport_moved": False,
                    "applied_mode": "lookat",
                    "pos": [1004.0, 52.0, 2011.0],
                },
            },
            "mode_mismatch": {
                "ok": True,
                "camera": {
                    "ok": True,
                    "viewport_moved": True,
                    "applied_mode": "free",
                    "pos": [1004.0, 52.0, 2011.0],
                },
            },
            "position_invalid": {
                "ok": True,
                "camera": {
                    "ok": True,
                    "viewport_moved": True,
                    "applied_mode": "lookat",
                    "pos": ["bad", 52.0, 2011.0],
                },
            },
            "position_mismatch": {
                "ok": True,
                "camera": {
                    "ok": True,
                    "viewport_moved": True,
                    "applied_mode": "lookat",
                    "pos": [999.0, 52.0, 2011.0],
                },
            },
        }

        for expected_reason, camera_result in invalid_results.items():
            with self.subTest(expected_reason=expected_reason):
                runtime = FakeRuntime()
                runtime.camera_result_by_view["front"] = camera_result

                outcome = smoke.collect(
                    self.config(f"camera-contract-{expected_reason}"),
                    SequenceProvider(_owned_snapshot()),
                    lambda _: runtime,
                )

                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertEqual(
                    f"camera_contract_failed:front:{expected_reason}",
                    outcome.payload["stop_reason"],
                )
                self.assertEqual([], runtime.capture_calls)
                self.assertEqual(
                    {"front": camera_result},
                    outcome.payload["evidence"]["camera_results"],
                )
                self.assertEqual([77], runtime.cleanup_calls)

    def test_byte_identical_view_capture_stops_at_first_duplicate(self) -> None:
        runtime = FakeRuntime()
        runtime.capture_identical = True

        outcome = smoke.collect(
            self.config("camera-stale-frame"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(
            "capture_stale:rear:matches:front", outcome.payload["stop_reason"]
        )
        self.assertEqual(["front", "rear"], [call["view"] for call in runtime.capture_calls])
        self.assertEqual({"front"}, set(outcome.payload["evidence"]["views"]))
        self.assertEqual(
            {"front", "rear"}, set(outcome.payload["evidence"]["camera_results"])
        )
        self.assertEqual([77], runtime.cleanup_calls)

    def test_client_peer_settlement_runs_after_fixture_before_first_camera(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(events=events)

        outcome = smoke.collect(
            self.config("client-peer-settlement-order"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code, outcome.payload["stop_reason"])
        self.assertEqual([{"timeout_s": smoke.CLIENT_PEER_SETTLE_TIMEOUT_SECONDS}], runtime.client_settlement_calls)
        self.assertLess(events.index("prepare"), events.index("client_settle"))
        self.assertLess(events.index("client_settle"), events.index("camera:front"))
        self.assertEqual(runtime.client_settlement_result, outcome.payload["client_settlement"])

    def test_client_peer_settlement_failure_stops_before_camera_and_cleans_up(self) -> None:
        runtime = FakeRuntime()
        runtime.client_settlement_result = {
            "ready": False,
            "error": "client_peer_settlement_timeout",
            "samples_required": 5,
            "samples_observed": 0,
        }

        outcome = smoke.collect(
            self.config("client-peer-settlement-stop"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(
            "client_peer_settlement_failed:client_peer_settlement_timeout",
            outcome.payload["stop_reason"],
        )
        self.assertEqual([], runtime.camera_calls)
        self.assertEqual([], runtime.capture_calls)
        self.assertEqual([77], runtime.cleanup_calls)
        self.assertEqual("DELETED_AND_ABSENT", outcome.payload["cleanup"]["outcome"])
        self.assertEqual(runtime.client_settlement_result, outcome.payload["client_settlement"])

    def test_client_peer_settlement_requires_five_consecutive_clean_status_samples(self) -> None:
        clock = FakeClock()

        class StatusClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []
                self.statuses = [
                    {
                        "daemon_generation": "generation-a",
                        "client_peer": {
                            "last_poll_age_s": 12.0,
                            "queue_depth": 0,
                            "version_state": "ok",
                        },
                    },
                    {
                        "daemon_generation": "generation-a",
                        "client_peer": {
                            "last_poll_age_s": 0.2,
                            "queue_depth": 1,
                            "version_state": "ok",
                        },
                    },
                ] + [
                    {
                        "daemon_generation": "generation-a",
                        "client_peer": {
                            "last_poll_age_s": 0.1,
                            "queue_depth": 0,
                            "version_state": "ok",
                        },
                    }
                    for _ in range(5)
                ]

            def request_json(self, method: str, path: str) -> dict[str, object]:
                self.calls.append((method, path))
                return self.statuses.pop(0)

        client = StatusClient()
        result = smoke._wait_for_client_peer_settlement(
            client,
            clock.monotonic,
            clock.sleep,
            timeout_s=10.0,
        )

        self.assertTrue(result["ready"])
        self.assertTrue(result["recovery_observed"])
        self.assertEqual(7, result["samples_observed"])
        self.assertEqual(5, result["stable_samples"])
        self.assertEqual("generation-a", result["daemon_generation"])
        self.assertEqual([("GET", "/status")] * 7, client.calls)

    def test_client_peer_settlement_times_out_fail_closed_on_stale_poll_age(self) -> None:
        clock = FakeClock()

        class StaleStatusClient:
            def request_json(self, method: str, path: str) -> dict[str, object]:
                return {
                    "daemon_generation": "generation-a",
                    "client_peer": {
                        "last_poll_age_s": 15.0,
                        "queue_depth": 0,
                        "version_state": "ok",
                    },
                }

        result = smoke._wait_for_client_peer_settlement(
            StaleStatusClient(),
            clock.monotonic,
            clock.sleep,
            timeout_s=1.1,
        )

        self.assertFalse(result["ready"])
        self.assertEqual("client_peer_settlement_timeout", result["error"])
        self.assertEqual(0, result["stable_samples"])
        self.assertGreaterEqual(result["samples_observed"], 3)

    def task9_spawn_protocol_runtime(
        self, clock: FakeClock
    ) -> smoke.DefaultRuntime:
        runtime = smoke.DefaultRuntime.__new__(smoke.DefaultRuntime)
        runtime.mcp_client = mock.Mock()
        runtime.mcp_client.run_result.return_value = (
            901,
            {
                "ok": True,
                "object_id": 77,
                "pos": [1004.0, 49.75, 2004.0],
            },
        )
        runtime.monotonic = clock.monotonic  # type: ignore[method-assign]
        runtime.sleep = clock.sleep  # type: ignore[method-assign]
        return runtime

    def test_spawn_settles_server_then_enqueues_once_and_awaits_exact_id(self) -> None:
        clock = FakeClock()
        client = SpawnProtocolClient(
            statuses=[_peer_status() for _ in range(5)]
            + [
                _peer_status(queue_depth=1),
                _peer_status(queue_depth=0),
                _peer_status(queue_depth=0),
            ],
            awaits=[
                {"status": "pending"},
                {"status": "pending"},
                {
                    "status": "done",
                    "result": {
                        "ok": True,
                        "object_id": 77,
                        "pos": [1004.0, 49.75, 2004.0],
                    },
                },
            ],
        )
        runtime = self.task9_spawn_protocol_runtime(clock)

        result = runtime.spawn(
            client,
            smoke.OBJECT_TYPE,
            [1004.0, 49.75, 2004.0],
            smoke.SPAWN_FLAGS,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(77, result["object_id"])
        self.assertEqual(
            [
                {
                    "cmd": "world_spawn",
                    "args": {
                        "type": smoke.OBJECT_TYPE,
                        "pos": [1004.0, 49.75, 2004.0],
                        "flags": smoke.SPAWN_FLAGS,
                    },
                    "peer": "server",
                    "operation_timeout_s": 30.0,
                }
            ],
            client.enqueue_calls,
        )
        evidence = result["command_observation"]
        self.assertEqual(901, evidence["command_id"])
        self.assertEqual("completed", evidence["classification"])
        self.assertTrue(evidence["server_settlement"]["ready"])
        self.assertEqual(3, len(evidence["observations"]))
        self.assertEqual(
            {
                "elapsed_s",
                "await_status",
                "last_poll_age_s",
                "queue_depth",
                "version_state",
                "daemon_generation",
            },
            set(evidence["observations"][0]),
        )
        await_calls = [
            call for call in client.request_calls if call["path"] == "/await"
        ]
        self.assertEqual(
            [
                {
                    "method": "GET",
                    "path": "/await",
                    "payload": None,
                    "query": {"id": 901, "remove": 1},
                }
            ]
            * 3,
            await_calls,
        )
        self.assertFalse(
            any(call["path"] not in ("/status", "/await") for call in client.request_calls)
        )

    def test_spawn_server_settlement_failure_stops_before_enqueue(self) -> None:
        clock = FakeClock()
        client = SpawnProtocolClient(
            statuses=[_peer_status(poll_age_s=12.0) for _ in range(128)],
            awaits=[],
        )
        runtime = self.task9_spawn_protocol_runtime(clock)

        result = runtime.spawn(
            client,
            smoke.OBJECT_TYPE,
            [1004.0, 49.75, 2004.0],
            smoke.SPAWN_FLAGS,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("server_peer_settlement_failed", result["error"])
        self.assertEqual([], client.enqueue_calls)
        self.assertEqual(None, result["command_observation"]["command_id"])
        self.assertFalse(result["command_observation"]["server_settlement"]["ready"])

    def test_spawn_server_settlement_rejects_queue_version_and_generation_drift(self) -> None:
        cases = (
            ("queue", [_peer_status(queue_depth=1) for _ in range(128)]),
            (
                "version",
                [_peer_status(version_state="legacy") for _ in range(128)],
            ),
            (
                "generation",
                [_peer_status(generation="generation-a")]
                + [_peer_status(generation="generation-b") for _ in range(127)],
            ),
        )
        for label, statuses in cases:
            with self.subTest(case=label):
                clock = FakeClock()
                client = SpawnProtocolClient(statuses=statuses, awaits=[])
                runtime = self.task9_spawn_protocol_runtime(clock)

                result = runtime.spawn(
                    client,
                    smoke.OBJECT_TYPE,
                    [1004.0, 49.75, 2004.0],
                    smoke.SPAWN_FLAGS,
                )

                self.assertFalse(result["ok"])
                self.assertEqual("server_peer_settlement_failed", result["error"])
                self.assertEqual([], client.enqueue_calls)

    def test_spawn_timeout_classifies_bounded_exact_id_observations(self) -> None:
        cases = (
            (
                "not-dispatched",
                [_peer_status(queue_depth=1) for _ in range(64)],
                "spawn_not_dispatched_before_deadline",
            ),
            (
                "post-dispatch-stalled",
                [_peer_status(queue_depth=1)]
                + [
                    _peer_status(queue_depth=0, poll_age_s=12.0)
                    for _ in range(63)
                ],
                "spawn_post_dispatch_peer_stalled",
            ),
            (
                "live-peer-result-missing",
                [_peer_status(queue_depth=1)]
                + [_peer_status(queue_depth=0) for _ in range(63)],
                "spawn_result_missing_with_live_peer",
            ),
        )
        for label, observations, expected in cases:
            with self.subTest(case=label):
                clock = FakeClock()
                client = SpawnProtocolClient(
                    statuses=[_peer_status() for _ in range(5)] + observations,
                    awaits=[{"status": "pending"} for _ in range(64)],
                )
                runtime = self.task9_spawn_protocol_runtime(clock)

                result = runtime.spawn(
                    client,
                    smoke.OBJECT_TYPE,
                    [1004.0, 49.75, 2004.0],
                    smoke.SPAWN_FLAGS,
                )

                self.assertFalse(result["ok"])
                self.assertEqual(expected, result["error"])
                evidence = result["command_observation"]
                self.assertEqual(expected, evidence["classification"])
                self.assertEqual(901, evidence["command_id"])
                self.assertLessEqual(len(evidence["observations"]), 64)
                self.assertEqual(1, len(client.enqueue_calls))
                self.assertTrue(
                    all(
                        call["query"] == {"id": 901, "remove": 1}
                        for call in client.request_calls
                        if call["path"] == "/await"
                    )
                )

    def test_spawn_late_result_with_generation_drift_is_fail_closed_but_keeps_id(self) -> None:
        clock = FakeClock()
        client = SpawnProtocolClient(
            statuses=[_peer_status() for _ in range(5)]
            + [_peer_status(queue_depth=0, generation="generation-b")],
            awaits=[
                {
                    "status": "done",
                    "result": {
                        "ok": True,
                        "object_id": 77,
                        "pos": [1004.0, 49.75, 2004.0],
                    },
                }
            ],
        )
        runtime = self.task9_spawn_protocol_runtime(clock)

        result = runtime.spawn(
            client,
            smoke.OBJECT_TYPE,
            [1004.0, 49.75, 2004.0],
            smoke.SPAWN_FLAGS,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("spawn_observation_incomplete", result["error"])
        self.assertEqual(77, result["object_id"])
        self.assertEqual(901, result["command_observation"]["command_id"])
        self.assertEqual(
            "daemon_generation_drift",
            result["command_observation"]["observation_error"],
        )

    def test_spawn_observation_http_error_fails_closed_without_second_enqueue(self) -> None:
        clock = FakeClock()

        class AwaitErrorClient(SpawnProtocolClient):
            def request_json(
                self,
                method: str,
                path: str,
                payload: dict[str, object] | None = None,
                query: dict[str, object] | None = None,
            ) -> dict[str, object]:
                if method == "GET" and path == "/await":
                    self.request_calls.append(
                        {
                            "method": method,
                            "path": path,
                            "payload": payload,
                            "query": dict(query or {}),
                        }
                    )
                    raise ConnectionError("fixture_await_failed")
                return super().request_json(method, path, payload, query)

        client = AwaitErrorClient(
            statuses=[_peer_status() for _ in range(6)],
            awaits=[],
        )
        runtime = self.task9_spawn_protocol_runtime(clock)

        result = runtime.spawn(
            client,
            smoke.OBJECT_TYPE,
            [1004.0, 49.75, 2004.0],
            smoke.SPAWN_FLAGS,
        )

        self.assertFalse(result["ok"])
        self.assertEqual("spawn_observation_incomplete", result["error"])
        self.assertEqual(1, len(client.enqueue_calls))
        self.assertEqual(901, result["command_observation"]["command_id"])
        self.assertEqual(
            "await_request_ConnectionError",
            result["command_observation"]["observation_error"],
        )

    def test_collect_preserves_spawn_protocol_evidence_and_cleans_known_object(self) -> None:
        runtime = FakeRuntime()
        protocol_evidence = {
            "command_id": 901,
            "classification": "spawn_observation_incomplete",
            "observation_error": "daemon_generation_drift",
            "server_settlement": {"ready": True, "daemon_generation": "generation-a"},
            "observations": [
                {
                    "elapsed_s": 0.0,
                    "await_status": "done",
                    "last_poll_age_s": 0.1,
                    "queue_depth": 0,
                    "version_state": "ok",
                    "daemon_generation": "generation-b",
                }
            ],
        }
        runtime.spawn_result = {
            "ok": False,
            "error": "spawn_observation_incomplete",
            "object_id": 77,
            "pos": [1004.0, 49.75, 2004.0],
            "command_observation": protocol_evidence,
        }

        outcome = smoke.collect(
            self.config("spawn-protocol-evidence"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(901, outcome.payload["spawn"]["command_id"])
        self.assertEqual(
            "spawn_observation_incomplete",
            outcome.payload["spawn"]["classification"],
        )
        self.assertEqual(
            protocol_evidence["server_settlement"],
            outcome.payload["spawn"]["server_settlement"],
        )
        self.assertEqual(
            protocol_evidence["observations"],
            outcome.payload["spawn"]["command_observations"],
        )
        self.assertNotIn("protocol", outcome.payload["spawn"])
        self.assertEqual([77], runtime.cleanup_calls)
        self.assertEqual("DELETED_AND_ABSENT", outcome.payload["cleanup"]["outcome"])
        self.assertFalse(outcome.payload["automatic_retry_blocked"])

    def test_collect_normal_path_preserves_direct_spawn_command_evidence(self) -> None:
        runtime = FakeRuntime()
        protocol_evidence = {
            "command_id": 902,
            "classification": "completed",
            "server_settlement": {"ready": True, "daemon_generation": "generation-a"},
            "observations": [
                {
                    "elapsed_s": 0.0,
                    "await_status": "done",
                    "last_poll_age_s": 0.1,
                    "queue_depth": 0,
                    "version_state": "ok",
                    "daemon_generation": "generation-a",
                }
            ],
        }
        runtime.spawn_result = {
            "ok": True,
            "object_id": 77,
            "pos": [1004.0, 49.75, 2004.0],
            "command_observation": protocol_evidence,
        }

        outcome = smoke.collect(
            self.config("spawn-protocol-normal"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(902, outcome.payload["spawn"]["command_id"])
        self.assertEqual("completed", outcome.payload["spawn"]["classification"])
        self.assertEqual(
            protocol_evidence["observations"],
            outcome.payload["spawn"]["command_observations"],
        )

    def test_capture_paths_magic_and_registered_format_are_real_jpeg(self) -> None:
        outcome, _, _, _ = self.run_success("jpeg-evidence")

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        for evidence in outcome.payload["evidence"]["views"].values():
            path = pathlib.Path(evidence["path"])
            data = path.read_bytes()
            self.assertEqual(".jpg", path.suffix)
            self.assertEqual("JPEG", evidence["format"])
            self.assertTrue(data.startswith(b"\xff\xd8\xff"))
            self.assertTrue(data.endswith(b"\xff\xd9"))
            with Image.open(path) as image:
                self.assertEqual("JPEG", image.format)
                self.assertGreater(image.width, 0)
                self.assertGreater(image.height, 0)
                image.verify()

    def test_non_jpeg_capture_magic_is_rejected(self) -> None:
        runtime = FakeRuntime()
        runtime.capture_invalid_magic = True

        outcome = smoke.collect(
            self.config("invalid-jpeg"), SequenceProvider(_owned_snapshot()), lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("capture_format_invalid", outcome.payload["stop_reason"])

    def test_corrupt_jpeg_body_is_rejected_during_collection(self) -> None:
        runtime = FakeRuntime()
        runtime.capture_corrupt_jpeg_body = True

        outcome = smoke.collect(
            self.config("corrupt-jpeg-collect"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("capture_format_invalid", outcome.payload["stop_reason"])

    def test_corrupt_jpeg_body_is_rejected_during_finalize_even_with_matching_hash(self) -> None:
        outcome, config, _, _ = self.run_success("corrupt-jpeg-finalize")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        front = payload["evidence"]["views"]["front"]
        front_path = pathlib.Path(front["path"])
        front_path.write_bytes(b"\xff\xd8\xff\xe0corrupt-body\xff\xd9")
        front["sha256"] = _sha256(front_path)
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "PASS")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_rear_right_is_mandatory_for_finalize(self) -> None:
        outcome, config, _, _ = self.run_success("missing-rear-right")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        del payload["evidence"]["views"]["rear_right"]
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "PASS")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
        self.assertIn("eight_view_evidence", finalized.payload["stop_reason"])

    def test_hold_uses_monotonic_for_thirty_seconds_and_owned_loss_never_passes(self) -> None:
        clock = FakeClock()
        runtime = FakeRuntime(clock)
        outcome, config, _, _ = self.run_success("hold-ok", runtime=runtime)
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertGreaterEqual(outcome.payload["hold"]["elapsed_seconds"], 30.0)
        self.assertEqual(5.0, outcome.payload["hold"]["sample_interval_seconds"])
        sample_times = [
            sample["elapsed_seconds"] for sample in outcome.payload["hold"]["samples"]
        ]
        self.assertGreaterEqual(sample_times[-1] - sample_times[0], 30.0)
        self.assertTrue(all(0.0 < later - earlier <= 5.5 for earlier, later in zip(sample_times, sample_times[1:])))
        finalized = self.finalize(config, "PASS")
        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
        self.assertGreaterEqual(clock.monotonic_calls, 2)
        self.assertTrue(clock.sleeps)

        provider = SequenceProvider(
            _owned_snapshot(),
            _owned_snapshot(),
            _owned_snapshot(),
            _foreign_snapshot(),
        )
        failed, _, _, failed_runtime = self.run_success(
            "hold-loss", provider=provider, runtime=FakeRuntime()
        )
        self.assertEqual(smoke.EXIT_STOP, failed.exit_code)
        self.assertNotEqual("PASS", failed.payload["result"])
        self.assertEqual([77], failed_runtime.cleanup_calls)
        self.assertGreaterEqual(len(failed.payload["hold"]["samples"]), 2)
        self.assertFalse(failed.payload["hold"]["samples"][-1]["ok"])

    def test_hold_accounts_for_measured_provider_cost_and_remains_finalizable(self) -> None:
        clock = FakeClock()
        provider = CostedSequenceProvider(clock, (2.5, 1.3), _owned_snapshot())
        runtime = FakeRuntime(clock)
        config = self.config("costed-hold")
        config.hold_interval = 4.0

        outcome = smoke.collect(
            config,
            provider,
            lambda _: runtime,
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        hold = outcome.payload["hold"]
        required_sample_fields = {
            "snapshot_started_elapsed",
            "snapshot_duration_seconds",
            "elapsed_seconds",
        }
        self.assertTrue(
            all(required_sample_fields.issubset(sample) for sample in hold["samples"])
        )
        sample_times = [sample["elapsed_seconds"] for sample in hold["samples"]]
        self.assertAlmostEqual(
            sample_times[-1] - sample_times[0], hold["observed_span_seconds"], places=3
        )
        self.assertGreaterEqual(hold["observed_span_seconds"], 30.0)
        self.assertGreater(hold["elapsed_seconds"], hold["observed_span_seconds"])
        for previous, current in zip(hold["samples"], hold["samples"][1:]):
            self.assertLessEqual(
                current["elapsed_seconds"] - previous["elapsed_seconds"],
                hold["sample_interval_seconds"]
                + current["snapshot_duration_seconds"]
                + smoke.HOLD_SCHEDULER_JITTER_SECONDS,
            )
        finalized = self.finalize(config, "PASS")
        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)

    def test_finalize_accepts_observed_scheduler_overhead_but_rejects_excessive_jitter(self) -> None:
        for name, scheduler_overhead, expected_exit in (
            ("observed", 0.067, smoke.EXIT_OK),
            ("excessive", 0.200, smoke.EXIT_STOP),
        ):
            with self.subTest(scheduler_overhead=scheduler_overhead):
                outcome, config, _, _ = self.run_success(f"hold-jitter-{name}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                template = payload["hold"]["samples"][0]
                target_elapsed = 17.600 + 1.0 + 3.605 + scheduler_overhead
                sample_times = (
                    0.000,
                    4.400,
                    8.800,
                    13.200,
                    17.600,
                    target_elapsed,
                    26.472,
                    30.657,
                )
                sample_durations = (
                    0.000,
                    3.400,
                    3.400,
                    3.400,
                    3.400,
                    3.605,
                    26.472 - target_elapsed - 1.0,
                    3.185,
                )
                payload["hold"].update(
                    sample_interval_seconds=1.0,
                    elapsed_seconds=30.657,
                    observed_span_seconds=30.657,
                    samples=[
                        {
                            **template,
                            "snapshot_started_elapsed": round(elapsed - duration, 3),
                            "snapshot_duration_seconds": round(duration, 3),
                            "elapsed_seconds": round(elapsed, 3),
                        }
                        for elapsed, duration in zip(sample_times, sample_durations)
                    ],
                )
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(
                    expected_exit,
                    finalized.exit_code,
                    finalized.payload.get("stop_reason"),
                )
                if scheduler_overhead == 0.200:
                    self.assertIn(
                        "hold_sample_cadence_invalid", finalized.payload["stop_reason"]
                    )

    def test_finalize_rejects_missing_or_invalid_measured_snapshot_timing(self) -> None:
        mutations = {
            "missing_started": lambda payload: payload["hold"]["samples"][0].pop(
                "snapshot_started_elapsed", None
            ),
            "missing_duration": lambda payload: payload["hold"]["samples"][0].pop(
                "snapshot_duration_seconds", None
            ),
            "missing_observed_span": lambda payload: payload["hold"].pop(
                "observed_span_seconds", None
            ),
            "negative_duration": lambda payload: payload["hold"]["samples"][0].update(
                snapshot_duration_seconds=-1.0
            ),
            "duration_after_elapsed": lambda payload: payload["hold"]["samples"][0].update(
                snapshot_duration_seconds=payload["hold"]["samples"][0]["elapsed_seconds"]
                + 1.0
            ),
            "observed_span_mismatch": lambda payload: payload["hold"].update(
                observed_span_seconds=0.0
            ),
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            with self.subTest(timing=name):
                outcome, config, _, _ = self.run_success(f"snapshot-timing-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                mutate(payload)
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_finalize_rejects_snapshot_duration_inconsistent_with_its_timestamps(self) -> None:
        outcome, config, _, _ = self.run_success("inconsistent-snapshot-duration")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        first = payload["hold"]["samples"][0]
        second = payload["hold"]["samples"][-1]
        first.update(
            snapshot_started_elapsed=0.0,
            snapshot_duration_seconds=0.0,
            elapsed_seconds=0.0,
        )
        second.update(
            snapshot_started_elapsed=29.0,
            snapshot_duration_seconds=30.0,
            elapsed_seconds=30.0,
        )
        payload["hold"].update(
            samples=[first, second],
            elapsed_seconds=30.0,
            observed_span_seconds=30.0,
        )
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "PASS")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_capture_failure_preserves_partial_views_and_full_stop_context(self) -> None:
        runtime = FakeRuntime()
        runtime.capture_error_view = "left"
        config = self.config("partial-capture")

        outcome = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: runtime)

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual({"front", "rear"}, set(outcome.payload["evidence"]["views"]))
        for section in (
            "readiness",
            "raycast",
            "spawn",
            "hold",
            "cleanup",
            "artifacts",
            "ownership",
            "visual_review_contract",
        ):
            self.assertIn(section, outcome.payload)
        self.assertEqual(
            {"server", "client"},
            {record["source"] for record in outcome.payload["evidence"]["logs"]},
        )

    def test_publish_snapshot_is_distinct_validated_and_taken_after_cleanup(self) -> None:
        events: list[str] = []
        hold_snapshot = _owned_snapshot()
        publish_snapshot = smoke.OwnershipSnapshot(
            processes=(
                *hold_snapshot.processes,
                _process(909, "C:\\Windows\\System32\\notepad.exe", "notepad.exe"),
            ),
            ports=hold_snapshot.ports,
        )
        provider = SequenceProvider(
            *([hold_snapshot] * 9),
            publish_snapshot,
            events=events,
        )
        runtime = FakeRuntime(events=events)

        outcome = smoke.collect(self.config("publish-snapshot"), provider, lambda _: runtime)

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        ownership = outcome.payload["ownership"]
        self.assertNotIn("final_snapshot", ownership)
        hold_pids = {item["pid"] for item in ownership["hold_final_snapshot"]["processes"]}
        publish_pids = {item["pid"] for item in ownership["publish_final_snapshot"]["processes"]}
        self.assertNotIn(909, hold_pids)
        self.assertIn(909, publish_pids)
        self.assertEqual(["cleanup", "telemetry", "snapshot"], events[-3:])

    def test_owned_process_loss_after_cleanup_before_publication_is_stop(self) -> None:
        provider = SequenceProvider(*([_owned_snapshot()] * 9), _foreign_snapshot())
        runtime = FakeRuntime()

        outcome = smoke.collect(
            self.config("publish-owner-loss"), provider, lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual([77], runtime.cleanup_calls)
        self.assertIn("ownership_changed_before_publication", outcome.payload["stop_reason"])
        self.assertIn("hold_final_snapshot", outcome.payload["ownership"])
        self.assertIn("publish_final_snapshot", outcome.payload["ownership"])

    def test_stop_collects_best_effort_logs_before_publish_snapshot(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(events=events)
        runtime.capture_error_view = "left"
        provider = SequenceProvider(_owned_snapshot(), events=events)

        outcome = smoke.collect(
            self.config("stop-publish-order"), provider, lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(["cleanup", "telemetry", "logs", "snapshot"], events[-4:])

    def test_host_or_pbo_hash_drift_stops_before_client(self) -> None:
        for artifact_name in ("host_p3d", "pbo"):
            with self.subTest(artifact=artifact_name):
                config = self.config(f"drift-{artifact_name}")
                spec = config.artifacts[artifact_name]
                config.artifacts[artifact_name] = smoke.ArtifactSpec(spec.path, "0" * 64)
                runtime = FakeRuntime()
                outcome = smoke.collect(
                    config, SequenceProvider(_owned_snapshot()), lambda _: runtime
                )
                self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
                self.assertIn("hash_drift", outcome.payload["stop_reason"])
                self.assertEqual(0, runtime.client_factory_calls)

    def test_collect_publishes_atomically_and_never_auto_passes(self) -> None:
        config = self.config("atomic-collect")
        replace = smoke.os.replace
        with mock.patch.object(smoke.os, "replace", wraps=replace) as replace_spy:
            outcome = smoke.collect(
                config, SequenceProvider(_owned_snapshot()), lambda _: FakeRuntime()
            )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual("STOP", outcome.payload["result"])
        self.assertEqual("NOT_EVALUATED", outcome.payload["model_verdict"])
        self.assertEqual("COMPLETE", outcome.payload["collection_status"])
        self.assertTrue(config.verdict_path.is_file())
        self.assertEqual(1, replace_spy.call_count)
        self.assertEqual([], list(config.output_dir.glob("*.tmp")))

    def test_baseline_pin_build_id_and_default_artifacts_exclude_build_a_history(self) -> None:
        self.assertEqual(
            "6DB34BDCA308F68829B1E758688EFAB380AA68094957E101676AF3C14C589911",
            smoke.DEFAULT_EXPECTED_HASHES["pbo"],
        )
        self.assertEqual({"host_p3d", "pbo"}, set(smoke.DEFAULT_EXPECTED_HASHES))
        self.assertEqual({"host_p3d", "pbo"}, set(smoke._default_artifacts()))
        self.assertFalse(
            any("BUILD_A" in specification["visual_gate"] for specification in smoke.VIEW_SPECS.values())
        )
        outcome, _, _, _ = self.run_success("baseline-build-id")
        self.assertEqual("BASELINE_ROLLBACK_6DB34BDC", outcome.payload["build_id"])

    def test_finalize_accepts_only_pass_or_falsified_and_atomically_updates_same_verdict(self) -> None:
        for decision in ("PASS", "FALSIFIED"):
            with self.subTest(decision=decision):
                outcome, config, _, _ = self.run_success(f"finalize-{decision.lower()}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                replace = smoke.os.replace
                with mock.patch.object(smoke.os, "replace", wraps=replace) as replace_spy:
                    finalized = self.finalize(config, decision)
                self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
                self.assertEqual(decision, finalized.payload["model_verdict"])
                self.assertEqual(decision, finalized.payload["result"])
                self.assertEqual(1, replace_spy.call_count)
                self.assertEqual(
                    decision,
                    json.loads(config.verdict_path.read_text(encoding="utf-8"))["result"],
                )

        invalid, config, _, _ = self.run_success("finalize-invalid")
        self.assertEqual(smoke.EXIT_OK, invalid.exit_code)
        rejected = self.finalize(config, "NOT_EVALUATED")
        self.assertEqual(smoke.EXIT_USAGE, rejected.exit_code)

    def test_collect_snapshots_artifacts_and_finalize_survives_live_rollback(self) -> None:
        outcome, config, _, _ = self.run_success("artifact-snapshot-live-rollback")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        snapshot_root = (config.output_dir / "evidence" / "artifacts").resolve()
        for name, evidence in outcome.payload["artifacts"].items():
            snapshot_path = pathlib.Path(evidence["path"]).resolve()
            self.assertTrue(snapshot_path.is_relative_to(snapshot_root), name)
            self.assertTrue(snapshot_path.is_file(), name)
            self.assertEqual(str(config.artifacts[name].path.resolve()), evidence["live_path"])
            self.assertEqual(evidence["sha256"], _sha256(snapshot_path))

        self.host.write_bytes(b"baseline host restored after collect")
        self.pbo.write_bytes(b"baseline pbo restored after collect")

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
        self.assertEqual("FALSIFIED", finalized.payload["result"])

    def test_finalize_rejects_mutated_artifact_snapshot(self) -> None:
        outcome, config, _, _ = self.run_success("mutated-artifact-snapshot")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        snapshot_root = (config.output_dir / "evidence" / "artifacts").resolve()
        snapshot_path = pathlib.Path(outcome.payload["artifacts"]["pbo"]["path"]).resolve()
        self.assertTrue(snapshot_path.is_relative_to(snapshot_root))

        with snapshot_path.open("ab") as handle:
            handle.write(b"snapshot mutation\n")

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
        self.assertIn("artifact_hash_drift:pbo", finalized.payload["stop_reason"])

    def test_finalize_accepts_dayz_integer_true_in_cleanup_response(self) -> None:
        outcome, config, _, _ = self.run_success("finalize-dayz-integer-true")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        payload["cleanup"]["response"]["ok"] = 1
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
        self.assertEqual("FALSIFIED", finalized.payload["model_verdict"])

    def test_falsified_selects_rollback_without_modifying_artifacts(self) -> None:
        outcome, config, _, _ = self.run_success("rollback")
        before = {name: _sha256(spec.path) for name, spec in config.artifacts.items()}

        finalized = self.finalize(config, "FALSIFIED")

        after = {name: _sha256(spec.path) for name, spec in config.artifacts.items()}
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
        self.assertEqual(before, after)
        self.assertEqual("SELECTED", finalized.payload["rollback"]["decision"])
        self.assertFalse(finalized.payload["rollback"]["files_modified"])

    def test_malformed_or_incomplete_verdict_fails_closed(self) -> None:
        malformed = self.root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        result = smoke.finalize(malformed, "PASS")
        self.assertEqual(smoke.EXIT_STOP, result.exit_code)

        incomplete = self.root / "incomplete.json"
        incomplete.write_text(json.dumps({"owner": smoke.VERDICT_OWNER}), encoding="utf-8")
        result = smoke.finalize(incomplete, "PASS")
        self.assertEqual(smoke.EXIT_STOP, result.exit_code)

    def test_finalize_rejects_each_missing_or_false_load_bearing_invariant(self) -> None:
        mutations = {
            "build_id_missing": lambda payload: payload.pop("build_id"),
            "build_id_wrong": lambda payload: payload.update(build_id="BUILD_A"),
            "readiness_missing": lambda payload: payload.pop("readiness"),
            "readiness_false": lambda payload: payload["readiness"].update(inworld=False),
            "raycast_missing": lambda payload: payload.pop("raycast"),
            "raycast_no_hit": lambda payload: payload["raycast"]["raycast"].update(hit=False),
            "spawn_missing": lambda payload: payload.pop("spawn"),
            "spawn_flags": lambda payload: payload["spawn"].update(flags=0),
            "spawn_identity": lambda payload: payload["spawn"].update(object_id=0),
            "isolation_missing": lambda payload: payload.pop("isolation"),
            "isolation_false": lambda payload: payload["isolation"].update(pre_spawn_absent=False),
            "unique_missing": lambda payload: payload.pop("unique_vehicle"),
            "unique_false": lambda payload: payload["unique_vehicle"].update(verified=False),
            "fixture_missing": lambda payload: payload.pop("fixture"),
            "fixture_not_ready": lambda payload: payload["fixture"].update(vehicle_fixture_ready=False),
            "fixture_wheel_count": lambda payload: payload["fixture"].update(wheel_count=3),
            "fixture_wheel_items": lambda payload: payload["fixture"].update(wheel_item_count=3),
            "hold_required_short": lambda payload: payload["hold"].update(required_seconds=29.9),
            "hold_elapsed_short": lambda payload: payload["hold"].update(elapsed_seconds=29.9),
            "hold_samples_empty": lambda payload: payload["hold"].update(samples=[]),
            "hold_sample_not_owned": lambda payload: payload["hold"]["samples"][0].update(ok=False),
            "cleanup_false": lambda payload: payload["cleanup"].update(ok=False),
            "cleanup_deleted_zero": lambda payload: payload["cleanup"].update(deleted=0),
            "cleanup_post_delete_found": lambda payload: payload["cleanup"].update(post_delete_absent=False),
            "ownership_missing": lambda payload: payload.pop("ownership"),
            "ownership_pid_mismatch": lambda payload: payload["ownership"].update(server_pid=999),
            "visual_contract_missing": lambda payload: payload.pop("visual_review_contract"),
            "visual_contract_auto_pass": lambda payload: payload["visual_review_contract"].update(
                auto_pass_forbidden=False
            ),
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            with self.subTest(invariant=name):
                outcome, config, _, _ = self.run_success(f"finalize-invariant-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                mutate(payload)
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
                self.assertNotEqual("PASS", finalized.payload["result"])

    def test_finalize_requires_exact_false_retry_block_and_coherent_cleanup(self) -> None:
        mutations = {
            "retry_missing": lambda payload: payload.pop("automatic_retry_blocked"),
            "retry_true": lambda payload: payload.update(automatic_retry_blocked=True),
            "retry_integer_zero": lambda payload: payload.update(automatic_retry_blocked=0),
            "retry_string_false": lambda payload: payload.update(automatic_retry_blocked="false"),
            "cleanup_not_attempted": lambda payload: payload["cleanup"].update(attempted=False),
            "cleanup_not_ok": lambda payload: payload["cleanup"].update(ok=False),
            "cleanup_wrong_outcome": lambda payload: payload["cleanup"].update(
                outcome="DELETE_FAILED"
            ),
            "cleanup_possible_orphan": lambda payload: payload["cleanup"].update(
                possible_orphan=True
            ),
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            with self.subTest(invariant=name):
                outcome, config, _, _ = self.run_success(f"retry-cleanup-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                mutate(payload)
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_finalize_requires_server_and_client_logs_with_live_paths_and_hashes(self) -> None:
        valid, valid_config, _, _ = self.run_success("valid-log-contract")
        self.assertEqual(smoke.EXIT_OK, valid.exit_code)
        valid_logs = valid.payload["evidence"]["logs"]
        self.assertEqual({"server", "client"}, {record["source"] for record in valid_logs})
        for record in valid_logs:
            self.assertTrue(pathlib.Path(record["profile_path"]).is_dir())
            self.assertTrue(pathlib.Path(record["path"]).is_file())
            self.assertEqual(record["sha256"], _sha256(pathlib.Path(record["path"])))
        self.assertEqual(smoke.EXIT_OK, self.finalize(valid_config, "PASS").exit_code)

        for index, mutation in enumerate(
            ("empty", "missing_client", "missing_profile", "missing_log", "hash_drift")
        ):
            with self.subTest(logs=mutation):
                outcome, config, _, _ = self.run_success(f"invalid-logs-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                logs = payload["evidence"]["logs"]
                if mutation == "empty":
                    payload["evidence"]["logs"] = []
                elif mutation == "missing_client":
                    payload["evidence"]["logs"] = [
                        record for record in logs if record["source"] == "server"
                    ]
                elif mutation == "missing_profile":
                    logs[0]["profile_path"] = str(self.root / "missing-profile")
                elif mutation == "missing_log":
                    logs[0]["path"] = str(self.root / "missing-server.RPT")
                else:
                    pathlib.Path(logs[0]["path"]).write_text("drift\n", encoding="utf-8")
                if mutation != "hash_drift":
                    smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_collect_snapshots_logs_and_finalize_ignores_live_source_growth(self) -> None:
        config = self.config("immutable-log-snapshots")
        runtime = FakeRuntime()
        source_paths: dict[str, pathlib.Path] = {}
        source_bytes: dict[str, bytes] = {}
        runtime.log_records_override = []
        for source, profile_path in (
            ("server", self.server_profiles),
            ("client", self.client_profiles),
        ):
            profile_path.mkdir(parents=True, exist_ok=True)
            source_path = profile_path / f"{source}.RPT"
            source_path.write_text(f"{source} live log\n", encoding="utf-8")
            source_paths[source] = source_path
            source_bytes[source] = source_path.read_bytes()
            runtime.log_records_override.append(
                {
                    "source": source,
                    "profile_path": str(profile_path.resolve()),
                    "path": str(source_path.resolve()),
                    "sha256": _sha256(source_path),
                }
            )

        outcome = smoke.collect(
            config, SequenceProvider(_owned_snapshot()), lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        snapshot_root = (config.output_dir / "evidence" / "logs").resolve()
        for record in outcome.payload["evidence"]["logs"]:
            source = record["source"]
            snapshot_path = pathlib.Path(record["path"]).resolve()
            self.assertTrue(snapshot_path.is_relative_to(snapshot_root))
            self.assertTrue(snapshot_path.is_file())
            self.assertEqual(record["sha256"], _sha256(snapshot_path))
            self.assertEqual(source_bytes[source], source_paths[source].read_bytes())

        for source_path in source_paths.values():
            with source_path.open("ab") as handle:
                handle.write(b"source kept growing after collect\n")

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_OK, finalized.exit_code)
        self.assertEqual("FALSIFIED", finalized.payload["result"])

    def test_finalize_rejects_mutated_log_snapshot(self) -> None:
        outcome, config, _, _ = self.run_success("mutated-log-snapshot")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        snapshot_root = (config.output_dir / "evidence" / "logs").resolve()
        snapshot_path = pathlib.Path(outcome.payload["evidence"]["logs"][0]["path"]).resolve()
        self.assertTrue(snapshot_path.is_relative_to(snapshot_root))

        with snapshot_path.open("ab") as handle:
            handle.write(b"snapshot mutation\n")

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
        self.assertIn("log_hash_drift", finalized.payload["stop_reason"])

    def test_finalize_rejects_existing_live_path_outside_trusted_profile(self) -> None:
        for source in ("server", "client"):
            with self.subTest(source=source):
                outcome, config, _, _ = self.run_success(f"untrusted-live-path-{source}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                record = next(
                    item for item in payload["evidence"]["logs"] if item["source"] == source
                )
                snapshot_path = pathlib.Path(record["path"])
                self.assertTrue(snapshot_path.is_file())
                self.assertEqual(record["sha256"], _sha256(snapshot_path))
                outside_live_path = self.root / "outside-live" / source / "live.RPT"
                outside_live_path.parent.mkdir(parents=True, exist_ok=True)
                outside_live_path.write_text(f"outside {source}\n", encoding="utf-8")
                record["live_path"] = str(outside_live_path.resolve())
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "FALSIFIED")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
                self.assertEqual(
                    f"log_hash_drift:{source}",
                    finalized.payload["stop_reason"],
                )

    def test_finalize_rejects_valid_snapshot_copied_outside_verdict_evidence_logs(self) -> None:
        for source in ("server", "client"):
            with self.subTest(source=source):
                outcome, config, _, _ = self.run_success(f"external-log-snapshot-{source}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                record = next(
                    item for item in payload["evidence"]["logs"] if item["source"] == source
                )
                original_snapshot = pathlib.Path(record["path"])
                outside_snapshot = self.root / "outside-snapshots" / source / "snapshot.RPT"
                outside_snapshot.parent.mkdir(parents=True, exist_ok=True)
                outside_snapshot.write_bytes(original_snapshot.read_bytes())
                record["path"] = str(outside_snapshot.resolve())
                record["sha256"] = _sha256(outside_snapshot)
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "FALSIFIED")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
                self.assertEqual(
                    f"log_hash_drift:{source}",
                    finalized.payload["stop_reason"],
                )

    def test_collect_publishes_exact_trusted_live_log_provenance(self) -> None:
        config = self.config("trusted-live-log-provenance")
        runtime = FakeRuntime()
        source_paths: dict[str, pathlib.Path] = {}
        runtime.log_records_override = []
        for source, profile_path in (
            ("server", self.server_profiles),
            ("client", self.client_profiles),
        ):
            live_dir = profile_path / "nested"
            live_dir.mkdir(parents=True, exist_ok=True)
            live_path = live_dir / f"{source}.RPT"
            live_path.write_text(f"{source} provenance\n", encoding="utf-8")
            source_paths[source] = live_path.resolve()
            runtime.log_records_override.append(
                {
                    "source": source,
                    "profile_path": str(profile_path.resolve()),
                    "path": str(live_path.resolve()),
                    "sha256": _sha256(live_path),
                }
            )

        outcome = smoke.collect(
            config, SequenceProvider(_owned_snapshot()), lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        for record in outcome.payload["evidence"]["logs"]:
            self.assertIn("live_path", record)
            live_path = pathlib.Path(record["live_path"]).resolve()
            profile_path = pathlib.Path(record["profile_path"]).resolve()
            self.assertEqual(source_paths[record["source"]], live_path)
            self.assertTrue(live_path.is_relative_to(profile_path))

    def test_same_log_basename_from_distinct_profile_subdirs_gets_distinct_snapshots(self) -> None:
        config = self.config("same-log-basename")
        runtime = FakeRuntime()
        runtime.log_records_override = []
        live_paths: dict[str, pathlib.Path] = {}
        basename = "DayZ_x64_2026-07-12_00-00-00.RPT"
        for source, profile_path, subdir in (
            ("server", self.server_profiles, "server-session"),
            ("client", self.client_profiles, "client-session"),
        ):
            live_dir = profile_path / subdir
            live_dir.mkdir(parents=True, exist_ok=True)
            live_path = live_dir / basename
            live_path.write_text(f"distinct {source} content\n", encoding="utf-8")
            live_paths[source] = live_path.resolve()
            runtime.log_records_override.append(
                {
                    "source": source,
                    "profile_path": str(profile_path.resolve()),
                    "path": str(live_path.resolve()),
                    "sha256": _sha256(live_path),
                }
            )

        outcome = smoke.collect(
            config, SequenceProvider(_owned_snapshot()), lambda _: runtime
        )

        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        snapshot_root = (config.output_dir / "evidence" / "logs").resolve()
        records = {record["source"]: record for record in outcome.payload["evidence"]["logs"]}
        snapshot_paths = {
            source: pathlib.Path(record["path"]).resolve()
            for source, record in records.items()
        }
        self.assertNotEqual(snapshot_paths["server"], snapshot_paths["client"])
        for source, snapshot_path in snapshot_paths.items():
            self.assertTrue(snapshot_path.is_relative_to(snapshot_root))
            self.assertEqual(records[source]["sha256"], _sha256(snapshot_path))
            self.assertEqual(_sha256(live_paths[source]), _sha256(snapshot_path))

    def test_finalize_rejects_two_log_records_reusing_one_snapshot(self) -> None:
        outcome, config, _, _ = self.run_success("reused-log-snapshot")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        logs = payload["evidence"]["logs"]
        logs[1]["path"] = logs[0]["path"]
        logs[1]["sha256"] = logs[0]["sha256"]
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "FALSIFIED")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
        self.assertIn("log_snapshot_path_reused", finalized.payload["stop_reason"])

    def test_finalize_rejects_self_declared_log_roots_outside_trusted_contract(self) -> None:
        outcome, config, _, _ = self.run_success("self-declared-log-roots")
        self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
        payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
        record = payload["evidence"]["logs"][0]
        profile_path = self.root / "untrusted" / record["source"] / "profiles"
        profile_path.mkdir(parents=True, exist_ok=True)
        live_path = profile_path / "live.RPT"
        live_path.write_text("untrusted live provenance\n", encoding="utf-8")
        record["profile_path"] = str(profile_path.resolve())
        record["live_path"] = str(live_path.resolve())
        smoke.atomic_write_json(config.verdict_path, payload)

        finalized = self.finalize(config, "PASS")

        self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)
        self.assertIn("log_hash_drift", finalized.payload["stop_reason"])

    def test_finalize_rejects_truncated_nonfinite_or_nonmonotonic_hold_series(self) -> None:
        def one_sample(payload: dict) -> None:
            payload["hold"]["samples"] = [payload["hold"]["samples"][-1]]

        def nonfinite_required(payload: dict) -> None:
            payload["hold"]["required_seconds"] = float("nan")

        def nonfinite_total(payload: dict) -> None:
            payload["hold"]["elapsed_seconds"] = float("inf")

        def nonfinite_sample(payload: dict) -> None:
            payload["hold"]["samples"][-1]["elapsed_seconds"] = float("nan")

        def decreasing(payload: dict) -> None:
            payload["hold"]["samples"][1]["elapsed_seconds"] = -1.0

        def sample_after_total(payload: dict) -> None:
            payload["hold"]["samples"][-1]["elapsed_seconds"] = (
                payload["hold"]["elapsed_seconds"] + 1.0
            )

        mutations = {
            "one_sample": one_sample,
            "nan_required": nonfinite_required,
            "infinite_total": nonfinite_total,
            "nan_sample": nonfinite_sample,
            "decreasing": decreasing,
            "sample_after_total": sample_after_total,
        }
        for index, (name, mutate) in enumerate(mutations.items()):
            with self.subTest(hold=name):
                outcome, config, _, _ = self.run_success(f"hold-series-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                mutate(payload)
                config.verdict_path.write_text(json.dumps(payload), encoding="utf-8")

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_finalize_rejects_late_or_excessively_gapped_hold_samples(self) -> None:
        def late_two_samples(payload: dict) -> None:
            samples = payload["hold"]["samples"][-2:]
            samples[0]["elapsed_seconds"] = 29.999
            samples[1]["elapsed_seconds"] = 30.0
            payload["hold"]["samples"] = samples
            payload["hold"]["elapsed_seconds"] = 30.0

        def excessive_gap(payload: dict) -> None:
            samples = [payload["hold"]["samples"][0], payload["hold"]["samples"][-1]]
            samples[0]["elapsed_seconds"] = 0.0
            samples[1]["elapsed_seconds"] = 30.0
            payload["hold"]["samples"] = samples
            payload["hold"]["elapsed_seconds"] = 30.0

        for index, (name, mutate) in enumerate(
            (("late_two_samples", late_two_samples), ("excessive_gap", excessive_gap))
        ):
            with self.subTest(hold=name):
                outcome, config, _, _ = self.run_success(f"hold-cadence-{index}")
                self.assertEqual(smoke.EXIT_OK, outcome.exit_code)
                payload = json.loads(config.verdict_path.read_text(encoding="utf-8"))
                mutate(payload)
                smoke.atomic_write_json(config.verdict_path, payload)

                finalized = self.finalize(config, "PASS")

                self.assertEqual(smoke.EXIT_STOP, finalized.exit_code)

    def test_atomic_json_rejects_nan_without_final_or_temp_file(self) -> None:
        destination = self.root / "nan-verdict.json"

        with self.assertRaises(ValueError):
            smoke.atomic_write_json(destination, {"value": float("nan")})

        self.assertFalse(destination.exists())
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_existing_non_owned_output_is_not_overwritten(self) -> None:
        config = self.config("non-owned")
        config.output_dir.mkdir()
        config.verdict_path.write_bytes(b"foreign")

        result = smoke.collect(config, SequenceProvider(_owned_snapshot()), lambda _: FakeRuntime())

        self.assertEqual(smoke.EXIT_STOP, result.exit_code)
        self.assertEqual(b"foreign", config.verdict_path.read_bytes())
        incidents = list(config.output_dir.parent.glob(f"{config.output_dir.name}.incident-*.json"))
        self.assertEqual(1, len(incidents))
        incident = json.loads(incidents[0].read_text(encoding="utf-8"))
        self.assertEqual("STOP", incident["result"])
        self.assertEqual("existing_output_not_owned_or_not_absent", incident["stop_reason"])

        empty = self.config("existing-empty")
        empty.output_dir.mkdir()
        empty_result = smoke.collect(
            empty, SequenceProvider(_owned_snapshot()), lambda _: FakeRuntime()
        )
        self.assertEqual(smoke.EXIT_STOP, empty_result.exit_code)
        self.assertFalse(empty.verdict_path.exists())
        self.assertEqual(
            1,
            len(list(empty.output_dir.parent.glob(f"{empty.output_dir.name}.incident-*.json"))),
        )

    def test_collision_incident_is_published_by_atomic_replace(self) -> None:
        config = self.config("incident-replace")
        config.output_dir.mkdir()
        replace = smoke.os.replace

        with mock.patch.object(smoke.os, "replace", wraps=replace) as replace_spy:
            outcome = smoke.collect(
                config, SequenceProvider(_owned_snapshot()), lambda _: FakeRuntime()
            )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertEqual(1, replace_spy.call_count)
        source, destination = replace_spy.call_args.args
        self.assertEqual(pathlib.Path(outcome.payload["incident_path"]), pathlib.Path(destination))
        self.assertNotEqual(pathlib.Path(source), pathlib.Path(destination))
        self.assertFalse(pathlib.Path(source).exists())

    def test_interrupted_incident_write_leaves_no_partial_final_or_temp(self) -> None:
        config = self.config("incident-interrupted")
        config.output_dir.mkdir()

        def interrupt_dump(payload: dict, stream: object, **kwargs: object) -> None:
            stream.write('{"partial":')  # type: ignore[attr-defined]
            raise OSError("incident_write_interrupted")

        with mock.patch.object(smoke.json, "dump", side_effect=interrupt_dump):
            outcome = smoke.collect(
                config, SequenceProvider(_owned_snapshot()), lambda _: FakeRuntime()
            )

        self.assertEqual(smoke.EXIT_STOP, outcome.exit_code)
        self.assertIn("incident_write_interrupted", outcome.payload["incident_error"])
        self.assertEqual(
            [], list(config.output_dir.parent.glob(f"{config.output_dir.name}.incident-*.json"))
        )
        self.assertEqual(
            [], list(config.output_dir.parent.glob(f".{config.output_dir.name}.incident-*.tmp"))
        )

    def test_provider_error_daemon_mismatch_and_cleanup_failure_fail_closed(self) -> None:
        provider_error = smoke.collect(
            self.config("provider-error"),
            SequenceProvider(smoke.DiscoveryError("cim_failed")),
            lambda _: FakeRuntime(),
        )
        self.assertEqual(smoke.EXIT_STOP, provider_error.exit_code)

        unexpected_provider_error = smoke.collect(
            self.config("provider-unexpected-error"),
            SequenceProvider(RuntimeError("unexpected_cim_failure")),
            lambda _: FakeRuntime(),
        )
        self.assertEqual(smoke.EXIT_STOP, unexpected_provider_error.exit_code)

        owned = _owned_snapshot()
        wrong_daemon = smoke.OwnershipSnapshot(
            processes=tuple(
                _process(p.pid, "python.exe -m unrelated --port 8765", p.name)
                if p.pid == 303
                else p
                for p in owned.processes
            ),
            ports=owned.ports,
        )
        daemon_result = smoke.collect(
            self.config("daemon-mismatch"),
            SequenceProvider(wrong_daemon),
            lambda _: FakeRuntime(),
        )
        self.assertEqual(smoke.EXIT_STOP, daemon_result.exit_code)
        self.assertIn("daemon", daemon_result.payload["stop_reason"])

        runtime = FakeRuntime()
        runtime.cleanup_ok = False
        cleanup_result = smoke.collect(
            self.config("cleanup-failure"),
            SequenceProvider(_owned_snapshot()),
            lambda _: runtime,
        )
        self.assertEqual(smoke.EXIT_STOP, cleanup_result.exit_code)
        self.assertIn("cleanup", cleanup_result.payload["stop_reason"])
        self.assertEqual(
            {
                "attempted": True,
                "ok": False,
                "outcome": "DELETE_UNCONFIRMED",
                "object_id": 77,
                "response": {"ok": False, "deleted": 0},
                "deleted": 0,
                "post_delete_absent": True,
                "possible_orphan": True,
            },
            cleanup_result.payload["cleanup"],
        )
        self.assertTrue(cleanup_result.payload["automatic_retry_blocked"])

    def test_windows_snapshot_parser_rejects_malformed_payload(self) -> None:
        with self.assertRaises(smoke.DiscoveryError):
            smoke.parse_windows_snapshot("not-json")
        with self.assertRaises(smoke.DiscoveryError):
            smoke.parse_windows_snapshot(json.dumps({"processes": "wrong", "ports": []}))

    def test_cli_usage_has_distinct_exit_code(self) -> None:
        self.assertEqual(smoke.EXIT_USAGE, smoke.main([]))

    def test_collect_cli_pins_explicit_candidate_pbo_and_hash(self) -> None:
        output_dir = self.root / "cli-candidate"
        candidate = self.root / "candidate.pbo"
        candidate_hash = "A" * 64
        expected = smoke.RunOutcome(smoke.EXIT_STOP, {"result": "STOP"})
        with mock.patch.object(smoke, "collect", return_value=expected) as collect_spy:
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                exit_code = smoke.main(
                    [
                        "collect",
                        "--output-dir",
                        str(output_dir),
                        "--pbo",
                        str(candidate),
                        "--pbo-sha256",
                        candidate_hash.lower(),
                    ]
                )

        self.assertEqual(smoke.EXIT_STOP, exit_code)
        config = collect_spy.call_args.args[0]
        self.assertEqual(candidate, config.artifacts["pbo"].path)
        self.assertEqual(candidate_hash, config.artifacts["pbo"].expected_sha256)

    def test_collect_cli_routes_self_lease_without_environment_secret(self) -> None:
        output_dir = self.root / "cli-self-lease"
        expected = smoke.RunOutcome(smoke.EXIT_STOP, {"result": "STOP"})
        with mock.patch.object(
            smoke, "collect_with_self_lease", return_value=expected
        ) as collect_spy:
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                exit_code = smoke.main(
                    [
                        "collect",
                        "--output-dir",
                        str(output_dir),
                        "--self-lease",
                        "--lease-wait-seconds",
                        "45",
                    ]
                )

        self.assertEqual(smoke.EXIT_STOP, exit_code)
        self.assertEqual(45.0, collect_spy.call_args.kwargs["max_wait_s"])

    def test_collect_cli_rejects_non_sha256_candidate_pin(self) -> None:
        with mock.patch.object(smoke, "collect") as collect_spy:
            exit_code = smoke.main(
                [
                    "collect",
                    "--output-dir",
                    str(self.root / "cli-invalid-hash"),
                    "--pbo-sha256",
                    "not-a-sha256",
                ]
            )

        self.assertEqual(smoke.EXIT_USAGE, exit_code)
        collect_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
