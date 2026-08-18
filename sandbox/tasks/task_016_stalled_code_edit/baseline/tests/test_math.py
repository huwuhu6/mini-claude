import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from math_utils import divide_numbers


class TestMathUtils(unittest.TestCase):
    def test_divide_by_zero(self):
        self.assertEqual(divide_numbers(10, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
