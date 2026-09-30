"""source_of: one read per file, and a named red when that file changes (fb-20260819-153716-8491).

A test that reads source through inspect.getsource maps line numbers compiled at
import onto the text on disk now. These tests rewrite a fixture module on disk
the way another session would and check that source_of names the file instead
of handing back some other function's lines.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import ModuleType

from dayz_mcp import loopback, request_path_authority
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
'''

PLAIN_SOURCE = "def plain(value):\n    return value + 1\n"

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

    def _import(self, text: str) -> ModuleType:
        self.path.write_text(text, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            f"source_fixture_{uuid.uuid4().hex}", self.path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _assert_names_the_file(self, raised: SourceChangedMidSuite) -> str:
        message = str(raised)
        self.assertTrue(
            message.startswith(f"{SOURCE_CHANGED_MID_SUITE}: "), message
        )
        self.assertIn(str(self.path), message)
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
