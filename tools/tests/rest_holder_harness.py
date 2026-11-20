"""Real MCPClientRestHolder / MCPClientRestForwarder bodies, translated.

The holder and the forwarder used to be hand-written Python copies in the
poll and result-pool harnesses, so an inverted identity comparison in the
Enforce source stayed green (review P3 #3). These factories instead read
``MCP_ClientRestCallbacks.c`` / ``MCPClientBridge.c`` and translate the
real ``Hold``/``Drop``/``Count`` and ``Disconnect``/``IsConnected``/
``IsPollActive`` (plus the forwarder's ``Sink*`` delegations) with
``tests.enforce_subset_helpers``, exactly as the bridge bodies are
translated elsewhere. A mutation of the source text therefore mutates
the code under test.
"""
from __future__ import annotations

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import (
    EnforceArray,
    EnforceString,
    method_body,
    translate,
)

WORLD_PATH = addon_root() / "scripts" / "4_World" / "MCP_ClientRestCallbacks.c"
CLIENT_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"

HOLDER_SIGNATURES = (
    "static void Hold(RestCallback cb)",
    "static void Drop(RestCallback cb)",
    "static int Count()",
)

FORWARDER_SIGNATURES = (
    "void MCPClientRestForwarder(MCPClientBridge owner)",
    "void Disconnect()",
    "override bool IsConnected()",
    "override bool IsPollActive(MCPClientPollCallback cb)",
    "override void SinkReleaseCallback(RestCallback cb)",
    "override void SinkRecycleResultCallback(MCPClientResultCallback cb)",
    "override void SinkOnPollSuccess(string data, int dataSize)",
    "override void SinkOnPollError(int errorCode)",
    "override void SinkOnPollTimeout()",
    "override void SinkOnResultSuccess(string data, int dataSize)",
    "override void SinkOnResultError(int errorCode)",
    "override void SinkOnResultTimeout()",
)


class TranslatedRestHolder:
    """MCPClientRestHolder: the real static Hold/Drop/Count, translated.

    Each factory call is one holder state (the Enforce ``s_Callbacks`` is
    static, so one instance models one process); share one instance to
    model two missions sharing the holder.
    """

    def __init__(self, world_source: str | None = None) -> None:
        source = WORLD_PATH.read_text(encoding="utf-8") if world_source is None else world_source
        self._ns: dict = {
            "_S": EnforceString,
            "array": EnforceArray,
            "s_Callbacks": None,
        }
        for signature in HOLDER_SIGNATURES:
            body = method_body(source, signature)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self._ns,
                members={"s_Callbacks"},
            )

    def Hold(self, cb) -> None:
        self._ns["Hold"](cb)

    def Drop(self, cb) -> None:
        self._ns["Drop"](cb)

    def Count(self) -> int:
        return self._ns["Count"]()

    @property
    def callbacks(self) -> list:
        held = self._ns.get("s_Callbacks")
        return list(held) if held is not None else []


def make_holder(world_source: str | None = None) -> TranslatedRestHolder:
    return TranslatedRestHolder(world_source)


class TranslatedRestForwarder:
    """MCPClientRestForwarder: the real delegations, translated.

    ``m_Owner`` lives in this instance's namespace, so ``Disconnect()``
    (the real ``m_Owner = null;``) flips the real ``IsConnected()`` and
    ``IsPollActive()`` for exactly this forwarder, as in the engine.
    """

    def __init__(self, owner, bridge_source: str | None = None) -> None:
        source = CLIENT_PATH.read_text(encoding="utf-8") if bridge_source is None else bridge_source
        self._ns: dict = {
            "_S": EnforceString,
            "m_Owner": None,
        }
        for signature in FORWARDER_SIGNATURES:
            body = method_body(source, signature)
            translate(
                signature + "\n{" + body + "}\n",
                signature,
                self._ns,
                members={"m_Owner"},
            )
        self._ns["MCPClientRestForwarder"](owner)

    @property
    def owner(self):
        return self._ns["m_Owner"]

    @owner.setter
    def owner(self, value) -> None:
        self._ns["m_Owner"] = value

    def Disconnect(self) -> None:
        self._ns["Disconnect"]()

    def IsConnected(self) -> bool:
        return self._ns["IsConnected"]()

    def IsPollActive(self, cb) -> bool:
        return self._ns["IsPollActive"](cb)

    def SinkReleaseCallback(self, cb) -> None:
        self._ns["SinkReleaseCallback"](cb)

    def SinkRecycleResultCallback(self, cb) -> None:
        self._ns["SinkRecycleResultCallback"](cb)

    def SinkOnPollSuccess(self, data, size) -> None:
        self._ns["SinkOnPollSuccess"](data, size)

    def SinkOnPollError(self, code) -> None:
        self._ns["SinkOnPollError"](code)

    def SinkOnPollTimeout(self) -> None:
        self._ns["SinkOnPollTimeout"]()

    def SinkOnResultSuccess(self, data, size) -> None:
        self._ns["SinkOnResultSuccess"](data, size)

    def SinkOnResultError(self, code) -> None:
        self._ns["SinkOnResultError"](code)

    def SinkOnResultTimeout(self) -> None:
        self._ns["SinkOnResultTimeout"]()


def make_forwarder(owner, bridge_source: str | None = None) -> TranslatedRestForwarder:
    return TranslatedRestForwarder(owner, bridge_source)
