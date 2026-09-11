"""Compatibility view over the runtime AttemptHistory."""
from __future__ import annotations

from typing import Dict, Optional

from core.loop_controller import AttemptHistory, AttemptStatus


class FailureMemory:
    """Deprecated read view; recurrence is derived from AttemptHistory."""

    def __init__(self, history: Optional[AttemptHistory] = None):
        self.history = history if history is not None else AttemptHistory()
        self._current_task = ""

    def set_task(self, task_id: str) -> None:
        self._current_task = task_id

    def record(self, category: str, strategy_fp: str = "") -> None:
        # Agent runtime records each completed attempt directly. This method
        # only serves old standalone callers with the same event stream API.
        if self._current_task:
            self.history.record(
                turn=0, tool_name=f"legacy:{self._current_task}", intent_key=f"legacy:{category}",
                args_fingerprint="", status=AttemptStatus.FAILURE,
                failure_category=category, strategy_fingerprint=strategy_fp,
            )

    def _events(self):
        return [event for event in self.history
                if event.failure_category
                and event.tool_name == f"legacy:{self._current_task}"]

    def get_category_count(self, category: str) -> int:
        return sum(event.failure_category == category for event in self._events())

    def get_strategy_diversity(self, category: str) -> int:
        return len({event.strategy_fingerprint for event in self._events()
                    if event.failure_category == category and event.strategy_fingerprint})

    def get_all_categories(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self._events():
            counts[event.failure_category] = counts.get(event.failure_category, 0) + 1
        return counts

    def reset(self) -> None:
        self.history.reset()
        self._current_task = ""
