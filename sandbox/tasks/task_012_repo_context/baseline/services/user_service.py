"""
user_service.py - 用户业务逻辑层
依赖：cache.local_cache.LocalCache
"""

from cache.local_cache import LocalCache


class UserService:
    def __init__(self, cache: LocalCache):
        self.cache = cache

    def get_user_name(self, user_id: str) -> str:
        key = f"user:{user_id}"
        name = self.cache.get(key)
        if name is None:
            name = f"User_{user_id}"
            self.cache.set(key, name)
        return name

    def set_user_profile(self, user_id: str, profile: str) -> None:
        key = f"user_profile:{user_id}"
        self.cache.set(key, profile)

    def delete_user(self, user_id: str) -> None:
        self.cache.delete(f"user:{user_id}")
        self.cache.delete(f"user_profile:{user_id}")
