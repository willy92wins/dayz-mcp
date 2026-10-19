"""steam_app_view_stale: this app's copy of Steam's ActiveProcess against the real one.

2026-10-16: an agent's own launch of DayZ, from a shell inside the Claude app,
read the app's private copy of ActiveProcess (a Steam gone for ten days) and
stopped at "Unable to locate a running instance of Steam", while dayz_test_run
kept launching. observe_app_steam_view names that state; session_acquire,
session_acquire_wait and session_status carry it as a warning and refuse nothing.
The observation reads WMI, which has no timeout, so the tools wait for it a
bounded time, outside the tool lock, and never run two at once (Codex review
of #239).
"""

from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dayz_mcp import server
from dayz_mcp import steam_preflight as sp
from dayz_mcp.server import ServerConfig, build_app
from tests._tiers import slow_test
from tests.client_helpers import _fixture_client_runtime
from tests.mcp_helpers import _content_json

_LIVE_STEAM = 277748
_GONE_STEAM = 67468
_STEAM_EXE = r"C:\Program Files (x86)\Steam\steam.exe"
_PROCESSES = {_LIVE_STEAM: _STEAM_EXE, 4242: r"C:\Windows\notepad.exe"}


class _View:
    """One registry view of ActiveProcess over the host's process list.

    ``pid`` is what every read returns, or a list read in order (the key
    changing between reads); an exception in its place is raised.
    """

    def __init__(
        self,
        pid: object,
        *,
        processes: dict[int, str] | BaseException = _PROCESSES,
        unavailable: str | None = None,
    ) -> None:
        self.reads = list(pid) if isinstance(pid, list) else None
        self.pid = pid
        self.processes = processes
        self.real_registry_unavailable = unavailable

    def read_active_process(self) -> sp.SteamActiveProcessSnapshot:
        pid = self.reads.pop(0) if self.reads is not None else self.pid
        if isinstance(pid, BaseException):
            raise pid
        return sp.SteamActiveProcessSnapshot(pid=pid, active_user=1)

    def steam_process_pids(self) -> tuple[int, ...]:
        if isinstance(self.processes, BaseException):
            raise self.processes
        return tuple(
            pid
            for pid, image in self.processes.items()
            if image.rsplit("\\", 1)[-1].casefold() == "steam.exe"
        )


class ObserveAppSteamViewTest(unittest.TestCase):
    def test_a_copy_that_does_not_register_the_running_steam_is_named(self) -> None:
        cases = {
            "pid_of_a_steam_that_is_gone": (_GONE_STEAM, _GONE_STEAM),
            # 2026-09-23: pid 0 inside the Claude app, the live Steam outside.
            "pid_zero": (0, 0),
            "pid_now_another_program": (4242, 4242),
            # A tombstone in the copy hides the real key (LL-516).
            "key_absent": (FileNotFoundError(2, "absent"), None),
            "pid_written_as_text": (str(_LIVE_STEAM), None),
            # Not truncated to a DWORD, so not the live Steam's pid.
            "pid_wider_than_a_dword": (2**32 + _LIVE_STEAM, 2**32 + _LIVE_STEAM),
            # REG_MULTI_SZ: winreg returns a list, one per read (Codex review of #239).
            "pid_written_as_a_list": ([[str(_LIVE_STEAM)], [str(_LIVE_STEAM)]], None),
            # REG_NONE or an empty REG_BINARY: winreg returns None (same review).
            "pid_with_no_data": (None, None),
        }
        for name, (copy, app_view_pid) in cases.items():
            with self.subTest(name):
                self.assertEqual(
                    sp.observe_app_steam_view(_View(copy), _View(_LIVE_STEAM)),
                    {
                        "code": sp.STEAM_APP_VIEW_STALE,
                        "app_view_pid": app_view_pid,
                        "real_pid": _LIVE_STEAM,
                        "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
                    },
                )
        # The real pid counts wherever it stands in the whole list, the ninth too.
        nine_steams = {pid: _STEAM_EXE for pid in range(1001, 1010)}
        advisory = sp.observe_app_steam_view(
            _View(_GONE_STEAM, processes=nine_steams), _View(1009, processes=nine_steams)
        )
        self.assertEqual((advisory or {}).get("real_pid"), 1009)

    def test_it_stays_quiet_when_a_dayz_from_this_app_would_find_steam(self) -> None:
        other_steam = {**_PROCESSES, 5150: _STEAM_EXE}
        # More steam.exe than steam_live_pids reports (8): the whole list counts.
        nine_steams = {pid: _STEAM_EXE for pid in range(1001, 1010)}
        cases = {
            "views_agree": (_View(_LIVE_STEAM), _View(_LIVE_STEAM)),
            "copy_names_another_running_steam": (
                _View(5150, processes=other_steam),
                _View(_LIVE_STEAM, processes=other_steam),
            ),
            "copy_names_the_ninth_running_steam": (
                _View(1009, processes=nine_steams),
                _View(1001, processes=nine_steams),
            ),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))

    def test_it_stays_quiet_when_there_is_nothing_sound_to_compare(self) -> None:
        denied = OSError(5, "denied")
        cases = {
            # dayz_test_run names a Steam that is not running itself.
            "real_registers_no_running_steam": (_View(_GONE_STEAM), _View(_GONE_STEAM + 1)),
            "real_registers_pid_zero": (_View(_GONE_STEAM), _View(0)),
            "real_key_absent": (_View(_GONE_STEAM), _View(FileNotFoundError(2, "absent"))),
            "real_key_moving": (_View(_GONE_STEAM), _View([_LIVE_STEAM, 0])),
            # A real-registry read without WMI fell back to this process's own copy.
            "real_registry_unavailable": (
                _View(_GONE_STEAM),
                _View(_LIVE_STEAM, unavailable=sp.REAL_REGISTRY_PYWIN32_MISSING),
            ),
            "real_unreadable": (_View(_GONE_STEAM), _View(denied)),
            "copy_unreadable": (_View(PermissionError(5, "denied")), _View(_LIVE_STEAM)),
            # Steam restarting writes 0, then its new pid, to the one key both
            # views read when there is no copy.
            "copy_changed_meanwhile": (_View([0, _LIVE_STEAM]), _View(_LIVE_STEAM)),
            "real_key_went_back_meanwhile": (
                _View(_GONE_STEAM),
                _View([_LIVE_STEAM, _LIVE_STEAM, _GONE_STEAM, _GONE_STEAM]),
            ),
            "process_list_unreadable": (
                _View(_GONE_STEAM, processes=denied),
                _View(_LIVE_STEAM, processes=denied),
            ),
        }
        for name, (app, real) in cases.items():
            with self.subTest(name):
                self.assertIsNone(sp.observe_app_steam_view(app, real))


