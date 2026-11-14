from __future__ import annotations

import json
import asyncio
import inspect
import os
import re
import types
import unittest
from unittest.mock import AsyncMock, patch

from dayz_mcp import dayz_test_modes, dayz_test_request, dayz_test_worker
from dayz_mcp import native_launcher_transaction
from dayz_mcp import dayz_test_tool
from dayz_mcp import server
from dayz_mcp import steam_preflight
import mcp_capture
from dayz_mcp.control_client import ControlClientError
from tests._source_snapshot import source_of
from tests.dayz_test_tool_helpers import (
    RUN_ID,
    _Bundle,
    _Opened,
    _Runtime,
    _policy,
    _sealed,
    _terminal,
)


def _worker_mod_arg(
    payload: dict[str, object],
    policy: dayz_test_request.RequestProjectPolicy,
) -> str:
    """The -mod= string the sealed worker builds. No DayZ process."""
    runtime = types.SimpleNamespace(mod=policy.mod, mods_root=r"P:\Mods")
    return dayz_test_worker._mods(payload, runtime)


def _bridge_mod_entries(mod_arg: str) -> list[str]:
    return [
        item
        for item in mod_arg.split(";")
        if item.casefold() == r"p:\mods\@dayz_mcp"
    ]


def _preserved_payload(
    policy: dayz_test_request.RequestProjectPolicy,
    *,
    base_mods: list[str] | None = None,
) -> dict[str, object]:
    """Canonical payload with no extra_mods default applied."""
    document: dict[str, object] = {
        "dev_root": policy.dev_root,
        "mission": "chernarus",
        "mod": policy.mod,
        "mode": "offline",
        "version": 1,
    }
    if base_mods is not None:
        document["base_mods"] = base_mods
    raw = json.dumps(
        document, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return dayz_test_request.parse_dayz_test_request(
        raw, policies=(policy,)
    ).payload


def _list_projects_path(payload: bytes | BaseException) -> type:
    """Path stand-in so list_project_names never leaves the fixture."""

    class _PolicyPath:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def resolve(self) -> _PolicyPath:
            return self

        @property
        def parents(self) -> tuple[_PolicyPath, _PolicyPath]:
            return (self, self)

        def __truediv__(self, _other: object) -> _PolicyPath:
            return self

        def read_bytes(self) -> bytes:
            if isinstance(payload, BaseException):
                raise payload
            return payload

    return _PolicyPath


class DayzTestToolRequestTest(unittest.TestCase):
    def test_server_only_bridge_suppresses_shared_default(self) -> None:
        for project in ("ExampleMod", "DayZ_MCP"):
            policy = _policy(mod=project)
            raw, _ = dayz_test_tool.build_run_request(
                _sealed(policy), project=project, mode="all",
                server_only_mods=["@DayZ_MCP", "@Audit"],
            )
            payload = json.loads(raw)
            self.assertEqual(payload["version"], 3)
            self.assertEqual(payload["extra_mods"], [])
            self.assertEqual(payload["server_only_mods"], ["@DayZ_MCP", "@Audit"])
            self.assertEqual(dayz_test_tool._bridge_default_report(None, raw), [])

    def test_empty_server_only_mods_keeps_default_payload(self) -> None:
        policy = _policy()
        default, _ = dayz_test_tool.build_run_request(_sealed(policy), project=policy.mod, mode="all")
        empty, _ = dayz_test_tool.build_run_request(
            _sealed(policy), project=policy.mod, mode="all", server_only_mods=[],
        )
        self.assertEqual(empty, default)

    def test_server_only_duplicate_and_explicit_conflicts_are_named(self) -> None:
        policy = _policy()
        cases = (
            ({"server_only_mods": ["@Audit", "@audit"]}, "mod_list_invalid"),
            ({"server_only_mods": ["@Audit"], "extra_mods": ["@audit"]}, "server_only_mods_conflict"),
            ({"server_only_mods": ["@CF"]}, "server_only_mods_conflict"),
            ({"server_only_mods": ["@Audit"], "server_mods": ["@Audit"]}, "server_only_mods_conflict"),
            ({"server_only_mods": ["@DayZ_MCP"], "extra_mods": ["@DayZ_MCP"]}, "server_only_mods_conflict"),
        )
        for options, reason in cases:
            with self.subTest(options=options), self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "bad_dayz_test_request:" + reason
            ):
                dayz_test_tool.build_run_request(_sealed(policy), project=policy.mod, mode="all", **options)

    def test_build_run_request_uses_sealed_project_and_policy_defaults(self) -> None:
        policy = _policy()

        raw, selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            mission="chernarus",
            extra_mods=["@DayZ_MCP"],
        )

        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["dev_root"], policy.dev_root)
        self.assertEqual(parsed.payload["source"], policy.default_source)
        self.assertEqual(parsed.payload["base_mods"], list(policy.default_base_mods))
        self.assertEqual(parsed.payload["mode"], "offline")

    def test_build_run_request_defaults_bridge_into_extra_mods(self) -> None:
        """fb-20260925-233943-9ccc: a non-bridge project gets @DayZ_MCP appended."""
        policy = _policy()
        sealed = _sealed(policy)

        raw, selected = dayz_test_tool.build_run_request(
            sealed,
            project="ExampleMod",
            mode="offline",
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
        self.assertEqual(parsed.payload["base_mods"], list(policy.default_base_mods))
        self.assertEqual(parsed.payload["server_mods"], [])
        self.assertEqual(
            dayz_test_tool._bridge_default_report(None, raw), ["@DayZ_MCP"]
        )

        raw, _selected = dayz_test_tool.build_run_request(
            sealed,
            project="ExampleMod",
            mode="offline",
            extra_mods=["@Other"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertEqual(parsed.payload["extra_mods"], ["@Other", "@DayZ_MCP"])
        self.assertEqual(
            dayz_test_tool._bridge_default_report(["@Other"], raw), ["@DayZ_MCP"]
        )

        # server_mods is not on -mod=. It still does not satisfy the gate, so
        # the default is written onto extra_mods and that list stays as passed.
        raw, _selected = dayz_test_tool.build_run_request(
            sealed,
            project="ExampleMod",
            mode="offline",
            server_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
        self.assertEqual(parsed.payload["server_mods"], ["@DayZ_MCP"])
        mod_arg = _worker_mod_arg(parsed.payload, policy)
        self.assertEqual(_bridge_mod_entries(mod_arg), [r"P:\Mods\@DayZ_MCP"])

    def test_defaulted_bridge_appears_once_on_the_worker_mod_arg(self) -> None:
        """origin/main raises bridge_mod_missing and never builds this -mod=."""
        policy = _policy()
        raw, _selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        mod_arg = _worker_mod_arg(parsed.payload, policy)
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
        self.assertEqual(_bridge_mod_entries(mod_arg), [r"P:\Mods\@DayZ_MCP"])
        folded = [item.casefold() for item in mod_arg.split(";") if item]
        self.assertEqual(len(folded), len(set(folded)))

    def test_bridge_in_effective_base_mods_is_not_duplicated_on_mod_arg(self) -> None:
        """Caller list and policy defaults. Round 1 appended and doubled -mod=.

        The preserved document (no copy into extra_mods) lists the folder
        once. build_run_request keeps that document and the bridge check,
        so the call is bridge_mod_missing instead of a doubled -mod=.
        """
        caller_policy = _policy()
        cases: tuple[tuple[str, dayz_test_request.RequestProjectPolicy, dict[str, object]], ...] = (
            ("caller_base", caller_policy, {"base_mods": ["@DayZ_MCP"]}),
            ("caller_base_case", caller_policy, {"base_mods": ["@dayz_mcp"]}),
            (
                "caller_base_absolute",
                caller_policy,
                {"base_mods": [r"P:\Mods\@DayZ_MCP"]},
            ),
            (
                "policy_base",
                _policy(default_base_mods=("@CF", "@DayZ_MCP")),
                {},
            ),
            (
                "policy_base_case",
                _policy(default_base_mods=("@CF", "@dayz_mcp")),
                {},
            ),
        )
        for label, policy, kwargs in cases:
            with self.subTest(label=label):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    dayz_test_tool.build_run_request(
                        _sealed(policy),
                        project="ExampleMod",
                        mode="offline",
                        **kwargs,
                    )
                self.assertEqual(
                    caught.exception.code,
                    dayz_test_tool._BRIDGE_MOD_MISSING_IN_BASE_MODS,
                )
                self.assertTrue(
                    caught.exception.code.startswith("bridge_mod_missing:")
                )
                self.assertIn("already in base_mods", caught.exception.code)
                self.assertIn("does not count", caught.exception.code)
                self.assertIn("extra_mods=['@DayZ_MCP']", caught.exception.code)
                self.assertNotIn("sealed request", caught.exception.code)
                self.assertNotEqual(
                    caught.exception.code, dayz_test_tool._BRIDGE_MOD_MISSING
                )
                preserved = _preserved_payload(
                    policy,
                    base_mods=kwargs.get("base_mods"),  # type: ignore[arg-type]
                )
                mod_arg = _worker_mod_arg(preserved, policy)
                self.assertEqual(len(_bridge_mod_entries(mod_arg)), 1)
                folded = [item.casefold() for item in mod_arg.split(";") if item]
                self.assertEqual(len(folded), len(set(folded)))

    def test_moving_the_bridge_out_of_base_mods_lists_it_once(self) -> None:
        """The error's recipe, followed exactly, puts the bridge on -mod= once.

        Adding extra_mods=['@DayZ_MCP'] while the folder stays in the
        effective base_mods lists it twice: the worker concatenates both
        lists. Policy defaults stay in force when the field is omitted, so
        that case has to pass an explicit base_mods without the bridge.
        """
        caller_policy = _policy()
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(caller_policy),
                project="ExampleMod",
                mode="offline",
                base_mods=["@DayZ_MCP"],
            )
        code = caught.exception.code
        self.assertIn(
            "Move '@DayZ_MCP' from the effective base_mods to extra_mods",
            code,
        )
        self.assertIn("explicit base_mods without the bridge", code)
        self.assertNotIn("Add extra_mods=['@DayZ_MCP']", code)

        # Same inputs as the error cases above this test. None means the
        # effective list is the policy default, so the call must pass it
        # explicitly with the bridge removed.
        sources: tuple[
            tuple[str, dayz_test_request.RequestProjectPolicy, list[str] | None],
            ...,
        ] = (
            ("caller_base", caller_policy, ["@DayZ_MCP"]),
            ("caller_base_case", caller_policy, ["@dayz_mcp"]),
            (
                "caller_base_absolute",
                caller_policy,
                [r"P:\Mods\@DayZ_MCP"],
            ),
            (
                "policy_base",
                _policy(default_base_mods=("@CF", "@DayZ_MCP")),
                None,
            ),
            (
                "policy_base_case",
                _policy(default_base_mods=("@CF", "@dayz_mcp")),
                None,
            ),
        )
        for label, policy, caller_base in sources:
            with self.subTest(label=label):
                effective = (
                    list(policy.default_base_mods)
                    if caller_base is None
                    else caller_base
                )
                moved = [
                    item
                    for item in effective
                    if not dayz_test_tool._names_bridge(item)
                ]
                raw, _selected = dayz_test_tool.build_run_request(
                    _sealed(policy),
                    project="ExampleMod",
                    mode="offline",
                    base_mods=moved,
                    extra_mods=["@DayZ_MCP"],
                )
                parsed = dayz_test_request.parse_dayz_test_request(
                    raw, policies=(policy,)
                )
                self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
                self.assertFalse(
                    any(
                        dayz_test_tool._names_bridge(item)
                        for item in parsed.payload["base_mods"]
                    )
                )
                mod_arg = _worker_mod_arg(parsed.payload, policy)
                self.assertEqual(
                    _bridge_mod_entries(mod_arg), [r"P:\Mods\@DayZ_MCP"]
                )

    def test_cleared_base_mods_still_defaults_the_bridge_once(self) -> None:
        """An empty caller list, or no_base_mods, is not the policy default.

        origin/main raises bridge_mod_missing. The -mod= lists the folder once.
        """
        policy = _policy(default_base_mods=("@CF", "@DayZ_MCP"))
        for label, kwargs in (
            ("caller_empty", {"base_mods": []}),
            ("no_base_mods", {"no_base_mods": True}),
        ):
            with self.subTest(label=label):
                raw, _selected = dayz_test_tool.build_run_request(
                    _sealed(policy),
                    project="ExampleMod",
                    mode="offline",
                    **kwargs,
                )
                parsed = dayz_test_request.parse_dayz_test_request(
                    raw, policies=(policy,)
                )
                self.assertEqual(parsed.payload["base_mods"], [])
                self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
                mod_arg = _worker_mod_arg(parsed.payload, policy)
                self.assertEqual(
                    _bridge_mod_entries(mod_arg), [r"P:\Mods\@DayZ_MCP"]
                )

    def test_explicit_bridge_extra_mod_is_not_duplicated(self) -> None:
        """Regression control: origin/main already kept an explicit bridge entry."""
        policy = _policy()
        sealed = _sealed(policy)
        absolute = r"P:\Mods\@DayZ_MCP"
        cases = (
            ["@DayZ_MCP"],
            ["@dayz_mcp"],
            ["@DAYZ_MCP"],
            ["DayZ_MCP"],
            ["@Other", "@DayZ_MCP"],
            [absolute],
        )
        for explicit in cases:
            with self.subTest(explicit=explicit):
                raw, _selected = dayz_test_tool.build_run_request(
                    sealed,
                    project="ExampleMod",
                    mode="offline",
                    extra_mods=explicit,
                )
                parsed = dayz_test_request.parse_dayz_test_request(
                    raw, policies=(policy,)
                )
                self.assertEqual(parsed.payload["extra_mods"], explicit)
                self.assertEqual(
                    dayz_test_tool._bridge_default_report(explicit, raw), []
                )

    def test_dayz_mcp_project_does_not_gain_an_extra_mod(self) -> None:
        """Regression control: origin/main already left this project's extra_mods empty."""
        bridge_policy = _policy(
            mod="DayZ_MCP",
            dev_root=r"P:\DayZ_MCP_dev",
            default_source=r"P:\DayZ_MCP",
        )
        raw, selected = dayz_test_tool.build_run_request(
            _sealed(bridge_policy),
            project="DayZ_MCP",
            mode="offline",
        )
        parsed = dayz_test_request.parse_dayz_test_request(
            raw, policies=(bridge_policy,)
        )
        self.assertIs(selected, bridge_policy)
        self.assertEqual(parsed.payload["mod"], "DayZ_MCP")
        self.assertEqual(parsed.payload["extra_mods"], [])
        self.assertEqual(dayz_test_tool._bridge_default_report(None, raw), [])

    def test_kill_request_does_not_gain_the_bridge_default(self) -> None:
        policy = _policy()
        raw, _selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            run_id=RUN_ID,
            kill=True,
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertEqual(parsed.payload["extra_mods"], [])
        self.assertIs(parsed.payload["kill"], True)

    def test_sealed_list_limit_keeps_bridge_mod_missing(self) -> None:
        """65 entries is mod_list_invalid; the default must not produce that."""
        policy = _policy()
        full = [f"@Mod{index:02d}" for index in range(64)]
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(policy),
                project="ExampleMod",
                mode="offline",
                extra_mods=full,
            )
        self.assertEqual(caught.exception.code, dayz_test_tool._BRIDGE_MOD_MISSING)
        self.assertTrue(caught.exception.code.startswith("bridge_mod_missing:"))
        self.assertIn("folder name", caught.exception.code)
        self.assertIn("extra_mods=['@DayZ_MCP']", caught.exception.code)
        self.assertIn("base_mods and server_mods do not count", caught.exception.code)
        self.assertIn("sealed request", caught.exception.code)
        self.assertIn("would reject that appended entry", caught.exception.code)
        self.assertNotIn("already in base_mods", caught.exception.code)

    def test_bad_extra_mod_still_fails_before_the_bridge_default(self) -> None:
        """Regression control: origin/main already rejected this form as bad_mod."""
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="offline",
                extra_mods=[r"mods\@DayZ_MCP"],
            )
        self.assertEqual(caught.exception.code, dayz_test_tool._BAD_MOD)
        self.assertTrue(caught.exception.code.startswith("bad_mod:"))

    def test_build_run_request_rejects_unknown_project_and_public_paths(self) -> None:
        sealed = _sealed(_policy())
        invalid = (
            ({"project": "Unknown", "mode": "offline"}, "bad_project"),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "mission": r"P:\missions\custom.ChernarusPlus",
                },
                "bad_dayz_test_request",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "extra_mods": [r"C:\Users\Public\@Outside"],
                },
                "bad_mod",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "server_mods": [r"folder\@Server"],
                },
                "bad_mod",
            ),
            (
                {
                    "project": "ExampleMod",
                    "mode": "offline",
                    "base_mods": [".."],
                },
                "bad_mod",
            ),
        )
        for arguments, code in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, code) as caught:
                    dayz_test_tool.build_run_request(sealed, **arguments)
                if code == "bad_mod":
                    self.assertEqual(caught.exception.code, dayz_test_tool._BAD_MOD)
                    self.assertTrue(caught.exception.code.startswith("bad_mod:"))
                    self.assertIn("folder name", caught.exception.code)
                    self.assertIn("@DayZ_MCP", caught.exception.code)

    def test_build_run_request_names_accepted_form_for_relative_mod_path(self) -> None:
        """fb-20260909-213257-49a9: a relative path is not a folder name."""
        sealed = _sealed(_policy())
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                sealed,
                project="ExampleMod",
                mode="offline",
                extra_mods=[r"mods\@DayZ_MCP"],
            )
        self.assertEqual(caught.exception.code, dayz_test_tool._BAD_MOD)
        self.assertTrue(caught.exception.code.startswith("bad_mod:"))
        self.assertIn("single folder name", caught.exception.code)
        self.assertIn("absolute path inside the project's mod_roots", caught.exception.code)

    def test_build_run_request_accepts_absolute_mission_inside_roots(self) -> None:
        policy = _policy()
        inside = r"P:\ExampleMod_Suite\_server\mpmissions\custom.ChernarusPlus"
        raw, selected = dayz_test_tool.build_run_request(
            _sealed(policy),
            project="ExampleMod",
            mode="offline",
            mission=inside,
            extra_mods=["@DayZ_MCP"],
        )
        parsed = dayz_test_request.parse_dayz_test_request(raw, policies=(policy,))
        self.assertIs(selected, policy)
        self.assertEqual(parsed.payload["mission"], inside)

    def test_build_run_request_delegates_cross_field_validation(self) -> None:
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "bad_dayz_test_request"
        ):
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="client",
            )

    def test_build_run_request_names_run_id_matrix_causes(self) -> None:
        sealed = _sealed(_policy())
        bad_uuid = "not-a-uuid"
        rows = (
            (
                {"mode": "client", "run_id": bad_uuid},
                "bad_run_id",
            ),
            (
                {"mode": "client", "run_id": None},
                dayz_test_request._CLIENT_REQUIRES_RUN_ID,
            ),
            (
                {"mode": "server", "run_id": RUN_ID},
                dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
            ),
            (
                {"mode": "all", "run_id": RUN_ID},
                dayz_test_request._SERVER_ALL_FORBID_RUN_ID,
            ),
        )
        for arguments, token in rows:
            for preflight in (False, True):
                with self.subTest(arguments=arguments, preflight=preflight):
                    with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                        dayz_test_tool.build_run_request(
                            sealed,
                            project="ExampleMod",
                            extra_mods=["@DayZ_MCP"],
                            preflight=preflight,
                            **arguments,
                        )
                    self.assertIn(token, caught.exception.code)
                    self.assertNotEqual(caught.exception.code, "bad_dayz_test_request")

        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                sealed,
                project="ExampleMod",
                mode="server",
                run_id=bad_uuid,
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(caught.exception.code, "bad_run_id")
        self.assertNotIn(
            dayz_test_request._SERVER_ALL_FORBID_RUN_ID, caught.exception.code
        )

    def test_a_declared_request_rejection_reaches_the_caller_with_its_reason(
        self,
    ) -> None:
        """8f8c point 3. The 25 parser conditions stop arriving as one token."""
        cases = (
            ({"port": 80}, "port_out_of_range"),
            ({"width": 10}, "window_size_out_of_range"),
            ({"server_wait_s": 0}, "server_wait_out_of_range"),
            ({"player_name": "Dev"}, "player_name_invalid"),
            ({"mission": "moon"}, "mission_not_allowed"),
            ({"pack_only": True}, "pack_only_requires_build"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    dayz_test_tool.build_run_request(
                        _sealed(_policy()),
                        project="ExampleMod",
                        mode="server",
                        extra_mods=["@DayZ_MCP"],
                        **overrides,
                    )
                self.assertEqual(
                    caught.exception.code, f"bad_dayz_test_request:{reason}"
                )
                self.assertIn(reason, dayz_test_request.REQUEST_REJECTION_REASONS)

    def test_an_undeclared_suffix_keeps_the_bare_legacy_code(self) -> None:
        """Negative control that kills the mutant "translate any suffix".

        Green before the change too: it is the guard, not the feature.
        """
        original = dayz_test_request.parse_dayz_test_request

        def refuse(*_args: object, **_kwargs: object) -> object:
            raise ValueError("invalid_dayz_test_request:a_reason_nobody_declared")

        dayz_test_request.parse_dayz_test_request = refuse
        try:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                dayz_test_tool.build_run_request(
                    _sealed(_policy()),
                    project="ExampleMod",
                    mode="server",
                    extra_mods=["@DayZ_MCP"],
                )
        finally:
            dayz_test_request.parse_dayz_test_request = original
        self.assertEqual(caught.exception.code, "bad_dayz_test_request")

    def test_a_type_error_from_the_parser_keeps_the_bare_legacy_code(self) -> None:
        original = dayz_test_request.parse_dayz_test_request

        def refuse(*_args: object, **_kwargs: object) -> object:
            raise TypeError("something else entirely")

        dayz_test_request.parse_dayz_test_request = refuse
        try:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                dayz_test_tool.build_run_request(
                    _sealed(_policy()),
                    project="ExampleMod",
                    mode="server",
                    extra_mods=["@DayZ_MCP"],
                )
        finally:
            dayz_test_request.parse_dayz_test_request = original
        self.assertEqual(caught.exception.code, "bad_dayz_test_request")

    def test_the_replacement_witness_is_only_valid_on_a_client_relaunch(self) -> None:
        """79e2. The field exists for one call: the one that supersedes a client."""
        raw, _chosen = dayz_test_tool.build_run_request(
            _sealed(_policy()),
            project="ExampleMod",
            mode="client",
            run_id=RUN_ID,
            extra_mods=["@DayZ_MCP"],
            replace_if_not_polling_since=1_756_000_000_000,
        )
        self.assertEqual(
            json.loads(raw)["replace_if_not_polling_since"], 1_756_000_000_000
        )

        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="server",
                extra_mods=["@DayZ_MCP"],
                replace_if_not_polling_since=1_756_000_000_000,
            )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:replace_witness_not_allowed",
        )

    def test_a_call_without_a_replacement_carries_no_witness(self) -> None:
        raw, _chosen = dayz_test_tool.build_run_request(
            _sealed(_policy()),
            project="ExampleMod",
            mode="client",
            run_id=RUN_ID,
            extra_mods=["@DayZ_MCP"],
        )
        self.assertIsNone(json.loads(raw)["replace_if_not_polling_since"])

    def test_build_run_request_names_invalid_mode_and_expected_values(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            dayz_test_tool.build_run_request(
                _sealed(_policy()),
                project="ExampleMod",
                mode="not-a-mode",
                extra_mods=["@DayZ_MCP"],
            )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected "
            + "|".join(dayz_test_modes.public_mode_names()),
        )

    def test_extension_run_must_be_idle_and_match_selected_project(self) -> None:
        policy = _policy()
        valid = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING_IDLE",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                }
            ]
        }
        run = dayz_test_tool.require_extension_run(valid, policy, RUN_ID)
        self.assertEqual(run["run_id"], RUN_ID)

        for status, code in (
            ({"runs": []}, "run_not_found"),
            (
                {"runs": [{**valid["runs"][0], "state": "RUNNING"}]},
                "run_not_extensible",
            ),
            (
                {"runs": [{**valid["runs"][0], "mod": "@StorageMod"}]},
                "run_project_mismatch",
            ),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, code):
                    dayz_test_tool.require_extension_run(status, policy, RUN_ID)

    def test_stop_resolves_project_only_from_exact_manifest_run(self) -> None:
        utopia = _policy()
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "RUNNING_IDLE",
                    "mod": "@StorageMod",
                    "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
                }
            ]
        }

        selected, run = dayz_test_tool.resolve_stop_run(
            status, _sealed(utopia, lfv), RUN_ID
        )

        self.assertIs(selected, lfv)
        self.assertEqual(run["run_id"], RUN_ID)

    def test_stop_resolves_never_started_unacked_exited_run(self) -> None:
        policy = _policy()
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "EXITED",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                    "launch_acknowledged": False,
                }
            ]
        }

        selected, run = dayz_test_tool.resolve_stop_run(
            status, _sealed(policy), RUN_ID
        )

        self.assertIs(selected, policy)
        self.assertEqual(run["run_id"], RUN_ID)

    def test_stop_rejects_acked_exited_run(self) -> None:
        status = {
            "runs": [
                {
                    "run_id": RUN_ID,
                    "state": "EXITED",
                    "mod": "@ExampleMod",
                    "profiles": r"P:\ExampleMod_Suite\_client\profiles",
                    "launch_acknowledged": True,
                }
            ]
        }
        with self.assertRaisesRegex(dayz_test_tool.DayzTestToolError, "run_not_active"):
            dayz_test_tool.resolve_stop_run(status, _sealed(_policy()), RUN_ID)


