"""
main.py - 项目入口
负责初始化缓存并注入到各 Service。
当前使用 LocalCache，需要迁移到 RedisClient。
"""

from cache.local_cache import LocalCache
from services.user_service import UserService
from services.product_service import ProductService
from services.order_service import OrderService


def create_app():
    cache = LocalCache()
    user_service = UserService(cache)
    product_service = ProductService(cache)
    order_service = OrderService(cache)
    return {
        "user": user_service,
        "product": product_service,
        "order": order_service,
    }


if __name__ == "__main__":
    app = create_app()
    app["user"].set_user_profile("001", "Alice")
    print(app["user"].get_user_name("001"))
