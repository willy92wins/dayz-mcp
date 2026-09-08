"""Offline source contracts for N1; GUARDS_SOURCE_DIR selects unmodified BEFOREs.

These tests do not compile or execute Enforce. The fixed capacity oracle comes
from the existing daemon ingress (64 commands) and accounts for accepted jobs,
not merely the number of POSTs currently in flight. No live services are used.
"""
from __future__ import annotations

import ast
import itertools
import os
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "addon/scripts/5_Mission"
TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')


def _clean(source: str, *, strings: bool = False) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group()
        if value.startswith('"') and not strings:
            return value
        return ''.join('\n' if char == '\n' else ' ' for char in value)
    return TOKENS.sub(replace, source)


def _body(source: str, signature: str) -> str:
    source = _clean(source)
    assert signature in source, f"missing executable block: {signature}"
    masked = _clean(source, strings=True)
    brace = masked.index('{', source.index(signature))
    depth = 0
    for index in range(brace, len(masked)):
        depth += (masked[index] == '{') - (masked[index] == '}')
        if depth == 0:
            return source[brace + 1:index]
    raise AssertionError(f"unterminated block: {signature}")


def _source(name: str) -> str:
    directory = Path(os.environ.get('GUARDS_SOURCE_DIR', str(SCRIPTS)))
    path = directory / name
    if not path.exists():
        path = directory / (name + '.BEFORE')
    return _clean(path.read_text(encoding='utf-8-sig'))


def _constant(source: str, name: str) -> int:
    match = re.search(r'const int ' + name + r'\s*=\s*(\d+)\s*;', source)
    assert match, f"missing integer capacity: {name}"
    return int(match[1])