class DayzTestTerminalTest(unittest.TestCase):
    def test_parse_success_terminal(self) -> None:
        raw = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            }
        )

        result = dayz_test_tool.parse_worker_terminal(raw, b"", 0)

        self.assertTrue(result.ok)
        self.assertEqual(result.run_id, RUN_ID)
        self.assertIsNone(result.error_code)

    def test_parse_failure_terminal_with_degraded_cleanup(self) -> None:
        raw = _terminal(
            {
                "cleanup_degraded": True,
                "error_code": "readiness_failed",
                "exit_code": 2,
                "ok": False,
                "run_id": RUN_ID,
            }
        )

        result = dayz_test_tool.parse_worker_terminal(raw, b"", 2)

        self.assertFalse(result.ok)
        self.assertTrue(result.cleanup_degraded)
        self.assertEqual(result.run_id, RUN_ID)

    def test_typed_readiness_codes_keep_five_key_terminal_contract(self) -> None:
        readiness_codes = sorted(
            code
            for code in dayz_test_worker.WORKER_ERROR_CODES
            if code.startswith("readiness_") and code != "readiness_failed"
        )
        self.assertEqual(len(readiness_codes), 8)
        for code in readiness_codes:
            with self.subTest(code=code):
                raw = _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": code,
                        "exit_code": 2,
                        "ok": False,
                        "run_id": None,
                    }
                )
                payload = json.loads(raw)
                self.assertEqual(
                    set(payload),
                    {
                        "cleanup_degraded",
                        "error_code",
                        "exit_code",
                        "ok",
                        "run_id",
                    },
                )
                result = dayz_test_tool.parse_worker_terminal(raw, b"", 2)
                self.assertEqual(result.error_code, code)

    def test_terminal_rejects_non_closed_or_inconsistent_payloads(self) -> None:
        valid = {
            "cleanup_degraded": False,
            "error_code": None,
            "exit_code": 0,
            "ok": True,
            "run_id": RUN_ID,
        }
        cases = {
            "stderr": (_terminal(valid), b"unexpected", 0),
            "extra": (_terminal({**valid, "secret": "x"}), b"", 0),
            "noncanonical": (json.dumps(valid, indent=2).encode(), b"", 0),
            "exit_mismatch": (_terminal(valid), b"", 1),
            "success_error": (
                _terminal({**valid, "error_code": "worker_failed"}),
                b"",
                0,
            ),
            "unknown_error": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "raw_exception_text",
                        "run_id": None,
                    }
                ),
                b"",
                2,
            ),
            "degraded_without_run": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "worker_failed",
                        "cleanup_degraded": True,
                        "run_id": None,
                    }
                ),
                b"",
                2,
            ),
            "failure_run_without_degradation": (
                _terminal(
                    {
                        **valid,
                        "ok": False,
                        "exit_code": 2,
                        "error_code": "worker_failed",
                    }
                ),
                b"",
                2,
            ),
            "oversize": (b"{" + b" " * 4096 + b"}", b"", 2),
            "duplicate": (
                b'{"cleanup_degraded":false,"error_code":null,"exit_code":0,'
                b'"ok":true,"ok":true,"run_id":null}',
                b"",
                0,
            ),
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    dayz_test_tool.DayzTestToolError, "terminal_invalid"
                ):
                    dayz_test_tool.parse_worker_terminal(*arguments)

    def test_success_run_id_matches_operation_context(self) -> None:
        success_without_run = dayz_test_tool.WorkerTerminal(False, None, 0, True, None)
        success_with_run = dayz_test_tool.WorkerTerminal(
            False, None, 0, True, RUN_ID
        )

        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_without_run, preflight=False, expected_run_id=None
            )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_with_run, preflight=True, expected_run_id=None
            )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_with_run,
                preflight=False,
                expected_run_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            )
        # preflight client reattach: worker echoes the requested run_id.
        dayz_test_tool._validate_terminal_context(
            success_with_run, preflight=True, expected_run_id=RUN_ID
        )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "terminal_invalid"
        ):
            dayz_test_tool._validate_terminal_context(
                success_without_run, preflight=True, expected_run_id=RUN_ID
            )

    def test_stop_artifact_must_be_derived_from_sealed_project(self) -> None:
        policy = _policy()
        self.assertEqual(
            dayz_test_tool._stop_artifacts(
                policy,
                {"profiles": r"P:\ExampleMod_Suite\_client\profiles"},
            ),
            [r"P:\ExampleMod_Suite\_client\profiles"],
        )
        with self.assertRaisesRegex(
            dayz_test_tool.DayzTestToolError, "lifecycle_status_invalid"
        ):
            dayz_test_tool._stop_artifacts(
                policy, {"profiles": r"P:\Unapproved\profiles"}
            )


