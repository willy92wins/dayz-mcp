"""c31e review: each claim of world_spawn's ContaminatedArea note stays bound to its source.

The first review of the note found three ways it could mislead while the c31e oracle still passed:
- the claims could be swapped (Dynamic starting its zone, Local not) and the oracle would not notice;
- the 360 s lifetime of ContaminatedArea_Local was credited to two in-game runs, which deleted their areas by hand
  before it ran out; the figure is m_Lifetime in the vanilla script;
- the 310 s measurement on ContaminatedArea_Dynamic did not say which flags the spawn used (flags=0, the default).

The assertions read the description a client gets (resolve_effective_schemas, the list_tools path) and split it into
clauses at whitespace that follows a full stop, a semicolon or a colon.
"""

from __future__ import annotations

import functools
import re
import unittest

from dayz_mcp.effective_schema import resolve_effective_schemas

# An eight-character lowercase hex token with a digit in it: the short form of a run id.
_RUN_ID = re.compile(r"\b(?=[0-9a-f]*[0-9])[0-9a-f]{8}\b")


@functools.lru_cache(maxsize=1)
def _clauses() -> tuple[str, ...]:
    text = resolve_effective_schemas()["world_spawn"]["description"]
    return tuple(c for c in re.split(r"(?<=[.;:])\s+", text) if c.strip())


def _with(needle: str) -> list[str]:
    return [c for c in _clauses() if needle in c]


class ContaminatedAreaClaimsTest(unittest.TestCase):
    def test_the_not_starting_claim_is_about_dynamic_only(self) -> None:
        negatives = _with("not start")
        self.assertTrue(negatives, "no clause says that a zone does not start")
        for clause in negatives:
            self.assertIn("ContaminatedArea_Dynamic", clause)
            self.assertNotIn("ContaminatedArea_Local", clause)

    def test_the_360_s_lifetime_is_credited_to_the_vanilla_script(self) -> None:
        lifetime = _with("360 s")
        self.assertTrue(lifetime, "no clause gives ContaminatedArea_Local's 360 s lifetime")
        for clause in lifetime:
            self.assertIn("vanilla", clause)
            self.assertIsNone(_RUN_ID.search(clause), clause)

    def test_the_310_s_measurement_names_its_flags(self) -> None:
        measured = _with("310 s")
        self.assertTrue(measured, "no clause gives the 310 s measurement")
        for clause in measured:
            self.assertIn("flags=0", clause)


if __name__ == "__main__":
    unittest.main()
