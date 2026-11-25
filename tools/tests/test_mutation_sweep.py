"""tools/lote_harness/mutation_sweep.py: one deletion sweep over the lines a diff adds.

The sweep blanks each added line in turn, runs the given test modules and lists the
mutants the suite does not kill. Every test here builds a throwaway git repository with a
toy package and a toy suite, so each verdict is known before the sweep runs:

- toy/scale.py: no toy test passes a negative factor, so blanking ``factor = -factor`` or
  ``value = -value`` survives; blanking ``result = value * factor`` or ``return result``
  is killed. The docstring and the comment line are never mutated.
- toy/loop.py: blanking ``n -= 1`` makes ``countdown`` loop forever, so that mutant ends
  on the timeout and counts as killed. With TOY_HEARTBEAT_DIR set, the toy countdown test
  starts a grandchild that keeps rewriting a heartbeat file until it is killed: after the
  sweep no heartbeat may still move, which takes a kill of the whole process tree.
- toy/twins.py: ``total = x * 2`` and ``return total`` occur twice, so they only match
  once in the file with context lines around them.
- toy/bridge.c: comment, brace and code lines for the C-like rules; the toy suite only
  reads ``value += 2;`` from it.
- toy/renamed.py: toy/legacy.py renamed with one function added, so only that function's
  two lines are new. toy/run.ps1 and toy/run.sh: the ``#`` and ``<# #>`` comment rules;
  a block opener inside a string (bridge.c, run.ps1) must not hide the lines after it.
- toy/test_extra.py and toy/extra_test.py: test files by their basename alone.
  toy/data.json: a suffix with no comment rule, swept only through ``--include``.
- tests/test_writer.py: with TOY_WRITER=1 it appends to toy/twins.py, standing in for
  another process that writes the tree while the sweep runs.
- tests/test_skips.py and tests/test_empty.py: modules that run no test at all.

Each test starts real processes (git and the toy suite), so the module is in the slow tier.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests._tiers import slow_test

_HARNESS = Path(__file__).resolve().parents[1] / "lote_harness"

_BASE_FILES = {
    "tools/toy/__init__.py": "",
    "tools/toy/scale.py": (
        '"""Toy module the sweep mutates."""\n'
        "\n"
        "\n"
        "def base_only(x):\n"
        "    return x + 1\n"
    ),
    "tools/toy/legacy.py": (
        "def legacy_one():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def legacy_two():\n"
        "    return 2\n"
        "\n"
        "\n"
        "def legacy_three():\n"
        "    return 3\n"
    ),
    "tools/tests/__init__.py": "",
    "tools/tests/test_skips.py": (
        "import unittest\n"
        "\n"
        "\n"
        "class SkipsTest(unittest.TestCase):\n"
        "    @unittest.skip('never runs')\n"
        "    def test_skipped(self):\n"
        "        self.fail('unreachable')\n"
    ),
    "tools/tests/test_empty.py": "# A test module that defines no test.\n",
    "tools/tests/test_writer.py": (
        "import os\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "\n"
        "\n"
        "class WriterTest(unittest.TestCase):\n"
        "    def test_touches_a_swept_file(self):\n"
        "        if os.environ.get('TOY_WRITER') != '1':\n"
        "            self.skipTest('writer off')\n"
        "        target = Path(__file__).resolve().parents[1] / 'toy' / 'twins.py'\n"
        "        with open(target, 'a', encoding='utf-8', newline='') as fh:\n"
        "            fh.write('# touched\\n')\n"
    ),
    # Changes twins.py only while a mutant is on disk, never during the base run (PR #446 review, F1).
    "tools/tests/test_mutant_writer.py": (
        "import os\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "\n"
        "\n"
        "class MutantWriterTest(unittest.TestCase):\n"
        "    def test_changes_the_mutant(self):\n"
        "        original = os.environ.get('TOY_MUTANT_WRITER')\n"
        "        if not original:\n"
        "            self.skipTest('writer off')\n"
        "        target = Path(__file__).resolve().parents[1] / 'toy' / 'twins.py'\n"
        "        if target.read_bytes() != Path(original).read_bytes():\n"
        "            with open(target, 'ab') as fh:\n"
        "                fh.write(b'# changed under the mutant\\n')\n"
    ),
    "tools/tests/test_toy.py": (
        "import unittest\n"
        "\n"
        "from toy import scale\n"
        "\n"
        "\n"
        "class ToyTest(unittest.TestCase):\n"
        "    def test_base_only(self):\n"
        "        self.assertEqual(scale.base_only(1), 2)\n"
    ),
    "tools/tests/test_red.py": (
        "import unittest\n"
        "\n"
        "\n"
        "class RedTest(unittest.TestCase):\n"
        "    def test_always_red(self):\n"
        "        self.fail('this module is red on purpose')\n"
    ),
    "tools/tests/test_env_probe.py": (
        "import os\n"
        "import stat\n"
        "import tempfile\n"
        "import unittest\n"
        "\n"
        "\n"
        "def _norm(path):\n"
        "    return os.path.normcase(os.path.abspath(path))\n"
        "\n"
        "\n"
        "class EnvProbeTest(unittest.TestCase):\n"
        "    def test_private_dirs(self):\n"
        "        parent_temp = os.environ.get('TOY_PARENT_TEMP')\n"
        "        if not parent_temp:\n"
        "            self.skipTest('no parent recorded')\n"
        "        parent_local = os.environ['TOY_PARENT_LOCALAPPDATA']\n"
        "        temp = tempfile.gettempdir()\n"
        "        local = os.environ.get('LOCALAPPDATA', '')\n"
        "        self.assertNotEqual(_norm(temp), _norm(parent_temp))\n"
        "        self.assertNotEqual(_norm(local), _norm(parent_local))\n"
        "        self.assertIn('mutation-sweep-', temp)\n"
        "        self.assertIn('mutation-sweep-', local)\n"
        "        self.assertTrue(os.path.isdir(temp) and os.path.isdir(local))\n"
        "        # A read-only file the sweep has to remove with the rest of its directory.\n"
        "        leftover = os.path.join(temp, 'read-only.txt')\n"
        "        if not os.path.exists(leftover):\n"
        "            with open(leftover, 'w', encoding='utf-8') as fh:\n"
        "                fh.write('x')\n"
        "            os.chmod(leftover, stat.S_IREAD)\n"
    ),
}

_HEAD_FILES = {
    "tools/toy/scale.py": (
        '"""Toy module the sweep mutates."""\n'
        "\n"
        "\n"
        "def base_only(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "LABEL = (\n"
        '    "scaled "\n'
        '    "value"\n'
        ")\n"
        "\n"
        "\n"
        "def scale(value, factor):\n"
        '    """Multiply value by factor.\n'
        "\n"
        "    Docstring lines are never mutated.\n"
        '    """\n'
        "    # A comment-only line is never mutated.\n"
        "    if factor < 0:\n"
        "        factor = -factor\n"
        "        value = -value\n"
        "    result = value * factor\n"
        "    return result\n"
    ),
    "tools/toy/loop.py": (
        "def countdown(n):\n"
        "    steps = 0\n"
        "    while n > 0:\n"
        "        steps += 1\n"
        "        n -= 1\n"
        "    return steps\n"
    ),
    "tools/toy/twins.py": (
        "def twin_a(x):\n"
        "    total = x * 2\n"
        "    return total\n"
        "\n"
        "\n"
        "def twin_b(x):\n"
        "    total = x * 2\n"
        "    return total + 1\n"
    ),
    "tools/toy/bridge.c": (
        "// Header comment, never mutated.\n"
        "class ToyBridge\n"
        "{\n"
        "\t/* A block comment\n"
        "\t   that spans two lines. */\n"
        "\tint Answer()\n"
        "\t{\n"
        "\t\tint value = 40; // a trailing comment keeps the line\n"
        '\t\tstring opener = "/*"; // a block opener inside a string opens nothing\n'
        "\t\tvalue += 2;\n"
        "\t\treturn value;\n"
        "\t}\n"
        "\n"
        "\tstring Url()\n"
        "\t{\n"
        '\t\treturn "http://example.invalid/*not a comment*/";\n'
        "\t}\n"
        "}\n"
    ),
    # legacy.py renamed with one function added: only that function is new.
    "tools/toy/renamed.py": (
        "def legacy_one():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def legacy_two():\n"
        "    return 2\n"
        "\n"
        "\n"
        "def legacy_three():\n"
        "    return 3\n"
        "\n"
        "\n"
        "def renamed_four():\n"
        "    return 4\n"
    ),
    "tools/toy/run.ps1": (
        "<#\n"
        ".SYNOPSIS\n"
        "Toy script; the block comment lines are never mutated.\n"
        "#>\n"
        "# A line comment, never mutated.\n"
        "$value = 2\n"
        '$quoted = "a`"<# not a comment"\n'
        "Write-Output $value\n"
    ),
    "tools/toy/run.sh": (
        "#!/bin/sh\n"
        "# A comment, never mutated.\n"
        "echo toy\n"
    ),
    # Test files outside a tests/ folder: their basename keeps them out of the default selection.
    "tools/toy/test_extra.py": "def extra():\n    return 'a test helper'\n",
    "tools/toy/extra_test.py": "def other():\n    return 'a test helper'\n",
    # A suffix with no comment rule: swept only through --include.
    "tools/toy/data.json": '{\n  "name": "toy",\n  "size": 2\n}\n',
    "tools/tests/test_toy.py": (
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import unittest\n"
        "from pathlib import Path\n"
        "\n"
        "from toy import loop, scale, twins\n"
        "\n"
        "# Rewrites its file with its pid and the time every 0.2 s, for at most five minutes.\n"
        "BEAT = (\n"
        "    'import os, sys, time\\n'\n"
        "    'for _ in range(1500):\\n'\n"
        "    '    open(sys.argv[1], \"w\").write(f\"{os.getpid()} {time.time()}\")\\n'\n"
        "    '    time.sleep(0.2)\\n'\n"
        ")\n"
        "\n"
        "\n"
        "class ToyTest(unittest.TestCase):\n"
        "    def test_base_only(self):\n"
        "        self.assertEqual(scale.base_only(1), 2)\n"
        "\n"
        "    def test_scale(self):\n"
        "        self.assertEqual(scale.scale(2, 3), 6)\n"
        "\n"
        "    def test_label(self):\n"
        "        self.assertEqual(scale.LABEL, 'scaled value')\n"
        "\n"
        "    def test_countdown(self):\n"
        "        beat_dir = os.environ.get('TOY_HEARTBEAT_DIR')\n"
        "        child = None\n"
        "        if beat_dir:\n"
        "            # A grandchild that outlives this process unless the whole tree is killed.\n"
        "            beat = os.path.join(beat_dir, f'beat-{os.getpid()}.txt')\n"
        "            child = subprocess.Popen([sys.executable, '-c', BEAT, beat])\n"
        "        try:\n"
        "            self.assertEqual(loop.countdown(3), 3)\n"
        "        finally:\n"
        "            if child is not None:\n"
        "                child.kill()\n"
        "                child.wait()\n"
        "\n"
        "    def test_twins(self):\n"
        "        self.assertEqual(twins.twin_a(2), 4)\n"
        "        self.assertEqual(twins.twin_b(2), 5)\n"
        "\n"
        "    def test_bridge_text(self):\n"
        "        source = (Path(__file__).resolve().parents[1] / 'toy' / 'bridge.c')\n"
        "        self.assertIn('value += 2;', source.read_text(encoding='utf-8'))\n"
    ),
}

