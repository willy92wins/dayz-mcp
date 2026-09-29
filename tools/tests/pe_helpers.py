"""Minimal x64 PE bytes shared by tests that need a file to look like an executable.

Moved out of test_install_mcp.py so test modules do not import each other.
"""
from __future__ import annotations

import struct
from pathlib import Path


def write_fake_x64_pe(path: Path) -> None:
    payload = bytearray(512)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x94, 0xF0)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    path.write_bytes(payload)
