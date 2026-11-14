"""The game's GET /poll must fit the URL length DayZ's RestApi sends.

fb-20261103-133622-3e7a and fb-20261103-133703-05ec: on LIVE e18e481 the server
bridge logged ``poll error=7`` and doubled its backoff to 30 s, and the daemon
saw no server poll after the first one (``server_poll_stale``). The client
bridge kept polling on the same daemon.

What decides it is the length of the URL the engine is handed (``m_Url`` plus
the request ``StartPoll`` builds):

* run 7e70320e: after the failures the daemon still held the reading of the
  last server poll that reached it (bridge_status ``server_peer.load``:
  init_attempts 1, polls 105, last_poll_interval_s 0.9, cmds 11,
  cmd_bytes 25672, busy_ms 5268, last_cmds_per_poll 1). With the 43-character
  key and 36-character instance of that run (``keylen=43 instlen=36`` in
  script_2026-11-03_14-32-46.log), that URL is 1024 characters long. The
  next poll left 0.2 s after a poll the daemon had held about 0.9 s, so its
  ``lpims`` has four digits: 1025 characters, and ``poll error=7`` at
  14:35:05.956. Every later poll is longer still: pc, cc, cb and bms only
  grow, and the backoff keeps ``lpims`` at four or five digits. None of them
  reached the daemon (``loopback.py`` stamps ``_last_poll_at`` on every poll
  record_poll handles; server last_poll_age_s passed 600 s while the bridge
  logged a failed poll every 30 s).
* run 5438db5b: the first ``error=7`` came while idle, 920 s after the
  config loaded, about when ``pc`` reached four digits.
* 83b569b9: the same URL was 918 characters and never changed. The promotion
  added survivor_drive and survivor_drive_release to the census (+42, #266),
  the load counters (#286) and ``&wait_ms=800`` (#308).

``ENGINE_URL_MAX`` is the longest URL observed to poll. The engine's own
limit is not documented in the vanilla scripts (restapi.c says nothing about
it), so a URL past it is not allowed even when a longer one once went
through. Inputs are the production ones: the key install-mcp.ps1 mints
(32 random bytes, base64url without padding: 43 characters), a UUID instance,
the game version of those runs, and the longest base IsLoopbackBridgeUrl
accepts (a five-digit port). The bounds hold for those inputs only. A longer
key spends the margin: pinned_keyfile accepts up to 1024 characters
(tools/dayz_mcp/pinned_keyfile.py), and a hand-made key of several hundred
characters can still pass 1024 even with the compact cm= announcement.
This file does not change key handling.
"""

from __future__ import annotations

import re
import unittest
from urllib.parse import parse_qs, urlparse

from dayz_mcp import loopback
from dayz_mcp.bridge_readiness import EXPANSION_AI_VERB_BITS, parse_capability_mask
from tests.poll_harness_helpers import _MISSION, _bridge_harness, _dispatch_branches


ENGINE_URL_MAX = 1024
LIVE_BASE_URL = "http://127.0.0.1:8765/"
LONGEST_BASE_URL = "http://127.0.0.1:65535/"
KEY_LENGTH = 43
INSTANCE = "b9c0cb25-610a-4647-864a-398f50303792"
GAME_VERSION = "1.30.164200.56"
# MCPBridge.LOAD_COUNTER_CAP: every work counter saturates here.
COUNTER_CAP = 2000000000
# LoadCounterQuery clamps a negative lpims to 0; above this the int has
# wrapped, so ten digits is the most it can print.
LPIMS_MAX_S = 2147483.0


def _poll_url(
    name: str,
    base_url: str,
    *,
    polls_before: int,
    interval_s: float,
    init_attempts: int,
    cmds: int,
    cmd_bytes: int,
    busy_ms: int,
    last_cmds_per_poll: int,
) -> str:
    """The URL one real StartPoll hands the engine, run through the translated bridge."""
    ns = _bridge_harness(name)
    wire = ns["_S"]
    ns["game"].version = GAME_VERSION
    ns["m_Key"] = wire.of("k" * KEY_LENGTH)
    ns["m_PeerInstance"] = wire.of(INSTANCE)
    ns["m_InitAttempts"] = init_attempts
    ns["m_Polls"] = polls_before
    ns["m_SincePollS"] = interval_s
    ns["m_Cmds"] = cmds
    ns["m_CmdBytes"] = cmd_bytes
    ns["m_BusyMs"] = busy_ms
    ns["m_LastCmdsPerPoll"] = last_cmds_per_poll
    ns["StartPoll"]()
    if len(ns["polls"]) != 1:
        raise AssertionError(f"{name}: expected one poll GET, got {len(ns['polls'])}")
    return base_url + ns["polls"][0].decode("ascii")


def _saturated_url(name: str) -> str:
    return _poll_url(
        name,
        LONGEST_BASE_URL,
        polls_before=COUNTER_CAP,
        interval_s=LPIMS_MAX_S,
        init_attempts=COUNTER_CAP,
        cmds=COUNTER_CAP,
        cmd_bytes=COUNTER_CAP,
        busy_ms=COUNTER_CAP,
        last_cmds_per_poll=COUNTER_CAP,
    )


