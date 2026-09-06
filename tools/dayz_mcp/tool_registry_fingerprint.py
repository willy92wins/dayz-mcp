"""Pure M14 registry fingerprint, authority parser, and comparator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Literal, TypeAlias

from dayz_mcp.effective_schema_core import EffectiveSchemaError, _tool_record

Profile: TypeAlias = Literal["standard", "exec_enforce"]
Role: TypeAlias = Literal["claude", "codex"]
SnapshotStatus: TypeAlias = Literal["known", "unknown"]
AuthorityStatus: TypeAlias = Literal["fresh", "stale", "unknown"]

_PROFILES = frozenset({"standard", "exec_enforce"})
_ROLES = frozenset({"claude", "codex"})
_EFFECTS = frozenset({"wire", "in_game_required"})
_RECORD_KEYS = frozenset(
    {"name", "description", "input_schema", "public_constraints", "effect_verification"}
)
_PAYLOAD_KEYS = frozenset(
    {"profile", "role", "instructions", "tools", "tool_registry_fingerprint"}
)
_MARKER_KEYS = frozenset(
    {
        "artifact_version",
        "schema_version",
        "artifact_txid",
        "payloads",
        "generator",
        "producers",
        "fingerprint_sha256",
        "verdict_sha256",
        "producers_sha256",
    }
)
_VERDICT_KEYS = frozenset(
    {"artifact_version", "schema_version", "artifact_txid", "verdict", "bank_members"}
)
_PRODUCERS_KEYS = frozenset(
    {
        "artifact_version",
        "schema_version",
        "artifact_txid",
        "producers",
        "validator_sources",
        "active_approval",
    }
)
_COMMIT_KEYS = frozenset(
    {
        "kind",
        "operation_txid",
        "artifact_txid",
        "previous_schema_sha256",
        "schema_sha256",
    }
)
_PAIR_ORDER = (
    ("standard", "claude"),
    ("standard", "codex"),
    ("exec_enforce", "claude"),
    ("exec_enforce", "codex"),
)
_HEX = frozenset("0123456789abcdef")
_MANIFEST_LINE = re.compile(r"^[0-9a-f]{64}  (standard\|claude|standard\|codex|exec_enforce\|claude|exec_enforce\|codex)$")

_VALIDATOR_SOURCES = {
    "dayz_test_request.parse_dayz_test_request": "tools/dayz_mcp/dayz_test_request.py",
    "vehicle_get_in_client.seat_index": "tools/dayz_mcp/server.py",
    "vehicle_get_in_client.expected_type": "tools/dayz_mcp/server.py",
    "build_app.instructions": "tools/dayz_mcp/server.py",
}

_BANK_TABLE: tuple[tuple[str, str, int, str, tuple[str, ...]], ...] = (
    (
        "tools/tests/fixtures/effective_schema_v1/required_constraint_ids.json",
        "979e395f1cad3364bbf51f622e70b306d40239989dccf64d58f42cdb9576af9a",
        9,
        "8aaffe2751a511a30c166ecad33c11995f249fd25a8787507781fe4124c43ff3",
        (
            "schema:dayz_test_run:mission",
            "schema:vehicle_get_in_client:seat_index",
            "schema:vehicle_get_in_client:expected_type",
            "manual:new_site_guard",
            "manual:spawn_y_provider",
            "manual:living_infected_flags",
            "manual:wait_log_sources",
            "manual:wait_default_lookback",
            "manual:action_use_target_contract",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/instructions_required_concepts.json",
        "aa4b8e3b1602a69fcdf4ef682819e888d5b4c9fbc78185ad9885b0a9f7b3c583",
        6,
        "63075c8888d0c80aa8aca55f4da29a69bb6e7b1ccd61de933db10954593b9f94",
        (
            "new_site_guard",
            "spawn_y_provider",
            "living_infected_flags",
            "wait_log_sources",
            "wait_default_lookback",
            "action_use_target_contract",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/profile_inventory.json",
        "e7d7be819d32f59d8c160a592d66a6038e0708aaf9d06acc11ec8772bfd34a9e",
        4,
        "314755d4a61b0ba382b4286ecd42f00efebdfdbee5dc8e7c4702c4e649ad0306",
        ("standard|claude", "standard|codex", "exec_enforce|claude", "exec_enforce|codex"),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/validator_cases.json",
        "dc2bd69e325b445287c1680fda3067c676cb85f7662e61fab7d20b88b86897fc",
        18,
        "2035e4558adbb877fe22f0c012c5eaeb3dc442d65ec21a403458b9048266bef6",
        (
            "mission_alias_chernarus",
            "mission_alias_livonia",
            "mission_alias_sakhal",
            "mission_alias_lfheli",
            "mission_sealed_path",
            "mission_external_path",
            "seat_omitted",
            "seat_zero",
            "seat_one",
            "seat_sixty_three",
            "seat_bool",
            "seat_string",
            "seat_negative",
            "seat_sixty_four",
            "type_omitted",
            "type_civilian_sedan",
            "type_boat",
            "type_non_string",
        ),
    ),
    (
        "tools/tests/fixtures/effective_schema_v5/mutation_cases.json",
        "88cd0e8975a8703993203ee4775af9f81b28b4640ba9e5ac6fde16f5aef1783b",
        13,
        "8f6e6c03433b6101fa3e4a408226c9c06a9436a4cdf561af34f7b407a3774ab0",
        (
            "field_removed_from_app_schema",
            "catalog_constraint_removed",
            "fixture_constraint_removed",
            "runtime_adapter_extra",
            "runtime_adapter_dangling",
            "validator_logic_altered",
            "extra_wrapper_removed",
            "extra_arguments_accepted",
            "offline_public",
            "mission_external_accepted",
            "parameter_renamed",
            "bridge_marked_wire",
            "runtime_extra_after_alias",
        ),
    ),
)

_MINIMUM_PRODUCERS = frozenset(
    {
        "tools/dayz_mcp/server.py",
        "tools/dayz_mcp/knowledge.py",
        "tools/dayz_mcp/effective_schema_core.py",
        "tools/dayz_mcp/effective_schema_catalog.py",
        "tools/dayz_mcp/effective_schema_runtime_validators.py",
        "tools/dayz_mcp/dayz_test_modes.py",
        "tools/dayz_mcp/tool_registry_fingerprint.py",
        "tools/dayz_mcp/dayz_test_request.py",
        "tools/mcp_capture.py",
        "tools/promote_effective_schema.py",
        "tools/tests/test_effective_schema_promotion.py",
        "tools/requirements-mcp.txt",
        "tools/pyproject.toml",
        "tools/native-launchers/dayz-test-v1/app.pyz",
        "tools/native-launchers/dayz-test-v1/closure-manifest.json",
        "tools/approved-launchers.json",
        *(row[0] for row in _BANK_TABLE),
        *_VALIDATOR_SOURCES.values(),
    }
)
_FIXTURE_SHA = {row[0]: row[1] for row in _BANK_TABLE}


@dataclass(frozen=True)
class RegistrySnapshot:
    session_id: str | None
    profile: Profile | Literal["unknown"]
    role: Role | Literal["unknown"]
    captured_at_utc: str | None
    fingerprint: str | None
    canonical_bytes: bytes | None
    status: SnapshotStatus


@dataclass(frozen=True)
class AuthorityBundleBytes:
    marker: bytes | None
    fingerprint_sidecar: bytes | None
    verdict_sidecar: bytes | None
    producers_sidecar: bytes | None
    receipts: bytes | None


@dataclass(frozen=True)
class AuthoritySnapshot:
    artifact_txid: str | None
    profile: Profile | Literal["unknown"]
    role: Role | Literal["unknown"]
    fingerprint: str | None
    status: Literal["known", "unknown"]


_UNKNOWN_REGISTRY = RegistrySnapshot(
    session_id=None,
    profile="unknown",
    role="unknown",
    captured_at_utc=None,
    fingerprint=None,
    canonical_bytes=None,
    status="unknown",
)
_UNKNOWN_AUTHORITY = AuthoritySnapshot(
    artifact_txid=None,
    profile="unknown",
    role="unknown",
    fingerprint=None,
    status="unknown",
)


def _fail(message: str) -> None:
    raise EffectiveSchemaError(message)


def _require_hex(value: object, size: int, label: str) -> str:
    if type(value) is not str or len(value) != size or any(char not in _HEX for char in value):
        _fail(f"{label} must be lowercase hex of length {size}")
    return value


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or value == "":
        _fail(f"{label} must be a non-empty string")
    return value


def _require_pair(profile: object, role: object) -> tuple[str, str]:
    if profile not in _PROFILES or role not in _ROLES:
        _fail("profile/role pair is not canonical")
    return profile, role


def _require_int(value: object, expected: int, label: str) -> int:
    if type(value) is not int or value != expected:
        _fail(f"{label} mismatch")
    return value


def _nfc(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized.encode("utf-8")
    return normalized


def _normalize(value: object) -> object:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail("float must be finite")
        return value
    if type(value) is str:
        return _nfc(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail("object keys must be strings")
            folded = _nfc(key)
            if folded in normalized:
                _fail("NFC key collision")
            normalized[folded] = _normalize(item)
        return normalized
    if type(value) is list:
        return [_normalize(item) for item in value]
    _fail("value is not JSON-canonical")


def canonical_json_bytes(value: object) -> bytes:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_tool(tool: object) -> dict[str, Any]:
    if not isinstance(tool, Mapping):
        _fail("tool record must be a mapping")
    if set(tool) != _RECORD_KEYS:
        _fail("tool record keyset must be exact")
    name = tool["name"]
    description = tool["description"]
    schema = tool["input_schema"]
    constraints = tool["public_constraints"]
    effect = tool["effect_verification"]
    if type(name) is not str or name == "":
        _fail("tool.name must be a non-empty string")
    if type(description) is not str:
        _fail("tool.description must be a string")
    if not isinstance(schema, Mapping):
        _fail("input_schema must be a mapping")
    if type(constraints) is not list:
        _fail("public_constraints must be a list")
    if any(type(item) is not str or item == "" for item in constraints):
        _fail("public_constraints must be non-empty strings")
    if len(constraints) != len(set(constraints)):
        _fail("public_constraints must be unique")
    if type(effect) is not str or effect not in _EFFECTS:
        _fail("effect_verification is not recognized")
    projected = _tool_record(
        {
            "name": name,
            "description": description,
            "input_schema": schema,
            "public_constraints": constraints,
            "effect_verification": effect,
        }
    )
    return {
        "name": projected["name"],
        "description": projected["description"],
        "input_schema": projected["input_schema"],
        "public_constraints": list(projected["public_constraints"]),
        "effect_verification": projected["effect_verification"],
    }


def canonical_registry_fingerprint(
    tools: Sequence[Mapping[str, object]],
) -> tuple[bytes, str]:
    if isinstance(tools, (str, bytes, bytearray, Mapping)):
        _fail("tools must be a sequence of mappings")
    if not isinstance(tools, Sequence):
        _fail("tools must be a sequence of mappings")
    snapshot = tuple(tools)
    records = [_normalize(_project_tool(item)) for item in snapshot]
    names = [record["name"] for record in records]
    if any(type(name) is not str for name in names) or len(names) != len(set(names)):
        _fail("tool names must be unique after NFC")
    for record in records:
        constraints = record["public_constraints"]
        if type(constraints) is not list or len(constraints) != len(set(constraints)):
            _fail("public_constraints must be unique after NFC")
    records.sort(key=lambda item: item["name"])
    raw = canonical_json_bytes({"tools": records})
    return raw, _sha256(raw)


def capture_registry_snapshot(
    *,
    session_id: object,
    profile: object,
    role: object,
    captured_at_utc: object,
    tools: Sequence[Mapping[str, object]],
) -> RegistrySnapshot:
    try:
        if type(session_id) is not str or session_id == "":
            _fail("session_id")
        if type(captured_at_utc) is not str or captured_at_utc == "":
            _fail("captured_at_utc")
        pair = _require_pair(profile, role)
        raw, digest = canonical_registry_fingerprint(tools)
    except (EffectiveSchemaError, TypeError, ValueError, UnicodeError):
        return _UNKNOWN_REGISTRY
    return RegistrySnapshot(
        session_id=session_id,
        profile=pair[0],
        role=pair[1],
        captured_at_utc=captured_at_utc,
        fingerprint=digest,
        canonical_bytes=raw,
        status="known",
    )


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: set[str] = set()
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            _fail("duplicate JSON key")
        seen.add(key)
        result[key] = value
    return result


def _reject_constant(name: str) -> None:
    _fail(f"JSON constant {name} is not allowed")


def _parse_canonical_json(raw: object) -> object:
    if type(raw) is not bytes:
        _fail("blob must be bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("BOM is not allowed")
    text = raw.decode("utf-8")
    parsed = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_object_pairs)
    if canonical_json_bytes(parsed) != raw:
        _fail("JSON is not canonical A")
    return parsed


def _repo_relative(path: object) -> str:
    if type(path) is not str or path == "" or "\\" in path or "\x00" in path:
        _fail("producer path is not canonical")
    if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
        _fail("producer path is not canonical")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("producer path is not canonical")
    if "/".join(parts) != path:
        _fail("producer path is not canonical")
    return path


def _parse_producers_array(value: object) -> list[dict[str, str]]:
    if type(value) is not list:
        _fail("producers must be an array")
    records: list[dict[str, str]] = []
    paths: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            _fail("producer shape")
        path = _repo_relative(item["path"])
        digest = _require_hex(item["sha256"], 64, "producer.sha256")
        if path in paths:
            _fail("duplicate producer path")
        if path in _FIXTURE_SHA and digest != _FIXTURE_SHA[path]:
            _fail("fixture producer hash mismatch")
        paths.append(path)
        records.append({"path": path, "sha256": digest})
    if paths != sorted(paths):
        _fail("producers must be lexicographically ordered")
    present = set(paths)
    if not _MINIMUM_PRODUCERS.issubset(present):
        _fail("minimum producer union is incomplete")
    return records


def _parse_manifest(raw: object, payloads: list[Mapping[str, Any]]) -> None:
    if type(raw) is not bytes:
        _fail("fingerprint sidecar must be bytes")
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or b"\t" in raw or not raw.endswith(b"\n"):
        _fail("fingerprint manifest framing")
    text = raw.decode("utf-8")
    lines = text.split("\n")
    if lines[-1] != "":
        _fail("fingerprint manifest must end with LF")
    body = lines[:-1]
    if len(body) != 4 or any(line == "" for line in body):
        _fail("fingerprint manifest must have four lines")
    for index, (profile, role) in enumerate(_PAIR_ORDER):
        line = body[index]
        if _MANIFEST_LINE.fullmatch(line) is None:
            _fail("fingerprint manifest line")
        digest, label = line.split("  ", 1)
        if label != f"{profile}|{role}":
            _fail("fingerprint label order")
        payload = payloads[index]
        if payload["tool_registry_fingerprint"] != digest:
            _fail("manifest digest does not match payload")
        recomputed, recomputed_digest = canonical_registry_fingerprint(payload["tools"])
        if recomputed_digest != digest:
            _fail("manifest digest does not match recomputation")
        if recomputed_digest != payload["tool_registry_fingerprint"]:
            _fail("payload fingerprint mismatch")
        del recomputed


def _parse_payloads(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) != 4:
        _fail("payloads must contain four pairs")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != _PAYLOAD_KEYS:
            _fail("payload shape")
        expected_profile, expected_role = _PAIR_ORDER[index]
        profile, role = _require_pair(item["profile"], item["role"])
        if (profile, role) != (expected_profile, expected_role):
            _fail("payload order")
        if type(item["instructions"]) is not str:
            _fail("instructions must be a string")
        digest = _require_hex(item["tool_registry_fingerprint"], 64, "payload fingerprint")
        if type(item["tools"]) is not list:
            _fail("payload tools must be a list")
        _, recomputed = canonical_registry_fingerprint(item["tools"])
        if recomputed != digest:
            _fail("payload fingerprint recomputation")
        records.append(dict(item))
    return records


def _parse_verdict(value: object, artifact_txid: str) -> None:
    if not isinstance(value, Mapping) or set(value) != _VERDICT_KEYS:
        _fail("verdict shape")
    _require_int(value["artifact_version"], 5, "verdict.artifact_version")
    _require_int(value["schema_version"], 1, "verdict.schema_version")
    if value["artifact_txid"] != artifact_txid:
        _fail("verdict txid")
    if value["verdict"] != "PASS":
        _fail("verdict must be PASS")
    members = value["bank_members"]
    if type(members) is not list or len(members) != len(_BANK_TABLE):
        _fail("bank_members cardinality")
    for member, row in zip(members, _BANK_TABLE, strict=True):
        path, fixture_sha, count, ids_sha, ids = row
        if not isinstance(member, Mapping) or set(member) != {
            "path",
            "sha256",
            "expected_ids",
            "results",
            "verdict",
        }:
            _fail("bank member shape")
        if member["path"] != path or member["sha256"] != fixture_sha or member["verdict"] != "PASS":
            _fail("bank member identity")
        expected_ids = member["expected_ids"]
        results = member["results"]
        if type(expected_ids) is not list or type(results) is not list:
            _fail("bank member lists")
        if expected_ids != list(ids) or len(expected_ids) != count:
            _fail("bank expected_ids")
        if _sha256(canonical_json_bytes({"ids": expected_ids})) != ids_sha:
            _fail("external ids hash")
        if len(results) != len(expected_ids):
            _fail("results coverage")
        seen: set[str] = set()
        for result, expected_id in zip(results, expected_ids, strict=True):
            if not isinstance(result, Mapping) or set(result) != {"id", "expected", "observed", "verdict"}:
                _fail("result shape")
            identifier = result["id"]
            if type(identifier) is not str or identifier == "" or identifier in seen:
                _fail("result id")
            if identifier != expected_id or result["verdict"] != "PASS":
                _fail("result identity")
            seen.add(identifier)


def _parse_producers_object(value: object, artifact_txid: str) -> list[dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != _PRODUCERS_KEYS:
        _fail("producers sidecar shape")
    _require_int(value["artifact_version"], 5, "producers.artifact_version")
    _require_int(value["schema_version"], 1, "producers.schema_version")
    if value["artifact_txid"] != artifact_txid:
        _fail("producers txid")
    records = _parse_producers_array(value["producers"])
    sources = value["validator_sources"]
    if not isinstance(sources, Mapping) or dict(sources) != _VALIDATOR_SOURCES:
        _fail("validator_sources")
    present = {item["path"] for item in records}
    for path in _VALIDATOR_SOURCES.values():
        if path not in present:
            _fail("validator source path missing from producers")
    approval = value["active_approval"]
    if not isinstance(approval, Mapping) or set(approval) != {
        "txid",
        "prepared_path",
        "committed_path",
        "rolled_back_absent",
    }:
        _fail("active_approval shape")
    if approval["rolled_back_absent"] is not True:
        _fail("rolled_back_absent")
    launcher = _require_hex(approval["txid"], 32, "active_approval.txid")
    if launcher == artifact_txid:
        _fail("launcher txid must not equal artifact txid")
    prepared = _repo_relative(approval["prepared_path"])
    committed = _repo_relative(approval["committed_path"])
    expected_prepared = f"tools/approved-launchers.receipts/{launcher}/prepared.json"
    expected_committed = f"tools/approved-launchers.receipts/{launcher}/committed.json"
    if prepared != expected_prepared or committed != expected_committed:
        _fail("active approval paths")
    if prepared not in present or committed not in present:
        _fail("active approval receipts missing from producers")
    return records


def _parse_receipts(raw: object, artifact_txid: str, marker_sha: str) -> None:
    if type(raw) is not bytes:
        _fail("receipts must be bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("receipts BOM")
    text = raw.decode("utf-8")
    if not text.endswith("\n"):
        _fail("receipts must end with LF")
    parts = text.split("\n")
    matches = 0
    for line in parts[:-1]:
        if line == "":
            _fail("blank receipt line")
        parsed = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_object_pairs)
        if not isinstance(parsed, Mapping):
            _fail("receipt line must be an object")
        kind = parsed.get("kind")
        if kind != "commit":
            continue
        encoded = line.encode("utf-8")
        if canonical_json_bytes(parsed) != encoded:
            _fail("commit line is not canonical")
        if set(parsed) != _COMMIT_KEYS:
            _fail("commit keyset")
        operation = _require_hex(parsed["operation_txid"], 32, "operation_txid")
        commit_txid = _require_hex(parsed["artifact_txid"], 32, "commit artifact_txid")
        if operation != commit_txid:
            _fail("operation_txid must equal artifact_txid")
        previous = parsed["previous_schema_sha256"]
        if previous is not None:
            _require_hex(previous, 64, "previous_schema_sha256")
        schema_sha = _require_hex(parsed["schema_sha256"], 64, "schema_sha256")
        if commit_txid == artifact_txid and schema_sha == marker_sha:
            matches += 1
    if matches != 1:
        _fail("exactly one current commit is required")


def _read_authority_marker(
    bundle: AuthorityBundleBytes,
    *,
    expected_profile: object,
    expected_role: object,
) -> AuthoritySnapshot:
    pair = _require_pair(expected_profile, expected_role)
    if not isinstance(bundle, AuthorityBundleBytes):
        _fail("bundle type")
    blobs = (
        bundle.marker,
        bundle.fingerprint_sidecar,
        bundle.verdict_sidecar,
        bundle.producers_sidecar,
        bundle.receipts,
    )
    if any(type(item) is not bytes for item in blobs):
        _fail("all five blobs must be bytes")
    marker = _parse_canonical_json(bundle.marker)
    verdict = _parse_canonical_json(bundle.verdict_sidecar)
    producers = _parse_canonical_json(bundle.producers_sidecar)
    if not isinstance(marker, Mapping) or set(marker) != _MARKER_KEYS:
        _fail("marker shape")
    _require_int(marker["artifact_version"], 5, "artifact_version")
    _require_int(marker["schema_version"], 1, "schema_version")
    artifact_txid = _require_hex(marker["artifact_txid"], 32, "artifact_txid")
    generator = marker["generator"]
    if not isinstance(generator, Mapping) or set(generator) != {"name", "version"}:
        _fail("generator shape")
    _require_text(generator["name"], "generator.name")
    _require_text(generator["version"], "generator.version")
    payloads = _parse_payloads(marker["payloads"])
    producer_records = _parse_producers_object(producers, artifact_txid)
    if marker["producers"] != producer_records:
        _fail("marker.producers must equal sidecar producers")
    _parse_manifest(bundle.fingerprint_sidecar, payloads)
    if marker["fingerprint_sha256"] != _sha256(bundle.fingerprint_sidecar):
        _fail("marker.fingerprint_sha256")
    _parse_verdict(verdict, artifact_txid)
    if marker["verdict_sha256"] != _sha256(bundle.verdict_sidecar):
        _fail("marker.verdict_sha256")
    if marker["producers_sha256"] != _sha256(bundle.producers_sidecar):
        _fail("marker.producers_sha256")
    _parse_receipts(bundle.receipts, artifact_txid, _sha256(bundle.marker))
    selected = payloads[_PAIR_ORDER.index(pair)]
    return AuthoritySnapshot(
        artifact_txid=artifact_txid,
        profile=pair[0],
        role=pair[1],
        fingerprint=selected["tool_registry_fingerprint"],
        status="known",
    )


def read_authority_marker(
    bundle: AuthorityBundleBytes,
    *,
    expected_profile: object,
    expected_role: object,
) -> AuthoritySnapshot:
    try:
        return _read_authority_marker(
            bundle,
            expected_profile=expected_profile,
            expected_role=expected_role,
        )
    except (EffectiveSchemaError, TypeError, ValueError, UnicodeError, json.JSONDecodeError, KeyError, IndexError):
        return _UNKNOWN_AUTHORITY


def _hex64(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(char in _HEX for char in value)


def _hex32(value: object) -> bool:
    return type(value) is str and len(value) == 32 and all(char in _HEX for char in value)


def _local_valid(local: RegistrySnapshot) -> bool:
    if not isinstance(local, RegistrySnapshot):
        return False
    if local.status == "unknown":
        return (
            local.session_id is None
            and local.profile == "unknown"
            and local.role == "unknown"
            and local.captured_at_utc is None
            and local.fingerprint is None
            and local.canonical_bytes is None
        )
    if local.status != "known":
        return False
    if type(local.session_id) is not str or local.session_id == "":
        return False
    if type(local.captured_at_utc) is not str or local.captured_at_utc == "":
        return False
    if local.profile not in _PROFILES or local.role not in _ROLES:
        return False
    if type(local.canonical_bytes) is not bytes or not _hex64(local.fingerprint):
        return False
    return _sha256(local.canonical_bytes) == local.fingerprint


def _authority_valid(authority: AuthoritySnapshot) -> bool:
    if not isinstance(authority, AuthoritySnapshot):
        return False
    if authority.status == "unknown":
        return (
            authority.artifact_txid is None
            and authority.profile == "unknown"
            and authority.role == "unknown"
            and authority.fingerprint is None
        )
    if authority.status != "known":
        return False
    if not _hex32(authority.artifact_txid):
        return False
    if authority.profile not in _PROFILES or authority.role not in _ROLES:
        return False
    return _hex64(authority.fingerprint)


def compare_snapshot_to_authority(
    local: RegistrySnapshot,
    authority: AuthoritySnapshot,
) -> AuthorityStatus:
    local_ok = _local_valid(local)
    authority_ok = _authority_valid(authority)
    if not local_ok or not authority_ok:
        return "unknown"
    if local.status != "known" or authority.status != "known":
        return "unknown"
    if (local.profile, local.role) != (authority.profile, authority.role):
        return "unknown"
    if local.fingerprint == authority.fingerprint:
        return "fresh"
    return "stale"
