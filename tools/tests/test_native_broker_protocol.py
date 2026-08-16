from __future__ import annotations

import hashlib
import json
import struct
import unittest

from dayz_mcp import native_broker_protocol as protocol


class NativeBrokerProtocolTests(unittest.TestCase):
    def test_private_worker_frame_is_canonical_and_round_trips_public_request(self) -> None:
        request = b'{"dev_root":"P:\\\\ExampleMod_Suite","mod":"ExampleMod","version":1}'
        frame = protocol.encode_request(
            protocol.BrokerKind.PRIVATE_WORKER,
            {"request_sha256": hashlib.sha256(request).hexdigest()},
            stdin=request,
        )

        decoded = protocol.decode_request(frame)

        self.assertEqual(decoded.kind, protocol.BrokerKind.PRIVATE_WORKER)
        self.assertEqual(decoded.stdin, request)
        self.assertEqual(decoded.payload["request_sha256"], hashlib.sha256(request).hexdigest())
        magic, version, kind, flags, payload_size, stdin_size, stdin_sha = struct.unpack(
            "<4sBBHII32s", frame[:48]
        )
        self.assertEqual((magic, version, kind, flags), (b"DZM1", 1, 1, 0))
        self.assertEqual(stdin_size, len(request))
        self.assertEqual(stdin_sha, hashlib.sha256(request).digest())
        payload_bytes = frame[48 : 48 + payload_size]
        self.assertEqual(
            payload_bytes,
            json.dumps(decoded.payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def test_lifecycle_and_addon_payloads_are_closed(self) -> None:
        lifecycle = {
            "command": "start",
            "launch_operation_id": "12345678-1234-4234-8234-1234567890ab",
            "run_id": "87654321-4321-4321-8321-ba0987654321",
        }
        encoded = protocol.encode_request(
            protocol.BrokerKind.LIFECYCLE_CLI,
            lifecycle,
            stdin=b'{"request":"public"}',
        )
        self.assertEqual(protocol.decode_request(encoded).payload, lifecycle)

        targeted_status = {
            "command": "status",
            "launch_operation_id": None,
            "run_id": lifecycle["run_id"],
        }
        self.assertEqual(
            protocol.decode_request(
                protocol.encode_request(
                    protocol.BrokerKind.LIFECYCLE_CLI, targeted_status
                )
            ).payload,
            targeted_status,
        )

        addon = {
            "clear": True,
            "pack_only": False,
            "prefix": "ExampleMod",
            "source": r"P:\ExampleMod",
            "target": r"P:\Mods\@ExampleMod\Addons",
            "temp": r"P:\temp\ExampleMod",
        }
        self.assertEqual(
            protocol.decode_request(
                protocol.encode_request(protocol.BrokerKind.ADDON_BUILDER, addon)
            ).payload,
            addon,
        )

        invalid = (
            (protocol.BrokerKind.LIFECYCLE_CLI, {**lifecycle, "argv": ["bad"]}),
            (protocol.BrokerKind.LIFECYCLE_CLI, {**lifecycle, "command": "shell"}),
            (protocol.BrokerKind.ADDON_BUILDER, {**addon, "executable": "bad.exe"}),
            (protocol.BrokerKind.ADDON_BUILDER, {**addon, "lease_token": "secret"}),
            (protocol.BrokerKind.PRIVATE_WORKER, {"request_sha256": "0" * 64, "env": {}}),
        )
        for kind, payload in invalid:
            with self.subTest(kind=kind, payload=payload), self.assertRaises(
                protocol.BrokerProtocolError
            ):
                protocol.encode_request(kind, payload)

    def test_decoder_rejects_malformed_noncanonical_and_oversize_frames(self) -> None:
        valid = protocol.encode_request(
            protocol.BrokerKind.LIFECYCLE_CLI,
            {"command": "status", "launch_operation_id": None, "run_id": None},
        )
        malformed = {
            "magic": b"BAD!" + valid[4:],
            "version": valid[:4] + b"\x02" + valid[5:],
            "kind": valid[:5] + b"\x09" + valid[6:],
            "flags": valid[:6] + b"\x01\x00" + valid[8:],
            "truncated": valid[:-1],
            "trailing": valid + b"x",
            "stdin_hash": valid[:16] + (b"\0" * 32) + valid[48:],
        }
        for label, frame in malformed.items():
            with self.subTest(label=label), self.assertRaises(
                protocol.BrokerProtocolError
            ):
                protocol.decode_request(frame)

        noncanonical_payload = b'{"run_id":null, "command":"status","launch_operation_id":null}'
        header = struct.pack(
            "<4sBBHII32s",
            b"DZM1",
            1,
            2,
            0,
            len(noncanonical_payload),
            0,
            hashlib.sha256(b"").digest(),
        )
        with self.assertRaises(protocol.BrokerProtocolError):
            protocol.decode_request(header + noncanonical_payload)

        with self.assertRaises(protocol.BrokerProtocolError):
            protocol.encode_request(
                protocol.BrokerKind.PRIVATE_WORKER,
                {"request_sha256": "0" * 64},
                stdin=b"x" * 65_536,
            )

    def test_rejects_bool_kind_invalid_unicode_and_secret_nested_keys(self) -> None:
        cases = (
            (True, {"request_sha256": "0" * 64}),
            (protocol.BrokerKind.PRIVATE_WORKER, {"request_sha256": "0" * 64, "nested": {"token": "x"}}),
            (protocol.BrokerKind.ADDON_BUILDER, {"clear": False, "pack_only": False, "prefix": "Cafe\u0301", "source": r"P:\A", "target": r"P:\B", "temp": r"P:\C"}),
        )
        for kind, payload in cases:
            with self.subTest(kind=kind), self.assertRaises(protocol.BrokerProtocolError):
                protocol.encode_request(kind, payload)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
