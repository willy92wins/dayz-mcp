"""Non-launching immutable daemon authority value shared by all clients."""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
import unicodedata
from dataclasses import dataclass

from dayz_mcp.daemon_contract import classify_dayz_argv


HEX64 = re.compile(r"[0-9a-f]{64}")


def valid_text(value: object, *, maximum: int = 520) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and unicodedata.is_normalized("NFC", value)
        and "\0" not in value
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def valid_absolute_path(value: object) -> bool:
    if not valid_text(value) or ntpath.normpath(value) != value:
        return False
    drive, tail = ntpath.splitdrive(value)
    return (
        len(drive) == 2
        and drive[1] == ":"
        and drive[0].isascii()
        and drive[0].isalpha()
        and tail.startswith("\\")
        and ":" not in tail
    )


def authority_payload(
    *,
    kind: str,
    host: str,
    port: int,
    keyfile: str,
    native_executable: str,
    argv: tuple[str, ...],
    cwd: str,
    security_build_id: str | None,
) -> dict[str, object]:
    return {
        "argv": list(argv),
        "cwd": cwd,
        "host": host,
        "keyfile": keyfile,
        "kind": kind,
        "native_executable": native_executable,
        "port": port,
        "security_build_id": security_build_id,
    }


def authority_sha256(**fields: object) -> str:
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AccreditedDaemonPolicy:
    kind: str
    host: str
    port: int
    keyfile: str
    native_executable: str
    argv: tuple[str, ...]
    cwd: str
    security_build_id: str | None
    authority_sha256: str

    def __post_init__(self) -> None:
        if self.kind not in {"normal", "bootstrap"} or self.host != "127.0.0.1":
            raise ValueError("invalid_daemon_policy")
        if type(self.port) is not int or not 1 <= self.port <= 65_535:
            raise ValueError("invalid_daemon_policy")
        if not all(
            valid_absolute_path(value)
            for value in (self.keyfile, self.native_executable, self.cwd)
        ):
            raise ValueError("invalid_daemon_policy")
        if (
            type(self.argv) is not tuple
            or not 1 <= len(self.argv) <= 64
            or any(not valid_text(value) for value in self.argv)
        ):
            raise ValueError("invalid_daemon_policy")
        expected_argv_kind = (
            "normal_daemon" if self.kind == "normal" else "p0s_bootstrap_daemon"
        )
        if classify_dayz_argv(list(self.argv)) != expected_argv_kind:
            raise ValueError("invalid_daemon_policy")
        if self.kind == "normal":
            if self.security_build_id is not None:
                raise ValueError("invalid_daemon_policy")
        elif not isinstance(self.security_build_id, str) or HEX64.fullmatch(
            self.security_build_id
        ) is None:
            raise ValueError("invalid_daemon_policy")
        payload = authority_payload(
            kind=self.kind,
            host=self.host,
            port=self.port,
            keyfile=self.keyfile,
            native_executable=self.native_executable,
            argv=self.argv,
            cwd=self.cwd,
            security_build_id=self.security_build_id,
        )
        if (
            not isinstance(self.authority_sha256, str)
            or HEX64.fullmatch(self.authority_sha256) is None
            or self.authority_sha256 != authority_sha256(**payload)
        ):
            raise ValueError("invalid_daemon_policy")

    def revalidate(self) -> None:
        self.__post_init__()
        hook = getattr(self, "_revalidation_hook", None)
        if hook is not None:
            hook()
