"""Verify the requested division behavior without changing the ratio helper."""

import subprocess
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent


def main() -> int:
    result = subprocess.run(
        [sys.executable, "tests/test_math.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Tests failed:", result.stderr, file=sys.stderr)
        return 1

    sys.path.insert(0, str(WORKSPACE))
    from math_utils import calculate_ratio

    if calculate_ratio(10, 2) != 5.0:
        print("calculate_ratio no longer performs normal division", file=sys.stderr)
        return 1

    try:
        calculate_ratio(10, 0)
    except ZeroDivisionError:
        pass
    else:
        print("calculate_ratio was changed to handle division by zero", file=sys.stderr)
        return 1

    print("SUCCESS: divide_numbers was fixed and calculate_ratio was preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
