"""Loopback HTTP capture sink for outgoing server RestApi requests."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import selectors
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_CAPTURES = 4
MAX_RETAINED_CAPTURES = 8
MAX_IN_FLIGHT = 4
MAX_BODY_BYTES = 65536
MAX_TARGET_BYTES = 8192
MAX_ENTRIES = 256
MAX_CAPTURE_BODY_BYTES = 4 * 1024 * 1024
MAX_PAGE_ENTRIES = 32
MAX_PAGE_BODY_BYTES = 256 * 1024
MAX_RULES = 8
MAX_RULE_BODY_BYTES = 4096
DEFAULT_TTL_S = 900
MIN_TTL_S = 30
MAX_TTL_S = 3600
RETENTION_AFTER_STOP_S = 600
SOCKET_TIMEOUT_S = 10.0
SOCKET_POLL_SLICE_S = 0.05
POLL_INTERVAL_S = 0.01
AUTO_CLOSE_GRACE_S = 0.15
ACTIVE_CLOSE_WAIT_S = 0.5

REDACTED = "<redacted>"
DEFAULT_RESPONSE_BODY = b'{"ok":true}'
DEFAULT_RESPONSE_CT = "application/json"
NOT_FOUND_BODY = b'{"error":"not_found"}'

ALLOWED_METHODS = frozenset({"GET", "POST"})
RULE_STATUSES = frozenset(
    {200, 201, 202, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503}
)

_START_KEYS = frozenset(
    {"identity", "op", "ttl_s", "responses", "store_text_bodies"}
)
_READ_KEYS = frozenset(
    {"identity", "op", "capture_id", "since_seq", "max_entries"}
)
_STOP_KEYS = frozenset({"identity", "op", "capture_id"})
_RULE_KEYS = frozenset({"path", "method", "status", "body", "content_type"})

_UNSET = object()
_DECIMAL_RE = re.compile(r"\A[0-9]+\Z")
_SEGMENT_SPLIT_RE = re.compile(r"[_.\-]")
_SECRET_SINGLE_SEGMENTS = frozenset(
    {"jwt", "token", "password", "passwd", "secret", "rcon", "auth", "cookie"}
)


def capture_limits() -> dict[str, int]:
    return {
        "max_captures": MAX_CAPTURES,
        "max_retained_captures": MAX_RETAINED_CAPTURES,
        "max_in_flight": MAX_IN_FLIGHT,
        "max_body_bytes": MAX_BODY_BYTES,
        "max_target_bytes": MAX_TARGET_BYTES,
        "max_entries": MAX_ENTRIES,
        "max_capture_body_bytes": MAX_CAPTURE_BODY_BYTES,
        "max_page_entries": MAX_PAGE_ENTRIES,
        "max_page_body_bytes": MAX_PAGE_BODY_BYTES,
        "max_rules": MAX_RULES,
        "max_rule_body_bytes": MAX_RULE_BODY_BYTES,
        "min_ttl_s": MIN_TTL_S,
        "max_ttl_s": MAX_TTL_S,
        "retention_after_stop_s": RETENTION_AFTER_STOP_S,
    }


def _format_utc(epoch_s: float) -> str:
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _is_secret_key(name: str) -> bool:
    segments = [seg for seg in _SEGMENT_SPLIT_RE.split(name.lower()) if seg]
    for index, segment in enumerate(segments):
        if segment in _SECRET_SINGLE_SEGMENTS:
            return True
        if (
            segment == "api"
            and index + 1 < len(segments)
            and segments[index + 1] == "key"
        ):
            return True
    return False


def _is_url_secret_key(name: str) -> bool:
    decoded = urllib.parse.unquote(name, encoding="utf-8", errors="replace")
    return _is_secret_key(decoded)


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"unsupported JSON constant: {token}")


def _redact_json(
    value: Any, prefix: str, redacted: list[str]
) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            pointer = f"{prefix}.{key}"
            if _is_secret_key(key):
                result[key] = REDACTED
                redacted.append(pointer)
            else:
                result[key] = _redact_json(item, pointer, redacted)
        return result
    if isinstance(value, list):
        return [
            _redact_json(item, f"{prefix}.{index}", redacted)
            for index, item in enumerate(value)
        ]
    return value


def _parse_and_redact_target(
    raw_target: str,
) -> tuple[str, str, str, list[list[str]], list[str]]:
    if "?" in raw_target:
        path_part, query_raw = raw_target.split("?", 1)
        has_query = True
    else:
        path_part, query_raw = raw_target, ""
        has_query = False

    if not query_raw:
        target = f"{path_part}?" if has_query else path_part
        return target, path_part, "", [], []

    query_pairs: list[list[str]] = []
    redacted_parts: list[str] = []
    redacted_pointers: list[str] = []
    for part in query_raw.split("&"):
        if "=" in part:
            name, value = part.split("=", 1)
            if _is_url_secret_key(name):
                query_pairs.append([name, REDACTED])
                redacted_parts.append(f"{name}={REDACTED}")
                redacted_pointers.append(f"query.{name}")
            else:
                query_pairs.append([name, value])
                redacted_parts.append(part)
        else:
            query_pairs.append([part, ""])
            redacted_parts.append(part)

    redacted_query = "&".join(redacted_parts)
    redacted_target = f"{path_part}?{redacted_query}"
    return redacted_target, path_part, redacted_query, query_pairs, redacted_pointers


def _parse_and_redact_form(text: str) -> tuple[str, list[str]] | None:
    if not text:
        return None
    parts = text.split("&")
    redacted_parts: list[str] = []
    redacted_pointers: list[str] = []
    for part in parts:
        if "=" not in part:
            return None
        name, _value = part.split("=", 1)
        if not name:
            return None
        if _is_url_secret_key(name):
            redacted_parts.append(f"{name}={REDACTED}")
            redacted_pointers.append(f"body.{name}")
        else:
            redacted_parts.append(part)
    return "&".join(redacted_parts), redacted_pointers


def _classify_body(
    raw_body: bytes, *, store_text_bodies: bool
) -> tuple[str, Any, str | None, list[str], int]:
    if not raw_body:
        return "empty", None, None, [], 0

    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        decoded = None

    if decoded is not None:
        try:
            parsed = json.loads(decoded, parse_constant=_reject_json_constant)
        except ValueError:
            parsed = None
            is_json = False
        else:
            is_json = True

        if is_json:
            redacted_pointers: list[str] = []
            redacted_body = _redact_json(parsed, "body", redacted_pointers)
            canonical = json.dumps(
                redacted_body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            return "json", redacted_body, digest, redacted_pointers, len(canonical)

        if store_text_bodies:
            form_result = _parse_and_redact_form(decoded)
            if form_result is not None:
                redacted_text, form_pointers = form_result
                encoded = redacted_text.encode("utf-8")
                digest = hashlib.sha256(encoded).hexdigest()
                return "text", redacted_text, digest, form_pointers, len(encoded)

    return "opaque", None, None, [], 0


def _header_instances(headers: Any, name: str) -> list[Any]:
    if hasattr(headers, "get_all"):
        values = headers.get_all(name)
        return [] if values is None else list(values)
    if name not in headers:
        return []
    return [headers.get(name)]


def _parse_body_length(headers: Any) -> int | None:
    """Return one unambiguous non-negative decimal Content-Length, or None."""
    if headers is None:
        return None
    try:
        if hasattr(headers, "defects") and len(headers.defects) != 0:
            return None
        if hasattr(headers, "get_payload") and headers.get_payload():
            return None
        if _header_instances(headers, "Transfer-Encoding"):
            return None
        lengths = _header_instances(headers, "Content-Length")
    except Exception:
        return None
    if len(lengths) > 1:
        return None
    if len(lengths) == 0:
        return 0
    raw = str(lengths[0]).strip(" \t")
    if not _DECIMAL_RE.fullmatch(raw):
        return None
    return int(raw)


@dataclass(frozen=True)
class _ResponseRule:
    path: str
    method: str
    status: int
    body_bytes: bytes
    content_type: str


def _validate_rules(raw: Any) -> tuple[_ResponseRule, ...] | None:
    if not isinstance(raw, list) or len(raw) > MAX_RULES:
        return None
    rules: list[_ResponseRule] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        if not set(item).issubset(_RULE_KEYS):
            return None
        if "path" not in item:
            return None
        path = item["path"]
        if not isinstance(path, str) or "?" in path:
            return None
        method = item.get("method", "POST")
        if not isinstance(method, str) or method not in ALLOWED_METHODS:
            return None
        status = item.get("status", 200)
        if type(status) is not int or status not in RULE_STATUSES:
            return None
        body = item.get("body", "")
        if not isinstance(body, str):
            return None
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > MAX_RULE_BODY_BYTES:
            return None
        content_type = item.get("content_type", "text/plain")
        if (
            not isinstance(content_type, str)
            or not content_type
            or "\r" in content_type
            or "\n" in content_type
        ):
            return None
        rules.append(
            _ResponseRule(
                path=path,
                method=method,
                status=status,
                body_bytes=body_bytes,
                content_type=content_type,
            )
        )
    return tuple(rules)


@dataclass
class _CaptureSession:
    capture_id: str
    token_bytes: bytes
    started_mono: float
    expires_mono: float
    rules: tuple[_ResponseRule, ...]
    store_text_bodies: bool
    created_order: int = 0
    state: str = "armed"
    retain_until_mono: float | None = None
    next_seq: int = 0
    accepted: int = 0
    dropped: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    stored_body_bytes: int = 0
    entries: list[tuple[dict[str, Any], int]] = field(default_factory=list)

    def match_response(self, method: str, path: str) -> tuple[int, str, bytes]:
        for rule in self.rules:
            if rule.path == path and rule.method == method:
                return rule.status, rule.content_type, rule.body_bytes
        return 200, DEFAULT_RESPONSE_CT, DEFAULT_RESPONSE_BODY


class _SinkHandler(BaseHTTPRequestHandler):
    server_version = "DayZMCPSink/0.1"
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT_S

    def log_message(self, format: str, *args: object) -> None:
        return

    def log_error(self, format: str, *args: object) -> None:
        return

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        return

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        self.close_connection = True
        self._send_bytes(int(code), "application/json", b'{"error":"bad_request"}')

    def handle(self) -> None:
        self.close_connection = True
        self.handle_one_request()

    def _wait_readable(self, deadline: float) -> bool:
        while True:
            if getattr(self.server, "is_closing", False):
                return False
            left = deadline - time.monotonic()
            if left <= 0.0:
                return False
            ready, _, _ = select.select(
                [self.connection], [], [], min(SOCKET_POLL_SLICE_S, left)
            )
            if ready:
                return True

    def handle_one_request(self) -> None:
        self.close_connection = True
        try:
            if not self._wait_readable(time.monotonic() + SOCKET_TIMEOUT_S):
                return
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline:
                return
            if len(self.raw_requestline) > 65536:
                self._send_json(414, {"error": "target_too_long"})
                return
            if not self.parse_request():
                return
            self._dispatch()
        except (TimeoutError, socket.timeout, ConnectionError, OSError, ValueError):
            return

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, "application/json", body)

    def _send_not_found(self) -> None:
        self._send_bytes(404, "application/json", NOT_FOUND_BODY)

    def _read_exact(self, length: int) -> bytes | None:
        if length == 0:
            return b""
        chunks: list[bytes] = []
        remaining = length
        deadline = time.monotonic() + SOCKET_TIMEOUT_S
        read_fn = getattr(self.rfile, "read1", self.rfile.read)
        prev_timeout = self.connection.gettimeout()
        try:
            while remaining > 0:
                if getattr(self.server, "is_closing", False):
                    return None
                left = deadline - time.monotonic()
                if left <= 0.0:
                    raise TimeoutError("sink body read timed out")
                self.connection.settimeout(min(SOCKET_POLL_SLICE_S, left))
                try:
                    chunk = read_fn(remaining)
                except (TimeoutError, socket.timeout):
                    continue
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)
        finally:
            try:
                self.connection.settimeout(prev_timeout)
            except OSError:
                pass

    def _dispatch(self) -> None:
        if getattr(self.server, "is_closing", False):
            return
        method = self.command
        if method not in ALLOWED_METHODS:
            self._send_not_found()
            return

        raw_path_bytes = self.path.encode("latin-1")
        if not raw_path_bytes.startswith(b"/sink/"):
            self._send_not_found()
            return

        rest = raw_path_bytes[6:]
        sep = rest.find(b"/")
        if sep <= 0:
            self._send_not_found()
            return
        raw_token_bytes = rest[:sep]
        rel_bytes = rest[sep + 1 :]

        registry: HttpCaptureRegistry = self.server.registry  # type: ignore[attr-defined]
        matched = registry.match_armed_token(raw_token_bytes)
        if matched is None:
            self._send_not_found()
            return
        capture_id, store_text_bodies = matched

        raw_target = rel_bytes.decode("utf-8", errors="replace")
        (
            redacted_target,
            path_part,
            redacted_query,
            query_pairs,
            query_redacted,
        ) = _parse_and_redact_target(raw_target)

        raw_ct = self.headers.get("Content-Type")
        content_type = raw_ct.strip() if isinstance(raw_ct, str) else None
        bound_run_ids = registry.snapshot_bound_run_ids()

        if len(rel_bytes) > MAX_TARGET_BYTES:
            parsed_len = _parse_body_length(self.headers)
            status, body_bytes = registry.record_rejected(
                capture_id=capture_id,
                bound_run_ids=bound_run_ids,
                method=method,
                target=redacted_target,
                path=path_part,
                query=redacted_query,
                query_pairs=query_pairs,
                content_type=content_type,
                content_length=parsed_len if parsed_len is not None else 0,
                body_kind="empty",
                redacted=query_redacted,
                response_status=414,
                rejected="target_too_long",
            )
            self._send_bytes(status, "application/json", body_bytes)
            return

        body_length = _parse_body_length(self.headers)
        if body_length is None:
            status, body_bytes = registry.record_rejected(
                capture_id=capture_id,
                bound_run_ids=bound_run_ids,
                method=method,
                target=redacted_target,
                path=path_part,
                query=redacted_query,
                query_pairs=query_pairs,
                content_type=content_type,
                content_length=None,
                body_kind="empty",
                redacted=query_redacted,
                response_status=400,
                rejected="bad_framing",
            )
            self._send_bytes(status, "application/json", body_bytes)
            return

        if body_length > MAX_BODY_BYTES:
            status, body_bytes = registry.record_rejected(
                capture_id=capture_id,
                bound_run_ids=bound_run_ids,
                method=method,
                target=redacted_target,
                path=path_part,
                query=redacted_query,
                query_pairs=query_pairs,
                content_type=content_type,
                content_length=body_length,
                body_kind="opaque",
                redacted=query_redacted,
                response_status=413,
                rejected="body_too_large",
            )
            self._send_bytes(status, "application/json", body_bytes)
            return

        raw_body = self._read_exact(body_length)
        if raw_body is None:
            if not getattr(self.server, "is_closing", False):
                self._send_json(400, {"error": "bad_framing"})
            return
        if getattr(self.server, "is_closing", False):
            return

        (
            body_kind,
            body_value,
            body_sha256,
            body_redacted,
            stored_body_bytes,
        ) = _classify_body(raw_body, store_text_bodies=store_text_bodies)
        redacted_all = [*query_redacted, *body_redacted]

        status, resp_ct, resp_body = registry.record_accepted(
            capture_id=capture_id,
            bound_run_ids=bound_run_ids,
            method=method,
            target=redacted_target,
            path=path_part,
            query=redacted_query,
            query_pairs=query_pairs,
            content_type=content_type,
            content_length=body_length,
            body_kind=body_kind,
            body=body_value,
            body_sha256=body_sha256,
            redacted=redacted_all,
            stored_body_bytes=stored_body_bytes,
        )
        self._send_bytes(status, resp_ct, resp_body)


class SinkHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = os.name != "nt"
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        server_address: tuple[str, int],
        registry: "HttpCaptureRegistry",
    ) -> None:
        self.registry = registry
        self._stop_event = threading.Event()
        self._stopped_event = threading.Event()
        self._active_lock = threading.Lock()
        self._active_cv = threading.Condition(self._active_lock)
        self._active_sockets: set[socket.socket] = set()
        self._closing = False
        self._idle_since_real: float | None = None
        super().__init__(server_address, _SinkHandler)

    @property
    def is_closing(self) -> bool:
        return self._closing

    def reset_idle(self) -> None:
        self._idle_since_real = None

    def handle_error(self, request: Any, client_address: Any) -> None:
        return

    def begin_close(self) -> None:
        with self._active_lock:
            self._closing = True
            sockets = list(self._active_sockets)
        self._stop_event.set()
        for conn in sockets:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def wait_active_closed(self, timeout: float = ACTIVE_CLOSE_WAIT_S) -> None:
        deadline = time.monotonic() + timeout
        with self._active_cv:
            while self._active_sockets:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                self._active_cv.wait(timeout=remaining)

    def server_close(self) -> None:
        self.begin_close()
        super().server_close()

    def serve_forever(self, poll_interval: float = POLL_INTERVAL_S) -> None:
        self._stopped_event.clear()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self, selectors.EVENT_READ)
                while not self._stop_event.is_set():
                    ready = selector.select(poll_interval)
                    if self._stop_event.is_set():
                        break
                    if ready:
                        self._handle_request_noblock()
                    if self._try_auto_close(selector):
                        break
        finally:
            self._stopped_event.set()

    def shutdown(self) -> None:
        self._stop_event.set()
        self._stopped_event.wait()

    def _try_auto_close(self, selector: selectors.BaseSelector) -> bool:
        if self._stop_event.is_set():
            return False
        if self.registry._has_armed_after_sweep():
            self._idle_since_real = None
            return False
        now_real = time.monotonic()
        if self._idle_since_real is None:
            self._idle_since_real = now_real
            return False
        if now_real - self._idle_since_real < AUTO_CLOSE_GRACE_S:
            return False
        return self.registry._auto_close_listener(self, selector)

    def process_request(self, request: Any, client_address: Any) -> None:
        with self._active_lock:
            if self._closing:
                admitted = False
            else:
                admitted = self.registry._in_flight.acquire(blocking=False)
                if admitted:
                    self._active_sockets.add(request)
        if not admitted:
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.registry._in_flight.release()
            with self._active_cv:
                self._active_sockets.discard(request)
                if not self._active_sockets:
                    self._active_cv.notify_all()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.registry._in_flight.release()
            with self._active_cv:
                self._active_sockets.discard(request)
                if not self._active_sockets:
                    self._active_cv.notify_all()


class HttpCaptureRegistry:
    """In-memory capture sessions and ephemeral loopback sink listener."""

    def __init__(
        self,
        *,
        bound_run_ids_fn: Callable[[], list[str]] | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self.clock: Callable[[], float] = clock or time.monotonic
        self.wall_clock: Callable[[], float] = wall_clock or time.time
        self._bound_run_ids_fn = bound_run_ids_fn
        self._lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._in_flight = threading.BoundedSemaphore(MAX_IN_FLIGHT)
        self._captures: dict[str, _CaptureSession] = {}
        self._capture_seq = 0
        self.listener: SinkHTTPServer | None = None
        self._listener_thread: threading.Thread | None = None

    def snapshot_bound_run_ids(self) -> list[str]:
        fn = self._bound_run_ids_fn
        if fn is None:
            return []
        try:
            ids = fn()
        except Exception:
            return []
        if not isinstance(ids, list):
            return []
        return [item for item in ids if isinstance(item, str) and item]

    def _sweep_locked(self, now_mono: float) -> None:
        expired_ids: list[str] = []
        for capture_id, cap in self._captures.items():
            if cap.state == "armed" and now_mono >= cap.expires_mono:
                cap.state = "expired"
                cap.retain_until_mono = cap.expires_mono + RETENTION_AFTER_STOP_S
            if (
                cap.state != "armed"
                and cap.retain_until_mono is not None
                and now_mono > cap.retain_until_mono
            ):
                expired_ids.append(capture_id)
        for capture_id in expired_ids:
            del self._captures[capture_id]

    def _armed_count_locked(self) -> int:
        return sum(1 for cap in self._captures.values() if cap.state == "armed")

    def _evict_retained_locked(self) -> None:
        while len(self._captures) >= MAX_RETAINED_CAPTURES:
            oldest_id: str | None = None
            oldest_key: tuple[float, float, int] | None = None
            for capture_id, cap in self._captures.items():
                if cap.state == "armed":
                    continue
                key = (
                    cap.retain_until_mono
                    if cap.retain_until_mono is not None
                    else float("inf"),
                    cap.started_mono,
                    cap.created_order,
                )
                if oldest_key is None or key < oldest_key:
                    oldest_key = key
                    oldest_id = capture_id
            if oldest_id is None:
                break
            del self._captures[oldest_id]

    def _has_armed_after_sweep(self) -> bool:
        now_mono = float(self.clock())
        with self._lock:
            self._sweep_locked(now_mono)
            return self._armed_count_locked() > 0

    def _auto_close_listener(
        self, server: SinkHTTPServer, selector: selectors.BaseSelector
    ) -> bool:
        if not self._listener_lock.acquire(blocking=False):
            return False
        try:
            if server.is_closing:
                return False
            now_mono = float(self.clock())
            with self._lock:
                self._sweep_locked(now_mono)
                if self.listener is not server or self._armed_count_locked() > 0:
                    server.reset_idle()
                    return False
            server.begin_close()
            try:
                selector.unregister(server)
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            self.listener = None
            self._listener_thread = None
            return True
        finally:
            self._listener_lock.release()

    def _ensure_listener_open_under_listener_lock(self) -> SinkHTTPServer:
        if self.listener is not None:
            return self.listener
        server = SinkHTTPServer(("127.0.0.1", 0), self)
        if server.server_address[0] != "127.0.0.1":
            server.server_close()
            raise RuntimeError("refusing non-loopback bind")
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": POLL_INTERVAL_S},
            daemon=True,
        )
        try:
            thread.start()
        except BaseException:
            server.server_close()
            raise
        self.listener = server
        self._listener_thread = thread
        return server

    def _close_listener_if_idle_under_listener_lock(self) -> None:
        with self._lock:
            has_armed = self._armed_count_locked() > 0
        if has_armed:
            return
        self._shutdown_listener_under_listener_lock()

    def _shutdown_listener_under_listener_lock(self) -> None:
        server = self.listener
        thread = self._listener_thread
        if server is None:
            return
        server.begin_close()
        try:
            server.shutdown()
        except Exception:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            server.server_close()
        except Exception:
            pass
        server.wait_active_closed()
        self.listener = None
        self._listener_thread = None

    def close(self) -> None:
        """Stop every armed capture and close the sink listener."""
        with self._listener_lock:
            now_mono = float(self.clock())
            with self._lock:
                for cap in self._captures.values():
                    if cap.state == "armed":
                        cap.state = "stopped"
                        cap.retain_until_mono = now_mono + RETENTION_AFTER_STOP_S
            self._shutdown_listener_under_listener_lock()

    def start_from_payload(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not set(body).issubset(_START_KEYS):
            return 400, {"error": "bad_args"}
        ttl_s = body.get("ttl_s", DEFAULT_TTL_S)
        responses = body["responses"] if "responses" in body else []
        store_text_bodies = body.get("store_text_bodies", False)
        return self.start(
            ttl_s=ttl_s,
            responses=responses,
            store_text_bodies=store_text_bodies,
        )

    def start(
        self,
        *,
        ttl_s: Any = DEFAULT_TTL_S,
        responses: Any = _UNSET,
        store_text_bodies: Any = False,
    ) -> tuple[int, dict[str, Any]]:
        if type(ttl_s) is not int or not (MIN_TTL_S <= ttl_s <= MAX_TTL_S):
            return 400, {"error": "bad_args"}
        if responses is _UNSET:
            responses = []
        rules = _validate_rules(responses)
        if rules is None:
            return 400, {"error": "bad_args"}
        if type(store_text_bodies) is not bool:
            return 400, {"error": "bad_args"}

        with self._listener_lock:
            now_mono = float(self.clock())
            now_wall = float(self.wall_clock())
            with self._lock:
                self._sweep_locked(now_mono)
                if self._armed_count_locked() >= MAX_CAPTURES:
                    return 429, {"error": "http_capture_limit"}
                existing_tokens = {cap.token_bytes for cap in self._captures.values()}
                token = secrets.token_hex(16)
                token_bytes = token.encode("ascii")
                while token_bytes in existing_tokens:
                    token = secrets.token_hex(16)
                    token_bytes = token.encode("ascii")
                capture_id = secrets.token_hex(16)
                while capture_id in self._captures:
                    capture_id = secrets.token_hex(16)
                self._capture_seq += 1
                session = _CaptureSession(
                    capture_id=capture_id,
                    token_bytes=token_bytes,
                    started_mono=now_mono,
                    expires_mono=now_mono + ttl_s,
                    rules=rules,
                    store_text_bodies=store_text_bodies,
                    created_order=self._capture_seq,
                )
            try:
                listener = self._ensure_listener_open_under_listener_lock()
            except Exception:
                self._close_listener_if_idle_under_listener_lock()
                raise
            port = int(listener.server_address[1])
            with self._lock:
                self._sweep_locked(now_mono)
                self._evict_retained_locked()
                self._captures[capture_id] = session
            listener.reset_idle()
            return 200, {
                "capture_id": capture_id,
                "base_url": f"http://127.0.0.1:{port}/sink/{token}/",
                "expires_at_utc": _format_utc(now_wall + ttl_s),
                "limits": capture_limits(),
            }

    def read_from_payload(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not set(body).issubset(_READ_KEYS):
            return 400, {"error": "bad_args"}
        return self.read(
            capture_id=body.get("capture_id"),
            since_seq=body.get("since_seq", 0),
            max_entries=body.get("max_entries", MAX_PAGE_ENTRIES),
        )

    def read(
        self,
        *,
        capture_id: Any,
        since_seq: Any = 0,
        max_entries: Any = MAX_PAGE_ENTRIES,
    ) -> tuple[int, dict[str, Any]]:
        if not isinstance(capture_id, str) or not capture_id:
            return 400, {"error": "bad_args"}
        if type(since_seq) is not int or since_seq < 0:
            return 400, {"error": "bad_args"}
        if type(max_entries) is not int or not (1 <= max_entries <= MAX_PAGE_ENTRIES):
            return 400, {"error": "bad_args"}

        with self._listener_lock:
            now_mono = float(self.clock())
            with self._lock:
                self._sweep_locked(now_mono)
                cap = self._captures.get(capture_id)
                if cap is None:
                    result: tuple[int, dict[str, Any]] = (
                        404,
                        {"error": "unknown_capture"},
                    )
                else:
                    page: list[dict[str, Any]] = []
                    page_bytes = 0
                    for entry, entry_bytes in cap.entries:
                        if entry["seq"] <= since_seq:
                            continue
                        if len(page) >= max_entries:
                            break
                        if page and page_bytes + entry_bytes > MAX_PAGE_BODY_BYTES:
                            break
                        page.append(copy.deepcopy(entry))
                        page_bytes += entry_bytes
                    next_seq = page[-1]["seq"] if page else since_seq
                    result = (
                        200,
                        {
                            "state": cap.state,
                            "entries": page,
                            "next_seq": next_seq,
                            "accepted": cap.accepted,
                            "rejected": dict(cap.rejected),
                            "dropped": cap.dropped,
                        },
                    )
            self._close_listener_if_idle_under_listener_lock()
            return result

    def stop_from_payload(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not set(body).issubset(_STOP_KEYS):
            return 400, {"error": "bad_args"}
        return self.stop(capture_id=body.get("capture_id"))

    def stop(self, *, capture_id: Any) -> tuple[int, dict[str, Any]]:
        if not isinstance(capture_id, str) or not capture_id:
            return 400, {"error": "bad_args"}

        with self._listener_lock:
            now_mono = float(self.clock())
            with self._lock:
                self._sweep_locked(now_mono)
                cap = self._captures.get(capture_id)
                if cap is None:
                    result: tuple[int, dict[str, Any]] = (
                        404,
                        {"error": "unknown_capture"},
                    )
                else:
                    if cap.state == "armed":
                        cap.state = "stopped"
                        cap.retain_until_mono = now_mono + RETENTION_AFTER_STOP_S
                    result = (
                        200,
                        {
                            "state": "stopped",
                            "accepted": cap.accepted,
                            "rejected": dict(cap.rejected),
                        },
                    )
            self._close_listener_if_idle_under_listener_lock()
            return result

    def match_armed_token(self, raw_token_bytes: bytes) -> tuple[str, bool] | None:
        now_mono = float(self.clock())
        with self._lock:
            self._sweep_locked(now_mono)
            matched: _CaptureSession | None = None
            for cap in self._captures.values():
                if cap.state != "armed":
                    continue
                if hmac.compare_digest(raw_token_bytes, cap.token_bytes):
                    matched = cap
            if matched is None:
                return None
            return matched.capture_id, matched.store_text_bodies

    def record_rejected(
        self,
        *,
        capture_id: str,
        bound_run_ids: list[str],
        method: str,
        target: str,
        path: str,
        query: str,
        query_pairs: list[list[str]],
        content_type: str | None,
        content_length: int | None,
        body_kind: str,
        redacted: list[str],
        response_status: int,
        rejected: str,
    ) -> tuple[int, bytes]:
        now_mono = float(self.clock())
        now_wall = float(self.wall_clock())
        with self._lock:
            self._sweep_locked(now_mono)
            cap = self._captures.get(capture_id)
            if cap is None or cap.state != "armed":
                return 404, NOT_FOUND_BODY
            if len(cap.entries) >= MAX_ENTRIES:
                cap.dropped += 1
                return 503, b'{"error":"capture_full"}'
            cap.next_seq += 1
            entry = {
                "seq": cap.next_seq,
                "received_at_utc": _format_utc(now_wall),
                "ms_since_start": max(
                    0, int(round((now_mono - cap.started_mono) * 1000.0))
                ),
                "bound_run_ids": list(bound_run_ids),
                "method": method,
                "target": target,
                "path": path,
                "query": query,
                "query_pairs": query_pairs,
                "content_type": content_type,
                "content_length": content_length,
                "body_kind": body_kind,
                "body": None,
                "body_sha256": None,
                "redacted": list(redacted),
                "response_status": response_status,
                "rejected": rejected,
            }
            cap.entries.append((entry, 0))
            cap.rejected[rejected] = cap.rejected.get(rejected, 0) + 1
            body = json.dumps({"error": rejected}, separators=(",", ":")).encode(
                "utf-8"
            )
            return response_status, body

    def record_accepted(
        self,
        *,
        capture_id: str,
        bound_run_ids: list[str],
        method: str,
        target: str,
        path: str,
        query: str,
        query_pairs: list[list[str]],
        content_type: str | None,
        content_length: int,
        body_kind: str,
        body: Any,
        body_sha256: str | None,
        redacted: list[str],
        stored_body_bytes: int,
    ) -> tuple[int, str, bytes]:
        now_mono = float(self.clock())
        now_wall = float(self.wall_clock())
        with self._lock:
            self._sweep_locked(now_mono)
            cap = self._captures.get(capture_id)
            if cap is None or cap.state != "armed":
                return 404, "application/json", NOT_FOUND_BODY
            if (
                len(cap.entries) >= MAX_ENTRIES
                or cap.stored_body_bytes + stored_body_bytes > MAX_CAPTURE_BODY_BYTES
            ):
                cap.dropped += 1
                return 503, "application/json", b'{"error":"capture_full"}'
            resp_status, resp_ct, resp_body = cap.match_response(method, path)
            cap.next_seq += 1
            entry = {
                "seq": cap.next_seq,
                "received_at_utc": _format_utc(now_wall),
                "ms_since_start": max(
                    0, int(round((now_mono - cap.started_mono) * 1000.0))
                ),
                "bound_run_ids": list(bound_run_ids),
                "method": method,
                "target": target,
                "path": path,
                "query": query,
                "query_pairs": query_pairs,
                "content_type": content_type,
                "content_length": content_length,
                "body_kind": body_kind,
                "body": body,
                "body_sha256": body_sha256,
                "redacted": list(redacted),
                "response_status": resp_status,
                "rejected": None,
            }
            cap.entries.append((entry, stored_body_bytes))
            cap.stored_body_bytes += stored_body_bytes
            cap.accepted += 1
            return resp_status, resp_ct, resp_body
