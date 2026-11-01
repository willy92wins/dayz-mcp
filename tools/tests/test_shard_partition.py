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
code = module.run_assigned(
    module.groups_from_suite(suite), index=1, count=1, list_only=False
)
raise SystemExit(code)
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
        for test in _shard.iter_tests(suite):
            name = _shard.module_of(test)
            if name not in seen:
                seen.add(name)
                discovered.append(name)
        union = [name for names in first_names for name in names]
        self.assertEqual(sorted(union), sorted(discovered))
        self.assertEqual(len(union), len(set(union)), "a module is on two shards")
        for names in first_names:
            self.assertTrue(names, "a shard has no modules")

        owner: dict[int, int] = {}
        for shard_index, shard in enumerate(first):
            for group in shard:
                for test in group.tests:
                    self.assertNotIn(id(test), owner)
                    owner[id(test)] = shard_index
        all_tests = list(_shard.iter_tests(suite))
        self.assertEqual(
            {id(test) for test in all_tests},
            set(owner),
            "a discovered test is missing from the shards or was added",
        )
        for child in suite:
            tests = list(_shard.iter_tests(child))
            if not tests:
                continue
            shards = {owner[id(test)] for test in tests}
            self.assertEqual(
                len(shards),
                1,
                f"a discovered module was split across shards: {tests[0].id()}",
            )

        parts: list[str] = []
        for index, shard in enumerate(first, start=1):
            names = _shard.module_names(shard)
            test_count = sum(len(group.tests) for group in shard)
            parts.append(f"{index}: {len(names)} modules, {test_count} tests")
        print("SHARD_SIZES " + "; ".join(parts), flush=True)


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


if __name__ == "__main__":
    unittest.main()
