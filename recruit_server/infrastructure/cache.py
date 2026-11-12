"""Bounded process-local TTL/LRU cache with single-flight loads and invalidation."""
import copy
import json
import threading
import time
from collections import OrderedDict


class Cache:
    def __init__(self, ttl, max_entries, max_bytes):
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.epoch = 0
        self.lock = threading.RLock()
        # Fixed stripes bound lock memory and coalesce simultaneous identical reads.
        self.stripes = [threading.RLock() for _ in range(32)]
        self.write_locks = [threading.RLock() for _ in range(32)]

    def load(self, key, loader, *, cacheable=lambda value: True):
        if self.ttl == 0:
            return loader(), False
        with self.stripes[hash(key) % len(self.stripes)]:
            with self.lock:
                now = time.monotonic()
                for stale in [k for k, entry in self.entries.items() if entry[0] <= now]:
                    self.bytes -= self.entries.pop(stale)[2]
                entry = self.entries.get(key)
                if entry is not None:
                    self.entries.move_to_end(key)
                    return copy.deepcopy(entry[1]), True
                generation = self.epoch
            value = loader()
            if cacheable(value):
                size = len(json.dumps(value, ensure_ascii=True).encode())
                if size <= self.max_bytes:
                    with self.lock:
                        # A completed/uncertain write invalidates even in-flight reads.
                        if generation == self.epoch:
                            while self.entries and (len(self.entries) >= self.max_entries
                                                    or self.bytes + size > self.max_bytes):
                                self.bytes -= self.entries.popitem(last=False)[1][2]
                            self.entries[key] = (time.monotonic() + self.ttl, copy.deepcopy(value), size)
                            self.bytes += size
            return value, False

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.bytes = 0
            self.epoch += 1
