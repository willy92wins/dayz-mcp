from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


class DaemonContractTests(unittest.TestCase):
    def test_pure_contract_owns_byte_exact_daemon_argv_and_cwd(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.daemon_contract")
        self.assertIsNotNone(spec, "dayz_mcp.daemon_contract is not implemented")
        contract = importlib.import_module("dayz_mcp.daemon_contract")
        daemon = importlib.import_module("dayz_mcp.daemon")

        configs = (
            SimpleNamespace(),
            SimpleNamespace(
                port=2302,
                keyfile=r"P:\Keys\daemon.key",
                expected_game_version="1.29.159000",
                require_version=True,
                idle_timeout_s=900.0,
                enable_exec_enforce=True,
                exec_allowlist=r"P:\Policy\exec.json",
            ),
        )
        for config in configs:
            with self.subTest(config=vars(config)):
                expected = contract.build_daemon_argv(
                    config, python=r"P:\Runtime\python.exe"
                )
                self.assertEqual(
                    expected,
                    [
                        r"P:\Runtime\python.exe",
                        "-m",
                        "dayz_mcp",
                        "--daemon",
                        "--port",
                        str(int(getattr(config, "port", 8765))),
                    ]
                    + (
                        ["--keyfile", config.keyfile]
                        if getattr(config, "keyfile", None)
                        else []
                    )
                    + (
                        ["--expected-game-version", config.expected_game_version]
                        if getattr(config, "expected_game_version", None)
                        else []
                    )
                    + (["--require-version"] if getattr(config, "require_version", False) else [])
                    + [
                        "--idle-timeout",
                        str(float(getattr(config, "idle_timeout_s", 1800.0))),
                    ]
                    + (
                        ["--enable-exec-enforce"]
                        + (
                            ["--exec-allowlist", config.exec_allowlist]
                            if getattr(config, "exec_allowlist", None)
                            else []
                        )
                        if getattr(config, "enable_exec_enforce", False)
                        else []
                    ),
                )

        self.assertIs(daemon.build_daemon_argv, contract.build_daemon_argv)
        self.assertIs(daemon.daemon_runtime_cwd, contract.daemon_runtime_cwd)
        self.assertEqual(
            contract.build_daemon_argv(SimpleNamespace()),
            [
                sys.executable,
                "-m",
                "dayz_mcp",
                "--daemon",
                "--port",
                "8765",
                "--idle-timeout",
                "1800.0",
            ],
        )
        self.assertEqual(
            Path(contract.daemon_runtime_cwd()), Path(__file__).resolve().parents[1]
        )

    def test_contract_import_graph_has_no_process_or_network_surface(self) -> None:
        spec = importlib.util.find_spec("dayz_mcp.daemon_contract")
        self.assertIsNotNone(spec, "dayz_mcp.daemon_contract is not implemented")
        source_path = Path(spec.origin or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"ctypes", "multiprocessing", "socket", "subprocess", "urllib"}
            ),
            imported_roots,
        )

    def test_host_config_consumes_only_the_pure_contract(self) -> None:
        host_config_path = Path(__file__).resolve().parents[1] / "dayz_mcp" / "host_config.py"
        tree = ast.parse(
            host_config_path.read_text(encoding="utf-8"), filename=str(host_config_path)
        )
        resolver = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "resolve_daemon_provenance"
        )
        imported_names = {
            alias.name
            for node in ast.walk(resolver)
            if isinstance(node, ast.ImportFrom) and node.module == "dayz_mcp"
            for alias in node.names
        }
        used_contract_calls = {
            node.func.attr
            for node in ast.walk(resolver)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "daemon_contract"
        }
        self.assertIn("daemon_contract", imported_names)
        self.assertNotIn("daemon", imported_names)
        self.assertEqual(
            used_contract_calls, {"build_daemon_argv", "daemon_runtime_cwd"}
        )

    def test_orphan_guard_reexports_pure_daemon_argv_classification(self) -> None:
        contract = importlib.import_module("dayz_mcp.daemon_contract")
        orphan_guard = importlib.import_module("dayz_mcp.orphan_guard")
        self.assertIs(orphan_guard.argv_targets_port, contract.argv_targets_port)
        self.assertIs(orphan_guard.classify_dayz_argv, contract.classify_dayz_argv)

        normal = [
            r"P:\Runtime\python.exe",
            "-m",
            "dayz_mcp",
            "--daemon",
            "--port",
            "8765",
        ]
        bootstrap = [
            r"P:\Runtime\python.exe",
            "-I",
            "-B",
            r"P:\DayZ_MCP_dev\tools\p0s_daemon_bootstrap.py",
            "daemon",
            "--port",
            "8765",
        ]
        self.assertEqual(contract.classify_dayz_argv(normal), "normal_daemon")
        self.assertEqual(
            contract.classify_dayz_argv(bootstrap), "p0s_bootstrap_daemon"
        )
        self.assertTrue(contract.argv_targets_port(normal, 8765))
        self.assertFalse(contract.argv_targets_port(normal, 8766))


if __name__ == "__main__":
    unittest.main()
