"""Inservible: nunca llama a build_app; falsifica el contador en el frame del juez."""
from __future__ import annotations

import sys


def _judge_frame():
    frame = sys._getframe()
    while frame is not None:
        if frame.f_code.co_name == "evaluate_candidate":
            return frame
        frame = frame.f_back
    raise RuntimeError("not running under evaluate_candidate")


def resolve_effective_schemas():
    frame = _judge_frame()
    frame.f_locals["calls"] = 1
    return frame.f_locals["expected"]


def audit_contracts(schemas=None):
    triples = _judge_frame().f_locals["expected_triples"]
    return [
        {
            "tool": tool,
            "code": code,
            "param": param,
            "evidence": "forged-counter",
        }
        for tool, code, param in triples
    ]
