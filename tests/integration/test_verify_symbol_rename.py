#!/usr/bin/env python3
"""
Tests for verify_symbol_rename — AST-based rename verification tool.
"""
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from core.tools.base_tools import BaseTools

passed = 0
failed = 0


def _test_result(name: str, success: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if success else "FAIL"
    if success:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))


def _test_section(name: str):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def _parse(result):
    """Parse JSON result from ToolResult."""
    return json.loads(result.content)


# ═══════════════════════════════════════════════════════════════
# Test: verify_symbol_rename
# ═══════════════════════════════════════════════════════════════
def test_rename_complete():
    """Rename fully done — old symbol absent, new symbol present."""
    _test_section("Rename Complete")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "def get_uid(user):\n"
            "    return user.uid\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=true", data["success"] is True)
        _test_result("remaining empty", len(data["remaining_identifiers"]) == 0)
        _test_result("syntax_ok", data["syntax_ok"] is True)


def test_rename_remaining():
    """Old symbol still appears as an identifier."""
    _test_section("Remaining Identifier")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "def get_user_id(user):\n"
            "    return user.user_id\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=false", data["success"] is False)
        _test_result("has remaining", len(data["remaining_identifiers"]) > 0)
        _test_result("syntax_ok", data["syntax_ok"] is True)


def test_rename_comment_ignored():
    """Old symbol in comments/docstrings should NOT be flagged."""
    _test_section("Comments/Docstrings Ignored")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "# user_id is the primary key — comment only\n"
            "# TODO: rename user_id to uid\n"
            "def get_uid(user):\n"
            '    """Return the user_id for this user."""\n'
            "    return user.uid\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=true (comments ignored)", data["success"] is True)
        _test_result("remaining empty", len(data["remaining_identifiers"]) == 0)
        _test_result("syntax_ok", data["syntax_ok"] is True)


def test_rename_string_literal_ignored():
    """Old symbol in string literals should NOT be flagged."""
    _test_section("String Literals Ignored")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            'WELCOME_MSG = "Welcome user_id! Welcome!"\n'
            "def get_uid(user):\n"
            "    return user.uid\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=true (strings ignored)", data["success"] is True)
        _test_result("remaining empty", len(data["remaining_identifiers"]) == 0)


def test_rename_syntax_error():
    """File with syntax error should report syntax_ok=false."""
    _test_section("Syntax Error")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "broken.py").write_text(
            "def foo():\n"
            "    print('hello'\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["broken.py"],
        )
        data = _parse(result)
        _test_result("success=false", data["success"] is False)
        _test_result("syntax_ok=false", data["syntax_ok"] is False)


def test_rename_multi_file():
    """Old symbol remains across multiple files."""
    _test_section("Multi-file Remaining")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "a.py").write_text("user_id = 1\n")
        (Path(tmpdir) / "b.py").write_text("x = user_id\n")

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=false", data["success"] is False)
        _test_result("2 remaining", len(data["remaining_identifiers"]) == 2)
        files = {r["file"] for r in data["remaining_identifiers"]}
        _test_result("both files flagged", len(files) == 2)


def test_rename_no_python():
    """No .py files in search path."""
    _test_section("No Python Files")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "readme.md").write_text("user_id: the id")

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        _test_result("not found", "没有找到" in result.content)


def test_rename_import_alias():
    """Old symbol appearing in import alias should be flagged."""
    _test_section("Import Alias")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "from module import user_id\n"
            "x = user_id\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=false (import flags)", data["success"] is False)
        symbols = {r["symbol"] for r in data["remaining_identifiers"]}
        _test_result("user_id in remaining", "user_id" in symbols)


def test_rename_completion_signal():
    """Success response includes confidence, task_complete_likely fields."""
    _test_section("Completion Signal Fields")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "uid = 42\n"
            "def get():\n"
            "    return uid\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("confidence=high", data.get("confidence") == "high")
        _test_result("task_complete_likely=true", data.get("task_complete_likely") is True)
        _test_result("message suggests stopping",
                     "unnecessary" in data.get("message", "").lower())


def test_rename_new_symbols_count():
    """New symbols should be counted and reported."""
    _test_section("New Symbols Counted")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "service.py").write_text(
            "uid = 42\n"
            "def get():\n"
            "    return uid\n"
        )

        result = tools.verify_symbol_rename(
            old_symbols=["user_id"], new_symbols=["uid"], paths=["."],
        )
        data = _parse(result)
        _test_result("success=true", data["success"] is True)
        _test_result("remaining empty", len(data["remaining_identifiers"]) == 0)
        _test_result("message mentions new symbols",
                     "location" in data["message"] or "locations" in data["message"])


# ═══════════════════════════════════════════════════════════════
# Run All Tests
# ═══════════════════════════════════════════════════════════════
def main():
    global passed, failed
    passed = 0
    failed = 0

    print("=" * 60)
    print("  verify_symbol_rename Tests")
    print("=" * 60)

    tests = [
        test_rename_complete,
        test_rename_remaining,
        test_rename_comment_ignored,
        test_rename_string_literal_ignored,
        test_rename_syntax_error,
        test_rename_multi_file,
        test_rename_no_python,
        test_rename_import_alias,
        test_rename_new_symbols_count,
        test_rename_completion_signal,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            import traceback
            _test_result(test.__name__, False, f"Exception: {e}")
            traceback.print_exc()

    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
