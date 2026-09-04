"""The run modes dayz_test_tool gates on are read from the M12 authority.

Ficha fb-20260830-010517-9d46: the public/internal split is deliberate, the six
mirrored copies of the name set are the defect. Adding a mode had to be done in
six places and forgetting one gave an error message that lied instead of a
crash. These checks are the oracle for the derivation: substituting
``dayz_test_modes.MODE_RECORDS`` must move the tool, and a name that is not a
record at all -- ``dayz_test_stop`` projects the label "stop" as its public_mode
(dayz_test_tool.py:915) -- must project False instead of raising.
"""

from __future__ import annotations

import ntpath
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from dayz_mcp import dayz_test_modes, dayz_test_request, dayz_test_tool
from dayz_mcp import steam_preflight

from tests.test_dayz_test_tool import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)


class _Reached(RuntimeError):
    """Raised by the launcher stand-in: proof the mode gate let the call past."""


def _authority_with(target: str, **changes: object) -> tuple[dayz_test_modes.ModeRecord, ...]:
    """The live authority with one record replaced. Nothing else moves.

    The parameter is `target` and not `name` because `name` is itself one
    of the fields a caller replaces.
    """

    return tuple(
        replace(record, **changes) if record.name == target else record
        for record in dayz_test_modes.MODE_RECORDS
    )


def _renamed_authority() -> tuple[dayz_test_modes.ModeRecord, ...]:
    """Two public modes with names and an order that exist nowhere in the tree."""

    base = {record.name: record for record in dayz_test_modes.MODE_RECORDS}
    return (
        replace(base["server"], name="zulu"),
        replace(base["all"], name="alpha"),
    )


class ModeAuthorityDerivationTest(unittest.TestCase):
    def test_mode_expected_error_is_built_from_the_authority(self) -> None:
        """The rejection names the public modes in the order the records declare.

        The literal below is the authority's order (MODE_RECORDS: all, server,
        client), not the mirrored "server|all|client" the tool used to carry.
        """

        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="not-a-mode",
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected all|server|client",
        )

        with patch.object(dayz_test_modes, "MODE_RECORDS", _renamed_authority()):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as substituted:
                dayz_test_tool.build_run_request(
                    _sealed(_policy()),
                    project="ExampleMod",
                    mode="not-a-mode",
                    extra_mods=["@DayZ_MCP"],
                )
        self.assertEqual(
            substituted.exception.code,
            "bad_dayz_test_request:mode expected zulu|alpha",
        )

    def test_accepted_membership_follows_request_visibility(self) -> None:
        """build_run_request accepts exactly the request-visible names.

        Retiring `offline` from the request view must be refused by the tool's
        own gate, named, and not one layer later as a bare bad_dayz_test_request
        from the parser.
        """

        policy = _policy()
        raw, _selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            extra_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertEqual(parsed.payload["mode"], "offline")

        with patch.object(
            dayz_test_modes,
            "MODE_RECORDS",
            _authority_with("offline", request_visible=False),
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                dayz_test_tool.build_run_request(
                    _sealed(policy),
                    project="ExampleMod",
                    mode="offline",
                    extra_mods=["@DayZ_MCP"],
                )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected all|server|client",
        )

    def test_starts_client_projection_follows_the_authority(self) -> None:
        """Every record answers its own starts_client; substitution moves it."""

        for record in dayz_test_modes.MODE_RECORDS:
            with self.subTest(mode=record.name):
                self.assertIs(
                    dayz_test_tool._mode_starts_client(record.name),
                    record.starts_client,
                )

        with patch.object(
            dayz_test_modes, "MODE_RECORDS", _authority_with("server", starts_client=True)
        ):
            self.assertIs(dayz_test_tool._mode_starts_client("server"), True)

    def test_a_label_outside_the_authority_projects_false_without_raising(self) -> None:
        """"stop" is dayz_test_stop's public_mode label, not a mode record.

        Resolving it raises ModeAuthorityError, a ValueError whose message is
        prose: it would reach the caller as a mute dayz_test_failed:ValueError
        (see tests/test_dayz_test_value_error_codes.py).
        """

        self.assertNotIn("stop", [record.name for record in dayz_test_modes.MODE_RECORDS])
        self.assertIs(dayz_test_tool._mode_starts_client("stop"), False)
        with self.assertRaises(dayz_test_modes.ModeAuthorityError):
            dayz_test_modes.resolve_mode("stop")


class ModeAuthorityExecutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.steam_calls = 0

        def steam() -> steam_preflight.SteamSessionResult:
            self.steam_calls += 1
            return steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            )

        patcher = patch.object(dayz_test_tool, "evaluate_steam_session", side_effect=steam)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _run(
        self, runtime: _Runtime, policy: dayz_test_request.RequestProjectPolicy, mode: str
    ) -> dict[str, object]:
        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime, project="ExampleMod", mode=mode, extra_mods=["@DayZ_MCP"]
            )

    async def test_public_membership_follows_a_substituted_authority(self) -> None:
        """A view that publishes `offline` stops dayz_test_run rejecting it."""

        runtime = _Runtime()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", side_effect=_Reached("gate passed")
        ) as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="offline"
                )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected all|server|client",
        )
        opened.assert_not_called()

        runtime = _Runtime()
        with patch.object(
            dayz_test_modes, "MODE_RECORDS", _authority_with("offline", public=True)
        ):
            with patch.object(
                dayz_test_tool, "open_approved_launcher", side_effect=_Reached("gate passed")
            ) as opened:
                with self.assertRaises(_Reached):
                    await dayz_test_tool.execute_dayz_test_run(
                        runtime, project="ExampleMod", mode="offline"
                    )
        opened.assert_called_once()

    async def test_an_unknown_mode_is_rejected_before_the_launcher_is_opened(self) -> None:
        """Fail closed and fail early: nothing is opened, nothing is written.

        The second half is the negative control: the same fixture with a valid
        mode DOES reach open_approved_launcher, so "not called" above measures
        the mode gate and not a call that never gets that far anyway.
        """

        runtime = _Runtime()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", side_effect=_Reached("gate passed")
        ) as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="not-a-mode"
                )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected all|server|client",
        )
        opened.assert_not_called()

        runtime = _Runtime()
        with patch.object(
            dayz_test_tool, "open_approved_launcher", side_effect=_Reached("gate passed")
        ) as opened:
            with self.assertRaises(_Reached):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="server"
                )
        opened.assert_called_once()

    async def test_a_broken_authority_is_a_named_bad_request(self) -> None:
        """An unreadable record view fails closed, the way the parser does.

        dayz_test_request._request_mode_view (dayz_test_request.py:67-75) maps
        ModeAuthorityError to the invalid-request token; letting it escape here
        would reach the caller as dayz_test_failed:ValueError instead.
        """

        runtime = _Runtime()
        with patch.object(dayz_test_modes, "MODE_RECORDS", ()):
            with patch.object(
                dayz_test_tool, "open_approved_launcher", side_effect=_Reached("gate passed")
            ) as opened:
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    await dayz_test_tool.execute_dayz_test_run(
                        runtime, project="ExampleMod", mode="all"
                    )
        self.assertEqual(caught.exception.code, "bad_dayz_test_request")
        opened.assert_not_called()

    async def test_readiness_and_steam_branches_read_starts_client(self) -> None:
        """Both per-mode branches move with the record, and only with it.

        dayz_test_tool.py:649 (bridge readiness) and :729 (Steam preflight) both
        asked `in {"client", "all"}`. With `server` declared as a client-starting
        mode they must both fire for mode="server"; with the live authority
        neither does.
        """

        policy = _policy()
        runtime = _Runtime()
        result = await self._run(runtime, policy, "server")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.bridge_calls, 0)
        self.assertEqual(self.steam_calls, 0)

        self.steam_calls = 0
        runtime = _Runtime()
        with patch.object(
            dayz_test_modes, "MODE_RECORDS", _authority_with("server", starts_client=True)
        ):
            result = await self._run(runtime, policy, "server")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.bridge_calls, 1)
        self.assertEqual(self.steam_calls, 1)

    async def test_stop_reaches_its_terminal_without_consulting_the_authority(self) -> None:
        """The happy path of dayz_test_stop carries public_mode="stop".

        Guard, not oracle: green before the change. Its positive control is the
        Vaciado patch, which resolved "stop" through the authority and turned
        this into ModeAuthorityError: unknown mode.
        """

        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        run = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        runtime = _Runtime({"runs": [run]})

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "stop")
        self.assertEqual(runtime.bridge_calls, 0)



