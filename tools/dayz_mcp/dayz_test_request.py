from __future__ import annotations

import hashlib
import json
import ntpath
import re
import unicodedata
import uuid
from dataclasses import dataclass


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
    }
)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_dayz_test_request")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid_dayz_test_request")


def _invalid() -> None:
    raise ValueError("invalid_dayz_test_request")


def _invalid_policy() -> None:
    raise ValueError("invalid_dayz_test_policy")


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
        _invalid()
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        _invalid()
    if not isinstance(value, dict) or not set(value).issubset(_REQUEST_KEYS):
        raise ValueError("invalid_dayz_test_request")
    if not _valid_unicode_tree(value):
        _invalid()
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
        _invalid()
    if policy is None:
        _invalid()

    mode = value.get("mode", "all")
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
    run_id = value.get("run_id")

    if mode not in {"offline", "server", "client", "all"}:
        _invalid()
    if not _bounded_text(mission, 1, 520) or (
        mission not in {"chernarus", "livonia", "sakhal"}
        and not _path_is_within(mission, policy.mission_roots)
    ):
        _invalid()
    if source is not None and (
        not _path_is_within(source, (policy.default_source,))
    ):
        _invalid()
    if not all(
        _valid_mod_list(candidate, policy.mod_roots)
        for candidate in (extra_mods, base_mods, server_mods)
    ):
        _invalid()
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
        )
    ):
        _invalid()
    if not _bounded_int(port, 1024, 65530):
        _invalid()
    if not _bounded_int(width, 320, 16384) or not _bounded_int(
        height, 320, 16384
    ):
        _invalid()
    if not _bounded_text(player_name, 1, 64) or any(
        ord(char) <= 31 or 127 <= ord(char) <= 159 for char in player_name
    ):
        _invalid()
    if not _bounded_int(server_wait_s, 1, 3600):
        _invalid()
    if run_id is not None and not _valid_uuid4(run_id):
        _invalid()
    if no_base_mods and "base_mods" in value and bool(base_mods):
        _invalid()

    effective_build = build or clean
    if pack_only and not effective_build:
        _invalid()
    canonical_default_source = (
        set(value) == _REQUEST_KEYS
        and source == policy.default_source
        and not effective_build
    )
    if source is not None and not effective_build and not canonical_default_source:
        _invalid()
    if kill and (
        run_id is None or effective_build or pack_only or preflight
    ):
        _invalid()
    if not preflight:
        if mode in {"server", "all"} and run_id is not None:
            _invalid()
        if mode == "client" and run_id is None:
            _invalid()

    payload: dict[str, object] = {
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
        _invalid()
    if len(canonical_bytes) > 65_536:
        _invalid()
    return ParsedDayzTestRequest(
        payload=payload,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )
