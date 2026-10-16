"""Bounded metadata cache, with no SDK/global runtime dependencies."""

import time


class TTLCache:
    def __init__(self, capacity=100, clock=time.monotonic):
        self.capacity, self.clock = capacity, clock
        self.items = {}

    def get(self, key):
        record = self.items.get(key)
        if record is None:
            return None
        if record[0] <= self.clock():
            self.items.pop(key)
            return None
        return record[1]

    def put(self, key, value, ttl):
        now = self.clock()
        self.items = {k: v for k, v in self.items.items() if v[0] > now}
        if key not in self.items and len(self.items) >= self.capacity:
            self.items.pop(min(self.items, key=lambda k: self.items[k][0]))
        self.items[key] = (now + ttl, value)

    def clear(self):
        self.items.clear()
