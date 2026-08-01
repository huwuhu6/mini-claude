"""
local_cache.py - 本地内存缓存（已废弃，待迁移到 RedisClient）
"""

from typing import Optional


class LocalCache:
    """基于 dict 的本地缓存，将被 RedisClient 取代。"""

    def __init__(self):
        self._store = {}

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
