from __future__ import annotations

import importlib
import io
import json
import multiprocessing
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import install_mcp as installer
from dayz_mcp import knowledge

try:
    knowledge_pack = importlib.import_module("dayz_mcp.knowledge_pack")
except ModuleNotFoundError:
    knowledge_pack = None


PREPARE_ENTRIES = [
    {
        "name": "API",
        "source_file": "source.md",
        "evidence": [{"path": "x.c", "line": 1}],
    }
]


def _run_knowledge_prepare(
    index_path: str,
    outcome_queue: object,
    ready: object,
    release: object,
    fail_before_replace: bool,
) -> None:
    def before_replace() -> None:
        ready.set()
        if not release.wait(10.0):
            raise RuntimeError("prepare test barrier timeout")
        if fail_before_replace:
            raise OSError("injected before replace")

    knowledge._PREPARE_TEST_HOOK = before_replace
    try:
        knowledge._publish_index(Path(index_path), PREPARE_ENTRIES)
    except Exception as error:
        outcome_queue.put(f"{type(error).__name__}:{error}")
    else:
        outcome_queue.put("ok")
    finally:
        knowledge._PREPARE_TEST_HOOK = None


class RecordingGitRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> object:
        arguments = list(argv)
        self.calls.append((arguments, dict(kwargs)))
        if arguments[1:3] == ["clone", "--"]:
            destination = Path(arguments[-1])
            (destination / "skills" / "fixture-skill").mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class KnowledgePackInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def require_module(self):
        self.assertIsNotNone(
            knowledge_pack, "dayz_mcp.knowledge_pack is not implemented"
        )
        return knowledge_pack

    @staticmethod
    def write_skill(root: Path, name: str, value: str) -> Path:
        skill = root / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(value, encoding="utf-8")
        return skill

    def test_resolve_pack_dir_uses_localappdata_and_explicit_override(self) -> None:
        module = self.require_module()
        local_appdata = self.root / "local"
        override = self.root / "override"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}, clear=False):
            os.environ.pop("DAYZ_MCP_PACK_DIR", None)
            self.assertEqual(
                module.resolve_pack_dir(),
                (local_appdata / "DayZ_MCP" / "knowledge-pack").resolve(),
            )
            with patch.dict(
                os.environ, {"DAYZ_MCP_PACK_DIR": str(override)}, clear=False
            ):
                self.assertEqual(module.resolve_pack_dir(), override.resolve())

    def test_ensure_pack_clones_or_pulls_from_the_public_repository(self) -> None:
        module = self.require_module()
        runner = RecordingGitRunner()
        destination = self.root / "knowledge-pack"

        self.assertEqual(module.ensure_pack(destination, runner), destination.resolve())
        self.assertEqual(
            runner.calls[0][0],
            ["git", "clone", "--", module.PACK_URL, str(destination.resolve())],
        )

        self.assertEqual(module.ensure_pack(destination, runner), destination.resolve())
        self.assertEqual(
            runner.calls[1][0],
            [
                "git",
                "-C",
                str(destination.resolve()),
                "pull",
                "--ff-only",
                module.PACK_URL,
            ],
        )
        for _arguments, kwargs in runner.calls:
            self.assertEqual(
                kwargs,
                {
                    "capture_output": True,
                    "check": False,
                    "shell": False,
                    "text": True,
                    "timeout": 300.0,
                },
            )

    def test_ensure_pack_names_git_missing_and_gives_a_remedy(self) -> None:
        module = self.require_module()

        def missing_git(_argv: list[str], **_kwargs: object) -> object:
            raise FileNotFoundError("git.exe")

        with self.assertRaises(module.KnowledgePackError) as raised:
            module.ensure_pack(self.root / "knowledge-pack", missing_git)

        self.assertEqual(raised.exception.code, "git_missing")
        self.assertEqual(
            raised.exception.remedy,
            "Install Git for Windows and ensure git.exe is available on PATH.",
        )

    def test_target_game_build_requires_exactly_one_compatibility_match(self) -> None:
        module = self.require_module()
        pack = self.root / "pack"
        pack.mkdir()
        compatibility = pack / "compatibility-matrix.md"
        compatibility.write_text(
            "# Matrix\nTarget stable build: **DayZ PC 1.29.0.163451** (released)\n",
            encoding="utf-8",
        )
        self.assertEqual(module.target_game_build(pack), "1.29.0.163451")

        compatibility.write_text("# Matrix\n", encoding="utf-8")
        self.assertIsNone(module.target_game_build(pack))

        compatibility.write_text(
            "Target stable build: **DayZ PC 1.29.0.163451**\n"
            "Target stable build: **DayZ PC 1.29.0.163709**\n",
            encoding="utf-8",
        )
        self.assertIsNone(module.target_game_build(pack))

    def test_sync_update_and_unsync_never_touch_an_unowned_skill(self) -> None:
        module = self.require_module()
        pack = self.root / "pack"
        skills_dir = self.root / "agent-skills"
        manifest = self.root / "knowledge-pack-skills-manifest.json"
        self.write_skill(pack, "owned", "pack-v1")
        self.write_skill(pack, "foreign", "pack-copy")
        foreign = skills_dir / "foreign"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("user-copy", encoding="utf-8")

        registered = module.sync_skills(pack, skills_dir, manifest)

        self.assertEqual(registered, ["owned"])
        self.assertEqual((foreign / "SKILL.md").read_text(encoding="utf-8"), "user-copy")
        self.assertEqual(
            json.loads(manifest.read_text(encoding="utf-8")),
            {
                "entries": ["owned"],
                "kind": "dayz-mcp-knowledge-pack-skills-v1",
                "schema_version": 1,
                "skills_dir": str(skills_dir.resolve()),
            },
        )

        (pack / "skills" / "owned" / "SKILL.md").write_text(
            "pack-v2", encoding="utf-8"
        )
        self.write_skill(pack, "new-skill", "new")
        registered = module.sync_skills(pack, skills_dir, manifest)

        self.assertEqual(registered, ["new-skill", "owned"])
        self.assertEqual(
            (skills_dir / "owned" / "SKILL.md").read_text(encoding="utf-8"),
            "pack-v2",
        )
        self.assertEqual((foreign / "SKILL.md").read_text(encoding="utf-8"), "user-copy")

        removed = module.unsync(manifest)

        self.assertEqual(removed, ["new-skill", "owned"])
        self.assertFalse((skills_dir / "new-skill").exists())
        self.assertFalse((skills_dir / "owned").exists())
        self.assertTrue(foreign.is_dir())
        self.assertEqual((foreign / "SKILL.md").read_text(encoding="utf-8"), "user-copy")
        self.assertFalse(manifest.exists())

    def test_install_operation_prints_exact_paths_without_syncing_by_default(self) -> None:
        module = self.require_module()
        runner = RecordingGitRunner()
        pack = self.root / "pack"
        skills_dir = self.root / "agent-skills"
        manifest = self.root / "manifest.json"

        result = module.install_knowledge_pack(
            sync=False,
            runner=runner,
            pack_dir=pack,
            skills_dir=skills_dir,
            manifest_path=manifest,
            python_executable=self.root / "python.exe",
        )

        self.assertEqual(result["pack_dir"], str(pack.resolve()))
        self.assertEqual(result["skills_dir"], str(skills_dir.resolve()))
        self.assertEqual(result["manifest_path"], str(manifest.resolve()))
        self.assertEqual(result["skills_registration"], "print_only")
        self.assertEqual(result["skills_pending"], ["fixture-skill"])
        self.assertEqual(result["skills_registered"], [])
        self.assertFalse(skills_dir.exists())
        self.assertFalse(manifest.exists())
        self.assertIn("dayz_mcp.knowledge_pack unsync", result["undo"])
        self.assertIn(str(manifest.resolve()), result["undo"])

    def test_python_installer_defaults_to_pack_and_skip_is_explicit(self) -> None:
        self.assertTrue(
            hasattr(installer, "install_knowledge_pack"),
            "install_mcp does not expose the Knowledge Pack operation",
        )
        order: list[tuple[str, object]] = []

        venv_python = self.root / "runtime" / "Scripts" / "python.exe"

        def runtime(*_args: object, **_kwargs: object) -> dict[str, object]:
            order.append(("runtime", None))
            return {
                "status": "installed",
                "registered": False,
                "venv_python": str(venv_python),
            }

        def pack(*, sync: bool, python_executable: Path) -> dict[str, object]:
            order.append(("pack", (sync, python_executable)))
            return {"status": "ready", "skills_registered": []}

        with (
            patch.object(installer, "run_installer", side_effect=runtime),
            patch.object(installer, "install_knowledge_pack", side_effect=pack),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            exit_code = installer.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            order,
            [("runtime", None), ("pack", (False, venv_python))],
        )
        self.assertEqual(json.loads(stdout.getvalue())["knowledge_pack"]["status"], "ready")

        order.clear()
        with (
            patch.object(installer, "run_installer", side_effect=runtime),
            patch.object(installer, "install_knowledge_pack", side_effect=pack),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = installer.main(["--register"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            order,
            [("runtime", None), ("pack", (True, venv_python))],
        )

        order.clear()
        with (
            patch.object(installer, "run_installer", side_effect=runtime),
            patch.object(installer, "install_knowledge_pack", side_effect=pack),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            exit_code = installer.main(["--skip-knowledge-pack"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(order, [("runtime", None)])
        self.assertEqual(
            json.loads(stdout.getvalue())["knowledge_pack"], {"status": "skipped"}
        )

    def test_python_installer_preserves_git_missing_remedy(self) -> None:
        module = self.require_module()
        with (
            patch.object(
                installer,
                "run_installer",
                return_value={
                    "status": "installed",
                    "registered": False,
                    "venv_python": str(self.root / "runtime" / "python.exe"),
                },
            ),
            patch.object(
                installer,
                "install_knowledge_pack",
                side_effect=module.KnowledgePackError(
                    "git_missing", remedy=module.GIT_MISSING_REMEDY
                ),
            ),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()) as stderr,
        ):
            exit_code = installer.main([])

        self.assertEqual(exit_code, 2)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "error": "git_missing",
                "remedy": module.GIT_MISSING_REMEDY,
                "status": "error",
            },
        )

    def test_prepare_operations_are_exclusive_and_atomic_between_windows_processes(self) -> None:
        # "spawn" reproduces two independent Windows windows; fork is the
        # POSIX equivalent and inherits the same lock primitives, so the
        # two-process contention contract holds on both platforms.
        context = multiprocessing.get_context("spawn" if sys.platform == "win32" else "fork")
        for fail_before_replace in (False, True):
            with self.subTest(fail_before_replace=fail_before_replace):
                index_path = self.root / (
                    "knowledge-success.json"
                    if not fail_before_replace
                    else "knowledge-failure.json"
                )
                previous = b'{"previous":true}\n'
                index_path.write_bytes(previous)
                foreign_candidate = Path(f"{index_path}.candidate.foreign")
                foreign_candidate.write_bytes(b"foreign")
                ready = context.Event()
                release = context.Event()
                outcomes = context.Queue()
                winner = context.Process(
                    target=_run_knowledge_prepare,
                    args=(str(index_path), outcomes, ready, release, fail_before_replace),
                )
                winner.start()
                loser = None
                try:
                    self.assertTrue(ready.wait(10.0), "winner did not reach prepare barrier")
                    loser = context.Process(
                        target=_run_knowledge_prepare,
                        args=(str(index_path), outcomes, context.Event(), context.Event(), False),
                    )
                    loser.start()
                    loser.join(10.0)
                    self.assertFalse(loser.is_alive(), "loser did not return immediately")
                    release.set()
                    winner.join(10.0)
                    self.assertFalse(winner.is_alive(), "winner did not finish")
                finally:
                    release.set()
                    for process in (winner, loser):
                        if process is not None:
                            process.join(5.0)
                            if process.is_alive():
                                process.terminate()
                                process.join(5.0)

                results = {outcomes.get(timeout=2.0), outcomes.get(timeout=2.0)}
                self.assertIn("KnowledgePrepareConflict:knowledge_prepare_conflict", results)
                if fail_before_replace:
                    self.assertIn("OSError:injected before replace", results)
                    self.assertEqual(index_path.read_bytes(), previous)
                else:
                    self.assertIn("ok", results)
                    expected = (
                        json.dumps(PREPARE_ENTRIES, ensure_ascii=False, indent=2) + "\n"
                    ).encode("utf-8")
                    self.assertEqual(index_path.read_bytes(), expected)
                self.assertEqual(foreign_candidate.read_bytes(), b"foreign")
                self.assertEqual(
                    list(index_path.parent.glob(f"{index_path.name}.candidate.*")),
                    [foreign_candidate],
                )

    def test_prepare_lock_rejects_name_surrogate_before_open(self) -> None:
        lock_path = self.root / "knowledge.json.prepare.lock"
        with (
            patch.object(
                knowledge,
                "_assert_no_name_surrogates",
                side_effect=knowledge.KnowledgePrepareConflict("knowledge_prepare_conflict"),
            ),
            patch.object(knowledge.os, "open", side_effect=AssertionError("opened too early")),
        ):
            with self.assertRaises(knowledge.KnowledgePrepareConflict):
                with knowledge._exclusive_prepare_lock(lock_path):
                    pass

    def test_prepare_lock_rejects_existing_non_regular_anchor_before_open(self) -> None:
        lock_path = self.root / "knowledge.json.prepare.lock"
        lock_path.mkdir()
        with patch.object(knowledge.os, "open", side_effect=AssertionError("opened too early")):
            with self.assertRaises(knowledge.KnowledgePrepareConflict):
                with knowledge._exclusive_prepare_lock(lock_path):
                    pass

    def test_prepare_lock_rejects_short_anchor_write(self) -> None:
        lock_path = self.root / "knowledge.json.prepare.lock"
        with patch.object(knowledge.os, "write", return_value=0):
            with self.assertRaises(knowledge.KnowledgePrepareConflict):
                with knowledge._exclusive_prepare_lock(lock_path):
                    pass

    def test_prepare_lock_repins_identity_after_acquiring_lock(self) -> None:
        lock_path = self.root / "knowledge.json.prepare.lock"
        original = knowledge._regular_lock_identity
        calls = 0

        def drift_on_second_pin(path: Path, descriptor: int) -> tuple[int, int]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise knowledge.KnowledgePrepareConflict("knowledge_prepare_conflict")
            return original(path, descriptor)

        with patch.object(knowledge, "_regular_lock_identity", side_effect=drift_on_second_pin):
            with self.assertRaises(knowledge.KnowledgePrepareConflict):
                with knowledge._exclusive_prepare_lock(lock_path):
                    pass
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