_HEAD_REMOVED = ["tools/toy/legacy.py"]

_SCALE = "tools/toy/scale.py"
_LOOP = "tools/toy/loop.py"
_TWINS = "tools/toy/twins.py"
_BRIDGE = "tools/toy/bridge.c"
_RENAMED = "tools/toy/renamed.py"
_PS1 = "tools/toy/run.ps1"
_SH = "tools/toy/run.sh"

# Every code line of the toy diff, by file: the only lines the default selection mutates.
_EXPECTED_CANDIDATES = {
    _SCALE: [
        "LABEL = (",
        '"scaled "',
        '"value"',
        "def scale(value, factor):",
        "if factor < 0:",
        "factor = -factor",
        "value = -value",
        "result = value * factor",
        "return result",
    ],
    _LOOP: [
        "def countdown(n):",
        "steps = 0",
        "while n > 0:",
        "steps += 1",
        "n -= 1",
        "return steps",
    ],
    _TWINS: [
        "def twin_a(x):",
        "total = x * 2",
        "return total",
        "def twin_b(x):",
        "total = x * 2",
        "return total + 1",
    ],
    _BRIDGE: [
        "class ToyBridge",
        "int Answer()",
        "int value = 40; // a trailing comment keeps the line",
        'string opener = "/*"; // a block opener inside a string opens nothing',
        "value += 2;",
        "return value;",
        "string Url()",
        'return "http://example.invalid/*not a comment*/";',
    ],
    _RENAMED: ["def renamed_four():", "return 4"],
    _PS1: ["$value = 2", '$quoted = "a`"<# not a comment"', "Write-Output $value"],
    _SH: ["echo toy"],
}
_SURVIVORS = {
    (_RENAMED, "def renamed_four():"),
    (_RENAMED, "return 4"),
    (_PS1, "$value = 2"),
    (_PS1, '$quoted = "a`"<# not a comment"'),
    (_PS1, "Write-Output $value"),
    (_BRIDGE, 'string opener = "/*"; // a block opener inside a string opens nothing'),
    (_SH, "echo toy"),
    (_SCALE, "factor = -factor"),
    (_SCALE, "value = -value"),
    (_BRIDGE, "class ToyBridge"),
    (_BRIDGE, "int Answer()"),
    (_BRIDGE, "int value = 40; // a trailing comment keeps the line"),
    (_BRIDGE, "return value;"),
    (_BRIDGE, "string Url()"),
    (_BRIDGE, 'return "http://example.invalid/*not a comment*/";'),
}


