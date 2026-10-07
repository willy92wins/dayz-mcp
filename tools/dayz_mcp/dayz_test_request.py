from __future__ import annotations

import hashlib
import json
import ntpath
import re
import unicodedata
import uuid
from dataclasses import dataclass

from . import dayz_test_attestation, dayz_test_modes


_REQUEST_KEYS_V1 = frozenset(
    {
        "version",
        "dev_root",
        "mod",
        "mode",
        "mission",
        "source",
        "extra_mods",
        "base_mods",
        "server_mods",
        "no_base_mods",
        "port",
        "width",
        "height",
        "player_name",
        "server_wait_s",
        "build",
        "clean",
        "pack_only",
        "no_file_patching",
        "preflight",
        "kill",
        "run_id",
        "replace_if_not_polling_since",
        "auto_remediate_steam",
        "navmesh_data_server",
        "shared_lock_root",
        "instance_token",
        "build_lock_wait_s",
    }
)
# Version 1 stays this set. Version 2 adds exactly one closed field.
_REQUEST_KEYS = _REQUEST_KEYS_V1
_REQUEST_KEYS_V2 = _REQUEST_KEYS_V1 | {"project_mod_override"}
_BRIDGE_BASENAMES = frozenset({"dayz_mcp", "@dayz_mcp"})
_MISSION_ALIASES = frozenset({"chernarus", "livonia", "sakhal", "lfheli"})
_INVALID_RUN_ID = "invalid_run_id"
_CLIENT_REQUIRES_RUN_ID = "client_requires_run_id"
_SERVER_ALL_FORBID_RUN_ID = "server_all_forbid_run_id"

# fb-20260829-023649-8f8c point 3. Every rejection below used to collapse into a
# single token, so the caller learned that the request was bad and nothing else.
# The vocabulary is CLOSED and the reason is validated against it before it is
# published: a typo degrades to the bare legacy token instead of inventing a
# code, and dayz_test_tool refuses to translate a suffix that is not in here.
# The reason never crosses the bundle cable -- it is raised and caught inside
# dayz_test_tool.build_run_request, in the MCP server process (0 hits for
# invalid_dayz_test_request in daemon_contract.py and native_broker_protocol.py).
# Codex F-07. A canonical payload emitted before replace_if_not_polling_since
# existed carries every other key and not that one. Recognising only the new
# keyset would make a stored or replayed v1 document stop being canonical and
# die as source_requires_build, with no version bump and no legacy route -- the
# exact failure a rollback of this window would hit. Keep the pre-navmesh
# canonical forms too; omitted optional fields retain their false defaults.
_CANONICAL_KEYSETS = frozenset(
    variant
    for keys in (
        _REQUEST_KEYS,
        _REQUEST_KEYS - {"replace_if_not_polling_since"},
        _REQUEST_KEYS - {"auto_remediate_steam"},
        _REQUEST_KEYS - {"replace_if_not_polling_since", "auto_remediate_steam"},
    )
    for omitted in (
        frozenset(),
        frozenset({"navmesh_data_server"}),
        frozenset({"shared_lock_root"}),
        frozenset({"instance_token"}),
        frozenset({"shared_lock_root", "instance_token"}),
        frozenset({"navmesh_data_server", "shared_lock_root"}),
        frozenset({"navmesh_data_server", "instance_token"}),
        frozenset({"navmesh_data_server", "shared_lock_root", "instance_token"}),
    )
    for extra in (
        frozenset(),
        frozenset({"build_lock_wait_s"}),
    )
    for variant in (
        keys - omitted - extra,
        (keys - omitted - extra) | {"project_mod_override"},
    )
)


