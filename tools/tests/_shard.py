"""Run one shard of the suite CI discovers.

Invoke from tools/:

    python -B -m tests._shard <i>/<N>
    python -B -m tests._shard --list <i>/<N>

<i> is 1-based. The suite is unittest.TestLoader().discover("tests",
pattern="test*.py", top_level_dir="."), the same call the single full job
used, so load_tests and the rest of discovery stay in place.

Each direct child of that suite is one discovered module (a test file, or
whatever load_tests returned for it). A child is never split. If two children
share a test module, they stay on the same shard too: splitting either one
would split a module. Children with no tests are not modules.

Shards are a deterministic longest-processing-time balance. A group's weight
is its test count plus SLOW_TEST_WEIGHT times the @slow_test decorators in
the source of its modules (tools/tests/_tiers.py). Heavier groups are placed
first; equal weights break ties by module name; an equal load breaks the tie
toward the lower shard index. Inside a shard, modules run in discovery order.

Exit 0 when every selected test passes. Exit 1 when a test fails or the shard
has no tests. Exit 2 when the arguments are wrong.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

# A @slow_test is most of a full run (_tiers.py: a few hundred of them are the
# bulk of the time). 30 makes one of them outweigh a batch of fast tests so
# the heavy modules land on different shards. The count is the decorator in
# the source, including a class decorator over that class's test methods.
SLOW_TEST_WEIGHT = 30

_SPEC = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")
_USAGE = "usage: python -B -m tests._shard [--list] <i>/<N>"


class Group(NamedTuple):
    """One unsplittable piece of the suite, in discovery order."""

    modules: tuple[str, ...]
    tests: tuple[unittest.case.TestCase, ...]


def discover_suite() -> unittest.TestSuite:
    """The suite the unsharded CI job ran."""
    return unittest.TestLoader().discover(
        "tests", pattern="test*.py", top_level_dir="."
    )


def iter_tests(item: unittest.TestSuite | unittest.case.TestCase) -> Iterator[unittest.case.TestCase]:
    if isinstance(item, unittest.TestSuite):
        for child in item:
            yield from iter_tests(child)
        return
    yield item


def module_of(test: unittest.case.TestCase) -> str:
    """Dotted module that owns this test.

    An import or load_tests failure is reported as a synthetic case whose
    method name is the module that failed. That name is the module.
    """
    cls = type(test)
    if cls.__module__ == "unittest.loader" and cls.__name__ in {"_FailedTest", "ModuleSkipped"}:
        method = getattr(test, "_testMethodName", None)
        if isinstance(method, str) and method:
            return method
    module = getattr(cls, "__module__", None)
    if isinstance(module, str) and module:
        return module
    return test.id().rsplit(".", 1)[0]


def groups_from_suite(suite: unittest.TestSuite) -> list[Group]:
    """Whole discovered modules, in discovery order.

    Children that name the same module are one group, so a module cannot land
    on two shards. A child that names several modules ties those modules
    together for the same reason.
    """
    children: list[tuple[list[str], list[unittest.case.TestCase]]] = []
    parent: list[int] = []
    module_owner: dict[str, int] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for child in suite:
        tests = list(iter_tests(child))
        if not tests:
            continue
        modules: list[str] = []
        seen: set[str] = set()
        for test in tests:
            name = module_of(test)
            if name not in seen:
                seen.add(name)
                modules.append(name)
        index = len(children)
        children.append((modules, tests))
        parent.append(index)
        for name in modules:
            owner = module_owner.get(name)
            if owner is None:
                module_owner[name] = index
            else:
                union(index, owner)

    members: dict[int, list[int]] = {}
    order: list[int] = []
    for index in range(len(children)):
        root = find(index)
        if root not in members:
            members[root] = []
            order.append(root)
        members[root].append(index)

    groups: list[Group] = []
    for root in order:
        modules: list[str] = []
        seen_modules: set[str] = set()
        tests: list[unittest.case.TestCase] = []
        for index in members[root]:
            child_modules, child_tests = children[index]
            for name in child_modules:
                if name not in seen_modules:
                    seen_modules.add(name)
                    modules.append(name)
            tests.extend(child_tests)
        groups.append(Group(tuple(modules), tuple(tests)))
    return groups


def _module_source(module_name: str) -> str | None:
    module = sys.modules.get(module_name)
    file = getattr(module, "__file__", None) if module is not None else None
    candidates: list[Path] = []
    if isinstance(file, str) and file.endswith(".py"):
        candidates.append(Path(file))
    relative = Path(*module_name.split("."))
    candidates.append(relative.with_suffix(".py"))
    candidates.append(relative / "__init__.py")
    for path in candidates:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
    return None


def _is_slow_decorator(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id == "slow_test"
    if isinstance(target, ast.Attribute):
        return target.attr == "slow_test"
    return False


def _decorated_slow(node: ast.AST) -> bool:
    return any(_is_slow_decorator(item) for item in getattr(node, "decorator_list", ()))


def _slow_tests_in_source(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return _slow_tests_in_body(tree.body)


def _slow_tests_in_body(body: Iterable[ast.AST]) -> int:
    total = 0
    for node in body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = [
            item
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name.startswith("test")
        ]
        if _decorated_slow(node):
            total += len(methods)
            continue
        total += sum(1 for item in methods if _decorated_slow(item))
    return total


def slow_test_count(module_name: str) -> int:
    source = _module_source(module_name)
    if source is None:
        return 0
    return _slow_tests_in_source(source)


def group_weight(group: Group) -> int:
    slow = sum(slow_test_count(name) for name in group.modules)
    return len(group.tests) + SLOW_TEST_WEIGHT * slow


def assign_groups(groups: list[Group], shard_count: int) -> list[list[Group]]:
    """Place whole groups onto shard_count shards. Shard 0 is index 1."""
    if shard_count < 1:
        raise ValueError("shard count must be at least 1")
    discover_order = {id(group): index for index, group in enumerate(groups)}
    weighted = [(group_weight(group), group) for group in groups]
    ranked = sorted(weighted, key=lambda item: (-item[0], item[1].modules))
    loads = [0] * shard_count
    buckets: list[list[Group]] = [[] for _ in range(shard_count)]
    for weight, group in ranked:
        shard = min(range(shard_count), key=lambda item: (loads[item], item))
        buckets[shard].append(group)
        loads[shard] += weight
    for bucket in buckets:
        bucket.sort(key=lambda group: discover_order[id(group)])
    return buckets


def parse_shard_spec(text: str) -> tuple[int, int]:
    """Return (index, count) for a 1-based '<i>/<N>' spec."""
    match = _SPEC.fullmatch(text)
    if match is None:
        raise ValueError(f"shard spec must look like 1/4, not {text!r}")
    index, count = int(match.group(1)), int(match.group(2))
    if index > count:
        raise ValueError(f"shard {index}/{count} is outside 1..{count}")
    return index, count


def module_names(groups: Iterable[Group]) -> list[str]:
    names: list[str] = []
    for group in groups:
        names.extend(group.modules)
    return names


def run_assigned(
    groups: list[Group], *, index: int, count: int, list_only: bool
) -> int:
    chosen = assign_groups(groups, count)[index - 1]
    if not chosen or not any(group.tests for group in chosen):
        print(f"shard {index}/{count} has no tests", file=sys.stderr)
        return 1
    if list_only:
        for name in module_names(chosen):
            print(name)
        return 0
    suite = unittest.TestSuite()
    for group in chosen:
        suite.addTests(group.tests)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    list_only = False
    if argv and argv[0] == "--list":
        list_only = True
        argv = argv[1:]
    if len(argv) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        index, count = parse_shard_spec(argv[0])
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    groups = groups_from_suite(discover_suite())
    return run_assigned(groups, index=index, count=count, list_only=list_only)


if __name__ == "__main__":
    sys.exit(main())
