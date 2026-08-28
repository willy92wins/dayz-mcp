from __future__ import annotations

import unittest

from dayz_mcp import core


def _snapshot(
    *,
    server_age: float | None,
    client_age: float | None,
    server_version: str | None = None,
    client_version: str | None = None,
) -> dict:
    return {
        "peers": {
            "server": {
                "last_poll_age_s": server_age,
                "queue_depth": 0,
                "version": server_version,
                "binding_state": "BOUND",
                "instance_prefix": "ab",
                "bound_last_poll_age_s": server_age,
            },
            "client": {
                "last_poll_age_s": client_age,
                "queue_depth": 0,
                "version": client_version,
                "binding_state": "BOUND",
                "instance_prefix": "cd",
                "bound_last_poll_age_s": client_age,
            },
        },
        "results_pending": 0,
    }


_EXISTING_KEYS = {
    "last_poll_age_s",
    "queue_depth",
    "version",
    "version_state",
    "version_detail",
    "binding_state",
    "instance_prefix",
    "bound_last_poll_age_s",
}


class BridgeStatusGenerationTest(unittest.TestCase):
    def test_never_polled_this_generation_is_distinct_from_legacy_blocked(self) -> None:
        payload = core.build_status(
            _snapshot(server_age=None, client_age=None),
            require_version=True,
            expected_game_version=None,
        )
        client = payload["client_peer"]
        server = payload["server_peer"]

        self.assertFalse(client["observed_this_generation"])
        self.assertFalse(server["observed_this_generation"])
        self.assertEqual(client["version_detail"], "never_polled_this_generation")
        self.assertEqual(server["version_detail"], "never_polled_this_generation")
        # Existing classification is preserved so old consumers keep working.
        self.assertEqual(client["version_state"], "legacy_blocked")
        self.assertEqual(server["version_state"], "legacy_blocked")
        self.assertIsNone(client["last_poll_age_s"])
        self.assertTrue(_EXISTING_KEYS.issubset(client))
        self.assertTrue(_EXISTING_KEYS.issubset(server))

    def test_require_version_false_never_polled_keeps_legacy_state(self) -> None:
        payload = core.build_status(
            _snapshot(server_age=None, client_age=None),
            require_version=False,
            expected_game_version=None,
        )
        self.assertEqual(payload["server_peer"]["version_state"], "legacy")
        self.assertEqual(
            payload["server_peer"]["version_detail"], "never_polled_this_generation"
        )
        self.assertFalse(payload["server_peer"]["observed_this_generation"])

    def test_polled_without_ver_keeps_real_legacy_detail(self) -> None:
        payload = core.build_status(
            _snapshot(server_age=0.2, client_age=None, server_version=None),
            require_version=True,
            expected_game_version=None,
        )
        server = payload["server_peer"]
        client = payload["client_peer"]

        self.assertTrue(server["observed_this_generation"])
        self.assertEqual(server["version_state"], "legacy_blocked")
        self.assertEqual(server["version_detail"], "poll did not include ver=")
        self.assertFalse(client["observed_this_generation"])
        self.assertEqual(client["version_detail"], "never_polled_this_generation")

    def test_observed_zero_age_counts_as_this_generation(self) -> None:
        version = f"{core.EXPECTED_BRIDGE_VERSION}~1.29.0"
        payload = core.build_status(
            _snapshot(
                server_age=0.0,
                client_age=0.0,
                server_version=version,
                client_version=version,
            ),
            require_version=True,
            expected_game_version="1.29.0",
        )
        self.assertTrue(payload["client_peer"]["observed_this_generation"])
        self.assertEqual(payload["client_peer"]["version_state"], "ok")
        self.assertEqual(payload["client_peer"]["version_detail"], "version accepted")
        self.assertNotEqual(
            payload["client_peer"]["version_detail"], "never_polled_this_generation"
        )


if __name__ == "__main__":
    unittest.main()
