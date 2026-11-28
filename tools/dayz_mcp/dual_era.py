"""Dual-era MCP stdio front (2026-07-28 + legacy handshake) and SEP-2640 skills wire.

Pinned ``mcp==1.27.2`` speaks protocol versions up to ``2025-11-25`` and has no
skills extension. This front sits in front of ``FastMCP._mcp_server.run``:
- Modern requests (carrying ``params._meta["io.modelcontextprotocol/protocolVersion"]``)
  are handled statelessly for ``server/discover``, ``skills/list``, ``skills/get``,
  and ``skill://`` ``resources/read``, or bridged onto the SDK session with a lazy
  one-time synthetic ``2025-11-25`` handshake and modern result shaping.
- Legacy requests (no modern ``_meta``) pass through untouched in both directions.

All modern requests on one stdio pipe share the single ``ClientRuntime`` and
session lease of this process, so two interleaved conversations on the same
pipe are not isolated from each other; run one conversation per pipe.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import JSONRPCMessage

from dayz_mcp import skills_catalog

MODERN_VERSION = "2026-07-28"
_LEGACY_BRIDGE_VERSION = "2025-11-25"

_MODERN_META_PREFIX = "io.modelcontextprotocol/"
_PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
_SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"

_CACHEABLE_METHODS = frozenset(
    {
        "server/discover",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)
_MODERN_ALLOWED_SERVER_NOTIFICATIONS = frozenset(
    {
        "notifications/progress",
        "notifications/message",
        "notifications/cancelled",
    }
)
_FRONT_CACHE_TTL_MS = 300_000
_SYNTHETIC_INIT_ID = "__dayz_mcp_dual_era_init__"
# JSON-RPC wants a null id for a request whose id cannot be read, but the pinned SDK
# cannot encode one, and answering with a real id can collide with a call still pending
# under it. Such errors carry "<prefix><counter>", where the prefix holds a random
# per-process nonce: a client cannot know it before it has been sent one. A client that
# then uses an id inside that prefix as a request id is refused (under its own id), in
# both eras, so no id is ever answered twice.
_INVALID_ID_NONCE = secrets.token_hex(8)
_INVALID_ID_PREFIX = f"dayz-mcp:invalid-request-id:{_INVALID_ID_NONCE}:"


def is_modern(message: Any) -> bool:
    """True when ``message`` is a JSON-RPC request/notification carrying modern _meta."""
    if not isinstance(message, dict):
        return False
    if not isinstance(message.get("method"), str):
        return False
    params = message.get("params")
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return False
    return _PROTOCOL_VERSION_KEY in meta


def strip_modern_meta(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``params`` without ``io.modelcontextprotocol/*`` _meta keys."""
    if not isinstance(params, dict):
        return {}
    copied = dict(params)
    meta = copied.get("_meta")
    if isinstance(meta, dict):
        kept = {
            key: value
            for key, value in meta.items()
            if not (isinstance(key, str) and key.startswith(_MODERN_META_PREFIX))
        }
        if kept:
            copied["_meta"] = kept
        else:
            copied.pop("_meta", None)
    return copied


