from __future__ import annotations

import ctypes
import importlib
import inspect
import tempfile
import unittest
from pathlib import Path


_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_STANDARD_INFO_CLASS = 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _query_standard_info(path: str):
    module = importlib.import_module("dayz_mcp.win32_fileinfo")
    kernel32 = module.bind_common_kernel32()
    handle = kernel32.CreateFileW(
        path,
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", path)
    try:
        info = module.FILE_STANDARD_INFO()
        ctypes.memset(ctypes.addressof(info), 0xFF, ctypes.sizeof(info))
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            _FILE_STANDARD_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise OSError(
                ctypes.get_last_error(),
                "GetFileInformationByHandleEx failed",
                path,
            )
        return info
    finally:
        kernel32.CloseHandle(handle)


class Win32FileStandardInfoTests(unittest.TestCase):
    def test_layout_is_win32_boolean_not_bool(self) -> None:
        module = importlib.import_module("dayz_mcp.win32_fileinfo")
        info = module.FILE_STANDARD_INFO
        self.assertEqual(ctypes.sizeof(info), 24)
        self.assertEqual(info.DeletePending.offset, 20)
        self.assertEqual(info.DeletePending.size, 1)
        self.assertEqual(info.Directory.offset, 21)
        self.assertEqual(info.Directory.size, 1)

    def test_directory_flag_from_kernel_is_true_for_a_real_directory_and_false_for_a_real_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "regular.bin"
            file_path.write_bytes(b"x")
            directory_info = _query_standard_info(str(root))
            file_info = _query_standard_info(str(file_path))
            self.assertTrue(directory_info.Directory, "kernel Directory on a real directory")
            self.assertFalse(file_info.Directory, "kernel Directory on a real file")
            self.assertFalse(directory_info.DeletePending)
            self.assertFalse(file_info.DeletePending)

    def test_both_consumers_import_the_shared_struct(self) -> None:
        shared = importlib.import_module("dayz_mcp.win32_fileinfo").FILE_STANDARD_INFO
        pinned = importlib.import_module("dayz_mcp.pinned_keyfile")
        authority = importlib.import_module("dayz_mcp.request_path_authority")
        self.assertIs(pinned._FILE_STANDARD_INFO, shared)
        self.assertIs(authority._FILE_STANDARD_INFO, shared)
        for name in ("pinned_keyfile", "request_path_authority"):
            source = inspect.getsource(importlib.import_module(f"dayz_mcp.{name}"))
            self.assertNotIn("class _FILE_STANDARD_INFO", source)
            self.assertNotIn("class FILE_STANDARD_INFO", source)


if __name__ == "__main__":
    unittest.main()
