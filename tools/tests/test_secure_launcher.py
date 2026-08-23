from __future__ import annotations

import hashlib
import asyncio
import inspect
import io
import json
import os
import struct
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import dayz_mcp.launcher_registry as registry
import dayz_mcp.native_broker_protocol as broker
import dayz_mcp.secure_launcher as launcher
from tests._bundle_paths import requires_installed_launcher


def _x64_pe() -> bytes:
    payload = bytearray(512)
    payload[0:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, 0x8664)
    struct.pack_into("<H", payload, 0x98, 0x20B)
    return bytes(payload)


class SecureLauncherRegistryTests(unittest.TestCase):
    def _private(self, name: str) -> object:
        self.assertTrue(hasattr(registry, name), f"missing private helper {name}")
        return getattr(registry, name)

    @requires_installed_launcher
    def test_canonical_registry_contains_only_native_dayz_test_and_public_route_accepts_only_id(self) -> None:
        self.assertFalse(hasattr(registry, "CANONICAL_REGISTRY"))
        canonical_registry = self._private("_CANONICAL_REGISTRY")
        self.assertIsInstance(canonical_registry, Path)
        payload = json.loads(
            canonical_registry.read_text(encoding="utf-8")
        )

        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(len(payload["launchers"]), 1)
        approved = payload["launchers"][0]
        self.assertEqual(
            set(approved),
            {"id", "relative_path", "root", "root_file_id", "sha256"},
        )
        self.assertEqual(approved["id"], "dayz-test-v1")
        self.assertEqual(approved["relative_path"], "dayz-test-launcher.exe")
        # The PE hash changes on every reproducible rebuild; compare against the bundle
        # instead of a literal. root is deliberately NOT asserted here: it pins the entry
        # to the tree it was published from, so any copy of the repo would fail. Location,
        # identity and binary swaps are covered by checks/check_native_launcher_registry.py
        # outside this suite, plus verify_bundle and the CAS receipts.
        bundle = canonical_registry.parent / "native-launchers" / "dayz-test-v1"
        self.assertEqual(
            approved["sha256"],
            hashlib.sha256(
                (bundle / approved["relative_path"]).read_bytes()
            ).hexdigest().upper(),
        )
        for obsolete_public_name in (
            "create_registry_entry",
            "validate_launcher_registry",
            "load_approved_launcher",
        ):
            with self.subTest(name=obsolete_public_name):
                self.assertFalse(hasattr(launcher, obsolete_public_name))
        self.assertEqual(
            tuple(inspect.signature(registry.open_approved_launcher).parameters),
            ("launcher_id",),
        )
        self.assertEqual(
            tuple(inspect.signature(launcher.run_secure_launcher).parameters),
            ("launcher_id", "max_wait_s"),
        )
        self.assertFalse(inspect.iscoroutinefunction(launcher.run_secure_launcher))

    @requires_installed_launcher
    def test_productive_open_uses_only_the_canonical_registry(self) -> None:
        with self.assertRaisesRegex(ValueError, "launcher_not_approved"):
            registry.open_approved_launcher("unavailable")

        source = inspect.getsource(registry.open_approved_launcher)
        self.assertIn("_read_canonical_registry", source)
        self.assertNotIn("registry_path", source)

    def test_json_parser_rejects_duplicate_keys_at_every_level_and_bool_version(self) -> None:
        parse_registry = self._private("_parse_launcher_registry")
        assert callable(parse_registry)

        self.assertEqual(
            parse_registry('{"format_version":1,"launchers":[]}'),
            [],
        )
        invalid_documents = (
            '{"format_version":1,"format_version":1,"launchers":[]}',
            '{"format_version":1,"launchers":[],"launchers":[]}',
            (
                '{"format_version":1,"launchers":[{' 
                '"id":"fixture","root":"C:\\\\fixture",'
                '"root_file_id":{"volume_serial_number":1,'
                '"volume_serial_number":1,"file_id":"00000000000000000000000000000000"},'
                '"relative_path":"consumer.exe","sha256":"'
                + ("0" * 64)
                + '"}]}'
            ),
            '{"format_version":true,"launchers":[]}',
            '{"format_version":1.0,"launchers":[]}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaisesRegex(
                ValueError, "invalid_launcher_registry"
            ):
                parse_registry(document)

    def test_arbitrary_registry_helpers_are_private_and_offline_only(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        validate_payload = self._private("_validate_launcher_registry_payload")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(validate_payload)
        assert callable(open_entry)

        run_source = inspect.getsource(launcher.run_secure_launcher)
        main_source = inspect.getsource(launcher.main)
        for helper_name in (
            "_create_registry_entry_for_test",
            "_validate_launcher_registry_payload",
            "_open_registry_entry_for_test",
        ):
            with self.subTest(helper=helper_name):
                self.assertNotIn(helper_name, run_source)
                self.assertNotIn(helper_name, main_source)

    def test_private_fixture_helpers_validate_native_path_schema_and_hash(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        validate_payload = self._private("_validate_launcher_registry_payload")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(validate_payload)
        assert callable(open_entry)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "consumer.exe"
            native.write_bytes(b"fixture")
            entry = create_entry("fixture", root, native.name)
            validated = validate_payload(
                {"format_version": 1, "launchers": [entry]}
            )

            with open_entry(validated[0]) as opened:
                self.assertEqual(opened.path, native)

            rejected = root / "consumer.txt"
            rejected.write_bytes(b"fixture")
            with self.assertRaisesRegex(
                ValueError, "launcher_requires_native_executable"
            ):
                create_entry("rejected", root, rejected.name)

            for payload in (
                {"format_version": 1, "launchers": [entry], "extra": True},
                {"format_version": 1, "launchers": [entry, entry]},
                {
                    "format_version": 1,
                    "launchers": [{**entry, "relative_path": "..\\escape.exe"}],
                },
                {
                    "format_version": 1,
                    "launchers": [{**entry, "sha256": "0" * 64}],
                },
            ):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    entries = validate_payload(payload)
                    with open_entry(entries[0]):
                        pass

    def test_opened_launcher_validates_pe_only_through_its_pinned_stream(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(open_entry)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "consumer.exe"
            native.write_bytes(_x64_pe())
            entry = create_entry("fixture", root, native.name)

            with open_entry(entry) as opened, patch.object(
                Path,
                "open",
                side_effect=AssertionError("must not reopen launcher by path"),
            ):
                opened.validate_native_pe()

            invalid = root / "invalid.exe"
            invalid.write_bytes(b"not-a-pe")
            invalid_entry = create_entry("invalid", root, invalid.name)
            with open_entry(invalid_entry) as opened, self.assertRaisesRegex(
                ValueError, "native_launcher_not_native_x64_pe"
            ):
                opened.validate_native_pe()

    def test_opened_launcher_accredits_root_debug_hfile_by_identity_and_path(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(open_entry)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "consumer.exe"
            native.write_bytes(_x64_pe())
            entry = create_entry("fixture", root, native.name)

            with open_entry(entry) as opened:
                raw_handle_file_id = bytes.fromhex(
                    opened.file_identity.file_id
                )[::-1].hex().upper()
                matching_identity = SimpleNamespace(
                    volume_serial_number=opened.file_identity.volume_serial_number,
                    file_id=raw_handle_file_id,
                )
                stat_text_identity = SimpleNamespace(
                    volume_serial_number=opened.file_identity.volume_serial_number,
                    file_id=opened.file_identity.file_id,
                )
                wrong_identity = SimpleNamespace(
                    volume_serial_number=opened.file_identity.volume_serial_number,
                    file_id="F" * 32,
                )
                with patch(
                    "dayz_mcp.request_path_authority._file_identity",
                    return_value=matching_identity,
                ), patch(
                    "dayz_mcp.request_path_authority._final_handle_path",
                    return_value=str(opened.path),
                ):
                    self.assertTrue(opened.approve_root_debug_image(123))
                with patch(
                    "dayz_mcp.request_path_authority._file_identity",
                    return_value=stat_text_identity,
                ), patch(
                    "dayz_mcp.request_path_authority._final_handle_path",
                    return_value=str(opened.path),
                ):
                    self.assertFalse(opened.approve_root_debug_image(123))
                with patch(
                    "dayz_mcp.request_path_authority._file_identity",
                    return_value=wrong_identity,
                ), patch(
                    "dayz_mcp.request_path_authority._final_handle_path",
                    return_value=str(opened.path),
                ):
                    self.assertFalse(opened.approve_root_debug_image(123))
                with patch(
                    "dayz_mcp.request_path_authority._file_identity",
                    return_value=matching_identity,
                ), patch(
                    "dayz_mcp.request_path_authority._final_handle_path",
                    return_value=str(root / "other.exe"),
                ):
                    self.assertFalse(opened.approve_root_debug_image(123))

    def test_opened_launcher_checks_embedded_marker_only_on_its_pinned_stream(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(open_entry)
        marker = b"DAYZ_MCP_MANIFEST_SHA256=" + b"A" * 64

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, suffix, expected_error in (
                ("unique", marker, None),
                ("missing", b"", "launcher_embedded_marker_mismatch"),
                ("duplicate", marker + marker, "launcher_embedded_marker_mismatch"),
            ):
                with self.subTest(label=label):
                    native = root / f"{label}.exe"
                    native.write_bytes(_x64_pe() + suffix)
                    entry = create_entry(label, root, native.name)
                    with open_entry(entry) as opened, patch.object(
                        Path,
                        "open",
                        side_effect=AssertionError("must not reopen launcher by path"),
                    ):
                        if expected_error is None:
                            opened.require_unique_embedded_marker(marker)
                        else:
                            with self.assertRaisesRegex(ValueError, expected_error):
                                opened.require_unique_embedded_marker(marker)

    @unittest.skipUnless(os.name == "nt", "Windows sharing semantics required")
    def test_pinned_native_handle_allows_read_but_blocks_in_place_write(self) -> None:
        create_entry = self._private("_create_registry_entry_for_test")
        open_entry = self._private("_open_registry_entry_for_test")
        assert callable(create_entry)
        assert callable(open_entry)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "consumer.exe"
            native.write_bytes(b"fixture")
            entry = create_entry("fixture", root, native.name)

            with open_entry(entry) as opened:
                with native.open("rb") as parallel_reader:
                    self.assertEqual(parallel_reader.read(), b"fixture")
                with self.assertRaises(OSError):
                    with native.open("r+b") as writer:
                        writer.write(b"changed")
                opened.revalidate()

            with native.open("r+b") as writer:
                writer.write(b"changed")

    def test_canonical_registry_reader_propagates_name_surrogate_rejection(self) -> None:
        read_registry = self._private("_read_canonical_registry")
        assert callable(read_registry)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = root / "approved-launchers.json"
            registry_path.write_text(
                '{"format_version":1,"launchers":[]}', encoding="utf-8"
            )
            with patch.object(
                registry, "_CANONICAL_REGISTRY", registry_path
            ), patch.object(
                registry,
                "_reject_path_name_surrogates",
                side_effect=(
                    None,
                    ValueError("launcher_registry_name_surrogate"),
                ),
            ) as reject:
                with self.assertRaisesRegex(
                    ValueError, "launcher_registry_name_surrogate"
                ):
                    read_registry()
            self.assertEqual(reject.call_count, 2)
            self.assertEqual(
                reject.call_args_list,
                [
                    unittest.mock.call(
                        registry_path, error_code="launcher_registry_name_surrogate"
                    ),
                    unittest.mock.call(
                        registry_path, error_code="launcher_registry_name_surrogate"
                    ),
                ],
            )

    def test_name_surrogate_bit_is_rejected_and_other_reparse_tags_are_allowed(self) -> None:
        reject = self._private("_reject_path_name_surrogates")
        assert callable(reject)

        class StatResult:
            st_reparse_tag = registry.NAME_SURROGATE_BIT

        fixture = Path("C:\\fixture\\approved-launchers.json")
        with patch.object(registry.os, "lstat", return_value=StatResult()):
            with self.assertRaisesRegex(ValueError, "registry_reparse"):
                reject(fixture, error_code="registry_reparse")

        StatResult.st_reparse_tag = 0x8000001A
        with patch.object(registry.os, "lstat", return_value=StatResult()):
            reject(fixture, error_code="registry_reparse")


def _legacy_pop0_redact(secrets: tuple[bytes, ...], chunks: tuple[bytes, ...]) -> bytes:
    """Byte-identical predecessor of _IncrementalRedactor._consume (pop(0))."""

    pending = bytearray()
    max_secret = max((len(value) for value in secrets if value), default=1)
    kept = tuple(sorted({value for value in secrets if value}, key=len, reverse=True))
    output = bytearray()

    def consume(*, final: bool) -> None:
        while pending and (final or len(pending) >= max_secret):
            matched = next(
                (secret for secret in kept if pending.startswith(secret)),
                None,
            )
            if matched is not None:
                del pending[: len(matched)]
                output.extend(b"[REDACTED]")
            else:
                output.append(pending.pop(0))

    for chunk in chunks:
        pending.extend(chunk)
        consume(final=False)
    consume(final=True)
    return bytes(output)


class PrivateIncrementalRedactorTests(unittest.TestCase):
    def test_redacts_two_secrets_split_across_arbitrary_chunks(self) -> None:
        self.assertTrue(hasattr(launcher, "_IncrementalRedactor"))
        redactor = launcher._IncrementalRedactor(  # type: ignore[attr-defined]
            (b"identity-secret", b"lease-secret")
        )
        chunks = (
            b"before identity-",
            b"secret middle lease-",
            b"secret after",
        )
        output = b"".join(redactor.feed(chunk) for chunk in chunks)
        output += redactor.flush()
        self.assertEqual(
            output,
            b"before [REDACTED] middle [REDACTED] after",
        )
        self.assertNotIn(b"identity-secret", output)
        self.assertNotIn(b"lease-secret", output)

    def _redact(self, secrets: tuple[bytes, ...], chunks: tuple[bytes, ...]) -> bytes:
        redactor = launcher._IncrementalRedactor(secrets)  # type: ignore[attr-defined]
        output = b"".join(redactor.feed(chunk) for chunk in chunks)
        return output + redactor.flush()

    def test_redactor_matches_legacy_pop0_including_split_secrets(self) -> None:
        cases = (
            ((b"identity-secret", b"lease-secret"), (b"before identity-", b"secret middle lease-", b"secret after")),
            ((b"aa",), (b"a", b"aa", b"a")),
            ((b"secret", b"sec"), (b"xxsec", b"retxxsecxx")),
            ((b"ab",), (b"", b"ab", b"")),
            ((b"token",), (b"no-match-here",)),
            ((b"xy",), tuple(bytes([byte]) for byte in b"abxycdxy")),
            ((b"long-secret-value",), (b"long-se", b"cret-value-tail")),
        )
        for secrets, chunks in cases:
            with self.subTest(secrets=secrets, chunks=chunks):
                self.assertEqual(
                    self._redact(secrets, chunks),
                    _legacy_pop0_redact(secrets, chunks),
                )

    def test_unmatched_feed_is_linear_not_pop0(self) -> None:
        payload = bytes(range(256)) * 400
        secret = b"this-secret-does-not-appear"
        chunks = (payload,)
        current = self._redact((secret,), chunks)
        legacy = _legacy_pop0_redact((secret,), chunks)
        self.assertEqual(current, legacy)
        self.assertEqual(current, payload)

    def test_repeated_and_absent_real_secrets_stay_linear_across_sizes(self) -> None:
        identity = '{"session_id":"identity-secret"}'
        lease = "lease-secret"
        secrets = (
            identity.encode("utf-8"),
            identity.encode("utf-16le"),
            lease.encode("utf-8"),
            lease.encode("utf-16le"),
        )
        repeated = lease.encode("utf-8")
        absent = identity.encode("utf-16le")
        # Unmatched filler keeps pending large so pop(0) memmoves the
        # tail on every miss. A dense ".lease-secret" stream only pops
        # the separator and hides that cost under 0.30 s at 200 kB.
        unit = b"X" * 256 + repeated
        small = unit * 4
        self.assertEqual(
            self._redact(secrets, (small,)),
            _legacy_pop0_redact(secrets, (small,)),
        )
        self.assertNotIn(absent, small)

        sizes = (100_000, 400_000)
        timings: list[float] = []
        for size in sizes:
            payload = (unit * (size // len(unit) + 1))[:size]
            self.assertIn(repeated, payload)
            self.assertNotIn(absent, payload)
            started = time.perf_counter()
            output = self._redact(secrets, (payload,))
            timings.append(time.perf_counter() - started)
            for secret in secrets:
                self.assertNotIn(secret, output)
            self.assertIn(b"[REDACTED]", output)

        # 4x size: indexed growth measured ~3.6-4.6x, pop(0) ~10x. 7x sits
        # between them and holds under load, where an absolute cap accused
        # the correct implementation (0.666 s with a valid 4.63x slope).
        self.assertLess(timings[-1] / timings[0], 7.0)


class SecureLauncherRuntimeTests(unittest.TestCase):
    def test_shared_secure_request_executor_is_async_and_redacts_before_sink(self) -> None:
        self.assertTrue(hasattr(launcher, "execute_secure_launcher_request"))
        execute = launcher.execute_secure_launcher_request
        self.assertTrue(inspect.iscoroutinefunction(execute))
        public_request = b'{"dev_root":"P:\\Suite","mod":"ExampleMod","version":1}'
        digest = hashlib.sha256(public_request).hexdigest()
        captured: list[tuple[str, bytes]] = []
        started: list[bool] = []

        async def transaction(_raw: bytes, **kwargs: object) -> int:
            consumer = kwargs["consumer"]
            return await consumer(
                canonical_request=public_request,
                request_sha256=digest,
                client_identity_json='{"session_id":"identity-secret"}',
                lease_token="lease-secret",
                cancel_event=asyncio.Event(),
                accredited_paths=object(),
                heartbeat_supervisor=object(),
            )

        async def native_launch(_opened: object, **kwargs: object) -> int:
            kwargs["output_sink"]("stdout", b"lease-")
            kwargs["output_sink"]("stdout", b"secret")
            return 0

        async def execution_started() -> None:
            started.append(True)

        with patch.object(
            launcher, "execute_native_launcher_transaction", side_effect=transaction
        ), patch.object(
            launcher, "serialize_normal_daemon_policy", return_value="policy-json"
        ), patch(
            "dayz_mcp.native_launcher_backend.launch_registered_native",
            side_effect=native_launch,
        ):
            result = asyncio.run(
                execute(
                    public_request,
                    opened_launcher=object(),
                    verified_bundle=SimpleNamespace(sealed_policies=(object(),)),
                    control_client=object(),
                    daemon_policy=object(),
                    output_sink=lambda channel, chunk: captured.append((channel, chunk)),
                    execution_started_cb=execution_started,
                )
            )

        self.assertEqual(result, 0)
        self.assertEqual(started, [True])
        self.assertEqual(captured, [("stdout", b"[REDACTED]")])

    def test_launcher_reads_public_stdin_waits_indefinitely_and_routes_token_only_internally(self) -> None:
        class Opened:
            validate_calls = 0

            def __enter__(self) -> "Opened":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def validate_native_pe(self) -> None:
                self.validate_calls += 1

        opened = Opened()
        class Bundle:
            sealed_policies = (object(),)
            exited = False

            def __enter__(self) -> "Bundle":
                return self

            def __exit__(self, *_args: object) -> None:
                self.exited = True

        bundle = Bundle()
        public_request = b'{"version":1,"dev_root":"P:\\\\ExampleMod_Suite","mod":"ExampleMod"}'
        request_digest = hashlib.sha256(public_request).hexdigest()
        stdout_bytes = io.BytesIO()
        stderr_bytes = io.BytesIO()
        transaction_arguments: dict[str, object] = {}

        async def transaction(raw_request: bytes, **kwargs: object) -> int:
            transaction_arguments["raw_request"] = raw_request
            transaction_arguments.update(kwargs)
            consumer = kwargs["consumer"]
            return await consumer(
                canonical_request=public_request,
                request_sha256=request_digest,
                client_identity_json='{"platform":"unknown","session_id":"private"}',
                lease_token="private-lease-token",
                cancel_event=__import__("asyncio").Event(),
                accredited_paths=object(),
                heartbeat_supervisor=object(),
            )

        self.assertFalse(inspect.iscoroutinefunction(launcher.run_secure_launcher))
        import dayz_mcp.native_launcher_backend as native_backend

        async def native_launch(
            _opened: object,
            *,
            verified_bundle: object,
            identity_json: str,
            lease_token: str,
            daemon_policy_json: str,
            canonical_request: bytes,
            cancel_event: object,
            output_sink: object,
        ) -> int:
            self.assertIs(_opened, opened)
            self.assertIs(verified_bundle, bundle)
            self.assertEqual(identity_json, '{"platform":"unknown","session_id":"private"}')
            self.assertEqual(lease_token, "private-lease-token")
            self.assertEqual(daemon_policy_json, "policy-json")
            self.assertIsNotNone(cancel_event)
            decoded = broker.decode_request(canonical_request)
            self.assertIs(decoded.kind, broker.BrokerKind.PRIVATE_WORKER)
            self.assertEqual(decoded.stdin, public_request)
            self.assertEqual(decoded.payload, {"request_sha256": request_digest})
            self.assertTrue(callable(output_sink))
            output_sink("stdout", b"before private-lease-")
            output_sink("stdout", b"token after")
            identity_utf16 = identity_json.encode("utf-16le")
            output_sink("stderr", b"before " + identity_utf16[:17])
            output_sink("stderr", identity_utf16[17:] + b" after")
            return 0

        with patch.object(launcher.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(public_request))), patch.object(
            launcher.sys, "stdout", SimpleNamespace(buffer=stdout_bytes)
        ), patch.object(
            launcher.sys, "stderr", SimpleNamespace(buffer=stderr_bytes)
        ), patch.object(
            launcher, "open_approved_launcher", return_value=opened
        ) as canonical_open, patch.object(
            launcher, "_load_verified_bundle", return_value=bundle
        ), patch.object(
            launcher, "load_normal_daemon_policy", return_value=object()
        ), patch.object(
            launcher, "serialize_normal_daemon_policy", return_value="policy-json"
        ), patch.object(
            launcher, "ControlClient", return_value=object()
        ) as client, patch.object(
            launcher, "execute_native_launcher_transaction", side_effect=transaction
        ), patch.object(
            native_backend, "launch_registered_native", side_effect=native_launch
        ):
            self.assertEqual(launcher.run_secure_launcher("fixture"), 0)

        canonical_open.assert_called_once_with("fixture")
        self.assertEqual(opened.validate_calls, 1)
        self.assertEqual(transaction_arguments["raw_request"], public_request)
        self.assertIsNone(transaction_arguments["max_wait_s"])
        identity = client.call_args.kwargs["identity"]
        self.assertEqual(identity.platform, "unknown")
        self.assertNotIn(b"private-lease-token", public_request)
        self.assertEqual(stdout_bytes.getvalue(), b"before [REDACTED] after")
        self.assertEqual(stderr_bytes.getvalue(), b"before [REDACTED] after")
        self.assertTrue(bundle.exited)

    def test_public_request_is_bounded_before_registry_or_control_state(self) -> None:
        for raw in (b"", b"x" * (launcher.MAX_PUBLIC_REQUEST_BYTES + 1)):
            with self.subTest(length=len(raw)), patch.object(
                launcher.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(raw))
            ), patch.object(launcher, "open_approved_launcher") as canonical_open:
                with self.assertRaisesRegex(ValueError, "invalid_dayz_test_request"):
                    launcher.run_secure_launcher("fixture")
            canonical_open.assert_not_called()

    def test_explicit_wait_must_be_positive_finite_before_reading_stdin(self) -> None:
        for value in (0.0, -1.0, float("nan"), float("inf"), True):
            with self.subTest(value=value), patch.object(
                launcher, "_read_public_request"
            ) as read:
                with self.assertRaisesRegex(ValueError, "invalid_launcher_wait"):
                    launcher.run_secure_launcher("fixture", max_wait_s=value)
            read.assert_not_called()

    def test_cli_parse_errors_are_generic_exit_2_and_do_not_echo_arguments(self) -> None:
        sensitive = "SENSITIVE-LAUNCHER-ARGUMENT"
        for argv in (
            ["fixture", "--registry", sensitive],
            ["fixture", "--", sensitive],
            ["fixture", "--max-wait-s", sensitive],
        ):
            stderr = io.StringIO()
            with self.subTest(argv=argv), redirect_stderr(stderr), self.assertRaises(
                SystemExit
            ) as raised:
                launcher._parser().parse_args(argv)
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(stderr.getvalue(), "secure launcher: invalid arguments\n")
            self.assertNotIn(sensitive, stderr.getvalue())

    def test_main_routes_only_launcher_id_and_reports_runtime_error_generically(self) -> None:
        sensitive = "sensitive-launcher-id"
        stderr = io.StringIO()
        with patch.object(launcher.sys, "stderr", stderr), patch.object(
            launcher,
            "run_secure_launcher",
            side_effect=ValueError("sensitive runtime detail"),
        ) as run:
            self.assertEqual(launcher.main([sensitive]), 1)

        run.assert_called_once_with(sensitive, max_wait_s=launcher.DEFAULT_MAX_WAIT_S)
        self.assertEqual(stderr.getvalue(), "secure launcher failed: ValueError\n")
        self.assertNotIn(sensitive, stderr.getvalue())
        self.assertNotIn("runtime detail", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