def modern_result(method: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``result`` shaped for 2026-07-28 without mutating the input."""
    shaped = dict(result)
    shaped.setdefault("resultType", "complete")
    if method in _CACHEABLE_METHODS:
        shaped.setdefault("ttlMs", 0)
        shaped.setdefault("cacheScope", "private")
    return shaped


def _supported_versions() -> list[str]:
    legacy = sorted(
        (version for version in SUPPORTED_PROTOCOL_VERSIONS if version != MODERN_VERSION),
        reverse=True,
    )
    return [MODERN_VERSION, *legacy]


def _package_version(fallback: str) -> str:
    version_path = Path(__file__).resolve().parent / "VERSION.json"
    try:
        payload = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback
    if isinstance(payload, dict):
        version = payload.get("version")
        if isinstance(version, str) and version:
            return version
    return fallback


def _session_message(payload: dict[str, Any]) -> SessionMessage:
    return SessionMessage(JSONRPCMessage.model_validate(payload))


def _result_message(request_id: Any, result: dict[str, Any]) -> SessionMessage:
    return _session_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error_message(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> SessionMessage:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _session_message({"jsonrpc": "2.0", "id": request_id, "error": error})


def _id_key(request_id: Any) -> tuple[type, Any]:
    return (type(request_id), request_id)


def _is_valid_request_id(request_id: Any) -> bool:
    return isinstance(request_id, (str, int)) and not isinstance(request_id, bool)


def _is_reserved_request_id(request_id: Any) -> bool:
    return isinstance(request_id, str) and request_id.startswith(_INVALID_ID_PREFIX)


class _ClientGoneError(Exception):
    """Raised internally when writing to the client stream fails because it closed."""


def _client_info_from_meta(meta: dict[str, Any]) -> dict[str, str]:
    raw = meta.get(_CLIENT_INFO_KEY)
    if (
        isinstance(raw, dict)
        and isinstance(raw.get("name"), str)
        and raw["name"]
        and isinstance(raw.get("version"), str)
        and raw["version"]
    ):
        return {"name": raw["name"], "version": raw["version"]}
    return {"name": "unknown", "version": "0"}


def _discover_result(app: FastMCP, init_options: Any) -> dict[str, Any]:
    capabilities = init_options.capabilities.model_dump(
        by_alias=True, exclude_none=True
    )
    if not isinstance(capabilities.get("resources"), dict):
        capabilities["resources"] = {}
    extensions = capabilities.get("extensions")
    extensions_dict = dict(extensions) if isinstance(extensions, dict) else {}
    extensions_dict[skills_catalog.EXTENSION_ID] = {}
    capabilities["extensions"] = extensions_dict

    server_name = init_options.server_name or app.name
    sdk_version = init_options.server_version or "0"
    server_version = _package_version(sdk_version)

    result: dict[str, Any] = {
        "resultType": "complete",
        "supportedVersions": _supported_versions(),
        "capabilities": capabilities,
        "_meta": {
            _SERVER_INFO_KEY: {
                "name": server_name,
                "version": server_version,
            }
        },
        "ttlMs": _FRONT_CACHE_TTL_MS,
        "cacheScope": "public",
    }
    instructions = app._mcp_server.instructions
    if instructions is not None:
        result["instructions"] = instructions
    return result


async def _aclose_streams(*streams: Any) -> None:
    """Close every stream even under cancellation, without skipping later streams."""
    with anyio.CancelScope(shield=True):
        first_exc: BaseException | None = None
        for stream in streams:
            try:
                await stream.aclose()
            except BaseException as exc:
                if first_exc is None:
                    first_exc = exc
        if first_exc is not None:
            raise first_exc


async def serve(
    app: FastMCP,
    read_stream: Any,
    write_stream: Any,
    catalog: skills_catalog.SkillCatalog,
) -> None:
    """Serve ``app`` over ``read_stream`` / ``write_stream`` in dual-era mode."""
    sdk_in_send, sdk_in_recv = anyio.create_memory_object_stream(64)
    sdk_out_send, sdk_out_recv = anyio.create_memory_object_stream(64)

    try:
        init_options = app._mcp_server.create_initialization_options()

        write_lock = anyio.Lock()
        handshake_lock = anyio.Lock()
        handshake_done = anyio.Event()

        synthetic_init_pending = False
        synthetic_initialized = False
        legacy_initialized = False
        client_eof = False
        pending_modern: dict[tuple[type, Any], str] = {}
        invalid_id_count = 0

        def next_invalid_id() -> str:
            nonlocal invalid_id_count
            while True:
                invalid_id_count += 1
                candidate = f"{_INVALID_ID_PREFIX}{invalid_id_count}"
                if _id_key(candidate) not in pending_modern:
                    return candidate

        async def send_client(message: SessionMessage, *, check: bool = False) -> None:
            if check:
                # The SDK writer serializes later and dies on what it cannot serialize,
                # which would end the whole process: fail this one answer instead.
                message.message.model_dump_json(by_alias=True, exclude_none=True)
            try:
                async with write_lock:
                    await write_stream.send(message)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
                raise _ClientGoneError() from exc

        async def send_checked(message: SessionMessage) -> None:
            await send_client(message, check=True)

        async def try_send_error(
            request_id: str | int,
            code: int,
            message: str,
            data: Any = None,
        ) -> bool:
            try:
                await send_client(_error_message(request_id, code, message, data=data))
                return True
            except _ClientGoneError:
                return False

        async def ensure_sdk_initialized(meta: dict[str, Any]) -> None:
            nonlocal synthetic_init_pending, synthetic_initialized
            async with handshake_lock:
                if synthetic_initialized or legacy_initialized:
                    return
                synthetic_init_pending = True
                handshake_done_event = handshake_done
                try:
                    init_request = _session_message(
                        {
                            "jsonrpc": "2.0",
                            "id": _SYNTHETIC_INIT_ID,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": _LEGACY_BRIDGE_VERSION,
                                # Empty capabilities prevent the SDK from issuing server-to-client requests.
                                "capabilities": {},
                                "clientInfo": _client_info_from_meta(meta),
                            },
                        }
                    )
                    await sdk_in_send.send(init_request)
                    await handshake_done_event.wait()
                finally:
                    synthetic_init_pending = False
                # Mark modern-initialized before notifications/initialized so any
                # unsolicited SDK notification triggered on init is filtered.
                synthetic_initialized = True
                await sdk_in_send.send(
                    _session_message(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                        }
                    )
                )

        async def run_sdk() -> None:
            try:
                await app._mcp_server.run(sdk_in_recv, sdk_out_send, init_options)
            finally:
                await _aclose_streams(sdk_out_send, sdk_in_recv)

        async def forward_sdk_to_client() -> None:
            nonlocal synthetic_init_pending
            try:
                async for item in sdk_out_recv:
                    msg_id: Any = None
                    is_response = False
                    try:
                        raw = item.message.model_dump(by_alias=True, exclude_none=True)
                        is_response = "id" in raw and ("result" in raw or "error" in raw)
                        if is_response:
                            msg_id = raw["id"]
                            if synthetic_init_pending and msg_id == _SYNTHETIC_INIT_ID:
                                handshake_done.set()
                                continue
                            method = (
                                pending_modern.pop(_id_key(msg_id), None)
                                if _is_valid_request_id(msg_id)
                                else None
                            )
                            if method is not None:
                                if "result" in raw and isinstance(raw["result"], dict):
                                    shaped = modern_result(method, raw["result"])
                                    await send_checked(
                                        _result_message(msg_id, shaped))
                                else:
                                    await send_client(item)
                                continue
                            await send_client(item)
                            continue

                        if synthetic_initialized and not legacy_initialized:
                            # Server-to-client request: modern stateless front refuses with -32601.
                            if "id" in raw and "method" in raw:
                                reply = _error_message(
                                    raw["id"],
                                    -32601,
                                    "Server-to-client requests are not supported in 2026-07-28 mode",
                                )
                                try:
                                    await sdk_in_send.send(reply)
                                except (
                                    anyio.ClosedResourceError,
                                    anyio.BrokenResourceError,
                                ):
                                    pass
                                continue
                            # Server-to-client notification: keep only progress/message/cancelled.
                            notif_method = raw.get("method")
                            if notif_method in _MODERN_ALLOWED_SERVER_NOTIFICATIONS:
                                await send_client(item)
                            continue

                        await send_client(item)
                    except _ClientGoneError:
                        break
                    except Exception as exc:
                        print(
                            f"[dual_era] internal error forwarding SDK message ({type(exc).__name__})",
                            file=sys.stderr,
                            flush=True,
                        )
                        if is_response and _is_valid_request_id(msg_id):
                            pending_modern.pop(_id_key(msg_id), None)
                            if not await try_send_error(msg_id, -32603, "Internal error"):
                                break
            finally:
                # Unblock a waiting synthetic handshake if the SDK exits unexpectedly.
                handshake_done.set()
                try:
                    await _aclose_streams(sdk_out_recv)
                finally:
                    if not client_eof:
                        task_group.cancel_scope.cancel()

        async def handle_modern_request(
            request_id: Any,
            method: str,
            params: dict[str, Any],
        ) -> None:
            meta = params["_meta"]
            requested_version = meta.get(_PROTOCOL_VERSION_KEY)
            if requested_version != MODERN_VERSION:
                await send_client(
                    _error_message(
                        request_id,
                        -32022,
                        "Unsupported protocol version",
                        data={
                            "supported": _supported_versions(),
                            "requested": requested_version,
                        },
                    )
                )
                return

            if method == "server/discover":
                await send_checked(
                    _result_message(request_id, _discover_result(app, init_options)))
                return

            if method == "skills/list":
                cursor = params.get("cursor")
                if cursor is not None and cursor != "":
                    await send_client(
                        _error_message(request_id, -32602, "Invalid params: unknown cursor")
                    )
                    return
                await send_checked(
                    _result_message(
                        request_id,
                        {
                            "resultType": "complete",
                            "skills": catalog.list_entries(),
                            "ttlMs": _FRONT_CACHE_TTL_MS,
                            "cacheScope": "public",
                        },
                    )
                )
                return

            if method == "skills/get":
                uri = params.get("uri")
                if not isinstance(uri, str) or not uri:
                    await send_client(
                        _error_message(
                            request_id, -32602, "Invalid params: uri must be a non-empty string"
                        )
                    )
                    return
                try:
                    entry = catalog.get_entry(uri)
                except KeyError:
                    await send_client(
                        _error_message(request_id, -32602, f"Unknown skill uri: {uri}")
                    )
                    return
                await send_checked(
                    _result_message(
                        request_id,
                        {
                            "resultType": "complete",
                            "skill": entry,
                            "ttlMs": _FRONT_CACHE_TTL_MS,
                            "cacheScope": "public",
                        },
                    )
                )
                return

            if method == "resources/read":
                uri = params.get("uri")
                if isinstance(uri, str) and uri.startswith("skill://"):
                    try:
                        raw_bytes, mime = catalog.read(uri)
                    except KeyError:
                        await send_client(
                            _error_message(
                                request_id, -32602, f"Unknown skill resource uri: {uri}"
                            )
                        )
                        return
                    await send_checked(
                        _result_message(
                            request_id,
                            {
                                "resultType": "complete",
                                "contents": [
                                    {
                                        "uri": uri,
                                        "mimeType": mime,
                                        "text": raw_bytes.decode("utf-8"),
                                    }
                                ],
                                "ttlMs": _FRONT_CACHE_TTL_MS,
                                "cacheScope": "public",
                            },
                        )
                    )
                    return

            await ensure_sdk_initialized(meta)
            stripped = strip_modern_meta(params)
            forwarded: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if stripped:
                forwarded["params"] = stripped
            forwarded_msg = _session_message(forwarded)
            pending_modern[_id_key(request_id)] = method
            await sdk_in_send.send(forwarded_msg)

        async def route_client_to_sdk() -> None:
            nonlocal legacy_initialized, client_eof
            try:
                async for item in read_stream:
                    if isinstance(item, Exception):
                        try:
                            await sdk_in_send.send(item)
                        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                            break
                        continue
                    request_id: Any = None
                    has_valid_id = False
                    modern_line = False
                    try:
                        raw = item.message.model_dump(by_alias=True, exclude_none=True)
                        if is_modern(raw):
                            modern_line = True
                            method = raw["method"]
                            params = raw["params"]
                            has_id = "id" in raw
                            if has_id:
                                request_id = raw["id"]
                            else:
                                raw_all = item.message.model_dump(by_alias=True)
                                if "id" in raw_all:
                                    has_id = True
                                    request_id = raw_all["id"]
                            if has_id:
                                if not _is_valid_request_id(request_id):
                                    # Not bridged. The answer carries a unique string id,
                                    # never a number a pending call could own.
                                    if not await try_send_error(
                                        next_invalid_id(), -32600, "Invalid Request"
                                    ):
                                        task_group.cancel_scope.cancel()
                                        return
                                    continue
                                if _is_reserved_request_id(request_id):
                                    if not await try_send_error(
                                        request_id, -32600, "Invalid Request: reserved id"
                                    ):
                                        task_group.cancel_scope.cancel()
                                        return
                                    continue
                                has_valid_id = True
                                await handle_modern_request(request_id, method, params)
                            else:
                                # Modern notification: strip modern _meta and forward if version matches.
                                if params["_meta"].get(_PROTOCOL_VERSION_KEY) != MODERN_VERSION:
                                    continue
                                if synthetic_initialized or legacy_initialized:
                                    stripped = strip_modern_meta(params)
                                    forwarded: dict[str, Any] = {
                                        "jsonrpc": "2.0",
                                        "method": method,
                                    }
                                    if stripped:
                                        forwarded["params"] = stripped
                                    await sdk_in_send.send(_session_message(forwarded))
                            continue

                        # Legacy traffic: reject initialize if the synthetic handshake already ran.
                        method = raw.get("method")
                        if method is not None and _is_reserved_request_id(raw.get("id")):
                            if not await try_send_error(
                                raw["id"], -32600, "Invalid Request: reserved id"
                            ):
                                task_group.cancel_scope.cancel()
                                return
                            continue
                        if method == "initialize" and "id" in raw:
                            if synthetic_initialized:
                                reply_id = raw["id"] if _is_valid_request_id(raw["id"]) else next_invalid_id()
                                await send_client(
                                    _error_message(
                                        reply_id,
                                        -32600,
                                        f"Invalid request: this process already serves the {MODERN_VERSION} era",
                                    )
                                )
                                continue
                            legacy_initialized = True
                        elif (
                            method == "notifications/initialized"
                            and synthetic_initialized
                            and not legacy_initialized
                        ):
                            # Drop the trailing initialized notification of a refused legacy initialize.
                            continue

                        try:
                            await sdk_in_send.send(item)
                        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                            break
                    except _ClientGoneError:
                        task_group.cancel_scope.cancel()
                        return
                    except Exception as exc:
                        if has_valid_id:
                            pending_modern.pop(_id_key(request_id), None)
                        kind = "modern request" if modern_line else "client line"
                        print(
                            f"[dual_era] internal error handling {kind} ({type(exc).__name__})",
                            file=sys.stderr,
                            flush=True,
                        )
                        if has_valid_id:
                            if not await try_send_error(request_id, -32603, "Internal error"):
                                task_group.cancel_scope.cancel()
                                return
                client_eof = True
            finally:
                await _aclose_streams(sdk_in_send)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run_sdk)
            task_group.start_soon(forward_sdk_to_client)
            task_group.start_soon(route_client_to_sdk)
    finally:
        await _aclose_streams(
            write_stream,
            read_stream,
            sdk_in_send,
            sdk_in_recv,
            sdk_out_send,
            sdk_out_recv,
        )


async def run_stdio(
    app: FastMCP,
    catalog: skills_catalog.SkillCatalog | None = None,
) -> None:
    """Run ``app`` over stdio through the dual-era front."""
    if catalog is not None:
        active_catalog = catalog
    else:
        attached = getattr(app, skills_catalog.APP_CATALOG_ATTR, None)
        if isinstance(attached, skills_catalog.SkillCatalog):
            active_catalog = attached
        else:
            active_catalog = skills_catalog.load_or_empty()
    async with stdio_server() as (read_stream, write_stream):
        await serve(app, read_stream, write_stream, active_catalog)