class DayzTestExecutionTest(unittest.IsolatedAsyncioTestCase):
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
        desktop_patcher = patch.object(
            dayz_test_tool,
            "evaluate_prerun_desktop",
            return_value=mcp_capture.PrerunDesktopResult(
                error_code=None,
                desktop="unlocked",
                mean_brightness=80.0,
                nonblack_ratio=0.9,
                waited_s=0.01,
                remediation="",
            ),
        )
        desktop_patcher.start()
        self.addCleanup(desktop_patcher.stop)
        # ficha df93: the admin-tools preflight verifies the tools a request
        # asks for and warns when it asks for none. These fixtures are policies
        # and stubs, not a server workspace, so the gate is neutralised here
        # exactly as the Steam one above is; its oracle is tests/test_vpp_preflight.py.
        vpp_patcher = patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=native_launcher_transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=(),
                hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
            ),
        )
        vpp_patcher.start()
        self.addCleanup(vpp_patcher.stop)
        boot_clock = _FakeBootClock()
        clock_patcher = patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=boot_clock.now
        )
        clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        sleep_patcher = patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=boot_clock.sleep
        )
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    async def _launch_and_capture(self, **kwargs: object):
        policy = kwargs.pop("policy", _policy())
        seen: list[bytes] = []

        async def launch(raw_request: bytes, **launch_kwargs: object) -> int:
            seen.append(raw_request)
            await launch_kwargs["execution_started_cb"]()
            launch_kwargs["output_sink"](
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
            result = await dayz_test_tool.execute_dayz_test_run(
                _Runtime(),
                project=policy.mod,
                mode="all",
                **kwargs,
            )
        self.assertEqual(len(seen), 1)
        parsed = dayz_test_request.parse_dayz_test_request(
            seen[0], policies=(policy,)
        )
        return result, parsed

    async def test_run_defaults_bridge_mod_and_reports_it(self) -> None:
        """A non-bridge project launches with @DayZ_MCP appended and named."""
        policy = _policy()
        result, parsed = await self._launch_and_capture(policy=policy)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["project"], "ExampleMod")
        self.assertEqual(parsed.payload["extra_mods"], ["@DayZ_MCP"])
        self.assertEqual(parsed.payload["base_mods"], list(policy.default_base_mods))
        self.assertEqual(parsed.payload["server_mods"], [])
        self.assertEqual(result["extra_mods_defaulted"], ["@DayZ_MCP"])

    async def test_run_forwards_server_only_mods_without_bridge_default_report(self) -> None:
        result, parsed = await self._launch_and_capture(server_only_mods=["@DayZ_MCP", "@Audit"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(parsed.payload["server_only_mods"], ["@DayZ_MCP", "@Audit"])
        self.assertNotIn("extra_mods_defaulted", result)

    async def test_run_does_not_duplicate_an_explicit_bridge_mod(self) -> None:
        result, parsed = await self._launch_and_capture(
            extra_mods=["@Other", "@DayZ_MCP"]
        )
        self.assertEqual(parsed.payload["extra_mods"], ["@Other", "@DayZ_MCP"])
        self.assertNotIn("extra_mods_defaulted", result)

    async def test_run_dayz_mcp_project_is_unchanged(self) -> None:
        policy = _policy(
            mod="DayZ_MCP",
            dev_root=r"P:\DayZ_MCP_dev",
            default_source=r"P:\DayZ_MCP",
        )
        result, parsed = await self._launch_and_capture(policy=policy)
        self.assertEqual(parsed.payload["mod"], "DayZ_MCP")
        self.assertEqual(parsed.payload["extra_mods"], [])
        self.assertNotIn("extra_mods_defaulted", result)

    async def test_run_bad_extra_mod_still_fails_with_bad_mod(self) -> None:
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(_policy())),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ) as launch:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    _Runtime(),
                    project="ExampleMod",
                    mode="all",
                    extra_mods=[r"mods\@Broken"],
                )
        launch.assert_not_awaited()
        self.assertEqual(caught.exception.code, dayz_test_tool._BAD_MOD)
        self.assertTrue(caught.exception.code.startswith("bad_mod:"))

    async def test_run_reports_progress_and_returns_compact_terminal_result(self) -> None:
        policy = _policy()
        opened = _Opened()
        bundle = _Bundle(_sealed(policy))
        runtime = _Runtime()
        progress: list[tuple[str, str | None]] = []

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(policy,)
            )
            self.assertEqual(parsed.payload["mode"], "all")
            self.assertIs(kwargs["daemon_policy"], runtime.daemon_policy)
            await kwargs["queue_progress_cb"](0.0, None, "En cola (posición 2)")
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

        async def report(stage: str, message: str | None) -> None:
            progress.append((stage, message))

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=opened
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=bundle,
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
                progress_cb=report,
            )

        self.assertTrue(opened.validated)
        self.assertEqual(
            [stage for stage, _message in progress],
            ["validating", "queued", "executing", "finalizing"],
        )
        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
                "server_alive",
                "client_alive",
                # M19: the readiness triple is always present, null included. A
                # key that appears only on some rows is a key no consumer can
                # branch on.
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
                # Fase 1b + ronda 2: the extension gate publishes the branch it
                # took, and what actually happened to the client role.
                "client_terminated",
                "client_relaunched",
                "client_replace_reason",
                "client_last_poll_age_s",
                "client_record_age_s",
                "vpp_missing",
                "vpp_warnings",
                "steam_startup",
                "steam_pid_repair",
                "steam_restarted",
                "client_death_diagnosis",
                "storage_rotated",
                "storage_backup",
                "storage_reset_notice",
                # E2: present on every envelope. null when this call is not a
                # declared storage refusal (success, and every other failure).
                "storage_recovery_reason",
                "session_idle_s",
                "display_timeout_s",
                "server_boot",
            },
        )
        self.assertIsNone(result["storage_recovery_reason"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["run_id"], RUN_ID)
        # M19 call discipline: mode=all, not preflight, terminal ok -> the
        # bridge snapshot is consulted exactly once and the triple is filled
        # from it, never from the PID.
        self.assertEqual(runtime.bridge_calls, 1)
        self.assertIs(result["bridge_ready"], True)
        self.assertEqual(result["reason"], "ready")
        self.assertEqual(
            result["artifacts_paths"],
            [
                r"P:\ExampleMod_Suite\_server\profiles",
                r"P:\ExampleMod_Suite\_client\profiles",
            ],
        )

    async def test_server_mode_never_consults_the_bridge_snapshot(self) -> None:
        """Zero snapshot reads outside client|all.

        The ficha allows exactly one read for a successful non-preflight
        client|all and zero anywhere else. Without this case a build that asks
        on every row stays green, and then the triple no longer means "the
        client's bridge" -- it means whatever the daemon happened to answer.
        Found by a mutant that widened the predicate to every row and survived.
        """

        policy = _policy()
        opened = _Opened()
        bundle = _Bundle(_sealed(policy))
        runtime = _Runtime()

        async def launch(raw_request: bytes, **kwargs: object) -> int:
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
            dayz_test_tool, "open_approved_launcher", return_value=opened
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=bundle,
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="server",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(runtime.bridge_calls, 0)
        self.assertIsNone(result["bridge_ready"])
        self.assertIsNone(result["reason"])

    async def test_typed_readiness_failure_uses_the_same_envelope(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        readiness_code = "readiness_udp_foreign_owner"

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": readiness_code,
                        "exit_code": 2,
                        "ok": False,
                        "run_id": None,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool,
            "open_approved_launcher",
            return_value=_Opened(),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
                "server_alive",
                "client_alive",
                # M19: the readiness triple is always present, null included. A
                # key that appears only on some rows is a key no consumer can
                # branch on.
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
                # Fase 1b + ronda 2: the extension gate publishes the branch it
                # took, and what actually happened to the client role.
                "client_terminated",
                "client_relaunched",
                "client_replace_reason",
                "client_last_poll_age_s",
                "client_record_age_s",
                "vpp_missing",
                "vpp_warnings",
                "steam_startup",
                "steam_pid_repair",
                "steam_restarted",
                "client_death_diagnosis",
                "storage_rotated",
                "storage_backup",
                "storage_reset_notice",
                "storage_recovery_reason",
                "session_idle_s",
                "display_timeout_s",
                "server_boot",
            },
        )
        self.assertIsNone(result["storage_recovery_reason"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], readiness_code)

    async def test_steam_session_stale_returns_typed_failure_before_launch(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        stale = steam_preflight.SteamSessionResult(
            error_code=steam_preflight.STEAM_SESSION_STALE,
            steam_registered_pid=4321,
            steam_live_pids=(1, 2, 3, 4, 5, 6, 7, 8, 9),
            remediation="restart Steam",
        )
        launch = AsyncMock()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=stale
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["artifacts_paths"], [])
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertEqual(result["steam_registered_pid"], 4321)
        self.assertEqual(result["steam_live_pids"], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(result["remediation"], "restart Steam")
        self.assertIsNone(result["storage_recovery_reason"])
        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
                "server_alive",
                "client_alive",
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
                # Fase 1b + ronda 2: the extension gate publishes the branch it
                # took, and what actually happened to the client role.
                "client_terminated",
                "client_relaunched",
                "client_replace_reason",
                "client_last_poll_age_s",
                "client_record_age_s",
                "vpp_missing",
                "vpp_warnings",
                "steam_startup",
                "steam_pid_repair",
                "steam_restarted",
                "client_death_diagnosis",
                "storage_rotated",
                "storage_backup",
                "storage_reset_notice",
                "storage_recovery_reason",
                "session_idle_s",
                "display_timeout_s",
                "server_boot",
            },
        )

    async def test_steam_evaluator_exception_returns_typed_stale_envelope(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        launch = AsyncMock()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            side_effect=RuntimeError("sonda P2-F"),
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["run_id"])
        self.assertEqual(result["phase"], "validating")
        self.assertEqual(result["artifacts_paths"], [])
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertIsNone(result["steam_registered_pid"])
        self.assertEqual(result["steam_live_pids"], [])
        self.assertEqual(result["remediation"], steam_preflight.REMEDIATION)
        self.assertIsNone(result["storage_recovery_reason"])
        self.assertEqual(
            set(result),
            {
                "status",
                "project",
                "mode",
                "run_id",
                "phase",
                "elapsed_s",
                "artifacts_paths",
                "error_code",
                "cleanup_degraded",
                "server_alive",
                "client_alive",
                "process_alive",
                "bridge_ready",
                "reason",
                "steam_registered_pid",
                "steam_live_pids",
                "remediation",
                # Fase 1b + ronda 2: the extension gate publishes the branch it
                # took, and what actually happened to the client role.
                "client_terminated",
                "client_relaunched",
                "client_replace_reason",
                "client_last_poll_age_s",
                "client_record_age_s",
                "vpp_missing",
                "vpp_warnings",
                "steam_startup",
                "steam_pid_repair",
                "steam_restarted",
                "client_death_diagnosis",
                "storage_rotated",
                "storage_backup",
                "storage_reset_notice",
                "storage_recovery_reason",
                "session_idle_s",
                "display_timeout_s",
                "server_boot",
            },
        )

    async def test_steam_evaluator_keyboardinterrupt_propagates(self) -> None:
        policy = _policy()
        runtime = _Runtime()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="client",
                    preflight=False,
                    run_id=RUN_ID,
                    extra_mods=["@DayZ_MCP"],
                )

    async def test_steam_session_is_not_consulted_for_preflight_or_server(self) -> None:
        policy = _policy()
        calls: list[int] = []

        def _stale_provider(*_args: object, **_kwargs: object) -> object:
            calls.append(1)
            return steam_preflight.SteamSessionResult(
                error_code=steam_preflight.STEAM_SESSION_STALE,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation="restart Steam",
            )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            parsed = json.loads(_raw_request.decode("utf-8"))
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": None if parsed.get("preflight") else RUN_ID,
                    }
                ),
            )
            return 0

        # preflight=True with mode="client" requires a request run_id
        # (client_requires_run_id) but then expected_run_id is set and the
        # terminal of a preflight run has run_id=None, which
        # _validate_terminal_context rejects (dayz_test_tool.py:527-530).
        # mode="all" consults Steam even for preflight. mode="server" does not.
        for label, arguments, expect_call in (
            ("preflight", {"mode": "all", "preflight": True}, True),
            ("server", {"mode": "server", "preflight": False}, False),
        ):
            calls.clear()
            runtime = _Runtime()
            with self.subTest(label=label), patch.object(
                dayz_test_tool, "open_approved_launcher", return_value=_Opened()
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "load_verified_bundle",
                return_value=_Bundle(_sealed(policy)),
            ), patch.object(
                dayz_test_tool.secure_launcher,
                "execute_secure_launcher_request",
                side_effect=launch,
            ), patch.object(
                dayz_test_tool, "evaluate_steam_session", side_effect=_stale_provider
            ):
                result = await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    extra_mods=["@DayZ_MCP"],
                    **arguments,
                )
                self.assertEqual(calls, [1] if expect_call else [])
                if expect_call:
                    self.assertEqual(result["error_code"], "steam_session_stale")
                    self.assertNotIn("steam_session", result["preflight_skipped_checks"])
                else:
                    self.assertEqual(result["status"], "succeeded")

    async def test_preflight_client_reattach_preserves_requested_run_id(self) -> None:
        policy = _policy()
        runtime = _Runtime()

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = json.loads(raw_request.decode("utf-8"))
            self.assertEqual(parsed["mode"], "client")
            self.assertEqual(parsed["run_id"], RUN_ID)
            self.assertTrue(parsed["preflight"])
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": False,
                        "error_code": None,
                        "exit_code": 0,
                        "ok": True,
                        "run_id": parsed["run_id"],
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
        ), patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=41,
                steam_live_pids=(41,),
                remediation="",
            ),
        ) as steam:
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=True,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        steam.assert_called()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertTrue(result["preflight"])
        self.assertIsNone(result["error_code"])

    async def test_run_fails_when_client_pid_is_already_dead(self) -> None:
        policy = _policy()
        runtime = _Runtime(
            lifecycle={
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "processes": [
                            {"role": "server", "pid": os.getpid()},
                            {"role": "client", "pid": 999_999_999},
                        ],
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
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "client_dead_after_ack")
        self.assertEqual(result["client_alive"], False)
        self.assertEqual(result["server_alive"], True)

    def test_list_project_names_returns_mod_fields_only(self) -> None:
        policy = {
            "projects": [
                {
                    "mod": "FixtureListedMod",
                    "name": "ShouldNotSurface",
                    "path": "ignored-extra",
                },
                "skip-non-dict",
                {"name": "Nameless"},
                {"mod": ""},
                {"mod": 3},
                None,
                {"mod": "FixtureSecondMod"},
            ]
        }

        class _PolicyPath:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def resolve(self) -> _PolicyPath:
                return self

            @property
            def parents(self) -> tuple[_PolicyPath, _PolicyPath]:
                return (self, self)

            def __truediv__(self, _other: object) -> _PolicyPath:
                return self

            def read_bytes(self) -> bytes:
                return json.dumps(policy).encode("utf-8")

        # list_project_names() locates the policy through Path(__file__);
        # replace Path so the read never leaves this fixture.
        with patch.object(dayz_test_tool, "Path", _PolicyPath):
            payload = dayz_test_tool.list_project_names()

        self.assertEqual(
            payload,
            {
                "projects": [
                    {"name": "FixtureListedMod"},
                    {"name": "FixtureSecondMod"},
                ]
            },
        )
        self.assertTrue(all(set(item) == {"name"} for item in payload["projects"]))

    def test_list_project_names_missing_policy_is_not_invalid(self) -> None:
        """A clone has no sealed policy; that is missing, not corrupt.

        FileNotFoundError is an OSError. If the read except swallows OSError
        as launcher_policy_invalid, this assertion goes red.
        """
        with patch.object(
            dayz_test_tool, "Path", _list_projects_path(FileNotFoundError())
        ):
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                dayz_test_tool.list_project_names()
        self.assertEqual(caught.exception.code, "launcher_policy_missing")

    def test_list_project_names_unreadable_policy_stays_invalid(self) -> None:
        cases: tuple[tuple[str, bytes | BaseException], ...] = (
            ("broken-json", b"{not json"),
            ("non-object", b"[1]"),
            ("undecodable", b"\x80"),
            ("permission", PermissionError()),
        )
        for label, payload in cases:
            with self.subTest(label=label):
                with patch.object(
                    dayz_test_tool, "Path", _list_projects_path(payload)
                ):
                    with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                        dayz_test_tool.list_project_names()
                self.assertEqual(caught.exception.code, "launcher_policy_invalid")

    async def test_run_rejects_busy_session_before_opening_launcher(self) -> None:
        runtime = _Runtime()
        runtime.active_ticket = "ticket"
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "session_busy"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_run_rejects_public_offline_mode_with_expected_enum(self) -> None:
        runtime = _Runtime()
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="offline"
                )
        self.assertEqual(
            caught.exception.code,
            "bad_dayz_test_request:mode expected "
            + "|".join(dayz_test_modes.public_mode_names()),
        )
        opened.assert_not_called()

    async def test_run_names_held_session_lease_on_transition_conflict(self) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "held-lease"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: release your session lease first - "
            "dayz_test_run manages its own lease internally",
        )
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_run_transition_conflict_without_local_lease_stays_neutral(
        self,
    ) -> None:
        runtime = _Runtime()
        runtime.active_ticket = "queued-ticket"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_run(
                    runtime, project="ExampleMod", mode="all"
                )
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: a session transition is in flight",
        )
        self.assertNotIn("release your session lease first", caught.exception.code)
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_stop_names_held_session_lease_without_naming_run(self) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "held-lease"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            raise ControlClientError(
                "session_transition_conflict",
                request_stage="post_request",
                http_bytes_sent=1,
            )

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]
        with patch.object(dayz_test_tool, "open_approved_launcher") as opened:
            with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)
        self.assertEqual(
            caught.exception.code,
            "session_transition_conflict: release your session lease first - "
            "dayz_test_stop manages its own lease internally",
        )
        self.assertNotIn("dayz_test_run manages", caught.exception.code)
        opened.assert_not_called()
        self.assertEqual(runtime.reconcile_calls, 1)

    async def test_idle_guard_recovers_stale_local_operation_before_launch(
        self,
    ) -> None:
        runtime = _Runtime()
        runtime.active_lease_token = "stale-local-token"
        runtime.active_operation_id = "11111111-1111-4111-8111-111111111111"

        async def reconcile() -> dict[str, object]:
            runtime.reconcile_calls += 1
            runtime.active_lease_token = None
            runtime.active_operation_id = None
            return {"reconciled": True}

        runtime.reconcile_idle_session = reconcile  # type: ignore[method-assign]

        await dayz_test_tool._require_idle_session(runtime, tool="dayz_test_run")

        self.assertEqual(runtime.reconcile_calls, 1)
        self.assertIsNone(runtime.active_lease_token)
        self.assertIsNone(runtime.active_operation_id)

    async def test_run_id_is_bound_before_secure_launch(self) -> None:
        policy = _policy()
        runtime = _Runtime(
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING_IDLE",
                        "mod": "@StorageMod",
                    }
                ]
            }
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=AsyncMock(),
        ) as launch:
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "run_project_mismatch"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="client",
                    run_id=RUN_ID,
                    extra_mods=["@DayZ_MCP"],
                )
        launch.assert_not_awaited()

    async def test_stop_derives_policy_and_builds_adopt_then_stop_request(self) -> None:
        lfv = _policy(
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

        async def launch(raw_request: bytes, **kwargs: object) -> int:
            parsed = dayz_test_request.parse_dayz_test_request(
                raw_request, policies=(lfv,)
            )
            self.assertIs(parsed.payload["kill"], True)
            self.assertEqual(parsed.payload["run_id"], RUN_ID)
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
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["project"], "StorageMod")
        self.assertEqual(result["mode"], "stop")
        self.assertEqual(result["artifacts_paths"], [run["profiles"]])

    async def test_stop_reconciles_lost_terminal_response_from_exact_exited_run(self) -> None:
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        active = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        exited = {**active, "state": "EXITED"}
        runtime = _Runtime()
        runtime.lifecycle_status = AsyncMock(
            side_effect=({"runs": [active]}, {"runs": [exited]})
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "run_stop_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["phase"], "completed")
        self.assertIsNone(result["error_code"])
        self.assertFalse(result["cleanup_degraded"])

    async def test_stop_treats_empty_processes_after_run_stop_failed_as_success(
        self,
    ) -> None:
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        active = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        gone = {**active, "state": "UNRECONCILED", "processes": []}
        runtime = _Runtime()
        runtime.lifecycle_status = AsyncMock(
            side_effect=({"runs": [active]}, {"runs": [gone]})
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "run_stop_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error_code"])
        self.assertFalse(result["cleanup_degraded"])

    async def test_stop_treats_reaped_row_after_run_stop_failed_as_success(self) -> None:
        lfv = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        active = {
            "run_id": RUN_ID,
            "state": "RUNNING_IDLE",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
        }
        runtime = _Runtime()
        runtime.lifecycle_status = AsyncMock(
            side_effect=({"runs": [active]}, {"runs": []})
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "run_stop_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(lfv)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            result = await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID)

        self.assertEqual(result["status"], "succeeded")
        self.assertIsNone(result["error_code"])
        self.assertFalse(result["cleanup_degraded"])

    async def test_worker_failed_carries_lifecycle_start_error(self) -> None:
        policy = _policy()
        # last_start_error is only written by _settle_failed_launch, which
        # settles the row the launch already added: the store lists the run.
        runtime = _Runtime(
            lifecycle={
                "runs": [{"run_id": RUN_ID, "state": "EXITED", "processes": []}],
                "last_start_error": "instance_config_missing",
            }
        )

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            kwargs["output_sink"](
                "stdout",
                _terminal(
                    {
                        "cleanup_degraded": True,
                        "error_code": "worker_failed",
                        "exit_code": 2,
                        "ok": False,
                        "run_id": RUN_ID,
                    }
                ),
            )
            return 2

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
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "instance_config_missing")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertGreaterEqual(runtime.lifecycle_calls, 1)

    async def test_stdout_overflow_sentinel_is_rejected_by_real_parser(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        seen_len: list[int] = []
        real_parse = dayz_test_tool.parse_worker_terminal
        valid = _terminal(
            {
                "cleanup_degraded": False,
                "error_code": None,
                "exit_code": 0,
                "ok": True,
                "run_id": RUN_ID,
            }
        )
        # Compact five-key terminals stay well under the 4096 cap; 4097
        # is overflow, not a valid payload the parser could accept.
        self.assertLess(len(valid), 4096)
        self.assertEqual(len(b"{" + (b" " * 4094) + b"}"), 4096)

        def parse_and_record(
            stdout: bytes, stderr: bytes, process_exit_code: int
        ) -> dayz_test_tool.WorkerTerminal:
            seen_len.append(len(stdout))
            self.assertEqual(stderr, b"")
            self.assertEqual(process_exit_code, 0)
            return real_parse(stdout, stderr, process_exit_code)

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            await kwargs["execution_started_cb"]()
            kwargs["output_sink"]("stdout", b"{" + (b" " * 4094) + b"}")
            kwargs["output_sink"]("stdout", b"X")
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
        ), patch.object(
            dayz_test_tool, "parse_worker_terminal", side_effect=parse_and_record
        ):
            with self.assertRaisesRegex(
                dayz_test_tool.DayzTestToolError, "terminal_invalid"
            ):
                await dayz_test_tool.execute_dayz_test_run(
                    runtime,
                    project="ExampleMod",
                    mode="all",
                    extra_mods=["@DayZ_MCP"],
                )

        self.assertEqual(seen_len, [4097])


