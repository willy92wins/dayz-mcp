"""Inservible: no importa MCP, no lista tools, no audita.

Lee `input.json` (argv[3]) y escribe `result.json` (argv[4]) con la expectativa
derivada del fixture, luego `os._exit(0)` para que el worker no pise el fichero.
Los nombres de caso del request (`runtime-enum-mismatch`, ...) cifran el
veredicto del auditor. `build_app` no se llama; `calls` se pone a 1 en el JSON.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def resolve_effective_schemas():
    raise RuntimeError("inservible: no resuelve; solo falsifica result.json del worker")


def audit_contracts(schemas=None):
    raise RuntimeError("inservible: no audita; solo falsifica result.json del worker")


def _project_fixture(resolver_tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # Proyeccion mecanica del fixture del gate (toda property trae type/enum/default).
    # En el arbol real falta `enum`/`default` y esto revienta: no es un resolutor.
    expected: dict[str, dict[str, Any]] = {}
    for tool in resolver_tools:
        schema = tool["inputSchema"]
        required = set(schema["required"])
        params: dict[str, dict[str, Any]] = {}
        for name, param_schema in schema["properties"].items():
            declared = param_schema["type"]
            if isinstance(declared, list):
                non_null = [item for item in declared if item != "null"]
                published_type: Any = non_null[0] if len(non_null) == 1 else non_null
            else:
                published_type = None if declared == "null" else declared
            params[name] = {
                "required": name in required,
                "default": param_schema["default"],
                "type": published_type,
                "enum": list(param_schema["enum"]),
            }
        expected[tool["name"]] = {"description": tool["description"], "params": params}
    return expected


def _canned_audit(case_name: str, schemas: dict[str, Any]) -> list[dict[str, str]]:
    if case_name in {"runtime-enum-clean", "runtime-name-cleared"}:
        return []
    if case_name == "runtime-enum-mismatch":
        findings = []
        for tool, spec in schemas.items():
            for param in (spec or {}).get("params") or {}:
                findings.append(
                    {
                        "tool": tool,
                        "code": "DESC-ENUM-MISMATCH",
                        "param": param,
                        "evidence": "stolen-from-channel",
                    }
                )
        return findings
    if case_name == "runtime-name-divergence":
        findings = []
        for tool, spec in schemas.items():
            for param in (spec or {}).get("params") or {}:
                if param in {"type", "classname"}:
                    findings.append(
                        {
                            "tool": tool,
                            "code": "PARAM-NAME-DIVERGENCE",
                            "param": param,
                            "evidence": "stolen-from-channel",
                        }
                    )
        return findings
    raise RuntimeError(f"unknown leaked case name {case_name!r}")


def _forge_result_file() -> None:
    if len(sys.argv) != 5 or sys.argv[1] != "--_worker":
        return
    request = json.loads(Path(sys.argv[3]).read_bytes().decode("utf-8"))
    if type(request) is not dict:
        raise RuntimeError("worker request is not an object")
    projected = _project_fixture(request["resolver_tools"])
    audits = []
    for raw_case in request["audit_cases"]:
        audits.append(
            {
                "name": raw_case["name"],
                "value": _canned_audit(raw_case["name"], raw_case["schemas"]),
                "error": None,
            }
        )
    payload = {
        "protocol": request["protocol"],
        "bootstrap_error": None,
        "resolver": {"calls": 1, "value": projected, "error": None},
        "audits": audits,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    with open(sys.argv[4], "xb") as handle:
        handle.write(encoded)
    os._exit(0)


_forge_result_file()
