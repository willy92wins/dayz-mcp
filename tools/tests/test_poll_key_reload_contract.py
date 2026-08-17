from __future__ import annotations

import unittest
from pathlib import Path

from tests._addon_paths import addon_root


MOD_SCRIPTS = addon_root() / "scripts"

BRIDGES = (
    MOD_SCRIPTS / "5_Mission" / "MCPBridge.c",
    MOD_SCRIPTS / "5_Mission" / "MCPClientBridge.c",
)


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class PollKeyReloadContractTest(unittest.TestCase):
    """BUG-071: the API key was read once at configure time and never again.

    After a key rotation -- or a port reclaim handing the socket to a
    differently keyed holder -- the bridge kept polling with a dead credential
    until the mission restarted, and the only symptom was the poll backoff
    climbing to its 30 s cap. Both bridges carry the same loader, so both must
    carry the same recovery; a fix on one side only would leave the other
    silently stuck.
    """

    def test_both_bridges_reload_the_key_after_persistent_poll_failure(self) -> None:
        for bridge in BRIDGES:
            with self.subTest(bridge=bridge.name):
                source = bridge.read_text(encoding="utf-8")
                fail = _method_body(source, "protected void OnPollFail(string reason)")
                self.assertIn("ReloadKeyAfterFailure();", fail)
                # Gated, not unconditional: a reload on every failure would put a
                # file read on the path of an ordinary unreachable server.
                self.assertIn("KEY_RELOAD_BACKOFF_S", fail)

                reload_body = _method_body(source, "protected void ReloadKeyAfterFailure()")
                self.assertIn("m_Key = cfg.key;", reload_body)
                # Adopting a key while still backing off would delay recovery by up
                # to the 30 s cap, which is the symptom this bug is about.
                self.assertIn("m_Backoff = 0.0;", reload_body)
                # An unchanged key must return early, or an unreachable server would
                # have its backoff reset on every failure and retry hot forever.
                self.assertIn("if (cfg.key == m_Key)", reload_body)
                self.assertLess(
                    reload_body.index("if (cfg.key == m_Key)"),
                    reload_body.index("m_Backoff = 0.0;"),
                )

    def test_reload_reads_the_same_config_locations_as_the_initial_load(self) -> None:
        # A reload that looked somewhere else would "work" in tests and silently
        # never see the rotated key on a real profile.
        for bridge in BRIDGES:
            with self.subTest(bridge=bridge.name):
                reload_body = _method_body(
                    bridge.read_text(encoding="utf-8"), "protected void ReloadKeyAfterFailure()"
                )
                self.assertIn('"$profile:dayz_mcp.json"', reload_body)
                self.assertIn('"$mission:dayz_mcp.json"', reload_body)


if __name__ == "__main__":
    unittest.main()