class ModeContractM19Test(unittest.IsolatedAsyncioTestCase):
    """The two per-mode policies M19 still owed the authority.

    Both used literal sets that disagreed with the records: the post-ack check
    named `offline` (a label that never arrives here) and omitted `client`, and
    the artifact roots answered `_client` for every name the table did not know.
    """

    def setUp(self) -> None:
        patcher = patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _runtime_with_dead_client(*, extensible: bool = False) -> _Runtime:
        # Same construction as the sealed test_run_fails_when_client_pid_is_
        # already_dead: a live server pid and a pid nothing can own.
        # `extensible` adds what require_extension_run demands, because a
        # client run reattaches to a live one and carries its run_id.
        run: dict[str, object] = {
            "run_id": RUN_ID,
            "processes": [
                {"role": "server", "pid": os.getpid()},
                {"role": "client", "pid": 999_999_999},
            ],
        }
        if extensible:
            run["state"] = "RUNNING_IDLE"
            run["mod"] = "@ExampleMod"
        return _Runtime(lifecycle={"runs": [run]})

    async def _run(
        self, runtime: _Runtime, mode: str, run_id: str | None = None
    ) -> dict[str, object]:
        policy = _policy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode=mode,
                run_id=run_id,
                extra_mods=["@DayZ_MCP"],
            )

    async def test_a_dead_client_fails_every_mode_that_started_one(self) -> None:
        """client is the mode the literal set omitted; server is the control.

        M19 owes: `all|client` with a dead client fail client_dead_after_ack and
        `server|preflight|stop` do not.
        """

        result = await self._run(
            self._runtime_with_dead_client(extensible=True), "client", RUN_ID
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertIs(result["client_alive"], False)

        # Positive control, green before and after: the line that must not break.
        result = await self._run(self._runtime_with_dead_client(), "all")
        self.assertEqual(result["error_code"], "client_dead_after_ack")

        # Negative control: server starts no client, so a dead client pid is not
        # its failure. Without it the assertion above would also pass a rule that
        # simply failed on any dead pid.
        result = await self._run(self._runtime_with_dead_client(), "server")
        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error_code"])

    async def test_the_dead_client_check_follows_a_substituted_authority(self) -> None:
        """Renaming a record must move the check with it, not leave it behind."""

        with patch.object(
            dayz_test_modes, "MODE_RECORDS", _authority_with("all", name="combo")
        ):
            result = await self._run(self._runtime_with_dead_client(), "combo")
        self.assertEqual(result["error_code"], "client_dead_after_ack")

        with patch.object(
            dayz_test_modes,
            "MODE_RECORDS",
            _authority_with("client", starts_client=False),
        ):
            result = await self._run(
                self._runtime_with_dead_client(extensible=True), "client", RUN_ID
            )
        self.assertEqual(result["status"], "succeeded")

    async def test_stop_never_accuses_a_dead_client(self) -> None:
        """The label "stop" is not a record: it starts no client to lose."""

        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        runtime = _Runtime(
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING_IDLE",
                        "mod": "@StorageMod",
                        "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
                        "processes": [{"role": "client", "pid": 999_999_999}],
                    }
                ]
            }
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 0

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error_code"])


class ArtifactRootsTest(unittest.TestCase):
    """The profile roots are the record's artifact_roots, resolved exactly."""

    def test_every_record_reports_its_own_roots(self) -> None:
        policy = _policy()
        for record in dayz_test_modes.MODE_RECORDS:
            with self.subTest(mode=record.name):
                self.assertEqual(
                    dayz_test_tool._artifact_paths(policy, record.name),
                    [
                        ntpath.join(policy.dev_root, root, "profiles")
                        for root in record.artifact_roots
                    ],
                )

    def test_the_roots_follow_a_substituted_authority(self) -> None:
        """A renamed record keeps its roots; a moved root moves the answer."""

        policy = _policy()
        with patch.object(
            dayz_test_modes, "MODE_RECORDS", _authority_with("server", name="zulu")
        ):
            self.assertEqual(
                dayz_test_tool._artifact_paths(policy, "zulu"),
                [r"P:\ExampleMod_Suite\_server\profiles"],
            )
        with patch.object(
            dayz_test_modes,
            "MODE_RECORDS",
            _authority_with("server", artifact_roots=("_moved",)),
        ):
            self.assertEqual(
                dayz_test_tool._artifact_paths(policy, "server"),
                [r"P:\ExampleMod_Suite\_moved\profiles"],
            )

    def test_a_name_outside_the_authority_is_named_not_projected_to_client(self) -> None:
        """The old else branch answered _client for anything it did not know."""

        policy = _policy()
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool._artifact_paths(policy, "not-a-mode")
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected all|server|client",
        )
        self.assertNotIn("_client", caught.exception.code)

    def test_stop_still_unions_the_roots_of_the_full_run(self) -> None:
        """_stop_artifacts asks for the roots of "all"; both must stay reachable."""

        policy = _policy()
        for profiles in (
            r"P:\ExampleMod_Suite\_server\profiles",
            r"P:\ExampleMod_Suite\_client\profiles",
        ):
            with self.subTest(profiles=profiles):
                self.assertEqual(
                    dayz_test_tool._stop_artifacts(policy, {"profiles": profiles}),
                    [profiles],
                )

if __name__ == "__main__":
    unittest.main()