REQUEST_REJECTION_REASONS = frozenset(
    {
        "duplicate_key",
        "flag_not_boolean",
        "json_constant_rejected",
        "json_undecodable",
        "kill_conflicts_with_other_work",
        "kill_requires_offline_mode",
        "mission_not_allowed",
        "mod_list_invalid",
        "mode_authority_unreadable",
        "mode_unknown",
        "navmesh_data_server_requires_server_launch",
        "no_base_mods_conflict",
        "pack_only_requires_build",
        "payload_not_encodable",
        "payload_too_large",
        "player_name_invalid",
        "port_out_of_range",
        "project_mod_override_conflicts_with_build",
        "project_mod_override_includes_original",
        "project_mod_override_requires_candidate",
        "project_policy_not_found",
        "raw_envelope_invalid",
        "replace_witness_invalid",
        "replace_witness_not_allowed",
        "server_wait_out_of_range",
        "source_outside_default",
        "source_requires_build",
        "unicode_not_normalized",
        "shared_lock_root_invalid",
        "build_lock_wait_invalid",
        "instance_token_invalid",
        "unknown_key",
        "version_unsupported",
        "window_size_out_of_range",
    }
)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _invalid("duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    _invalid("json_constant_rejected")


def reject_original_project_directory(
    payload: dict[str, object], mods_root: str
) -> None:
    """Refuse an override that puts the live project folder back on `-mod`.

    Comparison is the normalized runtime path, not the leaf name. The public
    token stays the closed request reason.
    """
    if payload.get("project_mod_override") is not True:
        return
    from dayz_mcp.dayz_test_storage import StorageError, normalize_mod_path

    mod = payload.get("mod")
    if type(mod) is not str:
        return
    try:
        original = ntpath.normcase(normalize_mod_path("@" + mod, mods_root))
    except StorageError:
        return
    listed: list[object] = []
    for field in ("base_mods", "extra_mods", "server_mods"):
        values = payload.get(field)
        if isinstance(values, list):
            listed.extend(values)
    for value in listed:
        try:
            resolved = ntpath.normcase(normalize_mod_path(value, mods_root))
        except StorageError:
            continue
        if resolved == original:
            _invalid("project_mod_override_includes_original")


def _invalid(reason: str) -> None:
    """Reject, naming the condition when the name is a declared one.

    Fail-safe, not fail-open: an undeclared reason keeps EXACTLY the legacy
    token, so a typo cannot publish a code no consumer has ever seen.
    """
    if reason in REQUEST_REJECTION_REASONS:
        raise ValueError("invalid_dayz_test_request:" + reason)
    raise ValueError("invalid_dayz_test_request")


def _invalid_policy() -> None:
    raise ValueError("invalid_dayz_test_policy")


def _request_mode_view() -> tuple[str, tuple[str, ...]]:
    try:
        records = dayz_test_modes.mode_records()
        default = dayz_test_modes.resolve_default_mode(records)
        names = dayz_test_modes.request_mode_names(records)
    except dayz_test_modes.ModeAuthorityError:
        _invalid("mode_authority_unreadable")
    return default.name, names


def _bounded_int(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _bounded_text(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= maximum


def _valid_string_list(value: object) -> bool:
    if not isinstance(value, list) or not 0 <= len(value) <= 64:
        return False
    if any(not _bounded_text(item, 1, 520) for item in value):
        return False
    folded = [item.casefold() for item in value]
    return len(folded) == len(set(folded))


def _valid_uuid4(value: object) -> bool:
    if not isinstance(value, str) or value != value.casefold():
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _valid_unicode_tree(value: object, depth: int = 1) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        return (
            unicodedata.is_normalized("NFC", value)
            and "\0" not in value
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        )
    if isinstance(value, (list, tuple)):
        return all(_valid_unicode_tree(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            _valid_unicode_tree(key, depth + 1)
            and _valid_unicode_tree(item, depth + 1)
            for key, item in value.items()
        )
    return True


def _valid_local_absolute_path(value: object) -> bool:
    if not _bounded_text(value, 3, 520) or not _valid_unicode_tree(value):
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


def _path_is_within(path: object, roots: tuple[str, ...]) -> bool:
    if not _valid_local_absolute_path(path):
        return False
    normalized_path = ntpath.normcase(path)
    for root in roots:
        normalized_root = ntpath.normcase(root)
        try:
            if ntpath.commonpath((normalized_path, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _valid_mod_entry(value: object, roots: tuple[str, ...]) -> bool:
    if not _bounded_text(value, 1, 520):
        return False
    if ntpath.isabs(value):
        return _path_is_within(value, roots)
    return (
        value not in {".", ".."}
        and ":" not in value
        and "\\" not in value
        and "/" not in value
        and ntpath.normpath(value) == value
    )


def _valid_mod_list(value: object, roots: tuple[str, ...]) -> bool:
    return _valid_string_list(value) and all(
        _valid_mod_entry(item, roots) for item in value
    )


@dataclass(frozen=True)
class RequestProjectPolicy:
    mod: str
    dev_root: str
    default_source: str
    default_base_mods: tuple[str, ...]
    mission_roots: tuple[str, ...]
    mod_roots: tuple[str, ...]
    attestation: dayz_test_attestation.ProjectAttestation | None = None


@dataclass(frozen=True)
class ParsedDayzTestRequest:
    payload: dict[str, object]
    canonical_bytes: bytes
    sha256: str


def _validate_policies(policies: object) -> tuple[RequestProjectPolicy, ...]:
    if type(policies) is not tuple or not 1 <= len(policies) <= 128:
        _invalid_policy()

    identities: set[tuple[str, str]] = set()
    for policy in policies:
        if type(policy) is not RequestProjectPolicy:
            _invalid_policy()
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", policy.mod)
            or not _valid_unicode_tree(policy.__dict__)
        ):
            _invalid_policy()
        if any(
            not _valid_local_absolute_path(path)
            for path in (policy.dev_root, policy.default_source)
        ):
            _invalid_policy()
        for roots in (policy.mission_roots, policy.mod_roots):
            if type(roots) is not tuple or not 1 <= len(roots) <= 64:
                _invalid_policy()
            if any(
                not _valid_local_absolute_path(path)
                for path in roots
            ):
                _invalid_policy()
            folded_roots = [ntpath.normcase(path) for path in roots]
            if len(folded_roots) != len(set(folded_roots)):
                _invalid_policy()
        if type(policy.default_base_mods) is not tuple or not _valid_string_list(
            list(policy.default_base_mods)
        ):
            _invalid_policy()
        if not _valid_unicode_tree(policy.default_base_mods):
            _invalid_policy()
        if not all(
            _valid_mod_entry(item, policy.mod_roots)
            for item in policy.default_base_mods
        ):
            _invalid_policy()
        if policy.attestation is not None and (
            type(policy.attestation) is not dayz_test_attestation.ProjectAttestation
        ):
            _invalid_policy()

        identity = (policy.mod.casefold(), ntpath.normcase(policy.dev_root))
        if identity in identities:
            _invalid_policy()
        identities.add(identity)
    return policies


def parse_dayz_test_request(
    raw: bytes,
    *,
    policies: tuple[RequestProjectPolicy, ...],
) -> ParsedDayzTestRequest:
    policies = _validate_policies(policies)
    if type(raw) is not bytes or not 1 <= len(raw) <= 65_536 or raw.startswith(
        b"\xef\xbb\xbf"
    ):
        _invalid("raw_envelope_invalid")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        # RecursionError: on Python 3.11 and 3.12 the decoder itself gives up on
        # deep nesting that 3.14 parses and _valid_unicode_tree then refuses.
        _invalid("json_undecodable")
    if not isinstance(value, dict):
        _invalid("unknown_key")
    version = value.get("version")
    if type(version) is int and version == 1:
        allowed_keys = _REQUEST_KEYS_V1
    elif type(version) is int and version == 2:
        allowed_keys = _REQUEST_KEYS_V2
    else:
        if not set(value).issubset(_REQUEST_KEYS_V2):
            _invalid("unknown_key")
        _invalid("version_unsupported")
    if not set(value).issubset(allowed_keys):
        _invalid("unknown_key")
    if not _valid_unicode_tree(value):
        _invalid("unicode_not_normalized")
    mod = value.get("mod")
    dev_root = value.get("dev_root")
    policy = next(
        (
            candidate
            for candidate in policies
            if candidate.mod == mod and candidate.dev_root == dev_root
        ),
        None,
    )
    if policy is None:
        _invalid("project_policy_not_found")

    default_mode, request_mode_names = _request_mode_view()
    mode = value.get("mode", default_mode)
    mission = value.get("mission", "chernarus")
    source = value.get("source")
    extra_mods = value.get("extra_mods", [])
    base_mods = value.get("base_mods", list(policy.default_base_mods))
    server_mods = value.get("server_mods", [])
    no_base_mods = value.get("no_base_mods", False)
    port = value.get("port", 2302)
    width = value.get("width", 1920)
    height = value.get("height", 1080)
    player_name = value.get("player_name", "Dev")
    server_wait_s = value.get("server_wait_s", 60)
    build = value.get("build", False)
    clean = value.get("clean", False)
    pack_only = value.get("pack_only", False)
    no_file_patching = value.get("no_file_patching", False)
    preflight = value.get("preflight", False)
    kill = value.get("kill", False)
    auto_remediate_steam = value.get("auto_remediate_steam", False)
    navmesh_data_server = value.get("navmesh_data_server", False)
    if version == 2:
        project_mod_override = value.get("project_mod_override")
        if type(project_mod_override) is not bool:
            _invalid("flag_not_boolean")
    else:
        project_mod_override = False
    run_id = value.get("run_id")
    replace_witness = value.get("replace_if_not_polling_since")
    shared_lock_root = value.get("shared_lock_root") if "shared_lock_root" in value else None
    instance_token = value.get("instance_token") if "instance_token" in value else None
    build_lock_wait_s = value.get("build_lock_wait_s") if "build_lock_wait_s" in value else None
    if "build_lock_wait_s" in value and (
        isinstance(build_lock_wait_s, bool)
        or not isinstance(build_lock_wait_s, (int, float))
        or build_lock_wait_s < 0
        or build_lock_wait_s != build_lock_wait_s
        or build_lock_wait_s == float("inf")
        or build_lock_wait_s > 24 * 60 * 60
    ):
        _invalid("build_lock_wait_invalid")
    if "shared_lock_root" in value and (
        not isinstance(shared_lock_root, str)
        or not shared_lock_root
        or "\x00" in shared_lock_root
        or not ntpath.isabs(shared_lock_root)
        or ntpath.normpath(shared_lock_root) != shared_lock_root
    ):
        _invalid("shared_lock_root_invalid")
    if "instance_token" in value:
        from dayz_mcp.server_cli import InstanceSelectionError, validate_instance_token

        try:
            if validate_instance_token(instance_token) != instance_token:
                _invalid("instance_token_invalid")
        except InstanceSelectionError:
            _invalid("instance_token_invalid")

    if mode not in request_mode_names:
        _invalid("mode_unknown")
    if not _bounded_text(mission, 1, 520) or (
        mission not in _MISSION_ALIASES
        and not _path_is_within(mission, policy.mission_roots)
    ):
        _invalid("mission_not_allowed")
    if source is not None and (
        not _path_is_within(source, (policy.default_source,))
    ):
        _invalid("source_outside_default")
    if not all(
        _valid_mod_list(candidate, policy.mod_roots)
        for candidate in (extra_mods, base_mods, server_mods)
    ):
        _invalid("mod_list_invalid")
    if any(
        type(candidate) is not bool
        for candidate in (
            no_base_mods,
            build,
            clean,
            pack_only,
            no_file_patching,
            preflight,
            kill,
            auto_remediate_steam,
            navmesh_data_server,
        )
    ):
        _invalid("flag_not_boolean")
    if not _bounded_int(port, 1024, 65530):
        _invalid("port_out_of_range")
    if not _bounded_int(width, 320, 16384) or not _bounded_int(
        height, 320, 16384
    ):
        _invalid("window_size_out_of_range")
    if not _bounded_text(player_name, 1, 64) or any(
        ord(char) <= 31 or 127 <= ord(char) <= 159 for char in player_name
    ):
        _invalid("player_name_invalid")
    if not _bounded_int(server_wait_s, 1, 3600):
        _invalid("server_wait_out_of_range")
    if run_id is not None and not _valid_uuid4(run_id):
        raise ValueError(_INVALID_RUN_ID)
    if no_base_mods and "base_mods" in value and bool(base_mods):
        _invalid("no_base_mods_conflict")

    effective_build = build or clean
    if navmesh_data_server and (mode != "server" or kill or pack_only):
        _invalid("navmesh_data_server_requires_server_launch")
    if pack_only and not effective_build:
        _invalid("pack_only_requires_build")
    canonical_default_source = (
        set(value) in _CANONICAL_KEYSETS
        and source == policy.default_source
        and not effective_build
    )
    if source is not None and not effective_build and not canonical_default_source:
        _invalid("source_requires_build")
    if kill and (
        run_id is None or effective_build or pack_only or preflight
    ):
        _invalid("kill_conflicts_with_other_work")
    if kill and mode != "offline":
        _invalid("kill_requires_offline_mode")
    if not kill and mode in {"server", "all"} and run_id is not None:
        raise ValueError(_SERVER_ALL_FORBID_RUN_ID)
    if not kill and mode == "client" and run_id is None:
        raise ValueError(_CLIENT_REQUIRES_RUN_ID)
    if project_mod_override:
        if effective_build:
            _invalid("project_mod_override_conflicts_with_build")
        declared: list[object] = []
        if "base_mods" in value:
            declared.extend(base_mods if isinstance(base_mods, list) else [])
        if "extra_mods" in value:
            declared.extend(extra_mods if isinstance(extra_mods, list) else [])
        candidates = [
            item
            for item in declared
            if isinstance(item, str)
            and ntpath.basename(item).casefold() not in _BRIDGE_BASENAMES
        ]
        if not candidates:
            _invalid("project_mod_override_requires_candidate")
    # Path identity of the original project folder is checked once the
    # selected runtime mods root is known (reject_original_project_directory).
    # A shared basename under another root is a different directory.
    # fb-20260904-200816-79e2. The witness of the client-replacement gate: the
    # instant, in epoch milliseconds, at which the gate READ the bridge and
    # concluded that the client had stopped polling. It only makes sense on the
    # one call that supersedes a live client, so any other shape is a rejection
    # and not a field quietly dropped.
    if replace_witness is not None:
        if kill or mode != "client":
            _invalid("replace_witness_not_allowed")
        if not _bounded_int(replace_witness, 1, 4_102_444_800_000):
            _invalid("replace_witness_invalid")

    payload: dict[str, object] = {
        "auto_remediate_steam": auto_remediate_steam,
        "base_mods": [] if no_base_mods else list(base_mods),
        "build": effective_build,
        "clean": clean,
        "dev_root": policy.dev_root,
        "extra_mods": list(extra_mods),
        "height": height,
        "kill": kill,
        "mission": mission,
        "mod": policy.mod,
        "mode": mode,
        "navmesh_data_server": navmesh_data_server,
        "no_base_mods": no_base_mods,
        "no_file_patching": no_file_patching,
        "pack_only": pack_only,
        "player_name": player_name,
        "port": port,
        "preflight": preflight,
        "project_mod_override": project_mod_override,
        "replace_if_not_polling_since": replace_witness,
        "run_id": run_id,
        "server_mods": list(server_mods),
        "server_wait_s": server_wait_s,
        "source": policy.default_source if source is None else source,
        "version": 2 if project_mod_override else 1,
        "width": width,
    }
    if not project_mod_override:
        payload.pop("project_mod_override")
    if "shared_lock_root" in value:
        payload["shared_lock_root"] = shared_lock_root
    if "instance_token" in value:
        payload["instance_token"] = instance_token
    if "build_lock_wait_s" in value:
        assert isinstance(build_lock_wait_s, (int, float))
        payload["build_lock_wait_s"] = (
            int(build_lock_wait_s)
            if float(build_lock_wait_s) == int(build_lock_wait_s)
            else float(build_lock_wait_s)
        )
    try:
        canonical_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except UnicodeEncodeError:
        _invalid("payload_not_encodable")
    if len(canonical_bytes) > 65_536:
        _invalid("payload_too_large")
    return ParsedDayzTestRequest(
        payload=payload,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )
