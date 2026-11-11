"""A local Hologram's fakes and ApplyDeployRefusal translated (inbox 204e),
shared by test_204e_deploy_hologram's model tests and test_204e_instant_action's
dispatch tests. Moved from test_204e_deploy_hologram.py."""
from __future__ import annotations

from types import SimpleNamespace

from tests._addon_paths import addon_root
from tests.enforce_subset_helpers import EnforceArray, EnforceVector, fake_class, translate

BRIDGE_PATH = addon_root() / "scripts" / "5_Mission" / "MCPClientBridge.c"
MESSAGES_PATH = addon_root() / "scripts" / "5_Mission" / "MCPMessages.c"
REFUSAL = "protected void ApplyDeployRefusal(ActionBase action, PlayerBase player, MCPResult result)"
VECTOR_TO_ARRAY = "protected void VectorToArray(vector v, array<float> a)"
# The checks in the order the addon asks them, and the hologram method each
# reads; a check refuses when the method answers `refuses`.
CHECKS = (
    ("is_floating", "IsFloating", True),
    ("is_colliding_bbox", "IsCollidingBBox", True),
    ("is_clipping_roof", "IsClippingRoof", True),
    ("is_base_viable", "IsBaseViable", False),
    ("height_placement_check", "HeightPlacementCheck", False),
)


class Projection:
    def __init__(self, pos: tuple[float, float, float]) -> None:
        self.pos = pos

    def GetPosition(self) -> EnforceVector:
        return EnforceVector(self.pos)


class Hologram:
    """A local Hologram. Each check answers as if it passed unless named in
    `refusing`; `asked` records every method the addon called."""

    def __init__(
        self,
        *,
        colliding: bool = True,
        refusing: tuple[str, ...] = (),
        radius: float = 2.5,
        projection: object = "default",
    ) -> None:
        self.colliding = colliding
        self.refusing = refusing
        self.radius = radius
        self.projection = Projection((7500.0, 12.5, 2000.25)) if projection == "default" else projection
        self.asked: list[str] = []

    def _check(self, method: str) -> bool:
        self.asked.append(method)
        for name, check, refuses in CHECKS:
            if check == method:
                return refuses if name in self.refusing else not refuses
        raise AssertionError(method)

    def IsColliding(self) -> bool:
        self.asked.append("IsColliding")
        return self.colliding

    def IsFloating(self) -> bool:
        return self._check("IsFloating")

    def IsCollidingBBox(self) -> bool:
        return self._check("IsCollidingBBox")

    def IsClippingRoof(self) -> bool:
        return self._check("IsClippingRoof")

    def IsBaseViable(self) -> bool:
        return self._check("IsBaseViable")

    def HeightPlacementCheck(self) -> bool:
        return self._check("HeightPlacementCheck")

    def GetProjectionRadius(self) -> float:
        self.asked.append("GetProjectionRadius")
        return self.radius

    def GetProjectionEntity(self) -> object:
        self.asked.append("GetProjectionEntity")
        return self.projection


class Deploy:
    """Stands for an ActionDeployObject; the fake Cast in model tells it apart
    (test_204e_instant_action's DeployAction is one too)."""


def _is_deploy(action: object) -> object:
    return action if isinstance(action, Deploy) else None


def model(bridge: str | None = None) -> dict:
    """ApplyDeployRefusal and VectorToArray of `bridge`, translated, with
    MCPResult and MCPHologram as MCPMessages.c declares them."""
    source = BRIDGE_PATH.read_text(encoding="utf-8") if bridge is None else bridge
    messages = MESSAGES_PATH.read_text(encoding="utf-8")
    namespace: dict = {
        "array": EnforceArray,
        "ActionDeployObject": SimpleNamespace(Cast=_is_deploy),
    }
    fake_class(messages, "MCPHologram", namespace)
    fake_class(messages, "MCPResult", namespace)
    translate(source, VECTOR_TO_ARRAY, namespace)
    translate(source, REFUSAL, namespace, class_types=("Hologram", "Object", "MCPHologram"))
    return namespace


def checks_of(result) -> list[str]:
    return [name.text for name in result.hologram.checks]