def _load(name: str):
    path = _HARNESS / f"{name}.py"
    module_name = f"_lote_harness_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered first, as an import does: dataclasses resolve string annotations through
    # sys.modules[cls.__module__].
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _call(module, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = module.main(argv)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=toy", "-c", "user.email=toy@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))


def _rmtree(path: Path) -> None:
    def _retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


def _digests(root: Path) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith(".git/") and "__pycache__" not in rel:
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _line_numbers(rel: str, text: str) -> list[int]:
    lines = _HEAD_FILES[rel].split("\n")
    return [i + 1 for i, line in enumerate(lines) if line.strip() == text]


@slow_test
class MutationSweepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = Path(tempfile.mkdtemp(prefix="sweep-test-"))
        self.addCleanup(_rmtree, self.scratch)
        self.root = self.scratch / "repo"
        self.root.mkdir()
        _git(self.root, "-c", "init.defaultBranch=main", "init", "-q")
        _git(self.root, "config", "core.autocrlf", "false")
        _write(self.root, _BASE_FILES)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")
        _write(self.root, _HEAD_FILES)
        for rel in _HEAD_REMOVED:
            (self.root / rel).unlink()
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "head")
        self.head = _git(self.root, "rev-parse", "HEAD")
        self.report = self.scratch / "report.json"

    def _argv(self, *extra: str, modules: tuple[str, ...] = ("tests.test_toy",)) -> list[str]:
        argv = ["--base", self.base, "--tree", str(self.root), "--python", sys.executable,
                "--json", str(self.report)]
        for module in modules:
            argv += ["--module", module]
        return argv + list(extra)

    def _sweep(self, *extra: str, modules: tuple[str, ...] = ("tests.test_toy",)):
        return _call(_load("mutation_sweep"), self._argv(*extra, modules=modules))

    def _json(self) -> dict:
        return json.loads(self.report.read_text(encoding="utf-8"))

    def _assert_no_grandchild_left(self, beats: Path) -> None:
        # The hanging mutant's toy test started a grandchild that rewrites its heartbeat file
        # every 0.2 s; only a kill of the whole process tree stops it.
        files = sorted(beats.glob("beat-*.txt"))
        self.assertTrue(files, "the toy countdown test started no grandchild")
        first = {path: path.read_text(encoding="utf-8") for path in files}
        time.sleep(1.0)
        alive = [path for path in files if path.read_text(encoding="utf-8") != first[path]]
        for path in alive:
            with contextlib.suppress(ValueError, OSError):
                os.kill(int(path.read_text(encoding="utf-8").split()[0]), signal.SIGTERM)
        self.assertEqual(alive, [], "a timed-out mutant left its grandchild running")

    def test_killed_mutant_is_not_listed_and_unkilled_one_is(self) -> None:
        before = _digests(self.root)
        beats = self.scratch / "beats"
        beats.mkdir()
        with mock.patch.dict(os.environ, {"TOY_HEARTBEAT_DIR": str(beats)}):
            rc, out, err = self._sweep("--timeout", "10")
        self.assertEqual(rc, 0, f"a sweep with survivors still exits 0\n{out}\n{err}")
        self._assert_no_grandchild_left(beats)
        report = self._json()
        self.assertTrue(all(m["seconds"] > 0 for m in report["mutants"]), report["mutants"])
        self.assertEqual(report["base"], self.base)
        self.assertEqual(report["head"], self.head)
        by_key = {}
        for mutant in report["mutants"]:
            by_key.setdefault((mutant["path"], mutant["text"]), []).append(mutant)
        expected_keys = {(rel, text) for rel, texts in _EXPECTED_CANDIDATES.items() for text in texts}
        self.assertEqual(set(by_key), expected_keys)
        survived = {key for key, ms in by_key.items() if all(m["status"] == "survived" for m in ms)}
        self.assertEqual(survived, _SURVIVORS)
        # stdout lists the survivors and nothing else: a killed or timed-out mutant listed
        # there fails (PR #446 review, F9).
        rows = {line for line in out.splitlines() if " | " in line}
        self.assertEqual(
            rows, {f"{rel}:{_line_numbers(rel, text)[0]} | {text}" for rel, text in _SURVIVORS}
        )
        for rel, text in ((_SCALE, "result = value * factor"), (_SCALE, "return result"),
                          (_LOOP, "steps += 1"), (_BRIDGE, "value += 2;")):
            self.assertEqual({m["status"] for m in by_key[(rel, text)]}, {"killed"})
        self.assertEqual({m["status"] for m in by_key[(_LOOP, "n -= 1")]}, {"timeout"})
        counts = report["counts"]
        self.assertEqual(counts["mutants"], len(report["mutants"]))
        self.assertEqual(counts["survived"], len(_SURVIVORS))
        self.assertEqual(counts["killed"], counts["mutants"] - counts["survived"])
        self.assertEqual(counts["timeout"], 1)
        self.assertEqual(counts["not_unique"], 0)
        self.assertIn(f"supervivientes: {len(_SURVIVORS)}", out.splitlines())
        self.assertEqual(_digests(self.root), before, "every file is restored byte for byte")
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")

    def test_only_added_code_lines_of_non_test_files_are_mutated(self) -> None:
        rc, out, err = self._sweep("--list")
        self.assertEqual(rc, 0, f"{out}\n{err}")
        report = self._json()
        listed = {}
        for mutant in report["mutants"]:
            self.assertEqual(mutant["status"], "listed")
            listed.setdefault(mutant["path"], []).append((mutant["line"], mutant["text"]))
        self.assertEqual(sorted(listed), sorted(_EXPECTED_CANDIDATES))
        for rel, texts in _EXPECTED_CANDIDATES.items():
            self.assertEqual([text for _line, text in sorted(listed[rel])], texts)
            for line, text in listed[rel]:
                self.assertIn(line, _line_numbers(rel, text))
        self.assertFalse(any(line <= 5 for line, _text in listed[_SCALE]),
                         "lines that were already in the base are never mutated")

    def test_list_mode_runs_no_test_module(self) -> None:
        before = _digests(self.root)
        rc, out, err = self._sweep("--list", modules=("tests.test_red",))
        self.assertEqual(rc, 0, f"--list must not run the (red) module\n{out}\n{err}")
        self.assertEqual(_digests(self.root), before)

    def test_line_unique_only_with_context_is_skipped_at_context_0(self) -> None:
        rc, out, err = self._sweep("--list", "--context", "0", "--include", _TWINS)
        self.assertEqual(rc, 0, f"{out}\n{err}")
        report = self._json()
        skipped = sorted((s["path"], s["line"], s["text"]) for s in report["skipped_not_unique"])
        self.assertEqual(skipped, [(_TWINS, 2, "total = x * 2"), (_TWINS, 3, "return total"),
                                   (_TWINS, 7, "total = x * 2")])
        self.assertEqual(report["counts"]["not_unique"], 3)
        self.assertEqual(sorted(m["line"] for m in report["mutants"]), [1, 6, 8])

        rc, out, err = self._sweep("--timeout", "10", "--include", _TWINS)
        self.assertEqual(rc, 0, f"{out}\n{err}")
        report = self._json()
        self.assertEqual(report["skipped_not_unique"], [])
        self.assertEqual(sorted(m["line"] for m in report["mutants"]), [1, 2, 3, 6, 7, 8])
        self.assertEqual({m["status"] for m in report["mutants"]}, {"killed"})
        self.assertEqual({m["line"]: m["context"] > 0 for m in report["mutants"]},
                         {1: False, 2: True, 3: True, 6: False, 7: True, 8: False})
        line_2 = next(m for m in report["mutants"] if m["line"] == 2)
        self.assertEqual((line_2["context"], line_2["old"], line_2["new"]),
                         (1, "def twin_a(x):\n    total = x * 2\n    return total",
                          "def twin_a(x):\n\n    return total"))

    def test_other_suffixes_are_swept_only_through_include(self) -> None:
        rc, out, err = self._sweep("--list", "--include", "tools/toy/data.json")
        self.assertEqual(rc, 0, f"{out}\n{err}")
        listed = [(m["line"], m["text"]) for m in self._json()["mutants"]]
        self.assertEqual(listed, [(2, '"name": "toy",'), (3, '"size": 2')])

    def test_every_mutant_is_the_one_mutation_gate_confirms(self) -> None:
        rc, out, err = self._sweep("--timeout", "10", "--include", _SCALE, "--include", _TWINS)
        self.assertEqual(rc, 0, f"{out}\n{err}")
        mutants = {(m["path"], m["line"]): m for m in self._json()["mutants"]}
        gate = _load("mutation_gate")
        cases = [
            (_SCALE, _line_numbers(_SCALE, "factor = -factor")[0], 1),
            (_SCALE, _line_numbers(_SCALE, "result = value * factor")[0], 0),
            (_TWINS, 2, 0),
        ]
        for rel, line, gate_rc in cases:
            mutant = mutants[(rel, line)]
            old_file = self.scratch / f"old-{line}.txt"
            new_file = self.scratch / f"new-{line}.txt"
            old_file.write_bytes(mutant["old"].encode("utf-8"))
            new_file.write_bytes(mutant["new"].encode("utf-8"))
            argv = ["--tree", str(self.root), "--file", rel, "--old", f"@{old_file}",
                    "--new", f"@{new_file}", "--module", "tests.test_toy",
                    "--python", sys.executable]
            got, gate_out, gate_err = _call(gate, argv)
            self.assertEqual(got, gate_rc, f"{rel}:{line} {mutant}\n{gate_out}\n{gate_err}")
            self.assertEqual(mutant["status"], "survived" if gate_rc == 1 else "killed")
        single = mutants[(_SCALE, _line_numbers(_SCALE, "factor = -factor")[0])]
        self.assertEqual((single["context"], single["old"], single["new"]),
                         (0, "        factor = -factor", ""))
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")

    def test_red_base_exits_2_and_mutates_nothing(self) -> None:
        before = _digests(self.root)
        rc, out, err = self._sweep(modules=("tests.test_toy", "tests.test_red"))
        self.assertEqual(rc, 2, f"{out}\n{err}")
        self.assertEqual(_digests(self.root), before)
        self.assertFalse(any(" | " in line for line in out.splitlines()), out)

    def test_base_module_that_runs_no_test_exits_2(self) -> None:
        # With every test skipped (as DAYZ_MCP_FAST_TESTS=1 does to a slow-tier module) or
        # none defined, unittest can exit 0, and every mutant would read as a survivor.
        before = _digests(self.root)
        for module in ("tests.test_skips", "tests.test_empty"):
            rc, out, err = self._sweep("--include", _TWINS, modules=("tests.test_toy", module))
            self.assertEqual(rc, 2, f"{module}\n{out}\n{err}")
            self.assertFalse(any(" | " in line for line in out.splitlines()), out)
        self.assertEqual(_digests(self.root), before)

    def test_file_modified_against_head_exits_2_untouched(self) -> None:
        dirty = (self.root / _SCALE).read_bytes() + b"# uncommitted\n"
        (self.root / _SCALE).write_bytes(dirty)
        rc, out, err = self._sweep("--include", _SCALE)
        self.assertEqual(rc, 2, f"{out}\n{err}")
        self.assertIn(_SCALE, out + err)
        self.assertEqual((self.root / _SCALE).read_bytes(), dirty)

    def test_file_changed_by_someone_else_exits_3_and_is_left_alone(self) -> None:
        # The toy writer appends to twins.py during the base run, after the sweep recorded
        # the original: the first mutant must refuse to touch the file.
        with mock.patch.dict(os.environ, {"TOY_WRITER": "1"}):
            rc, out, err = self._sweep("--include", _TWINS,
                                       modules=("tests.test_toy", "tests.test_writer"))
        self.assertEqual(rc, 3, f"{out}\n{err}")
        self.assertIn(_TWINS, out + err)
        self.assertTrue((self.root / _TWINS).read_bytes().endswith(b"# touched\n"),
                        "the sweep must not overwrite a change it did not make")

    def test_a_change_made_while_a_mutant_is_on_disk_exits_3_and_is_kept(self) -> None:
        # PR #446 review, F1: the toy writer runs first in every mutant run and appends to
        # twins.py while the first mutant (line 1 deleted) is on disk. The sweep must find the
        # file is no longer the mutant it wrote, leave it as the writer left it, exit 3 and name
        # the file and the deleted line.
        original = (self.root / _TWINS).read_bytes()
        copy = self.scratch / "twins-original.py"
        copy.write_bytes(original)
        with mock.patch.dict(os.environ, {"TOY_MUTANT_WRITER": str(copy)}):
            rc, out, err = self._sweep("--timeout", "10", "--include", _TWINS,
                                       modules=("tests.test_mutant_writer", "tests.test_toy"))
        self.assertEqual(rc, 3, f"{out}\n{err}")
        first_mutant = original.decode("utf-8").replace("def twin_a(x):", "", 1).encode("utf-8")
        self.assertEqual((self.root / _TWINS).read_bytes(), first_mutant + b"# changed under the mutant\n")
        self.assertIn(f"{_TWINS}:1 | def twin_a(x):", out + err)

    def test_ctrl_c_restores_the_file_and_exits_130(self) -> None:
        # Ctrl+C is simulated in-process, as a KeyboardInterrupt from the second mutant's run.
        sweep = _load("mutation_sweep")
        real_run = sweep._run_mutant
        calls = []

        def interrupted(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real_run(*args, **kwargs)

        before = _digests(self.root)
        with mock.patch.object(sweep, "_run_mutant", interrupted):
            rc, out, err = _call(sweep, self._argv("--timeout", "10", "--include", _TWINS))
        self.assertEqual(rc, 130, f"{out}\n{err}")
        self.assertEqual(_digests(self.root), before)
        self.assertEqual(_git(self.root, "status", "--porcelain"), "")
        self.assertIn("mueren: 1 de 1 (0 por timeout)", out.splitlines(), "the report says what was done")

    def test_unknown_base_exits_2(self) -> None:
        argv = self._argv()
        argv[argv.index("--base") + 1] = "0" * 40
        rc, out, err = _call(_load("mutation_sweep"), argv)
        self.assertEqual(rc, 2, f"{out}\n{err}")

    def test_test_runs_get_private_temp_and_localappdata_by_default(self) -> None:
        parent = {
            "TOY_PARENT_TEMP": tempfile.gettempdir(),
            "TOY_PARENT_LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        }
        modules = ("tests.test_toy", "tests.test_env_probe")
        with mock.patch.dict(os.environ, parent):
            rc, out, err = self._sweep("--timeout", "10", "--include", _TWINS, modules=modules)
            self.assertEqual(rc, 0, f"isolated by default\n{out}\n{err}")
            private = [line[len("entorno: "):] for line in err.splitlines() if line.startswith("entorno: ")]
            self.assertEqual(len(private), 1, err)
            self.assertFalse(Path(private[0]).exists(),
                             "the private directory is removed at the end, read-only files included")
            rc, out, err = self._sweep("--no-isolate", "--include", _TWINS, modules=modules)
            self.assertEqual(rc, 2, f"--no-isolate inherits the environment\n{out}\n{err}")

    def test_script_runs_from_the_command_line(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-B", str(_HARNESS / "mutation_sweep.py"), *self._argv("--list")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = _line_numbers(_SCALE, "factor = -factor")[0]
        self.assertIn(f"{_SCALE}:{line} | factor = -factor", proc.stdout.splitlines())


def _occurrences(text: str, old: str) -> int:
    """Matches of `old` in `text`, overlapping ones included."""
    return sum(1 for i in range(len(text)) if text.startswith(old, i))


class MutantWindowTest(unittest.TestCase):
    """PR #446 review, F4: a widened window must match exactly once counting overlapping
    matches, so the line the sweep deletes is always the line it reports."""

    def setUp(self) -> None:
        self.sweep = _load("mutation_sweep")

    def test_a_window_that_matches_twice_with_overlap_is_not_unique(self) -> None:
        for sep in ("\n", "\r\n"):
            text = ("echo x" + sep) * 4
            starts, lines = self.sweep._index_lines(text)
            for line in (1, 2, 3, 4):
                with self.subTest(sep=repr(sep), line=line):
                    self.assertIsNone(self.sweep._build_mutant(text, starts, lines, line, 1))
            # Widened to the whole file the window is unique, as today's rule allows.
            candidate = self.sweep._build_mutant(text, starts, lines, 3, 3)
            self.assertIsNotNone(candidate)
            self.assertEqual(text.replace(candidate[1], candidate[2], 1).split(sep),
                             ["echo x", "echo x", "", "echo x", ""])

    def test_the_mutated_line_is_always_the_reported_one(self) -> None:
        texts = (
            "echo x\n" * 4,
            "echo x\r\n" * 4,
            "a\nb\na\nb\na\n",
            "x\nx\ny\nx\nx\n",
            "x = 1\r\nx = 1\r\ny = 2\r\nx = 1\r\n",
            "aa\naa\naa\n",
        )
        for text in texts:
            starts, lines = self.sweep._index_lines(text)
            for line in range(1, len(lines)):
                for context in range(4):
                    candidate = self.sweep._build_mutant(text, starts, lines, line, context)
                    if candidate is None:
                        continue
                    _k, old, new = candidate
                    with self.subTest(text=text, line=line, context=context):
                        self.assertEqual(_occurrences(text, old), 1)
                        expected = text[: starts[line]] + text[starts[line] + len(lines[line]):]
                        self.assertEqual(text.replace(old, new, 1), expected)


if __name__ == "__main__":
    unittest.main()
