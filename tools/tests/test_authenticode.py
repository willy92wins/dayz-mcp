from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dayz_mcp import authenticode


_KERNEL32 = r"C:\Windows\System32\kernel32.dll"
_MICROSOFT_SUBJECT = (
    "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
)


class AuthenticodeSubjectTest(unittest.TestCase):
    def test_measured_valve_subjects_are_exact(self) -> None:
        self.assertEqual(
            authenticode.VALVE_SIGNER_SUBJECTS,
            frozenset(
                {
                    "CN=Valve Corp., O=Valve Corp., L=Bellevue, S=Washington, C=US",
                    "CN=Valve, O=Valve, L=Bellevue, S=WA, C=US",
                }
            ),
        )
        self.assertNotIn("CN=Valve Corp.", authenticode.VALVE_SIGNER_SUBJECTS)
        self.assertNotIn("CN=Valve", authenticode.VALVE_SIGNER_SUBJECTS)

    def test_microsoft_signed_system_file_is_not_valve(self) -> None:
        self.assertEqual(authenticode.signer_subject(_KERNEL32), _MICROSOFT_SUBJECT)
        self.assertFalse(authenticode.is_valve_signed(_KERNEL32))

    def test_copy_of_a_microsoft_dll_is_not_valve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / "kernel32.dll"
            shutil.copyfile(_KERNEL32, copy)
            subject = authenticode.signer_subject(str(copy))
            self.assertNotIn(subject, authenticode.VALVE_SIGNER_SUBJECTS)
            self.assertFalse(authenticode.is_valve_signed(str(copy)))

    def test_unsigned_file_is_not_valve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unsigned = Path(temporary) / "unsigned.dll"
            unsigned.write_bytes(b"not a signed image")
            self.assertIsNone(authenticode.signer_subject(str(unsigned)))
            self.assertFalse(authenticode.is_valve_signed(str(unsigned)))
            self.assertIsNone(authenticode.signer_subject(str(Path(temporary) / "missing.dll")))
            self.assertFalse(authenticode.is_valve_signed(temporary))

    def test_only_a_measured_subject_passes_the_predicate(self) -> None:
        valve = "CN=Valve Corp., O=Valve Corp., L=Bellevue, S=Washington, C=US"
        with patch.object(authenticode, "signer_subject", return_value=valve):
            self.assertTrue(authenticode.is_valve_signed(r"C:\signed.dll"))
        with patch.object(
            authenticode, "signer_subject", return_value=_MICROSOFT_SUBJECT
        ):
            self.assertFalse(authenticode.is_valve_signed(r"C:\signed.dll"))
        with patch.object(authenticode, "signer_subject", return_value=None):
            self.assertFalse(authenticode.is_valve_signed(r"C:\signed.dll"))

    def test_unsigned_handle_loses_to_a_signed_path(self) -> None:
        signed = Path(r"C:\Program Files (x86)\Steam\steamclient.dll")
        self.assertTrue(signed.is_file())
        self.assertTrue(authenticode.is_valve_signed(str(signed)))
        with tempfile.TemporaryDirectory() as temporary:
            unsigned = Path(temporary) / "steamclient.dll"
            unsigned.write_bytes(b"not a signed image")
            handle = authenticode._open_read_handle(str(unsigned))
            self.assertIsNotNone(handle)
            assert handle is not None
            try:
                self.assertFalse(
                    authenticode.is_valve_signed_handle(handle, path=str(signed))
                )
                self.assertNotIn(
                    authenticode.signer_subject_handle(handle, path=str(signed)),
                    authenticode.VALVE_SIGNER_SUBJECTS,
                )
            finally:
                authenticode._close_handle(handle)


if __name__ == "__main__":
    unittest.main()
