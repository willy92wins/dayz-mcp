"""Acceptance tests for f078: the HTTP capture sink (fb-20261115-191029-f078).

Contract: _ref/RESEARCH-f078.md §3 (security) and §4.1 (tools, arguments,
results, entry fields); the 17 items of §5.1, one test method each. Written
before the feature: on main 43215db every test fails on its own in setUp,
which names what is missing.

Revised after the cross-family review _ref/REVIEW-oracle.md: positives
test_p8 to test_p17 and negatives test_n11 to test_n25 were added, one
behaviour each (the 17 original methods keep their assertions; only N5 gained
a bounded wait before its final reads). The sink ones live in HttpCaptureTest
and fail in its setUp on main; test_p15 to test_p17 live in
HttpCaptureToolsTest, have no sink setUp, and fail on main on their own
because the tool names are missing.

Extended after the second cross-family review _ref/REVIEW-r1.md: negatives
test_n26 to test_n30 were added, one per P1 item, after every existing
method (nothing earlier was changed or reordered). They are written against
the implementation on this branch: each fails there, on the defect its
comment names, and passes once that item is fixed. Their readings, decided
by the receiver, are listed last under "Readings the tests pin".

Seams the implementation provides, exactly as these tests use them
------------------------------------------------------------------
1. Module ``dayz_mcp.http_capture`` with class ``HttpCaptureRegistry``.
   ``loopback.ServerState(key).http_capture`` is one instance per state,
   built by the ServerState constructor.
2. ``registry.clock``: zero-argument callable returning monotonic seconds
   (float). TTL, expiry and ``ms_since_start`` use it.
   ``registry.wall_clock``: zero-argument callable returning POSIX seconds
   (float). ``expires_at_utc`` and ``received_at_utc`` come from it.
   Both are plain attributes read at every use; the tests replace them right
   after building the state, before any op.
3. ``registry.listener``: the sink server object (it has ``server_address``)
   while at least one capture is armed, ``None`` otherwise. One listener
   serves every armed capture.
4. Control route ``POST /http-capture?key=<key>`` on the main port, shaped like
   ``/client-dumps``: JSON body ``{"identity": <ClientIdentity payload>,
   "op": "start" | "read" | "stop", ...}`` with the tool arguments at the top
   level (``ttl_s``, ``responses``, ``store_text_bodies``; ``capture_id``,
   ``since_seq``, ``max_entries``). Success is 200 with the tool result as the
   body. Errors are ``{"error": <code>}``: 401 ``unauthorized`` (the main
   gate), 400 ``invalid_identity``, 400 ``bad_args``, 403
   ``live_profile_refused``, any 4xx ``http_capture_limit``.

Readings the tests pin (where §3/§4.1 leave a choice)
-----------------------------------------------------
- ``base_url`` is ``http://127.0.0.1:<port>/sink/<32 lowercase hex>/``.
- Timestamps (``expires_at_utc``, ``received_at_utc``) are ISO-8601 UTC
  ending in ``Z``; compared to the fake wall clock within 1 s.
- Default sink answer: 200, ``Content-Type: application/json``, body
  ``{"ok":true}``. A rule matches on ``path`` (the entry ``path``) and
  ``method`` (default POST) and answers its ``status`` with ``body`` as the
  exact bytes and a Content-Type starting with ``content_type``.
- Entry keys are exactly the 17 of §4.1. ``target`` is the request after
  ``/sink/<token>/``, raw bytes decoded as UTF-8, no percent-decoding and no
  normalization. ``path`` and ``query`` split it at the first ``?``.
  ``query_pairs`` splits ``query`` on ``&`` then at the first ``=``, in order,
  empty values kept, nothing decoded.
- Redaction pointers in ``redacted``: ``body.`` plus the dotted key path, list
  items by decimal index (``body.nested.api_key``, ``body.list.0.token``);
  query keys as ``query.<name>``. The order of ``redacted`` is not pinned.
  A redacted query value is also ``<redacted>`` in ``target`` and ``query``.
- ``body_sha256`` is the hex SHA-256 of ``json.dumps(redacted_body,
  ensure_ascii=False, separators=(",", ":"))`` encoded as UTF-8, object keys
  in the order received.
- ``body_kind``: ``json`` (body is the redacted value), ``empty`` (no body;
  ``body`` is ``None``), ``opaque`` (non-JSON without ``store_text_bodies``:
  ``body`` and ``body_sha256`` are ``None``, only ``content_length`` and
  ``content_type``).
- Rule arguments: a ``path`` with ``?``, a rule without ``path``, a rule body
  over 4096 bytes and more than 8 rules are ``bad_args``; so are a non-int or
  out-of-range ``ttl_s`` or ``max_entries``, a negative ``since_seq`` and a
  non-bool ``store_text_bodies``.
- Four armed captures fill the daemon; the fifth start is refused. Whether a
  stopped capture frees its slot is not pinned.
- Rejected requests (``body_too_large``, ``target_too_long``,
  ``bad_framing``) are stored as entries with ``body`` ``None`` and the status
  sent in ``response_status``; ``accepted`` counts entries whose ``rejected``
  is ``None``; the read ``rejected`` maps reason to count. The 257th request
  is not stored: 503 ``{"error": "capture_full", ...}`` and ``dropped`` += 1.
- Limits are inclusive: a body of 65536 bytes and a ``target`` of 8192 bytes
  are accepted, one more byte is 413 / 414. The tests send rejected requests
  without a body, so the sink answers them without reading one; it still
  reads the whole request head before answering (a 414 included), so the
  client gets the answer and not a reset.
- Paging: ``next_seq`` goes back as ``since_seq``; a read returns the entries
  after it, ``seq`` strictly increasing; past the end, ``[]`` and the same
  ``next_seq``. The seq base is not pinned.
- Expiry is observed lazily on the fake clock: the next control op or sink
  request after ``expires_at`` sees it (sink: 404; read: ``state`` expired).
  A control op (``stop`` or ``read``) that leaves no capture armed closes the
  listener before it answers.
- A bad token, an absolute-form target, a stopped or expired capture and any
  non-sink route answer 404 with the body of an unknown sink route, which is
  ``{"error": "not_found"}`` like ``loopback.py:9860``.
- Live profile: refused when ``ServerState.live_profile`` is true, which the
  daemon derives from ``DAYZ_MCP_LIVE_PROFILE`` through
  ``loopback.live_profile_from_env``; the test also keeps that variable set.

Still under "Readings the tests pin": the ones below were added after the
review.

- Content-Length: every default and canned sink answer carries a
  ``Content-Length`` equal to the bytes of its body (a canned body goes out
  as its UTF-8 bytes, so a multibyte body counts bytes, not characters).
- JSON is recognised by content, not by ``Content-Type``: a body that decodes
  as UTF-8 and parses as JSON is ``body_kind`` ``json`` (redacted, same
  pointers and hash) even when it arrives as ``application/octet-stream``,
  which is what 8 of the 9 BR webhooks send (spec line 59), and also with
  ``store_text_bodies=true``. ``content_type`` keeps the header as sent.
- ``store_text_bodies=true``: a non-JSON body "of the form ``a=b&c=d``" is one
  that decodes as UTF-8, is not empty, and whose every ``&``-separated part
  holds an ``=`` with a non-empty name before it. It is stored with
  ``body_kind`` ``text`` and ``body`` the text with the value of every secret
  name replaced by ``<redacted>`` (nothing decoded, order kept), pointers
  ``body.<name>``, and ``body_sha256`` the hex SHA-256 of that redacted text in
  UTF-8. Any other non-JSON body stays ``opaque`` even with the flag
  (conservative reading of spec §3). The form is recognised by content too.
- Secret names (spec §3): ``jwt``, ``token``, ``password``, ``passwd``,
  ``secret``, ``rcon``, ``auth``, ``api_key``, ``cookie``. A key name (body,
  form or query) is split on ``_``, ``-`` and ``.``, lowercased, and is secret
  when its segments contain one of those names as whole segments; ``api_key``
  is the two consecutive segments ``api``, ``key``. So ``client_secret``,
  ``db_passwd``, ``x-auth``, ``API_KEY`` and ``set-Cookie`` match;
  ``authority``, ``apikey``, ``api`` and ``key`` alone do not. The whole value
  is replaced; the pointer keeps the key name as received (case included);
  list items nest by index at any depth (``body.matrix.0.0.token``).
- Query redaction also holds for rejected entries (400, 413, 414): a query
  secret never appears in that entry's ``target``, ``query`` or
  ``query_pairs``.
- Token match is exact: ``/sink/<token>x/...`` or ``/sink/<token>0/...`` is
  the plain 404. Any method other than GET and POST (PUT, DELETE, PATCH,
  OPTIONS) under a valid token is the plain 404 and stores nothing.
- Framing: a ``Content-Length`` that is negative or not a decimal number
  (``-1``, ``nope``, ``0x10``, ``1.5``) is 400 ``bad_framing``, stored like
  the other 400s. A body shorter than its ``Content-Length`` (the client
  half-closes early) never becomes an accepted entry; if the sink answers at
  all, the answer is not 2xx.
- Limits are UTF-8 bytes: a ``target`` of 8192 bytes of two-byte characters
  is accepted and 8193 bytes is 414; a rule body of 4096 bytes is accepted
  and 4097 bytes is ``bad_args``.
- Canned rules: ``method`` is matched exactly (default POST), ``status`` and
  ``content_type`` are what the sink answers. The closed list of rule
  statuses is {200, 201, 202, 400, 401, 403, 404, 409, 422, 429, 500, 502,
  503}; anything else, 204 first of all (a 204 is engine OnError 8, spec
  §1.3), is ``bad_args``.
- Retention: after ``stop`` the capture stays readable on ``registry.clock``
  for 600 s with ``state`` ``stopped`` and its entries, even when its TTL
  passes meanwhile (tested at stop + 599 s). After that (tested at
  stop + 601 s; exactly 600 s is not pinned) ``read`` answers a 4xx with
  ``{"error": "unknown_capture"}``, the same answer as for a capture id that
  never existed.
- ``registry.listener`` is ``None`` before the first ``start``.
- ``bound_run_ids`` is computed when the request arrives: an entry keeps the
  ids it got even after the binding goes away, a later entry has ``[]``, and
  only bindings in ``instance_fence.BINDING_BOUND`` are listed (a STARTING
  binding is not).
- Empty capture: ``read`` returns ``entries == []`` and an int ``next_seq``
  that, given back as ``since_seq``, returns the first entry stored later.
  An ``empty`` entry (GET without body, or POST with ``Content-Length: 0``)
  has ``body`` and ``body_sha256`` ``None``.
- Byte caps (spec §3). The stored body bytes of an entry are the bytes its
  body is hashed over (for the compact, ASCII, secret-free JSON bodies the
  tests send this equals ``content_length``); the tests do not pin how
  ``opaque``, ``empty`` or rejected entries count. The sum over a capture is
  at most 4 MiB (4194304, inclusive): a request whose body would push it over
  gets 503 ``capture_full``, ``dropped`` grows by one and nothing is stored.
  A read returns entries in order until the next one would push the page's
  stored body bytes over 256 KiB (262144, inclusive); a page always holds at
  least one entry; ``next_seq`` lets the reader continue.
- Stderr: no sink request (accepted, rejected or 404) writes anything with
  the capture token or a request secret to ``sys.stderr`` or to the daemon
  ``log_sink``; the stdlib ``BaseHTTPRequestHandler.log_message`` must be
  silenced. The token is never in the daemon log either.
- MCP tools: ``http_capture_start``, ``http_capture_read`` and
  ``http_capture_stop`` are registered by ``server.build_app`` like
  ``logs_since``, are in ``tool_catalog._INITIAL_CATALOG_NAMES`` (no lease),
  and in client mode go to the daemon through ``ControlClient`` methods named
  ``http_capture_start``, ``http_capture_read`` and ``http_capture_stop``
  (like ``client_dumps_*``) that post to ``/http-capture`` with
  ``op`` ``start``, ``read`` or ``stop``. Only the names are pinned.
- N5 waits, bounded, until the sink can hold ``MAX_IN_FLIGHT`` requests at
  once again (so every slow handler has finished) before its final reads.

Still under "Readings the tests pin": the ones below were added after the
second review (_ref/REVIEW-r1.md, items P1-1 to P1-5, tested by test_n26 to
test_n30). Where one refines an earlier reading above, the later one wins.

- Live profile in embedded mode (N26). The embedded build is
  ``server.Runtime(ServerConfig(...)).start_loopback()``, which constructs
  ``loopback.LoopbackServer`` and with it the ``ServerState``
  (server.py:1415-1429, loopback.py:11504-11531); the test builds it that way
  with ``key`` given and ``port=0``. With ``DAYZ_MCP_LIVE_PROFILE=1`` in the
  environment while it is built and used, ``POST /http-capture`` ``op``
  ``start`` on that server's port answers 403 ``live_profile_refused``, and
  ``Runtime.http_capture_start`` raises a ``ToolError`` whose text is
  ``live_profile_refused``; ``registry.listener`` stays ``None`` after both.
  Without the variable both entries arm a capture (positive control). How the
  profile reaches the embedded state (environment read in ``LoopbackServer``,
  in ``Runtime``, or ``ServerConfig.live_profile`` falling back to the
  environment like daemon.py:488) is not pinned.
- Expiry closes the listener on its own (N27). This refines "Expiry is
  observed lazily" above: when the fake ``registry.clock`` passes the
  ``expires_at`` of the last armed capture, the listener closes with no
  control op and no sink request: within 2 s of real time
  ``registry.listener`` is ``None`` and a connect to the old port is refused.
  The fake clock does not move with real time, so the implementation must
  re-read ``registry.clock`` by itself (for example on a short timer); a
  ``threading.Timer`` armed for the real TTL would not satisfy this. A later
  ``read`` still answers ``state`` ``expired`` with the entries.
- Retention budget (N28). At most 8 captures are retained in total: armed
  ones plus stopped or expired ones still inside their 600 s read window. A
  ``start`` that would retain a ninth drops the oldest stopped or expired
  capture (in the test start, stop and age order coincide, so which "oldest"
  is meant is not pinned further); a dropped capture's ``read`` gets the same
  status and body as a capture id that never existed. Armed captures are never
  dropped, and with four armed a fifth ``start`` is still refused with
  ``http_capture_limit``. Whether a refused ``start`` drops anything is not
  pinned.
- Stop closes accepted connections (N29). When ``stop`` leaves no capture
  armed, the connections the listener accepted are closed too: four slow
  requests (head sent, body pending) each see EOF or a reset within 2 s of
  the ``stop`` answer (bytes before that, if any, are not a 2xx answer), and
  none of them becomes an accepted entry. The in-flight cap is one budget for
  the registry: pinned only as "never more than ``MAX_IN_FLIGHT`` handled at
  once", tested as: if a request to a capture B started right after that
  ``stop`` is answered, at most three of A's four slow sockets are still open
  (no EOF, reset or answer) at that moment. A slow socket is "open" while a
  non-blocking ``MSG_PEEK`` finds nothing to read.
- Percent-encoded secret names (N30). This refines the secret-name reading
  above (which said "nothing decoded" for form bodies): a query or form key
  name is percent-decoded (as UTF-8) only to decide whether it is secret,
  then matched with the same segment rule; the stored key keeps its raw
  spelling in ``target``, ``query``, ``query_pairs``, the form ``body`` and
  the pointers (``query.api%5Fkey``, ``query.%74oken``, ``body.api%5Fkey``),
  and values that are not secret (``version=1``) are untouched. The form body
  hash is over that redacted raw text. Whether ``+`` decodes to a space, and
  whether JSON object keys are percent-decoded, is not pinned.

Note for the implementer
------------------------
- The sink listener must shut down promptly: serve it with a poll interval of
  0.05 s or less (or an equivalent wake-up), because every fast-tier test
  stops it in cleanup and the fast tier has a 0.2 s budget per test.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import importlib
import io
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from dayz_mcp import loopback
from tests._tiers import slow_test
from tests.fence_helpers import bind_both_peers

KEY = "test-key"
# Same shape LoopbackTest feeds ClientIdentity.from_payload.
IDENTITY: dict[str, object] = {
    "platform": "codex",
    "pid": 101,
    "ppid": 10,
    "started_at_utc": "2026-07-14T20:00:00Z",
    "session_id": "f078-capture-session",
    "task_label": "f078 http capture",
}
BOUND_RUN_ID = "test-run"
BASE_URL_RE = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/sink/([0-9a-f]{32})/$")
IO_TIMEOUT_S = 5.0
REDACTED = "<redacted>"
NOT_FOUND = {"error": "not_found"}
CT_JSON = "Content-Type: application/json"
MAX_BODY_BYTES = 65536
MAX_TARGET_BYTES = 8192
MAX_ENTRIES = 256
MAX_CAPTURES = 4
MAX_IN_FLIGHT = 4
MAX_RULES = 8
MAX_RULE_BODY = 4096
MAX_CAPTURE_BODY_BYTES = 4 * 1024 * 1024
MAX_PAGE_BODY_BYTES = 256 * 1024
RETENTION_AFTER_STOP_S = 600
RULE_STATUSES = (200, 201, 202, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503)
UNKNOWN_CAPTURE = "unknown_capture"
CAPTURE_TOOLS = ("http_capture_start", "http_capture_read", "http_capture_stop")
CT_OCTET = "Content-Type: application/octet-stream"
UUID36 = "00000000-0000-4000-8000-0000000000f0"
WALL_START = 1_800_000_000.0
MONO_START = 1_000.0
ENTRY_FIELDS = frozenset(
    {
        "seq",
        "received_at_utc",
        "ms_since_start",
        "bound_run_ids",
        "method",
        "target",
        "path",
        "query",
        "query_pairs",
        "content_type",
        "content_length",
        "body_kind",
        "body",
        "body_sha256",
        "redacted",
        "response_status",
        "rejected",
    }
)


def _http_capture_module() -> object:
    try:
        return importlib.import_module("dayz_mcp.http_capture")
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "dayz_mcp.http_capture is missing: f078 needs HttpCaptureRegistry "
            f"there ({exc})"
        ) from None


class _FakeClock:
    """Monotonic and wall seconds that move only on advance()."""

    def __init__(self) -> None:
        self.mono = MONO_START
        self.wall = WALL_START

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


class _Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self) -> str:
        return f"<{self.status} {self.headers!r} {self.body[:200]!r}>"


class _Capture:
    def __init__(self, result: dict, port: int, token: str) -> None:
        self.result = result
        self.id = str(result["capture_id"])
        self.base_url = str(result["base_url"])
        self.port = port
        self.token = token


class _Daemon:
    def __init__(
        self,
        state: object,
        httpd: object,
        thread: threading.Thread,
        clock: _FakeClock,
        log: list[str],
    ) -> None:
        self.state = state
        self.httpd = httpd
        self.thread = thread
        self.clock = clock
        self.log = log
        self.port = int(httpd.server_address[1])  # type: ignore[attr-defined]
        self.capture_ids: list[str] = []

    @property
    def registry(self) -> object:
        return self.state.http_capture  # type: ignore[attr-defined]


def _http_request(
    port: int,
    method: str,
    target: str,
    *,
    body: bytes = b"",
    headers: tuple[str, ...] = (),
    length: bool | None = None,
) -> bytes:
    """Raw request bytes; the target goes out as UTF-8, byte for byte."""
    lines = [f"{method} {target} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
    lines.extend(headers)
    if length is None:
        length = bool(body) or method != "GET"
    if length:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def _parse_response(raw: bytes) -> _Response:
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        raise AssertionError(f"no HTTP response header in {raw[:200]!r}")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return _Response(status, headers, body)


def _raw_exchange(port: int, request: bytes, timeout: float = IO_TIMEOUT_S) -> _Response:
    """One request on its own socket, read to EOF (as test_loopback._raw_http)."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    chunks: list[bytes] = []
    try:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        while True:
            try:
                data = sock.recv(65536)
            except (ConnectionResetError, ConnectionAbortedError):
                if chunks:
                    break
                raise
            if not data:
                break
            chunks.append(data)
    finally:
        sock.close()
    return _parse_response(b"".join(chunks))


