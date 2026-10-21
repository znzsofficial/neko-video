"""Chat-scoped, expiring media handles; never cache image/video bodies."""

import time
import uuid
from dataclasses import dataclass


@dataclass
class Asset:
    scope: str
    kind: str
    urls: tuple[str, ...]
    digest: str
    expires: float
    state: str = "ready"


class MediaAssets:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.items: dict[str, Asset] = {}

    def clear(self):
        self.items.clear()

    def add(self, scope, kind, urls, digest=""):
        now = self.clock()
        for key, asset in list(self.items.items()):
            if asset.expires <= now:
                self.items.pop(key)
        owned = [key for key, asset in self.items.items() if asset.scope == scope]
        if len(owned) >= 24:
            self.items.pop(owned[0])
        while len(self.items) >= 128:
            self.items.pop(next(iter(self.items)))
        key = uuid.uuid4().hex
        self.items[key] = Asset(scope, kind, tuple(urls[:8]), digest, now + 600)
        return key

    def claim(self, scope, key):
        asset = self.peek(scope, key)
        if asset.state != "ready":
            raise ValueError("该媒体已发送、处理中或投递未确认，不自动重发")
        asset.state = "busy"
        return asset

    def peek(self, scope, key):
        """Analysis may inspect a handle without claiming/resetting delivery state."""
        asset = self.items.get(key)
        if asset is None or asset.scope != scope or asset.expires <= self.clock():
            raise ValueError("媒体编号已过期或不属于当前聊天，请重新读取")
        return asset
