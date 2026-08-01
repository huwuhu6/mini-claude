#!/usr/bin/env python3
"""
验证脚本：检查 task_005_cross_file_drift 的修改是否正确

验证点：
1. db.py 中的 get_user_by_id 函数的 user_id 参数是否改为 uid
2. service.py 中所有相关函数的 user_id 参数是否改为 uid
3. controller.py 中所有相关函数的 user_id 参数是否改为 uid
4. 确保跨文件一致性：调用方传入的参数名与被调用方接收的参数名匹配
"""

import sys
import re
import ast

def check_file_syntax(filepath: str) -> bool:
    """检查文件语法是否正确"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            ast.parse(f.read())
        print(f"✅ {filepath} 语法正确")
        return True
    except SyntaxError as e:
        print(f"❌ {filepath} 语法错误: {e}")
        return False
    except FileNotFoundError:
        print(f"❌ {filepath} 文件不存在")
        return False


def check_param_name(filepath: str, target_params: list) -> dict:
    """
    检查文件中指定函数的参数名是否已修改
    
    Returns:
        dict: {"function_name": {"modified": bool, "old": str, "new": str}}
    """
    results = {}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 使用正则匹配函数定义行
        # 匹配 def func_name(param1, param2, ...):
        pattern = r'def\s+(\w+)\s*\((.*?)\):'
        
        for match in re.finditer(pattern, content, re.DOTALL):
            func_name = match.group(1)
            params_str = match.group(2)
            
            # 清理参数字符串（移除类型注解和默认值的影响）
            clean_params = re.sub(r':\s*[^=,)]+', '', params_str)
            clean_params = re.sub(r'=\s*[^,)]+', '', clean_params)
            param_names = [p.strip() for p in clean_params.split(',') if p.strip()]
            
            # 检查目标参数
            for target in target_params:
                if target in param_names:
                    results[func_name] = {
                        "modified": True,
                        "new": target,
                        "params": param_names
                    }
                elif target in [p.replace('_', '') for p in param_names]:
                    # 可能是部分匹配
                    results[func_name] = {
                        "modified": False,
                        "old": "user_id/user_ids",
                        "new": param_names,
                        "warning": True
                    }
        
        return results
    except Exception as e:
        print(f"检查 {filepath} 时出错: {e}")
        return {}


def test_cross_file_drift():
    """测试跨文件连续修改的一致性"""
    
    print("=" * 60)
    print("task_005_cross_file_drift - 跨文件修改一致性测试")
    print("=" * 60)
    
    errors = []
    successes = []
    
    # 1. 检查语法正确性
    print("\n--- 语法检查 ---")
    files = ['db.py', 'service.py', 'controller.py']
    for f in files:
        if not check_file_syntax(f):
            errors.append(f"{f} 语法错误")
    
    # 2. 检查各文件中的参数名修改
    print("\n--- 参数名检查 ---")
    
    # db.py: 应该将 user_id 改为 uid
    print("\n[db.py]")
    db_results = check_param_name('db.py', ['uid'])
    
    # 检查关键函数
    expected_db_functions = ['get_user_by_id']
    for func in expected_db_functions:
        if func in db_results:
            if db_results[func].get("modified"):
                print(f"  ✅ {func}: 参数已修改为 uid")
                successes.append(f"db.py:{func} 已修改")
            else:
                print(f"  ❌ {func}: 参数未修改，当前参数: {db_results[func].get('new', 'unknown')}")
                errors.append(f"db.py:{func} 参数未从 user_id 改为 uid")
        else:
            print(f"  ⚠️ {func}: 函数定义未找到或格式异常")
    
    # service.py: 应该将 user_id 改为 uid，user_ids 改为 uids
    print("\n[service.py]")
    service_results = check_param_name('service.py', ['uid', 'uids'])
    
    expected_service_functions = [
        'get_user_service',
        'get_user_profile', 
        'validate_user_permission',
        'batch_get_users_service'
    ]
    
    for func in expected_service_functions:
        if func in service_results:
            params = service_results[func].get('new', [])
            if 'uid' in params or 'uids' in params:
                print(f"  ✅ {func}: 参数已修改 (params: {params})")
                successes.append(f"service.py:{func} 已修改")
            else:
                print(f"  ❌ {func}: 参数未修改，当前参数: {params}")
                errors.append(f"service.py:{func} 参数未修改")
        else:
            print(f"  ⚠️ {func}: 函数定义未找到")
    
    # 检查 batch_get_users_service 的 uids
    if 'batch_get_users_service' in service_results:
        params = service_results['batch_get_users_service'].get('new', [])
        if 'uids' in params:
            print(f"  ✅ batch_get_users_service: 已使用 uids")
        else:
            print(f"  ❌ batch_get_users_service: 应使用 uids，当前参数: {params}")
            errors.append("batch_get_users_service 参数应改为 uids")
    
    # controller.py: 应该将 user_id 改为 uid，user_ids 改为 uids
    print("\n[controller.py]")
    controller_results = check_param_name('controller.py', ['uid', 'uids'])
    
    expected_controller_functions = [
        'get_user',
        'check_user_exists',
        'get_user_profile',
        'check_user_permission',
        'batch_get_users',
        'api_get_user',
        'api_check_user_exists',
        'api_get_user_profile',
        'api_batch_get_users'
    ]
    
    for func in expected_controller_functions:
        if func in controller_results:
            params = controller_results[func].get('new', [])
            if 'uid' in params or 'uids' in params:
                print(f"  ✅ {func}: 参数已修改 (params: {params})")
                successes.append(f"controller.py:{func} 已修改")
            else:
                print(f"  ❌ {func}: 参数未修改，当前参数: {params}")
                errors.append(f"controller.py:{func} 参数未修改")
        else:
            # api_* 函数可能是全局函数，不在类内
            if not func.startswith('api_'):
                print(f"  ⚠️ {func}: 函数定义未找到")
    
    # 3. 调用链验证（导入和执行测试）
    print("\n--- 调用链验证 ---")
    try:
        # 动态导入并测试
        sys.path.insert(0, '.')
        
        import db
        import service
        import controller
        
        # 测试 db 层：应该能用 uid 参数调用
        if hasattr(db, 'get_user_by_id'):
            # 检查函数签名
            import inspect
            sig = inspect.signature(db.get_user_by_id)
            params = list(sig.parameters.keys())
            
            if 'uid' in params:
                print(f"  ✅ db.get_user_by_id 签名正确: {params}")
                # 测试实际调用
                result = db.get_user_by_id(uid="user_001")
                if result and result.get("id") == "user_001":
                    print(f"  ✅ db.get_user_by_id 调用成功")
                else:
                    print(f"  ⚠️ db.get_user_by_id 调用返回异常")
            else:
                print(f"  ❌ db.get_user_by_id 签名错误，应为 uid，实际: {params}")
                errors.append("db.get_user_by_id 签名未正确修改")
        
        # 测试 service 层
        if hasattr(service, 'get_user_service'):
            sig = inspect.signature(service.get_user_service)
            params = list(sig.parameters.keys())
            if 'uid' in params:
                print(f"  ✅ service.get_user_service 签名正确: {params}")
                result = service.get_user_service(uid="user_001")
                if result and result.get("id") == "user_001":
                    print(f"  ✅ service.get_user_service 调用成功")
            else:
                print(f"  ❌ service.get_user_service 签名错误，应为 uid，实际: {params}")
                errors.append("service.get_user_service 签名未正确修改")
        
        # 测试 controller 层
        if hasattr(controller, 'api_get_user'):
            sig = inspect.signature(controller.api_get_user)
            params = list(sig.parameters.keys())
            if 'uid' in params:
                print(f"  ✅ controller.api_get_user 签名正确: {params}")
                result = controller.api_get_user(uid="user_001")
                if result.get("success"):
                    print(f"  ✅ controller.api_get_user 调用成功")
            else:
                print(f"  ❌ controller.api_get_user 签名错误，应为 uid，实际: {params}")
                errors.append("controller.api_get_user 签名未正确修改")
        
        # 测试批量接口
        if hasattr(service, 'batch_get_users_service'):
            sig = inspect.signature(service.batch_get_users_service)
            params = list(sig.parameters.keys())
            if 'uids' in params:
                print(f"  ✅ service.batch_get_users_service 签名正确: {params}")
                result = service.batch_get_users_service(uids=["user_001", "user_002"])
                if len(result) == 2:
                    print(f"  ✅ service.batch_get_users_service 调用成功")
            else:
                print(f"  ❌ service.batch_get_users_service 签名错误，应为 uids，实际: {params}")
                errors.append("service.batch_get_users_service 签名未正确修改")
                
    except Exception as e:
        print(f"  ⚠️ 调用链验证异常: {e}")
        errors.append(f"调用链验证失败: {e}")
    
    # 4. 最终判定
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    if errors:
        print(f"\n❌ [FAILED] 发现 {len(errors)} 个问题:")
        for err in errors:
            print(f"  - {err}")
        print("\n💡 提示：跨文件修改需要保证所有相关文件的参数名一致修改，")
        print("   包括函数定义、函数调用、以及文档字符串中的参数名。")
        return 1
    else:
        print(f"\n✅ [SUCCESS] 所有检查通过！({len(successes)} 处修改验证成功)")
        print("\n   跨文件连续修改测试通过：")
        print("   - db.py: get_user_by_id 参数 user_id → uid")
        print("   - service.py: 多个函数参数 user_id → uid, user_ids → uids")
        print("   - controller.py: 多个函数参数 user_id → uid, user_ids → uids")
        print("   - 调用链完整，参数传递正确")
        return 0


if __name__ == "__main__":
    sys.exit(test_cross_file_drift())