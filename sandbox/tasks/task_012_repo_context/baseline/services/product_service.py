"""
product_service.py - 商品业务逻辑层
依赖：cache.local_cache.LocalCache
"""

from cache.local_cache import LocalCache


class ProductService:
    def __init__(self, cache: LocalCache):
        self.cache = cache

    def get_product_price(self, product_id: str) -> float:
        key = f"product:{product_id}"
        price_str = self.cache.get(key)
        if price_str is None:
            price = 99.9
            self.cache.set(key, str(price))
            return price
        return float(price_str)

    def set_product_stock(self, product_id: str, stock: int) -> None:
        key = f"stock:{product_id}"
        self.cache.set(key, str(stock))

    def clear_product_cache(self, product_id: str) -> None:
        self.cache.delete(f"product:{product_id}")
        self.cache.delete(f"stock:{product_id}")
