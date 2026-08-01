"""
service.py - 业务逻辑层
依赖关系：调用 db.py，被 controller.py 调用
"""

import logging
from typing import Dict, Optional, List
from db import get_user_by_id, get_all_users, user_exists, get_user_count

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_user_service(user_id: str) -> Optional[Dict]:
    """
    获取用户信息的业务逻辑（需要重构：将 user_id 参数名改为 uid）
    
    Args:
        user_id: 用户唯一标识符
        
    Returns:
        用户信息字典，包含额外处理字段
    """
    if not user_id:
        logger.warning("get_user_service called with empty user_id")
        return None
    
    user = get_user_by_id(user_id)
    
    if user:
        # 添加一些业务字段
        user = user.copy()
        user["display_name"] = user["name"].upper()
        user["is_premium"] = user.get("role") == "admin"
        logger.info(f"Retrieved user: {user_id}")
    
    return user


def get_all_users_service() -> List[Dict]:
    """获取所有用户的业务逻辑"""
    users = get_all_users()
    for user in users:
        user["display_name"] = user["name"].upper()
    logger.info(f"Retrieved {len(users)} users")
    return users


def user_exists_service(user_id: str) -> bool:
    """检查用户是否存在（业务层）"""
    return user_exists(user_id)


def get_user_profile(user_id: str) -> Dict:
    """
    获取用户完整资料（需要重构：将 user_id 参数名改为 uid）
    
    Args:
        user_id: 用户唯一标识符
        
    Returns:
        用户资料，包含基本信息和统计数据
    """
    user = get_user_service(user_id)
    
    if not user:
        return {"error": "User not found", "user_id": user_id}
    
    # 添加统计数据
    total_users = get_user_count()
    user["total_users_count"] = total_users
    user["rank"] = "top" if user.get("is_premium") else "normal"
    
    return user


def validate_user_permission(user_id: str, required_role: str) -> bool:
    """
    验证用户权限（需要重构：将 user_id 参数名改为 uid）
    
    Args:
        user_id: 用户唯一标识符
        required_role: 所需角色
        
    Returns:
        是否有权限
    """
    user = get_user_service(user_id)
    if not user:
        return False
    return user.get("role") == required_role


def batch_get_users_service(user_ids: List[str]) -> List[Dict]:
    """
    批量获取用户（需要重构：将 user_ids 参数名改为 uids）
    
    Args:
        user_ids: 用户ID列表
        
    Returns:
        用户信息列表
    """
    results = []
    for uid in user_ids:
        user = get_user_service(uid)
        if user:
            results.append(user)
    logger.info(f"Batch retrieved {len(results)} users")
    return results


def update_user_email_service(user_id: str, new_email: str) -> bool:
    """更新用户邮箱的业务逻辑"""
    from db import update_user
    if not user_id or not new_email:
        return False
    success = update_user(user_id, email=new_email)
    if success:
        logger.info(f"Updated email for user {user_id}")
    return success


def delete_user_service(user_id: str) -> bool:
    """删除用户的业务逻辑"""
    from db import delete_user
    user = get_user_service(user_id)
    if not user:
        return False
    success = delete_user(user_id)
    if success:
        logger.info(f"Deleted user {user_id}")
    return success


def get_user_statistics() -> Dict:
    """获取用户统计信息"""
    total = get_user_count()
    users = get_all_users()
    role_counts = {}
    for user in users:
        role = user.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
    
    return {
        "total_users": total,
        "role_distribution": role_counts
    }