class PollUrlEngineLimitTest(unittest.TestCase):
    def test_last_server_poll_the_daemon_saw_in_run_7e70320e_fits(self) -> None:
        url = _poll_url(
            "MCPBridge.c",
            LIVE_BASE_URL,
            polls_before=104,
            interval_s=0.9,
            init_attempts=1,
            cmds=11,
            cmd_bytes=25672,
            busy_ms=5268,
            last_cmds_per_poll=1,
        )
        self.assertIn("&pc=105&lpims=900&cc=11&cb=25672&bms=5268&lcp=1", url)
        self.assertLessEqual(len(url), ENGINE_URL_MAX)

    def test_first_failed_server_poll_of_run_7e70320e_fits(self) -> None:
        # Poll 106 left about 1.1 s after poll 105 (held ~0.9 s, then the
        # 0.2 s pollHz gap) and after id 12 was dispatched. cb and bms are
        # the lower bounds poll 105 announced; they only grow.
        url = _poll_url(
            "MCPBridge.c",
            LIVE_BASE_URL,
            polls_before=105,
            interval_s=1.1,
            init_attempts=1,
            cmds=12,
            cmd_bytes=25672,
            busy_ms=5268,
            last_cmds_per_poll=1,
        )
        self.assertIn("&pc=106&lpims=1100&cc=12", url)
        self.assertLessEqual(len(url), ENGINE_URL_MAX, f"server poll URL is {len(url)} characters")

    def test_every_counter_value_fits_on_both_bridges(self) -> None:
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            with self.subTest(bridge=name):
                url = _saturated_url(name)
                self.assertIn("&lpims=2147483000&", url)
                self.assertLessEqual(len(url), ENGINE_URL_MAX, f"{name} poll URL is {len(url)} characters")

    def test_saturated_server_poll_with_the_adapter_fits(self) -> None:
        # H3 (#321): the adapter adds no caps names; its saturated poll is
        # the base poll (v13 already carries ,lease_close) plus &xai=<hash>.<mask>.
        adapter = (_MISSION.parents[2] / "addon_expansion_ai" / "scripts" / "5_Mission"
                   / "MCPExpansionAI.c").read_text(encoding="utf-8")
        caps = re.search(r'super\.ServerCapabilities\(\) \+ "([^"]*)"', adapter)
        hash_ = re.search(r'EXPANSION_AI_ARG_CONTRACT_HASH = "([0-9a-f]{16})"', adapter)
        verbs = re.search(r'EXPANSION_AI_VERBS = "([0-9]+)"', adapter)
        self.assertIsNotNone(hash_)
        widest = len(str((1 << len(EXPANSION_AI_VERB_BITS)) - 1))
        announced = len(verbs.group(1)) if verbs else 0
        extra = (len(caps.group(1)) if caps else 0) + len("&xai=") + len(hash_.group(1)) \
            + 1 + max(widest, announced)
        self.assertLessEqual(len(_saturated_url("MCPBridge.c")) + extra,
                             ENGINE_URL_MAX)

    def test_full_256_bit_mask_stays_within_the_limit(self) -> None:
        # Every verb the tables can ever hold: a 256-bit mask is 64 hex
        # digits. Replacing the announced cm value in the saturated poll
        # gives, from the base measurements (server 996 / client 1017 with
        # the old caps list): server 345, client 336, and server plus the
        # widest adapter xai 369 characters -- all far below 1024 for the
        # production inputs named in the module docstring (43-character
        # minted key, UUID instance, five-digit port).
        full = "cm=1." + "f" * 64
        for name in ("MCPBridge.c", "MCPClientBridge.c"):
            with self.subTest(bridge=name):
                url = re.sub(r"cm=[^&]*", full, _saturated_url(name))
                self.assertLessEqual(
                    len(url), ENGINE_URL_MAX, f"{name} poll URL is {len(url)} characters"
                )
        adapter = (_MISSION.parents[2] / "addon_expansion_ai" / "scripts" / "5_Mission"
                   / "MCPExpansionAI.c").read_text(encoding="utf-8")
        hash_ = re.search(r'EXPANSION_AI_ARG_CONTRACT_HASH = "([0-9a-f]{16})"', adapter)
        self.assertIsNotNone(hash_)
        widest = len(str((1 << len(EXPANSION_AI_VERB_BITS)) - 1))
        extra = len("&xai=") + len(hash_.group(1)) + 1 + widest
        url = re.sub(r"cm=[^&]*", full, _saturated_url("MCPBridge.c"))
        self.assertLessEqual(len(url) + extra, ENGINE_URL_MAX)

    def test_daemon_reads_the_saturated_server_poll(self) -> None:
        # The reads Handler.do_GET and _handle_poll make of the query.
        url = _saturated_url("MCPBridge.c")
        qs = parse_qs(urlparse(url).query, keep_blank_values=True)
        names, reason = parse_capability_mask("server", qs.get("cm", [""])[0])
        self.assertEqual(reason, "ok")
        source = (_MISSION / "MCPBridge.c").read_text(encoding="utf-8")
        self.assertEqual(set(names), set(_dispatch_branches(source)))
        load = loopback._load_from_poll_query(qs)
        self.assertIsNotNone(load)
        self.assertEqual(load["polls"], COUNTER_CAP)
        self.assertEqual(load["last_cmds_per_poll"], COUNTER_CAP)
        self.assertEqual(qs["inst"], [INSTANCE])
        self.assertEqual(loopback._parse_poll_wait_s(qs), loopback.POLL_LONG_POLL_MAX_S)


if __name__ == "__main__":
    unittest.main()
