"""The four CI shards are a stable partition of the discovered suite.

Discovery here is the same call the runner uses, so load_tests is included.
The test that walks the suite is @slow_test: the fast tier does not need to
build the whole suite a second time.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from tests import _shard
from tests._tiers import slow_test


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


if __name__ == "__main__":
    unittest.main()
