"""Run one shard of the suite CI discovers.

Invoke from tools/:

    python -B -m tests._shard <i>/<N>
    python -B -m tests._shard --list <i>/<N>

<i> is 1-based. The suite is unittest.TestLoader().discover("tests",
pattern="test*.py", top_level_dir="."), the same call the single full job
used, so load_tests and the rest of discovery stay in place.

Each direct child of that suite is kept. The shard runs that original object
once, on one shard, in discovery order. Flattening is only for counting and
grouping, so a load_tests suite keeps its own run(). Only a tree that can be
walked twice without changing it is partitioned. The root must be exactly the
loader's default suite type. Leaves are TestCase objects: an iterable TestCase
stays that case, because unittest calls it and does not run what it yields.
Every other suite must yield the same objects on two walks, whether it is a
BaseTestSuite or a repeatable adapter. A one-pass iterable (iter(x) is x, a
generator, or two walks that differ) or any other construct is refused on
every shard, by name and module, and nothing is run. If two children share a
test module, they stay on the same shard too: splitting either one would split
a module. A child with no leaves is not a module and does not move the
balance. It runs on the shard of the nearest preceding child that has leaves,
or on shard 1 when none precedes it (the tests package is usually that
child). A shard whose run executed no tests exits 1. The check is
result.testsRun == 0 with nothing skipped, which is what the unittest CLI has
done since 3.12, and it is applied here on 3.11 as well.

Warnings follow unittest's CLI (TestProgram): 'default' when the process has
no -W and no PYTHONWARNINGS; those filters stay in place when it does.

Shards are a deterministic longest-processing-time balance. A group's weight
is its test count plus SLOW_TEST_WEIGHT times the @slow_test decorators in
the source of its modules (tools/tests/_tiers.py). Heavier groups are placed
first; equal weights break ties by module name; an equal load breaks the tie
toward the lower shard index. Grouping only chooses the shard. Inside a
shard the original children run in discovery order, even when one module's
suites were not contiguous.

Exit 0 when every selected test passes. Exit 1 when a test fails, the shard
executed no tests, or the tree cannot be partitioned. Exit 2 when the
arguments are wrong.
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
    """One unsplittable piece of the suite, in discovery order.

    tests is the flattened leaves, used only to count and to name modules.
    suites is the original discover or load_tests children. positions is each
    suite's discovery index: a shard runs its suites in that order, not in
    group order.
    """

    modules: tuple[str, ...]
    tests: tuple[unittest.case.TestCase, ...]
    suites: tuple[unittest.TestSuite | unittest.case.TestCase, ...]
    positions: tuple[int, ...]


def discover_suite() -> unittest.TestSuite:
    """The suite the unsharded CI job ran."""
    return unittest.TestLoader().discover(
        "tests", pattern="test*.py", top_level_dir="."
    )


class UnsupportedSuite(Exception):
    """The tree cannot be partitioned without changing what unittest runs."""


def _type_name(kind: type) -> str:
    """Module and qualname, the name a refusal prints."""
    module = getattr(kind, "__module__", None) or "?"
    qual = getattr(kind, "__qualname__", None) or kind.__name__
    return f"{module}.{qual}"


def _construct_name(obj: object) -> str:
    """Module and qualname of obj's type."""
    return _type_name(type(obj))


def _default_suite_type() -> type:
    """The suite class a fresh loader uses before load_tests changes it."""
    return unittest.TestLoader().suiteClass


def admission_problems(suite: object) -> list[str]:
    """Why this tree cannot be partitioned, or an empty list when it can.

    Admitted: a root of exactly the default suite type, TestCase leaves, and
    suites that walk twice to the same objects. An iterable TestCase is a leaf.
    unittest calls that object; the iteration is not the tests it runs, and
    walking it would both mis-count and could consume a one-shot iterator
    before the one iter() the parent suite performs.
    """
    expected = _default_suite_type()
    if type(suite) is not expected:
        return [
            "refusing to partition "
            f"{_construct_name(suite)}: discovery root is not the default "
            f"suite type {_type_name(expected)}"
        ]
    problems: list[str] = []
    _admit_walk(suite, problems, set())
    return problems


def _admit_walk(item: object, problems: list[str], stack: set[int]) -> None:
    if isinstance(item, unittest.case.TestCase):
        return
    if id(item) in stack:
        return
    children = _repeatable_children(item, problems)
    if children is None:
        return
    stack.add(id(item))
    try:
        for child in children:
            _admit_walk(child, problems, stack)
            if problems:
                return
    finally:
        stack.discard(id(item))


