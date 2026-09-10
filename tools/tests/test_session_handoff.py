from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from dayz_mcp.session_coordination import (  # noqa: E402
    SESSION_TTL_S,
    ClientIdentity,
    SessionCoordinator,
)
from dayz_mcp.session_handoff import (  # noqa: E402
    HANDOFF_ENV,
    HANDOFF_VERSION,
    MAX_HANDOFF_AGE_S,
    carrier_path,
    clear_handoff,
    consume_handoff,
    write_handoff,
)


def _identity(pid: int = 1000, session_id: str = "s" * 32) -> ClientIdentity:
    return ClientIdentity(
        platform="claude",
        pid=pid,
        ppid=pid - 100,
        started_at_utc="2026-09-10T12:00:00Z",
        session_id=session_id,
        task_label="handoff test",
    )


class HandoffRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-test-"))
        self.path = self.dir / "session-handoff.json"
        self.identity = _identity()

    def _write(self, **overrides: object) -> None:
        payload: dict[str, object] = {
            "identity": self.identity,
            "lease_token": "token-abc",
            "lease_id": "lease-1",
            "generation": 3,
        }
        payload.update(overrides)
        write_handoff(self.path, now=lambda: 1000.0, **payload)  # type: ignore[arg-type]

    def test_round_trip_carries_both_secrets_and_the_whole_identity(self) -> None:
        self._write()
        carried = consume_handoff(self.path, now=lambda: 1000.0)
        assert carried is not None
        self.assertEqual(carried.identity, self.identity)
        self.assertEqual(carried.lease_token, "token-abc")
        self.assertEqual(carried.lease_id, "lease-1")
        self.assertEqual(carried.generation, 3)

    def test_the_identity_that_comes_back_is_not_the_object_that_went_in(self) -> None:
        # Guards the tautology the rehearsal caught: equality has to survive the
        # serialisation, not just hold because it is the same Python object.
        self._write()
        carried = consume_handoff(self.path, now=lambda: 1000.0)
        assert carried is not None
        self.assertIsNot(carried.identity, self.identity)
        self.assertEqual(carried.identity, self.identity)

    def test_consuming_removes_the_carrier_so_it_cannot_be_replayed(self) -> None:
        self._write()
        self.assertTrue(self.path.exists())
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: 1000.0))
        self.assertFalse(self.path.exists())
        self.assertIsNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_a_malformed_carrier_is_removed_too_not_retried_forever(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(consume_handoff(self.path))
        self.assertFalse(self.path.exists())

    def test_missing_carrier_is_not_an_error(self) -> None:
        self.assertIsNone(consume_handoff(self.dir / "absent.json"))

    def test_clear_removes_it_and_tolerates_absence(self) -> None:
        self._write()
        clear_handoff(self.path)
        self.assertFalse(self.path.exists())
        clear_handoff(self.path)


class HandoffRefusalTest(unittest.TestCase):
    """Every refusal returns None -- start leaseless -- and never raises."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-refuse-"))
        self.path = self.dir / "session-handoff.json"
        self.good = {
            "version": HANDOFF_VERSION,
            "identity": _identity().to_payload(),
            "lease_token": "token-abc",
            "lease_id": "lease-1",
            "generation": 3,
            "written_at": 1000.0,
        }

    def _put(self, document: object) -> None:
        self.path.write_text(json.dumps(document), encoding="utf-8")

    def _refused(self, **overrides: object) -> None:
        document = dict(self.good)
        document.update(overrides)
        self._put(document)
        self.assertIsNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_a_good_document_is_the_control_and_is_accepted(self) -> None:
        self._put(self.good)
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: 1000.0))

    def test_wrong_version(self) -> None:
        self._refused(version=HANDOFF_VERSION + 1)

    def test_pid_that_arrived_as_text(self) -> None:
        # What an env var would hand over. from_payload rejects it; so do we.
        identity = _identity().to_payload()
        identity["pid"] = str(identity["pid"])
        self._refused(identity=identity)

    def test_identity_missing_a_field(self) -> None:
        identity = _identity().to_payload()
        del identity["ppid"]
        self._refused(identity=identity)

    def test_empty_or_absurd_secrets(self) -> None:
        self._refused(lease_token="")
        self._refused(lease_id="")
        self._refused(lease_token="x" * 5000)

    def test_generation_that_is_a_bool_or_negative(self) -> None:
        self._refused(generation=True)
        self._refused(generation=-1)

    def test_a_carrier_older_than_the_lease_it_names(self) -> None:
        self._put(self.good)
        stale = 1000.0 + MAX_HANDOFF_AGE_S + 1.0
        self.assertIsNone(consume_handoff(self.path, now=lambda: stale))

    def test_a_carrier_from_the_future_beyond_skew(self) -> None:
        self._put(self.good)
        self.assertIsNone(consume_handoff(self.path, now=lambda: 900.0))

    def test_a_carrier_right_at_the_edge_still_works(self) -> None:
        # The symmetric check: narrowing the window must not refuse a legitimate one.
        self._put(self.good)
        edge = 1000.0 + MAX_HANDOFF_AGE_S - 0.001
        self.assertIsNotNone(consume_handoff(self.path, now=lambda: edge))

    def test_a_document_that_is_not_an_object(self) -> None:
        self._put(["not", "a", "mapping"])
        self.assertIsNone(consume_handoff(self.path))


class HandoffWriterContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-write-"))
        self.path = self.dir / "session-handoff.json"

    def test_writer_refuses_a_caller_mistake_instead_of_persisting_it(self) -> None:
        with self.assertRaises(TypeError):
            write_handoff(
                self.path, identity="not-an-identity", lease_token="t",  # type: ignore[arg-type]
                lease_id="l", generation=0,
            )
        with self.assertRaises(ValueError):
            write_handoff(
                self.path, identity=_identity(), lease_token="", lease_id="l", generation=0
            )
        with self.assertRaises(ValueError):
            write_handoff(
                self.path, identity=_identity(), lease_token="t", lease_id="l",
                generation=True,  # type: ignore[arg-type]
            )
        self.assertFalse(self.path.exists())
        self.assertFalse((self.dir / "session-handoff.json.tmp").exists())

    def test_rewriting_replaces_rather_than_appends(self) -> None:
        write_handoff(
            self.path, identity=_identity(), lease_token="first", lease_id="l1", generation=1
        )
        write_handoff(
            self.path, identity=_identity(), lease_token="second", lease_id="l2", generation=2
        )
        carried = consume_handoff(self.path)
        assert carried is not None
        self.assertEqual(carried.lease_token, "second")
        self.assertFalse((self.dir / "session-handoff.json.tmp").exists())

    def test_the_token_is_not_in_the_env_var_name_or_the_path(self) -> None:
        # The rehearsal's rule: only the PATH travels. This box's process list is shared.
        path = carrier_path(self.dir)
        write_handoff(
            path, identity=_identity(), lease_token="s3cret-token", lease_id="l", generation=0
        )
        self.assertNotIn("s3cret-token", str(path))
        self.assertNotIn("s3cret-token", HANDOFF_ENV)

    def test_carrier_path_defaults_to_a_private_directory(self) -> None:
        path = carrier_path()
        self.assertTrue(path.parent.is_dir())
        self.assertEqual(path.name, "session-handoff.json")


class HandoffAgainstTheRealCoordinatorTest(unittest.TestCase):
    """The carrier is only worth anything if the coordinator accepts what comes out.

    This is the rehearsal's group C and F, now against the shipped carrier instead of a
    hand-built dict: acquire as one worker, hand over, and mutate as the next one.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="handoff-coord-"))
        self.path = self.dir / "session-handoff.json"
        self.clock = 0.0
        self.coordinator = SessionCoordinator(
            time_fn=lambda: self.clock,
            token_fn=lambda: "token-1",
            id_fn=lambda: "lease-1",
            audit=lambda _event: None,
            cleanup=lambda *_args: {},
        )

    def test_the_next_worker_keeps_the_lease_and_the_lease_id(self) -> None:
        old = _identity(pid=1000)
        status, active = self.coordinator.acquire(old, "before recycle")
        self.assertEqual(status, 200)
        self.assertTrue(self.coordinator.authorize(old, active["lease_token"], "world_spawn").allowed)

        write_handoff(
            self.path,
            identity=old,
            lease_token=active["lease_token"],
            lease_id=active["lease_id"],
            generation=1,
            now=lambda: 1000.0,
        )
        # The recycle itself: measured at 2.24 s by the spike, against a 120 s TTL.
        self.clock += 2.24
        carried = consume_handoff(self.path, now=lambda: 1002.24)
        assert carried is not None

        decision = self.coordinator.authorize(
            carried.identity, carried.lease_token, "world_spawn"
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.lease_id, active["lease_id"])
        self.assertEqual(self.coordinator.release(carried.identity, carried.lease_token)[0], 200)

    def test_a_worker_that_starts_without_the_carrier_cannot_touch_the_lease(self) -> None:
        old = _identity(pid=1000)
        _, active = self.coordinator.acquire(old, "before recycle")
        fresh = _identity(pid=2000, session_id="d" * 32)
        decision = self.coordinator.authorize(fresh, active["lease_token"], "world_spawn")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error, "lease_invalid")

    def test_an_overrun_recycle_does_not_get_the_box_back(self) -> None:
        # The rehearsal's E4, now end to end through the carrier: past the TTL the lease
        # is gone, and the carried token must not smuggle it back.
        old = _identity(pid=1000)
        _, active = self.coordinator.acquire(old, "before recycle")
        write_handoff(
            self.path, identity=old, lease_token=active["lease_token"],
            lease_id=active["lease_id"], generation=1, now=lambda: 1000.0,
        )
        self.clock += SESSION_TTL_S + 1.0
        carried = consume_handoff(self.path, now=lambda: 1000.5)
        assert carried is not None
        self.assertFalse(
            self.coordinator.authorize(carried.identity, carried.lease_token, "world_spawn").allowed
        )


if __name__ == "__main__":
    unittest.main()
