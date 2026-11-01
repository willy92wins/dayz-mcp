"""The four CI shards are a stable partition of the discovered suite.

Discovery here is the same call the runner uses, so load_tests is included.
The test that walks the suite is @slow_test: the fast tier does not need to
build the whole suite a second time.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tests import _shard
from tests._tiers import slow_test

_SHARD_FILE = Path(__file__).resolve().parent / "_shard.py"
_DRIVER = """\
import importlib.util
import sys
import unittest

spec = importlib.util.spec_from_file_location("shard_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.TestLoader().discover("tests", pattern="test*.py", top_level_dir=".")
try:
    groups = module.groups_from_suite(suite)
except Exception as exc:
    if type(exc).__name__ != "UnsupportedSuite":
        raise
    print(exc, file=sys.stderr)
    raise SystemExit(1)
code = module.run_assigned(
    groups, index=1, count=1, list_only=False
)
raise SystemExit(code)
"""

# Runs shard argv[2]/argv[3] of whatever tests/ discover finds in cwd.
_SHARD_RUN = """\
import importlib.util
import sys
import unittest

spec = importlib.util.spec_from_file_location("shard_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.TestLoader().discover("tests", pattern="test*.py", top_level_dir=".")
try:
    groups = module.groups_from_suite(suite)
except Exception as exc:
    if type(exc).__name__ != "UnsupportedSuite":
        raise
    print(exc, file=sys.stderr)
    raise SystemExit(1)
code = module.run_assigned(
    groups,
    index=int(sys.argv[2]),
    count=int(sys.argv[3]),
    list_only=False,
)
raise SystemExit(code)
"""

# Independent of tests._shard.iter_tests: an iterative walk, and it grades the
# runner by the original suite objects, not by the runner's own leaf list.
_PARTITION_DRIVER = """\
import importlib.util
import sys
import unittest

spec = importlib.util.spec_from_file_location("shard_under_test", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def stable_children(item, stack):
    # Independent of tests._shard. A TestCase is the object unittest calls.
    # A suite counts only when two walks yield the same objects. One pass is
    # not an expectation: walking it changes the run.
    if isinstance(item, unittest.case.TestCase):
        return None
    if id(item) in stack:
        return ()
    try:
        first_iter = iter(item)
    except TypeError:
        return None
    if first_iter is item:
        raise RuntimeError(
            "single-pass iterable %s.%s" % (type(item).__module__, type(item).__qualname__)
        )
    first = tuple(first_iter)
    second_iter = iter(item)
    if second_iter is item:
        raise RuntimeError(
            "single-pass iterable %s.%s" % (type(item).__module__, type(item).__qualname__)
        )
    second = tuple(second_iter)
    if len(first) != len(second) or any(left is not right for left, right in zip(first, second)):
        raise RuntimeError(
            "two walks differ %s.%s" % (type(item).__module__, type(item).__qualname__)
        )
    return tuple(child for child in first if child is not item)


def leaves(item):
    pending = [(item, ())]
    while pending:
        current, stack = pending.pop()
        try:
            children = stable_children(current, stack)
        except RuntimeError as exc:
            print("UNWALKABLE %s" % exc)
            raise SystemExit(1)
        if children is None:
            yield current
            continue
        deeper = stack + (id(current),)
        pending.extend(reversed([(child, deeper) for child in children]))


def module_name(test):
    kind = type(test)
    if kind.__module__ == "unittest.loader" and kind.__name__ in {"_FailedTest", "ModuleSkipped"}:
        method = getattr(test, "_testMethodName", None)
        if isinstance(method, str) and method:
            return method
    name = getattr(kind, "__module__", None)
    if isinstance(name, str) and name:
        return name
    return test.id().rsplit(".", 1)[0]


suite = unittest.TestLoader().discover("tests", pattern="test*.py", top_level_dir=".")
assigned = module.assign_groups(module.groups_from_suite(suite), 4)
owner = {}
for shard_index, shard in enumerate(assigned):
    for group in shard:
        for original in group.suites:
            for test in leaves(original):
                owner[id(test)] = shard_index

by_module = {}
missing = False
for test in leaves(suite):
    shard_index = owner.get(id(test))
    if shard_index is None:
        missing = True
        print("MISSING %s" % test.id())
        continue
    by_module.setdefault(module_name(test), set()).add(shard_index)

split = False
for name in sorted(by_module):
    shards = sorted(by_module[name])
    print(
        "MODULE %s shards=%s count=%s"
        % (name, ",".join(str(item) for item in shards), len(shards))
    )
    if len(shards) != 1:
        split = True
raise SystemExit(1 if missing or split else 0)
"""


class ShardSpecTest(unittest.TestCase):
    def test_parse_shard_spec(self) -> None:
        self.assertEqual(_shard.parse_shard_spec("1/4"), (1, 4))
        self.assertEqual(_shard.parse_shard_spec("4/4"), (4, 4))
        for text in ("0/4", "1/0", "5/4", "1/4/2", "1-4", ""):
            with self.assertRaises(ValueError):
                _shard.parse_shard_spec(text)

    def test_a_bad_spec_exits_nonzero_without_running(self) -> None:
        self.assertEqual(_shard.main([]), 2)
        self.assertEqual(_shard.main(["nope"]), 2)
        self.assertEqual(_shard.main(["--list"]), 2)

    def test_an_empty_shard_is_an_error(self) -> None:
        with redirect_stderr(io.StringIO()) as captured:
            code = _shard.run_assigned([], index=1, count=4, list_only=True)
        self.assertEqual(code, 1)
        self.assertIn("no tests", captured.getvalue())


class ShardPartitionTest(unittest.TestCase):
    @slow_test
    def test_four_shards_partition_the_suite_and_stay_stable(self) -> None:
        suite = _shard.discover_suite()
        groups = _shard.groups_from_suite(suite)
        first = _shard.assign_groups(groups, 4)
        second = _shard.assign_groups(
            _shard.groups_from_suite(_shard.discover_suite()), 4
        )
        first_names = [_shard.module_names(shard) for shard in first]
        second_names = [_shard.module_names(shard) for shard in second]
        self.assertEqual(first_names, second_names)

        discovered: list[str] = []
        seen: set[str] = set()
        for test in _suite_leaves(suite):
            name = _shard.module_of(test)
            if name not in seen:
                seen.add(name)
                discovered.append(name)
        union = [name for names in first_names for name in names]
        self.assertEqual(sorted(union), sorted(discovered))
        self.assertEqual(len(union), len(set(union)), "a module is on two shards")
        for names in first_names:
            self.assertTrue(names, "a shard has no modules")

        # Ownership comes from walking the original suites, not group.tests.
        # group.tests is the runner's own flattening, so it cannot be the oracle.
        owner: dict[int, int] = {}
        for shard_index, shard in enumerate(first):
            for group in shard:
                for original in group.suites:
                    for test in _suite_leaves(original):
                        self.assertNotIn(id(test), owner, test.id())
                        owner[id(test)] = shard_index
        all_tests = list(_suite_leaves(suite))
        self.assertEqual(
            {id(test) for test in all_tests},
            set(owner),
            "a discovered test is missing from the shards or was added",
        )
        for child in suite:
            tests = list(_suite_leaves(child))
            if not tests:
                continue
            shards = {owner[id(test)] for test in tests}
            self.assertEqual(
                len(shards),
                1,
                f"a discovered module was split across shards: {tests[0].id()}",
            )
        by_module: dict[str, set[int]] = {}
        for test in all_tests:
            by_module.setdefault(_shard.module_of(test), set()).add(owner[id(test)])
        for name, shards in by_module.items():
            self.assertEqual(
                len(shards),
                1,
                f"module {name} was split across shards {sorted(shards)}",
            )

        # Direct children, not the runner's leaf walk. Every discovered child
        # is executed by exactly one shard: none dropped, none duplicated.
        executed_ids = [
            id(original)
            for shard in first
            for group in shard
            for original in group.suites
        ]
        self.assertEqual(
            sorted(executed_ids),
            sorted(_discovered_child_ids(suite)),
            "a discovered child was dropped or duplicated",
        )

        parts: list[str] = []
        for index, shard in enumerate(first, start=1):
            names = _shard.module_names(shard)
            test_count = sum(len(group.tests) for group in shard)
            parts.append(f"{index}: {len(names)} modules, {test_count} tests")
        print("SHARD_SIZES " + "; ".join(parts), flush=True)


def _discovered_child_ids(suite: unittest.TestSuite) -> list[int]:
    """Ids of the suite's own children, in discovery order.

    This does not call tests._shard. The partition check compares the
    runner's assigned suites to this list.
    """
    return [id(child) for child in suite]


def _stable_suite_children(item: object, stack: tuple[int, ...]) -> tuple[object, ...] | None:
    """Children of a repeatable suite, or None when item is a leaf.

    Not tests._shard. A TestCase is the object unittest calls, even if it
    iterates. Two walks must yield the same objects before those objects are
    expectations. A one-pass iterable raises: the walk would change the run,
    so it is not a source of expected tests.
    """
    if isinstance(item, unittest.case.TestCase):
        return None
    if id(item) in stack:
        return ()
    try:
        first_iter = iter(item)  # type: ignore[arg-type]
    except TypeError:
        return None
    kind = type(item)
    label = f"{kind.__module__}.{kind.__qualname__}"
    if first_iter is item:
        raise RuntimeError(f"single-pass iterable {label}")
    first = tuple(first_iter)
    try:
        second_iter = iter(item)  # type: ignore[arg-type]
    except TypeError as exc:
        raise RuntimeError(f"iteration is not repeatable {label}") from exc
    if second_iter is item:
        raise RuntimeError(f"single-pass iterable {label}")
    second = tuple(second_iter)
    if len(first) != len(second) or any(left is not right for left, right in zip(first, second)):
        raise RuntimeError(f"two walks differ {label}")
    return tuple(child for child in first if child is not item)


def _suite_leaves(item: object):
    """Leaf tests under item.

    Iterative on purpose, and not tests._shard.iter_tests. Expectations come
    from a walk that can be repeated. The regression tests observe the
    processes, not this list.
    """
    pending: list[tuple[object, tuple[int, ...]]] = [(item, ())]
    while pending:
        current, stack = pending.pop()
        children = _stable_suite_children(current, stack)
        if children is None:
            yield current
            continue
        deeper = stack + (id(current),)
        pending.extend(reversed([(child, deeper) for child in children]))


def _one_test_suite(module_name: str) -> unittest.TestSuite:
    """A suite whose only case lives in a fresh module."""
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    class Case(unittest.TestCase):
        def test_one(self) -> None:
            pass

    Case.__module__ = module_name
    module.Case = Case
    suite = unittest.TestSuite()
    suite.addTest(Case("test_one"))
    return suite


def _fresh_env() -> dict[str, str]:
    """A child with no -W and no PYTHONWARNINGS, whatever this process inherited."""
    env = os.environ.copy()
    env.pop("PYTHONWARNINGS", None)
    return env


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")


def _toy_exit_codes(root: Path) -> tuple[int, str, int, str]:
    """Exit code and stderr of unittest discover and of one shard, on `root`."""
    driver = root / "_drive_shard.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = _fresh_env()
    discover = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    shard = subprocess.run(
        [sys.executable, "-B", str(driver), str(_SHARD_FILE)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return discover.returncode, discover.stderr, shard.returncode, shard.stderr


class ShardRunnerParityTest(unittest.TestCase):
    def test_load_tests_suite_matches_unittest_discover(self) -> None:
        """A load_tests suite that records an error in its own run().

        The suite also holds a passing test. Flattening runs that test and
        drops the suite's run(), so the shard exits 0 while discover exits 1.
        """
        with tempfile.TemporaryDirectory(prefix="shard-load-tests-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "test_hook.py",
                """
                import sys
                import unittest

                class PassingTest(unittest.TestCase):
                    def test_passes(self):
                        pass

                class HookSuite(unittest.TestSuite):
                    def run(self, result, debug=False):
                        # Held before run(): TestSuite clears its slots after each test.
                        case = next(iter(self))
                        super().run(result, debug=debug)
                        try:
                            raise RuntimeError("load_tests suite ran")
                        except RuntimeError:
                            result.addError(case, sys.exc_info())
                        return result

                def load_tests(loader, tests, pattern):
                    suite = HookSuite()
                    suite.addTest(PassingTest("test_passes"))
                    return suite
                """,
            )
            discover_code, discover_err, shard_code, shard_err = _toy_exit_codes(root)
        self.assertEqual(
            shard_code,
            discover_code,
            f"discover={discover_code} shard={shard_code}\n"
            f"--- discover stderr ---\n{discover_err}\n"
            f"--- shard stderr ---\n{shard_err}",
        )
        self.assertEqual(discover_code, 1)
        self.assertIn("load_tests suite ran", discover_err)
        self.assertIn("load_tests suite ran", shard_err)

    def test_deprecation_warning_matches_unittest_discover(self) -> None:
        """Without -W, a DeprecationWarning must be emitted, as unittest's CLI does.

        The probe records the warning and fails if the filter swallowed it.
        It does not call simplefilter itself.
        """
        with tempfile.TemporaryDirectory(prefix="shard-warnings-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "test_warn.py",
                """
                import warnings
                import unittest

                class DeprecationFilterTest(unittest.TestCase):
                    def test_deprecation_is_emitted(self):
                        with warnings.catch_warnings(record=True) as caught:
                            warnings.warn("shard-deprecation-probe", DeprecationWarning)
                        emitted = [
                            item for item in caught
                            if issubclass(item.category, DeprecationWarning)
                        ]
                        if not emitted:
                            self.fail("DeprecationWarning was not emitted")
                """,
            )
            discover_code, discover_err, shard_code, shard_err = _toy_exit_codes(root)
        self.assertEqual(
            shard_code,
            discover_code,
            f"discover={discover_code} shard={shard_code}\n"
            f"--- discover stderr ---\n{discover_err}\n"
            f"--- shard stderr ---\n{shard_err}",
        )
        self.assertEqual(discover_code, 0)
        self.assertNotIn("DeprecationWarning was not emitted", shard_err)
        self.assertNotIn("DeprecationWarning was not emitted", discover_err)

    def test_base_test_suite_keeps_a_module_on_one_shard(self) -> None:
        """A case of module A hidden in a BaseTestSuite stays with A's other case.

        One load_tests suite nests BaseTestSuite -> TestSuite -> case. The other
        holds a second case of the same class directly. Discover runs the class
        and module fixtures once; the four shards must too, and the independent
        walk must see A on a single shard.
        """
        with tempfile.TemporaryDirectory(prefix="shard-base-suite-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            log_path = root / "fixtures.log"
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "cases.py",
                """
                import os
                import unittest

                def _mark(kind):
                    with open(os.environ["SHARD_FIXTURE_LOG"], "a", encoding="utf-8") as handle:
                        handle.write(kind + "\\n")

                def setUpModule():
                    _mark("module-setup")

                def tearDownModule():
                    _mark("module-teardown")

                class Shared(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        _mark("class-setup")

                    @classmethod
                    def tearDownClass(cls):
                        _mark("class-teardown")

                    def test_one(self):
                        _mark("ran-one")

                    def test_two(self):
                        _mark("ran-two")
                """,
            )
            _write(
                root / "tests" / "test_nested.py",
                """
                import unittest
                from tests.cases import Shared

                def load_tests(loader, tests, pattern):
                    inner = unittest.TestSuite()
                    inner.addTest(Shared("test_one"))
                    hidden = unittest.BaseTestSuite()
                    hidden.addTest(inner)
                    outer = unittest.TestSuite()
                    outer.addTest(hidden)
                    return outer
                """,
            )
            _write(
                root / "tests" / "test_direct.py",
                """
                import unittest
                from tests.cases import Shared

                def load_tests(loader, tests, pattern):
                    outer = unittest.TestSuite()
                    outer.addTest(Shared("test_two"))
                    return outer
                """,
            )
            env = _fresh_env()
            env["SHARD_FIXTURE_LOG"] = str(log_path)
            partition = root / "_partition.py"
            partition.write_text(_PARTITION_DRIVER, encoding="utf-8")
            runner = root / "_run_shard.py"
            runner.write_text(_SHARD_RUN, encoding="utf-8")
            graded = subprocess.run(
                [sys.executable, "-B", str(partition), str(_SHARD_FILE)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            log_path.write_text("", encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            control_log = log_path.read_text(encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            shard_codes: list[int] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [sys.executable, "-B", str(runner), str(_SHARD_FILE), str(index), "4"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                shard_codes.append(run.returncode)
            shard_log = log_path.read_text(encoding="utf-8")
        self.assertEqual(
            graded.returncode,
            0,
            f"independent walk split a module\n{graded.stdout}\n{graded.stderr}",
        )
        self.assertIn("MODULE tests.cases ", graded.stdout)
        self.assertIn("count=1", graded.stdout.split("MODULE tests.cases ", 1)[1].splitlines()[0])
        self.assertEqual(discover.returncode, 0, discover.stderr)
        for label in ("module-setup", "module-teardown", "class-setup", "class-teardown"):
            self.assertEqual(control_log.count(label), 1, f"control {label}\n{control_log}")
            self.assertEqual(
                shard_log.count(label),
                1,
                f"shards {label} codes={shard_codes}\n{shard_log}",
            )
        self.assertEqual(shard_log.count("ran-one"), 1, shard_log)
        self.assertEqual(shard_log.count("ran-two"), 1, shard_log)

    def test_empty_custom_suite_does_not_pass_a_shard(self) -> None:
        """Three one-test modules plus an empty TestSuite subclass.

        The empty suite has no tests, so it runs on the preceding module's
        shard. The shard that receives no child still exits non-zero. Shards
        that do have tests still pass.
        """
        names = ("shard_reg_a", "shard_reg_b", "shard_reg_c")

        class EmptyCustomSuite(unittest.TestSuite):
            pass

        top = unittest.TestSuite()
        try:
            for name in names:
                top.addTest(_one_test_suite(name))
            top.addTest(EmptyCustomSuite())
            groups = _shard.groups_from_suite(top)
            assigned = _shard.assign_groups(groups, 4)
            counts: list[int] = []
            codes: list[int] = []
            for index, shard in enumerate(assigned, start=1):
                counts.append(
                    sum(
                        sum(1 for _ in _suite_leaves(original))
                        for group in shard
                        for original in group.suites
                    )
                )
                with redirect_stderr(io.StringIO()):
                    codes.append(
                        _shard.run_assigned(groups, index=index, count=4, list_only=False)
                    )
        finally:
            for name in names:
                sys.modules.pop(name, None)
        self.assertEqual(counts.count(0), 1, f"counts={counts} codes={codes}")
        for count, code in zip(counts, codes, strict=True):
            if count == 0:
                self.assertNotEqual(code, 0, f"counts={counts} codes={codes}")
            else:
                self.assertEqual(code, 0, f"counts={counts} codes={codes}")

    def test_interleaved_module_keeps_discovery_order(self) -> None:
        """Hooks A1, B, A2, with A1 and A2 cases of one module.

        B fails unless it runs after A1 and before A2. Discover keeps that
        order. Grouping may put A1 and A2 in one group, but a shard that
        runs both must still execute the children in discovery order. 1/1
        covers every child; three heavier modules put A and B on one shard
        of four.
        """
        with tempfile.TemporaryDirectory(prefix="shard-order-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "mod_a.py",
                """
                import unittest

                LOG = []

                class Shared(unittest.TestCase):
                    def test_first(self):
                        LOG.append("A1")

                    def test_later(self):
                        LOG.append("A2")
                """,
            )
            _write(
                root / "tests" / "test_a1.py",
                """
                import unittest
                from tests.mod_a import Shared

                def load_tests(loader, tests, pattern):
                    suite = unittest.TestSuite()
                    suite.addTest(Shared("test_first"))
                    return suite
                """,
            )
            _write(
                root / "tests" / "test_b.py",
                """
                import unittest
                from tests.mod_a import LOG

                class Middle(unittest.TestCase):
                    def test_runs_between_a1_and_a2(self):
                        self.assertEqual(
                            LOG,
                            ["A1"],
                            "B ran out of discovery order: %r" % (LOG,),
                        )
                        LOG.append("B")
                """,
            )
            _write(
                root / "tests" / "test_c.py",
                """
                import unittest
                from tests.mod_a import Shared

                def load_tests(loader, tests, pattern):
                    suite = unittest.TestSuite()
                    suite.addTest(Shared("test_later"))
                    return suite
                """,
            )
            for name in ("test_d", "test_e", "test_f"):
                body = ["import unittest", "", "class Pad(unittest.TestCase):"]
                for index in range(5):
                    body.append(f"    def test_{index}(self):")
                    body.append("        pass")
                _write(root / "tests" / f"{name}.py", "\n".join(body) + "\n")
            env = _fresh_env()
            (root / "_drive.py").write_text(_DRIVER, encoding="utf-8")
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            (root / "_part.py").write_text(_PARTITION_DRIVER, encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            whole = subprocess.run(
                [sys.executable, "-B", str(root / "_drive.py"), str(_SHARD_FILE)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            graded = subprocess.run(
                [sys.executable, "-B", str(root / "_part.py"), str(_SHARD_FILE)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )

            def shard_of(module_name: str) -> int:
                prefix = f"MODULE {module_name} "
                for line in graded.stdout.splitlines():
                    if line.startswith(prefix):
                        shards = line.split("shards=", 1)[1].split(" ", 1)[0]
                        parts = [int(item) for item in shards.split(",") if item]
                        self.assertEqual(parts, [parts[0]], line)
                        return parts[0]
                self.fail(f"{module_name} missing from\n{graded.stdout}\n{graded.stderr}")

            shared = shard_of("tests.mod_a")
            self.assertEqual(shard_of("tests.test_b"), shared, graded.stdout)
            placed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(root / "_run.py"),
                    str(_SHARD_FILE),
                    str(shared + 1),
                    "4",
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        detail = (
            f"discover={discover.returncode} 1/1={whole.returncode} "
            f"shared_shard={shared + 1}/4 code={placed.returncode}\n"
            f"--- partition ---\n{graded.stdout}\n"
            f"--- discover stderr ---\n{discover.stderr}\n"
            f"--- 1/1 stderr ---\n{whole.stderr}\n"
            f"--- shared stderr ---\n{placed.stderr}"
        )
        self.assertEqual(graded.returncode, 0, detail)
        self.assertEqual(
            (discover.returncode, whole.returncode, placed.returncode),
            (0, 0, 0),
            detail,
        )

    def test_no_discovered_child_is_dropped_or_duplicated(self) -> None:
        """A standard suite with no leaves is still one of the children.

        The wrapper holds a custom suite and no test cases. A trailing empty
        TestSuite is another child. Both must be assigned once.
        """
        names = ("shard_keep_a", "shard_keep_b")

        class Inner(unittest.TestSuite):
            pass

        wrapper = unittest.TestSuite()
        wrapper.addTest(Inner())
        top = unittest.TestSuite()
        try:
            top.addTest(wrapper)
            for name in names:
                top.addTest(_one_test_suite(name))
            top.addTest(unittest.TestSuite())
            assigned = _shard.assign_groups(_shard.groups_from_suite(top), 4)
            executed = [
                original
                for shard in assigned
                for group in shard
                for original in group.suites
            ]
            discovered = [id(child) for child in top]
        finally:
            for name in names:
                sys.modules.pop(name, None)
        self.assertEqual(
            sorted(id(child) for child in executed),
            sorted(discovered),
            "a discovered child was dropped or duplicated",
        )

    def test_leafless_wrapper_matches_unittest_discover(self) -> None:
        """load_tests returns a TestSuite whose only child is a custom suite.

        The custom suite has no cases and records an error from run(). Discover
        reports that error. 1/1 and the four shards must too: the wrapper is a
        discovered child, so it runs once on one shard.
        """
        with tempfile.TemporaryDirectory(prefix="shard-wrapper-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            for name in ("test_m1", "test_m2", "test_m3", "test_m4"):
                _write(
                    root / "tests" / f"{name}.py",
                    """
                    import unittest

                    class One(unittest.TestCase):
                        def test_ok(self):
                            pass
                    """,
                )
            _write(
                root / "tests" / "test_wrap.py",
                """
                import sys
                import unittest

                class Marker(unittest.TestCase):
                    def test_marker(self):
                        pass

                class Hidden(unittest.TestSuite):
                    def run(self, result, debug=False):
                        super().run(result, debug=debug)
                        case = Marker("test_marker")
                        try:
                            raise RuntimeError("nested custom suite ran")
                        except RuntimeError:
                            result.addError(case, sys.exc_info())
                        return result

                def load_tests(loader, tests, pattern):
                    outer = unittest.TestSuite()
                    outer.addTest(Hidden())
                    return outer
                """,
            )
            env = _fresh_env()
            (root / "_drive.py").write_text(_DRIVER, encoding="utf-8")
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            whole = subprocess.run(
                [sys.executable, "-B", str(root / "_drive.py"), str(_SHARD_FILE)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            codes: list[int] = []
            shard_errs: list[str] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(root / "_run.py"),
                        str(_SHARD_FILE),
                        str(index),
                        "4",
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                codes.append(run.returncode)
                shard_errs.append(run.stderr)
        marker = "nested custom suite ran"
        detail = (
            f"discover={discover.returncode} 1/1={whole.returncode} "
            f"shards={codes}\n"
            f"--- discover stderr ---\n{discover.stderr}\n"
            f"--- 1/1 stderr ---\n{whole.stderr}\n"
            f"--- shard stderr ---\n" + "\n".join(shard_errs)
        )
        self.assertEqual(
            (
                discover.returncode,
                whole.returncode,
                tuple(sorted(codes)),
                marker in discover.stderr,
                marker in whole.stderr,
                sum(marker in err for err in shard_errs),
            ),
            (1, 1, (0, 0, 0, 1), True, True, 1),
            detail,
        )

    def test_iterable_adapter_keeps_a_module_on_one_shard(self) -> None:
        """An iterable adapter that is not a BaseTestSuite still hides a case.

        unittest treats anything iter() accepts as a suite (suite.py
        _isnotsuite). One hook nests that adapter around a case of the shared
        module; the other hook holds a second case. Discover runs the module
        and class fixtures once. The shards must too.
        """
        with tempfile.TemporaryDirectory(prefix="shard-adapter-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            log_path = root / "fixtures.log"
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "cases.py",
                """
                import os
                import unittest

                def _mark(kind):
                    with open(os.environ["SHARD_FIXTURE_LOG"], "a", encoding="utf-8") as handle:
                        handle.write(kind + "\\n")

                def setUpModule():
                    _mark("module-setup")

                def tearDownModule():
                    _mark("module-teardown")

                class Shared(unittest.TestCase):
                    @classmethod
                    def setUpClass(cls):
                        _mark("class-setup")

                    @classmethod
                    def tearDownClass(cls):
                        _mark("class-teardown")

                    def test_one(self):
                        _mark("ran-one")

                    def test_two(self):
                        _mark("ran-two")
                """,
            )
            _write(
                root / "tests" / "test_adapt.py",
                """
                import unittest
                from tests.cases import Shared

                class Adapter:
                    def __init__(self, inner):
                        self._inner = inner

                    def __iter__(self):
                        return iter(self._inner)

                    def __call__(self, result):
                        return self._inner(result)

                def load_tests(loader, tests, pattern):
                    inner = unittest.TestSuite()
                    inner.addTest(Shared("test_one"))
                    outer = unittest.TestSuite()
                    outer.addTest(Adapter(inner))
                    return outer
                """,
            )
            _write(
                root / "tests" / "test_direct.py",
                """
                import unittest
                from tests.cases import Shared

                def load_tests(loader, tests, pattern):
                    outer = unittest.TestSuite()
                    outer.addTest(Shared("test_two"))
                    return outer
                """,
            )
            for name in ("test_pad1", "test_pad2"):
                _write(
                    root / "tests" / f"{name}.py",
                    """
                    import unittest

                    class Pad(unittest.TestCase):
                        def test_ok(self):
                            pass
                    """,
                )
            env = _fresh_env()
            env["SHARD_FIXTURE_LOG"] = str(log_path)
            (root / "_drive.py").write_text(_DRIVER, encoding="utf-8")
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            control = log_path.read_text(encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            codes: list[int] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [sys.executable, "-B", str(root / "_run.py"), str(_SHARD_FILE), str(index), "4"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                codes.append(run.returncode)
            shard_log = log_path.read_text(encoding="utf-8")
        detail = f"discover={discover.returncode} shards={codes}\n{discover.stderr}\n{shard_log}"
        self.assertEqual(discover.returncode, 0, detail)
        for label in ("module-setup", "module-teardown", "class-setup", "class-teardown"):
            self.assertEqual(control.count(label), 1, f"control {label}\n{detail}")
            self.assertEqual(shard_log.count(label), 1, f"shards {label}\n{detail}")
        self.assertEqual(shard_log.count("ran-one"), 1, detail)
        self.assertEqual(shard_log.count("ran-two"), 1, detail)

    def test_empty_iterable_adapter_does_not_pass_a_shard(self) -> None:
        """An empty iterable adapter is a suite with no tests, not a passing run.

        Three ordinary modules fill three shards. The adapter used to count as
        a leaf, land on the remaining shard, print Ran 0 tests and exit 0.
        """
        with tempfile.TemporaryDirectory(prefix="shard-empty-adapter-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            log_path = root / "fixtures.log"
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "test_empty.py",
                """
                import os

                class EmptyAdapter:
                    def __iter__(self):
                        return iter(())

                    def __call__(self, result):
                        with open(os.environ["SHARD_FIXTURE_LOG"], "a", encoding="utf-8") as handle:
                            handle.write("empty-adapter\\n")
                        return result

                def load_tests(loader, tests, pattern):
                    return EmptyAdapter()
                """,
            )
            for name in ("test_m1", "test_m2", "test_m3"):
                _write(
                    root / "tests" / f"{name}.py",
                    """
                    import unittest

                    class One(unittest.TestCase):
                        def test_ok(self):
                            pass
                    """,
                )
            env = _fresh_env()
            env["SHARD_FIXTURE_LOG"] = str(log_path)
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            control = log_path.read_text(encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            codes: list[int] = []
            errs: list[str] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [sys.executable, "-B", str(root / "_run.py"), str(_SHARD_FILE), str(index), "4"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                codes.append(run.returncode)
                errs.append(run.stderr)
            shard_log = log_path.read_text(encoding="utf-8")
        zero_codes = [code for code, err in zip(codes, errs, strict=True) if "Ran 0 tests" in err]
        detail = (
            f"discover={discover.returncode} shards={codes} zero={zero_codes}\n"
            f"control={control!r} shards_log={shard_log!r}\n" + "\n".join(errs)
        )
        self.assertEqual(zero_codes, [], detail)
        self.assertEqual(control.count("empty-adapter"), 1, detail)
        self.assertEqual(shard_log.count("empty-adapter"), 1, detail)
        self.assertEqual(discover.returncode, 0, detail)

    def test_one_pass_adapter_is_refused_not_skipped(self) -> None:
        """A generator held by an adapter runs once under unittest and must not vanish.

        Walking it to partition consumes the generator. Discover still records
        the failure. No shard may exit 0, and the refusal must name the adapter.
        """
        with tempfile.TemporaryDirectory(prefix="shard-oneshot-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "test_boom.py",
                """
                import unittest

                class Boom(unittest.TestCase):
                    def test_fails(self):
                        self.fail("boom-marker")

                class OneShot:
                    def __init__(self, source):
                        self._source = source

                    def __iter__(self):
                        return self._source

                    def __call__(self, result):
                        for test in self._source:
                            test(result)
                        return result

                def load_tests(loader, tests, pattern):
                    case = Boom("test_fails")

                    def source():
                        yield case

                    outer = unittest.TestSuite()
                    outer.addTest(OneShot(source()))
                    return outer
                """,
            )
            for name in ("test_p1", "test_p2", "test_p3", "test_p4"):
                _write(
                    root / "tests" / f"{name}.py",
                    """
                    import unittest

                    class Pad(unittest.TestCase):
                        def test_ok(self):
                            pass
                    """,
                )
            env = _fresh_env()
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            codes: list[int] = []
            errs: list[str] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [sys.executable, "-B", str(root / "_run.py"), str(_SHARD_FILE), str(index), "4"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                codes.append(run.returncode)
                errs.append(run.stderr)
        detail = f"discover={discover.returncode} shards={codes}\n{discover.stderr}\n" + "\n".join(errs)
        self.assertNotEqual(discover.returncode, 0, detail)
        self.assertIn("boom-marker", discover.stderr, detail)
        self.assertTrue(all(code != 0 for code in codes), detail)
        self.assertTrue(
            all("tests.test_boom.OneShot" in err for err in errs),
            detail,
        )

    def test_iterable_testcase_matches_unittest_discover(self) -> None:
        """An empty iterable TestCase still runs. The shard must match discover.

        unittest calls the case. A structural count of its iteration sees no
        tests and fails the shard after the case has passed.
        """
        with tempfile.TemporaryDirectory(prefix="shard-itercase-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            log_path = root / "fixtures.log"
            _write(root / "tests" / "__init__.py", "")
            _write(
                root / "tests" / "test_itercase.py",
                """
                import os
                import unittest

                class IteratesEmpty(unittest.TestCase):
                    def __iter__(self):
                        return iter(())

                    def test_runs(self):
                        with open(os.environ["SHARD_FIXTURE_LOG"], "a", encoding="utf-8") as handle:
                            handle.write("ran-iterable\\n")
                """,
            )
            env = _fresh_env()
            env["SHARD_FIXTURE_LOG"] = str(log_path)
            (root / "_drive.py").write_text(_DRIVER, encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            control = log_path.read_text(encoding="utf-8")
            log_path.write_text("", encoding="utf-8")
            whole = subprocess.run(
                [sys.executable, "-B", str(root / "_drive.py"), str(_SHARD_FILE)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            shard_log = log_path.read_text(encoding="utf-8")
        detail = (
            f"discover={discover.returncode} shard={whole.returncode}\n"
            f"control={control!r} shard_log={shard_log!r}\n"
            f"{discover.stderr}\n{whole.stderr}"
        )
        self.assertEqual(control.count("ran-iterable"), 1, detail)
        self.assertEqual(shard_log.count("ran-iterable"), 1, detail)
        self.assertEqual(discover.returncode, 0, detail)
        self.assertEqual(whole.returncode, discover.returncode, detail)

    def test_custom_suite_class_root_is_refused(self) -> None:
        """loader.suiteClass replaces discovery's root. That root must not vanish.

        The hook runs after the module suites exist, so only the root has the
        subclass. Discover records its error. Shards must not exit 0.
        """
        with tempfile.TemporaryDirectory(prefix="shard-suiteclass-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            _write(root / "tests" / "__init__.py", "")
            for name in ("test_a", "test_b", "test_c"):
                _write(
                    root / "tests" / f"{name}.py",
                    """
                    import unittest

                    class Pad(unittest.TestCase):
                        def test_ok(self):
                            pass
                    """,
                )
            _write(
                root / "tests" / "test_zhook.py",
                """
                import sys
                import unittest

                class MarkingSuite(unittest.TestSuite):
                    def run(self, result, debug=False):
                        super().run(result, debug)

                        class Probe(unittest.TestCase):
                            def runTest(self):
                                pass

                        try:
                            raise RuntimeError("suiteclass-marker")
                        except RuntimeError:
                            result.addError(Probe(), sys.exc_info())

                class Hook(unittest.TestCase):
                    def test_ok(self):
                        pass

                def load_tests(loader, tests, pattern):
                    loader.suiteClass = MarkingSuite
                    return tests
                """,
            )
            env = _fresh_env()
            (root / "_run.py").write_text(_SHARD_RUN, encoding="utf-8")
            discover = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            codes: list[int] = []
            errs: list[str] = []
            for index in range(1, 5):
                run = subprocess.run(
                    [sys.executable, "-B", str(root / "_run.py"), str(_SHARD_FILE), str(index), "4"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                codes.append(run.returncode)
                errs.append(run.stderr)
        detail = f"discover={discover.returncode} shards={codes}\n{discover.stderr}\n" + "\n".join(errs)
        self.assertNotEqual(discover.returncode, 0, detail)
        self.assertIn("suiteclass-marker", discover.stderr, detail)
        self.assertTrue(all(code != 0 for code in codes), detail)
        self.assertTrue(
            all("tests.test_zhook.MarkingSuite" in err for err in errs),
            detail,
        )

    @slow_test
    def test_real_suite_is_admitted(self) -> None:
        """tests/ is inside the model. A blind partition is not that check."""
        admit = getattr(_shard, "admission_problems", None)
        self.assertTrue(
            callable(admit),
            "runner has no admission check; it partitions every tree",
        )
        self.assertEqual(admit(_shard.discover_suite()), [])


if __name__ == "__main__":
    unittest.main()
