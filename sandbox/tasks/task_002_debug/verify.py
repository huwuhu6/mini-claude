#!/usr/bin/env python3
"""verify.py — task_002_debug: 验证 main.py 能正常打印通关信息。"""

from __future__ import annotations
import subprocess
import sys

if __name__ == "__main__":
    result = subprocess.run(
        [sys.executable or "python", "main.py"],
        capture_output=True,
        timeout=30,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")

    if "恭喜通关" in stdout and result.returncode == 0:
        print(f"[PASS] main.py 正常输出通关信息")
        sys.exit(0)
    else:
        print(f"[FAIL] stdout:\n{stdout}")
        sys.exit(1)
