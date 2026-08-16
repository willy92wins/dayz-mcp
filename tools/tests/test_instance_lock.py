from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make tools/ importable whether run via discover or by module name.
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import loopback


class InstanceLockTest(unittest.TestCase):
    def test_second_bind_on_same_port_fails(self) -> None:
        state_a = loopback.ServerState("key-a")
        httpd_a = loopback.create_http_server(0, state_a)
        try:
            port = httpd_a.server_address[1]
            state_b = loopback.ServerState("key-b")
            with self.assertRaises(OSError):
                httpd_b = loopback.create_http_server(port, state_b)
                httpd_b.server_close()
        finally:
            httpd_a.server_close()


if __name__ == "__main__":
    unittest.main()
