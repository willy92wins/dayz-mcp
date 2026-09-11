"""Parity of LifecycleReconcileTest.request() against dayz_test_worker._start_core.

The reconcile fixture is a start_run stand-in: it does not copy every worker
core key. These tests compare the full KEY SET of both sides after subtracting
an explicit allowlist of worker-only keys. replace_if_not_polling_since is
copied by _start_core only when run_id is not None, role is client, and the
payload witness is an int (dayz_test_worker.py:325-332). A fixture that stamps
the witness by default goes red here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp import dayz_test_worker
from tests import test_lifecycle_reconcile as _reconcile

_WITNESS = "replace_if_not_polling_since"
_WITNESS_MS = 1_700_000_000_000
# Worker-only keys the lifecycle fixture never carries. Subtracted from both
# sides before set equality so an unlisted extra key on either side still
# fails.
_CORE_ONLY_KEYS = frozenset(
    {
        # _start_core copies this from the payload for client/offline; the
        # reconcile fixture is a start_run stand-in and never includes it.
        "auto_remediate_steam",
        # _start_core adds this on a create-run server/offline launch; the
        # fixture never models a storage seal.
        "storage_seal",
    }
)

_RUNTIME = dayz_test_worker.WorkerRuntimePolicy(
    dev_root=r"P:\ExampleMod_Suite",
    mod="ExampleMod",
    diag_executable=(
        r"C:\Program Files (x86)\Steam\steamapps\common\DayZ\DayZDiag_x64.exe"
    ),
    game_directory=r"C:\Program Files (x86)\Steam\steamapps\common\DayZ",
    mission_aliases=(
        (
            "chernarus",
            r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\DayZServer\mpmissions\dayzOffline.chernarusplus",
        ),
        (
            "livonia",
            r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\DayZServer\mpmissions\dayzOffline.enoch",
        ),
        (
            "sakhal",
            r"C:\Program Files (x86)\Steam\steamapps\common"
            r"\DayZServer\mpmissions\dayzOffline.sakhal",
        ),
    ),
    mods_root=r"P:\Mods",
    build_temp_root=r"P:\temp",
    build_source_basename=None,
)


def _payload(*, witness: object = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "auto_remediate_steam": False,
        "base_mods": [],
        "extra_mods": [],
        "height": 1080,
        "mission": "chernarus",
        "no_file_patching": False,
        "player_name": "Dev",
        "port": 2302,
        "server_mods": [],
        "width": 1920,
    }
    if witness is not None:
        payload[_WITNESS] = witness
    return payload


def _core(
    role: str, run_id: str | None, *, witness: object = None
) -> dict[str, object]:
    return dayz_test_worker._start_core(
        _payload(witness=witness), _RUNTIME, role=role, run_id=run_id
    )


class LifecycleRequestFixtureParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _reconcile.LifecycleReconcileTest("setUp")
        self.fixture.setUp()

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def _assert_witness_key_sets(
        self,
        fixture: dict[str, object],
        core: dict[str, object],
        *,
        present: bool,
    ) -> None:
        fixture_keys = set(fixture) - _CORE_ONLY_KEYS
        core_keys = set(core) - _CORE_ONLY_KEYS
        self.assertEqual(_WITNESS in fixture, present, sorted(fixture_keys))
        self.assertEqual(_WITNESS in core, present, sorted(core_keys))
        self.assertEqual(fixture_keys, core_keys)

    def test_fb_93a5_client_with_run_id_and_int_witness_includes_the_key(
        self,
    ) -> None:
        fixture = self.fixture.request(with_witness=True)
        core = _core("client", _reconcile.RUN_ID, witness=_WITNESS_MS)
        self._assert_witness_key_sets(fixture, core, present=True)
        self.assertIs(type(fixture[_WITNESS]), int)
        self.assertEqual(core[_WITNESS], _WITNESS_MS)

    def test_fb_93a5_client_with_run_id_and_no_witness_omits_the_key(self) -> None:
        fixture = self.fixture.request()
        core = _core("client", _reconcile.RUN_ID)
        self._assert_witness_key_sets(fixture, core, present=False)

    def test_fb_93a5_client_without_run_id_omits_the_witness(self) -> None:
        fixture = self.fixture.request(run_id=None)
        core = _core("client", None, witness=_WITNESS_MS)
        self._assert_witness_key_sets(fixture, core, present=False)

    def test_fb_93a5_server_omits_the_witness(self) -> None:
        fixture = self.fixture.request("server")
        core = _core("server", _reconcile.RUN_ID, witness=_WITNESS_MS)
        self._assert_witness_key_sets(fixture, core, present=False)

    def test_fb_93a5_rejects_an_unmodeled_fixture_key(self) -> None:
        fixture = self.fixture.request()
        fixture["unexpected"] = 1
        with self.assertRaises(AssertionError):
            self._assert_witness_key_sets(
                fixture,
                _core("client", _reconcile.RUN_ID),
                present=False,
            )
