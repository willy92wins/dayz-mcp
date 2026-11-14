"""Reference algorithm for the chat_read UTF-8 sanitize (S1 of #375).

``keep_valid_utf8_bytes`` is the Python reference for the Enforce helper
``MCPChatLog.KeepValidUtf8Bytes`` in ``addon/scripts/5_Mission/MCPChatLog.c``.
It lives here, not in a test module, because the suite forbids imports
between test modules: ``test_chat_read_utf8_sanitize.py`` pins the algorithm
against it, and ``test_chat_read_visibility.py`` uses it as the
``KeepValidUtf8Bytes`` shim for its translated ``MCPChatLog`` model.
"""
from __future__ import annotations


def keep_valid_utf8_bytes(data: bytes) -> tuple[bytes, bool]:
    """Reference implementation of KeepValidUtf8Bytes. Returns (cleaned, removed)."""
    cleaned = bytearray()
    removed = False
    index = 0
    total = len(data)
    while index < total:
        lead = data[index]
        if lead < 128:
            cleaned.append(lead)
            index += 1
            continue
        if 194 <= lead <= 223:
            seq_len = 2
            code_point = lead - 192
        elif 224 <= lead <= 239:
            seq_len = 3
            code_point = lead - 224
        elif 240 <= lead <= 244:
            seq_len = 4
            code_point = lead - 240
        else:
            seq_len = 0
            code_point = 0
        seq_valid = False
        if seq_len > 0 and index + seq_len <= total:
            seq_valid = True
            cont_index = 1
            while cont_index < seq_len and seq_valid:
                cont = data[index + cont_index]
                if cont < 128 or cont > 191:
                    seq_valid = False
                else:
                    code_point = code_point * 64 + (cont - 128)
                cont_index += 1
            if seq_valid and seq_len == 2 and code_point < 128:
                seq_valid = False
            if seq_valid and seq_len == 3 and code_point < 2048:
                seq_valid = False
            if seq_valid and seq_len == 3 and 55296 <= code_point <= 57343:
                seq_valid = False
            if seq_valid and seq_len == 4 and code_point < 65536:
                seq_valid = False
            if seq_valid and seq_len == 4 and code_point > 1114111:
                seq_valid = False
        if seq_valid:
            cleaned += data[index : index + seq_len]
            index += seq_len
        else:
            removed = True
            index += 1
    return bytes(cleaned), removed
