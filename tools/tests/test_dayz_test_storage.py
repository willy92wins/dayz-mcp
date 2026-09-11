"""Oracle for M15-min: rotate storage_1 only when the mod set changed.

Ficha fb-20260829-115147-4407 + annex fb-20260904-144835-01ae. The design these
assertions come from is reviews/2026-09-04-reserva/R1-storage-4407-diseno.md §5
(S1-S12). Two rules govern this file:

* the tree oracle is an INDEPENDENT digest (enumerate + sha256 per file, empty
  directories included), captured before the fault is injected. A file count is
  not a control: it matches for two different trees.
* every crash test asserts the durable invariant, not the happy path: after a
  kill at any single I/O boundary, every byte of the original storage_1 is still
  readable somewhere under the mission, and the next call either recovers or
  refuses to launch. It never launches over an ambiguous tree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dayz_mcp import dayz_test_storage as storage
from dayz_mcp import dayz_test_worker


MODS_ROOT = r"P:\Mods"
SEAL_A = "a" * 64
TXID = "0" * 32


def _tree_digest(root: Path) -> str:
    """Independent oracle: every path, every byte, directories included."""
    entries: list[str] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames.sort()
        relative = Path(current).relative_to(root).as_posix()
        entries.append(f"D {relative}")
        for name in sorted(filenames):
            payload = (Path(current) / name).read_bytes()
            entries.append(
                f"F {relative}/{name} {hashlib.sha256(payload).hexdigest()}"
            )
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _make_storage(mission: Path, *, marker_payload: object = None) -> Path:
    tree = mission / storage.STORAGE_NAME
    (tree / "players").mkdir(parents=True)
    (tree / "empty").mkdir()
    (tree / "data.bin").write_bytes(b"world-and-characters")
    (tree / "players" / "p1.bin").write_bytes(b"survivor")
    if marker_payload is not None:
        (mission / storage.MARKER_NAME).write_text(
            json.dumps(marker_payload), encoding="utf-8"
        )
    return tree


def _valid_marker(seal: str, project: str = "DayZ_MCP") -> dict[str, object]:
    return {
        "schema_version": storage.MARKER_SCHEMA_VERSION,
        "algorithm": storage.MARKER_ALGORITHM,
        "seal": seal,
        "project": project,
    }


def _roles(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_mods": ["@CF"],
        "project_mod": "@DayZ_MCP",
        "extra_mods": ["@LFQuad2"],
        "server_mods": [],
        "mods_root": MODS_ROOT,
    }
    base.update(overrides)
    return storage.modset_roles(**base)  # type: ignore[arg-type]


class NormalizationTest(unittest.TestCase):
    def test_s1_alias_and_absolute_name_the_same_folder(self) -> None:
        self.assertEqual(
            storage.normalize_modset(["@CF"], mods_root=MODS_ROOT),
            storage.normalize_modset([r"P:\Mods\@CF"], mods_root=MODS_ROOT),
        )

    def test_s2_case_does_not_change_the_seal(self) -> None:
        self.assertEqual(
            storage.modset_seal(_roles(base_mods=["@CF"])),
            storage.modset_seal(_roles(base_mods=[r"p:\mods\@cf"])),
        )

    def test_s3_load_order_is_contract_so_it_changes_the_seal(self) -> None:
        self.assertNotEqual(
            storage.modset_seal(_roles(base_mods=["@A", "@B"])),
            storage.modset_seal(_roles(base_mods=["@B", "@A"])),
        )

    def test_s4_moving_a_mod_between_roles_changes_the_seal(self) -> None:
        self.assertNotEqual(
            storage.modset_seal(_roles(extra_mods=["@X"], server_mods=[])),
            storage.modset_seal(_roles(extra_mods=[], server_mods=["@X"])),
        )

    def test_s5_the_seal_is_a_stable_sha256_of_canonical_json(self) -> None:
        roles = _roles()
        first = storage.modset_seal(roles)
        self.assertEqual(first, storage.modset_seal(dict(reversed(list(roles.items())))))
        expected = hashlib.sha256(
            json.dumps(
                {
                    "algorithm": "sha256",
                    "roles": {
                        "base_mods": [r"p:\mods\@cf"],
                        "extra_mods": [r"p:\mods\@lfquad2"],
                        "project_mod": r"p:\mods\@dayz_mcp",
                        "server_mods": [],
                    },
                    "schema_version": 1,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(first, expected)

    def test_the_separator_never_enters_the_seal(self) -> None:
        # A mod whose name carries the argv separator cannot forge a collision.
        self.assertNotEqual(
            storage.modset_seal(_roles(base_mods=["@A;@B"])),
            storage.modset_seal(_roles(base_mods=["@A", "@B"])),
        )

    def test_an_empty_or_non_string_entry_fails_closed(self) -> None:
        for value in ("", 3, None):
            with self.assertRaises(storage.StorageError):
                storage.normalize_modset([value], mods_root=MODS_ROOT)

    def test_the_worker_delegates_the_mod_path_rule_to_this_module(self) -> None:
        """No mirror: the argv and the seal read the same implementation.

        Positive control for the delegation: the source of _mod_path names
        dayz_test_storage. A table that merely agrees would keep passing over
        two copies of the rule, which is the defect ficha 9d46 documents.
        """
        import inspect

        source = inspect.getsource(dayz_test_worker._mod_path)
        self.assertIn("dayz_test_storage.normalize_mod_path", source)


class ShouldRotateTest(unittest.TestCase):
    def _marker(self, state: str, seal: str | None = None) -> storage.MarkerRead:
        return storage.MarkerRead(state, seal, "DayZ_MCP", state != storage.MARKER_ABSENT)

    def test_s6_the_six_rows_of_the_matrix(self) -> None:
        rows = (
            (False, self._marker(storage.MARKER_ABSENT), storage.DECISION_SEAL_ONLY),
            (False, self._marker(storage.MARKER_PRESENT_VALID, SEAL_A), storage.DECISION_SEAL_ONLY),
            (False, self._marker(storage.MARKER_PRESENT_INVALID), storage.DECISION_SEAL_ONLY),
            (True, self._marker(storage.MARKER_ABSENT), storage.DECISION_ROTATE),
            (True, self._marker(storage.MARKER_PRESENT_INVALID), storage.DECISION_ROTATE),
            (True, self._marker(storage.MARKER_PRESENT_VALID, SEAL_A), storage.DECISION_REUSE),
            (True, self._marker(storage.MARKER_PRESENT_VALID, "b" * 64), storage.DECISION_ROTATE),
        )
        for present, marker, expected in rows:
            with self.subTest(present=present, marker=marker.state):
                self.assertEqual(
                    storage.should_rotate(
                        seal=SEAL_A, marker=marker, storage_present=present
                    ).action,
                    expected,
                )

    def test_a_malformed_seal_is_a_programming_error_not_a_decision(self) -> None:
        with self.assertRaises(storage.StorageError):
            storage.should_rotate(
                seal="short",
                marker=self._marker(storage.MARKER_ABSENT),
                storage_present=True,
            )


class PrepareStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.mission = Path(self._temporary.name) / "dayzOffline.chernarusplus"
        self.mission.mkdir()

    def _prepare(self, seal: str = SEAL_A, txid: str = TXID) -> storage.RotationResult:
        return storage.prepare_storage(
            str(self.mission),
            seal=seal,
            project="DayZ_MCP",
            now=1_756_000_000.0,
            txid=txid,
        )

    def _siblings(self) -> list[str]:
        return sorted(
            entry.name
            for entry in self.mission.iterdir()
            if entry.name.startswith(storage.STORAGE_NAME)
        )

    def test_s7_seal_only_over_an_absent_tree_announces_no_reset(self) -> None:
        result = self._prepare()
        self.assertTrue(result.launch_allowed)
        self.assertFalse(result.storage_rotated)
        self.assertIsNone(result.storage_reset_notice)
        self.assertEqual(
            storage.read_marker(str(self.mission)).seal, SEAL_A
        )

    def test_the_same_mod_set_twice_does_not_rotate(self) -> None:
        _make_storage(self.mission, marker_payload=_valid_marker(SEAL_A))
        before = self._siblings()
        result = self._prepare()
        self.assertTrue(result.launch_allowed)
        self.assertFalse(result.storage_rotated)
        self.assertEqual(result.decision, storage.DECISION_REUSE)
        self.assertEqual(self._siblings(), before)

    def test_a_legacy_tree_without_a_marker_rotates_fail_closed(self) -> None:
        _make_storage(self.mission)
        result = self._prepare()
        self.assertTrue(result.storage_rotated)
        self.assertEqual(result.reason, "seal_changed")
        self.assertIsNotNone(result.storage_backup)
        self.assertIn(storage.LEGACY_SEAL8, str(result.storage_backup))
        self.assertEqual(result.storage_reset_notice, storage.RESET_NOTICE)

    def test_a_rotation_result_carries_the_fields_the_lifecycle_auditor_reads(self) -> None:
        """The auditor copies these off the producer. A richer fixture would hide a hole."""
        _make_storage(self.mission)
        result = self._prepare()
        self.assertTrue(result.storage_rotated)
        # JsonlAuditWriter rejects an empty reason; the row is built from this.
        self.assertIsInstance(result.reason, str)
        self.assertTrue(result.reason.strip())
        self.assertIsInstance(result.decision, str)
        self.assertTrue(result.decision.strip())
        self.assertIsInstance(result.storage_backup, str)
        self.assertTrue(result.storage_backup)
        self.assertIsInstance(result.storage_seal, str)
        self.assertEqual(result.storage_reset_notice, storage.RESET_NOTICE)
        self.assertEqual(
            {item.name for item in dataclasses.fields(result)},
            {
                "launch_allowed",
                "storage_rotated",
                "storage_backup",
                "storage_marker_backup",
                "storage_seal",
                "storage_recovery_required",
                "storage_reset_notice",
                "decision",
                "reason",
            },
        )

    def test_an_unreadable_marker_rotates_and_is_kept_as_evidence(self) -> None:
        _make_storage(self.mission, marker_payload={"schema_version": 9})
        result = self._prepare()
        self.assertTrue(result.storage_rotated)
        self.assertIsNotNone(result.storage_marker_backup)
        kept = self.mission / str(result.storage_marker_backup)
        self.assertTrue(kept.is_file())
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8")), {"schema_version": 9})

    def test_s8_rotation_never_deletes_a_byte(self) -> None:
        tree = _make_storage(self.mission, marker_payload=_valid_marker("b" * 64))
        expected = _tree_digest(tree)
        result = self._prepare()
        self.assertTrue(result.storage_rotated)
        self.assertFalse(tree.exists())
        moved = self.mission / str(result.storage_backup)
        self.assertEqual(_tree_digest(moved), expected)

    def test_s11_a_reserved_name_collision_aborts_before_the_first_mutation(self) -> None:
        _make_storage(self.mission)
        before = _tree_digest(self.mission)
        # Reserve the exact backup name the transaction would claim.
        (self.mission / f"{storage.STORAGE_NAME}.modset-{storage._backup_stamp(1_756_000_000.0)}-legacy").mkdir()
        after_setup = _tree_digest(self.mission)
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertTrue(result.storage_recovery_required)
        self.assertEqual(result.reason, "backup_name_collision")
        self.assertEqual(_tree_digest(self.mission), after_setup)
        self.assertNotEqual(before, after_setup)  # the fixture really changed something

    def test_s12_the_marker_is_a_sibling_and_a_file_inside_is_never_authority(self) -> None:
        tree = _make_storage(self.mission)
        (tree / storage.MARKER_NAME).write_text(
            json.dumps(_valid_marker(SEAL_A)), encoding="utf-8"
        )
        result = self._prepare()
        # The decoy inside the tree did not stop the rotation.
        self.assertTrue(result.storage_rotated)
        self.assertTrue((self.mission / storage.MARKER_NAME).is_file())

    def test_s12b_a_profiles_decoy_is_left_byte_identical(self) -> None:
        decoy = self.mission.parent / "_server" / "profiles"
        (decoy / storage.STORAGE_NAME).mkdir(parents=True)
        (decoy / storage.STORAGE_NAME / "keep.bin").write_bytes(b"decoy")
        expected = _tree_digest(decoy)
        _make_storage(self.mission)
        self._prepare()
        self.assertEqual(_tree_digest(decoy), expected)

    def test_s10_two_active_journals_block_the_launch(self) -> None:
        _make_storage(self.mission)
        for txid in ("1" * 32, "2" * 32):
            (self.mission / f"{storage.JOURNAL_PREFIX}{txid}{storage.JOURNAL_SUFFIX}").write_text(
                "{}", encoding="utf-8"
            )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertTrue(result.storage_recovery_required)
        self.assertEqual(result.reason, "journal_ambiguous")

    def test_s10b_a_journal_name_outside_the_grammar_blocks_the_launch(self) -> None:
        _make_storage(self.mission)
        (self.mission / f"{storage.JOURNAL_PREFIX}not-hex{storage.JOURNAL_SUFFIX}").write_text(
            "{}", encoding="utf-8"
        )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_name_invalid")

    def test_an_unreadable_journal_blocks_the_launch(self) -> None:
        _make_storage(self.mission)
        (self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_SUFFIX}").write_text(
            "not json", encoding="utf-8"
        )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_unreadable")

    def test_a_leftover_temporary_does_not_block_the_mission_forever(self) -> None:
        """A hard kill between the write and the rename leaves the temporary.

        v1 deletes nothing, so it stays. Named after its destination it would
        match the journal prefix without matching the journal grammar, and every
        future launch of this mission would answer journal_name_invalid.
        """
        _make_storage(self.mission, marker_payload=_valid_marker(SEAL_A))
        leftover = self.mission / f"{storage.STORAGE_NAME}.modset.tmp-4242-0"
        leftover.write_text("half a journal", encoding="utf-8")
        result = self._prepare()
        self.assertTrue(result.launch_allowed, result)
        self.assertEqual(result.decision, storage.DECISION_REUSE)
        self.assertTrue(leftover.is_file())

    def test_positive_control_a_journal_shaped_leftover_does_block(self) -> None:
        # Without this the test above would pass over a scan that ignores
        # everything, including a real ambiguous journal.
        _make_storage(self.mission, marker_payload=_valid_marker(SEAL_A))
        (self.mission / f"{storage.JOURNAL_PREFIX}zz{storage.JOURNAL_SUFFIX}").write_text(
            "half a journal", encoding="utf-8"
        )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_name_invalid")

    def test_s9_a_crash_between_the_rename_and_the_seal_recovers_from_the_journal(self) -> None:
        """The case the design names: storage moved, marker not published yet.

        The authority is the journal's new_seal, never the old marker, and the
        recovery must not rotate a second time.
        """
        tree = _make_storage(self.mission, marker_payload=_valid_marker("b" * 64))
        expected = _tree_digest(tree)
        backup = f"{storage.STORAGE_NAME}.modset-19700101-000000-bbbbbbbb"
        journal = self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_SUFFIX}"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "txid": "1" * 32,
                    "phase": storage.PHASE_STORAGE_MOVED,
                    "new_seal": SEAL_A,
                    "old_seal": "b" * 64,
                    "project": "DayZ_MCP",
                    "storage_backup": backup,
                    "marker_backup": f"{backup}.marker.json",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.rename(tree, self.mission / backup)
        result = self._prepare()
        self.assertTrue(result.launch_allowed)
        self.assertTrue(result.storage_rotated)
        self.assertEqual(result.reason, "recovered_storage_moved")
        self.assertEqual(storage.read_marker(str(self.mission)).seal, SEAL_A)
        self.assertEqual(_tree_digest(self.mission / backup), expected)
        self.assertFalse((self.mission / storage.STORAGE_NAME).exists())
        # A second call over the recovered state reuses; it does not rotate again.
        again = storage.prepare_storage(
            str(self.mission),
            seal=SEAL_A,
            project="DayZ_MCP",
            now=1_756_000_100.0,
            txid="3" * 32,
        )
        self.assertFalse(again.storage_rotated)

    def test_f03_marker_published_is_a_claim_and_needs_its_backup(self) -> None:
        """Codex F-03. The phase alone used to authorise the launch.

        Journal says marker_published, the marker carries the new seal, and yet
        storage_1 is still the OLD tree and no backup exists: the rotation never
        happened. Completing it would hand the engine the old world labelled
        with the new mod set -- exactly the poisoning M15 exists to stop.
        """
        _make_storage(self.mission)
        (self.mission / storage.MARKER_NAME).write_text(
            json.dumps(_valid_marker(SEAL_A)), encoding="utf-8"
        )
        backup = f"{storage.STORAGE_NAME}.modset-19700101-000000-legacy"
        (self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_SUFFIX}").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "txid": "1" * 32,
                    "phase": storage.PHASE_MARKER_PUBLISHED,
                    "new_seal": SEAL_A,
                    "old_seal": None,
                    "project": "DayZ_MCP",
                    "storage_backup": backup,
                    "marker_backup": f"{backup}.marker.json",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_state_impossible")

    def test_f03_positive_control_a_real_marker_published_completes(self) -> None:
        # Without it the assertion above would pass over a branch that blocks
        # every marker_published journal, real ones included.
        backup = f"{storage.STORAGE_NAME}.modset-19700101-000000-legacy"
        (self.mission / backup).mkdir()
        (self.mission / backup / "data.bin").write_bytes(b"world-and-characters")
        (self.mission / storage.MARKER_NAME).write_text(
            json.dumps(_valid_marker(SEAL_A)), encoding="utf-8"
        )
        (self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_SUFFIX}").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "txid": "1" * 32,
                    "phase": storage.PHASE_MARKER_PUBLISHED,
                    "new_seal": SEAL_A,
                    "old_seal": None,
                    "project": "DayZ_MCP",
                    "storage_backup": backup,
                    "marker_backup": f"{backup}.marker.json",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        result = self._prepare()
        self.assertTrue(result.launch_allowed, result)
        self.assertTrue(
            (self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_COMPLETED_SUFFIX}").is_file()
        )

    def test_f06_a_directory_that_cannot_be_listed_is_not_an_empty_one(self) -> None:
        """Codex F-06. An observation that failed is not an absence."""
        _make_storage(self.mission, marker_payload=_valid_marker(SEAL_A))
        real_listdir = os.listdir

        def refusing(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(path) == str(self.mission):
                raise PermissionError("cannot enumerate")
            return real_listdir(path, *args, **kwargs)

        os.listdir = refusing
        try:
            result = self._prepare()
        finally:
            os.listdir = real_listdir
        self.assertFalse(result.launch_allowed)
        self.assertTrue(result.storage_recovery_required)
        self.assertEqual(result.reason, "mission_not_enumerable")

    def test_a_journal_whose_physical_state_is_impossible_blocks(self) -> None:
        _make_storage(self.mission)
        backup = f"{storage.STORAGE_NAME}.modset-19700101-000000-legacy"
        (self.mission / backup).mkdir()
        (self.mission / f"{storage.JOURNAL_PREFIX}{'1' * 32}{storage.JOURNAL_SUFFIX}").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "txid": "1" * 32,
                    "phase": storage.PHASE_STORAGE_MOVED,
                    "new_seal": SEAL_A,
                    "old_seal": None,
                    "project": "DayZ_MCP",
                    "storage_backup": backup,
                    "marker_backup": f"{backup}.marker.json",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        result = self._prepare()
        self.assertFalse(result.launch_allowed)
        self.assertEqual(result.reason, "journal_state_impossible")

    def test_an_aborted_journal_lets_the_same_call_still_rotate(self) -> None:
        _make_storage(self.mission)
        (self.mission / f"{storage.JOURNAL_PREFIX}{TXID}{storage.JOURNAL_SUFFIX}").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "txid": TXID,
                    "phase": storage.PHASE_PREPARED,
                    "new_seal": "c" * 64,
                    "old_seal": None,
                    "project": "DayZ_MCP",
                    "storage_backup": "storage_1.modset-19700101-000000-legacy",
                    "marker_backup": "storage_1.modset-19700101-000000-legacy.marker.json",
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        result = self._prepare()
        self.assertTrue(result.launch_allowed)
        self.assertTrue(result.storage_rotated)


class CrashBetweenEveryPairOfIoTest(unittest.TestCase):
    """Kill the process at every rename/replace boundary, then recover.

    Durable invariant asserted after each: every byte of the original tree is
    still readable under the mission, and the next call either recovers or
    refuses to launch. Never a silent launch over an ambiguous tree.
    """

    def setUp(self) -> None:
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.mission = Path(self._temporary.name) / "dayzOffline.chernarusplus"
        self.mission.mkdir()

    def _content_map(self, root: Path) -> dict[str, str]:
        found: dict[str, str] = {}
        for current, _dirs, filenames in os.walk(root):
            for name in filenames:
                path = Path(current) / name
                found[hashlib.sha256(path.read_bytes()).hexdigest()] = str(path)
        return found

    def test_every_io_boundary(self) -> None:
        real_rename = os.rename
        real_replace = os.replace
        # Count the boundaries a clean rotation crosses, then fault each one.
        counter = {"n": 0}

        def counting_rename(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            counter["n"] += 1
            return real_rename(src, dst, *args, **kwargs)

        def counting_replace(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            counter["n"] += 1
            return real_replace(src, dst, *args, **kwargs)

        _make_storage(self.mission, marker_payload=_valid_marker("b" * 64))
        os.rename, os.replace = counting_rename, counting_replace
        try:
            storage.prepare_storage(
                str(self.mission),
                seal=SEAL_A,
                project="DayZ_MCP",
                now=1_756_000_000.0,
                txid=TXID,
            )
        finally:
            os.rename, os.replace = real_rename, real_replace
        boundaries = counter["n"]
        self.assertGreaterEqual(boundaries, 4, "a rotation must cross several boundaries")

        for fault_at in range(1, boundaries + 1):
            with self.subTest(fault_at=fault_at):
                with TemporaryDirectory() as room:
                    mission = Path(room) / "dayzOffline.chernarusplus"
                    mission.mkdir()
                    tree = _make_storage(mission, marker_payload=_valid_marker("b" * 64))
                    payloads = set(self._content_map(tree))
                    state = {"n": 0}

                    def faulting(real):  # type: ignore[no-untyped-def]
                        def wrapper(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
                            state["n"] += 1
                            if state["n"] == fault_at:
                                raise KeyboardInterrupt("killed between two I/O")
                            return real(src, dst, *args, **kwargs)

                        return wrapper

                    os.rename, os.replace = faulting(real_rename), faulting(real_replace)
                    try:
                        with self.assertRaises(KeyboardInterrupt):
                            storage.prepare_storage(
                                str(mission),
                                seal=SEAL_A,
                                project="DayZ_MCP",
                                now=1_756_000_000.0,
                                txid=TXID,
                            )
                    finally:
                        os.rename, os.replace = real_rename, real_replace

                    survivors = set(self._content_map(mission))
                    self.assertTrue(
                        payloads.issubset(survivors),
                        f"a crash at boundary {fault_at} lost player data",
                    )
                    recovered = storage.prepare_storage(
                        str(mission),
                        seal=SEAL_A,
                        project="DayZ_MCP",
                        now=1_756_000_100.0,
                        txid="4" * 32,
                    )
                    self.assertTrue(
                        payloads.issubset(set(self._content_map(mission))),
                        f"recovery after boundary {fault_at} lost player data",
                    )
                    if not recovered.launch_allowed:
                        self.assertTrue(recovered.storage_recovery_required)
                        continue
                    marker = storage.read_marker(str(mission))
                    self.assertEqual(marker.state, storage.MARKER_PRESENT_VALID)
                    self.assertEqual(marker.seal, SEAL_A)


class DiagnosticRuleTest(unittest.TestCase):
    def test_the_annex_rule_pins_both_rpt_signatures(self) -> None:
        """Annex 01ae. One signature is not the rule: the 16:26:48 start of
        2026-09-04 has `Failed to read modstorage` = 0 and 43197 of the other.
        """
        self.assertEqual(
            storage.STORAGE_POISON_RPT_SIGNATURES,
            ("Failed to read modstorage", "Scripted variables corrupted"),
        )
        self.assertIn("server_poll_stale", storage.__doc__ or "")


if __name__ == "__main__":
    unittest.main()
