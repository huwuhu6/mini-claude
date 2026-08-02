"""Verify the scoped cross-file parameter rename for task_006."""

import ast
import importlib
import sys


EXPECTED = {
    "db.py": {
        "get_user_by_id": ("uid", "user_id"),
    },
    "service.py": {
        "get_user_service": ("uid", "user_id"),
        "get_user_profile": ("uid", "user_id"),
        "validate_user_permission": ("uid", "user_id"),
        "batch_get_users_service": ("uids", "user_ids"),
    },
    "controller.py": {
        "get_user": ("uid", "user_id"),
        "check_user_exists": ("uid", "user_id"),
        "get_user_profile": ("uid", "user_id"),
        "check_user_permission": ("uid", "user_id"),
        "batch_get_users": ("uids", "user_ids"),
        "api_get_user": ("uid", "user_id"),
        "api_check_user_exists": ("uid", "user_id"),
        "api_get_user_profile": ("uid", "user_id"),
        "api_batch_get_users": ("uids", "user_ids"),
    },
}


def load_tree(path):
    with open(path, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def check_syntax(path):
    try:
        load_tree(path)
        print(f"PASS syntax: {path}")
        return True
    except (OSError, SyntaxError) as exc:
        print(f"FAIL syntax: {path}: {exc}")
        return False


def check_signatures(path, expected):
    """Require exact renamed parameters and no old-name variable references."""
    try:
        tree = load_tree(path)
    except (OSError, SyntaxError) as exc:
        print(f"FAIL signatures: {path}: {exc}")
        return False

    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    passed = True

    for function_name, (new_name, old_name) in expected.items():
        node = functions.get(function_name)
        if node is None:
            print(f"FAIL {path}:{function_name}: function is missing")
            passed = False
            continue

        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        params = [arg.arg for arg in args]
        old_refs = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and child.id == old_name
        ]

        if new_name not in params or old_name in params:
            print(
                f"FAIL {path}:{function_name}: expected {new_name}, "
                f"got {params}"
            )
            passed = False
        elif old_refs:
            print(
                f"FAIL {path}:{function_name}: {old_name} is still used "
                f"in the function body"
            )
            passed = False
        else:
            print(f"PASS {path}:{function_name}: {params}")

    return passed


def check_call_chain():
    """Exercise representative calls after signature checks pass."""
    try:
        sys.path.insert(0, ".")
        for module_name in ("db", "service", "controller"):
            sys.modules.pop(module_name, None)
        db = importlib.import_module("db")
        service = importlib.import_module("service")
        controller = importlib.import_module("controller")

        assert db.get_user_by_id(uid="user_001")["id"] == "user_001"
        assert service.get_user_service(uid="user_001")["id"] == "user_001"
        assert service.get_user_profile(uid="user_001")["id"] == "user_001"
        assert service.validate_user_permission(uid="user_001", required_role="admin")
        assert len(service.batch_get_users_service(uids=["user_001", "user_002"])) == 2

        instance = controller.UserController()
        assert instance.get_user(uid="user_001")["success"]
        assert instance.check_user_exists(uid="user_001")["exists"]
        assert instance.get_user_profile(uid="user_001")["success"]
        assert instance.check_user_permission(uid="user_001", required_role="admin")["has_permission"]
        assert instance.batch_get_users(uids=["user_001", "user_002"])["count"] == 2
        assert controller.api_get_user(uid="user_001")["success"]
        assert controller.api_check_user_exists(uid="user_001")["exists"]
        assert controller.api_get_user_profile(uid="user_001")["success"]
        assert controller.api_batch_get_users(uids=["user_001", "user_002"])["count"] == 2
        print("PASS representative cross-file calls")
        return True
    except (AssertionError, AttributeError, ImportError, TypeError, KeyError) as exc:
        print(f"FAIL representative cross-file calls: {exc}")
        return False
    finally:
        if "." in sys.path:
            sys.path.remove(".")


def main():
    errors = []
    files = list(EXPECTED)

    for path in files:
        if not check_syntax(path):
            errors.append(f"syntax: {path}")
        if not check_signatures(path, EXPECTED[path]):
            errors.append(f"signatures: {path}")

    if not check_call_chain():
        errors.append("call chain")

    if errors:
        print(f"FAIL task_006_cross_file_drift: {len(errors)} check(s) failed")
        return 1

    print("PASS task_006_cross_file_drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
