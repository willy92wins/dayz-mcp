from __future__ import annotations

import hashlib
import json
import ntpath
import re
import unicodedata
import uuid
from dataclasses import dataclass

from . import dayz_test_modes


_REQUEST_KEYS = frozenset(
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
    }
)
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
# exact failure a rollback of this window would hit. Both keysets are canonical.
_CANONICAL_KEYSETS = frozenset(
    {
        _REQUEST_KEYS,
        _REQUEST_KEYS - {"replace_if_not_polling_since"},
        _REQUEST_KEYS - {"auto_remediate_steam"},
        _REQUEST_KEYS - {"replace_if_not_polling_since", "auto_remediate_steam"},
    }
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
        "no_base_mods_conflict",
        "pack_only_requires_build",
        "payload_not_encodable",
        "payload_too_large",
        "player_name_invalid",
        "port_out_of_range",
        "project_policy_not_found",
        "raw_envelope_invalid",
        "replace_witness_invalid",
        "replace_witness_not_allowed",
        "server_wait_out_of_range",
        "source_outside_default",
        "source_requires_build",
        "unicode_not_normalized",
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
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid("json_undecodable")
    if not isinstance(value, dict) or not set(value).issubset(_REQUEST_KEYS):
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
    if type(value.get("version")) is not int or value.get("version") != 1:
        _invalid("version_unsupported")
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
    run_id = value.get("run_id")
    replace_witness = value.get("replace_if_not_polling_since")

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
        "no_base_mods": no_base_mods,
        "no_file_patching": no_file_patching,
        "pack_only": pack_only,
        "player_name": player_name,
        "port": port,
        "preflight": preflight,
        "replace_if_not_polling_since": replace_witness,
        "run_id": run_id,
        "server_mods": list(server_mods),
        "server_wait_s": server_wait_s,
        "source": policy.default_source if source is None else source,
        "version": 1,
        "width": width,
    }
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
