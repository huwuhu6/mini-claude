"""Independent verifier for the middle-log debugging task."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

from src.config import TIMEOUT


def main() -> int:
    if TIMEOUT != 30:
        print(f"TIMEOUT is still {TIMEOUT}; expected 30", file=sys.stderr)
        return 1

    env = {**os.environ, "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "tests/test_suite.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        print("test_suite.py did not pass", file=sys.stderr)
        print((result.stdout + result.stderr)[-4000:], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
