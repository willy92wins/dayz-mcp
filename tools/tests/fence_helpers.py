"""Helpers so existing tests can enqueue mutations under instance fencing."""
from __future__ import annotations

INST_SERVER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INST_CLIENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PID_SERVER = 41001
PID_CLIENT = 41002


def bind_both_peers(state: object, run_id: str = "test-run") -> tuple[str, str]:
    installer = getattr(state, "install_bound_peer", None)
    if not callable(installer):
        raise AssertionError("ServerState.install_bound_peer is required")
    installer(
        instance=INST_SERVER,
        role="server",
        pid=PID_SERVER,
        run_id=run_id,
    )
    installer(
        instance=INST_CLIENT,
        role="client",
        pid=PID_CLIENT,
        run_id=run_id,
    )
    return INST_SERVER, INST_CLIENT


def accredited_poll(
    state: object,
    peer: str,
    version: str | None = None,
):
    instance = INST_SERVER if peer == "server" else INST_CLIENT
    pid = PID_SERVER if peer == "server" else PID_CLIENT
    return state.record_poll(peer, version, instance=instance, source_pid=pid)


def bound_queue(state: object, peer: str) -> list[dict]:
    instance = INST_SERVER if peer == "server" else INST_CLIENT
    queues = getattr(state, "_bound_queues", {})
    return list(queues.get(instance, []))
