"""
controller.py - 控制器层（入口层）
依赖关系：调用 service.py
"""

import json
from typing import Dict, Any
from service import (
    get_user_service,
    get_all_users_service,
    user_exists_service,
    get_user_profile,
    validate_user_permission,
    batch_get_users_service,
    get_user_statistics
)


class UserController:
    """用户控制器 - 处理API请求"""
    
    def __init__(self):
        self.request_count = 0
        self.error_count = 0
    
    def get_user(self, user_id: str) -> Dict[str, Any]:
        """
        获取用户信息API（需要重构：将 user_id 参数名改为 uid）
        
        Args:
            user_id: 用户唯一标识符
            
        Returns:
            API响应格式
        """
        self.request_count += 1
        
        if not user_id:
            self.error_count += 1
            return {
                "success": False,
                "error": "user_id is required",
                "code": 400
            }
        
        user = get_user_service(user_id)
        
        if user:
            return {
                "success": True,
                "data": user,
                "code": 200
            }
        else:
            self.error_count += 1
            return {
                "success": False,
                "error": "User not found",
                "code": 404
            }
    
    def get_all_users(self) -> Dict[str, Any]:
        """获取所有用户API"""
        self.request_count += 1
        users = get_all_users_service()
        return {
            "success": True,
            "data": users,
            "count": len(users),
            "code": 200
        }
    
    def check_user_exists(self, user_id: str) -> Dict[str, Any]:
        """检查用户是否存在API（需要重构：将 user_id 参数名改为 uid）"""
        self.request_count += 1
        exists = user_exists_service(user_id)
        return {
            "success": True,
            "exists": exists,
            "code": 200
        }
    
    def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """获取用户资料API（需要重构：将 user_id 参数名改为 uid）"""
        self.request_count += 1
        profile = get_user_profile(user_id)
        
        if "error" in profile:
            self.error_count += 1
            return {
                "success": False,
                "error": profile["error"],
                "code": 404
            }
        
        return {
            "success": True,
            "data": profile,
            "code": 200
        }
    
    def check_user_permission(self, user_id: str, required_role: str) -> Dict[str, Any]:
        """检查用户权限API（需要重构：将 user_id 参数名改为 uid）"""
        self.request_count += 1
        has_permission = validate_user_permission(user_id, required_role)
        return {
            "success": True,
            "has_permission": has_permission,
            "code": 200
        }
    
    def batch_get_users(self, user_ids: list) -> Dict[str, Any]:
        """批量获取用户API（需要重构：将 user_ids 参数名改为 uids）"""
        self.request_count += 1
        users = batch_get_users_service(user_ids)
        return {
            "success": True,
            "data": users,
            "count": len(users),
            "code": 200
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """获取控制器统计信息"""
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / self.request_count if self.request_count > 0 else 0
        }
    
    def get_system_stats(self) -> Dict[str, Any]:
        """获取系统统计信息（包含用户统计）"""
        user_stats = get_user_statistics()
        controller_stats = self.get_stats()
        return {
            **controller_stats,
            "user_stats": user_stats
        }


# 全局控制器实例
_controller = UserController()


def api_get_user(user_id: str) -> Dict[str, Any]:
    """API入口：获取用户（需要重构：将 user_id 参数名改为 uid）"""
    return _controller.get_user(user_id)


def api_get_all_users() -> Dict[str, Any]:
    """API入口：获取所有用户"""
    return _controller.get_all_users()


def api_check_user_exists(user_id: str) -> Dict[str, Any]:
    """API入口：检查用户是否存在（需要重构：将 user_id 参数名改为 uid）"""
    return _controller.check_user_exists(user_id)


def api_get_user_profile(user_id: str) -> Dict[str, Any]:
    """API入口：获取用户资料（需要重构：将 user_id 参数名改为 uid）"""
    return _controller.get_user_profile(user_id)


def api_batch_get_users(user_ids: list) -> Dict[str, Any]:
    """API入口：批量获取用户（需要重构：将 user_ids 参数名改为 uids）"""
    return _controller.batch_get_users(user_ids)


def api_get_stats() -> Dict[str, Any]:
    """API入口：获取统计信息"""
    return _controller.get_system_stats()