if __name__ == "__main__":
    unittest.main()


# --- M19: the single readiness contract (ficha 21/a396 point 5) -------------
#
# The incident this comes from is a launch called ready because a PID existed.
# So the contract has two axes that never feed each other, and a third field
# that says WHY the bridge axis holds what it holds. The table below is written
# out rather than generated: deriving the expectations from the projection would
# make it agree with itself.
#
# label, client_alive, bridge snapshot, expected (process_alive, bridge_ready, reason)
_PROJECTION_CASES = (
    (
        "alive and polling",
        True,
        {"ready": {"ready": True, "reason": "ready"}},
        (True, True, "ready"),
    ),
    (
        "alive but not polling",
        True,
        {"ready": {"ready": False, "reason": "client_not_polling"}},
        (True, False, "client_not_polling"),
    ),
    (
        # The two axes have to be able to disagree, or one of them is decorative.
        "process gone, snapshot still says ready",
        False,
        {"ready": {"ready": True, "reason": "ready"}},
        (False, True, "ready"),
    ),
    ("no snapshot at all", True, None, (True, None, None)),
    ("snapshot is not a mapping", True, ["ready"], (True, None, None)),
    ("ready object is not a mapping", True, {"ready": "yes"}, (True, None, None)),
    (
        # An unfamiliar reason is transported, not swallowed: the reason space
        # is the server's to grow, and dropping one would report "unknown" for
        # an answer that exists.
        "reason this module has never seen",
        True,
        {"ready": {"ready": True, "reason": "some_new_reason"}},
        (True, True, "some_new_reason"),
    ),
    (
        "reason present but empty",
        True,
        {"ready": {"ready": True, "reason": ""}},
        (True, None, None),
    ),
    (
        "flag is not a boolean",
        True,
        {"ready": {"ready": 1, "reason": "ready"}},
        (True, None, None),
    ),
    ("liveness unknown too", None, None, (None, None, None)),
)


