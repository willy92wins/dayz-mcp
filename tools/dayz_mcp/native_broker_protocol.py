"""Closed, public-only framing for requests sent to the native launcher broker."""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import struct
import unicodedata
import uuid
from dataclasses import dataclass
from enum import IntEnum


MAX_FRAME_SIZE = 65_536
_MAGIC = b"DZM1"
_VERSION = 1
_HEADER = struct.Struct("<4sBBHII32s")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MOD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SECRET_KEYS = frozenset(
    {
        "argv",
        "env",
        "environment",
        "executable",
        "identity",
        "key",
        "lease",
        "password",
        "token",
    }
)


class BrokerProtocolError(ValueError):
    pass


class BrokerKind(IntEnum):
    PRIVATE_WORKER = 1
    LIFECYCLE_CLI = 2
    ADDON_BUILDER = 3


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    kind: BrokerKind
    payload: dict[str, object]
    stdin: bytes


def _invalid() -> None:
    raise BrokerProtocolError("invalid_native_broker_request")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    _invalid()


def _valid_unicode(value: object, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        return (
            unicodedata.is_normalized("NFC", value)
            and "\0" not in value
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        )
    if isinstance(value, list):
        return all(_valid_unicode(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and _valid_unicode(key, depth + 1)
            and _valid_unicode(item, depth + 1)
            for key, item in value.items()
        )
    return value is None or type(value) in {bool, int, float}


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized in _SECRET_KEYS or any(
                marker in normalized for marker in ("password", "token", "lease", "identity")
            ):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _uuid4_or_none(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _local_path(value: object) -> bool:
    if not isinstance(value, str) or not 3 <= len(value) <= 520:
        return False
    drive, tail = ntpath.splitdrive(value)
    return (
        len(drive) == 2
        and drive[0].isascii()
        and drive[0].isalpha()
        and drive[1] == ":"
        and tail.startswith("\\")
        and ":" not in tail
        and ntpath.normpath(value) == value
    )


def _validate_payload(kind: BrokerKind, payload: object, stdin: bytes) -> dict[str, object]:
    if not isinstance(payload, dict) or not _valid_unicode(payload) or _contains_secret_key(payload):
        _invalid()
    if kind is BrokerKind.PRIVATE_WORKER:
        if set(payload) != {"request_sha256"}:
            _invalid()
        digest = payload.get("request_sha256")
        if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
            _invalid()
        if not stdin or hashlib.sha256(stdin).hexdigest() != digest:
            _invalid()
    elif kind is BrokerKind.LIFECYCLE_CLI:
        if set(payload) != {"command", "launch_operation_id", "run_id"}:
            _invalid()
        command = payload.get("command")
        run_id = payload.get("run_id")
        operation_id = payload.get("launch_operation_id")
        if command not in {"start", "stop", "adopt", "reap", "ack", "status"}:
            _invalid()
        if not _uuid4_or_none(run_id) or not _uuid4_or_none(operation_id):
            _invalid()
        if command == "start" and (run_id is None or not stdin):
            _invalid()
        if command in {"stop", "adopt", "reap"} and (run_id is None or operation_id is not None or stdin):
            _invalid()
        if command == "ack" and (run_id is None or operation_id is None or stdin):
            _invalid()
        if command == "status" and (operation_id is not None or stdin):
            _invalid()
    elif kind is BrokerKind.ADDON_BUILDER:
        if set(payload) != {"clear", "pack_only", "prefix", "source", "target", "temp"}:
            _invalid()
        if type(payload.get("clear")) is not bool or type(payload.get("pack_only")) is not bool:
            _invalid()
        prefix = payload.get("prefix")
        if not isinstance(prefix, str) or _MOD.fullmatch(prefix) is None:
            _invalid()
        if not all(_local_path(payload.get(key)) for key in ("source", "target", "temp")):
            _invalid()
        if stdin:
            _invalid()
    else:
        _invalid()
    return dict(payload)


def _canonical_json(payload: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BrokerProtocolError("invalid_native_broker_request") from error


def encode_request(
    kind: BrokerKind,
    payload: dict[str, object],
    *,
    stdin: bytes = b"",
) -> bytes:
    if type(kind) is not BrokerKind or type(stdin) is not bytes:
        _invalid()
    validated = _validate_payload(kind, payload, stdin)
    payload_bytes = _canonical_json(validated)
    size = _HEADER.size + len(payload_bytes) + len(stdin)
    if size > MAX_FRAME_SIZE:
        _invalid()
    return (
        _HEADER.pack(
            _MAGIC,
            _VERSION,
            int(kind),
            0,
            len(payload_bytes),
            len(stdin),
            hashlib.sha256(stdin).digest(),
        )
        + payload_bytes
        + stdin
    )


def decode_request(frame: bytes) -> BrokerRequest:
    if type(frame) is not bytes or not _HEADER.size <= len(frame) <= MAX_FRAME_SIZE:
        _invalid()
    try:
        magic, version, kind_value, flags, payload_size, stdin_size, stdin_sha = _HEADER.unpack_from(frame)
    except struct.error:
        _invalid()
    if magic != _MAGIC or version != _VERSION or flags != 0:
        _invalid()
    try:
        kind = BrokerKind(kind_value)
    except ValueError:
        _invalid()
    expected_size = _HEADER.size + payload_size + stdin_size
    if expected_size != len(frame):
        _invalid()
    payload_bytes = frame[_HEADER.size : _HEADER.size + payload_size]
    stdin = frame[_HEADER.size + payload_size :]
    if hashlib.sha256(stdin).digest() != stdin_sha:
        _invalid()
    try:
        payload = json.loads(
            payload_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, BrokerProtocolError):
        _invalid()
    validated = _validate_payload(kind, payload, stdin)
    if _canonical_json(validated) != payload_bytes:
        _invalid()
    return BrokerRequest(kind=kind, payload=validated, stdin=stdin)
