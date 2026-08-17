"""A noisy, mostly passing test suite with one configuration failure."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import TIMEOUT


def _make_module_test(index: int):
    def test_module_initialization(self):
        self.assertGreater(index, 0)

    test_module_initialization.__name__ = f"test_module_initialization_{index:03d}"
    return test_module_initialization


class TestApplicationSuite(unittest.TestCase):
    """Many ordinary checks plus one failure hidden in a long log."""

    def test_timeout_matches_service_contract(self):
        self.assertTrue(TIMEOUT == 30, "service timeout contract violated")


for _index in range(1, 31):
    setattr(TestApplicationSuite, f"test_module_initialization_{_index:03d}",
            _make_module_test(_index))


def emit_initialization_log() -> None:
    for index in range(1, 2201):
        print(
            f"[module-init {index:04d}] initialized module_{(index % 37) + 1:02d} "
            f"phase={(index % 6) + 1} diagnostics=ok"
        )


if __name__ == "__main__":
    emit_initialization_log()
    result = unittest.main(module=__name__, verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)