class LaunchReadinessProjectionTest(unittest.TestCase):
    def test_projection_matrix(self) -> None:
        for label, alive, snapshot, expected in _PROJECTION_CASES:
            with self.subTest(label):
                projection = dayz_test_tool._project_launch_readiness(alive, snapshot)
                self.assertEqual(
                    (
                        projection.process_alive,
                        projection.bridge_ready,
                        projection.reason,
                    ),
                    expected,
                )

    def test_the_bridge_axis_is_never_promoted_from_the_process_axis(self) -> None:
        # The whole point: a live process with no readable snapshot stays
        # unknown on the bridge axis. If this ever returns True, the defect the
        # contract exists to expose is back.
        projection = dayz_test_tool._project_launch_readiness(True, None)
        self.assertIs(projection.process_alive, True)
        self.assertIsNone(projection.bridge_ready)
        self.assertIsNone(projection.reason)

    def test_every_reason_the_server_can_emit_survives_the_projection(self) -> None:
        # Derived from the server, not from prose. The first version of this
        # contract whitelisted six reasons taken from the tool description; the
        # server also emits "legacy_unbound" AND every value of
        # _FENCE_BLOCK_READY, so the whitelist would have reported "unknown" for
        # real answers. The reason space is open: what must hold is that nothing
        # the server can say gets dropped on the way through.
        source = source_of(server.compute_bridge_ready)
        emitted = set(re.findall(r'"reason":\s*"([a-z_]+)"', source))
        emitted |= {
            value
            for value in server._FENCE_BLOCK_READY.values()
            if isinstance(value, str) and value
        }
        self.assertGreater(len(emitted), 6, "no se leyo el espacio real de razones")
        for reason in sorted(emitted):
            with self.subTest(reason):
                projection = dayz_test_tool._project_launch_readiness(
                    True, {"ready": {"ready": False, "reason": reason}}
                )
                self.assertIs(projection.bridge_ready, False)
                self.assertEqual(projection.reason, reason)

    def test_a_reason_that_is_not_a_usable_string_is_no_answer(self) -> None:
        for bad in (None, "", 7, ["ready"], {}):
            with self.subTest(repr(bad)):
                projection = dayz_test_tool._project_launch_readiness(
                    True, {"ready": {"ready": False, "reason": bad}}
                )
                self.assertIsNone(projection.bridge_ready)
                self.assertIsNone(projection.reason)


class DayzTestStopEnvelopeTest(unittest.IsolatedAsyncioTestCase):
    async def _stop(self, lifecycle: dict[str, object]):
        runtime = _Runtime(lifecycle)
        policy = _policy(
            mod="StorageMod",
            dev_root=r"C:\Tools\LFV_D2_Executor",
            default_source=r"C:\Tools\LFV_D2_Executor\staged-source\StorageMod",
            default_base_mods=("@CF",),
        )
        async def launch(raw_request: bytes, **kwargs: object) -> int:
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
            return await dayz_test_tool.execute_dayz_test_stop(runtime, RUN_ID), runtime

    async def test_present_inactive_run_returns_structured_envelope(self) -> None:
        row = {
            "run_id": RUN_ID,
            "state": "EXITED",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "launch_acknowledged": True,
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": False,
        }
        result, runtime = await self._stop({"runs": [row]})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(result["error_code"], "run_not_active")
        self.assertEqual(result["daemon_generation_at_launch"], "old-gen")
        self.assertEqual(result["daemon_generation_current"], "new-gen")
        self.assertIs(result["generation_changed"], False)
        self.assertNotIn("retired_run_diagnostics", result)
        self.assertEqual(runtime.lifecycle_calls, 1)

    async def test_absent_run_with_one_diagnostic_returns_run_not_found_envelope(
        self,
    ) -> None:
        diagnostic = {
            "run_id": RUN_ID,
            "event": "run_reaped",
            "reason": "all_processes_gone_or_foreign",
            "decision": "reaped",
            "state": "EXITED",
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": False,
        }
        result, _runtime = await self._stop(
            {"runs": [], "retired_run_diagnostics": [diagnostic]}
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "run_not_found")
        self.assertEqual(result["daemon_generation_at_launch"], "old-gen")
        self.assertIs(result["generation_changed"], False)
        self.assertNotIn("retired_run_diagnostics", result)

    async def test_unknown_uuid_still_raises_run_not_found(self) -> None:
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await self._stop({"runs": [], "retired_run_diagnostics": []})
        self.assertEqual(caught.exception.code, "run_not_found")

    async def test_ambiguous_diagnostics_still_raise_run_not_found(self) -> None:
        diagnostic = {
            "run_id": RUN_ID,
            "event": "run_reaped",
            "reason": "all_processes_gone_or_foreign",
            "decision": "reaped",
            "state": "EXITED",
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": False,
        }
        other = dict(diagnostic, event="lifecycle_stop_outcome", decision="stopped")
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await self._stop({"runs": [], "retired_run_diagnostics": [diagnostic, other]})
        self.assertEqual(caught.exception.code, "run_not_found")

    async def test_incomplete_diagnostic_raises_run_not_found_without_fabricating_nulls(
        self,
    ) -> None:
        complete = {
            "run_id": RUN_ID,
            "event": "run_reaped",
            "reason": "all_processes_gone_or_foreign",
            "decision": "reaped",
            "state": "EXITED",
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": False,
        }
        for field in list(complete):
            mutilated = {key: value for key, value in complete.items() if key != field}
            with self.subTest(missing=field):
                with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
                    await self._stop({"runs": [], "retired_run_diagnostics": [mutilated]})
                self.assertEqual(caught.exception.code, "run_not_found")

    async def test_diagnostic_with_non_exited_state_raises_run_not_found(self) -> None:
        diagnostic = {
            "run_id": RUN_ID,
            "event": "run_reaped",
            "reason": "all_processes_gone_or_foreign",
            "decision": "reaped",
            "state": "RUNNING_IDLE",
            "daemon_generation_at_launch": "old-gen",
            "daemon_generation_current": "new-gen",
            "generation_changed": False,
        }
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await self._stop({"runs": [], "retired_run_diagnostics": [diagnostic]})
        self.assertEqual(caught.exception.code, "run_not_found")

    async def test_present_inactive_row_without_generation_raises_run_not_active(
        self,
    ) -> None:
        row = {
            "run_id": RUN_ID,
            "state": "EXITED",
            "mod": "@StorageMod",
            "profiles": r"C:\Tools\LFV_D2_Executor\_client\profiles",
            "launch_acknowledged": True,
        }
        with self.assertRaises(dayz_test_tool.DayzTestToolError) as caught:
            await self._stop({"runs": [row]})
        self.assertEqual(caught.exception.code, "run_not_active")


class PeerAgeTest(unittest.TestCase):
    def test_an_enormous_int_is_not_a_usable_age(self) -> None:
        self.assertIsNone(dayz_test_tool._peer_age(10**400))

    def test_a_normal_float_age_is_still_read(self) -> None:
        self.assertTrue(
            dayz_test_tool._peer_row_is_usable(
                {
                    "last_poll_age_s": 10**400,
                    "bound_last_poll_age_s": 1.5,
                }
            )
        )
        self.assertEqual(dayz_test_tool._peer_age(1.5), 1.5)


class SteamAutoRemediationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        vpp_patcher = patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=native_launcher_transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=(),
                hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
            ),
        )
        vpp_patcher.start()
        self.addCleanup(vpp_patcher.stop)
        boot_clock = _FakeBootClock()
        clock_patcher = patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=boot_clock.now
        )
        clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        sleep_patcher = patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=boot_clock.sleep
        )
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        desktop_patcher = patch.object(
            dayz_test_tool,
            "evaluate_prerun_desktop",
            return_value=mcp_capture.PrerunDesktopResult(
                error_code=None,
                desktop="unlocked",
                mean_brightness=80.0,
                nonblack_ratio=0.9,
                waited_s=0.01,
                remediation="",
            ),
        )
        desktop_patcher.start()
        self.addCleanup(desktop_patcher.stop)

    def _stale(self) -> steam_preflight.SteamSessionResult:
        return steam_preflight.SteamSessionResult(
            error_code=steam_preflight.STEAM_SESSION_STALE,
            steam_registered_pid=0,
            steam_live_pids=(4321,),
            remediation=steam_preflight.REMEDIATION,
        )

    def _healthy(self) -> steam_preflight.SteamSessionResult:
        return steam_preflight.SteamSessionResult(
            error_code=None,
            steam_registered_pid=4321,
            steam_live_pids=(4321,),
            remediation=steam_preflight.REMEDIATION,
        )

    async def test_without_the_opt_in_a_stale_steam_session_still_aborts(self) -> None:
        policy = _policy()
        runtime = _Runtime()
        launch = AsyncMock()
        remediations: list[object] = []

        def _record_remediation(*_args: object, **_kwargs: object) -> object:
            remediations.append(1)
            return self._healthy()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=self._stale()
        ), patch.object(
            steam_preflight,
            "remediate_stale_steam_session",
            side_effect=_record_remediation,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
                auto_remediate_steam=False,
            )

        launch.assert_not_awaited()
        self.assertEqual(remediations, [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertEqual(result["phase"], "validating")
        self.assertNotIn("steam_remediated", result)
        self.assertNotIn("steam_remediation_s", result)

    async def test_opt_in_is_sealed_and_no_remediation_occurs_before_admission(
        self,
    ) -> None:
        policy = _policy()
        runtime = _Runtime()
        remediations: list[object] = []

        def _fake_remediate(*_args: object, **_kwargs: object) -> object:
            remediations.append(1)
            return self._healthy()

        async def launch(_raw_request: bytes, **kwargs: object) -> int:
            self.assertIs(json.loads(_raw_request)["auto_remediate_steam"], True)
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
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=self._stale()
        ), patch.object(
            steam_preflight,
            "remediate_stale_steam_session",
            side_effect=_fake_remediate,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="all",
                extra_mods=["@DayZ_MCP"],
                auto_remediate_steam=True,
            )

        self.assertEqual(remediations, [])
        self.assertNotIn("steam_remediated", result)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["run_id"], RUN_ID)

    async def test_the_default_is_no_remediation_when_the_flag_is_not_passed(
        self,
    ) -> None:
        policy = _policy()
        runtime = _Runtime()
        launch = AsyncMock()
        remediations: list[object] = []

        def _record_remediation(*_args: object, **_kwargs: object) -> object:
            remediations.append(1)
            return self._healthy()

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=_Bundle(_sealed(policy)),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            new=launch,
        ), patch.object(
            dayz_test_tool, "evaluate_steam_session", return_value=self._stale()
        ), patch.object(
            steam_preflight,
            "remediate_stale_steam_session",
            side_effect=_record_remediation,
        ):
            result = await dayz_test_tool.execute_dayz_test_run(
                runtime,
                project="ExampleMod",
                mode="client",
                preflight=False,
                run_id=RUN_ID,
                extra_mods=["@DayZ_MCP"],
            )

        launch.assert_not_awaited()
        self.assertEqual(remediations, [])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], steam_preflight.STEAM_SESSION_STALE)
        self.assertNotIn("steam_remediated", result)
        self.assertNotIn("steam_remediation_s", result)
        self.assertIs(
            inspect.signature(dayz_test_tool.execute_dayz_test_run)
            .parameters["auto_remediate_steam"]
            .default,
            False,
        )


def _boot_pbo_bytes(entries: list[str]) -> bytes:
    import struct

    buf = bytearray(b"\x00" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0))
    buf.extend(b"prefix\x00scripts\x00\x00")
    for item in entries:
        buf.extend(item.encode("utf-8") + b"\x00" + struct.pack("<5I", 0, 16, 0, 0, 16))
    buf.extend(b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0))
    return bytes(buf)


