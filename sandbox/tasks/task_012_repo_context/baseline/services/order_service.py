"""
order_service.py - 订单业务逻辑层
依赖：cache.local_cache.LocalCache
"""

from cache.local_cache import LocalCache


class OrderService:
    def __init__(self, cache: LocalCache):
        self.cache = cache

    def get_order_status(self, order_id: str) -> str:
        key = f"order:{order_id}"
        status = self.cache.get(key)
        if status is None:
            status = "pending"
            self.cache.set(key, status)
        return status

    def update_order_status(self, order_id: str, status: str) -> None:
        key = f"order:{order_id}"
        self.cache.set(key, status)

    def cancel_order(self, order_id: str) -> None:
        self.cache.delete(f"order:{order_id}")
