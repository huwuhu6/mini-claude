"""
FailureMemory — Per-task tracking of failure categories and strategies.

Provides:
    - Category-frequency counting (how many times each category occurred)
    - Strategy diversity tracking (how many different strategies were tried per category)
    - Task-level isolation

Thread-safety note: currently single-threaded (agent loop runs in one thread).
"""
from __future__ import annotations
from typing import Dict, Set


class FailureMemory:
    """Per-task memory of failure events and strategy diversity."""

    def __init__(self):
        # {task_id: {category: count}}
        self._categories: Dict[str, Dict[str, int]] = {}
        # {task_id: {category: set<strategy_fp>}}
        self._strategies: Dict[str, Dict[str, Set[str]]] = {}
        self._current_task: str = ""

    def set_task(self, task_id: str) -> None:
        """Switch to a new task context. Resets counters for that task if new."""
        self._current_task = task_id
        if task_id not in self._categories:
            self._categories[task_id] = {}
            self._strategies[task_id] = {}

    def record(self, category: str, strategy_fp: str = "") -> None:
        """Record one occurrence of a failure in the current task."""
        tid = self._current_task
        if not tid:
            return
        if category not in self._categories[tid]:
            self._categories[tid][category] = 0
        self._categories[tid][category] += 1

        if strategy_fp and tid in self._strategies:
            if category not in self._strategies[tid]:
                self._strategies[tid][category] = set()
            self._strategies[tid][category].add(strategy_fp)

    def get_category_count(self, category: str) -> int:
        """Number of failures of a given category in the current task."""
        tid = self._current_task
        if tid not in self._categories:
            return 0
        return self._categories[tid].get(category, 0)

    def get_strategy_diversity(self, category: str) -> int:
        """Number of distinct strategy fingerprints tried for a failure category."""
        tid = self._current_task
        if tid not in self._strategies:
            return 0
        return len(self._strategies[tid].get(category, set()))

    def get_all_categories(self) -> Dict[str, int]:
        """Get all category counts for the current task."""
        return dict(self._categories.get(self._current_task, {}))

    def reset(self) -> None:
        """Clear all memory."""
        self._categories.clear()
        self._strategies.clear()
        self._current_task = ""