def _backdate_file_times(path: str, unix_ts: float) -> None:
    os.utime(path, (unix_ts, unix_ts))
    if os.name == "nt":
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_file_time = kernel32.SetFileTime
        set_file_time.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
        ]
        set_file_time.restype = ctypes.c_int
        ticks = int(unix_ts * 10_000_000) + 116_444_736_000_000_000
        stamp = ctypes.c_uint64(ticks)
        with open(path, "r+b") as handle:
            osf = msvcrt.get_osfhandle(handle.fileno())
            if not set_file_time(
                ctypes.c_void_p(osf),
                ctypes.byref(stamp),
                ctypes.byref(stamp),
                ctypes.byref(stamp),
            ):
                raise ctypes.WinError(ctypes.get_last_error())


class _FakeBootClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = None

    def now(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))


class _TempRuntimeBundle(_Bundle):
    def __init__(
        self,
        sealed_policies: tuple[object, ...],
        *,
        game_directory: str,
    ) -> None:
        super().__init__(sealed_policies)
        self._game_directory = game_directory

    def validated_worker_runtime(self, mod: str, dev_root: str):
        policy = self.sealed_policies[0].policy
        if policy.mod != mod or policy.dev_root != dev_root:
            raise ValueError("runtime_policy_invalid")
        from dayz_mcp.dayz_test_worker import (
            WorkerRuntimePolicy,
            runtime_policy_acceptable,
        )

        built = WorkerRuntimePolicy(
            dev_root=dev_root,
            mod=mod,
            diag_executable=self._game_directory + r"\DayZDiag_x64.exe",
            game_directory=self._game_directory,
            mission_aliases=(
                ("chernarus", dev_root + r"\_server\mpmissions\dayzOffline.chernarusplus"),
                ("livonia", dev_root + r"\_server\mpmissions\dayzOffline.enoch"),
                ("sakhal", dev_root + r"\_server\mpmissions\dayzOffline.sakhal"),
                ("lfheli", dev_root + r"\_server\mpmissions\lfheli"),
            ),
            mods_root=policy.mod_roots[0],
            build_temp_root=dev_root + r"\_build",
            build_source_basename=None,
        )
        if not runtime_policy_acceptable(built):
            raise ValueError("runtime_policy_invalid")
        return built


