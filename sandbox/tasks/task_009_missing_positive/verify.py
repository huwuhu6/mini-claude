import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if "missing_positive" in sys.modules:
    del sys.modules["missing_positive"]
from missing_positive import find_first_missing_positive

test_cases = [
    ([3, 4, -1, 1], 2),
    ([1, 2, 0], 3),
    ([7, 8, 9, 11, 12], 1),
    ([1, 2, 3], 4),
    ([], 1),
    ([-1, -2, 0], 1),
    ([1, 1, 1], 2),
    ([2, 1, 3, 4, 5], 6),
]

for nums, expected in test_cases:
    result = find_first_missing_positive(nums)
    if result != expected:
        print(f"[FAIL] find_first_missing_positive({nums}) = {result}, expected {expected}")
        sys.exit(1)

print("[PASS] 所有测试用例通过")
sys.exit(0)
