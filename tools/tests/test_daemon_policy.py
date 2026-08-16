from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import msvcrt
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


def _authority_sha256(fields: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            fields,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class DaemonPolicyTests(unittest.TestCase):
    def _bootstrap_policy(self, policy_module: object, build_id: str):
        authority = {
            "argv": [
                r"P:\Runtime\python.exe",
                "-I",
                "-B",
                r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
                "daemon",
            ],
            "cwd": r"P:\DayZ_MCP_dev\tools",
            "host": "127.0.0.1",
            "keyfile": r"P:\Keys\daemon.key",
            "kind": "bootstrap",
            "native_executable": r"P:\Runtime\python.exe",
            "port": 8765,
            "security_build_id": build_id,
        }
        return policy_module.AccreditedDaemonPolicy(
            kind="bootstrap",
            host="127.0.0.1",
            port=8765,
            keyfile=r"P:\Keys\daemon.key",
            native_executable=r"P:\Runtime\python.exe",
            argv=tuple(authority["argv"]),
            cwd=r"P:\DayZ_MCP_dev\tools",
            security_build_id=build_id,
            authority_sha256=_authority_sha256(authority),
        )

    def test_normal_factory_seals_resolved_host_consensus(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.daemon_policy")
        self.assertIsNotNone(spec, "dayz_mcp.daemon_policy is not implemented")
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        provenance = SimpleNamespace(
            native_executable=r"P:\Runtime\python.exe",
            argv=(
                r"P:\Runtime\python.exe",
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "8765",
            ),
            cwd=r"P:\DayZ_MCP_dev\tools",
            port=8765,
            keyfile=r"P:\Keys\daemon.key",
        )
        authority = {
            "argv": list(provenance.argv),
            "cwd": provenance.cwd,
            "host": "127.0.0.1",
            "keyfile": provenance.keyfile,
            "kind": "normal",
            "native_executable": provenance.native_executable,
            "port": provenance.port,
            "security_build_id": None,
        }

        with patch(
            "dayz_mcp.host_config.resolve_daemon_provenance",
            return_value=provenance,
        ) as resolve:
            policy = policy_module.load_normal_daemon_policy()

        resolve.assert_called_once_with()
        self.assertEqual(policy.kind, "normal")
        self.assertEqual(policy.host, "127.0.0.1")
        self.assertEqual(policy.port, 8765)
        self.assertEqual(policy.keyfile, provenance.keyfile)
        self.assertEqual(policy.native_executable, provenance.native_executable)
        self.assertEqual(policy.argv, provenance.argv)
        self.assertEqual(policy.cwd, provenance.cwd)
        self.assertIsNone(policy.security_build_id)
        self.assertEqual(policy.authority_sha256, _authority_sha256(authority))

    def test_normal_policy_manifest_roundtrips_without_child_host_resolution(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.normal_daemon_policy")
        provenance = SimpleNamespace(
            native_executable=r"P:\Runtime\python.exe",
            argv=(r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon"),
            cwd=r"P:\DayZ_MCP_dev\tools",
            port=8765,
            keyfile=r"P:\Keys\daemon.key",
        )
        with patch(
            "dayz_mcp.host_config.resolve_daemon_provenance",
            return_value=provenance,
        ) as resolve:
            parent_policy = policy_module.load_normal_daemon_policy()
            manifest = policy_module.serialize_normal_daemon_policy(parent_policy)
        self.assertEqual(resolve.call_count, 2)

        environment = {"DAYZ_MCP_NORMAL_POLICY_JSON": manifest}
        with patch(
            "dayz_mcp.host_config.resolve_daemon_provenance"
        ) as forbidden_resolve:
            child_policy = policy_module.load_inherited_normal_daemon_policy(
                environment
            )
        forbidden_resolve.assert_not_called()
        self.assertEqual(child_policy, parent_policy)
        self.assertEqual(environment, {})

    def test_normal_policy_manifest_is_canonical_closed_and_consumed_on_failure(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.normal_daemon_policy")
        for raw in ("{}", '{"format_version":1,"format_version":1}'):
            with self.subTest(raw=raw):
                environment = {"DAYZ_MCP_NORMAL_POLICY_JSON": raw}
                with self.assertRaisesRegex(
                    ValueError, "invalid_normal_policy_manifest"
                ):
                    policy_module.load_inherited_normal_daemon_policy(environment)
                self.assertEqual(environment, {})

    def test_policy_rejects_malformed_or_cross_kind_authority(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        base = {
            "kind": "normal",
            "host": "127.0.0.1",
            "port": 8765,
            "keyfile": r"P:\Keys\daemon.key",
            "native_executable": r"P:\Runtime\python.exe",
            "argv": (r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon"),
            "cwd": r"P:\DayZ_MCP_dev\tools",
            "security_build_id": None,
        }
        invalid_overrides = {
            "unknown_kind": {"kind": "foreign"},
            "non_loopback": {"host": "0.0.0.0"},
            "bool_port": {"port": True},
            "relative_keyfile": {"keyfile": "daemon.key"},
            "relative_executable": {"native_executable": "python.exe"},
            "relative_cwd": {"cwd": "tools"},
            "unc_keyfile": {"keyfile": r"\\server\share\daemon.key"},
            "device_executable": {
                "native_executable": r"\\?\P:\Runtime\python.exe"
            },
            "ads_keyfile": {"keyfile": r"P:\Keys\daemon.key:stream"},
            "argv_list": {"argv": [r"P:\Runtime\python.exe"]},
            "empty_argv": {"argv": ()},
            "normal_with_build_id": {"security_build_id": "a" * 64},
        }

        for label, overrides in invalid_overrides.items():
            fields = dict(base)
            fields.update(overrides)
            authority_fields = dict(fields)
            authority_fields["argv"] = list(fields["argv"])
            fields["authority_sha256"] = _authority_sha256(authority_fields)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "invalid_daemon_policy"):
                    policy_module.AccreditedDaemonPolicy(**fields)

        valid_fields = dict(base)
        authority_fields = dict(valid_fields)
        authority_fields["argv"] = list(valid_fields["argv"])
        valid_fields["authority_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "invalid_daemon_policy"):
            policy_module.AccreditedDaemonPolicy(**valid_fields)

    def test_policy_kind_must_match_the_exact_daemon_argv_grammar(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        normal_argv = (r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon")
        bootstrap_argv = (
            r"P:\Runtime\python.exe",
            "-I",
            "-B",
            r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
            "daemon",
        )
        for kind, argv, build_id in (
            ("bootstrap", normal_argv, "a" * 64),
            ("normal", bootstrap_argv, None),
        ):
            authority = {
                "argv": list(argv),
                "cwd": r"P:\DayZ_MCP_dev\tools",
                "host": "127.0.0.1",
                "keyfile": r"P:\Keys\daemon.key",
                "kind": kind,
                "native_executable": r"P:\Runtime\python.exe",
                "port": 8765,
                "security_build_id": build_id,
            }
            with self.subTest(kind=kind), self.assertRaisesRegex(
                ValueError, "invalid_daemon_policy"
            ):
                policy_module.AccreditedDaemonPolicy(
                    kind=kind,
                    host="127.0.0.1",
                    port=8765,
                    keyfile=r"P:\Keys\daemon.key",
                    native_executable=r"P:\Runtime\python.exe",
                    argv=argv,
                    cwd=r"P:\DayZ_MCP_dev\tools",
                    security_build_id=build_id,
                    authority_sha256=_authority_sha256(authority),
                )

    def test_normal_policy_revalidation_detects_consensus_drift(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        original = SimpleNamespace(
            native_executable=r"P:\Runtime\python.exe",
            argv=(r"P:\Runtime\python.exe", "-m", "dayz_mcp", "--daemon"),
            cwd=r"P:\DayZ_MCP_dev\tools",
            port=8765,
            keyfile=r"P:\Keys\daemon.key",
        )
        drifted = SimpleNamespace(
            native_executable=original.native_executable,
            argv=original.argv,
            cwd=original.cwd,
            port=8766,
            keyfile=original.keyfile,
        )
        current = [original]

        with patch(
            "dayz_mcp.host_config.resolve_daemon_provenance",
            side_effect=lambda: current[0],
        ):
            policy = policy_module.load_normal_daemon_policy()
            policy.revalidate()
            current[0] = drifted
            with self.assertRaisesRegex(ValueError, "daemon_policy_drift"):
                policy.revalidate()

    def test_bootstrap_policy_requires_inherited_handle_and_revalidates_bytes(
        self,
    ) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        build_id = "a" * 64
        authority = {
            "argv": [
                r"P:\Runtime\python.exe",
                "-I",
                "-B",
                r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
                "daemon",
            ],
            "cwd": r"P:\DayZ_MCP_dev\tools",
            "host": "127.0.0.1",
            "keyfile": r"P:\Keys\daemon.key",
            "kind": "bootstrap",
            "native_executable": r"P:\Runtime\python.exe",
            "port": 8765,
            "security_build_id": build_id,
        }
        manifest = {
            "argv": authority["argv"],
            "authority_sha256": _authority_sha256(authority),
            "cwd": authority["cwd"],
            "format_version": 1,
            "host": authority["host"],
            "keyfile": authority["keyfile"],
            "kind": authority["kind"],
            "native_executable": authority["native_executable"],
            "port": authority["port"],
            "security_build_id": build_id,
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

        with tempfile.TemporaryFile(mode="w+b") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            native_handle = msvcrt.get_osfhandle(handle.fileno())

            os.set_handle_inheritable(native_handle, False)
            with self.assertRaisesRegex(ValueError, "invalid_bootstrap_policy_handle"):
                policy_module.load_bootstrap_daemon_policy(
                    native_handle, expected_security_build_id=build_id
                )

            os.set_handle_inheritable(native_handle, True)
            with self.assertRaisesRegex(ValueError, "invalid_bootstrap_manifest"):
                policy_module.load_bootstrap_daemon_policy(
                    native_handle, expected_security_build_id="b" * 64
                )

            policy = policy_module.load_bootstrap_daemon_policy(
                native_handle, expected_security_build_id=build_id
            )
            self.assertEqual(policy.kind, "bootstrap")
            self.assertEqual(policy.security_build_id, build_id)
            self.assertEqual(policy.authority_sha256, manifest["authority_sha256"])
            policy.revalidate()

            drifted = dict(manifest)
            drifted["port"] = 8766
            handle.seek(0)
            handle.truncate()
            handle.write(
                json.dumps(
                    drifted,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            handle.flush()
            with self.assertRaisesRegex(ValueError, "daemon_policy_drift"):
                policy.revalidate()

    def test_named_policy_router_is_closed_and_pins_bootstrap_liveness(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        build_id = "a" * 64
        bootstrap = self._bootstrap_policy(policy_module, build_id)
        read_fd, write_fd = os.pipe()
        try:
            liveness_handle = msvcrt.get_osfhandle(read_fd)
            os.set_handle_inheritable(liveness_handle, True)
            environment = {
                "DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE": "123",
                "DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE": str(liveness_handle),
                "DAYZ_MCP_SECURITY_BUILD_ID": build_id,
            }
            with patch.object(
                policy_module.bootstrap_parent,
                "accredit_registered_bootstrap_parent",
            ) as accredit_parent, patch.object(
                policy_module,
                "load_bootstrap_daemon_policy",
                return_value=bootstrap,
            ) as load_bootstrap:
                loaded = policy_module.load_daemon_policy(
                    "bootstrap", environ=environment
                )

            self.assertIs(loaded, bootstrap)
            accredit_parent.assert_called_once_with()
            load_bootstrap.assert_called_once_with(
                123, expected_security_build_id=build_id
            )
            self.assertEqual(environment, {})
            os.close(read_fd)
            read_fd = -1
            loaded.revalidate()
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            os.close(write_fd)

    def test_named_policy_router_rejects_cross_kind_or_foreign_handles(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        bootstrap_names = (
            "DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE",
            "DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE",
            "DAYZ_MCP_SECURITY_BUILD_ID",
        )
        for name in bootstrap_names:
            environment = {name: "foreign"}
            with self.subTest(normal_extra=name), patch.object(
                policy_module, "load_normal_daemon_policy"
            ) as load_normal:
                with self.assertRaisesRegex(ValueError, "invalid_daemon_policy_environment"):
                    policy_module.load_daemon_policy("normal", environ=environment)
                load_normal.assert_not_called()
                self.assertEqual(environment, {})

        with patch.object(policy_module, "load_normal_daemon_policy") as load_normal:
            with self.assertRaisesRegex(ValueError, "invalid_daemon_policy_kind"):
                policy_module.load_daemon_policy("foreign", environ={})
            load_normal.assert_not_called()

        build_id = "a" * 64
        with tempfile.TemporaryFile(mode="w+b") as foreign_liveness:
            handle = msvcrt.get_osfhandle(foreign_liveness.fileno())
            os.set_handle_inheritable(handle, True)
            environment = {
                "DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE": "123",
                "DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE": str(handle),
                "DAYZ_MCP_SECURITY_BUILD_ID": build_id,
            }
            with patch.object(
                policy_module.bootstrap_parent,
                "accredit_registered_bootstrap_parent",
            ), patch.object(
                policy_module, "load_bootstrap_daemon_policy"
            ) as load_bootstrap:
                with self.assertRaisesRegex(
                    ValueError, "invalid_bootstrap_liveness_handle"
                ):
                    policy_module.load_daemon_policy(
                        "bootstrap", environ=environment
                    )
                load_bootstrap.assert_not_called()
                self.assertEqual(environment, {})

    def test_bootstrap_router_requires_the_registered_native_parent_first(self) -> None:
        policy_module = importlib.import_module("dayz_mcp.daemon_policy")
        build_id = "a" * 64
        bootstrap = self._bootstrap_policy(policy_module, build_id)
        read_fd, write_fd = os.pipe()
        try:
            liveness_handle = msvcrt.get_osfhandle(read_fd)
            os.set_handle_inheritable(liveness_handle, True)
            environment = {
                "DAYZ_MCP_BOOTSTRAP_POLICY_HANDLE": "123",
                "DAYZ_MCP_BOOTSTRAP_LIVENESS_HANDLE": str(liveness_handle),
                "DAYZ_MCP_SECURITY_BUILD_ID": build_id,
            }
            with patch.object(
                policy_module,
                "load_bootstrap_daemon_policy",
                return_value=bootstrap,
            ) as load_bootstrap:
                with self.assertRaisesRegex(
                    ValueError, "unaccredited_bootstrap_parent"
                ):
                    policy_module.load_daemon_policy(
                        "bootstrap", environ=environment
                    )
            load_bootstrap.assert_not_called()
            self.assertEqual(environment, {})
        finally:
            os.close(read_fd)
            os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
