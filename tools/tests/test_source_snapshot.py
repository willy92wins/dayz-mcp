"""source_of: one read per file, and a named red when that file changes (fb-20260819-153716-8491).

A test that reads source through inspect.getsource maps line numbers compiled at
import onto the text on disk now. These tests rewrite a fixture module on disk
the way another session would and check that source_of names the file instead
of handing back some other function's lines, or a body the process never ran.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import tempfile
import threading
import unittest
import uuid
import warnings
from pathlib import Path
from types import ModuleType
from unittest import mock

from dayz_mcp import loopback, request_path_authority
from tests import _source_snapshot
from tests._source_snapshot import (
    SOURCE_CHANGED_MID_SUITE,
    SourceChangedMidSuite,
    source_of,
)

FIXTURE = '''\
import functools


def plain(value):
    return value + 1


def traced(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@traced
def decorated(value):
    return value * 2


class Holder:
    def method(self):
        return "method"

    @staticmethod
    def static():
        return "static"

    @classmethod
    def klass(cls):
        return "klass"

    @property
    def prop(self):
        return "prop"


def factory():
    def inner():
        return "inner"

    return inner


async def coroutine():
    return "coroutine"


shout = lambda text: text.upper()


def counter(start):
    count = [start]

    def step():
        count[0] += 1
        return count[0]

    return step


class Base:
    def greet(self):
        return "base"


class Child(Base):
    def greet(self):
        return "child+" + super().greet()


def squares(limit):
    return [value * value for value in range(limit) if value % 2 == 0]


def tagged(tag):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return (tag, fn(*args, **kwargs))

        return wrapper

    return decorate


@tagged("label")
def labelled():
    return "labelled"


@functools.lru_cache(maxsize=None)
def cached(value):
    return value * 3


class Memo:
    @functools.lru_cache(maxsize=None)
    def twice(self, value):
        return value * 5
'''

PLAIN_SOURCE = "def plain(value):\n    return value + 1\n"

# One body edited in place, on the line it had: no definition moves.
# (label, old text, new text, the object asked about, its qualname)
BODY_REWRITES = (
    ("a function", "return value + 1", "return value + 2",
     lambda module: module.plain, "plain"),
    ("a method", 'return "method"', 'return "METHOD"',
     lambda module: module.Holder.method, "Holder.method"),
    ("a closure", "count[0] += 1", "count[0] += 2",
     lambda module: module.counter(0), "counter.<locals>.step"),
)

# Four lines above everything, the way a session adding a constant moves a module.
MOVED = FIXTURE.replace(
    "import functools\n",
    "import functools\n\nFIRST = 1\nSECOND = 2\nTHIRD = 3\n",
    1,
)


class SourceOfTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "source_fixture.py"

    def _import(self, text: str, path: Path | None = None) -> ModuleType:
        path = self.path if path is None else path
        path.write_text(text, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            f"source_fixture_{uuid.uuid4().hex}", path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _assert_names_the_file(
        self, raised: SourceChangedMidSuite, path: Path | None = None
    ) -> str:
        message = str(raised)
        self.assertTrue(
            message.startswith(f"{SOURCE_CHANGED_MID_SUITE}: "), message
        )
        self.assertIn(str(self.path if path is None else path), message)
        return message

    def test_a_quiet_file_reads_exactly_what_inspect_reads(self) -> None:
        module = self._import(FIXTURE)
        holder = module.Holder
        for obj in (
            module,
            module.plain,
            module.traced,
            module.decorated,
            holder.method,
            holder().method,
            holder.static,
            vars(holder)["static"],
            holder.klass,
            holder.prop.fget,
            module.factory(),
            module.coroutine,
            module.shout,
        ):
            with self.subTest(obj=obj):
                self.assertEqual(inspect.getsource(obj), source_of(obj))
        self.assertEqual(PLAIN_SOURCE, source_of(module.plain))
        self.assertTrue(source_of(module.decorated).startswith("@traced\n"))

    def test_this_checkouts_modules_read_exactly_what_inspect_reads(self) -> None:
        for obj in (
            request_path_authority,
            loopback.read_key,
            loopback.Handler._handle_session,
        ):
            with self.subTest(obj=obj):
                self.assertEqual(inspect.getsource(obj), source_of(obj))

    def test_lines_inserted_after_the_import_are_named_not_misread(self) -> None:
        module = self._import(FIXTURE)
        self.path.write_text(MOVED, encoding="utf-8")

        # The defect: inspect reads the moved text at the imported line numbers.
        self.assertNotEqual(PLAIN_SOURCE, inspect.getsource(module.plain))
        with self.assertRaises(SourceChangedMidSuite) as raised:
            source_of(module.plain)
        message = self._assert_names_the_file(raised.exception)
        self.assertIn("plain starts at line 4 in memory and at 8 in the file", message)

    def test_a_module_whose_functions_moved_after_the_import_is_named(self) -> None:
        module = self._import(FIXTURE)
        self.path.write_text(MOVED, encoding="utf-8")

        with self.assertRaises(SourceChangedMidSuite) as raised:
            source_of(module)
        self._assert_names_the_file(raised.exception)

    def test_a_body_rewritten_in_place_after_the_import_is_named(self) -> None:
        """Review R1 F1: nothing moves, so only the compiled code can tell the
        body on disk from the one this process runs."""
        for index, (label, old, new, pick, qualname) in enumerate(BODY_REWRITES):
            with self.subTest(label):
                self.assertEqual(FIXTURE.count(old), 1, old)
                path = self.path.with_name(f"rewritten_{index}.py")
                target = pick(self._import(FIXTURE, path))
                path.write_text(FIXTURE.replace(old, new), encoding="utf-8")

                # The defect: inspect hands back a body this process never ran.
                self.assertIn(new, inspect.getsource(target))
                with self.assertRaises(SourceChangedMidSuite) as raised:
                    source_of(target)
                message = self._assert_names_the_file(raised.exception, path)
                self.assertIn(f"{qualname} at line", message)
                self.assertIn("compiles to other code", message)

    def test_a_module_with_a_body_rewritten_after_the_import_is_named(self) -> None:
        module = self._import(FIXTURE)
        self.path.write_text(
            FIXTURE.replace('return "method"', 'return "METHOD"'), encoding="utf-8"
        )

        with self.assertRaises(SourceChangedMidSuite) as raised:
            source_of(module)
        self.assertIn("Holder.method at line", self._assert_names_the_file(raised.exception))

    def test_a_module_read_reaches_a_function_behind_a_callable_wrapper(self) -> None:
        """Review R2 F1: functools.lru_cache leaves a callable that is not a
        function and keeps the function as __wrapped__. A module read must
        still check it, at module level and in a class."""
        rewrites = (
            ("return value * 3", "return value * 4", "cached"),
            ("return value * 5", "return value * 6", "Memo.twice"),
        )
        for index, (old, new, qualname) in enumerate(rewrites):
            with self.subTest(qualname):
                self.assertEqual(FIXTURE.count(old), 1, old)
                path = self.path.with_name(f"wrapped_{index}.py")
                module = self._import(FIXTURE, path)
                self.assertFalse(inspect.isfunction(module.cached))
                self.assertFalse(inspect.isfunction(vars(module.Memo)["twice"]))
                path.write_text(FIXTURE.replace(old, new), encoding="utf-8")

                with self.assertRaises(SourceChangedMidSuite) as raised:
                    source_of(module)
                message = self._assert_names_the_file(raised.exception, path)
                self.assertIn(f"{qualname} at line", message)

    def test_a_warning_on_another_thread_survives_the_compile(self) -> None:
        """Review R2 F2: catch_warnings() around the compile changed the
        filters every thread reads, so a warning raised elsewhere during it
        vanished. The compile is held open while this thread warns."""
        module = self._import(FIXTURE)
        entered, release = threading.Event(), threading.Event()
        failures: list[BaseException] = []

        def held_compile(*args: object, **kwargs: object) -> object:
            entered.set()
            if not release.wait(10):
                raise AssertionError("the held compile was never released")
            return compile(*args, **kwargs)

        def read() -> None:
            try:
                source_of(module.plain)
            except BaseException as error:
                failures.append(error)

        reader = threading.Thread(target=read, daemon=True)
        with (
            mock.patch.object(_source_snapshot, "compile", held_compile, create=True),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", UserWarning)
            reader.start()
            try:
                self.assertTrue(entered.wait(10), "source_of never reached its compile")
                with self.assertRaises(UserWarning):
                    warnings.warn("raised on the test's own thread", UserWarning)
            finally:
                release.set()
                reader.join(10)
        self.assertFalse(reader.is_alive())
        self.assertEqual([], failures)

    def test_a_file_that_warns_at_compile_is_not_refused_under_an_error_filter(self) -> None:
        """An unchanged file whose compile warns (an invalid escape) does not
        compile while a filter turns that warning into an error. It falls
        back to the byte check instead of being refused."""
        text = 'def value():\n    return "\\q"\n'
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            module = self._import(text)

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            # The filter does stop this compile, so the fallback is what runs.
            with self.assertRaises(SyntaxError):
                compile(self.path.read_bytes(), str(self.path), "exec", dont_inherit=True)
            self.assertEqual(inspect.getsource(module.value), source_of(module.value))
            self.assertEqual(inspect.getsource(module), source_of(module))

        self.path.write_text(text + "\nLATE = 1\n", encoding="utf-8")
        with self.assertRaises(SourceChangedMidSuite) as raised:
            source_of(module.value)
        self.assertIn("changed on disk", self._assert_names_the_file(raised.exception))

    def test_an_unchanged_file_is_not_refused_after_its_code_ran_hot(self) -> None:
        """A closure, a method calling super(), decorated functions and a
        comprehension compile again to equal code. They ran hot first: 3.11+
        specialises bytecode in place, and co_code must not show it."""
        module = self._import(FIXTURE)
        step = module.counter(0)
        child = module.Child()
        holder = module.Holder()
        memo = module.Memo()
        for index in range(2000):
            step()
            child.greet()
            module.squares(6)
            module.labelled()
            module.decorated(3)
            holder.prop
            module.cached(index)
            memo.twice(index)
        for obj in (
            module,
            step,
            module.counter,
            module.Child.greet,
            child.greet,
            module.squares,
            module.labelled,
            module.tagged,
            module.decorated,
            holder.method,
            module.cached,
            vars(module.Memo)["twice"],
        ):
            with self.subTest(obj=obj):
                self.assertEqual(inspect.getsource(obj), source_of(obj))
        self.assertTrue(source_of(module.labelled).startswith('@tagged("label")\n'))

    def test_a_rewrite_after_the_first_read_is_named_for_every_object_of_that_file(self) -> None:
        module = self._import(FIXTURE)
        self.assertEqual(PLAIN_SOURCE, source_of(module.plain))
        # Appended below everything: no function moves, only the bytes change.
        self.path.write_text(FIXTURE + "\nLATE = 1\n", encoding="utf-8")

        for obj in (module.plain, module.Holder.method, module):
            with self.subTest(obj=obj):
                with self.assertRaises(SourceChangedMidSuite) as raised:
                    source_of(obj)
                self.assertIn("changed on disk", self._assert_names_the_file(raised.exception))

    def test_a_file_deleted_after_the_first_read_is_named(self) -> None:
        module = self._import(FIXTURE)
        source_of(module.plain)
        os.remove(self.path)

        with self.assertRaises(SourceChangedMidSuite) as raised:
            source_of(module.plain)
        self.assertIn("can no longer be read", self._assert_names_the_file(raised.exception))

    def test_a_class_is_refused_rather_than_guessed(self) -> None:
        module = self._import(FIXTURE)
        with self.assertRaisesRegex(TypeError, "module or a function"):
            source_of(module.Holder)


if __name__ == "__main__":
    unittest.main()
