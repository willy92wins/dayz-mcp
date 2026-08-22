"""Handler.timeout must stay strictly above the POST /wait long-poll ceiling.

The ceiling lives in Handler._handle_session as the `timeout_s` comparison,
not as a shared constant the socket timeout can import. This module reads that
comparison from the handler source so raising the wait cap without raising the
socket deadline turns the suite red.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from dayz_mcp import loopback


def _is_timeout_s(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "timeout_s"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
        return len(node.args) == 1 and _is_timeout_s(node.args[0])
    return False


def _numeric(node: ast.expr, namespace: dict[str, object]) -> float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric(node.operand, namespace)
        return None if inner is None else -inner
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return float(node.value)
    if isinstance(node, ast.Name):
        value = namespace.get(node.id)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _is_wait_branch(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    if not (isinstance(test.left, ast.Name) and test.left.id == "action"):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    right = test.comparators[0]
    return isinstance(right, ast.Constant) and right.value == "wait"


def _wait_branch(tree: ast.AST) -> ast.If:
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_wait_branch(node.test)
    ]
    if len(found) != 1:
        raise AssertionError(
            "expected exactly one `action == \"wait\"` branch in "
            f"Handler._handle_session, got {len(found)}"
        )
    return found[0]


def _wait_timeout_ceiling_s() -> float:
    """Max timeout_s the /wait handler accepts, read from its comparison."""
    source = textwrap.dedent(inspect.getsource(loopback.Handler._handle_session))
    tree = ast.parse(source)
    namespace = dict(loopback.Handler._handle_session.__globals__)
    ceilings: list[float] = []
    for node in ast.walk(_wait_branch(tree)):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for index, op in enumerate(node.ops):
            left = operands[index]
            right = operands[index + 1]
            if isinstance(op, (ast.Gt, ast.GtE)) and _is_timeout_s(left):
                value = _numeric(right, namespace)
                if value is not None:
                    ceilings.append(value)
            elif isinstance(op, (ast.Lt, ast.LtE)) and _is_timeout_s(right):
                value = _numeric(left, namespace)
                if value is not None:
                    ceilings.append(value)
    if len(ceilings) != 1:
        raise AssertionError(
            "expected exactly one timeout_s upper bound in the /wait branch, "
            f"got {ceilings!r}"
        )
    return ceilings[0]


class HandlerSocketTimeoutTests(unittest.TestCase):
    def test_handler_declares_its_own_numeric_timeout(self) -> None:
        self.assertIn("timeout", vars(loopback.Handler))
        declared = vars(loopback.Handler)["timeout"]
        self.assertIsInstance(declared, (int, float))
        self.assertNotIsInstance(declared, bool)

    def test_socket_timeout_exceeds_wait_ceiling(self) -> None:
        ceiling = _wait_timeout_ceiling_s()
        self.assertGreater(loopback.Handler.timeout, ceiling)

    def test_socket_timeout_is_not_none(self) -> None:
        self.assertIsNotNone(loopback.Handler.timeout)


if __name__ == "__main__":
    unittest.main()
