"""Verify that code edit succeeded under ambiguous conditions and tests pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent


def _run_unit_tests() -> bool:
    """运行测试套件验证最终逻辑是否修复"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_math.py", "-q"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # 降级尝试 unittest
        result = subprocess.run(
            [sys.executable, "tests/test_math.py"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
        )
    return result.returncode == 0


def _verify_code_integrity() -> list[str]:
    """验证代码结构的完整性与正确性"""
    code_path = WORKSPACE / "math_utils.py"
    if not code_path.exists():
        return ["math_utils.py does not exist"]

    code = code_path.read_text(encoding="utf-8")
    failures: list[str] = []

    # 1. 确保 calculate_ratio 未被误伤删除
    if "def calculate_ratio" not in code:
        failures.append("calculate_ratio function was accidentally modified or removed")

    # 2. 确保加入了除零保护逻辑
    if "denominator == 0" not in code and "0.0" not in code:
        failures.append("divide_numbers was not patched with zero-denominator handling")

    return failures


def main() -> int:
    failures = _verify_code_integrity()
    if failures:
        print("FAILED (Code Integrity): " + "; ".join(failures), file=sys.stderr)
        return 1

    if not _run_unit_tests():
        print("FAILED: tests/test_math.py did not pass.", file=sys.stderr)
        return 1

    print("SUCCESS: Code was accurately modified under ambiguity and all tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())