def _repeatable_children(item: object, problems: list[str]) -> tuple[object, ...] | None:
    """Children when two walks yield the same objects. None if item is refused.

    A self-yield (iter('a') is the character 'a') is not another suite.
    """
    try:
        first_iter = iter(item)  # type: ignore[arg-type]
    except TypeError:
        problems.append(
            f"refusing to partition {_construct_name(item)}: "
            "not a TestCase and not a suite"
        )
        return None
    if first_iter is item:
        problems.append(
            f"refusing to partition {_construct_name(item)}: "
            "single-pass iterable (iter(x) is x)"
        )
        return None
    first = tuple(first_iter)
    try:
        second_iter = iter(item)  # type: ignore[arg-type]
    except TypeError:
        problems.append(
            f"refusing to partition {_construct_name(item)}: "
            "iteration is not repeatable"
        )
        return None
    if second_iter is item:
        problems.append(
            f"refusing to partition {_construct_name(item)}: "
            "single-pass iterable (iter(x) is x)"
        )
        return None
    second = tuple(second_iter)
    if len(first) != len(second) or any(left is not right for left, right in zip(first, second)):
        problems.append(
            f"refusing to partition {_construct_name(item)}: "
            "two walks do not yield the same objects"
        )
        return None
    return tuple(child for child in first if child is not item)


def iter_tests(item: object) -> Iterator[unittest.case.TestCase]:
    """Yield TestCase leaves under an already-admitted tree.

    Does not iterate a TestCase. A suite on the current path is a cycle, not
    another copy of its tests.
    """
    yield from _iter_tests(item, set())


