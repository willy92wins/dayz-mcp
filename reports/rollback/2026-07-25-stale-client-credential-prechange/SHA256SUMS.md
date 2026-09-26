# Pre-change rollback manifest

Created before implementation of stale-client-credential recovery. Every listed
copy was verified byte-for-byte by SHA-256 before its corresponding file changed,
except `test_loopback.py`: that baseline was reconstructed by removing the one
new test block, then verified by an exact one-hunk diff against the active file.

| Path | Bytes | SHA-256 |
|---|---:|---|
| `product-spec.md` | 34688 | `2fe6b5c1fc0c2054ff3a80972ea345d2d2dcff1b5bffa9d0467cd052ab6f216b` |
| `plans/2026-07-25-stale-client-credential-recovery.md` | 20186 | `f29bdea24f9c864946da30c8de81661b063157768ce4bce863461b2b7def42bc` |
| `tools/dayz_mcp/server.py` | 59528 | `4af0a2d14358ccacb875db33932e4f30459eee5d004217adfd8f7832867038f7` |
| `tools/dayz_mcp/control_client.py` | 29058 | `50241347671fba89f6b847c131359a243394298ff276117c029858a2ee3605f3` |
| `tools/dayz_mcp/orphan_guard.py` | 38234 | `23065285ea34caf1569dfed0674b41b67b38bbec40f4a448dc1f29a38cb5f425` |
| `tools/dayz_mcp/loopback.py` | 70156 | `e7187ac3803ebb5a245ae9fdaf05847cd1fa8f28892457bbbbb9bf7ee6e37a9d` |
| `tools/dayz_mcp/doctor.py` | 32994 | `2229b2f1c7583376d1362cf57f4a9716c7174259d3687865c7b4933b5919cc00` |
| `tools/tests/test_control_client.py` | 65166 | `d7f6b9e8eeae1571f9343c305fb2f43c825a2c50b9b328581ffe0fcda92ca234` |
| `tools/tests/test_client_mode.py` | 57145 | `1678d9ca6c6c44241924c42ead6375a2ae8d827759e3a9e90a52c475c4e42338` |
| `tools/tests/test_client_runtime_control_composition.py` | 18450 | `c729072c70b67bd5503a81018b6d998af48a2c6b8d2012b050476d2b10e6f601` |
| `tools/tests/test_daemon.py` | 43918 | `61618577a79654a724d802416b6272fe980c6d9a964e584883902b56ff66ea41` |
| `tools/tests/test_doctor.py` | 44178 | `468c77273c614ff83a7cb81ab1e1b799dda41a5e56dfbb8f4248e291bf1c1d40` |
| `tools/tests/test_loopback.py` | 34804 | `a037e1a7194dce0869383dd4da41312c59273b5a9c7e241102721ac81333a927` |
| `tools/tests/test_accredited_daemon_transport.py` | 11832 | `50f88a45833f1f7381e741a85a25db66702e9ff772e22842248ef50bb0b4920e` |
| `tools/tests/test_pinned_keyfile.py` | 2281 | `e6bf0ed795dfae2374616e96e1f0f80c2b86b6bcc269a9a8a0b48d3da42d9d5e` |
| `tools/tests/test_security_runtime_audit.py` | 73372 | `d3df2e3860287b35a42a2e7bc4214906f54a094e9296bab5618805f77e35df78` |
| `HANDOFF.md` | 17993 | `e07ad5f1907f629dec809453243d3b02bfc11d44c2b5b1859457c9052d161935` |
| `decisions/decision-log.md` | 13575 | `e605b704759b023a21a4bf74a26f10d2fa49f7f7893e4f9e1fdc3c3710b15807` |

New files have no predecessor and must be removed explicitly on rollback:

- `tools/dayz_mcp/daemon_credential.py`
- `tools/tests/test_daemon_credential.py`
- `tools/tests/test_client_credential_rotation_e2e.py`
- `reviews/2026-07-25-stale-client-credential-implementation.md`
