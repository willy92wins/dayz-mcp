"""Load only normal daemon authority without importing lifecycle-capable modules."""

from __future__ import annotations

import json
import os
from typing import MutableMapping

from dayz_mcp import host_config
from dayz_mcp.daemon_policy_contract import (
    AccreditedDaemonPolicy,
    authority_payload,
    authority_sha256,
)


_INHERITED_POLICY_ENV = "DAYZ_MCP_NORMAL_POLICY_JSON"
_INHERITED_POLICY_KEYS = frozenset(
    {
        "argv",
        "authority_sha256",
        "cwd",
        "format_version",
        "host",
        "keyfile",
        "kind",
        "native_executable",
        "port",
        "security_build_id",
    }
)


def _reject_duplicate_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_normal_policy_manifest")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid_normal_policy_manifest")


def serialize_normal_daemon_policy(policy: AccreditedDaemonPolicy) -> str:
    if type(policy) is not AccreditedDaemonPolicy or policy.kind != "normal":
        raise ValueError("invalid_normal_policy_manifest")
    policy.revalidate()
    payload = authority_payload(
        kind=policy.kind,
        host=policy.host,
        port=policy.port,
        keyfile=policy.keyfile,
        native_executable=policy.native_executable,
        argv=policy.argv,
        cwd=policy.cwd,
        security_build_id=policy.security_build_id,
    )
    manifest = {
        **payload,
        "authority_sha256": policy.authority_sha256,
        "format_version": 1,
    }
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if not 1 <= len(serialized) <= 8191 or "\0" in serialized:
        raise ValueError("invalid_normal_policy_manifest")
    return serialized


def load_inherited_normal_daemon_policy(
    environ: MutableMapping[str, str] | None = None,
) -> AccreditedDaemonPolicy:
    environment = os.environ if environ is None else environ
    raw = environment.pop(_INHERITED_POLICY_ENV, None)
    if not isinstance(raw, str) or not 1 <= len(raw) <= 8191 or "\0" in raw:
        raise ValueError("invalid_normal_policy_manifest")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError):
        raise ValueError("invalid_normal_policy_manifest") from None
    if (
        not isinstance(value, dict)
        or set(value) != _INHERITED_POLICY_KEYS
        or type(value.get("format_version")) is not int
        or value["format_version"] != 1
        or json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        != raw
    ):
        raise ValueError("invalid_normal_policy_manifest")
    argv = value.get("argv")
    if not isinstance(argv, list):
        raise ValueError("invalid_normal_policy_manifest")
    try:
        policy = AccreditedDaemonPolicy(
            kind=value["kind"],
            host=value["host"],
            port=value["port"],
            keyfile=value["keyfile"],
            native_executable=value["native_executable"],
            argv=tuple(argv),
            cwd=value["cwd"],
            security_build_id=value["security_build_id"],
            authority_sha256=value["authority_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_normal_policy_manifest") from None
    policy.revalidate()
    return policy


def _from_provenance(provenance: object) -> AccreditedDaemonPolicy:
    port = getattr(provenance, "port")
    keyfile = getattr(provenance, "keyfile")
    native_executable = getattr(provenance, "native_executable")
    argv = getattr(provenance, "argv")
    cwd = getattr(provenance, "cwd")
    payload = authority_payload(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=keyfile,
        native_executable=native_executable,
        argv=argv,
        cwd=cwd,
        security_build_id=None,
    )
    return AccreditedDaemonPolicy(
        kind="normal",
        host="127.0.0.1",
        port=port,
        keyfile=keyfile,
        native_executable=native_executable,
        argv=argv,
        cwd=cwd,
        security_build_id=None,
        authority_sha256=authority_sha256(**payload),
    )


def load_normal_daemon_policy() -> AccreditedDaemonPolicy:
    policy = _from_provenance(host_config.resolve_daemon_provenance())

    def revalidate_normal() -> None:
        if _from_provenance(host_config.resolve_daemon_provenance()) != policy:
            raise ValueError("daemon_policy_drift")

    object.__setattr__(policy, "_revalidation_hook", revalidate_normal)
    return policy