def _iter_tests(item: object, stack: set[int]) -> Iterator[unittest.case.TestCase]:
    if isinstance(item, unittest.case.TestCase):
        yield item
        return
    if id(item) in stack:
        return
    try:
        children = iter(item)  # type: ignore[arg-type]
    except TypeError:
        return
    if children is item:
        return
    stack.add(id(item))
    try:
        for child in children:
            if child is item:
                continue
            yield from _iter_tests(child, stack)
    finally:
        stack.discard(id(item))


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

    Every direct child is kept. Children that name the same module are one
    group, so a module cannot land on two shards. A child that names several
    modules ties those modules together. A child with no leaves joins the
    group of the nearest preceding child that has leaves, so it runs on that
    child's shard. With no such predecessor it is its own group and
    assign_groups pins that group to shard 1.

    Raises UnsupportedSuite when the tree is outside the admitted model.
    Nothing is grouped in that case, so no shard can run a partial tree.
    """
    problems = admission_problems(suite)
    if problems:
        raise UnsupportedSuite("\n".join(problems))
    # modules, leaf tests (counting only), and the original child object.
    children: list[
        tuple[list[str], list[unittest.case.TestCase], unittest.TestSuite | unittest.case.TestCase]
    ] = []
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
        modules: list[str] = []
        seen: set[str] = set()
        for test in tests:
            name = module_of(test)
            if name not in seen:
                seen.add(name)
                modules.append(name)
        if tests and not modules:
            modules.append(f"{type(child).__module__}.{type(child).__qualname__}")
        index = len(children)
        children.append((modules, tests, child))
        parent.append(index)
        if not tests:
            continue
        for name in modules:
            owner = module_owner.get(name)
            if owner is None:
                module_owner[name] = index
            else:
                union(index, owner)

    members: dict[int, list[int]] = {}
    order: list[int] = []
    for index, (_modules, tests, _child) in enumerate(children):
        if not tests:
            continue
        root = find(index)
        if root not in members:
            members[root] = []
            order.append(root)
        members[root].append(index)

    # Mutable so a later child with no leaves can join the preceding group.
    modules_of: dict[int, list[str]] = {}
    tests_of: dict[int, list[unittest.case.TestCase]] = {}
    suites_of: dict[int, list[unittest.TestSuite | unittest.case.TestCase]] = {}
    positions_of: dict[int, list[int]] = {}
    group_of: dict[int, int] = {}
    for root in order:
        modules_of[root] = []
        tests_of[root] = []
        suites_of[root] = []
        positions_of[root] = []
        seen_modules: set[str] = set()
        for index in members[root]:
            child_modules, child_tests, child_suite = children[index]
            for name in child_modules:
                if name not in seen_modules:
                    seen_modules.add(name)
                    modules_of[root].append(name)
            tests_of[root].extend(child_tests)
            suites_of[root].append(child_suite)
            positions_of[root].append(index)
            group_of[index] = root

    prefix_suites: list[unittest.TestSuite | unittest.case.TestCase] = []
    prefix_positions: list[int] = []
    for index, (_modules, tests, child) in enumerate(children):
        if tests:
            continue
        host: int | None = None
        for earlier in range(index - 1, -1, -1):
            if children[earlier][1]:
                host = earlier
                break
        if host is None:
            prefix_suites.append(child)
            prefix_positions.append(index)
            continue
        root = group_of[host]
        suites_of[root].append(child)
        positions_of[root].append(index)

    groups: list[Group] = []
    if prefix_suites:
        groups.append(Group((), (), tuple(prefix_suites), tuple(prefix_positions)))
    for root in order:
        groups.append(
            Group(
                tuple(modules_of[root]),
                tuple(tests_of[root]),
                tuple(suites_of[root]),
                tuple(positions_of[root]),
            )
        )
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
    """Place whole groups onto shard_count shards. Shard 0 is index 1.

    A group with no tests is the leading children that have no leaves. They
    are not balanced: shard 1 runs them. Every other group has tests.
    """
    if shard_count < 1:
        raise ValueError("shard count must be at least 1")
    discover_order = {id(group): index for index, group in enumerate(groups)}
    pinned = [group for group in groups if not group.tests]
    floating = [group for group in groups if group.tests]
    weighted = [(group_weight(group), group) for group in floating]
    ranked = sorted(weighted, key=lambda item: (-item[0], item[1].modules))
    loads = [0] * shard_count
    buckets: list[list[Group]] = [[] for _ in range(shard_count)]
    for weight, group in ranked:
        shard = min(range(shard_count), key=lambda item: (loads[item], item))
        buckets[shard].append(group)
        loads[shard] += weight
    buckets[0].extend(pinned)
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


def _runner_warnings() -> str | None:
    """Same policy as unittest.main.TestProgram (main.py).

    'default' when warnings were not configured. None leaves -W and
    PYTHONWARNINGS alone: TextTestRunner only calls simplefilter when
    warnings is set (runner.py).
    """
    if not sys.warnoptions:
        return "default"
    return None


def run_assigned(
    groups: list[Group], *, index: int, count: int, list_only: bool
) -> int:
    chosen = assign_groups(groups, count)[index - 1]
    if not chosen:
        print(f"shard {index}/{count} has no tests", file=sys.stderr)
        return 1
    # --list does not execute, so it uses the admitted leaves. A run does not:
    # unittest's CLI (main.py, since 3.12) treats testsRun == 0 as no tests.
    has_tests = any(group.tests for group in chosen)
    if list_only:
        if not has_tests:
            print(f"shard {index}/{count} has no tests", file=sys.stderr)
            return 1
        for name in module_names(chosen):
            print(name)
        return 0
    suite = unittest.TestSuite()
    # positions are discovery indexes. A group holds every child of its
    # modules, so walking group.suites would run A2 before B when discovery
    # was A1, B, A2. The shard runs the selected children in discovery order.
    selected = sorted(
        (
            (position, original)
            for group in chosen
            for position, original in zip(group.positions, group.suites, strict=True)
        ),
        key=lambda item: item[0],
    )
    for _, original in selected:
        # addTest keeps the suite. addTests iterates it and would drop a
        # load_tests suite's own run() (suite.py).
        suite.addTest(original)
    result = unittest.TextTestRunner(verbosity=1, warnings=_runner_warnings()).run(suite)
    # Same order as TestProgram.runTests: a failure wins, then no tests ran.
    # Exit 1 on both, including on 3.11 where the CLI does not do this itself.
    # Skips with testsRun == 0 follow the CLI and are not "no tests".
    if not result.wasSuccessful():
        return 1
    if result.testsRun == 0 and len(result.skipped) == 0:
        print(f"shard {index}/{count} has no tests", file=sys.stderr)
        return 1
    return 0


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
    try:
        groups = groups_from_suite(discover_suite())
    except UnsupportedSuite as exc:
        print(exc, file=sys.stderr)
        return 1
    return run_assigned(groups, index=index, count=count, list_only=list_only)


if __name__ == "__main__":
    sys.exit(main())
