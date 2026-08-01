"""
verify.py - 验证 task_012_repo_context 的接口迁移是否正确

验证点：
1. 所有文件语法正确
2. 没有 LocalCache 的残留引用
3. 所有 service 和 main.py 正确导入 RedisClient
4. 所有 set() 调用都包含 ttl=300
5. 功能测试：运行 main.py 验证逻辑正确
"""

import ast
import os
import re
import sys


# 切换到脚本所在目录，确保 import 正确
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def check_syntax(filepath: str) -> bool:
    """检查 Python 文件语法"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        print(f"  ✅ {filepath} — 语法正确")
        return True
    except SyntaxError as e:
        print(f"  ❌ {filepath} — 语法错误: {e}")
        return False
    except FileNotFoundError:
        print(f"  ❌ {filepath} — 文件不存在")
        return False


def check_no_local_cache(filepath: str) -> bool:
    """检查文件中是否还有 LocalCache 的残留引用"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if "LocalCache" in content:
            print(f"  ❌ {filepath} — 仍包含 LocalCache 引用")
            return False
        if "local_cache" in content:
            print(f"  ❌ {filepath} — 仍引用 local_cache 模块")
            return False
        print(f"  ✅ {filepath} — 无 LocalCache 残留")
        return True
    except FileNotFoundError:
        print(f"  ❌ {filepath} — 文件不存在")
        return False


def check_redis_client_import(filepath: str) -> bool:
    """检查文件是否从 cache.redis_client 导入了 RedisClient"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        # 匹配 from cache.redis_client import RedisClient
        if re.search(r"from\s+cache\.redis_client\s+import\s+.*RedisClient", content):
            print(f"  ✅ {filepath} — 正确导入 RedisClient")
            return True
        # 也允许 from cache.redis_client import ... 这种形式
        if "cache.redis_client" in content and "RedisClient" in content:
            print(f"  ✅ {filepath} — 引用 RedisClient")
            return True
        print(f"  ❌ {filepath} — 未从 cache.redis_client 导入 RedisClient")
        return False
    except FileNotFoundError:
        print(f"  ❌ {filepath} — 文件不存在")
        return False


def check_ttl_in_set_calls(filepath: str) -> bool:
    """检查文件中所有 set() 调用是否都包含 ttl=300（使用 AST 精确解析）"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        has_set = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set":
                has_set = True
                has_ttl = any(
                    kw.arg == "ttl"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == 300
                    for kw in node.keywords
                )
                if not has_ttl:
                    print(f"  ❌ {filepath} — set() 调用缺少 ttl=300")
                    return False

        if not has_set:
            print(f"  ⏭️ {filepath} — 无 set() 调用")
            return True

        print(f"  ✅ {filepath} — 所有 set() 调用均包含 ttl=300")
        return True
    except FileNotFoundError:
        print(f"  ❌ {filepath} — 文件不存在")
        return False


def check_main_uses_redis() -> bool:
    """检查 main.py 是否使用 RedisClient"""
    try:
        with open("main.py", "r", encoding="utf-8") as f:
            content = f.read()
        if "RedisClient()" not in content:
            print(f"  ❌ main.py — 未使用 RedisClient() 初始化")
            return False
        print(f"  ✅ main.py — 使用 RedisClient() 初始化")
        return True
    except FileNotFoundError:
        print(f"  ❌ main.py — 文件不存在")
        return False


def run_functional_test() -> bool:
    """运行功能测试，验证迁移后的代码逻辑正确"""
    try:
        # 动态导入修改后的 main.py
        import importlib.util

        spec = importlib.util.spec_from_file_location("main", "main.py")
        main_module = importlib.util.module_from_spec(spec)

        # 先清除可能缓存的旧模块
        for mod_name in list(sys.modules.keys()):
            if "cache" in mod_name or "services" in mod_name or mod_name == "main":
                del sys.modules[mod_name]

        spec.loader.exec_module(main_module)
        app = main_module.create_app()

        user = app["user"]
        product = app["product"]
        order = app["order"]

        # Test 1: UserService — get_user_name 首次调用创建默认值并缓存
        name1 = user.get_user_name("001")
        assert name1 == "User_001", f"首次调用应创建默认值 User_001，实际为 {name1}"
        name2 = user.get_user_name("001")
        assert name2 == "User_001", f"缓存命中应返回 User_001，实际为 {name2}"
        user.set_user_profile("002", "Alice")
        # set_user_profile 使用不同的 key，不影响 get_user_name
        print("  ✅ UserService — get/set 正常工作")

        # Test 2: ProductService — get_product_price 首次调用创建默认值并缓存
        price1 = product.get_product_price("P001")
        assert price1 == 99.9, f"商品价格应为 99.9，实际为 {price1}"
        price2 = product.get_product_price("P001")
        assert price2 == 99.9, f"缓存命中应返回 99.9，实际为 {price2}"
        product.set_product_stock("P001", 100)
        print("  ✅ ProductService — get/set 正常工作")

        # Test 3: OrderService — update_order_status 修改后读取新状态
        status1 = order.get_order_status("O001")
        assert status1 == "pending", f"订单状态应为 pending，实际为 {status1}"
        order.update_order_status("O001", "shipped")
        status2 = order.get_order_status("O001")
        assert status2 == "shipped", f"订单状态应为 shipped，实际为 {status2}"
        print("  ✅ OrderService — get/set/update 正常工作")

        # Test 4: 共享缓存验证（所有 service 共享同一个 RedisClient 实例）
        user.set_user_profile("shared", "test_value")
        # product 和 user 共享同一个 cache，但 key 不同，这里不做跨 service 读取
        print("  ✅ 功能测试全部通过")
        return True

    except Exception as e:
        print(f"  ❌ 功能测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    all_ok = True
    files = [
        "main.py",
        "services/user_service.py",
        "services/product_service.py",
        "services/order_service.py",
        "cache/local_cache.py",
        "cache/redis_client.py",
    ]
    service_files = [
        "services/user_service.py",
        "services/product_service.py",
        "services/order_service.py",
    ]

    print("=" * 50)
    print("1️⃣  检查语法正确性")
    print("=" * 50)
    for f in files:
        if not check_syntax(f):
            all_ok = False

    print("\n" + "=" * 50)
    print("2️⃣  检查无 LocalCache 残留")
    print("=" * 50)
    for f in ["main.py"] + service_files:
        if not check_no_local_cache(f):
            all_ok = False

    print("\n" + "=" * 50)
    print("3️⃣  检查 RedisClient 导入")
    print("=" * 50)
    for f in ["main.py"] + service_files:
        if not check_redis_client_import(f):
            all_ok = False

    print("\n" + "=" * 50)
    print("4️⃣  检查 set() 调用包含 ttl=300")
    print("=" * 50)
    for f in service_files:
        if not check_ttl_in_set_calls(f):
            all_ok = False

    print("\n" + "=" * 50)
    print("5️⃣  检查 main.py 使用 RedisClient")
    print("=" * 50)
    if not check_main_uses_redis():
        all_ok = False

    print("\n" + "=" * 50)
    print("6️⃣  运行功能测试")
    print("=" * 50)
    if not run_functional_test():
        all_ok = False

    print("\n" + "=" * 50)
    if all_ok:
        print("🎉 全部验证通过！接口迁移成功！")
        print("=" * 50)
        sys.exit(0)
    else:
        print("❌ 验证失败，请检查修改")
        print("=" * 50)
        sys.exit(1)


if __name__ == "__main__":
    main()
