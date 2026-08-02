"""Verify the LocalCache -> RedisClient migration for task_012."""

import ast
import importlib.util
import os
import sys


ROOT_FILES = [
    "main.py",
    "services/user_service.py",
    "services/product_service.py",
    "services/order_service.py",
    "cache/local_cache.py",
    "cache/redis_client.py",
]
SERVICE_FILES = ROOT_FILES[1:4]


def parse_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def check_syntax(path):
    try:
        parse_file(path)
        print(f"PASS syntax: {path}")
        return True
    except (OSError, SyntaxError) as exc:
        print(f"FAIL syntax: {path}: {exc}")
        return False


def check_no_local_cache(path):
    """Check executable references, ignoring comments and docstrings."""
    try:
        tree = parse_file(path)
    except (OSError, SyntaxError) as exc:
        print(f"FAIL old-cache check: {path}: {exc}")
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "cache.local_cache":
            print(f"FAIL old-cache check: {path} imports cache.local_cache")
            return False
        if isinstance(node, ast.Import):
            if any(alias.name == "cache.local_cache" for alias in node.names):
                print(f"FAIL old-cache check: {path} imports cache.local_cache")
                return False
        if isinstance(node, ast.Name) and node.id == "LocalCache":
            print(f"FAIL old-cache check: {path} references LocalCache")
            return False

    print(f"PASS old-cache check: {path}")
    return True


def check_redis_import(path):
    try:
        tree = parse_file(path)
    except (OSError, SyntaxError) as exc:
        print(f"FAIL RedisClient import: {path}: {exc}")
        return False

    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "cache.redis_client"
        and any(alias.name == "RedisClient" for alias in node.names)
        for node in ast.walk(tree)
    )
    if not imported:
        print(f"FAIL RedisClient import: {path}")
        return False
    print(f"PASS RedisClient import: {path}")
    return True


def check_ttl(path):
    try:
        tree = parse_file(path)
    except (OSError, SyntaxError) as exc:
        print(f"FAIL ttl check: {path}: {exc}")
        return False

    set_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set"
    ]
    for node in set_calls:
        has_ttl = any(
            keyword.arg == "ttl"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == 300
            for keyword in node.keywords
        )
        if not has_ttl:
            print(f"FAIL ttl check: {path} has set() without ttl=300")
            return False

    print(f"PASS ttl check: {path}: {len(set_calls)} set call(s)")
    return True


def check_main_constructor():
    try:
        tree = parse_file("main.py")
    except (OSError, SyntaxError) as exc:
        print(f"FAIL main constructor: {exc}")
        return False

    used = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RedisClient"
        for node in ast.walk(tree)
    )
    if not used:
        print("FAIL main constructor: RedisClient() is not called")
        return False
    print("PASS main constructor: RedisClient()")
    return True


def run_functional_test():
    try:
        spec = importlib.util.spec_from_file_location("task012_main", "main.py")
        module = importlib.util.module_from_spec(spec)
        for name in list(sys.modules):
            if name == "main" or name.startswith(("cache", "services")):
                sys.modules.pop(name, None)
        spec.loader.exec_module(module)
        app = module.create_app()

        user = app["user"]
        product = app["product"]
        order = app["order"]
        assert user.get_user_name("001") == "User_001"
        assert user.get_user_name("001") == "User_001"
        user.set_user_profile("002", "Alice")
        assert product.get_product_price("P001") == 99.9
        product.set_product_stock("P001", 100)
        assert order.get_order_status("O001") == "pending"
        order.update_order_status("O001", "shipped")
        assert order.get_order_status("O001") == "shipped"
        print("PASS functional behavior")
        return True
    except (OSError, AssertionError, AttributeError, ImportError, TypeError, KeyError) as exc:
        print(f"FAIL functional behavior: {exc}")
        return False


def main():
    failures = []
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    for path in ROOT_FILES:
        if not check_syntax(path):
            failures.append(f"syntax:{path}")
    for path in ["main.py"] + SERVICE_FILES:
        if not check_no_local_cache(path):
            failures.append(f"old-cache:{path}")
        if not check_redis_import(path):
            failures.append(f"redis-import:{path}")
    for path in SERVICE_FILES:
        if not check_ttl(path):
            failures.append(f"ttl:{path}")
    if not check_main_constructor():
        failures.append("main-constructor")
    if not run_functional_test():
        failures.append("functional")

    if failures:
        print(f"FAIL task_012_repo_context: {len(failures)} check(s) failed")
        return 1
    print("PASS task_012_repo_context")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
