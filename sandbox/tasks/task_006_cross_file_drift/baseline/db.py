"""
db.py - 数据库访问层
依赖关系：被 service.py 调用
"""

import json
from typing import Dict, Optional

# 模拟用户数据库
_USER_DB: Dict[str, Dict] = {
    "user_001": {"id": "user_001", "name": "Alice", "email": "alice@example.com", "role": "admin"},
    "user_002": {"id": "user_002", "name": "Bob", "email": "bob@example.com", "role": "user"},
    "user_003": {"id": "user_003", "name": "Charlie", "email": "charlie@example.com", "role": "user"},
}


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """
    根据用户ID获取用户信息
    
    Args:
        user_id: 用户唯一标识符
        
    Returns:
        用户信息字典，如果不存在则返回 None
    """
    if not user_id:
        return None
    return _USER_DB.get(user_id, None)


def get_user_by_email(email: str) -> Optional[Dict]:
    """根据邮箱获取用户信息"""
    for user in _USER_DB.values():
        if user.get("email") == email:
            return user
    return None


def get_all_users() -> list:
    """获取所有用户列表"""
    return list(_USER_DB.values())


def user_exists(user_id: str) -> bool:
    """检查用户是否存在"""
    return user_id in _USER_DB


def create_user(user_id: str, name: str, email: str, role: str = "user") -> bool:
    """创建新用户"""
    if user_id in _USER_DB:
        return False
    _USER_DB[user_id] = {"id": user_id, "name": name, "email": email, "role": role}
    return True


def update_user(user_id: str, **kwargs) -> bool:
    """更新用户信息"""
    if user_id not in _USER_DB:
        return False
    _USER_DB[user_id].update(kwargs)
    return True


def delete_user(user_id: str) -> bool:
    """删除用户"""
    if user_id not in _USER_DB:
        return False
    del _USER_DB[user_id]
    return True


def batch_get_users(user_ids: list) -> list:
    """批量获取用户"""
    return [_USER_DB.get(uid) for uid in user_ids if uid in _USER_DB]


def get_user_count() -> int:
    """获取用户总数"""
    return len(_USER_DB)


def search_users_by_name(keyword: str) -> list:
    """根据名称关键词搜索用户"""
    keyword_lower = keyword.lower()
    return [
        user for user in _USER_DB.values()
        if keyword_lower in user.get("name", "").lower()
    ]


def export_users_to_json() -> str:
    """导出用户数据为JSON"""
    return json.dumps(_USER_DB, indent=2)


def import_users_from_json(json_str: str) -> int:
    """从JSON导入用户数据"""
    try:
        data = json.loads(json_str)
        _USER_DB.update(data)
        return len(data)
    except json.JSONDecodeError:
        return 0