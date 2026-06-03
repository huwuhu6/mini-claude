#!/usr/bin/env python3
"""
Tests for lightweight verification tools: count_occurrences and syntax_check.
"""
import sys
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


# ═══════════════════════════════════════════════════════════════
# Test: count_occurrences
# ═══════════════════════════════════════════════════════════════
def test_count_occurrences():
    _test_section("count_occurrences")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))

        # Create test files
        (Path(tmpdir) / "main.py").write_text(
            "user_id = 42\n"
            "name = 'alice'\n"
            "user_ids = [1, 2, 3]\n"
            "uid = user_id\n"
        )
        (Path(tmpdir) / "utils.py").write_text(
            "def get_user_id():\n"
            "    return current_user.id\n"
        )
        (Path(tmpdir) / "readme.md").write_text(
            "user_id is the primary key\n"
        )

        # Test 1: Single pattern, multiple files
        result = tools.count_occurrences(
            paths=["."], patterns=["user_id"],
        )
        _test_result(
            "single pattern multi-file",
            result.success and '"user_id": 3 matches' in result.content,
        )

        # Test 2: No matches
        result = tools.count_occurrences(
            paths=["."], patterns=["XYZZZZ_NOTFOUND"],
        )
        _test_result(
            "no matches",
            '"XYZZZZ_NOTFOUND": 0 matches' in result.content,
        )

        # Test 3: Multiple patterns
        result = tools.count_occurrences(
            paths=["."], patterns=["user_id", "name"],
        )
        _test_result(
            "multiple patterns",
            result.success
            and '"user_id"' in result.content
            and '"name"' in result.content,
        )

        # Test 4: Case sensitivity
        result = tools.count_occurrences(
            paths=["main.py"], patterns=["USER"],
            case_sensitive=True,
        )
        _test_result(
            "case sensitive no match",
            '"USER": 0 matches' in result.content,
        )

        # Test 5: Case insensitive
        result = tools.count_occurrences(
            paths=["main.py"], patterns=["USER"],
            case_sensitive=False,
        )
        _test_result(
            "case insensitive match",
            '"USER":' in result.content,
        )

        # Test 6: Ignores .git directory
        git_dir = Path(tmpdir) / ".git"
        git_dir.mkdir()
        (git_dir / "config.py").write_text("user_id = 999")
        result = tools.count_occurrences(
            paths=["."], patterns=["user_id"],
        )
        # The .git/config.py should NOT be counted
        _test_result(
            "ignores .git directory",
            "config.py" not in result.content,
        )

        # Test 7: Glob pattern
        result = tools.count_occurrences(
            paths=["*.py"], patterns=["user_id"],
        )
        _test_result(
            "glob pattern",
            result.success and "main.py" in result.content,
        )

        # Test 8: Per-file breakdown
        result = tools.count_occurrences(
            paths=["main.py"], patterns=["user_id"],
        )
        _test_result(
            "per-file breakdown",
            result.success
            and "main.py" in result.content
            and '"user_id": 2 matches' in result.content,
        )


# ═══════════════════════════════════════════════════════════════
# Test: syntax_check (commented out — tool removed, LLM uses
# language-native tools like ast.parse / javac / tsc / go vet)
# ═══════════════════════════════════════════════════════════════
"""
def test_syntax_check():
    _test_section("syntax_check")

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "good.py").write_text("def hello():\\n    print('hello')\\n")
        (Path(tmpdir) / "also_good.py").write_text("x = 1\\nif x > 0:\\n    print(x)\\n")
        (Path(tmpdir) / "data.json").write_text('{"key": "value"}')

        result = tools.syntax_check(paths=["."])
        _test_result("all files valid", result.success and "0 errors" in result.content)

        result = tools.syntax_check(paths=["good.py"])
        _test_result("single file valid", result.success and "1 file" in result.content or "1 file(s)" in result.content)

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "broken.py").write_text("def foo():\\n    print('hello'\\n")
        result = tools.syntax_check(paths=["broken.py"])
        _test_result("syntax error detected", not result.success and "error" in result.content.lower())

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "good.py").write_text("x = 1\\n")
        (Path(tmpdir) / "broken.py").write_text("if True\\n    pass\\n")
        result = tools.syntax_check(paths=["."])
        _test_result("mixed valid/invalid", not result.success and "broken.py" in result.content)

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "empty.py").write_text("")
        result = tools.syntax_check(paths=["empty.py"])
        _test_result("empty file valid", result.success)

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))
        (Path(tmpdir) / "data.json").write_text("{}")
        result = tools.syntax_check(paths=["."])
        _test_result("no python files", not result.success and "没有找到" in result.content)
"""


# ═══════════════════════════════════════════════════════════════
# Run All Tests
# ═══════════════════════════════════════════════════════════════
def main():
    global passed, failed
    passed = 0
    failed = 0

    print("=" * 60)
    print("  Verification Tools Tests")
    print("=" * 60)

    tests = [
        test_count_occurrences,
        # test_syntax_check,  # removed — syntax_check tool has been deactivated
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