class _FakeWinreg:
    """winreg as this process sees it: ActiveProcess holds ``pid``, or is absent."""

    HKEY_CURRENT_USER = object()

    def __init__(self, pid: int | None) -> None:
        self.pid = pid

    def OpenKey(self, _root, _path):  # noqa: N802
        if self.pid is None:
            raise FileNotFoundError(2, "absent")
        return nullcontext(self)

    def QueryValueEx(self, _key, name):  # noqa: N802
        return (self.pid if name == "pid" else 1), 4


class DefaultProvidersTest(unittest.TestCase):
    """observe_app_steam_view() reads the real side through WMI and its own through winreg."""

    def observe(self, wmi: dict, copy_pid: int | None) -> dict | None:
        with (
            patch.object(sp.wmi_host, "read_hkcu_dword", **wmi),
            patch.dict(sys.modules, {"winreg": _FakeWinreg(copy_pid)}),
            patch.object(
                sp.WindowsSteamPreflightProvider,
                "steam_process_pids",
                return_value=(_LIVE_STEAM,),
            ),
        ):
            return sp.observe_app_steam_view()

    def test_wmi_reads_the_running_steam_and_winreg_the_copy(self) -> None:
        for copy_pid, app_view_pid in ((_GONE_STEAM, _GONE_STEAM), (None, None)):
            with self.subTest(copy_pid=copy_pid):
                view = self.observe({"return_value": _LIVE_STEAM}, copy_pid)
                self.assertEqual(
                    (view["app_view_pid"], view["real_pid"]), (app_view_pid, _LIVE_STEAM)
                )

    def test_a_real_read_that_fell_back_to_winreg_compares_nothing(self) -> None:
        cases = {
            "pywin32_missing": {"side_effect": ImportError("pythoncom")},
            "wmi_error": {"side_effect": RuntimeError("com_error")},
            # Not in the real HKCU: the provider reads winreg, this very copy.
            "real_value_absent": {"return_value": None},
        }
        for name, wmi in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.observe(wmi, _GONE_STEAM))


_ADVISORY = {
    "code": sp.STEAM_APP_VIEW_STALE,
    "app_view_pid": _GONE_STEAM,
    "real_pid": _LIVE_STEAM,
    "remediation": sp.REMEDIATION_STEAM_APP_VIEW_STALE,
}
_TOOLS = {
    "session_status": {},
    "session_acquire": {"purpose": "fixture"},
    "session_acquire_wait": {"purpose": "fixture"},
}


class SessionToolsCarryTheAdvisoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        config = ServerConfig(
            mode="client",
            key="fixture",
            port=12345,
            client_platform="codex",
            auto_spawn_daemon=False,
            log_sink=lambda _message: None,
        )
        self.runtime = _fixture_client_runtime(config)
        with patch.object(server, "ClientRuntime", return_value=self.runtime):
            self.app, _built = build_app(config)
        for tool in _TOOLS:
            answer = AsyncMock(
                side_effect=lambda *_a, **_k: {"status": "active", "warnings": ["already_here"]}
            )
            self.enter(patch.object(self.runtime, tool, new=answer))
        self.enter(
            patch.dict(
                server._steam_app_view_cache,
                {"expires": 0.0, "value": None, "pending": None, "started": 0.0},
            )
        )

    def enter(self, patcher) -> None:
        patcher.start()
        self.addCleanup(patcher.stop)

    async def call(self, tool: str) -> dict:
        return _content_json(await self.app.call_tool(tool, _TOOLS[tool]))

    def observed(self, **behaviour):
        return patch.object(server, "observe_app_steam_view", **behaviour)

    def expire(self) -> None:
        server._steam_app_view_cache["expires"] = 0.0

    async def test_each_session_tool_warns_and_keeps_its_answer(self) -> None:
        for tool in _TOOLS:
            with self.subTest(tool), self.observed(return_value=dict(_ADVISORY)):
                self.expire()
                result = await self.call(tool)
                self.assertEqual(result["status"], "active")
                # session_acquire_wait may add tool_registry_stale_reopen_client in between.
                self.assertEqual(result["warnings"][0], "already_here")
                self.assertEqual(result["warnings"][-1], sp.STEAM_APP_VIEW_STALE)
                self.assertEqual(result["steam_app_view"], _ADVISORY)

    async def test_the_observation_runs_in_its_own_daemon_thread_outside_the_tool_lock(
        self,
    ) -> None:
        seen: list[tuple[bool, str, bool]] = []

        def observe() -> dict:
            thread = threading.current_thread()
            seen.append((self.runtime.tool_lock.locked(), thread.name, thread.daemon))
            return dict(_ADVISORY)

        for tool in _TOOLS:
            with self.subTest(tool), self.observed(side_effect=observe):
                seen.clear()
                self.expire()
                await self.call(tool)
                self.assertEqual(seen, [(False, "dayz-mcp-steam-app-view", True)])

    async def test_a_quiet_or_failed_observation_adds_nothing_and_is_kept(self) -> None:
        for name, behaviour in (
            ("quiet", {"return_value": None}),
            ("failed", {"side_effect": RuntimeError("fixture observe")}),
        ):
            with self.subTest(name), self.observed(**behaviour) as observe:
                self.expire()
                for _call in range(2):
                    result = await self.call("session_status")
                    self.assertEqual(result["status"], "active")
                    self.assertNotIn("steam_app_view", result)
                    self.assertNotIn(sp.STEAM_APP_VIEW_STALE, result["warnings"])
                observe.assert_called_once_with()

    async def test_one_observation_serves_the_calls_inside_its_ttl(self) -> None:
        with self.observed(return_value=dict(_ADVISORY)) as observe:
            await self.call("session_status")
            ttl = server._steam_app_view_cache["expires"] - time.monotonic()
            self.assertTrue(25.0 < ttl <= 30.0, ttl)
            second = await self.call("session_status")
            self.assertEqual(second["steam_app_view"], _ADVISORY)
            observe.assert_called_once_with()
            self.expire()
            await self.call("session_status")
            self.assertEqual(observe.call_count, 2)

    async def test_an_observation_that_finishes_meanwhile_is_not_started_again(self) -> None:
        # Its thread fills the cache between this call's freshness check and
        # its look at the pending observation.
        class _FinishesWhenPendingIsRead(dict):
            finished = False

            def __getitem__(self, key):
                if key == "pending" and not self.finished:
                    self.finished = True
                    self.update(
                        value=dict(_ADVISORY), expires=time.monotonic() + 30.0, pending=None
                    )
                return super().__getitem__(key)

        racy = _FinishesWhenPendingIsRead(
            expires=0.0, value=None, pending=object(), started=0.0
        )
        with (
            patch.object(server, "_steam_app_view_cache", racy),
            self.observed(return_value=None) as observe,
        ):
            result = await self.call("session_status")
        self.assertEqual(result["steam_app_view"], _ADVISORY)
        observe.assert_not_called()

    @slow_test
    async def test_a_hung_observation_holds_no_tool_and_is_not_started_again(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def hung() -> dict:
            release.wait(10)
            return dict(_ADVISORY)

        with self.observed(side_effect=hung) as observe:
            began = time.monotonic()
            first = await self.call("session_status")
            # The 2 s wait, not the query's 10 s, nor a wait twice as long.
            self.assertLess(time.monotonic() - began, 3.5)
            second = await self.call("session_acquire")
            third = await self.call("session_acquire_wait")
            for result in (first, second, third):
                self.assertEqual(result["status"], "active")
                self.assertNotIn("steam_app_view", result)
            observe.assert_called_once_with()
            threads = [
                thread
                for thread in threading.enumerate()
                if thread.name == "dayz-mcp-steam-app-view"
            ]
            self.assertEqual([thread.daemon for thread in threads], [True])
            # Calls past the wait do not wrap the hung future again: only the
            # first call's callback is on it (concurrent.futures keeps them in
            # _done_callbacks).
            pending = server._steam_app_view_cache["pending"]
            self.assertEqual(len(pending._done_callbacks), 1)
            # When the query comes back, its answer serves the next calls.
            release.set()
            await asyncio.wait_for(asyncio.wrap_future(pending), 5)
            after = await self.call("session_status")
            self.assertEqual(after["steam_app_view"], _ADVISORY)
            observe.assert_called_once_with()

    @slow_test
    async def test_concurrent_calls_share_one_observation(self) -> None:
        go = threading.Event()
        self.addCleanup(go.set)

        def slow() -> dict:
            go.wait(5)
            return dict(_ADVISORY)

        with (
            patch.object(server, "_STEAM_APP_VIEW_WAIT_S", 10.0),
            self.observed(side_effect=slow) as observe,
        ):
            calls = [asyncio.ensure_future(self.call(tool)) for tool in _TOOLS]
            deadline = time.monotonic() + 5
            while observe.call_count == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            go.set()
            results = await asyncio.gather(*calls)
        observe.assert_called_once_with()
        for result in results:
            self.assertEqual(result["steam_app_view"], _ADVISORY)


# For each .ps1 in DIR, read by PowerShell's own parser: its parse errors; every
# assignment to the preference (with 1 when it is a statement of the script's own
# body); every variable that names it; what could run before that assignment (a
# statement other than a variable set to a constant or a message or Set-StrictMode
# with plain arguments, a default or an attribute argument that is not a constant, a
# begin, process, dynamicparam or clean block, using, #requires that loads
# something); every call to reg, by offset; and whatever this reading does not show
# to leave variables alone. The script may use what it uses today and nothing else:
# the kinds of statement and expression in $syntax (the hashtable a splat takes
# besides), the commands in $allowed (the cmdlets that only write a message and
# Set-StrictMode besides), each named as written, and the members in $members. A
# command named at run time or dot-sourced, a -*Variable parameter, a splat that is
# not a hashtable of constant keys set once before it, ForEach-Object or
# Where-Object given anything but script blocks, an assignment to a drive,
# $ExecutionContext, $PSCmdlet, $PSDefaultParameterValues and [scriptblock] are
# refused. The commands that write items take their paths from variables set once,
# before them, to a path in the registry or under a folder of the environment, or
# from Get-ChildItem given such a path.
_PREFERENCE_PROBE = r"""
$alias = @{}
Get-Alias | ForEach-Object { $alias[$_.Name] = $_.Definition }
$allowed = 'Copy-Item', 'ForEach-Object', 'Get-AppxPackage', 'Get-ChildItem', 'Get-Date', 'Get-ItemProperty',
    'Get-Process', 'Join-Path', 'New-Item', 'New-Object', 'Out-Null', 'Remove-Item', 'Set-StrictMode',
    'Start-Sleep', 'Test-Path', 'Where-Object', 'Write-Debug', 'Write-Host', 'Write-Information',
    'Write-Verbose', 'Write-Warning', 'reg'
$early = 'Set-StrictMode', 'Write-Debug', 'Write-Host', 'Write-Information', 'Write-Verbose', 'Write-Warning'
$writers = 'Copy-Item', 'New-Item', 'Remove-Item'
$syntax = 'AssignmentStatementAst', 'BinaryExpressionAst', 'CommandAst', 'CommandExpressionAst',
    'CommandParameterAst', 'ConstantExpressionAst', 'ExpandableStringExpressionAst', 'ForStatementAst',
    'HashtableAst', 'IfStatementAst', 'InvokeMemberExpressionAst', 'MemberExpressionAst', 'NamedBlockAst',
    'ParamBlockAst', 'ParameterAst', 'ParenExpressionAst', 'PipelineAst', 'ScriptBlockAst',
    'ScriptBlockExpressionAst', 'StatementBlockAst', 'StringConstantExpressionAst', 'SubExpressionAst',
    'ThrowStatementAst', 'TryStatementAst', 'TypeConstraintAst', 'TypeExpressionAst', 'UnaryExpressionAst',
    'VariableExpressionAst'
$members = 'ActiveUser', 'Administrator', 'Collect', 'GetCurrent', 'Id', 'InstallLocation', 'IsInRole', 'Name',
    'OrdinalIgnoreCase', 'PackageFamilyName', 'Path', 'pid', 'PSChildName', 'StartsWith', 'WaitForPendingFinalizers'
$variableParameter = '^(?:[oewip]v$|outv|errorv|warningv|informationv|pi|p$)'
$constant = {
    param($e)
    $null -eq $e -or $e -is [System.Management.Automation.Language.ConstantExpressionAst] -or
        ($e -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $e.VariablePath.UserPath -in 'true', 'false', 'null')
}
# A constant, or a string that only reads variables.
$plain = {
    param($e)
    (& $constant $e) -or ($e -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
        -not @($e.NestedExpressions | Where-Object { $_ -isnot [System.Management.Automation.Language.VariableExpressionAst] }).Count)
}
$nameOf = {
    param($command)
    $head = $command.CommandElements[0]
    if ($command.InvocationOperator -eq 'Dot' -or $head -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { return '' }
    if (($head.Value -replace '^.*[\\/]', '') -match '^reg(\.exe)?$') { return 'reg' }
    if ($alias.ContainsKey($head.Value)) { return $alias[$head.Value] }
    $head.Value
}
$keyOf = { param($variable) $variable.VariablePath.UserPath -replace '^(?:script|local|private|global):', '' }
# True when $assignment, a statement of a block that holds $node, ends before it.
$dominates = {
    param($assignment, $node)
    if ($assignment.Extent.EndOffset -gt $node.Extent.StartOffset) { return $false }
    for ($up = $node.Parent; $up; $up = $up.Parent) {
        if ($up -eq $assignment.Parent) { return $true }
    }
    $false
}
foreach ($file in Get-ChildItem -LiteralPath 'DIR' -Filter '*.ps1' | Sort-Object Name) {
    $tag = $file.BaseName
    $tokens = $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    "$tag errors $($errors.Count)"
    if ($errors.Count) { continue }
    foreach ($node in $ast.FindAll({ $true }, $true)) {
        if ($node.GetType().Name -notin $syntax) { "$tag late $($node.Extent.StartOffset) $($node.GetType().Name)" }
    }
    $sets = @($ast.FindAll({ param($n)
        $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and @($n.Left.FindAll({ param($v)
            $v -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $v.VariablePath.UserPath -match '(^|:)PSNativeCommandUseErrorActionPreference$' }, $true)).Count }, $true))
    $sets | ForEach-Object { "$tag set $([int]($_.Parent -eq $ast.EndBlock)) $($_.Extent.StartOffset) $($_.Extent.Text)" }
    $variables = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] }, $true))
    $variables | Where-Object { $_.VariablePath.UserPath -match '(^|:)PSNativeCommandUseErrorActionPreference$' } |
        ForEach-Object { "$tag name $($_.Extent.StartOffset)" }
    $first = if ($sets.Count) { $sets[0].Extent.StartOffset } else { [int]::MaxValue }
    foreach ($statement in $ast.EndBlock.Statements) {
        if ($statement.Extent.StartOffset -ge $first) { break }
        $command = if ($statement -is [System.Management.Automation.Language.PipelineAst] -and
            $statement.PipelineElements.Count -eq 1) { $statement.PipelineElements[0] }
        if ($command -is [System.Management.Automation.Language.CommandAst] -and (& $nameOf $command) -in $early -and
                -not @($command.CommandElements | Select-Object -Skip 1 | Where-Object {
                    -not (& $plain $(if ($_ -is [System.Management.Automation.Language.CommandParameterAst]) { $_.Argument } else { $_ })) }).Count) {
            continue
        }
        if (-not ($statement -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                $statement.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $statement.Right -is [System.Management.Automation.Language.CommandExpressionAst] -and
                (& $constant $statement.Right.Expression))) {
            "$tag early $($statement.Extent.StartOffset) statement"
        }
    }
    if ($ast.ParamBlock) {
        foreach ($parameter in $ast.ParamBlock.Parameters) {
            if (-not (& $constant $parameter.DefaultValue)) { "$tag early $($parameter.Extent.StartOffset) default" }
        }
        foreach ($attribute in $ast.ParamBlock.FindAll({ param($n) $n -is [System.Management.Automation.Language.AttributeAst] }, $true)) {
            foreach ($argument in @($attribute.PositionalArguments) + @($attribute.NamedArguments | ForEach-Object { $_.Argument })) {
                if (-not (& $constant $argument)) { "$tag early $($attribute.Extent.StartOffset) attribute" }
            }
        }
    }
    foreach ($block in 'DynamicParamBlock', 'BeginBlock', 'ProcessBlock', 'CleanBlock') {
        if ($ast.PSObject.Properties[$block] -and $ast.$block) { "$tag early 0 $block" }
    }
    if ($ast.UsingStatements.Count) { "$tag early 0 using" }
    $requires = $ast.ScriptRequirements
    if ($requires -and ($requires.RequiredModules.Count -or $requires.RequiredAssemblies.Count -or $requires.RequiresPSSnapIns.Count)) {
        "$tag early 0 requires"
    }
    $assignments = @{}
    foreach ($assignment in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true)) {
        foreach ($variable in $assignment.Left.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)) {
            $assignments[(& $keyOf $variable)] += @($assignment)
            if ($variable.VariablePath.IsDriveQualified) { "$tag late $($variable.Extent.StartOffset) drive" }
        }
    }
    # The variables set once to a path in the registry or under a folder of the
    # environment, by a statement that runs before each use they are shown to have.
    $paths = @{}
    do {
        $grew = $false
        foreach ($name in @($assignments.Keys)) {
            $only = @($assignments[$name])
            if ($paths.ContainsKey($name) -or $only.Count -ne 1 -or $only[0].Operator -ne 'Equals' -or
                    $only[0].Left -isnot [System.Management.Automation.Language.VariableExpressionAst]) { continue }
            $right = $only[0].Right
            $base = $null
            if ($right -is [System.Management.Automation.Language.CommandExpressionAst] -and
                    ($right.Expression -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
                    $right.Expression -is [System.Management.Automation.Language.ExpandableStringExpressionAst])) {
                $text = $right.Expression
                if ($text.Value -like 'Registry::*') { $paths[$name] = $only[0]; $grew = $true; continue }
                if ($text -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -and
                        $text.NestedExpressions.Count -and
                        $text.NestedExpressions[0].Extent.StartOffset -eq $text.Extent.StartOffset + 1) {
                    $base = $text.NestedExpressions[0]
                }
            }
            elseif ($right -is [System.Management.Automation.Language.PipelineAst] -and $right.PipelineElements.Count -eq 1 -and
                    $right.PipelineElements[0] -is [System.Management.Automation.Language.CommandAst] -and
                    (& $nameOf $right.PipelineElements[0]) -eq 'Join-Path') {
                $base = $right.PipelineElements[0].CommandElements[1]
            }
            if ($base -is [System.Management.Automation.Language.VariableExpressionAst] -and -not $base.Splatted -and (
                    $base.VariablePath.DriveName -eq 'env' -or
                    ($paths.ContainsKey(($key = & $keyOf $base)) -and (& $dominates $paths[$key] $only[0])))) {
                $paths[$name] = $only[0]
                $grew = $true
            }
        }
    } while ($grew)
    $isPath = {
        param($part, $source)
        $part -is [System.Management.Automation.Language.VariableExpressionAst] -and -not $part.Splatted -and
            $paths.ContainsKey(($key = & $keyOf $part)) -and (& $dominates $paths[$key] $source)
    }
    foreach ($command in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        $name = & $nameOf $command
        if (-not $name) {
            "$tag late $($command.Extent.StartOffset) dynamic"
            continue
        }
        if ($name -notin $allowed) { "$tag late $($command.Extent.StartOffset) $name" }
        if ($name -eq 'reg') { "$tag reg $($command.Extent.StartOffset)" }
        foreach ($element in $command.CommandElements) {
            if ($element -is [System.Management.Automation.Language.CommandParameterAst] -and
                    $element.ParameterName -match $variableParameter) {
                "$tag late $($element.Extent.StartOffset) -$($element.ParameterName)"
            }
        }
        if ($name -in 'ForEach-Object', 'Where-Object' -and @($command.CommandElements | Select-Object -Skip 1 | Where-Object {
                $_ -isnot [System.Management.Automation.Language.ScriptBlockExpressionAst] }).Count) {
            "$tag late $($command.Extent.StartOffset) $name"
        }
        if ($name -notin $writers) { continue }
        $sources = @($command)
        $elements = @($command.Parent.PipelineElements)
        $at = [array]::IndexOf($elements, $command)
        if ($at -gt 0) {
            if ($at -gt 1 -or $elements[0] -isnot [System.Management.Automation.Language.CommandAst] -or
                    (& $nameOf $elements[0]) -ne 'Get-ChildItem') {
                "$tag late $($command.Extent.StartOffset) input"
            }
            else { $sources += $elements[0] }
        }
        foreach ($source in $sources) {
            $parts = $source.CommandElements
            for ($i = 1; $i -lt $parts.Count; $i++) {
                $part = $parts[$i]
                $parameter = if ($part -is [System.Management.Automation.Language.CommandParameterAst]) { $part }
                    elseif ($parts[$i - 1] -is [System.Management.Automation.Language.CommandParameterAst] -and
                        $null -eq $parts[$i - 1].Argument) { $parts[$i - 1] }
                if ($part -is [System.Management.Automation.Language.CommandParameterAst]) {
                    if ($null -eq $part.Argument) { continue }
                    $part = $part.Argument
                }
                if ($parameter -and $parameter.ParameterName -in 'ItemType', 'Filter' -and
                        $part -is [System.Management.Automation.Language.StringConstantExpressionAst]) { continue }
                if (-not (& $isPath $part $source)) { "$tag late $($part.Extent.StartOffset) path" }
            }
        }
    }
    foreach ($variable in $variables) {
        if (($variable.VariablePath.UserPath -replace '^.*:', '') -in 'ExecutionContext', 'PSCmdlet', 'PSDefaultParameterValues') {
            "$tag late $($variable.Extent.StartOffset) $($variable.Extent.Text)"
        }
        if (-not $variable.Splatted) { continue }
        $table = @($assignments[(& $keyOf $variable)])
        $pairs = $null
        if ($table.Count -eq 1 -and $table[0] -and $table[0].Operator -eq 'Equals' -and
                $table[0].Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                $table[0].Right -is [System.Management.Automation.Language.CommandExpressionAst] -and
                $table[0].Right.Expression -is [System.Management.Automation.Language.HashtableAst] -and
                (& $dominates $table[0] $variable)) {
            $pairs = @($table[0].Right.Expression.KeyValuePairs)
        }
        if ($null -eq $pairs -or @($pairs | Where-Object {
                $_.Item1 -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
                $_.Item1.Value -match $variableParameter }).Count) {
            "$tag late $($variable.Extent.StartOffset) $($variable.Extent.Text)"
        }
    }
    foreach ($member in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.MemberExpressionAst] }, $true)) {
        if ($member.Member -isnot [System.Management.Automation.Language.StringConstantExpressionAst] -or
                $member.Member.Value -notin $members) {
            "$tag late $($member.Extent.StartOffset) member"
        }
    }
    foreach ($type in $ast.FindAll({ param($n)
            $n -is [System.Management.Automation.Language.TypeExpressionAst] -or
            $n -is [System.Management.Automation.Language.TypeConstraintAst] }, $true)) {
        if ($type.TypeName.FullName -match '^(System\.Management\.Automation\.)?ScriptBlock$') {
            "$tag late $($type.Extent.StartOffset) scriptblock"
        }
    }
}
"""

_ASSIGNMENT = "$PSNativeCommandUseErrorActionPreference = $false"


def _probe_scripts(scripts: dict[str, str]) -> dict[str, list[tuple[str, str]]] | None:
    """(kind, rest) of each probe line, by script; None without PowerShell."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        return None
    with tempfile.TemporaryDirectory() as folder:
        for tag, text in scripts.items():
            Path(folder, tag + ".ps1").write_text(text, encoding="utf-8")
        probe = _PREFERENCE_PROBE.replace("DIR", folder.replace("'", "''"))
        result = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(probe.encode("utf-16-le")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    facts: dict[str, list[tuple[str, str]]] = {tag: [] for tag in scripts}
    for line in result.stdout.splitlines():
        tag, kind, rest = (line.split(" ", 2) + [""])[:3]
        facts[tag].append((kind, rest))
    return facts


def _preference_problems(facts: list[tuple[str, str]]) -> list[str]:
    lines: dict[str, list[str]] = {}
    for kind, rest in facts:
        lines.setdefault(kind, []).append(rest)
    if lines.get("errors") != ["0"]:
        return [f"parse errors: {lines.get('errors')}"]
    sets = [rest.split(" ", 2) for rest in lines.get("set", [])]
    if [(top, text) for top, _offset, text in sets] != [("1", _ASSIGNMENT)]:
        return [f"not one {_ASSIGNMENT} in the script's own body: {sets}"]
    at = int(sets[0][1])
    problems = []
    names = [int(rest) for rest in lines.get("name", [])]
    if names != [at]:
        problems.append(f"the preference is named at {names}")
    problems += [f"could run before the assignment: {rest}" for rest in lines.get("early", [])]
    problems += [f"not shown to leave the preference alone: {rest}" for rest in lines.get("late", [])]
    reg = [int(rest) for rest in lines.get("reg", [])]
    if not reg or min(reg) < at:
        problems.append(f"reg calls at {reg}")
    return problems


def _swap(script: str, old: str, new: str) -> str:
    if script.count(old) != 1:
        raise AssertionError(f"not found once in the script: {old!r}")
    return script.replace(old, new)


_SET = _ASSIGNMENT + "\n"
_NAME_IN_PIECES = "('PSNative' + 'CommandUseErrorActionPreference')"
_SAVE = "$callerPreferences = Get-Variable -Name '*Preference' -Scope 1\n"
_RESTORE = (
    "$callerPreferences | ForEach-Object { Set-Variable -Name $_.Name -Value $_.Value -Scope Script }\n"
)


def _preference_harmless_edits(script: str) -> dict[str, str]:
    return {
        "a_comment_before_it": _swap(script, _SET, "# Nothing above this line runs a command.\n" + _SET),
        "a_constant_before_it": _swap(script, _SET, "$unloadAttempts = 5\n" + _SET),
        "strict_mode_after_it": _swap(script, _SET, _SET + "Set-StrictMode -Version 2.0\n"),
        "a_message_after_it": _swap(
            script, _SET, _SET + "Write-Verbose 'reg.exe is judged by its exit code alone'\n"
        ),
        # Codex review of #251, Q7 and Q11: strings that name the preference or
        # the Variable: drive are data, a hashtable of constant keys may be
        # splatted, and Set-StrictMode may come first.
        "messages_that_name_it": _swap(
            script,
            _SET,
            _SET
            + "Write-Verbose 'PSNativeCommandUseErrorActionPreference is disabled'\n"
            + "$note = 'Nothing here reads the Variable: drive'\n"
            + "Write-Verbose $note\n",
        ),
        "a_constant_splat": _swap(
            script,
            _SET,
            _SET
            + "$verboseArguments = @{ Message = 'reg.exe is judged by its exit code alone' }\n"
            + "Write-Verbose @verboseArguments\n",
        ),
        "strict_mode_before_it": _swap(script, _SET, "Set-StrictMode -Version 2.0\n" + _SET),
    }


def _preference_mutants(script: str) -> dict[str, str]:
    def first(text: str) -> str:
        return _swap(script, _SET, text + _SET)

    def later(text: str) -> str:
        return _swap(script, "finally {\n", "finally {\n    " + text + "\n")

    valve = "        Write-Host 'Valve keys in the copy:'\n"
    return {
        # The assignment itself (Codex review of #240, U1).
        "removed": _swap(script, _SET, ""),
        "under_an_if": _swap(script, _SET, "if ($true) { " + _ASSIGNMENT + " }\n"),
        # Something that runs before it (Codex reviews of #241, W4, and #251, Q1).
        "reg_through_a_variable_first": first(
            "$registryTool = 'reg.exe'\n& $registryTool query HKCU\\Software | Out-Null\n"
        ),
        "reg_by_its_full_path_first": first(
            "$registryTool = Join-Path $env:SystemRoot 'System32\\reg.exe'\n"
            "& $registryTool query HKCU\\Software | Out-Null\n"
        ),
        "a_default_that_runs_reg": _swap(
            script, "'Claude_pzs8sxrjxfjjc'\n)", "(& reg.exe query HKCU\\Software | Out-String)\n)"
        ),
        "a_begin_block": _swap(
            script,
            "'Claude_pzs8sxrjxfjjc'\n)\n",
            "'Claude_pzs8sxrjxfjjc'\n)\nbegin { & reg.exe query HKCU\\Software | Out-Null }\nend {\n",
        )
        + "}\n",
        # Something that sets it back later, by name or not (Codex reviews of #240,
        # V1, #241, W3, and #251, Q6).
        "set_back": later("$PSNativeCommandUseErrorActionPreference = $true"),
        "braced": later("${PSNativeCommandUseErrorActionPreference} = $true"),
        "script_scope": later("$script:PSNativeCommandUseErrorActionPreference = $true"),
        "incremented": later("$PSNativeCommandUseErrorActionPreference++"),
        "restored_in_the_finally": _swap(later(_RESTORE), _SET, _SET + _SAVE),
        "restored_in_the_valve_branch": _swap(
            _swap(script, valve, valve + "        " + _RESTORE), _SET, _SET + _SAVE
        ),
        "an_alias": later(f"sv -Name {_NAME_IN_PIECES} -Value $true"),
        "the_variable_drive": later(f"Set-Item -Path ('Variable:' + {_NAME_IN_PIECES}) -Value $true"),
        "the_session_state": later(f"$ExecutionContext.SessionState.PSVariable.Set({_NAME_IN_PIECES}, $true)"),
        "out_variable": later(f"Get-Date -OutVariable {_NAME_IN_PIECES} | Out-Null"),
        "splatted": later(
            f"$dateArguments = @{{ OutVariable = {_NAME_IN_PIECES} }}\n    Get-Date @dateArguments | Out-Null"
        ),
        "default_parameter_values": later(f"$PSDefaultParameterValues['Get-Date:OutVariable'] = {_NAME_IN_PIECES}"),
        "invoke_expression": later(
            "Invoke-Expression ('$script:PSNative' + 'CommandUseErrorActionPreference = $true')"
        ),
        "a_script_block_from_text": later(
            ". ([scriptblock]::Create('$script:PSNative' + 'CommandUseErrorActionPreference = $true'))"
        ),
        # A command, member or path this reading does not follow (Codex review of
        # #251, Q8).
        "the_variable_drive_in_pieces": later(
            f"Set-Item -LiteralPath ('Vari' + 'able:' + {_NAME_IN_PIECES}) -Value $true"
        ),
        "removed_from_the_variable_drive": later(f"Remove-Item -LiteralPath ('Vari' + 'able:' + {_NAME_IN_PIECES})"),
        "new_item_on_the_variable_drive": later(
            f"New-Item -Path ('Vari' + 'able:') -Name {_NAME_IN_PIECES} -Value $true -Force | Out-Null"
        ),
        "removed_through_the_pipeline": later(
            f"Get-ChildItem -LiteralPath ('Vari' + 'able:' + {_NAME_IN_PIECES}) | Remove-Item"
        ),
        "a_path_set_twice": _swap(
            script,
            "            Remove-Item -LiteralPath $valve",
            f"            $valve = 'Vari' + 'able:' + {_NAME_IN_PIECES}\n            Remove-Item -LiteralPath $valve",
        ),
        "a_path_used_before_it_is_set": _swap(
            script, _SET, _SET + "Remove-Item -LiteralPath $valve -Recurse -Force\n"
        ),
        "the_environment_changed": _swap(script, _SET, _SET + "$env:USERPROFILE = 'C:\\Elsewhere'\n"),
        "the_runspace": later(
            f"[runspace]::DefaultRunspace.SessionStateProxy.SetVariable({_NAME_IN_PIECES}, $true)"
        ),
        "a_member_by_name": later(
            f"Get-ChildItem -LiteralPath ('Vari' + 'able:' + {_NAME_IN_PIECES}) | ForEach-Object -MemberName set_Value -ArgumentList $true"
        ),
        "a_splat_changed_later": later(
            "$verboseArguments = @{ Message = 'reg.exe is judged by its exit code alone' }\n"
            f"    $verboseArguments['OutVariable'] = {_NAME_IN_PIECES}\n"
            "    Write-Verbose @verboseArguments"
        ),
        "a_splat_before_its_table": later(
            "Write-Verbose @verboseArguments\n"
            "    $verboseArguments = @{ Message = 'reg.exe is judged by its exit code alone' }"
        ),
        # A statement that sets a variable without an assignment.
        "a_data_section": later("data PSNativeCommandUseErrorActionPreference { $true }"),
        "a_loop_over_a_path": _swap(
            script,
            "            Remove-Item -LiteralPath $valve",
            f"            foreach ($valve in ('Vari' + 'able:' + {_NAME_IN_PIECES})) {{ }}\n"
            "            Remove-Item -LiteralPath $valve",
        ),
    }


class ClearAppSteamShadowScriptTest(unittest.TestCase):
    """tools/clear-app-steam-shadow.ps1, read by PowerShell's own parser: nothing runs it."""

    def test_reg_exe_is_judged_by_its_exit_code_alone(self) -> None:
        # Fifth Codex review of #239 (T1): in PowerShell 7.3 and later, a caller's
        # $PSNativeCommandUseErrorActionPreference = $true makes a failed `reg unload`
        # throw, which skips its retries and its warning. The script turns it off at
        # script scope, before its first reg.exe. Read as lines of text, an assignment
        # that never runs (under an if, in a function, a comment or a script block) and
        # a later one back to $true passed too (Codex review of #240, U1). So did a
        # later Set-Variable or ${...} assignment, and a reg call spelled other than
        # reg.exe before the assignment (third Codex review of #240, V1 and V2), a
        # finally that put a caller's value back through Get-Variable and Set-Variable,
        # and a reg.exe called through a variable before the assignment (Codex review
        # of #241, W3 and W4). Nothing may run before the assignment now, and nothing
        # in the script may set a variable without naming it. A test that ran the
        # script with fakes in place of its registry commands missed a reg.exe called
        # by its full path, which would reach the real registry, and a restore in the
        # branch its fakes never took (Codex review of #251, Q1 and Q6), so nothing
        # runs it. A list of the ways to set a variable without naming it missed one,
        # a path to the Variable: drive built in pieces (Codex review of #251, Q8), so
        # the script is now held to what it uses today: the kinds of statement, the
        # commands and the members on the probe's lists, and for the commands that
        # write items, paths in the registry or under a folder of the environment. An
        # edit that needs more fails here until the lists grow, and whoever grows them
        # shows that the new entry cannot set a variable. The folders of the
        # environment and the commands PowerShell loads are trusted.
        script = (Path(__file__).resolve().parents[1] / "clear-app-steam-shadow.ps1").read_text(
            encoding="utf-8"
        )
        scripts = {"as_is": script}
        scripts.update({"h_" + label: text for label, text in _preference_harmless_edits(script).items()})
        scripts.update({"m_" + label: text for label, text in _preference_mutants(script).items()})
        facts = _probe_scripts(scripts)
        if facts is None:
            self.skipTest("PowerShell is not installed")
        for tag, text in scripts.items():
            with self.subTest(script=tag):
                self.assertTrue(tag == "as_is" or text != script)
                problems = _preference_problems(facts[tag])
                if tag.startswith("m_"):
                    self.assertNotEqual(problems, [])
                else:
                    self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
