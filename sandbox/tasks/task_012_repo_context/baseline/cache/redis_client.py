"""
redis_client.py - Redis 缓存客户端（新项目统一使用此接口）
"""

from typing import Optional


class RedisClient:
    """基于内存 dict 模拟的 Redis 客户端。

    与 LocalCache 的区别：
    - set() 方法需要传入 ttl（过期时间，单位秒）
    - 支持键过期清理
    """

    def __init__(self):
        self._store = {}
        self._ttl = {}

    def get(self, key: str) -> Optional[str]:
        import time
        if key in self._ttl and time.time() > self._ttl[key]:
            self._store.pop(key, None)
            self._ttl.pop(key, None)
            return None
        return self._store.get(key)

    def set(self, key: str, value: str, ttl: int = 300) -> None:
        import time
        self._store[key] = value
        self._ttl[key] = time.time() + ttl

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttl.pop(key, None)