def _utc_seconds(value: object) -> float:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AssertionError(f"expected an ISO-8601 UTC timestamp ending in Z, got {value!r}")
    parsed = datetime.fromisoformat(value[:-1])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _redacted_sha256(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_of_size(size: int) -> bytes:
    """A compact, ASCII, secret-free JSON object of exactly ``size`` bytes.

    Its redacted form is itself, so its stored body bytes equal its length
    whatever the implementation counts (raw bytes or the hashed form).
    """
    if size == 2:
        return b"{}"
    if size < 8:
        raise ValueError(f"no JSON object of {size} bytes in this shape")
    body = b'{"p":"' + b"x" * (size - 8) + b'"}'
    if len(body) != size:
        raise AssertionError(f"built {len(body)} bytes, wanted {size}")
    return body


def _tolerant_exchange(port: int, request: bytes, timeout: float = IO_TIMEOUT_S) -> bytes:
    """Send, half-close, read to EOF; a reset or abort just ends the read."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    chunks: list[bytes] = []
    try:
        try:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        while True:
            try:
                data = sock.recv(65536)
            except (ConnectionResetError, ConnectionAbortedError):
                break
            if not data:
                break
            chunks.append(data)
    finally:
        sock.close()
    return b"".join(chunks)


class HttpCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.http_capture = _http_capture_module()
        self.d = self._daemon(bind=True)
        self.registry = self.d.registry

    # -- fixtures -----------------------------------------------------------

    def _daemon(
        self,
        *,
        bind: bool,
        live_profile: bool = False,
        live_admin: bool = False,
    ) -> _Daemon:
        state = loopback.ServerState(
            KEY, live_profile=live_profile, live_admin_enabled=live_admin
        )
        if bind:
            bind_both_peers(state, run_id=BOUND_RUN_ID)
        registry_cls = getattr(self.http_capture, "HttpCaptureRegistry", None)
        if registry_cls is None:
            self.fail("dayz_mcp.http_capture.HttpCaptureRegistry is missing")
        registry = getattr(state, "http_capture", None)
        if not isinstance(registry, registry_cls):
            self.fail(
                "ServerState.http_capture must be an HttpCaptureRegistry built "
                f"by the constructor; got {registry!r}"
            )
        clock = _FakeClock()
        registry.clock = clock.monotonic
        registry.wall_clock = clock.time
        log: list[str] = []
        httpd = loopback.create_http_server(0, state, log_sink=log.append)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        daemon = _Daemon(state, httpd, thread, clock, log)
        self.addCleanup(self._close_daemon, daemon)
        return daemon

    def _close_daemon(self, daemon: _Daemon) -> None:
        for capture_id in list(daemon.capture_ids):
            try:
                self._op(daemon, "stop", capture_id=capture_id)
            except Exception:
                pass
        daemon.httpd.shutdown()  # type: ignore[attr-defined]
        daemon.thread.join(timeout=2.0)
        daemon.httpd.server_close()  # type: ignore[attr-defined]

    # -- control route ------------------------------------------------------

    def _control(
        self, d: _Daemon, payload: dict, *, key: str | None = KEY
    ) -> tuple[int, dict, str]:
        path = "/http-capture"
        if key is not None:
            path += "?" + urllib.parse.urlencode({"key": key})
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", d.port, timeout=IO_TIMEOUT_S)
        try:
            conn.request(
                "POST", path, body=data, headers={"Content-Type": "application/json"}
            )
            response = conn.getresponse()
            status = int(response.status)
            raw = response.read().decode("utf-8")
        finally:
            conn.close()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            self.fail(f"/http-capture answered non-JSON: {status} {raw[:200]!r}")
        if not isinstance(body, dict):
            self.fail(f"/http-capture answered a non-object: {status} {raw[:200]!r}")
        capture_id = body.get("capture_id")
        if payload.get("op") == "start" and status == 200 and isinstance(capture_id, str):
            # Recorded for cleanup even when the start should have been refused.
            d.capture_ids.append(capture_id)
        return status, body, raw

    def _op(self, d: _Daemon, op: str, **fields: object) -> tuple[int, dict, str]:
        payload: dict[str, object] = {"identity": dict(IDENTITY), "op": op}
        payload.update(fields)
        return self._control(d, payload)

    def _start(self, d: _Daemon | None = None, **args: object) -> _Capture:
        d = d or self.d
        status, body, _ = self._op(d, "start", **args)
        self.assertEqual(status, 200, f"http_capture_start failed: {status} {body}")
        capture_id = body.get("capture_id")
        self.assertIsInstance(capture_id, str, body)
        self.assertTrue(capture_id, body)
        base_url = body.get("base_url")
        self.assertIsInstance(base_url, str, body)
        match = BASE_URL_RE.match(base_url)
        self.assertIsNotNone(
            match, f"base_url must match {BASE_URL_RE.pattern}: {base_url!r}"
        )
        return _Capture(body, int(match.group(1)), match.group(2))

    def _read(self, d: _Daemon, capture_id: str, **args: object) -> dict:
        status, body, _ = self._op(d, "read", capture_id=capture_id, **args)
        self.assertEqual(status, 200, f"http_capture_read failed: {status} {body}")
        self.assertIsInstance(body.get("entries"), list, body)
        return body

    def _read_raw(self, d: _Daemon, capture_id: str) -> tuple[dict, str]:
        status, body, raw = self._op(d, "read", capture_id=capture_id)
        self.assertEqual(status, 200, f"http_capture_read failed: {status} {body}")
        return body, raw

    def _entries_all(self, d: _Daemon, capture_id: str) -> list[dict]:
        entries: list[dict] = []
        since = 0
        for _ in range(64):
            page = self._read(d, capture_id, since_seq=since, max_entries=32)
            if not page["entries"]:
                return entries
            self.assertLessEqual(len(page["entries"]), 32)
            entries.extend(page["entries"])
            self.assertNotEqual(page["next_seq"], since, "next_seq must advance")
            since = page["next_seq"]
        self.fail("paging did not reach the end in 64 reads")

    # -- sink ---------------------------------------------------------------

    def _sink(
        self,
        cap: _Capture,
        method: str,
        rel: str,
        *,
        body: bytes = b"",
        headers: tuple[str, ...] = (),
        length: bool | None = None,
    ) -> _Response:
        request = _http_request(
            cap.port,
            method,
            f"/sink/{cap.token}/{rel}",
            body=body,
            headers=headers,
            length=length,
        )
        return _raw_exchange(cap.port, request)

    def _post_json(self, cap: _Capture, rel: str, value: object) -> _Response:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return self._sink(cap, "POST", rel, body=body, headers=(CT_JSON,))

    def _assert_default_ok(self, response: _Response) -> None:
        self.assertEqual(response.status, 200, response)
        self.assertTrue(
            response.body,
            "the default answer carries a body: a 204 or an empty 200 is engine error 8",
        )
        self.assertTrue(
            response.headers.get("content-type", "").lower().startswith("application/json"),
            response,
        )
        self.assertEqual(response.json(), {"ok": True})

    def _not_found_body(self, port: int) -> bytes:
        response = _raw_exchange(port, _http_request(port, "GET", "/nope/x"))
        self.assertEqual(response.status, 404, response)
        self.assertEqual(response.json(), NOT_FOUND)
        return response.body

    def _assert_refused(self, port: int) -> None:
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=IO_TIMEOUT_S)
        except ConnectionRefusedError:
            return
        except TimeoutError:
            self.fail(f"connect to port {port} timed out instead of being refused")
        sock.close()
        self.fail(f"the sink listener on port {port} still accepts with no capture armed")

    def _assert_content_length(self, response: _Response) -> None:
        self.assertEqual(
            response.headers.get("content-length"),
            str(len(response.body)),
            f"Content-Length must be the bytes of the body: {response!r}",
        )

    def _wait_sink_idle(self, cap: _Capture, label: str) -> None:
        """Wait, bounded, until the sink holds MAX_IN_FLIGHT requests at once.

        Each probe sends its head and holds its 2-byte body back, so the probes
        hold their slots together; a slot still taken by an earlier handler
        makes the last probe be closed unanswered. When every probe of one
        round is answered 200, no earlier handler is still running.
        """
        deadline = time.monotonic() + IO_TIMEOUT_S
        attempt = 0
        while True:
            attempt += 1
            probes: list[socket.socket] = []
            answers: list[bytes] = []
            try:
                for index in range(MAX_IN_FLIGHT):
                    sock = socket.create_connection(
                        ("127.0.0.1", cap.port), timeout=IO_TIMEOUT_S
                    )
                    probes.append(sock)
                    sock.sendall(
                        _http_request(
                            cap.port,
                            "POST",
                            f"/sink/{cap.token}/{label}/{attempt}/{index}",
                            headers=(CT_JSON, "Content-Length: 2"),
                            length=False,
                        )
                    )
                # Let the sink accept every probe before any of them can finish.
                time.sleep(0.05)
                for sock in probes:
                    try:
                        sock.sendall(b"{}")
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                for sock in probes:
                    received = b""
                    try:
                        while True:
                            data = sock.recv(65536)
                            if not data:
                                break
                            received += data
                    except OSError:
                        pass
                    answers.append(received)
            finally:
                for sock in probes:
                    sock.close()
            if all(
                answer.startswith(b"HTTP/1.") and answer.split(b" ", 2)[1:2] == [b"200"]
                for answer in answers
            ):
                return
            if time.monotonic() >= deadline:
                self.fail(
                    f"the sink never held {MAX_IN_FLIGHT} requests at once again: "
                    f"{[answer[:40] for answer in answers]!r}"
                )
            time.sleep(0.02)

    # -- positives ------------------------------------------------------------

    def test_p1_start_opens_loopback_listener_with_32_hex_token_base_url(self) -> None:
        cap = self._start()
        listener = self.registry.listener
        self.assertIsNotNone(listener, "an armed capture must open the sink listener")
        self.assertEqual(listener.server_address[0], "127.0.0.1")
        self.assertEqual(int(listener.server_address[1]), cap.port)
        self.assertIsInstance(cap.result.get("limits"), dict, cap.result)
        self.assertAlmostEqual(
            _utc_seconds(cap.result.get("expires_at_utc")), WALL_START + 900, delta=1.0
        )
        second = self._start(ttl_s=30)
        self.assertEqual(second.port, cap.port, "one listener serves every armed capture")
        self.assertNotEqual(second.token, cap.token, "one token per capture")
        self.assertNotEqual(second.id, cap.id)
        self.assertAlmostEqual(
            _utc_seconds(second.result.get("expires_at_utc")), WALL_START + 30, delta=1.0
        )
        self._assert_default_ok(self._post_json(cap, "p1/ping", {"n": 1}))

    def test_p2_post_records_exact_target_query_pairs_and_redacted_jwt(self) -> None:
        cap = self._start()
        self.d.clock.advance(2.5)
        secret = "f078-p2-jwt-value"
        raw_body = ('{"jwt_token":"%s","event_name":"player.kill"}' % secret).encode("utf-8")
        target = "events/U?version=1&tick=12.5"
        response = self._sink(cap, "POST", target, body=raw_body, headers=(CT_JSON,))
        self._assert_default_ok(response)

        read, raw = self._read_raw(self.d, cap.id)
        self.assertEqual(read.get("state"), "armed")
        self.assertEqual(read.get("accepted"), 1)
        self.assertEqual(read.get("dropped"), 0)
        self.assertEqual(read.get("rejected"), {})
        self.assertEqual(len(read["entries"]), 1, read)
        entry = read["entries"][0]
        self.assertEqual(set(entry), ENTRY_FIELDS)
        expected_body = {"jwt_token": REDACTED, "event_name": "player.kill"}
        self.assertIsInstance(entry["seq"], int)
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["target"], target)
        self.assertEqual(entry["path"], "events/U")
        self.assertEqual(entry["query"], "version=1&tick=12.5")
        self.assertEqual(entry["query_pairs"], [["version", "1"], ["tick", "12.5"]])
        self.assertEqual(entry["content_type"], "application/json")
        self.assertEqual(entry["content_length"], len(raw_body))
        self.assertEqual(entry["body_kind"], "json")
        self.assertEqual(entry["body"], expected_body)
        self.assertEqual(entry["body"]["jwt_token"], REDACTED)
        self.assertEqual(entry["redacted"], ["body.jwt_token"])
        self.assertEqual(entry["body_sha256"], _redacted_sha256(expected_body))
        self.assertEqual(entry["response_status"], 200)
        self.assertIsNone(entry["rejected"])
        self.assertEqual(entry["bound_run_ids"], [BOUND_RUN_ID])
        self.assertEqual(entry["ms_since_start"], 2500)
        self.assertAlmostEqual(
            _utc_seconds(entry["received_at_utc"]), WALL_START + 2.5, delta=1.0
        )
        self.assertNotIn(secret, raw)

    def test_p3_target_bytes_arrive_without_decoding_or_reordering(self) -> None:
        cap = self._start()
        path = "p3/a%20b+c//d/\u00e9"
        query = "q=%20x+y&empty=&z=\u00e9&b=2&a=1"
        target = path + "?" + query
        response = self._sink(cap, "POST", target, body=b'{"n":3}', headers=(CT_JSON,))
        self._assert_default_ok(response)
        entries = self._read(self.d, cap.id)["entries"]
        self.assertEqual(len(entries), 1, entries)
        entry = entries[0]
        self.assertEqual(entry["target"], target)
        self.assertEqual(entry["path"], path)
        self.assertEqual(entry["query"], query)
        self.assertEqual(
            entry["query_pairs"],
            [["q", "%20x+y"], ["empty", ""], ["z", "\u00e9"], ["b", "2"], ["a", "1"]],
        )

    def test_p4_canned_rule_answers_its_36_byte_body(self) -> None:
        cap = self._start(responses=[{"path": "matches", "body": UUID36}])
        response = self._sink(
            cap,
            "POST",
            "matches?version=1&tick=3",
            body=b'{"mods":[]}',
            headers=(CT_JSON,),
        )
        self.assertEqual(response.status, 200, response)
        self.assertEqual(response.body, UUID36.encode("ascii"))
        self.assertEqual(len(response.body), 36)
        self.assertTrue(
            response.headers.get("content-type", "").lower().startswith("text/plain"),
            response,
        )
        # Another path, or the rule's path with another method, gets the default.
        self._assert_default_ok(
            self._sink(cap, "POST", "matches/other", body=b"{}", headers=(CT_JSON,))
        )
        self._assert_default_ok(self._sink(cap, "GET", "matches"))
        entries = self._read(self.d, cap.id)["entries"]
        self.assertEqual(
            [entry["target"] for entry in entries],
            ["matches?version=1&tick=3", "matches/other", "matches"],
        )
        self.assertEqual(entries[0]["path"], "matches")
        self.assertEqual([entry["response_status"] for entry in entries], [200, 200, 200])

    def test_p5_get_without_body_is_empty_and_since_seq_pages(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._sink(cap, "GET", "p5/get?x=1"))
        self._assert_default_ok(self._post_json(cap, "p5/two", {"n": 2}))
        self._assert_default_ok(self._post_json(cap, "p5/three", {"n": 3}))

        first = self._read(self.d, cap.id, since_seq=0, max_entries=1)
        self.assertEqual(len(first["entries"]), 1, first)
        e1 = first["entries"][0]
        self.assertEqual(e1["method"], "GET")
        self.assertEqual(e1["target"], "p5/get?x=1")
        self.assertEqual(e1["query_pairs"], [["x", "1"]])
        self.assertEqual(e1["body_kind"], "empty")
        self.assertIsNone(e1["body"])

        second = self._read(self.d, cap.id, since_seq=first["next_seq"], max_entries=1)
        self.assertEqual([e["target"] for e in second["entries"]], ["p5/two"])
        self.assertGreater(second["entries"][0]["seq"], e1["seq"])
        self.assertGreater(second["next_seq"], first["next_seq"])

        third = self._read(self.d, cap.id, since_seq=second["next_seq"], max_entries=32)
        self.assertEqual([e["target"] for e in third["entries"]], ["p5/three"])
        self.assertGreater(third["next_seq"], second["next_seq"])

        tail = self._read(self.d, cap.id, since_seq=third["next_seq"], max_entries=32)
        self.assertEqual(tail["entries"], [])
        self.assertEqual(tail["next_seq"], third["next_seq"])

        whole = self._read(self.d, cap.id, since_seq=0, max_entries=32)
        self.assertEqual(
            [e["target"] for e in whole["entries"]], ["p5/get?x=1", "p5/two", "p5/three"]
        )
        seqs = [e["seq"] for e in whole["entries"]]
        self.assertEqual(seqs, sorted(set(seqs)), "seq strictly increasing")
        two = self._read(self.d, cap.id, since_seq=0, max_entries=2)
        self.assertEqual(len(two["entries"]), 2)

    def test_p6_entries_carry_bound_run_ids_and_empty_without_binding(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "p6/bound", {"n": 1}))
        entries = self._read(self.d, cap.id)["entries"]
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["bound_run_ids"], [BOUND_RUN_ID])

        unbound = self._daemon(bind=False)
        other = self._start(unbound)
        self._assert_default_ok(self._post_json(other, "p6/unbound", {"n": 2}))
        entries = self._read(unbound, other.id)["entries"]
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["bound_run_ids"], [])

    @slow_test
    def test_p7_stop_and_ttl_close_the_sink_and_then_the_listener(self) -> None:
        d = self.d
        a = self._start(ttl_s=30)
        b = self._start(ttl_s=30)
        not_found = self._not_found_body(a.port)
        self._assert_default_ok(self._post_json(a, "p7/a1", {"n": 1}))
        self._assert_default_ok(self._post_json(b, "p7/b1", {"n": 1}))

        # stop: the token stops working, the entries stay readable.
        status, stopped, _ = self._op(d, "stop", capture_id=a.id)
        self.assertEqual(status, 200, stopped)
        self.assertEqual(stopped.get("state"), "stopped")
        self.assertEqual(stopped.get("accepted"), 1)
        self.assertIn("rejected", stopped)
        response = self._sink(a, "POST", "p7/a2")
        self.assertEqual(response.status, 404, response)
        self.assertEqual(response.body, not_found)
        read_a = self._read(d, a.id)
        self.assertEqual(read_a.get("state"), "stopped")
        self.assertEqual([e["target"] for e in read_a["entries"]], ["p7/a1"])

        # TTL on the fake clock, observed by the next sink request.
        d.clock.advance(29)
        self._assert_default_ok(self._post_json(b, "p7/b2", {"n": 2}))
        d.clock.advance(2)
        response = self._sink(b, "POST", "p7/b3")
        self.assertEqual(response.status, 404, response)
        self.assertEqual(response.body, not_found)
        read_b = self._read(d, b.id)
        self.assertEqual(read_b.get("state"), "expired")
        self.assertEqual([e["target"] for e in read_b["entries"]], ["p7/b1", "p7/b2"])
        self.assertIsNone(self.registry.listener, "no capture armed: no listener")
        self._assert_refused(b.port)

        # TTL observed by a control op alone.
        e = self._start(ttl_s=30)
        self._assert_default_ok(self._post_json(e, "p7/e1", {"n": 1}))
        d.clock.advance(31)
        read_e = self._read(d, e.id)
        self.assertEqual(read_e.get("state"), "expired")
        self.assertEqual([x["target"] for x in read_e["entries"]], ["p7/e1"])
        self.assertIsNone(self.registry.listener, "expiry seen by read closes the listener")
        self._assert_refused(e.port)

        # stop of the last armed capture closes the listener before answering.
        c = self._start()
        self._assert_default_ok(self._post_json(c, "p7/c1", {"n": 1}))
        status, stopped, _ = self._op(d, "stop", capture_id=c.id)
        self.assertEqual(status, 200, stopped)
        self.assertIsNone(self.registry.listener, "last stop closes the listener")
        self._assert_refused(c.port)

    # -- positives added after the review -------------------------------------

    def test_p8_default_and_canned_answers_carry_exact_content_length(self) -> None:
        multibyte = "\u00e9t\u00e9-\u20ac"  # 5 characters, 9 UTF-8 bytes
        self.assertNotEqual(len(multibyte), len(multibyte.encode("utf-8")))
        cap = self._start(
            responses=[
                {"path": "matches", "body": UUID36},
                {"path": "p8/multibyte", "body": multibyte},
                {"path": "p8/empty", "body": ""},
            ]
        )
        default_post = self._post_json(cap, "p8/default", {"n": 8})
        self._assert_default_ok(default_post)
        self._assert_content_length(default_post)
        default_get = self._sink(cap, "GET", "p8/get?x=1")
        self._assert_default_ok(default_get)
        self._assert_content_length(default_get)

        canned = self._sink(
            cap, "POST", "matches?version=1&tick=3", body=b'{"mods":[]}', headers=(CT_JSON,)
        )
        self.assertEqual(canned.status, 200, canned)
        self.assertEqual(canned.body, UUID36.encode("ascii"))
        self.assertEqual(canned.headers.get("content-length"), "36", canned)
        self._assert_content_length(canned)

        multi = self._sink(cap, "POST", "p8/multibyte", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(multi.status, 200, multi)
        self.assertEqual(multi.body, multibyte.encode("utf-8"))
        self.assertEqual(
            multi.headers.get("content-length"), str(len(multibyte.encode("utf-8"))), multi
        )
        self._assert_content_length(multi)

        empty = self._sink(cap, "POST", "p8/empty", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(empty.status, 200, empty)
        self.assertEqual(empty.body, b"")
        self.assertEqual(empty.headers.get("content-length"), "0", empty)

    def test_p9_json_body_sent_as_octet_stream_is_parsed_and_redacted(self) -> None:
        secret = "f078-p9-jwt-value"
        raw_body = ('{"jwt_token":"%s","event_name":"player.kill"}' % secret).encode("utf-8")
        expected_body = {"jwt_token": REDACTED, "event_name": "player.kill"}
        target = "servers/unlock?version=1&tick=1"
        plain = self._start()
        text = self._start(store_text_bodies=True)
        for cap in (plain, text):
            self._assert_default_ok(
                self._sink(cap, "POST", target, body=raw_body, headers=(CT_OCTET,))
            )
        for name, cap in (("default", plain), ("store_text_bodies", text)):
            with self.subTest(capture=name):
                read, raw = self._read_raw(self.d, cap.id)
                self.assertEqual(len(read["entries"]), 1, read)
                entry = read["entries"][0]
                self.assertEqual(entry["target"], target)
                self.assertEqual(entry["content_type"], "application/octet-stream")
                self.assertEqual(entry["content_length"], len(raw_body))
                self.assertIsNone(entry["rejected"])
                self.assertEqual(entry["body_kind"], "json")
                self.assertEqual(entry["body"], expected_body)
                self.assertEqual(entry["redacted"], ["body.jwt_token"])
                self.assertEqual(entry["body_sha256"], _redacted_sha256(expected_body))
                self.assertNotIn(secret, raw)

    def test_p10_store_text_bodies_keeps_a_form_body_as_redacted_text(self) -> None:
        cap = self._start(store_text_bodies=True)
        pw = "f078-sentinel-p10-pw"
        secret = "f078-sentinel-p10-secret"
        token = "f078-sentinel-p10-token"
        form = f"password={pw}&x=1&client_secret={secret}&empty=&tick=12.5"
        form_ct = "application/x-www-form-urlencoded"
        self._assert_default_ok(
            self._sink(
                cap,
                "POST",
                "p10/form",
                body=form.encode("utf-8"),
                headers=(f"Content-Type: {form_ct}",),
            )
        )
        # Recognised by content: the same shape sent as octet-stream.
        octet_form = f"token={token}&version=1"
        self._assert_default_ok(
            self._sink(
                cap, "POST", "p10/octet", body=octet_form.encode("utf-8"), headers=(CT_OCTET,)
            )
        )

        read, raw = self._read_raw(self.d, cap.id)
        by_target = {e["target"]: e for e in read["entries"]}
        self.assertEqual(sorted(by_target), ["p10/form", "p10/octet"])
        entry = by_target["p10/form"]
        expected = f"password={REDACTED}&x=1&client_secret={REDACTED}&empty=&tick=12.5"
        self.assertEqual(entry["body_kind"], "text")
        self.assertEqual(entry["body"], expected)
        self.assertEqual(sorted(entry["redacted"]), ["body.client_secret", "body.password"])
        self.assertEqual(
            entry["body_sha256"], hashlib.sha256(expected.encode("utf-8")).hexdigest()
        )
        self.assertEqual(entry["content_length"], len(form.encode("utf-8")))
        self.assertEqual(entry["content_type"], form_ct)
        self.assertIsNone(entry["rejected"])

        octet = by_target["p10/octet"]
        octet_expected = f"token={REDACTED}&version=1"
        self.assertEqual(octet["body_kind"], "text")
        self.assertEqual(octet["body"], octet_expected)
        self.assertEqual(octet["redacted"], ["body.token"])
        self.assertEqual(
            octet["body_sha256"], hashlib.sha256(octet_expected.encode("utf-8")).hexdigest()
        )
        for value in (pw, secret, token):
            self.assertNotIn(value, raw)

    def test_p11_canned_rule_status_method_and_content_type_are_honoured(self) -> None:
        cap = self._start(
            responses=[
                {
                    "path": "p11/created",
                    "method": "GET",
                    "status": 201,
                    "body": "created",
                    "content_type": "text/plain",
                },
                {
                    "path": "p11/json",
                    "method": "POST",
                    "status": 202,
                    "body": '{"c":1}',
                    "content_type": "application/json",
                },
                {"path": "p11/fail", "status": 500, "body": "boom"},
            ]
        )
        created = self._sink(cap, "GET", "p11/created?x=1")
        self.assertEqual(created.status, 201, created)
        self.assertEqual(created.body, b"created")
        self.assertTrue(
            created.headers.get("content-type", "").lower().startswith("text/plain"), created
        )
        self._assert_content_length(created)
        # The GET rule does not answer a POST to its path.
        self._assert_default_ok(
            self._sink(cap, "POST", "p11/created", body=b"{}", headers=(CT_JSON,))
        )
        accepted_json = self._sink(cap, "POST", "p11/json", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(accepted_json.status, 202, accepted_json)
        self.assertEqual(accepted_json.body, b'{"c":1}')
        self.assertTrue(
            accepted_json.headers.get("content-type", "").lower().startswith("application/json"),
            accepted_json,
        )
        self._assert_content_length(accepted_json)
        # Defaults: method POST, content_type text/plain.
        failed = self._sink(cap, "POST", "p11/fail", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(failed.status, 500, failed)
        self.assertEqual(failed.body, b"boom")
        self.assertTrue(
            failed.headers.get("content-type", "").lower().startswith("text/plain"), failed
        )
        self._assert_content_length(failed)

        entries = self._read(self.d, cap.id)["entries"]
        self.assertEqual(
            [(e["method"], e["target"], e["response_status"]) for e in entries],
            [
                ("GET", "p11/created?x=1", 201),
                ("POST", "p11/created", 200),
                ("POST", "p11/json", 202),
                ("POST", "p11/fail", 500),
            ],
        )
        self.assertTrue(all(e["rejected"] is None for e in entries))

    def test_p12_stopped_capture_stays_readable_600_s_then_is_unknown(self) -> None:
        d = self.d
        cap = self._start(ttl_s=30)
        self._assert_default_ok(self._post_json(cap, "p12/one", {"n": 1}))
        status, stopped, _ = self._op(d, "stop", capture_id=cap.id)
        self.assertEqual(status, 200, stopped)

        # Past the 30 s TTL and almost at the end of the retention: still stopped.
        d.clock.advance(RETENTION_AFTER_STOP_S - 1)
        late = self._read(d, cap.id)
        self.assertEqual(late.get("state"), "stopped")
        self.assertEqual(late.get("accepted"), 1)
        self.assertEqual([e["target"] for e in late["entries"]], ["p12/one"])

        # The error code, on an id that names no capture.
        status, body, _ = self._op(d, "read", capture_id=secrets.token_hex(16))
        self.assertTrue(400 <= status < 500, f"unknown id: {status} {body}")
        self.assertEqual(body.get("error"), UNKNOWN_CAPTURE)

        d.clock.advance(2)
        status, body, raw = self._op(d, "read", capture_id=cap.id)
        self.assertTrue(400 <= status < 500, f"read after retention: {status} {body}")
        self.assertEqual(body.get("error"), UNKNOWN_CAPTURE)
        self.assertNotIn("p12/one", raw)

    def test_p13_empty_capture_reads_no_entries_and_empty_entry_has_no_hash(self) -> None:
        cap = self._start()
        first = self._read(self.d, cap.id)
        self.assertEqual(first["entries"], [])
        self.assertEqual(first.get("accepted"), 0)
        next_seq = first.get("next_seq")
        self.assertIsInstance(next_seq, int, first)
        self.assertNotIsInstance(next_seq, bool, first)

        self._assert_default_ok(self._sink(cap, "GET", "p13/get"))
        self._assert_default_ok(
            self._sink(cap, "POST", "p13/zero", headers=(CT_JSON,), length=True)
        )
        after = self._read(self.d, cap.id, since_seq=next_seq)
        self.assertEqual([e["target"] for e in after["entries"]], ["p13/get", "p13/zero"])
        for entry in after["entries"]:
            with self.subTest(target=entry["target"]):
                self.assertEqual(entry["body_kind"], "empty")
                self.assertIsNone(entry["body"])
                self.assertIsNone(entry["body_sha256"])
                self.assertIsNone(entry["rejected"])

    def test_p14_bound_run_ids_are_a_snapshot_taken_when_the_request_arrives(self) -> None:
        d = self._daemon(bind=False)
        instances = bind_both_peers(d.state, run_id=BOUND_RUN_ID)
        cap = self._start(d)
        self._assert_default_ok(self._post_json(cap, "p14/bound", {"n": 1}))

        state = d.state
        with state._lock:  # type: ignore[attr-defined]
            for instance in instances:
                state._bindings.pop(instance, None)  # type: ignore[attr-defined]
        self._assert_default_ok(self._post_json(cap, "p14/after", {"n": 2}))

        entries = self._read(d, cap.id)["entries"]
        self.assertEqual(
            [(e["target"], e["bound_run_ids"]) for e in entries],
            [("p14/bound", [BOUND_RUN_ID]), ("p14/after", [])],
        )

    # -- negatives ------------------------------------------------------------

    # Naive: any token, trimmed, uppercase or absolute-form target is recorded.
    def test_n1_foreign_trimmed_uppercase_or_absolute_token_is_plain_404(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n1/ok", {"n": 1}))
        read = self._read(self.d, cap.id)
        self.assertEqual([e["target"] for e in read["entries"]], ["n1/ok"])

        not_found = self._not_found_body(cap.port)
        foreign = secrets.token_hex(16)
        while foreign == cap.token:
            foreign = secrets.token_hex(16)
        cases = {
            "foreign": f"/sink/{foreign}/n1/x",
            "trimmed": f"/sink/{cap.token[:-1]}/n1/x",
            "uppercase": f"/sink/{cap.token.upper()}/n1/x",
            "absolute": f"http://127.0.0.1:{cap.port}/sink/{cap.token}/n1/x",
        }
        for name, target in cases.items():
            with self.subTest(case=name):
                if name == "uppercase" and cap.token.upper() == cap.token:
                    self.skipTest("token without a-f letters has no uppercase form")
                response = _raw_exchange(
                    cap.port, _http_request(cap.port, "POST", target)
                )
                self.assertEqual(response.status, 404, f"{name}: {response!r}")
                self.assertEqual(response.body, not_found, name)
        read = self._read(self.d, cap.id)
        self.assertEqual([e["target"] for e in read["entries"]], ["n1/ok"])
        self.assertEqual(read.get("accepted"), 1)

    # Naive: no body cap; it reads and stores 64 KiB + 1.
    def test_n2_body_over_64_kib_is_413_unread_and_stored_without_body(self) -> None:
        cap = self._start()
        octet = "Content-Type: application/octet-stream"
        response = self._sink(
            cap, "POST", "n2/max", body=b"x" * MAX_BODY_BYTES, headers=(octet,)
        )
        self._assert_default_ok(response)

        # Headers only: a sink that tried to read the body would meet EOF.
        request = _http_request(
            cap.port,
            "POST",
            f"/sink/{cap.token}/n2/over",
            headers=(octet, f"Content-Length: {MAX_BODY_BYTES + 1}"),
            length=False,
        )
        response = _raw_exchange(cap.port, request)
        self.assertEqual(response.status, 413, response)

        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("accepted"), 1)
        self.assertEqual(read.get("rejected"), {"body_too_large": 1})
        entries = read["entries"]
        self.assertEqual([e["target"] for e in entries], ["n2/max", "n2/over"])
        ok, big = entries
        self.assertEqual(ok["content_length"], MAX_BODY_BYTES)
        self.assertIsNone(ok["rejected"])
        self.assertEqual(ok["response_status"], 200)
        self.assertEqual(big["rejected"], "body_too_large")
        self.assertEqual(big["content_length"], MAX_BODY_BYTES + 1)
        self.assertIsNone(big["body"])
        self.assertIsNone(big["body_sha256"])
        self.assertEqual(big["response_status"], 413)

    # Naive: reads chunked or the first Content-Length, and any target length.
    def test_n3_chunked_or_two_lengths_is_400_and_long_target_is_414(self) -> None:
        cap = self._start()
        rel_max = "n3/" + "a" * (MAX_TARGET_BYTES - len("n3/"))
        self.assertEqual(len(rel_max.encode("utf-8")), MAX_TARGET_BYTES)
        self._assert_default_ok(self._sink(cap, "GET", rel_max))
        self._assert_default_ok(
            self._sink(cap, "POST", "n3/one-length", body=b"{}", headers=(CT_JSON,))
        )

        framing = {
            "chunked": ("Transfer-Encoding: chunked",),
            "chunked_and_length": ("Transfer-Encoding: chunked", "Content-Length: 0"),
            "two_equal_lengths": ("Content-Length: 0", "Content-Length: 0"),
            "two_lengths": ("Content-Length: 0", "Content-Length: 5"),
        }
        for name, headers in framing.items():
            with self.subTest(framing=name):
                response = self._sink(
                    cap, "POST", f"n3/{name}", headers=headers, length=False
                )
                self.assertEqual(response.status, 400, f"{name}: {response!r}")

        rel_over = "n3/" + "a" * (MAX_TARGET_BYTES + 1 - len("n3/"))
        response = self._sink(cap, "GET", rel_over)
        self.assertEqual(response.status, 414, response)

        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("accepted"), 2)
        self.assertEqual(
            read.get("rejected"), {"bad_framing": len(framing), "target_too_long": 1}
        )
        entries = read["entries"]
        self.assertEqual(entries[0]["target"], rel_max)
        self.assertIsNone(entries[0]["rejected"])
        bad = [e for e in entries if e["rejected"] == "bad_framing"]
        self.assertEqual(
            sorted(e["target"] for e in bad), sorted(f"n3/{name}" for name in framing)
        )
        for entry in bad:
            self.assertEqual(entry["response_status"], 400)
            self.assertIsNone(entry["body"])
        too_long = [e for e in entries if e["rejected"] == "target_too_long"]
        self.assertEqual(len(too_long), 1)
        self.assertEqual(too_long[0]["response_status"], 414)

    # Naive: no entry cap; the 257th is stored or overwrites the first ones.
    @slow_test
    def test_n4_entry_257_is_503_capture_full_counted_in_dropped(self) -> None:
        cap = self._start()
        for index in range(MAX_ENTRIES):
            response = self._sink(cap, "GET", f"n4/{index}")
            if response.status != 200:
                self.fail(f"request {index + 1} of {MAX_ENTRIES} refused: {response!r}")
        response = self._sink(cap, "GET", "n4/overflow")
        self.assertEqual(response.status, 503, response)
        self.assertEqual(response.json().get("error"), "capture_full")

        read = self._read(self.d, cap.id, max_entries=1)
        self.assertEqual(read.get("dropped"), 1)
        self.assertEqual(read.get("accepted"), MAX_ENTRIES)
        entries = self._entries_all(self.d, cap.id)
        self.assertEqual(
            [e["target"] for e in entries], [f"n4/{i}" for i in range(MAX_ENTRIES)]
        )
        self.assertTrue(all(e["rejected"] is None for e in entries))
        self.assertTrue(all(e["response_status"] == 200 for e in entries))

    # Naive: one thread per connection, no in-flight cap.
    @slow_test
    def test_n5_fifth_concurrent_request_is_closed_and_main_port_answers(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n5/before", {"n": 1}))

        slow: list[socket.socket] = []

        def close_slow() -> None:
            while slow:
                try:
                    slow.pop().close()
                except OSError:
                    pass

        self.addCleanup(close_slow)
        for index in range(MAX_IN_FLIGHT):
            sock = socket.create_connection(("127.0.0.1", cap.port), timeout=IO_TIMEOUT_S)
            slow.append(sock)
            sock.sendall(
                (
                    f"POST /sink/{cap.token}/n5/slow{index} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{cap.port}\r\n"
                    f"{CT_JSON}\r\n"
                    "Content-Length: 10\r\n"
                    "\r\n"
                    "{"
                ).encode("ascii")
            )

        fifth = socket.create_connection(("127.0.0.1", cap.port), timeout=IO_TIMEOUT_S)
        received = b""
        try:
            try:
                fifth.sendall(
                    _http_request(
                        cap.port,
                        "POST",
                        f"/sink/{cap.token}/n5/fifth",
                        body=b"{}",
                        headers=(CT_JSON,),
                    )
                )
                fifth.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                while True:
                    data = fifth.recv(4096)
                    if not data:
                        break
                    received += data
            except (ConnectionResetError, ConnectionAbortedError):
                pass
            except TimeoutError:
                self.fail("the fifth in-flight request was held open, not closed")
        finally:
            fifth.close()
        self.assertFalse(
            received.startswith(b"HTTP/"),
            f"the fifth in-flight request was served: {received[:200]!r}",
        )

        conn = http.client.HTTPConnection("127.0.0.1", self.d.port, timeout=IO_TIMEOUT_S)
        try:
            conn.request("GET", "/status?" + urllib.parse.urlencode({"key": KEY}))
            status_response = conn.getresponse()
            status_response.read()
            self.assertEqual(status_response.status, 200)
        finally:
            conn.close()

        close_slow()
        deadline = time.monotonic() + IO_TIMEOUT_S
        after: _Response | None = None
        while time.monotonic() < deadline:
            try:
                after = self._post_json(cap, "n5/after", {"n": 2})
            except (AssertionError, OSError):
                after = None
            if after is not None and after.status == 200:
                break
            time.sleep(0.05)
        self.assertIsNotNone(after, "the sink did not recover after the slow sockets closed")
        self._assert_default_ok(after)
        # Review §3.3: no slow handler may still be writing while the test reads.
        self._wait_sink_idle(cap, "n5/idle")

        targets = [e["target"] for e in self._entries_all(self.d, cap.id)]
        self.assertIn("n5/before", targets)
        self.assertIn("n5/after", targets)
        self.assertNotIn("n5/fifth", targets)

    # Naive: stores every request header.
    def test_n6_only_content_type_and_length_headers_are_kept(self) -> None:
        cap = self._start()
        sentinels = {
            "Authorization": "Bearer f078-sentinel-authorization",
            "Cookie": "sid=f078-sentinel-cookie",
            "X-Api-Key": "f078-sentinel-x-api-key",
            "X-Forwarded-For": "f078-sentinel-x-forwarded-for",
            "User-Agent": "f078-sentinel-user-agent",
        }
        body = b'{"n":6}'
        headers = tuple(f"{name}: {value}" for name, value in sentinels.items())
        response = self._sink(cap, "POST", "n6/headers", body=body, headers=headers + (CT_JSON,))
        self._assert_default_ok(response)

        read, raw = self._read_raw(self.d, cap.id)
        self.assertEqual(len(read["entries"]), 1, read)
        entry = read["entries"][0]
        self.assertEqual(entry["content_type"], "application/json")
        self.assertEqual(entry["content_length"], len(body))
        self.assertEqual(entry["body"], {"n": 6})
        for name, value in sentinels.items():
            with self.subTest(header=name):
                self.assertNotIn(value.split()[-1].split("=")[-1], raw)

    # Naive: stores body and query secrets verbatim and hashes the original.
    def test_n7_secret_keys_redacted_at_any_depth_and_in_query(self) -> None:
        cap = self._start()
        secrets_by_letter = {letter: f"f078-sentinel-{letter}" for letter in "ABCDEF"}
        body_value = {
            "jwt_token": secrets_by_letter["A"],
            "server_password": secrets_by_letter["B"],
            "rcon_password": secrets_by_letter["C"],
            "nested": {"api_key": secrets_by_letter["D"]},
            "list": [{"token": secrets_by_letter["E"]}],
            "event_name": "player.kill",
            "note": "\u00e9",
        }
        raw_body = json.dumps(body_value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        target = f"n7/check?token={secrets_by_letter['F']}&version=1"
        response = self._sink(cap, "POST", target, body=raw_body, headers=(CT_JSON,))
        self._assert_default_ok(response)

        read, raw = self._read_raw(self.d, cap.id)
        self.assertEqual(len(read["entries"]), 1, read)
        entry = read["entries"][0]
        self.assertEqual(entry["body_kind"], "json")
        self.assertEqual(entry["body"]["event_name"], "player.kill")
        self.assertEqual(entry["body"]["note"], "\u00e9")
        self.assertIn(["version", "1"], entry["query_pairs"])
        self.assertTrue(entry["target"].startswith("n7/check?"), entry["target"])
        self.assertIn("version=1", entry["target"])

        for letter, value in secrets_by_letter.items():
            with self.subTest(secret=letter):
                self.assertNotIn(value, raw)
                self.assertNotIn(value, "\n".join(self.d.log))
        expected_body = {
            "jwt_token": REDACTED,
            "server_password": REDACTED,
            "rcon_password": REDACTED,
            "nested": {"api_key": REDACTED},
            "list": [{"token": REDACTED}],
            "event_name": "player.kill",
            "note": "\u00e9",
        }
        self.assertEqual(entry["body"], expected_body)
        self.assertEqual(entry["query_pairs"], [["token", REDACTED], ["version", "1"]])
        self.assertEqual(entry["query"], f"token={REDACTED}&version=1")
        self.assertEqual(
            sorted(entry["redacted"]),
            sorted(
                [
                    "body.jwt_token",
                    "body.server_password",
                    "body.rcon_password",
                    "body.nested.api_key",
                    "body.list.0.token",
                    "query.token",
                ]
            ),
        )
        self.assertEqual(entry["body_sha256"], _redacted_sha256(expected_body))
        self.assertNotEqual(entry["body_sha256"], hashlib.sha256(raw_body).hexdigest())

    # Naive: stores non-JSON bodies as text.
    def test_n8_non_json_body_is_opaque_with_length_only(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n8/json", {"n": 8}))
        cases = {
            "form": (
                "application/x-www-form-urlencoded",
                "f078-sentinel-n8-form",
                b"password=f078-sentinel-n8-form&x=1",
            ),
            "text": ("text/plain", "f078-sentinel-n8-text", b"plain text f078-sentinel-n8-text"),
            "broken_json": (
                "application/json",
                "f078-sentinel-n8-json",
                b'{"broken": f078-sentinel-n8-json',
            ),
        }
        for name, (content_type, _sentinel, body) in cases.items():
            response = self._sink(
                cap, "POST", f"n8/{name}", body=body, headers=(f"Content-Type: {content_type}",)
            )
            self._assert_default_ok(response)

        read, raw = self._read_raw(self.d, cap.id)
        by_target = {e["target"]: e for e in read["entries"]}
        self.assertEqual(
            sorted(by_target), sorted(["n8/json"] + [f"n8/{name}" for name in cases])
        )
        control = by_target["n8/json"]
        self.assertEqual(control["body_kind"], "json")
        self.assertEqual(control["body"], {"n": 8})
        for name, (content_type, sentinel, body) in cases.items():
            with self.subTest(body=name):
                entry = by_target[f"n8/{name}"]
                self.assertNotIn(sentinel, raw)
                self.assertEqual(entry["body_kind"], "opaque")
                self.assertIsNone(entry["body"])
                self.assertIsNone(entry["body_sha256"])
                self.assertEqual(entry["content_length"], len(body))
                self.assertEqual(entry["content_type"], content_type)

    # Naive: the listener serves any path, or the main port honours the token.
    def test_n9_sink_and_main_port_routes_stay_apart(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n9/ok", {"n": 1}))
        not_found = self._not_found_body(cap.port)

        main = self.d.port
        response = _raw_exchange(
            main, _http_request(main, "POST", f"/sink/{cap.token}/n9/main")
        )
        self.assertEqual(response.status, 401, response)
        self.assertEqual(response.json(), {"error": "unauthorized"})
        keyed = f"/sink/{cap.token}/n9/main?" + urllib.parse.urlencode({"key": KEY})
        response = _raw_exchange(main, _http_request(main, "POST", keyed))
        self.assertEqual(response.status, 404, response)
        self.assertEqual(response.json(), NOT_FOUND)

        key_query = urllib.parse.urlencode({"key": KEY})
        listener_routes = {
            "poll": ("GET", f"/poll?{key_query}&peer=server"),
            "status": ("GET", f"/status?{key_query}"),
            "http_capture": ("POST", f"/http-capture?{key_query}"),
            "enqueue": ("POST", f"/enqueue?{key_query}"),
        }
        for name, (method, target) in listener_routes.items():
            with self.subTest(route=name):
                response = _raw_exchange(
                    cap.port, _http_request(cap.port, method, target)
                )
                self.assertEqual(response.status, 404, f"{name}: {response!r}")
                self.assertEqual(response.body, not_found, name)
        read = self._read(self.d, cap.id)
        self.assertEqual([e["target"] for e in read["entries"]], ["n9/ok"])

    # Naive: control without key or identity, no capture cap, no live refusal, no arg checks.
    def test_n10_control_needs_key_and_refuses_limit_live_profile_and_bad_args(self) -> None:
        d = self.d
        first = self._start()

        start_payload = {"identity": dict(IDENTITY), "op": "start"}
        for name, key in (("no_key", None), ("wrong_key", "wrong-key")):
            with self.subTest(auth=name):
                status, body, _ = self._control(d, start_payload, key=key)
                self.assertEqual((status, body), (401, {"error": "unauthorized"}))
        status, body, _ = self._control(d, {"op": "start"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body.get("error"), "invalid_identity")

        rule = {"path": "matches", "body": UUID36}
        bad_start = {
            "ttl_s_29": {"ttl_s": 29},
            "ttl_s_3601": {"ttl_s": 3601},
            "ttl_s_str": {"ttl_s": "900"},
            "ttl_s_bool": {"ttl_s": True},
            "ttl_s_float": {"ttl_s": 900.5},
            "responses_str": {"responses": "matches"},
            "responses_9": {"responses": [dict(rule) for _ in range(MAX_RULES + 1)]},
            "rule_no_path": {"responses": [{"body": "x"}]},
            "rule_path_query": {"responses": [{"path": "matches?x=1"}]},
            "rule_body_4097": {"responses": [{"path": "m", "body": "x" * (MAX_RULE_BODY + 1)}]},
            "store_text_str": {"store_text_bodies": "yes"},
        }
        for name, args in bad_start.items():
            with self.subTest(start=name):
                status, body, _ = self._op(d, "start", **args)
                self.assertEqual(status, 400, f"{name}: {body}")
                self.assertEqual(body.get("error"), "bad_args", name)
        self._read(d, first.id, since_seq=0, max_entries=1)
        self._read(d, first.id, since_seq=0, max_entries=32)
        bad_read = {
            "max_entries_0": {"max_entries": 0},
            "max_entries_33": {"max_entries": 33},
            "max_entries_str": {"max_entries": "5"},
            "since_seq_negative": {"since_seq": -1},
        }
        for name, args in bad_read.items():
            with self.subTest(read=name):
                status, body, _ = self._op(d, "read", capture_id=first.id, **args)
                self.assertEqual(status, 400, f"{name}: {body}")
                self.assertEqual(body.get("error"), "bad_args", name)

        # Boundaries are accepted; with them four captures are armed.
        self._start(ttl_s=30)
        self._start(ttl_s=3600)
        rules = [
            {"path": f"r{i}", "body": "x" * MAX_RULE_BODY} for i in range(MAX_RULES)
        ]
        self._start(responses=rules, store_text_bodies=True)
        status, body, _ = self._op(d, "start")
        self.assertTrue(400 <= status < 500, f"fifth capture: {status} {body}")
        self.assertEqual(body.get("error"), "http_capture_limit")
        self.assertEqual(len(d.capture_ids), MAX_CAPTURES)

        with mock.patch.dict(os.environ, {loopback.LIVE_PROFILE_ENV: "1"}):
            enabled, admin = loopback.live_profile_from_env()
            self.assertTrue(enabled)
            live = self._daemon(bind=False, live_profile=enabled, live_admin=admin)
            status, body, _ = self._op(live, "start")
        self.assertEqual(status, 403, body)
        self.assertEqual(body.get("error"), "live_profile_refused")
        self.assertIsNone(live.registry.listener, "a refused start opens no socket")

    # -- negatives added after the review -------------------------------------

    # Naive: the sink keeps BaseHTTPRequestHandler.log_message, which prints the
    # request line (capture token, query secrets) to stderr.
    def test_n11_sink_requests_write_no_token_or_secret_to_stderr_or_log(self) -> None:
        cap = self._start()
        header_sentinels = {
            "Authorization": "f078-sentinel-n11-authorization",
            "Cookie": "f078-sentinel-n11-cookie",
            "X-Api-Key": "f078-sentinel-n11-x-api-key",
        }
        secrets_by_letter = {letter: f"f078-sentinel-n11-{letter}" for letter in "ABCDEF"}
        body_value = {
            "jwt_token": secrets_by_letter["A"],
            "server_password": secrets_by_letter["B"],
            "rcon_password": secrets_by_letter["C"],
            "nested": {"api_key": secrets_by_letter["D"]},
            "list": [{"token": secrets_by_letter["E"]}],
            "event_name": "player.kill",
        }
        raw_body = json.dumps(body_value, separators=(",", ":")).encode("utf-8")
        query_secret = secrets_by_letter["F"]
        headers = tuple(f"{name}: {value}" for name, value in header_sentinels.items())

        buffer = io.StringIO()
        saved_stderr = sys.stderr
        sys.stderr = buffer
        try:
            n6_like = self._sink(
                cap, "POST", "n11/headers", body=b'{"n":11}', headers=headers + (CT_JSON,)
            )
            n7_like = self._sink(
                cap,
                "POST",
                f"n11/check?token={query_secret}&version=1",
                body=raw_body,
                headers=(CT_JSON,),
            )
            too_large = _raw_exchange(
                cap.port,
                _http_request(
                    cap.port,
                    "POST",
                    f"/sink/{cap.token}/n11/big?token={query_secret}",
                    headers=(CT_OCTET, f"Content-Length: {MAX_BODY_BYTES + 1}"),
                    length=False,
                ),
            )
            chunked = self._sink(
                cap,
                "POST",
                f"n11/chunked?token={query_secret}",
                headers=("Transfer-Encoding: chunked",),
                length=False,
            )
            # Error paths of the stdlib (send_error, 501) log too.
            _tolerant_exchange(
                cap.port,
                _http_request(cap.port, "PUT", f"/sink/{cap.token}/n11/put?token={query_secret}"),
            )
        finally:
            sys.stderr = saved_stderr
        captured = buffer.getvalue()

        # Positive control: the requests reached the sink and were handled.
        self._assert_default_ok(n6_like)
        self._assert_default_ok(n7_like)
        self.assertEqual(too_large.status, 413, too_large)
        self.assertEqual(chunked.status, 400, chunked)
        targets = [e["target"] for e in self._read(self.d, cap.id)["entries"]]
        self.assertIn("n11/headers", targets)
        self.assertIn(f"n11/check?token={REDACTED}&version=1", targets)

        log = "\n".join(self.d.log)
        leaks = {"token": cap.token, **header_sentinels, **secrets_by_letter}
        for name, value in leaks.items():
            with self.subTest(value=name):
                self.assertNotIn(value, captured, f"{name} reached stderr: {captured[:300]!r}")
                self.assertNotIn(value, log, f"{name} reached the daemon log")

    # Naive: with store_text_bodies every non-JSON body is kept as text.
    def test_n12_non_form_body_stays_opaque_even_with_store_text_bodies(self) -> None:
        cap = self._start(store_text_bodies=True)
        self._assert_default_ok(
            self._sink(cap, "POST", "n12/form", body=b"a=1&b=2", headers=(CT_OCTET,))
        )
        cases = {
            "prose": ("text/plain", b"plain text f078-sentinel-n12-prose"),
            "part_without_equals": ("text/plain", b"f078-sentinel-n12-noeq&x=1"),
            "empty_name": ("text/plain", b"=f078-sentinel-n12-name&x=1"),
            "not_utf8": ("application/octet-stream", b"\xff\xfef078-sentinel-n12-bin=1"),
            "broken_json": ("application/json", b'{"broken": f078-sentinel-n12-json'),
        }
        for name, (content_type, body) in cases.items():
            self._assert_default_ok(
                self._sink(
                    cap, "POST", f"n12/{name}", body=body, headers=(f"Content-Type: {content_type}",)
                )
            )

        read, raw = self._read_raw(self.d, cap.id)
        by_target = {e["target"]: e for e in read["entries"]}
        control = by_target["n12/form"]
        self.assertEqual(control["body_kind"], "text")
        self.assertEqual(control["body"], "a=1&b=2")
        for name, (content_type, body) in cases.items():
            with self.subTest(body=name):
                entry = by_target[f"n12/{name}"]
                self.assertEqual(entry["body_kind"], "opaque")
                self.assertIsNone(entry["body"])
                self.assertIsNone(entry["body_sha256"])
                self.assertEqual(entry["content_length"], len(body))
                self.assertEqual(entry["content_type"], content_type)
                self.assertNotIn(f"f078-sentinel-n12-", raw)

    # Naive: a fixed list of whole key names, substring matching, or one level of lists.
    def test_n13_every_secret_name_matches_by_whole_segment_at_any_depth(self) -> None:
        cap = self._start()
        sentinel = {
            name: f"f078-sentinel-n13-{name}"
            for name in (
                "jwt", "token", "password", "passwd", "secret", "rcon", "auth",
                "api_key", "cookie", "matrix", "query_auth", "query_cookie",
            )
        }
        body_value = {
            "jwt": sentinel["jwt"],
            "token": sentinel["token"],
            "Password": sentinel["password"],
            "db_passwd": sentinel["passwd"],
            "client_secret": sentinel["secret"],
            "rcon": sentinel["rcon"],
            "x-auth": sentinel["auth"],
            "API_KEY": sentinel["api_key"],
            "set-Cookie": sentinel["cookie"],
            "matrix": [[{"token": sentinel["matrix"]}, "kept"]],
            "authority": "visible-authority",
            "apikey": "visible-apikey",
            "api": "visible-api",
            "key": "visible-key",
            "event_name": "player.kill",
        }
        raw_body = json.dumps(body_value, separators=(",", ":")).encode("utf-8")
        target = (
            f"n13/check?Auth={sentinel['query_auth']}"
            f"&my_cookie={sentinel['query_cookie']}&version=1&authority=visible"
        )
        self._assert_default_ok(
            self._sink(cap, "POST", target, body=raw_body, headers=(CT_JSON,))
        )

        read, raw = self._read_raw(self.d, cap.id)
        self.assertEqual(len(read["entries"]), 1, read)
        entry = read["entries"][0]
        expected_body = {
            "jwt": REDACTED,
            "token": REDACTED,
            "Password": REDACTED,
            "db_passwd": REDACTED,
            "client_secret": REDACTED,
            "rcon": REDACTED,
            "x-auth": REDACTED,
            "API_KEY": REDACTED,
            "set-Cookie": REDACTED,
            "matrix": [[{"token": REDACTED}, "kept"]],
            "authority": "visible-authority",
            "apikey": "visible-apikey",
            "api": "visible-api",
            "key": "visible-key",
            "event_name": "player.kill",
        }
        expected_query = f"Auth={REDACTED}&my_cookie={REDACTED}&version=1&authority=visible"
        self.assertEqual(entry["body_kind"], "json")
        self.assertEqual(entry["body"], expected_body)
        self.assertEqual(entry["target"], "n13/check?" + expected_query)
        self.assertEqual(entry["path"], "n13/check")
        self.assertEqual(entry["query"], expected_query)
        self.assertEqual(
            entry["query_pairs"],
            [["Auth", REDACTED], ["my_cookie", REDACTED], ["version", "1"], ["authority", "visible"]],
        )
        self.assertEqual(
            sorted(entry["redacted"]),
            sorted(
                [
                    "body.jwt",
                    "body.token",
                    "body.Password",
                    "body.db_passwd",
                    "body.client_secret",
                    "body.rcon",
                    "body.x-auth",
                    "body.API_KEY",
                    "body.set-Cookie",
                    "body.matrix.0.0.token",
                    "query.Auth",
                    "query.my_cookie",
                ]
            ),
        )
        self.assertEqual(entry["body_sha256"], _redacted_sha256(expected_body))
        for name, value in sentinel.items():
            with self.subTest(secret=name):
                self.assertNotIn(value, raw)

    # Naive: a rejected request is stored before its query is redacted.
    def test_n14_query_secret_is_redacted_in_rejected_entries(self) -> None:
        cap = self._start()
        s_ok, s413, s400, s414 = (f"f078-sentinel-n14-{n}" for n in ("ok", "413", "400", "414"))
        self._assert_default_ok(self._sink(cap, "GET", f"n14/ok?token={s_ok}&version=1"))

        response = _raw_exchange(
            cap.port,
            _http_request(
                cap.port,
                "POST",
                f"/sink/{cap.token}/n14/big?token={s413}&version=1",
                headers=(CT_OCTET, f"Content-Length: {MAX_BODY_BYTES + 1}"),
                length=False,
            ),
        )
        self.assertEqual(response.status, 413, response)
        response = self._sink(
            cap,
            "POST",
            f"n14/chunked?password={s400}&version=1",
            headers=("Transfer-Encoding: chunked",),
            length=False,
        )
        self.assertEqual(response.status, 400, response)
        long_prefix = f"n14/long?jwt={s414}&pad="
        long_rel = long_prefix + "a" * (MAX_TARGET_BYTES + 1 - len(long_prefix))
        response = self._sink(cap, "GET", long_rel)
        self.assertEqual(response.status, 414, response)

        read, raw = self._read_raw(self.d, cap.id)
        self.assertEqual(
            read.get("rejected"),
            {"body_too_large": 1, "bad_framing": 1, "target_too_long": 1},
        )
        by_reason = {e["rejected"]: e for e in read["entries"]}
        self.assertEqual(by_reason[None]["target"], f"n14/ok?token={REDACTED}&version=1")
        exact = {
            "body_too_large": ("n14/big", "token"),
            "bad_framing": ("n14/chunked", "password"),
        }
        for reason, (path, name) in exact.items():
            with self.subTest(rejected=reason):
                entry = by_reason[reason]
                self.assertEqual(entry["target"], f"{path}?{name}={REDACTED}&version=1")
                self.assertEqual(entry["query"], f"{name}={REDACTED}&version=1")
                self.assertEqual(entry["query_pairs"], [[name, REDACTED], ["version", "1"]])
        for reason in ("body_too_large", "bad_framing", "target_too_long"):
            entry = by_reason[reason]
            for field in ("target", "query", "query_pairs"):
                with self.subTest(rejected=reason, field=field):
                    serialized = json.dumps(entry[field], ensure_ascii=False)
                    for value in (s413, s400, s414):
                        self.assertNotIn(value, serialized)
        for value in (s_ok, s413, s400, s414):
            self.assertNotIn(value, raw)

    # Naive: the token is matched as a prefix of the first path segment.
    def test_n15_token_followed_by_one_more_character_is_plain_404(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n15/ok", {"n": 1}))
        not_found = self._not_found_body(cap.port)
        for suffix in ("x", "0"):
            with self.subTest(suffix=suffix):
                target = f"/sink/{cap.token}{suffix}/n15/x"
                response = _raw_exchange(cap.port, _http_request(cap.port, "POST", target))
                self.assertEqual(response.status, 404, response)
                self.assertEqual(response.body, not_found)
        read = self._read(self.d, cap.id)
        self.assertEqual([e["target"] for e in read["entries"]], ["n15/ok"])
        self.assertEqual(read.get("accepted"), 1)
        self.assertEqual(read.get("rejected"), {})

    # Naive: every method under a valid token is answered (or the stdlib's 501).
    def test_n16_methods_other_than_get_and_post_are_plain_404(self) -> None:
        cap = self._start()
        self._assert_default_ok(self._post_json(cap, "n16/ok", {"n": 1}))
        self._assert_default_ok(self._sink(cap, "GET", "n16/get"))
        not_found = self._not_found_body(cap.port)
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                response = self._sink(cap, method, f"n16/{method.lower()}", length=True)
                self.assertEqual(response.status, 404, response)
                self.assertEqual(response.body, not_found)
        read = self._read(self.d, cap.id)
        self.assertEqual([e["target"] for e in read["entries"]], ["n16/ok", "n16/get"])
        self.assertEqual(read.get("accepted"), 2)
        self.assertEqual(read.get("rejected"), {})

    # Naive: int() on Content-Length, or a negative length read to EOF.
    def test_n17_negative_or_non_numeric_content_length_is_400_bad_framing(self) -> None:
        cap = self._start()
        self._assert_default_ok(
            self._sink(cap, "POST", "n17/ok", body=b"{}", headers=(CT_JSON,))
        )
        lengths = {"negative": "-1", "word": "nope", "hex": "0x10", "decimal_point": "1.5"}
        for name, value in lengths.items():
            with self.subTest(length=name):
                response = self._sink(
                    cap,
                    "POST",
                    f"n17/{name}",
                    headers=(CT_JSON, f"Content-Length: {value}"),
                    length=False,
                )
                self.assertEqual(response.status, 400, f"{name}: {response!r}")
        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("accepted"), 1)
        self.assertEqual(read.get("rejected"), {"bad_framing": len(lengths)})
        bad = [e for e in read["entries"] if e["rejected"] == "bad_framing"]
        self.assertEqual(
            sorted(e["target"] for e in bad), sorted(f"n17/{name}" for name in lengths)
        )
        for entry in bad:
            self.assertEqual(entry["response_status"], 400)
            self.assertIsNone(entry["body"])

    # Naive: a short rfile.read(n) is taken as the whole body.
    def test_n18_body_shorter_than_its_content_length_is_never_accepted(self) -> None:
        cap = self._start()
        self._assert_default_ok(
            self._sink(cap, "POST", "n18/ok", body=b"{}", headers=(CT_JSON,))
        )
        request = _http_request(
            cap.port,
            "POST",
            f"/sink/{cap.token}/n18/short",
            body=b"{}",
            headers=(CT_JSON, "Content-Length: 10"),
            length=False,
        )
        received = _tolerant_exchange(cap.port, request)
        if received.startswith(b"HTTP/"):
            status = _parse_response(received).status
            self.assertFalse(
                200 <= status < 300, f"a truncated body was answered {received[:200]!r}"
            )
        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("accepted"), 1)
        accepted = [e["target"] for e in read["entries"] if e["rejected"] is None]
        self.assertEqual(accepted, ["n18/ok"])

    # Naive: the 8 KiB target limit counted in characters.
    def test_n19_target_limit_counts_utf8_bytes(self) -> None:
        cap = self._start()
        prefix = "n19/"
        rel_max = prefix + "\u00e9" * ((MAX_TARGET_BYTES - len(prefix)) // 2)
        rel_over = rel_max + "a"
        self.assertEqual(len(rel_max.encode("utf-8")), MAX_TARGET_BYTES)
        self.assertEqual(len(rel_over.encode("utf-8")), MAX_TARGET_BYTES + 1)
        self.assertLess(len(rel_over), MAX_TARGET_BYTES, "fits when counted in characters")
        self._assert_default_ok(self._sink(cap, "GET", rel_max))
        response = self._sink(cap, "GET", rel_over)
        self.assertEqual(response.status, 414, response)
        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("accepted"), 1)
        self.assertEqual(read.get("rejected"), {"target_too_long": 1})
        self.assertEqual(read["entries"][0]["target"], rel_max)
        self.assertIsNone(read["entries"][0]["rejected"])

    # Naive: the 4 KiB rule body limit counted in characters.
    def test_n20_rule_body_limit_counts_utf8_bytes(self) -> None:
        body_max = "\u00e9" * (MAX_RULE_BODY // 2)
        body_over = body_max + "a"
        self.assertEqual(len(body_max.encode("utf-8")), MAX_RULE_BODY)
        self.assertEqual(len(body_over.encode("utf-8")), MAX_RULE_BODY + 1)
        self.assertLess(len(body_over), MAX_RULE_BODY, "fits when counted in characters")
        cap = self._start(responses=[{"path": "m", "body": body_max}])
        response = self._sink(cap, "POST", "m", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(response.status, 200, response)
        self.assertEqual(response.body, body_max.encode("utf-8"))
        self._assert_content_length(response)
        status, body, _ = self._op(self.d, "start", responses=[{"path": "m", "body": body_over}])
        self.assertEqual(status, 400, body)
        self.assertEqual(body.get("error"), "bad_args")

    # Naive: any status is accepted in a rule, a 204 (engine error 8) included.
    def test_n21_rule_status_outside_the_closed_list_is_bad_args(self) -> None:
        for group in (RULE_STATUSES[:MAX_RULES], RULE_STATUSES[MAX_RULES:]):
            self._start(
                responses=[{"path": f"s{status}", "status": status, "body": "x"} for status in group]
            )
        for status in (204, 205, 100, 302, 304, 418, 999, "200", True, 200.0):
            with self.subTest(status=status):
                code, body, _ = self._op(
                    self.d, "start", responses=[{"path": "m", "status": status, "body": "x"}]
                )
                self.assertEqual(code, 400, f"{status!r}: {body}")
                self.assertEqual(body.get("error"), "bad_args")

    # Naive: the registry opens its listener when it is built.
    def test_n22_no_listener_before_the_first_start(self) -> None:
        self.assertIsNone(self.registry.listener)
        d = self._daemon(bind=False)
        self.assertIsNone(d.registry.listener, "no capture yet: no socket")
        cap = self._start(d)
        listener = d.registry.listener
        self.assertIsNotNone(listener, "start opens the listener")
        self.assertEqual(int(listener.server_address[1]), cap.port)

    # Naive: lists the run_id of every binding, whatever its state.
    def test_n23_a_binding_that_is_not_bound_is_not_listed(self) -> None:
        fence = importlib.import_module("dayz_mcp.instance_fence")
        d = self._daemon(bind=False)
        instances = bind_both_peers(d.state, run_id=BOUND_RUN_ID)
        cap = self._start(d)
        self._assert_default_ok(self._post_json(cap, "n23/bound", {"n": 1}))
        state = d.state
        with state._lock:  # type: ignore[attr-defined]
            for instance in instances:
                state._bindings[instance].state = fence.BINDING_STARTING  # type: ignore[attr-defined]
        self._assert_default_ok(self._post_json(cap, "n23/starting", {"n": 2}))
        entries = self._read(d, cap.id)["entries"]
        self.assertEqual(
            [(e["target"], e["bound_run_ids"]) for e in entries],
            [("n23/bound", [BOUND_RUN_ID]), ("n23/starting", [])],
        )

    # Naive: only the entry count is capped; bodies pile up past 4 MiB.
    @slow_test
    def test_n24_capture_over_4_mib_of_bodies_is_503_capture_full(self) -> None:
        cap = self._start()
        full = MAX_CAPTURE_BODY_BYTES // MAX_BODY_BYTES
        sizes = [MAX_BODY_BYTES] * (full - 1) + [MAX_BODY_BYTES - 2]
        for index, size in enumerate(sizes):
            response = self._sink(
                cap, "POST", f"n24/{index}", body=_json_of_size(size), headers=(CT_JSON,)
            )
            if response.status != 200:
                self.fail(f"request {index + 1} of {len(sizes)} refused: {response!r}")
        self.assertEqual(sum(sizes), MAX_CAPTURE_BODY_BYTES - 2)
        # Positive control: a body that reaches the cap exactly is accepted.
        self._assert_default_ok(
            self._sink(cap, "POST", "n24/exact", body=b"{}", headers=(CT_JSON,))
        )
        over = self._sink(cap, "POST", "n24/over", body=b"{}", headers=(CT_JSON,))
        self.assertEqual(over.status, 503, over)
        self.assertEqual(over.json().get("error"), "capture_full")

        read = self._read(self.d, cap.id, max_entries=1)
        self.assertEqual(read.get("dropped"), 1)
        self.assertEqual(read.get("accepted"), len(sizes) + 1)
        self.assertEqual(read.get("rejected"), {})
        entries = self._entries_all(self.d, cap.id)
        self.assertEqual(
            [e["target"] for e in entries],
            [f"n24/{i}" for i in range(len(sizes))] + ["n24/exact"],
        )
        self.assertEqual(sum(e["content_length"] for e in entries), MAX_CAPTURE_BODY_BYTES)

    # Naive: a read returns max_entries entries whatever the size of their bodies.
    @slow_test
    def test_n25_read_page_stops_before_256_kib_of_bodies(self) -> None:
        cap = self._start()
        sizes = [MAX_BODY_BYTES] * 7 + [MAX_BODY_BYTES - 1, 2]
        for index, size in enumerate(sizes):
            response = self._sink(
                cap, "POST", f"n25/{index}", body=_json_of_size(size), headers=(CT_JSON,)
            )
            if response.status != 200:
                self.fail(f"request {index + 1} of {len(sizes)} refused: {response!r}")
        # Positive control: max_entries still caps a page by count.
        two = self._read(self.d, cap.id, since_seq=0, max_entries=2)
        self.assertEqual([e["target"] for e in two["entries"]], ["n25/0", "n25/1"])

        first = self._read(self.d, cap.id, since_seq=0, max_entries=32)
        self.assertEqual([e["target"] for e in first["entries"]], [f"n25/{i}" for i in range(4)])
        self.assertEqual(
            sum(e["content_length"] for e in first["entries"]), MAX_PAGE_BODY_BYTES
        )
        second = self._read(self.d, cap.id, since_seq=first["next_seq"], max_entries=32)
        self.assertEqual(
            [e["target"] for e in second["entries"]], [f"n25/{i}" for i in range(4, 8)]
        )
        self.assertEqual(
            sum(e["content_length"] for e in second["entries"]), MAX_PAGE_BODY_BYTES - 1
        )
        third = self._read(self.d, cap.id, since_seq=second["next_seq"], max_entries=32)
        self.assertEqual([e["target"] for e in third["entries"]], ["n25/8"])
        tail = self._read(self.d, cap.id, since_seq=third["next_seq"], max_entries=32)
        self.assertEqual(tail["entries"], [])
        self.assertEqual(tail["next_seq"], third["next_seq"])

    # -- negatives added after the second review (_ref/REVIEW-r1.md) ----------

    # Defect (REVIEW-r1 P1-1): embedded mode builds its ServerState with live_profile=False.
    @slow_test
    def test_n26_live_profile_refuses_capture_start_in_embedded_mode(self) -> None:
        os.environ.setdefault(
            "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
        )
        server = importlib.import_module("dayz_mcp.server")

        def embedded() -> tuple[object, _Daemon]:
            # Built the way embedded mode builds it: Runtime.start_loopback()
            # constructs the LoopbackServer and its ServerState.
            log: list[str] = []
            runtime = server.Runtime(
                server.ServerConfig(key=KEY, port=0, log_sink=log.append)
            )
            runtime.start_loopback()
            self.addCleanup(runtime.stop_loopback)
            loop = runtime.loopback
            daemon = _Daemon(runtime.state, loop.httpd, loop.thread, _FakeClock(), log)
            return runtime, daemon

        # Positive control: without the variable both entries arm a capture.
        with mock.patch.dict(os.environ):
            os.environ.pop(loopback.LIVE_PROFILE_ENV, None)
            runtime, plain = embedded()
            status, body, _ = self._op(plain, "start", ttl_s=30)
            self.assertEqual(status, 200, f"embedded POST start without the profile: {body}")
            self.assertIsNotNone(plain.registry.listener)
            status, body, _ = self._op(plain, "stop", capture_id=body["capture_id"])
            self.assertEqual(status, 200, body)
            started = asyncio.run(runtime.http_capture_start(ttl_s=30))
            self.assertIsInstance(started.get("capture_id"), str, started)
            asyncio.run(runtime.http_capture_stop(capture_id=started["capture_id"]))
            self.assertIsNone(plain.registry.listener)

        with mock.patch.dict(os.environ, {loopback.LIVE_PROFILE_ENV: "1"}):
            self.assertTrue(loopback.live_profile_from_env()[0])
            runtime, live = embedded()
            status, body, _ = self._op(live, "start", ttl_s=30)
            self.assertEqual(status, 403, f"embedded POST start under the live profile: {body}")
            self.assertEqual(body.get("error"), "live_profile_refused")
            self.assertIsNone(live.registry.listener, "a refused POST start opens no socket")
            with self.assertRaises(Exception) as raised:
                asyncio.run(runtime.http_capture_start(ttl_s=30))
            self.assertIsInstance(raised.exception, server.ToolError)
            self.assertIn("live_profile_refused", str(raised.exception))
            self.assertIsNone(live.registry.listener, "a refused tool start opens no socket")

    # Defect (REVIEW-r1 P1-2): an expired TTL leaves the listener open until a read, stop or close.
    @slow_test
    def test_n27_expiry_closes_the_listener_with_no_op_and_no_request(self) -> None:
        cap = self._start(ttl_s=30)
        # Positive control: before the clock passes expires_at the listener answers.
        self._assert_default_ok(self._post_json(cap, "n27/before", {"n": 1}))
        self.assertIsNotNone(self.registry.listener)

        self.d.clock.advance(31)
        # No control op and no sink request from here until the listener is gone.
        deadline = time.monotonic() + 2.0
        while self.registry.listener is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(
            self.registry.listener,
            "the TTL passed 2 s ago with nothing else happening: the listener must be closed",
        )
        self._assert_refused(cap.port)
        read = self._read(self.d, cap.id)
        self.assertEqual(read.get("state"), "expired")
        self.assertEqual([e["target"] for e in read["entries"]], ["n27/before"])

    # Defect (REVIEW-r1 P1-3): only armed captures are counted; stopped ones pile up for 600 s.
    @slow_test
    def test_n28_retention_budget_of_eight_drops_the_oldest_stopped_capture(self) -> None:
        d = self.d
        max_retained = 8
        never_status, never_body, _ = self._op(d, "read", capture_id=secrets.token_hex(16))
        self.assertTrue(400 <= never_status < 500, f"unknown id: {never_status} {never_body}")
        self.assertEqual(never_body.get("error"), UNKNOWN_CAPTURE)

        def assert_unknown(cap: _Capture, label: str) -> None:
            status, body, _ = self._op(d, "read", capture_id=cap.id)
            self.assertEqual(
                (status, body),
                (never_status, never_body),
                f"{label} must be dropped, answered like an unknown id: {status} {body}",
            )

        def assert_readable(cap: _Capture, state: str, label: str) -> None:
            status, body, _ = self._op(d, "read", capture_id=cap.id)
            self.assertEqual(status, 200, f"{label} must be readable: {status} {body}")
            self.assertEqual(body.get("state"), state, label)

        cycles: list[_Capture] = []
        for index in range(max_retained + 1):
            cap = self._start(ttl_s=30)
            status, stopped, _ = self._op(d, "stop", capture_id=cap.id)
            self.assertEqual(status, 200, stopped)
            cycles.append(cap)
            d.clock.advance(1)
            if index == max_retained - 1:
                # Positive control: eight retained captures are all still readable.
                for number, kept in enumerate(cycles, start=1):
                    assert_readable(kept, "stopped", f"cycle {number} of {max_retained}")
        self.assertLess(d.clock.mono - MONO_START, RETENTION_AFTER_STOP_S)
        assert_unknown(cycles[0], "the first of nine stopped captures")
        for number, kept in enumerate(cycles[1:], start=2):
            assert_readable(kept, "stopped", f"cycle {number}")

        # Armed starts push out the oldest stopped ones; armed ones are never dropped.
        armed = [self._start() for _ in range(MAX_CAPTURES)]
        for number, cap in enumerate(cycles[1 : 1 + MAX_CAPTURES], start=2):
            assert_unknown(cap, f"cycle {number}, the oldest when an armed start came")
        for number, cap in enumerate(cycles[1 + MAX_CAPTURES :], start=2 + MAX_CAPTURES):
            assert_readable(cap, "stopped", f"cycle {number}")
        for number, cap in enumerate(armed, start=1):
            assert_readable(cap, "armed", f"armed capture {number}")
        status, body, _ = self._op(d, "start")
        self.assertTrue(400 <= status < 500, f"fifth armed capture: {status} {body}")
        self.assertEqual(body.get("error"), "http_capture_limit")
        for number, cap in enumerate(armed, start=1):
            assert_readable(cap, "armed", f"armed capture {number} after the refused fifth")

    # Defect (REVIEW-r1 P1-4): stop closes only the listening socket; accepted workers live on.
    @slow_test
    def test_n29_stop_closes_accepted_connections_and_in_flight_is_one_budget(self) -> None:
        d = self.d
        a = self._start()
        self._assert_default_ok(self._post_json(a, "n29/before", {"n": 1}))

        slow: list[socket.socket] = []

        def close_slow() -> None:
            while slow:
                try:
                    slow.pop().close()
                except OSError:
                    pass

        def still_open(sock: socket.socket) -> bool:
            """True while the sink holds the socket: no EOF, reset or answer yet."""
            sock.settimeout(0.0)
            try:
                sock.recv(1, socket.MSG_PEEK)
            except (BlockingIOError, TimeoutError):
                return True
            except OSError:
                return False
            finally:
                sock.settimeout(IO_TIMEOUT_S)
            return False

        self.addCleanup(close_slow)
        for index in range(MAX_IN_FLIGHT):
            sock = socket.create_connection(("127.0.0.1", a.port), timeout=IO_TIMEOUT_S)
            slow.append(sock)
            sock.sendall(
                (
                    f"POST /sink/{a.token}/n29/slow{index} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{a.port}\r\n"
                    f"{CT_JSON}\r\n"
                    "Content-Length: 10\r\n"
                    "\r\n"
                    "{"
                ).encode("ascii")
            )
        # Positive control: the four slow requests were accepted and fill the
        # in-flight cap (a fifth is closed unanswered), and they are held open.
        fifth = _tolerant_exchange(
            a.port,
            _http_request(
                a.port, "POST", f"/sink/{a.token}/n29/fifth", body=b"{}", headers=(CT_JSON,)
            ),
        )
        self.assertFalse(
            fifth.startswith(b"HTTP/"), f"the fifth in-flight request was served: {fifth[:200]!r}"
        )
        self.assertEqual(
            [still_open(sock) for sock in slow],
            [True] * MAX_IN_FLIGHT,
            "the four slow requests must be held open before the stop",
        )

        status, stopped, _ = self._op(d, "stop", capture_id=a.id)
        closed_deadline = time.monotonic() + 2.0
        self.assertEqual(status, 200, stopped)
        self.assertIsNone(self.registry.listener, "last stop closes the listener")

        # Observed now, asserted after the firm part: a request to a capture
        # started right after the stop, and how many of A's are still open.
        b = self._start()
        b_answer = _tolerant_exchange(
            b.port,
            _http_request(
                b.port, "POST", f"/sink/{b.token}/n29/b", body=b"{}", headers=(CT_JSON,)
            ),
        )
        open_when_b_answered = sum(1 for sock in slow if still_open(sock))

        # Firm part: stop closed every connection the old listener accepted.
        for index, sock in enumerate(slow):
            received = b""
            closed = False
            while True:
                remaining = closed_deadline - time.monotonic()
                sock.settimeout(max(remaining, 0.01))
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    break
                except OSError:
                    closed = True
                    break
                if not data:
                    closed = True
                    break
                received += data
                if remaining <= 0:
                    break
            with self.subTest(slow=index):
                self.assertTrue(
                    closed,
                    f"slow request {index} still open 2 s after the last stop: {received[:80]!r}",
                )
                if received.startswith(b"HTTP/"):
                    answer = _parse_response(received).status
                    self.assertFalse(
                        200 <= answer < 300, f"slow request {index} answered {received[:200]!r}"
                    )

        # Loose part: never more than MAX_IN_FLIGHT requests handled at once.
        if b_answer.startswith(b"HTTP/"):
            self.assertLessEqual(
                open_when_b_answered,
                MAX_IN_FLIGHT - 1,
                "capture B was served while all four slow requests of A were still held",
            )

        read = self._read(d, a.id)
        self.assertEqual(read.get("state"), "stopped")
        self.assertEqual(read.get("accepted"), 1, read)
        accepted = [e["target"] for e in read["entries"] if e["rejected"] is None]
        self.assertEqual(accepted, ["n29/before"])

    # Defect (REVIEW-r1 P1-5): secret names are matched raw, so api%5Fkey and %74oken keep values.
    def test_n30_percent_encoded_secret_names_are_redacted_and_keep_spelling(self) -> None:
        cap = self._start(store_text_bodies=True)
        s1, s2, s3, s4, s5 = (f"f078-sentinel-n30-s{n}" for n in range(1, 6))
        form_ct = "Content-Type: application/x-www-form-urlencoded"
        # Positive control: the same names spelled plainly are redacted.
        self._assert_default_ok(
            self._sink(
                cap,
                "POST",
                f"n30/plain?api_key={s4}&version=1",
                body=f"token={s5}&version=1".encode("utf-8"),
                headers=(form_ct,),
            )
        )
        query = f"api%5Fkey={s1}&%74oken={s2}&version=1"
        form = f"api%5Fkey={s3}&version=1"
        self._assert_default_ok(
            self._sink(
                cap, "POST", f"n30/check?{query}", body=form.encode("utf-8"), headers=(form_ct,)
            )
        )

        read, raw = self._read_raw(self.d, cap.id)
        by_path = {e["path"]: e for e in read["entries"]}
        self.assertEqual(sorted(by_path), ["n30/check", "n30/plain"])
        plain = by_path["n30/plain"]
        self.assertEqual(plain["query"], f"api_key={REDACTED}&version=1")
        self.assertEqual(plain["body"], f"token={REDACTED}&version=1")
        self.assertEqual(sorted(plain["redacted"]), ["body.token", "query.api_key"])

        for name, value in (("S1", s1), ("S2", s2), ("S3", s3), ("S4", s4), ("S5", s5)):
            with self.subTest(secret=name):
                self.assertNotIn(value, raw)
        entry = by_path["n30/check"]
        expected_query = f"api%5Fkey={REDACTED}&%74oken={REDACTED}&version=1"
        expected_body = f"api%5Fkey={REDACTED}&version=1"
        self.assertEqual(entry["target"], "n30/check?" + expected_query)
        self.assertEqual(entry["query"], expected_query)
        self.assertEqual(
            entry["query_pairs"],
            [["api%5Fkey", REDACTED], ["%74oken", REDACTED], ["version", "1"]],
        )
        self.assertEqual(entry["body_kind"], "text")
        self.assertEqual(entry["body"], expected_body)
        self.assertEqual(
            entry["body_sha256"], hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
        )
        self.assertEqual(entry["content_length"], len(form.encode("utf-8")))
        self.assertIsNone(entry["rejected"])
        self.assertEqual(
            sorted(entry["redacted"]),
            sorted(["query.api%5Fkey", "query.%74oken", "body.api%5Fkey"]),
        )


def _control_policy(policy_cls: object, keyfile: Path) -> object:
    """An AccreditedDaemonPolicy shaped like test_control_client._policy (copied)."""
    authority = {
        "argv": [r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon", "--port", "8765"],
        "cwd": r"P:\DayZ_MCP_dev\tools",
        "host": "127.0.0.1",
        "keyfile": str(keyfile),
        "kind": "normal",
        "native_executable": r"P:\Runtime\python.exe",
        "port": 8765,
        "security_build_id": None,
    }
    authority_sha256 = hashlib.sha256(
        json.dumps(authority, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    return policy_cls(  # type: ignore[operator]
        kind="normal",
        host="127.0.0.1",
        port=8765,
        keyfile=str(keyfile),
        native_executable=r"P:\Runtime\python.exe",
        argv=tuple(authority["argv"]),
        cwd=r"P:\DayZ_MCP_dev\tools",
        security_build_id=None,
        authority_sha256=authority_sha256,
    )


class HttpCaptureToolsTest(unittest.TestCase):
    """Review pista a: the three MCP tools exist. Names only, no sink here.

    These do not depend on the sink setUp: each fails on main 43215db on its
    own because the names are missing. Nothing new is imported at module level.
    """

    def test_p15_server_registers_the_three_capture_tools_like_logs_since(self) -> None:
        os.environ.setdefault(
            "LOCALAPPDATA", str(Path(tempfile.gettempdir()) / "dayz-mcp-localapp")
        )
        server = importlib.import_module("dayz_mcp.server")
        app, _runtime = server.build_app(server.ServerConfig(log_sink=lambda _message: None))
        names = {tool.name for tool in asyncio.run(app.list_tools())}
        self.assertIn("logs_since", names)
        for name in CAPTURE_TOOLS:
            with self.subTest(tool=name):
                self.assertIn(name, names, f"{name} is not a registered MCP tool")

    def test_p16_capture_tools_are_in_the_initial_catalog_without_a_lease(self) -> None:
        catalog = importlib.import_module("dayz_mcp.tool_catalog")
        initial = catalog._INITIAL_CATALOG_NAMES
        self.assertIn("logs_since", initial)
        for name in CAPTURE_TOOLS:
            with self.subTest(tool=name):
                self.assertIn(name, initial, f"{name} is not in the pre-lease catalog")

    def test_p17_control_client_forwards_each_tool_to_http_capture(self) -> None:
        control = importlib.import_module("dayz_mcp.control_client")
        policy_mod = importlib.import_module("dayz_mcp.daemon_policy")
        client_cls = control.ControlClient
        missing = [name for name in CAPTURE_TOOLS if not callable(getattr(client_cls, name, None))]
        if missing:
            self.fail(f"ControlClient lacks {missing}; f078 forwards them like client_dumps_*")
        identity = control.ControlIdentity(
            platform="unknown",
            pid=123,
            ppid=45,
            started_at_utc="2026-07-22T00:00:00Z",
            session_id="12345678-1234-4234-8234-1234567890ab",
            task_label="",
        )
        calls = {
            "client_dumps_get": ({"run_ids": ["r1"]}, "/client-dumps", "get"),  # control
            "http_capture_start": ({}, "/http-capture", "start"),
            "http_capture_read": ({"capture_id": "c1"}, "/http-capture", "read"),
            "http_capture_stop": ({"capture_id": "c1"}, "/http-capture", "stop"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            keyfile = Path(temporary) / "daemon.key"
            keyfile.write_text("test-key\n", encoding="utf-8")
            client = client_cls(
                policy=_control_policy(policy_mod.AccreditedDaemonPolicy, keyfile),
                identity=identity,
            )
            for name, (kwargs, path, op) in calls.items():
                with self.subTest(method=name):
                    session_call = mock.AsyncMock(return_value={"ok": True})
                    with mock.patch.object(client, "_session_call", new=session_call):
                        result = asyncio.run(getattr(client, name)(**kwargs))
                    self.assertEqual(result, {"ok": True})
                    session_call.assert_awaited_once()
                    args = session_call.await_args.args
                    payload = args[1] if len(args) > 1 else session_call.await_args.kwargs.get(
                        "payload"
                    )
                    self.assertEqual(args[0], path)
                    self.assertIsInstance(payload, dict)
                    self.assertEqual(payload.get("op"), op)


if __name__ == "__main__":
    unittest.main()
