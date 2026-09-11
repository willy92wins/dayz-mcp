"""Explicit host isolation for lifecycle tests unrelated to Steam readiness."""
from dayz_mcp.steam_launch_guard import Preparation, SteamIdentity


class FakeSteamGate:
    degraded = False

    def __init__(self):
        self.claimed = False
        self.preparations = []
        self.checks = 0
        self.action = None

    def claim(self):
        if self.claimed:
            return False
        self.claimed = True
        return True

    def release(self):
        self.claimed = False

    def prepare(self, **kwargs):
        self.preparations.append(kwargs)
        if self.action:
            return self.action(**kwargs)
        return Preparation(identity=SteamIdentity(41, 134000000000000000), startup="observed")

    def final_check(self, prepared):
        self.checks += 1
        return True