class BridgeGuardsTest(unittest.TestCase):
    def test_g1_unavailable_result_logs_once_before_return(self) -> None:
        source = _source('MCPBridge.c')
        self.assertIn('protected bool m_ResultDropLogged;', source)
        self.assertIn('m_ResultDropLogged = false;', _body(source, 'void MCPBridge()'))
        self.assertEqual(source.count('m_ResultDropLogged = false;'), 1)
        post = _body(source, 'protected void PostResult(MCPResult result)')
        unavailable = _body(post, 'if (!m_Configured || !m_Ctx)')
        once = _body(unavailable, 'if (!m_ResultDropLogged)')
        self.assertLess(once.index('m_ResultDropLogged = true;'), once.index('Log('))
        self.assertIn('Log("result dropped transport unavailable id=" + result.id);', once)
        self.assertEqual(unavailable.count('Log('), 1)
        self.assertRegex(unavailable, r'}\s*return;\s*$')
        self.assertLess(post.index('if (!m_Configured || !m_Ctx)'), post.index('new JsonSerializer'))
        self.assertNotIn('m_ResultDropLogged', _body(source, 'void OnTick(float timeslice)'))

    def test_g2_reserves_next_batch_and_accepted_work_without_dropping_results(self) -> None:
        source = _source('MCPBridge.c')
        cap = _constant(source, 'MAX_CALLBACK_REFS')
        batch = _constant(source, 'MAX_POLL_RESULTS')
        self.assertEqual((cap, batch), (128, 64))
        # Consumer-independent ingress oracle: read its real constant, do not import services.
        daemon = ast.parse((ROOT / 'tools/dayz_mcp/loopback.py').read_text(encoding='utf-8-sig'))
        values = [ast.literal_eval(node.value) for node in daemon.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'MAX_QUEUE' for t in node.targets)]
        self.assertEqual(values, [64], 'changed producer capacity needs a new reservation')
        poll = _body(source, 'protected void StartPoll()')
        expression = 'm_CallbackRefs.Count() + m_Pending.Count() + m_Jobs.Count() > MAX_CALLBACK_REFS - MAX_POLL_RESULTS'
        guard = 'if (' + expression + ')'
        self.assertIn(guard, poll)
        self.assertEqual(_body(poll, guard).strip(), 'return;')
        self.assertLess(poll.index(guard), poll.index('m_PollInFlight = true;'))
        self.assertLess(poll.index(guard), poll.index('new MCPPollCallback'))
        self.assertNotIn('Log(', _body(poll, guard))
        # Evaluate the verified source condition against an independent capacity invariant.
        translated = expression.replace('m_CallbackRefs.Count()', 'callbacks').replace('m_Pending.Count()', 'pending').replace('m_Jobs.Count()', 'jobs')
        for callbacks, pending, jobs in itertools.product((0, 1, 32, 63, 64, 65, 127, 128), repeat=3):
            blocked = eval(translated, {'__builtins__': {}}, dict(callbacks=callbacks, pending=pending, jobs=jobs, MAX_CALLBACK_REFS=cap, MAX_POLL_RESULTS=batch))
            outstanding = callbacks + pending + jobs
            self.assertEqual(blocked, outstanding > 64)
            if not blocked:
                self.assertLessEqual(callbacks + 1, 128, 'GET also holds one callback')
                self.assertLessEqual(outstanding + 64, 128, 'every accepted job can finish at once')
        # Backpressure must leave accepted commands/jobs and their terminal POSTs running.
        tick = _body(source, 'void OnTick(float timeslice)')
        self.assertLess(tick.index('ProcessJobs();'), tick.index('StartPoll();'))
        self.assertLess(tick.index('DrainPending();'), tick.index('StartPoll();'))
        for signature in ('protected void ProcessJobs()', 'protected void DrainPending()', 'protected void PostResult(MCPResult result)'):
            body = _body(source, signature)
            self.assertNotIn('MAX_CALLBACK_REFS', body)
            self.assertNotIn('MAX_POLL_RESULTS', body)
        self.assertIn('m_CallbackRefs.Insert(cb);', _body(source, 'protected void PostResult(MCPResult result)'))

    def _assert_poll_clamp(self, name: str, constructor: str) -> None:
        source = _source(name)
        self.assertIn('m_PollHz = 5.0;', _body(source, constructor))
        init = _body(source, 'protected void TryInit()')
        positive = _body(init, 'if (cfg.pollHz > 0.0)')
        self.assertIn('m_PollHz = cfg.pollHz;', positive)
        self.assertIn('if (m_PollHz > 60.0)', positive)
        clamp = _body(positive, 'if (m_PollHz > 60.0)')
        self.assertEqual(clamp.strip(), 'm_PollHz = 60.0;')
        self.assertLess(positive.index('m_PollHz = cfg.pollHz;'), positive.index('if (m_PollHz > 60.0)'))
        self.assertNotIn('return;', positive, 'out-of-range config is clamped, not rejected')
        self.assertEqual(positive.count('m_PollHz ='), 2)

    def test_g3_server_clamps_only_above_sixty_hz(self) -> None:
        self._assert_poll_clamp('MCPBridge.c', 'void MCPBridge()')

    def test_g3_client_clamps_only_above_sixty_hz(self) -> None:
        self._assert_poll_clamp('MCPClientBridge.c', 'void MCPClientBridge()')

    def test_g4_restore_checks_game_before_player_and_mission(self) -> None:
        source = _source('MCPClientBridge.c')
        restore = _body(source, 'protected void RestoreGameplay()')
        no_game = _body(restore, 'if (!GetGame())')
        self.assertRegex(no_game, r'}\s*return;\s*$')
        self.assertEqual(no_game.count('Log('), 1, 'eight call sites: latch it or it floods')
        once = _body(no_game, 'if (!m_RestoreNoGameLogged)')
        self.assertLess(once.index('m_RestoreNoGameLogged = true;'), once.index('Log('))
        self.assertIn('Log("restore skipped: no game");', once)
        self.assertIn('protected bool m_RestoreNoGameLogged;', source)
        self.assertIn('m_RestoreNoGameLogged = false;', _body(source, 'void MCPClientBridge()'))
        self.assertEqual(source.count('m_RestoreNoGameLogged = true;'), 1)
        self.assertLess(restore.index('if (!GetGame())'), restore.index('GetGame().GetPlayer()'))
        self.assertLess(restore.index('if (!GetGame())'), restore.index('GetGame().GetMission()'))
        self.assertIn('if (player && m_PlayerSimulationDisabled)', restore)
        self.assertIn('mission.PlayerControlEnable(true);', restore)

    def test_g5_shutdown_latches_before_cleanup_but_allows_first_terminal_post(self) -> None:
        source = _source('MCPClientBridge.c')
        self.assertIn('protected bool m_Shutdown;', source)
        self.assertIn('m_Shutdown = false;', _body(source, 'void MCPClientBridge()'))
        self.assertEqual(source.count('m_Shutdown = false;'), 1)
        self.assertEqual(source.count('m_Shutdown = true;'), 1)
        shutdown = _body(source, 'void Shutdown()')
        reentry = _body(shutdown, 'if (m_Shutdown)')
        self.assertRegex(reentry, r'}\s*return;\s*$')
        self.assertEqual(reentry.count('Log('), 1)
        once = _body(reentry, 'if (!m_ShutdownReentryLogged)')
        self.assertLess(once.index('m_ShutdownReentryLogged = true;'), once.index('Log('))
        self.assertIn('Log("shutdown re-entered");', once)
        self.assertIn('protected bool m_ShutdownReentryLogged;', source)
        self.assertIn('m_ShutdownReentryLogged = false;', _body(source, 'void MCPClientBridge()'))
        # Without the first-entry line, silence cannot be told from "never ran".
        self.assertIn('Log("shutdown first entry");', shutdown)
        self.assertLess(shutdown.index('m_Shutdown = true;'), shutdown.index('Log("shutdown first entry");'))
        self.assertLess(shutdown.index('if (m_Shutdown)'), shutdown.index('m_Shutdown = true;'))
        for effect in ('m_Dialog.FinishDisconnected();', 'PostUiDialogJob(m_DialogJob);', 'MCPCarDrive.Clear();', 'RestoreGameplay();', 'm_Ctx.reset();', 'm_CallbackRefs.Clear();'):
            self.assertLess(shutdown.index('m_Shutdown = true;'), shutdown.index(effect))
        self.assertLess(shutdown.index('PostUiDialogJob(m_DialogJob);'), shutdown.index('m_Configured = false;'))
        post = _body(source, 'protected void PostResult(MCPResult result)')
        self.assertNotIn('m_Shutdown', post, 'must not veto the first terminal result')
        context = _body(shutdown, 'if (m_Ctx)')
        self.assertIn('m_Ctx.reset();', _body(context, 'if (!postedTerminal)'))

    def test_existing_callbacks_release_on_success_error_and_timeout(self) -> None:
        callbacks = (SCRIPTS / 'MCPCallbacks.c').read_text(encoding='utf-8-sig')
        for kind in ('MCPPollCallback', 'MCPResultCallback'):
            cls = _body(callbacks, 'class ' + kind)
            for signature in ('override void OnSuccess(', 'override void OnError(', 'override void OnTimeout('):
                self.assertIn('m_Bridge.ReleaseCallback(this);', _body(cls, signature))
        release = _body(_source('MCPBridge.c'), 'void ReleaseCallback(RestCallback cb)')
        self.assertIn('m_CallbackRefs.Remove(idx);', _body(release, 'if (idx >= 0)'))

    def test_existing_shutdown_has_both_singleton_and_destructor_entries(self) -> None:
        source = _source('MCPClientBridge.c')
        self.assertIn('Shutdown();', _body(source, 'void ~MCPClientBridge()'))
        singleton = _body(source, 'static void ShutdownInstance()')
        self.assertLess(singleton.index('m_Instance.Shutdown();'), singleton.index('m_Instance = null;'))
        mission = (SCRIPTS / 'MissionGameplay.c').read_text(encoding='utf-8-sig')
        self.assertIn('MCPClientBridge.ShutdownInstance();', _body(mission, 'void ~MissionGameplay()'))


if __name__ == '__main__':
    unittest.main()
