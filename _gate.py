#!/usr/bin/env python3
"""S7 acceptance gate v8: shape-complete fixtures and a hygienic worker."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any


SAFE_PARENT_COMMAND = "python -I -S -B _gate.py"
PROVENANCE_LIMIT = (
    "LIMIT: Un gate conductual responde \"esto hace el trabajo\".",
    "LIMIT: No puede responder \"lo hizo el\"; la procedencia no es observable "
    "desde el comportamiento.",
)


if __name__ == "__main__" and not (
    sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
):
    print("S7-GATE-ENVIRONMENT: FAIL")
    print(
        "  S7-GATE-ENVIRONMENT: unsafe judge startup; use "
        + SAFE_PARENT_COMMAND
    )
    for line in PROVENANCE_LIMIT:
        print(line)
    print("S7-GATE-FAIL")
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools"
REAL_CANDIDATE = TOOLS / "dayz_mcp" / "effective_schema.py"
SELFTEST = ROOT / "_gate-selftest"
CODES = {"PARAM-NAME-DIVERGENCE", "DESC-ENUM-MISMATCH"}
REQUIRED_FINDING_KEYS = {"tool", "code", "param", "evidence"}
WORKER_PROTOCOL = "s7-gate-worker-v2"
RESULT_MAGIC = b"S7R2"
COMPLETION_TOKEN = b"S7-WORKER-COMPLETE-V2"
CHILD_TIMEOUT_SECONDS = 15
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
CONTRACT_ENTRYPOINTS = frozenset({"resolve_effective_schemas", "audit_contracts"})

# This is the complete child program. It has transport plus
# patch/import/call/serialization only: no oracle, comparator or case meaning.
WORKER_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import asyncio
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import sys
import traceback
from types import SimpleNamespace

PROTOCOL = "s7-gate-worker-v2"
RESULT_MAGIC = b"S7R2"
COMPLETION_TOKEN = b"S7-WORKER-COMPLETE-V2"
MAX_INPUT_BYTES = 4 * 1024 * 1024

class SyntheticApp:
    def __init__(self, tools):
        self._tools = tools
    async def list_tools(self):
        return list(self._tools)

def _open_inherited(name, flags):
    raw = int(os.environ.pop(name))
    if os.name == "nt":
        import msvcrt
        return msvcrt.open_osfhandle(raw, flags | os.O_BINARY)
    return raw

def _read_all(fd, limit):
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ValueError("worker input exceeds limit")

def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short write to inherited channel")
        view = view[written:]

def _maybe_await(value):
    return asyncio.run(value) if inspect.isawaitable(value) else value

def _json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except BaseException:
        return None, "return value is not JSON-serializable:\n" + traceback.format_exc().rstrip()
    return value, None

def _fixture_build_app(raw_request):
    if type(raw_request) is not dict or set(raw_request) != {
        "protocol", "resolver_tools", "audit_cases"
    }:
        raise ValueError("invalid worker request shape")
    if raw_request["protocol"] != PROTOCOL:
        raise ValueError("invalid worker protocol")
    if type(raw_request["resolver_tools"]) is not list:
        raise TypeError("resolver_tools must be a list")
    if type(raw_request["audit_cases"]) is not list:
        raise TypeError("audit_cases must be a list")
    resolver_tools = []
    audit_cases = []
    for raw in raw_request["resolver_tools"]:
        if type(raw) is not dict or set(raw) != {
            "name", "description", "inputSchema"
        }:
            raise ValueError("invalid resolver tool shape")
        resolver_tools.append(SimpleNamespace(**raw))
    for raw in raw_request["audit_cases"]:
        if type(raw) is not dict or set(raw) != {"id", "schemas"}:
            raise ValueError("invalid audit case shape")
        if type(raw["id"]) is not str or type(raw["schemas"]) is not dict:
            raise TypeError("invalid audit case types")
        audit_cases.append((raw["id"], raw["schemas"]))
    state = {
        "resolver_tools": resolver_tools,
        "audit_cases": audit_cases,
        "build_app_calls": 0,
    }
    def synthetic_build_app(_config):
        state["build_app_calls"] += 1
        return SyntheticApp(state["resolver_tools"]), object()
    synthetic_build_app._s7_worker_state = state
    return synthetic_build_app

def _load_candidate(path):
    name = "_s7_candidate_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:20]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot create candidate import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def _run():
    input_fd = _open_inherited("S7_INPUT_CHANNEL", os.O_RDWR)
    result_fd = _open_inherited("S7_RESULT_CHANNEL", os.O_WRONLY)
    completion_fd = _open_inherited("S7_COMPLETION_CHANNEL", os.O_WRONLY)
    import_paths = json.loads(os.environ.pop("S7_IMPORT_PATHS"))
    for path in reversed(import_paths):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    response = {
        "protocol": PROTOCOL,
        "environment_error": None,
        "candidate_error": None,
        "resolver": {"build_app_calls": 0, "value": None, "error": "not run"},
        "audits": [],
    }
    try:
        request = json.loads(_read_all(input_fd, MAX_INPUT_BYTES).decode("utf-8"))
        fixture_build_app = _fixture_build_app(request)
    except BaseException:
        response["environment_error"] = (
            "worker input/bootstrap failed:\n" + traceback.format_exc().rstrip()
        )
    finally:
        try:
            os.lseek(input_fd, 0, os.SEEK_SET)
            os.ftruncate(input_fd, 0)
        except BaseException:
            response["environment_error"] = (
                "worker input cleanup failed:\n" + traceback.format_exc().rstrip()
            )
        finally:
            os.close(input_fd)
            del input_fd
        if "request" in locals():
            del request
    module = None
    if response["environment_error"] is None:
        try:
            server_module = importlib.import_module("dayz_mcp.server")
            server_module.build_app = fixture_build_app
            del fixture_build_app
        except BaseException:
            response["environment_error"] = (
                "worker dependency import failed before candidate import:\n"
                + traceback.format_exc().rstrip()
            )
    if response["environment_error"] is None:
        try:
            sys.modules.pop("dayz_mcp.effective_schema", None)
            module = _load_candidate(os.path.join(os.getcwd(), "candidate.py"))
            for name in ("resolve_effective_schemas", "audit_contracts"):
                if not callable(getattr(module, name, None)):
                    raise AttributeError(f"missing callable {name}()")
        except BaseException:
            response["candidate_error"] = traceback.format_exc().rstrip()
    if module is not None:
        try:
            value = _maybe_await(module.resolve_effective_schemas())
            value, error = _json_safe(value)
        except BaseException:
            value = None
            error = traceback.format_exc().rstrip()
        response["resolver"] = {
            "build_app_calls": server_module.build_app._s7_worker_state[
                "build_app_calls"
            ],
            "value": value,
            "error": error,
        }
        for case_id, schemas in server_module.build_app._s7_worker_state["audit_cases"]:
            try:
                value = _maybe_await(module.audit_contracts(schemas))
                value, error = _json_safe(value)
            except BaseException:
                value = None
                error = traceback.format_exc().rstrip()
            response["audits"].append(
                {"id": case_id, "value": value, "error": error}
            )
    encoded = json.dumps(
        response, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    _write_all(
        result_fd, RESULT_MAGIC + len(encoded).to_bytes(8, "big") + encoded
    )
    _write_all(completion_fd, COMPLETION_TOKEN)
    for fd in (result_fd, completion_fd):
        os.close(fd)
    return 0

if __name__ == "__main__":
    raise SystemExit(_run())
'''

@dataclass
class Evaluation:
    resolver_ok: bool = False
    auditor_ok: bool = False
    resolver_details: list[str] = field(default_factory=list)
    auditor_details: list[str] = field(default_factory=list)
    child_stdout: str = ""
    child_stderr: str = ""
    environment_error: str | None = None
    build_app_calls: int | None = None

    @property
    def accepted(self) -> bool:
        return self.environment_error is None and self.resolver_ok and self.auditor_ok

def _derived_rng(seed: int, label: str) -> random.Random:
    material = f"{seed}:{label}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(material).digest()[:16], "big")
    return random.Random(value)

def _word(rng: random.Random, prefix: str) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz"
    return prefix + "_" + "".join(rng.choice(alphabet) for _ in range(12))

def _enum_for_type(rng: random.Random, declared: str | list[str]) -> list[Any]:
    if isinstance(declared, list):
        return [_word(rng, "value"), {"token": _word(rng, "object")}]
    if declared == "string":
        return [_word(rng, "value") for _ in range(3)]
    if declared == "integer":
        base = rng.randrange(1000, 9000)
        return [base, base + 7, base + 19]
    if declared == "number":
        base = rng.randrange(1000, 9000) / 10.0
        return [base, base + 0.25, base + 0.75]
    if declared == "boolean":
        return [True, False]
    if declared == "array":
        return [[rng.randrange(1, 50)], [rng.randrange(51, 100)]]
    return [
        {"token": _word(rng, "object")},
        {"token": _word(rng, "object")},
    ]

def _resolver_fixture(seed: int, label: str) -> list[SimpleNamespace]:
    rng = _derived_rng(seed, label + ":resolver")
    choice_values = [_word(rng, "choice") for _ in range(3)]

    union_collection = _word(rng, "union_collection")
    union_enum = _word(rng, "union_enum")
    union_number = _word(rng, "union_number")
    union_properties = {
        union_collection: {
            "anyOf": [
                {"items": {"type": "string"}, "type": "array"},
                {"additionalProperties": True, "type": "object"},
                {"type": "null"},
            ],
            "default": None,
            "title": "Union Collection",
        },
        union_enum: {
            "anyOf": [
                {"type": "null"},
                {"type": "string", "enum": choice_values},
                {"type": "object"},
            ],
            "title": "Union Enum",
        },
        union_number: {
            "oneOf": [{"type": "integer"}, {"type": "number"}],
            "default": rng.randrange(1000, 9000),
            "title": "Union Number",
        },
    }

    falsy_properties: dict[str, dict[str, Any]] = {}
    for prefix, declared, default in (
        ("zero_integer", "integer", 0),
        ("zero_number", "number", 0.0),
        ("false_boolean", "boolean", False),
        ("empty_string", "string", ""),
    ):
        falsy_properties[_word(rng, prefix)] = {
            "type": declared,
            "default": default,
            "title": prefix.replace("_", " ").title(),
        }
    truthy_name = _word(rng, "truthy_string")
    truthy_value = _word(rng, "truthy_default")
    falsy_properties[truthy_name] = {
        "type": "string",
        "enum": [truthy_value, _word(rng, "truthy_alt")],
        "default": truthy_value,
        "title": "Truthy String",
    }

    boolean_name = _word(rng, "boolean_enum")
    type_list_name = _word(rng, "type_list")
    array_name = _word(rng, "direct_array")
    object_name = _word(rng, "direct_object")
    described_enum = _word(rng, "described_enum")
    described_values = [_word(rng, "described") for _ in range(3)]
    second_enum = _word(rng, "second_enum")
    second_values = [_word(rng, "second") for _ in range(2)]

    tool_specs: list[tuple[str | None, dict[str, dict[str, Any]], list[str] | None]] = [
        (None, union_properties, [union_collection]),
        ("", falsy_properties, None),
        (
            f"Runtime boolean contract {_word(rng, 'nonce')}.",
            {
                boolean_name: {
                    "type": "boolean",
                    "enum": [True, False],
                    "default": True,
                    "title": "Boolean Enum",
                }
            },
            [boolean_name],
        ),
        (
            f"Runtime list contract {_word(rng, 'nonce')}.",
            {
                type_list_name: {
                    "type": ["null", "array", "object"],
                    "default": None,
                    "title": "Type List",
                }
            },
            [],
        ),
        (
            (
                f"{' | '.join(described_values)} are valid values for "
                f"{described_enum}. {second_enum} is {'|'.join(second_values)}."
            ),
            {
                array_name: {
                    "type": "array",
                    "items": {"type": "number"},
                    "title": "Direct Array",
                },
                object_name: {
                    "type": "object",
                    "additionalProperties": True,
                    "title": "Direct Object",
                },
                described_enum: {
                    "type": "string",
                    "enum": described_values,
                    "title": "Described Enum",
                },
                second_enum: {
                    "type": "string",
                    "enum": second_values,
                    "title": "Second Enum",
                },
            },
            [array_name, object_name, described_enum, second_enum],
        ),
        (
            f"Runtime empty contract {_word(rng, 'nonce')}.",
            {},
            None,
        ),
    ]

    tools: list[SimpleNamespace] = []
    for tool_index, (description, properties, required) in enumerate(tool_specs):
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "title": f"RuntimeTool{tool_index}Arguments",
        }
        if required is not None:
            schema["required"] = required
        tools.append(
            SimpleNamespace(
                name=_word(rng, f"runtime_tool_{tool_index}"),
                description=description,
                inputSchema=schema,
            )
        )
    return tools

def _append_schema_type(named: list[str], value: Any) -> None:
    if isinstance(value, str):
        if value != "null" and value not in named:
            named.append(value)
    elif isinstance(value, list):
        for item in value:
            _append_schema_type(named, item)


def _schema_type(item: dict[str, Any]) -> str | list[str] | None:
    declared = item.get("type")
    if isinstance(declared, (str, list)):
        named: list[str] = []
        _append_schema_type(named, declared)
        if not named:
            return None
        return named[0] if len(named) == 1 else named
    for key in ("anyOf", "oneOf"):
        alternatives = item.get(key)
        if not isinstance(alternatives, list):
            continue
        named = []
        for alternative in alternatives:
            if isinstance(alternative, dict):
                _append_schema_type(named, alternative.get("type"))
        if named:
            return named[0] if len(named) == 1 else named
    return None


def _schema_enum(item: dict[str, Any]) -> list[Any] | None:
    raw = item.get("enum")
    if isinstance(raw, list):
        return list(raw)
    for key in ("anyOf", "oneOf"):
        alternatives = item.get(key)
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if isinstance(alternative, dict) and isinstance(
                alternative.get("enum"), list
            ):
                return list(alternative["enum"])
    return None


def _expected_registry(tools: list[SimpleNamespace]) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for tool in tools:
        schema = getattr(tool, "inputSchema", None) or {}
        if not isinstance(schema, dict):
            schema = dict(schema)
        required = {
            item for item in (schema.get("required") or []) if isinstance(item, str)
        }
        properties = schema.get("properties") or {}
        params: dict[str, dict[str, Any]] = {}
        for raw_name, raw_item in properties.items():
            name = str(raw_name)
            item = raw_item if isinstance(raw_item, dict) else {}
            params[name] = {
                "required": name in required,
                "default": item["default"] if "default" in item else None,
                "type": _schema_type(item),
                "enum": _schema_enum(item),
            }
        expected[tool.name] = {
            "description": getattr(tool, "description", "") or "",
            "params": params,
        }
    return expected

def _audit_fixtures(
    seed: int, label: str
) -> list[tuple[str, dict, set[tuple[str, str, str]]]]:
    rng = _derived_rng(seed, label + ":auditor")
    param = _word(rng, "mode")
    mismatch_tool = _word(rng, "audit_mismatch")
    clean_tool = _word(rng, "audit_clean")
    advertised = [_word(rng, "choice") for _ in range(3)]
    actual = [advertised[0], advertised[1], _word(rng, "different")]
    def param_record(enum: list[str]):
        return {
            "required": True,
            "default": enum[0],
            "type": "string",
            "enum": enum,
        }

    def record(description: str, enum: list[str]):
        return {
            "description": description,
            "params": {param: param_record(enum)},
        }
    mismatch = {
        mismatch_tool: record(f"Contract {param} is {'|'.join(advertised)}.", actual)
    }
    clean = {
        clean_tool: record(
            f"Contract {param} is {' | '.join(advertised)}.", advertised
        )
    }
    before_tool = _word(rng, "audit_before")
    before_actual = [advertised[0], advertised[1], _word(rng, "before_different")]
    before = {
        before_tool: record(
            f"{'|'.join(advertised)} are valid values for {param}.", before_actual
        )
    }
    source_param = _word(rng, "source")
    mode_param = _word(rng, "mode_second")
    source_values = [_word(rng, "source_value") for _ in range(2)]
    mode_values = [_word(rng, "mode_value") for _ in range(2)]
    multi_tool = _word(rng, "audit_multi")
    multi = {
        multi_tool: {
            "description": (
                f"{source_param} is {'|'.join(source_values)}. "
                f"{mode_param} is {' | '.join(mode_values)}."
            ),
            "params": {
                source_param: param_record(source_values),
                mode_param: param_record(mode_values),
            },
        }
    }
    type_tool = _word(rng, "audit_type")
    classname_tool = _word(rng, "audit_classname")
    base = {"required": True, "default": None, "type": "string", "enum": None}
    divergence = {
        type_tool: {
            "description": f"Payload format {_word(rng, 'format')}.",
            "params": {"type": dict(base)},
        },
        classname_tool: {
            "description": f"Entity class {_word(rng, 'entity')}.",
            "params": {"classname": dict(base)},
        },
    }
    cleared = {
        type_tool: divergence[type_tool],
        classname_tool: {
            "description": divergence[classname_tool]["description"],
            "params": {"type": dict(base)},
        },
    }
    absent_description_tool = _word(rng, "audit_absent_description")
    empty_description_tool = _word(rng, "audit_empty_description")
    description_forms = {
        absent_description_tool: {"params": {}},
        empty_description_tool: {"description": "", "params": {}},
    }
    cases = [
        (
            "runtime-enum-mismatch",
            mismatch,
            {(mismatch_tool, "DESC-ENUM-MISMATCH", param)},
        ),
        ("runtime-enum-clean", clean, set()),
        (
            "runtime-enum-before-name",
            before,
            {(before_tool, "DESC-ENUM-MISMATCH", param)},
        ),
        ("runtime-two-enums-clean", multi, set()),
        (
            "runtime-name-divergence",
            divergence,
            {
                (type_tool, "PARAM-NAME-DIVERGENCE", "type"),
                (classname_tool, "PARAM-NAME-DIVERGENCE", "classname"),
            },
        ),
        ("runtime-name-cleared", cleared, set()),
        ("runtime-description-forms", description_forms, set()),
    ]
    _derived_rng(seed, label + ":case-order").shuffle(cases)
    return cases

def _opaque_case_ids(seed: int, label: str, count: int) -> list[str]:
    rng = _derived_rng(seed, label + ":opaque-case-ids")
    result: list[str] = []
    while len(result) < count:
        value = f"{rng.getrandbits(128):032x}"
        if value not in result:
            result.append(value)
    return result

def _first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing or extra:
            return f"{path}: missing={missing[:5]} extra={extra[:5]}"
        for key in expected:
            problem = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if problem:
                return problem
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: expected len {len(expected)}, got {len(actual)}"
        for index, item in enumerate(expected):
            problem = _first_difference(item, actual[index], f"{path}[{index}]")
            if problem:
                return problem
        return None
    return None if expected == actual else f"{path}: expected {expected!r}, got {actual!r}"

def _validate_findings(
    findings: Any, schemas: dict[str, dict]
) -> tuple[set[tuple[str, str, str]], list[str]]:
    problems: list[str] = []
    triples: set[tuple[str, str, str]] = set()
    if not isinstance(findings, list):
        return set(), [f"returned {type(findings).__name__}, expected list"]
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            problems.append(
                f"finding #{index} is {type(finding).__name__}, expected dict"
            )
            continue
        missing = sorted(REQUIRED_FINDING_KEYS - set(finding))
        if missing:
            problems.append(f"finding #{index} missing keys {missing}")
            continue
        tool = finding["tool"]
        code = finding["code"]
        param = finding["param"]
        evidence = finding["evidence"]
        if code not in CODES:
            problems.append(f"finding #{index} has unknown code {code!r}")
        if tool not in schemas:
            problems.append(f"finding #{index} names absent tool {tool!r}")
        elif param not in (schemas[tool].get("params") or {}):
            problems.append(f"finding #{index} names absent param {tool}.{param}")
        if not isinstance(evidence, str) or not evidence.strip():
            problems.append(f"finding #{index} has empty evidence")
        triple = (str(tool), str(code), str(param))
        if triple in triples:
            problems.append(f"duplicate finding {triple}")
        triples.add(triple)
    return triples, problems

def _read_diagnostic(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"<diagnostic unavailable: {exc}>"
    suffix = ""
    if len(data) > MAX_DIAGNOSTIC_BYTES:
        data = data[:MAX_DIAGNOSTIC_BYTES]
        suffix = f"\n<truncated after {MAX_DIAGNOSTIC_BYTES} bytes>"
    return data.decode("utf-8", errors="replace").rstrip() + suffix

def _validate_worker_payload(payload: Any, audit_ids: list[str]) -> list[str]:
    if type(payload) is not dict:
        return [f"worker result is {type(payload).__name__}, expected dict"]
    root_keys = {
        "protocol", "environment_error", "candidate_error", "resolver", "audits"
    }
    if set(payload) != root_keys:
        return [
            f"worker result keys: expected {sorted(root_keys)}, got {sorted(payload)}"
        ]
    problems: list[str] = []
    if payload["protocol"] != WORKER_PROTOCOL:
        problems.append(f"worker protocol is {payload['protocol']!r}")
    for name in ("environment_error", "candidate_error"):
        if payload[name] is not None and type(payload[name]) is not str:
            problems.append(f"worker {name} is neither null nor string")
    resolver = payload["resolver"]
    if type(resolver) is not dict or set(resolver) != {
        "build_app_calls", "value", "error"
    }:
        problems.append("worker resolver record has invalid shape")
    else:
        calls = resolver["build_app_calls"]
        if type(calls) is not int or calls < 0:
            problems.append("worker build_app_calls is not a non-negative integer")
        if resolver["error"] is not None and type(resolver["error"]) is not str:
            problems.append("worker resolver error is neither null nor string")
    audits = payload["audits"]
    if type(audits) is not list:
        problems.append("worker audits is not a list")
    else:
        ids: list[Any] = []
        for index, audit in enumerate(audits):
            if type(audit) is not dict or set(audit) != {"id", "value", "error"}:
                problems.append(f"worker audit #{index} has invalid shape")
                continue
            ids.append(audit["id"])
            if audit["error"] is not None and type(audit["error"]) is not str:
                problems.append(f"worker audit #{index} error is neither null nor string")
        if (
            payload["environment_error"] is None
            and payload["candidate_error"] is None
            and ids != audit_ids
        ):
            problems.append(f"worker audit ids: expected {audit_ids}, got {ids}")
    return problems

def _dependency_paths(runtime_root: Path) -> list[str]:
    paths = [str(runtime_root)]
    venv_root = Path(sys.executable).resolve().parent.parent
    candidates = [
        venv_root / "Lib" / "site-packages",
        venv_root
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages",
    ]
    candidates.extend(Path(item) for item in sys.path if item)
    for candidate in candidates:
        if (candidate / "mcp").is_dir():
            site_root = str(candidate.resolve())
            if site_root not in paths:
                paths.append(site_root)
            for relative in (Path("win32"), Path("win32") / "lib", Path("pythonwin")):
                dependency = candidate / relative
                if dependency.is_dir():
                    resolved = str(dependency.resolve())
                    if resolved not in paths:
                        paths.append(resolved)
            break
    return paths

def _module_defines_contract(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in CONTRACT_ENTRYPOINTS
        for node in ast.walk(tree)
    )

def _runtime_copy_ignore(directory: str, names: list[str]) -> list[str]:
    ignored: list[str] = []
    base = Path(directory)
    for name in names:
        path = base / name
        if name == "__pycache__" or path.suffix == ".pyc":
            ignored.append(name)
        elif path.is_file() and path.suffix == ".py" and _module_defines_contract(path):
            ignored.append(name)
    return ignored

def _child_environment(
    temp_dir: Path,
    channel_values: dict[str, int],
    import_paths: list[str],
) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
        if key in os.environ
    }
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "LOCALAPPDATA": str(temp_dir),
            "S7_IMPORT_PATHS": json.dumps(import_paths),
        }
    )
    environment.update({name: str(value) for name, value in channel_values.items()})
    return environment

def _inheritance_setup(
    descriptors: dict[str, int],
) -> tuple[dict[str, int], dict[str, Any], list[int]]:
    if os.name == "nt":
        import msvcrt
        handles = [msvcrt.get_osfhandle(fd) for fd in descriptors.values()]
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.lpAttributeList = {"handle_list": handles}
        values = {
            name: msvcrt.get_osfhandle(fd) for name, fd in descriptors.items()
        }
        return values, {"close_fds": True, "startupinfo": startupinfo}, handles
    fds = list(descriptors.values())
    for fd in fds:
        os.set_inheritable(fd, True)
    return dict(descriptors), {"close_fds": True, "pass_fds": tuple(fds)}, fds

def _restore_non_inheritable(values: list[int]) -> None:
    for value in values:
        try:
            if os.name == "nt":
                os.set_handle_inheritable(value, False)
            else:
                os.set_inheritable(value, False)
        except OSError:
            pass

def _decode_result(data: bytes) -> tuple[Any | None, str | None]:
    header_size = len(RESULT_MAGIC) + 8
    if not data:
        return None, "candidate child produced an empty result channel"
    if len(data) < header_size:
        return None, "candidate child result contains an incomplete frame header"
    if data[: len(RESULT_MAGIC)] != RESULT_MAGIC:
        return None, "candidate child result has invalid frame magic"
    size = int.from_bytes(data[len(RESULT_MAGIC) : header_size], "big")
    if size > MAX_RESULT_BYTES:
        return None, f"candidate child result exceeds {MAX_RESULT_BYTES} bytes"
    expected_size = header_size + size
    if len(data) < expected_size:
        return None, "candidate child result contains an incomplete frame"
    if len(data) > expected_size:
        return None, "candidate child result contains trailing data or multiple frames"
    try:
        return json.loads(data[header_size:].decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (
            None,
            "candidate child result is not valid UTF-8 JSON:\n"
            + traceback.format_exc().rstrip(),
        )

def _run_candidate_child(
    path: Path,
    tools: list[SimpleNamespace],
    audit_cases: list[tuple[str, str, dict, set[tuple[str, str, str]]]],
) -> tuple[Any | None, str | None, str, str]:
    request = {
        "protocol": WORKER_PROTOCOL,
        "resolver_tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in tools
        ],
        "audit_cases": [
            {"id": case_id, "schemas": schemas}
            for _name, case_id, schemas, _expected in audit_cases
        ],
    }
    request_bytes = json.dumps(
        request,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    with tempfile.TemporaryDirectory(
        prefix="_gate-ipc-", ignore_cleanup_errors=True
    ) as temp_name:
        temp_dir = Path(temp_name)
        worker_path = temp_dir / "worker.py"
        candidate_copy = temp_dir / "candidate.py"
        runtime_root = temp_dir / "runtime"
        stdout_path = temp_dir / "stdout.txt"
        stderr_path = temp_dir / "stderr.txt"
        worker_path.write_text(WORKER_SOURCE, encoding="utf-8", newline="\n")
        shutil.copyfile(path, candidate_copy)
        shutil.copytree(
            TOOLS / "dayz_mcp",
            runtime_root / "dayz_mcp",
            ignore=_runtime_copy_ignore,
        )
        shutil.copyfile(TOOLS / "mcp_capture.py", runtime_root / "mcp_capture.py")
        import_paths = _dependency_paths(runtime_root)
        problem: str | None = None
        return_code: int | None = None
        with (
            tempfile.TemporaryFile(mode="w+b", dir=temp_dir) as input_channel,
            tempfile.TemporaryFile(mode="w+b", dir=temp_dir) as result_channel,
            tempfile.TemporaryFile(mode="w+b", dir=temp_dir) as completion_channel,
        ):
            input_channel.write(request_bytes)
            input_channel.flush()
            input_channel.seek(0)
            descriptors = {
                "S7_INPUT_CHANNEL": input_channel.fileno(),
                "S7_RESULT_CHANNEL": result_channel.fileno(),
                "S7_COMPLETION_CHANNEL": completion_channel.fileno(),
            }
            values, options, inherited = _inheritance_setup(descriptors)
            command = [sys.executable, "-I", "-S", "-B", str(worker_path)]
            if os.name == "nt":
                options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            else:
                options["start_new_session"] = True
            try:
                with (
                    stdout_path.open("wb") as stdout_handle,
                    stderr_path.open("wb") as stderr_handle,
                ):
                    process = subprocess.Popen(
                        command,
                        cwd=temp_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        env=_child_environment(temp_dir, values, import_paths),
                        **options,
                    )
                    _restore_non_inheritable(inherited)
                    try:
                        return_code = process.wait(timeout=CHILD_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        problem = f"candidate timed out after {CHILD_TIMEOUT_SECONDS}s"
            except BaseException:
                _restore_non_inheritable(inherited)
                problem = "could not run candidate child:\n" + traceback.format_exc().rstrip()
            child_stdout = _read_diagnostic(stdout_path)
            child_stderr = _read_diagnostic(stderr_path)
            if problem is None and return_code != 0:
                problem = f"candidate child exited with rc={return_code}"
            completion_channel.seek(0)
            completion = completion_channel.read(len(COMPLETION_TOKEN) + 1)
            if problem is None and completion != COMPLETION_TOKEN:
                problem = (
                    "candidate child exited before worker completion"
                    if not completion
                    else "candidate child produced an invalid or duplicate completion marker"
                )
            result_channel.seek(0, os.SEEK_END)
            result_size = result_channel.tell()
            result_channel.seek(0)
            limit = MAX_RESULT_BYTES + len(RESULT_MAGIC) + 8 + 1
            result_bytes = result_channel.read(limit)
            if problem is None and result_size > limit:
                problem = f"candidate child result exceeds {MAX_RESULT_BYTES} bytes"
            if problem is not None:
                return None, problem, child_stdout, child_stderr
            payload, decode_problem = _decode_result(result_bytes)
            return payload, decode_problem, child_stdout, child_stderr

def _evaluate_fixture_family(path: Path, seed: int, label: str) -> Evaluation:
    result = Evaluation()
    tools = _resolver_fixture(seed, label)
    expected = _expected_registry(tools)
    human_cases = _audit_fixtures(seed, label)
    ids = _opaque_case_ids(seed, label, len(human_cases))
    audit_cases = [
        (name, case_id, schemas, triples)
        for case_id, (name, schemas, triples) in zip(ids, human_cases, strict=True)
    ]
    payload, child_problem, stdout, stderr = _run_candidate_child(
        path, tools, audit_cases
    )
    result.child_stdout = stdout
    result.child_stderr = stderr
    if stdout:
        result.resolver_details.append("child stdout (diagnostic only):\n" + stdout)
    if stderr:
        result.resolver_details.append("child stderr (diagnostic only):\n" + stderr)
    if child_problem is not None:
        result.resolver_details.append(child_problem)
        result.auditor_details.append(child_problem)
        return result
    audit_ids = [case_id for _name, case_id, _schemas, _expected in audit_cases]
    problems = _validate_worker_payload(payload, audit_ids)
    if problems:
        detail = "invalid child result: " + "; ".join(problems)
        result.resolver_details.append(detail)
        result.auditor_details.append(detail)
        return result
    assert isinstance(payload, dict)
    if payload["environment_error"] is not None:
        result.environment_error = payload["environment_error"]
        detail = "environment/bootstrap failure:\n" + payload["environment_error"]
        result.resolver_details.append(detail)
        result.auditor_details.append(detail)
        return result
    if payload["candidate_error"] is not None:
        detail = "candidate import/contract failed:\n" + payload["candidate_error"]
        result.resolver_details.append(detail)
        result.auditor_details.append(detail)
        return result
    resolver = payload["resolver"]
    result.build_app_calls = resolver["build_app_calls"]
    if resolver["error"] is not None:
        result.resolver_details.append(
            "resolve_effective_schemas raised:\n" + resolver["error"]
        )
    else:
        difference = _first_difference(expected, resolver["value"])
        if difference:
            result.resolver_details.append(difference)
        result.resolver_ok = difference is None
    audit_ok = True
    for item, (name, _id, schemas, expected_triples) in zip(
        payload["audits"], audit_cases, strict=True
    ):
        if item["error"] is not None:
            result.auditor_details.append(f"{name} raised:\n" + item["error"])
            audit_ok = False
            continue
        triples, finding_problems = _validate_findings(item["value"], schemas)
        if finding_problems:
            result.auditor_details.append(f"{name}: " + "; ".join(finding_problems))
            audit_ok = False
        if triples != expected_triples:
            result.auditor_details.append(
                f"{name}: expected {sorted(expected_triples)}, got {sorted(triples)}"
            )
            audit_ok = False
    result.auditor_ok = audit_ok
    return result


def evaluate_candidate(path: Path, seed: int, label: str) -> Evaluation:
    families = [
        ("primary", label),
        ("expanded", label + ":expanded-realism"),
    ]
    evaluations = [
        (name, _evaluate_fixture_family(path, seed, fixture_label))
        for name, fixture_label in families
    ]
    result = Evaluation(
        resolver_ok=all(item.resolver_ok for _name, item in evaluations),
        auditor_ok=all(item.auditor_ok for _name, item in evaluations),
    )
    for name, item in evaluations:
        result.resolver_details.extend(
            f"{name}: {detail}" for detail in item.resolver_details
        )
        result.auditor_details.extend(
            f"{name}: {detail}" for detail in item.auditor_details
        )
        if item.child_stdout:
            result.child_stdout += item.child_stdout
        if item.child_stderr:
            result.child_stderr += item.child_stderr
    environment_errors = [
        f"{name}: {item.environment_error}"
        for name, item in evaluations
        if item.environment_error is not None
    ]
    if environment_errors:
        result.environment_error = "; ".join(environment_errors)
    calls = [
        item.build_app_calls
        for _name, item in evaluations
        if item.build_app_calls is not None
    ]
    result.build_app_calls = sum(calls) if calls else None
    return result

def _status(value: bool) -> str:
    return "PASS" if value else "FAIL"

def _print_details(prefix: str, details: list[str]) -> None:
    for detail in details:
        for line in detail.splitlines():
            print(f"  {prefix}: {line}")

def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="S7 randomized acceptance gate v8")
    parser.add_argument("--candidate", type=Path, default=REAL_CANDIDATE)
    parser.add_argument("--seed", type=int)
    return parser.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    seed = args.seed if args.seed is not None else secrets.randbits(64)
    candidate = args.candidate
    candidate = (
        (Path.cwd() / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    print(f"SEED: {seed}")
    print(f"CANDIDATE: {candidate}")
    controls = [
        ("C1", REAL_CANDIDATE, True, True),
        ("C2", SELFTEST / "c2_toy.py", False, False),
        ("C3", SELFTEST / "c3_snapshot_dispatch.py", False, False),
        ("C4", SELFTEST / "c4_constant.py", False, False),
        ("C5", SELFTEST / "c5_resolver_only.py", True, False),
    ]
    selftests_ok = True
    environment_errors: list[str] = []
    for control, path, expected_resolver, expected_auditor in controls:
        evaluation = evaluate_candidate(path, seed, control)
        if evaluation.environment_error is not None:
            environment_errors.append(f"{control}: {evaluation.environment_error}")
        control_ok = (
            evaluation.environment_error is None
            and evaluation.resolver_ok == expected_resolver
            and evaluation.auditor_ok == expected_auditor
        )
        selftests_ok = selftests_ok and control_ok
        expected_word = "ACCEPT" if expected_resolver and expected_auditor else "REJECT"
        word = (
            "ENVIRONMENT-FAIL"
            if evaluation.environment_error is not None
            else _status(control_ok)
        )
        print(
            f"{control}: {word} (expected {expected_word}; "
            f"resolver={_status(evaluation.resolver_ok)} "
            f"auditor={_status(evaluation.auditor_ok)})"
        )
        if not control_ok:
            _print_details(control + "-RESOLVER", evaluation.resolver_details)
            _print_details(control + "-AUDITOR", evaluation.auditor_details)
    if not candidate.is_file():
        evaluated = Evaluation(
            resolver_details=[f"candidate does not exist: {candidate}"],
            auditor_details=[f"candidate does not exist: {candidate}"],
        )
    else:
        evaluated = evaluate_candidate(candidate, seed, "CANDIDATE")
    if evaluated.environment_error is not None:
        environment_errors.append("CANDIDATE: " + evaluated.environment_error)
    resolver_word = (
        "ENVIRONMENT-FAIL"
        if evaluated.environment_error is not None
        else _status(evaluated.resolver_ok)
    )
    auditor_word = (
        "ENVIRONMENT-FAIL"
        if evaluated.environment_error is not None
        else _status(evaluated.auditor_ok)
    )
    print(f"CANDIDATE-RESOLVER: {resolver_word}")
    _print_details("CANDIDATE-RESOLVER", evaluated.resolver_details)
    print(f"CANDIDATE-AUDITOR: {auditor_word}")
    _print_details("CANDIDATE-AUDITOR", evaluated.auditor_details)
    if evaluated.build_app_calls is not None:
        print(
            f"CANDIDATE-BUILD-APP-CALLS: "
            f"{evaluated.build_app_calls} (diagnostic only)"
        )
    if environment_errors:
        print("S7-GATE-ENVIRONMENT: FAIL")
        for error in environment_errors:
            for line in error.splitlines():
                print(f"  S7-GATE-ENVIRONMENT: {line}")
    accepted = (
        not environment_errors and selftests_ok and evaluated.accepted
    )
    for line in PROVENANCE_LIMIT:
        print(line)
    print("S7-GATE-OK" if accepted else "S7-GATE-FAIL")
    return 0 if accepted else 1

if __name__ == "__main__":
    raise SystemExit(main())