class DayzTestServerBootTest(unittest.IsolatedAsyncioTestCase):
    _INCOMPLETE_SCRIPT_LOG = (
        '04:36:51.817 SCRIPT       : Module: GameLib; loaded 1x files; 2x classes; defines: "…"\n'
        '04:36:52.464 SCRIPT       : Module: Game; loaded 2x files; 4x classes; defines: "…"\n'
        '04:36:54.618 SCRIPT       : Module: World; loaded 4x files; 8x classes; defines: "…"\n'
    )
    _MISSION_LINE = (
        '04:37:09.995  SCRIPT       : Module: Mission; loaded 3x files; 6x classes; defines: "…"\n'
    )
    _NORMAL_RPT = (
        "=====================================================================\n"
        "Version 1.30.164289.70\n"
        "=====================================================================\n"
    )

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.dev_root = root / "Suite"
        self.game_dir = root / "DayZ"
        self.mods_dir = root / "Mods"
        (self.game_dir / "dta").mkdir(parents=True, exist_ok=True)
        (self.game_dir / "DayZDiag_x64.exe").write_bytes(b"MZ")
        mission_dir = self.dev_root / "_server" / "mpmissions" / "dayzOffline.chernarusplus"
        mission_dir.mkdir(parents=True, exist_ok=True)
        (mission_dir / "init.c").write_text("// init\n", encoding="utf-8")
        (self.game_dir / "dta" / "scripts.pbo").write_bytes(
            _boot_pbo_bytes(
                [
                    r"2_GameLib\g.c",
                    r"3_Game\a.c",
                    r"3_Game\b.c",
                    r"4_World\w1.c",
                    r"4_World\w2.c",
                    r"4_World\w3.c",
                    r"5_Mission\m1.c",
                ]
            )
        )
        (self.mods_dir / "@DayZ_MCP" / "addons").mkdir(parents=True, exist_ok=True)
        (self.mods_dir / "@DayZ_MCP" / "addons" / "dayz_mcp.pbo").write_bytes(
            _boot_pbo_bytes(
                [
                    r"DayZ_MCP\scripts\4_World\mw.c",
                    r"DayZ_MCP\scripts\5_Mission\mm1.c",
                    r"DayZ_MCP\scripts\5_Mission\mm2.c",
                ]
            )
        )
        self.policy = dayz_test_request.RequestProjectPolicy(
            mod="DayZ_MCP",
            dev_root=str(self.dev_root),
            default_source=str(root / "DayZ_MCP_src"),
            default_base_mods=(),
            mission_roots=(str(self.dev_root / "_server" / "mpmissions"),),
            mod_roots=(str(self.mods_dir),),
        )

        steam_patcher = patch.object(
            dayz_test_tool,
            "evaluate_steam_session",
            return_value=steam_preflight.SteamSessionResult(
                error_code=None,
                steam_registered_pid=1,
                steam_live_pids=(1,),
                remediation=steam_preflight.REMEDIATION,
            ),
        )
        steam_patcher.start()
        self.addCleanup(steam_patcher.stop)

        desktop_patcher = patch.object(
            dayz_test_tool,
            "evaluate_prerun_desktop",
            return_value=mcp_capture.PrerunDesktopResult(
                error_code=None,
                desktop="unlocked",
                mean_brightness=80.0,
                nonblack_ratio=0.9,
                waited_s=0.01,
                remediation="",
            ),
        )
        desktop_patcher.start()
        self.addCleanup(desktop_patcher.stop)

        vpp_patcher = patch.object(
            dayz_test_tool,
            "preflight_vpp_request",
            return_value=native_launcher_transaction.VppPreflightResult(
                error_code=None,
                missing=(),
                warnings=(),
                hint=native_launcher_transaction.VPP_PREFLIGHT_HINT,
            ),
        )
        vpp_patcher.start()
        self.addCleanup(vpp_patcher.stop)
        boot_clock = _FakeBootClock()
        clock_patcher = patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=boot_clock.now
        )
        clock_patcher.start()
        self.addCleanup(clock_patcher.stop)
        sleep_patcher = patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=boot_clock.sleep
        )
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    @property
    def server_profiles(self):
        from pathlib import Path

        return Path(self.dev_root) / "_server" / "profiles"

    def _bundle(self) -> _TempRuntimeBundle:
        return _TempRuntimeBundle(
            _sealed(self.policy), game_directory=str(self.game_dir)
        )

    async def _run_tool(
        self,
        *,
        mode: str = "server",
        terminal_payload: dict[str, object] | None = None,
        on_executing=None,
        runtime: _Runtime | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        term = terminal_payload or {
            "cleanup_degraded": False,
            "error_code": None,
            "exit_code": 0,
            "ok": True,
            "run_id": RUN_ID,
        }
        exit_code = int(term["exit_code"])

        async def launch(_raw_request: bytes, **launch_kwargs: object) -> int:
            await launch_kwargs["execution_started_cb"]()
            if on_executing is not None:
                on_executing()
            launch_kwargs["output_sink"]("stdout", _terminal(term))
            return exit_code

        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=self._bundle(),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch,
        ):
            return await dayz_test_tool.execute_dayz_test_run(
                runtime or _Runtime(),
                project=self.policy.mod,
                mode=mode,
                **kwargs,
            )

    async def test_server_boot_is_null_in_client_offline_preflight_and_kill(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        (self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT").write_text(
            self._NORMAL_RPT, encoding="utf-8"
        )
        (self.server_profiles / "script_2026-11-11_04-36-48.log").write_text(
            self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE, encoding="utf-8"
        )

        client_res = await self._run_tool(
            mode="client", connect_host="203.0.113.10"
        )
        self.assertIn("server_boot", client_res)
        self.assertIsNone(client_res["server_boot"])

        preflight_res = await self._run_tool(mode="server", preflight=True)
        self.assertIn("server_boot", preflight_res)
        self.assertIsNone(preflight_res["server_boot"])

        raw_offline, selected = dayz_test_tool.build_run_request(
            _sealed(self.policy), project=self.policy.mod, mode="offline"
        )

        async def launch_ok(_raw: bytes, **launch_kwargs: object) -> int:
            await launch_kwargs["execution_started_cb"]()
            launch_kwargs["output_sink"](
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
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch_ok,
        ):
            offline_res = await dayz_test_tool._execute_request(
                _Runtime(),
                opened_launcher=_Opened(),
                verified_bundle=self._bundle(),
                raw_request=raw_offline,
                policy=selected,
                public_mode="offline",
                artifacts_paths=[],
                started_at=0.0,
                preflight=False,
                expected_run_id=None,
                progress_cb=None,
            )
        self.assertIn("server_boot", offline_res)
        self.assertIsNone(offline_res["server_boot"])

        stop_runtime = _Runtime(
            {
                "runs": [
                    {
                        "run_id": RUN_ID,
                        "state": "RUNNING_IDLE",
                        "mod": "@" + self.policy.mod,
                        "profiles": str(self.server_profiles),
                        "processes": [],
                    }
                ]
            }
        )
        with patch.object(
            dayz_test_tool, "open_approved_launcher", return_value=_Opened()
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "load_verified_bundle",
            return_value=self._bundle(),
        ), patch.object(
            dayz_test_tool.secure_launcher,
            "execute_secure_launcher_request",
            side_effect=launch_ok,
        ):
            kill_res = await dayz_test_tool.execute_dayz_test_stop(
                stop_runtime, RUN_ID
            )
        assert kill_res is not None
        self.assertIn("server_boot", kill_res)
        self.assertIsNone(kill_res["server_boot"])

    async def test_server_mode_reads_only_new_logs_and_returns_null_when_only_old(
        self,
    ) -> None:
        from pathlib import Path
        import time

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        old_rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_03-00-00.RPT"
        old_script = self.server_profiles / "script_2026-11-11_03-00-00.log"
        old_rpt.write_text(
            " 3:00:10.000 [Server] :: termination in: 10\n", encoding="utf-8"
        )
        old_script.write_text(
            "03:00:01.000 SCRIPT    (E): old error\n"
            '03:00:02.000 SCRIPT       : Module: GameLib; loaded 99x files; 1x classes; defines: "…"\n'
            '03:00:09.000  SCRIPT       : Module: Mission; loaded 999x files; 1x classes; defines: "…"\n',
            encoding="utf-8",
        )
        stale_epoch = time.time() - 120.0
        _backdate_file_times(str(old_rpt), stale_epoch)
        _backdate_file_times(str(old_script), stale_epoch)
        self.assertLess(old_rpt.stat().st_ctime, time.time() - 30.0)

        real_read = dayz_test_tool.server_boot.read_log_text
        only_old_reads: list[str] = []

        def spy_only_old_read(path, **kwargs):
            only_old_reads.append(str(Path(path)))
            return real_read(path, **kwargs)

        only_old_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=only_old_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=only_old_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_only_old_read
        ):
            only_old = await self._run_tool(mode="server")
        self.assertEqual(only_old["status"], "succeeded")
        self.assertIsNone(only_old["server_boot"])
        self.assertEqual(only_old_clock.now(), 60.0)
        self.assertNotIn(str(old_rpt), only_old_reads)
        self.assertNotIn(str(old_script), only_old_reads)
        self.assertEqual(only_old_reads, [])

        new_rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        new_script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_current_logs() -> None:
            new_rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            new_script.write_text(
                self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                encoding="utf-8",
            )

        with_new_reads: list[str] = []

        def spy_with_new_read(path, **kwargs):
            with_new_reads.append(str(Path(path)))
            return real_read(path, **kwargs)

        with patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_with_new_read
        ):
            with_new = await self._run_tool(
                mode="server", on_executing=write_current_logs
            )
        self.assertEqual(with_new["status"], "succeeded")
        self.assertNotIn(str(old_rpt), with_new_reads)
        self.assertNotIn(str(old_script), with_new_reads)
        self.assertEqual(with_new_reads, [str(new_rpt), str(new_script)])
        self.assertEqual(
            with_new["server_boot"],
            {
                "modules": {"GameLib": 1, "Game": 2, "World": 4, "Mission": 3},
                "complete": True,
                "script_errors": 0,
                "termination_countdown": False,
                "vanilla_files": {"GameLib": 1, "Game": 2, "World": 3, "Mission": 1},
                "mod_files": {"GameLib": 0, "Game": 0, "World": 1, "Mission": 2},
                "missing_files": {"GameLib": 0, "Game": 0, "World": 0, "Mission": 0},
            },
        )

    async def test_server_mode_waits_two_sleeps_for_mission_line_at_one_second(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_initial() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        clock = _FakeBootClock()

        def after_sleep(step: int) -> None:
            if step == 2:
                script.write_text(
                    self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                    encoding="utf-8",
                )

        clock.on_sleep = after_sleep
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ):
            result = await self._run_tool(mode="server", on_executing=write_initial)

        self.assertEqual(clock.sleeps, [0.5, 0.5])
        self.assertEqual(clock.now(), 1.0)
        self.assertIs(result["server_boot"]["complete"], True)
        self.assertEqual(
            result["server_boot"]["modules"],
            {"GameLib": 1, "Game": 2, "World": 4, "Mission": 3},
        )

    async def test_server_mode_without_mission_honors_server_wait_s_and_caps_at_sixty(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_incomplete() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        short_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=short_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=short_clock.sleep
        ):
            short_res = await self._run_tool(
                mode="server", server_wait_s=3, on_executing=write_incomplete
            )
        self.assertIs(short_res["server_boot"]["complete"], False)
        self.assertEqual(short_clock.now(), 3.0)

        capped_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=capped_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=capped_clock.sleep
        ):
            capped_res = await self._run_tool(
                mode="server", server_wait_s=600, on_executing=write_incomplete
            )
        self.assertIs(capped_res["server_boot"]["complete"], False)
        self.assertEqual(capped_clock.now(), 60.0)

    async def test_server_mode_stops_waiting_when_rpt_has_termination_countdown(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_initial() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        clock = _FakeBootClock()

        def add_countdown_on_first_sleep(step: int) -> None:
            if step == 1:
                rpt.write_text(
                    self._NORMAL_RPT + " 5:55:22.583 [Server] :: termination in: 10\n",
                    encoding="utf-8",
                )

        clock.on_sleep = add_countdown_on_first_sleep
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ):
            result = await self._run_tool(mode="server", on_executing=write_initial)

        self.assertEqual(clock.now(), 0.5)
        self.assertIs(result["server_boot"]["termination_countdown"], True)
        self.assertIs(result["server_boot"]["complete"], False)

    async def test_failed_terminal_readiness_timeout_reads_once_without_sleeping(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_incomplete() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        clock = _FakeBootClock()
        reads: list[str] = []
        real_read = dayz_test_tool.server_boot.read_log_text

        def counting_read(path, **kwargs):
            reads.append(str(path))
            return real_read(path, **kwargs)

        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=counting_read
        ):
            result = await self._run_tool(
                mode="server",
                terminal_payload={
                    "cleanup_degraded": False,
                    "error_code": "readiness_timeout",
                    "exit_code": 1,
                    "ok": False,
                    "run_id": None,
                },
                on_executing=write_incomplete,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "readiness_timeout")
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(reads, [str(rpt), str(script)])
        self.assertIs(result["server_boot"]["complete"], False)

    async def test_unreadable_profile_folder_returns_null_and_preserves_response(
        self,
    ) -> None:
        baseline = await self._run_tool(mode="server")
        self.assertIsNone(baseline["server_boot"])

        self.server_profiles.parent.mkdir(parents=True, exist_ok=True)
        self.server_profiles.write_bytes(b"not-a-directory")
        not_a_dir = await self._run_tool(mode="server")
        self.assertIsNone(not_a_dir["server_boot"])
        self.assertEqual(
            dict(not_a_dir, elapsed_s=0.0),
            dict(baseline, elapsed_s=0.0),
        )

        with patch.object(
            dayz_test_tool,
            "_list_rpt_files",
            side_effect=PermissionError("access denied"),
        ):
            denied = await self._run_tool(mode="server")

        self.assertIsNone(denied["server_boot"])
        baseline_copy = dict(baseline, elapsed_s=0.0)
        denied_copy = dict(denied, elapsed_s=0.0)
        self.assertEqual(denied_copy, baseline_copy)

    async def test_rpt_without_script_log_or_with_non_script_log_is_null_while_empty_script_reports(
        self,
    ) -> None:
        from pathlib import Path

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        failed_terminal = {
            "cleanup_degraded": False,
            "error_code": "readiness_timeout",
            "exit_code": 1,
            "ok": False,
            "run_id": None,
        }

        def write_rpt_only() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")

        fail_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=fail_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=fail_clock.sleep
        ):
            failed_no_script = await self._run_tool(
                mode="server",
                terminal_payload=failed_terminal,
                on_executing=write_rpt_only,
            )
        self.assertEqual(failed_no_script["status"], "failed")
        self.assertEqual(fail_clock.sleeps, [])
        self.assertIsNone(failed_no_script["server_boot"])

        wait_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=wait_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=wait_clock.sleep
        ):
            waited_no_script = await self._run_tool(
                mode="server",
                server_wait_s=2,
                on_executing=write_rpt_only,
            )
        self.assertEqual(waited_no_script["status"], "succeeded")
        self.assertEqual(wait_clock.now(), 2.0)
        self.assertIsNone(waited_no_script["server_boot"])

        other_log = self.server_profiles / "error_2026-11-11_04-36-48.log"

        def write_rpt_and_other_log() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            other_log.write_text(
                "04:36:52.464 SCRIPT    (E): not a script log\n"
                + self._INCOMPLETE_SCRIPT_LOG
                + self._MISSION_LINE,
                encoding="utf-8",
            )

        reads: list[str] = []
        real_read = dayz_test_tool.server_boot.read_log_text

        def spy_read(path, **kwargs):
            reads.append(str(path))
            return real_read(path, **kwargs)

        other_fail_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=other_fail_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=other_fail_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read
        ):
            other_failed = await self._run_tool(
                mode="server",
                terminal_payload=failed_terminal,
                on_executing=write_rpt_and_other_log,
            )
        self.assertIsNone(other_failed["server_boot"])
        self.assertNotIn(str(other_log), reads)

        other_wait_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=other_wait_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=other_wait_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read
        ):
            other_waited = await self._run_tool(
                mode="server",
                server_wait_s=2,
                on_executing=write_rpt_and_other_log,
            )
        self.assertEqual(other_wait_clock.now(), 2.0)
        self.assertIsNone(other_waited["server_boot"])
        self.assertNotIn(str(other_log), reads)

        script = self.server_profiles / "SCRIPT_2026-11-11_04-36-48.log"

        def write_empty_script() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text("", encoding="utf-8")

        empty_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=empty_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=empty_clock.sleep
        ):
            empty_res = await self._run_tool(
                mode="server",
                server_wait_s=1,
                on_executing=write_empty_script,
            )
        self.assertEqual(empty_clock.now(), 1.0)
        self.assertIsNotNone(empty_res["server_boot"])
        self.assertEqual(empty_res["server_boot"]["modules"], {})
        self.assertIs(empty_res["server_boot"]["complete"], False)
        self.assertEqual(empty_res["server_boot"]["missing_files"], {})

        def fail_on_script(path, **kwargs):
            if Path(path) == script:
                raise PermissionError("script log locked")
            return real_read(path, **kwargs)

        with patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=fail_on_script
        ):
            unreadable_script = await self._run_tool(
                mode="server",
                terminal_payload=failed_terminal,
                on_executing=write_empty_script,
            )
        self.assertIsNone(unreadable_script["server_boot"])

    async def test_newer_rpt_pair_appearing_during_wait_is_ignored(self) -> None:
        import time

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt_a = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script_a = self.server_profiles / "script_2026-11-11_04-36-48.log"
        rpt_b = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-55.RPT"
        script_b = self.server_profiles / "script_2026-11-11_04-36-55.log"

        def write_launch_a() -> None:
            rpt_a.write_text(self._NORMAL_RPT, encoding="utf-8")
            script_a.write_text(
                '04:36:51.817 SCRIPT       : Module: GameLib; loaded 1x files; 2x classes; defines: "…"\n',
                encoding="utf-8",
            )

        clock = _FakeBootClock()

        def spawn_launch_b_on_sleep(step: int) -> None:
            if step == 1:
                rpt_b.write_text(
                    self._NORMAL_RPT + " 5:55:22.583 [Server] :: termination in: 10\n",
                    encoding="utf-8",
                )
                script_b.write_text(
                    "04:36:56.000 SCRIPT    (E): foreign launch error\n"
                    + self._INCOMPLETE_SCRIPT_LOG
                    + '04:37:09.995  SCRIPT       : Module: Mission; loaded 999x files; 6x classes; defines: "…"\n',
                    encoding="utf-8",
                )
                newer = time.time() + 10.0
                os.utime(rpt_b, (newer, newer))
                os.utime(script_b, (newer, newer))

        clock.on_sleep = spawn_launch_b_on_sleep
        reads: list[str] = []
        real_read = dayz_test_tool.server_boot.read_log_text

        def spy_read(path, **kwargs):
            reads.append(str(path))
            return real_read(path, **kwargs)

        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read
        ):
            result = await self._run_tool(
                mode="server", server_wait_s=2, on_executing=write_launch_a
            )

        self.assertEqual(clock.now(), 2.0)
        self.assertNotIn(str(rpt_b), reads)
        self.assertNotIn(str(script_b), reads)
        self.assertEqual(result["server_boot"]["modules"], {"GameLib": 1})
        self.assertIs(result["server_boot"]["complete"], False)
        self.assertIs(result["server_boot"]["termination_countdown"], False)
        self.assertEqual(result["server_boot"]["script_errors"], 0)

    async def test_rpt_with_old_ctime_and_recent_mtime_is_rejected(self) -> None:
        import time

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_03-00-00.RPT"
        script = self.server_profiles / "script_2026-11-11_03-00-00.log"
        rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
        script.write_text(
            self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE, encoding="utf-8"
        )
        stale_epoch = time.time() - 120.0
        _backdate_file_times(str(rpt), stale_epoch)
        _backdate_file_times(str(script), stale_epoch)

        touched: list[float] = []

        def touch_mtime_only() -> None:
            now = time.time()
            touched.append(now)
            os.utime(rpt, (now + 5.0, now + 5.0))
            os.utime(script, (now + 5.0, now + 5.0))

        clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ):
            result = await self._run_tool(
                mode="server", server_wait_s=2, on_executing=touch_mtime_only
            )

        self.assertEqual(len(touched), 1)
        stat = rpt.stat()
        self.assertLess(stat.st_ctime, touched[0] - 30.0)
        self.assertGreaterEqual(stat.st_mtime, touched[0])
        self.assertEqual(clock.now(), 2.0)
        self.assertIsNone(result["server_boot"])

    async def test_ok_terminal_polls_when_rpt_pair_or_script_log_appears_after_sleep(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        clock = _FakeBootClock()

        def create_pair_on_first_sleep(step: int) -> None:
            if step == 1:
                rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
                script.write_text(
                    self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                    encoding="utf-8",
                )

        clock.on_sleep = create_pair_on_first_sleep
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ):
            late_pair = await self._run_tool(mode="server")

        self.assertEqual(clock.sleeps, [0.5])
        self.assertEqual(clock.now(), 0.5)
        self.assertIs(late_pair["server_boot"]["complete"], True)
        self.assertEqual(
            late_pair["server_boot"]["modules"],
            {"GameLib": 1, "Game": 2, "World": 4, "Mission": 3},
        )

        script.unlink()
        clock_script = _FakeBootClock()

        def write_rpt_then_late_script() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")

        def create_script_on_first_sleep(step: int) -> None:
            if step == 1:
                script.write_text(
                    self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                    encoding="utf-8",
                )

        clock_script.on_sleep = create_script_on_first_sleep
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock_script.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock_script.sleep
        ):
            late_script = await self._run_tool(
                mode="server", on_executing=write_rpt_then_late_script
            )

        self.assertEqual(clock_script.sleeps, [0.5])
        self.assertEqual(clock_script.now(), 0.5)
        self.assertIs(late_script["server_boot"]["complete"], True)

    async def test_termination_countdown_ends_wait_without_or_with_script_log(
        self,
    ) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZServer_x64_2026-11-10_05-55-15.RPT"
        script = self.server_profiles / "script_2026-11-10_05-55-15.log"
        facts_section_4_rpt = (
            " 5:55:22.582 NETWORK      : server: Player dpid=2 added at playerIndex=0\n"
            " 5:55:22.583 [Server] :: termination in: 10\n"
            " 5:55:23.587 [Server] :: termination in: 9\n"
            " 5:55:24.598 [Server] :: termination in: 8\n"
            " 5:55:25.611 [Server] :: termination in: 7\n"
            " 5:55:26.620 [Server] :: termination in: 6\n"
            " 5:55:27.626 [Server] :: termination in: 5\n"
            " 5:55:28.638 [Server] :: termination in: 4\n"
            " 5:55:29.648 [Server] :: termination in: 3\n"
            " 5:55:30.663 [Server] :: termination in: 2\n"
            " 5:55:31.673 [Server] :: termination in: 1\n"
            " 5:55:32.737 ENGINE       : Destroying game\n"
            " 5:55:32.737  SCRIPT       : ~DayZGame()\n"
            " 5:55:32.859  ENGINE       : Game destroyed.\n"
            " 5:55:32.862 ENGINE       : Destroying game\n"
            " 5:55:32.862  ENGINE       : Game destroyed.\n"
            " 5:55:33.73   --- Termination successfully completed --- \n"
        )

        def write_countdown_rpt_only() -> None:
            rpt.write_text(facts_section_4_rpt, encoding="utf-8")

        no_script_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=no_script_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=no_script_clock.sleep
        ):
            no_script_res = await self._run_tool(
                mode="server", on_executing=write_countdown_rpt_only
            )

        self.assertEqual(no_script_res["status"], "succeeded")
        self.assertEqual(no_script_clock.sleeps, [])
        self.assertEqual(no_script_clock.now(), 0.0)
        self.assertIsNone(no_script_res["server_boot"])

        rpt.unlink()

        def write_countdown_rpt_and_script() -> None:
            rpt.write_text(facts_section_4_rpt, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        with_script_clock = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=with_script_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=with_script_clock.sleep
        ):
            with_script_res = await self._run_tool(
                mode="server", on_executing=write_countdown_rpt_and_script
            )

        self.assertEqual(with_script_res["status"], "succeeded")
        self.assertEqual(with_script_clock.sleeps, [])
        self.assertEqual(with_script_clock.now(), 0.0)
        self.assertIsNotNone(with_script_res["server_boot"])
        self.assertIs(with_script_res["server_boot"]["termination_countdown"], True)
        self.assertIs(with_script_res["server_boot"]["complete"], False)
        self.assertEqual(
            with_script_res["server_boot"]["modules"],
            {"GameLib": 1, "Game": 2, "World": 4},
        )

    async def test_slow_read_past_deadline_stops_without_further_read_pbo_or_sleep(
        self,
    ) -> None:
        from pathlib import Path

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        real_read = dayz_test_tool.server_boot.read_log_text
        real_script_lookup = dayz_test_tool._server_boot_script_log
        real_pbo = dayz_test_tool._measure_server_boot_pbos
        real_to_thread = dayz_test_tool.asyncio.to_thread
        boot_to_thread_calls: list[object] = []

        async def spy_to_thread(func, /, *args, **kwargs):
            if func is not dayz_test_tool.preflight_bridge_contract_request:
                boot_to_thread_calls.append(func)
            return await real_to_thread(func, *args, **kwargs)

        def write_complete() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(
                self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                encoding="utf-8",
            )

        slow_rpt_clock = _FakeBootClock()
        reads: list[str] = []
        script_lookups: list[str] = []
        pbo_calls = 0

        def slow_first_read(path, **kwargs):
            reads.append(str(path))
            text = real_read(path, **kwargs)
            slow_rpt_clock.elapsed += 6.0
            return text

        def spy_script_lookup(rpt_path):
            script_lookups.append(str(rpt_path))
            return real_script_lookup(rpt_path)

        def spy_pbo(*args, **kwargs):
            nonlocal pbo_calls
            pbo_calls += 1
            return real_pbo(*args, **kwargs)

        with patch.object(
            dayz_test_tool.asyncio, "to_thread", side_effect=spy_to_thread
        ), patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=slow_rpt_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=slow_rpt_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=slow_first_read
        ), patch.object(
            dayz_test_tool, "_server_boot_script_log", side_effect=spy_script_lookup
        ), patch.object(
            dayz_test_tool, "_measure_server_boot_pbos", side_effect=spy_pbo
        ):
            expired_res = await self._run_tool(
                mode="server", server_wait_s=5, on_executing=write_complete
            )

        self.assertEqual(reads, [str(rpt)])
        self.assertEqual(script_lookups, [])
        self.assertEqual(pbo_calls, 0)
        self.assertEqual(slow_rpt_clock.sleeps, [])
        self.assertEqual(expired_res["status"], "succeeded")
        self.assertIsNone(expired_res["server_boot"])

        rpt.unlink()
        script.unlink()

        def write_incomplete() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")

        slow_script_clock = _FakeBootClock()
        script_reads: list[str] = []

        def slow_script_read(path, **kwargs):
            script_reads.append(str(path))
            text = real_read(path, **kwargs)
            if Path(path) == script:
                slow_script_clock.elapsed += 6.0
            return text

        with patch.object(
            dayz_test_tool.asyncio, "to_thread", side_effect=spy_to_thread
        ), patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=slow_script_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=slow_script_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=slow_script_read
        ), patch.object(
            dayz_test_tool, "_measure_server_boot_pbos", side_effect=spy_pbo
        ):
            script_expired_res = await self._run_tool(
                mode="server", server_wait_s=5, on_executing=write_incomplete
            )

        self.assertEqual(script_reads, [str(rpt), str(script)])
        self.assertEqual(pbo_calls, 0)
        self.assertEqual(slow_script_clock.sleeps, [])
        self.assertEqual(
            script_expired_res["server_boot"]["modules"],
            {"GameLib": 1, "Game": 2, "World": 4},
        )
        self.assertIsNone(script_expired_res["server_boot"]["vanilla_files"])
        self.assertIsNone(script_expired_res["server_boot"]["mod_files"])

        rpt.unlink()
        script.unlink()

        real_vanilla = dayz_test_tool.server_boot.count_vanilla_files
        real_mod = dayz_test_tool.server_boot.count_mod_files
        slow_vanilla_clock = _FakeBootClock()
        vanilla_reads: list[str] = []
        vanilla_calls: list[str] = []
        mod_calls: list[object] = []

        def spy_read_for_vanilla(path, **kwargs):
            vanilla_reads.append(str(path))
            return real_read(path, **kwargs)

        def slow_vanilla(game_directory):
            vanilla_calls.append(str(game_directory))
            counts = real_vanilla(game_directory)
            slow_vanilla_clock.elapsed += 6.0
            return counts

        def spy_mod_unused(mod_directories):
            mod_calls.append(list(mod_directories))
            return real_mod(mod_directories)

        with patch.object(
            dayz_test_tool.asyncio, "to_thread", side_effect=spy_to_thread
        ), patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=slow_vanilla_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=slow_vanilla_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read_for_vanilla
        ), patch.object(
            dayz_test_tool, "_measure_server_boot_pbos", side_effect=spy_pbo
        ), patch.object(
            dayz_test_tool.server_boot, "count_vanilla_files", side_effect=slow_vanilla
        ), patch.object(
            dayz_test_tool.server_boot, "count_mod_files", side_effect=spy_mod_unused
        ):
            vanilla_expired_res = await self._run_tool(
                mode="server", server_wait_s=5, on_executing=write_incomplete
            )

        self.assertEqual(pbo_calls, 1)
        self.assertEqual(vanilla_calls, [str(self.game_dir)])
        self.assertEqual(mod_calls, [])
        self.assertEqual(vanilla_reads, [str(rpt), str(script)])
        self.assertEqual(slow_vanilla_clock.sleeps, [])
        self.assertEqual(
            vanilla_expired_res["server_boot"],
            {
                "modules": {"GameLib": 1, "Game": 2, "World": 4},
                "complete": False,
                "script_errors": 0,
                "termination_countdown": False,
                "vanilla_files": {"GameLib": 1, "Game": 2, "World": 3, "Mission": 1},
                "mod_files": None,
                "missing_files": None,
            },
        )

        rpt.unlink()
        script.unlink()

        slow_pbo_clock = _FakeBootClock()
        pbo_reads: list[str] = []
        slow_mod_calls: list[object] = []

        def spy_read_for_pbo(path, **kwargs):
            pbo_reads.append(str(path))
            return real_read(path, **kwargs)

        def slow_mod(mod_directories):
            slow_mod_calls.append(list(mod_directories))
            counts = real_mod(mod_directories)
            slow_pbo_clock.elapsed += 6.0
            return counts

        with patch.object(
            dayz_test_tool.asyncio, "to_thread", side_effect=spy_to_thread
        ), patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=slow_pbo_clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=slow_pbo_clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read_for_pbo
        ), patch.object(
            dayz_test_tool, "_measure_server_boot_pbos", side_effect=spy_pbo
        ), patch.object(
            dayz_test_tool.server_boot, "count_mod_files", side_effect=slow_mod
        ):
            pbo_expired_res = await self._run_tool(
                mode="server", server_wait_s=5, on_executing=write_incomplete
            )

        self.assertEqual(pbo_calls, 2)
        self.assertEqual(len(slow_mod_calls), 1)
        self.assertEqual(pbo_reads, [str(rpt), str(script)])
        self.assertEqual(slow_pbo_clock.sleeps, [])
        self.assertIs(pbo_expired_res["server_boot"]["complete"], False)
        self.assertEqual(
            pbo_expired_res["server_boot"]["mod_files"],
            {"GameLib": 0, "Game": 0, "World": 1, "Mission": 2},
        )
        self.assertEqual(
            pbo_expired_res["server_boot"]["missing_files"],
            {"GameLib": 0, "Game": 0, "World": 0},
        )
        self.assertEqual(boot_to_thread_calls, [])

    async def test_script_log_must_match_pinned_rpt_launch_stamp(self) -> None:
        import time

        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt_a = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script_a = self.server_profiles / "script_2026-11-11_04-36-48.log"
        script_b = self.server_profiles / "script_2026-11-11_04-36-55.log"
        foreign_script_b = (
            "04:36:56.000 SCRIPT    (E): foreign launch error\n"
            + self._INCOMPLETE_SCRIPT_LOG
            + '04:37:09.995  SCRIPT       : Module: Mission; loaded 999x files; 6x classes; defines: "…"\n'
        )
        real_read = dayz_test_tool.server_boot.read_log_text
        real_parse = dayz_test_tool.server_boot.parse_script_log

        def write_rpt_a_only() -> None:
            rpt_a.write_text(self._NORMAL_RPT, encoding="utf-8")

        clock_1 = _FakeBootClock()

        def spawn_script_b_on_first_sleep(step: int) -> None:
            if step == 1:
                script_b.write_text(foreign_script_b, encoding="utf-8")
                newer = time.time() + 10.0
                os.utime(script_b, (newer, newer))

        clock_1.on_sleep = spawn_script_b_on_first_sleep
        reads_1: list[str] = []
        parsed_1: list[str] = []

        def spy_read_1(path, **kwargs):
            reads_1.append(str(path))
            return real_read(path, **kwargs)

        def spy_parse_1(text: str):
            parsed_1.append(text)
            return real_parse(text)

        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock_1.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock_1.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read_1
        ), patch.object(
            dayz_test_tool.server_boot, "parse_script_log", side_effect=spy_parse_1
        ):
            res_1 = await self._run_tool(
                mode="server", server_wait_s=2, on_executing=write_rpt_a_only
            )

        self.assertEqual(clock_1.now(), 2.0)
        self.assertNotIn(str(script_b), reads_1)
        self.assertEqual(parsed_1, [])
        self.assertIsNone(res_1["server_boot"])

        rpt_a.unlink()
        script_b.unlink()

        def write_a_and_newer_b() -> None:
            rpt_a.write_text(self._NORMAL_RPT, encoding="utf-8")
            script_a.write_text(
                self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                encoding="utf-8",
            )
            script_b.write_text(foreign_script_b, encoding="utf-8")
            newer = time.time() + 10.0
            os.utime(script_b, (newer, newer))

        clock_2 = _FakeBootClock()
        reads_2: list[str] = []
        parsed_2: list[str] = []

        def spy_read_2(path, **kwargs):
            reads_2.append(str(path))
            return real_read(path, **kwargs)

        def spy_parse_2(text: str):
            parsed_2.append(text)
            return real_parse(text)

        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock_2.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock_2.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "read_log_text", side_effect=spy_read_2
        ), patch.object(
            dayz_test_tool.server_boot, "parse_script_log", side_effect=spy_parse_2
        ):
            res_2 = await self._run_tool(
                mode="server", server_wait_s=2, on_executing=write_a_and_newer_b
            )

        self.assertEqual(dayz_test_tool._script_log_for_server_rpt(rpt_a), script_b)
        self.assertEqual(clock_2.now(), 2.0)
        self.assertNotIn(str(script_b), reads_2)
        self.assertNotIn(str(script_a), reads_2)
        self.assertEqual(parsed_2, [])
        self.assertIsNone(res_2["server_boot"])

        rpt_a.unlink()
        script_a.unlink()
        script_b.unlink()

        def write_a_pair() -> None:
            rpt_a.write_text(self._NORMAL_RPT, encoding="utf-8")
            script_a.write_text(
                self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                encoding="utf-8",
            )

        clock_3 = _FakeBootClock()
        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock_3.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock_3.sleep
        ):
            res_3 = await self._run_tool(
                mode="server", server_wait_s=2, on_executing=write_a_pair
            )

        self.assertEqual(clock_3.sleeps, [])
        self.assertIsNotNone(res_3["server_boot"])
        self.assertIs(res_3["server_boot"]["complete"], True)
        self.assertEqual(
            res_3["server_boot"]["modules"],
            {"GameLib": 1, "Game": 2, "World": 4, "Mission": 3},
        )

        rpt_a.unlink()
        script_a.unlink()
        plain_rpt = self.server_profiles / "DayZDiag_x64.RPT"
        plain_script = self.server_profiles / "script_custom.log"
        plain_rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
        plain_script.write_text(self._INCOMPLETE_SCRIPT_LOG, encoding="utf-8")
        self.assertEqual(dayz_test_tool._server_boot_script_log(plain_rpt), plain_script)
        plain_script.unlink()
        (self.server_profiles / "error.log").write_text("err\n", encoding="utf-8")
        self.assertIsNone(dayz_test_tool._server_boot_script_log(plain_rpt))

    async def test_vanilla_count_past_deadline_skips_mod_count(self) -> None:
        self.server_profiles.mkdir(parents=True, exist_ok=True)
        rpt = self.server_profiles / "DayZDiag_x64_2026-11-11_04-36-48.RPT"
        script = self.server_profiles / "script_2026-11-11_04-36-48.log"

        def write_logs() -> None:
            rpt.write_text(self._NORMAL_RPT, encoding="utf-8")
            script.write_text(
                self._INCOMPLETE_SCRIPT_LOG + self._MISSION_LINE,
                encoding="utf-8",
            )

        clock = _FakeBootClock()
        real_vanilla = dayz_test_tool.server_boot.count_vanilla_files
        real_mod = dayz_test_tool.server_boot.count_mod_files
        vanilla_calls: list[str] = []
        mod_calls: list[object] = []

        def fake_vanilla(game_directory):
            vanilla_calls.append(str(game_directory))
            counts = real_vanilla(game_directory)
            clock.elapsed += 61.0
            return counts

        def spy_mod(mod_directories):
            mod_calls.append(list(mod_directories))
            return real_mod(mod_directories)

        with patch.object(
            dayz_test_tool, "_server_boot_clock", side_effect=clock.now
        ), patch.object(
            dayz_test_tool, "_server_boot_sleep", side_effect=clock.sleep
        ), patch.object(
            dayz_test_tool.server_boot, "count_vanilla_files", side_effect=fake_vanilla
        ), patch.object(
            dayz_test_tool.server_boot, "count_mod_files", side_effect=spy_mod
        ):
            result = await self._run_tool(mode="server", on_executing=write_logs)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(clock.now(), 61.0)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(vanilla_calls, [str(self.game_dir)])
        self.assertEqual(mod_calls, [])
        self.assertEqual(
            result["server_boot"],
            {
                "modules": {"GameLib": 1, "Game": 2, "World": 4, "Mission": 3},
                "complete": True,
                "script_errors": 0,
                "termination_countdown": False,
                "vanilla_files": {"GameLib": 1, "Game": 2, "World": 3, "Mission": 1},
                "mod_files": None,
                "missing_files": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
