"""Task-local evidence for actions before the first workspace mutation.

This module is deliberately observational: it never makes runtime decisions.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable


class MutationRequirement(str, Enum):
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNKNOWN = "UNKNOWN"


SHADOW_THRESHOLDS = (8, 10, 12)
SHADOW_MIN_MODEL_TURNS = 3

_MUTATION_VERBS = r"(?:implement|fix|modify|refactor|create|add|update)"
_DIRECT_MUTATION = re.compile(
    rf"^\s*(?:(?:please|kindly)\s+)?{_MUTATION_VERBS}\b|"
    rf"\b(?:please|could you|can you|i want you to|i need you to)\s+{_MUTATION_VERBS}\b|"
    rf"\band\s+(?:please\s+)?{_MUTATION_VERBS}\b",
    re.IGNORECASE,
)
_DIRECT_READ_ONLY = re.compile(
    r"^\s*(?:(?:please|kindly)\s+)?"
    r"(?:explain|analyze|analyse|review|find|summarize|summarise|inspect|audit|"
    r"give advice|advise)\b|"
    r"\b(?:please|could you|can you|i want you to)\s+"
    r"(?:explain|analyze|analyse|review|find|summarize|summarise|inspect|audit)\b",
    re.IGNORECASE,
)
_DIRECT_MUTATION_ZH = re.compile(
    r"(?:^|[。！？\n])\s*(?:请)?(?:帮我)?(?:实现|修复|修改|重构|创建|新增|添加|更新)"
    r"|并(?:且)?(?:请)?(?:帮我)?(?:实现|修复|修改|重构|创建|新增|添加|更新)"
    r"|请(?:帮我)?(?:基于[^。！？\n]{0,50})?完成(?:一次|一个)?[^。！？\n]{0,25}"
    r"(?:修复|重构|实现|修改|创建|新增|添加|更新)"
)
_DIRECT_READ_ONLY_ZH = re.compile(
    r"(?:^|[。！？\n])\s*(?:请)?(?:帮我)?(?:解释|分析|审查|检查|查找|总结|概括|给出建议|评估)"
)
_NON_ACTION_TOOLS = frozenset({"todowrite", "update_agent_note"})


def classify_mutation_requirement(user_prompt: str) -> MutationRequirement:
    """Classify only clear direct workspace-change or read-only requests."""
    prompt = str(user_prompt or "").strip()
    if not prompt:
        return MutationRequirement.UNKNOWN

    if _DIRECT_MUTATION.search(prompt) or _DIRECT_MUTATION_ZH.search(prompt):
        return MutationRequirement.REQUIRED
    if _DIRECT_READ_ONLY.search(prompt) or _DIRECT_READ_ONLY_ZH.search(prompt):
        return MutationRequirement.NOT_REQUIRED
    return MutationRequirement.UNKNOWN


class PreFirstMutationShadow:
    """Count executed model-selected actions until the first workspace diff."""

    def __init__(
        self,
        requirement: MutationRequirement | str,
        *,
        thresholds: Iterable[int] = SHADOW_THRESHOLDS,
        min_model_turns: int = SHADOW_MIN_MODEL_TURNS,
    ) -> None:
        self.requirement = MutationRequirement(requirement)
        self.thresholds = tuple(sorted(set(int(value) for value in thresholds)))
        self.min_model_turns = int(min_model_turns)
        self.pre_mutation_action_count = 0
        self._model_turns: set[int] = set()
        self.first_workspace_mutation_seen = False
        self.first_workspace_mutation_turn: int | None = None
        self.first_workspace_mutation_action_index: int | None = None
        self.first_workspace_changed_paths: tuple[str, ...] = ()
        self.todo_write_count = 0
        self._triggers = {
            threshold: {
                "would_trigger": False,
                "trigger_turn": None,
                "trigger_action_index": None,
            }
            for threshold in self.thresholds
        }

    @property
    def pre_mutation_model_turn_count(self) -> int:
        return len(self._model_turns)

    def observe_model_turn(self, turn: int) -> dict[str, Any]:
        """Record an existing _llm_tool_cycle iteration before its response."""
        if (
            self.requirement is MutationRequirement.REQUIRED
            and not self.first_workspace_mutation_seen
        ):
            self._model_turns.add(int(turn))
        return self.snapshot()

    def observe_tool_action(
        self,
        *,
        tool_name: str,
        turn: int,
        executed: bool,
        changed_paths: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Record executed actions and any workspace diff observed after a tool."""
        normalized_name = str(tool_name or "").casefold()
        if self.requirement is not MutationRequirement.REQUIRED:
            return self.snapshot()
        if self.first_workspace_mutation_seen:
            return self.snapshot()

        changed = tuple(sorted({str(path) for path in changed_paths if str(path)}))
        if changed:
            self.first_workspace_mutation_seen = True
            self.first_workspace_mutation_turn = int(turn)
            self.first_workspace_mutation_action_index = self.pre_mutation_action_count + 1
            self.first_workspace_changed_paths = changed
            return self.snapshot()
        if not executed:
            return self.snapshot()
        if normalized_name == "todowrite":
            self.todo_write_count += 1
            return self.snapshot()
        if normalized_name in _NON_ACTION_TOOLS:
            return self.snapshot()

        self.pre_mutation_action_count += 1
        self._model_turns.add(int(turn))
        if self.pre_mutation_model_turn_count < self.min_model_turns:
            return self.snapshot()

        for threshold, trigger in self._triggers.items():
            if not trigger["would_trigger"] and self.pre_mutation_action_count >= threshold:
                trigger["would_trigger"] = True
                trigger["trigger_turn"] = int(turn)
                trigger["trigger_action_index"] = self.pre_mutation_action_count
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mutation_requirement": self.requirement.value,
            "pre_mutation_action_count": self.pre_mutation_action_count,
            "pre_mutation_model_turn_count": self.pre_mutation_model_turn_count,
            "first_workspace_mutation_seen": self.first_workspace_mutation_seen,
            "first_workspace_mutation_turn": self.first_workspace_mutation_turn,
            "first_workspace_mutation_action_index": self.first_workspace_mutation_action_index,
            "first_workspace_changed_paths": list(self.first_workspace_changed_paths),
            "todo_write_count": self.todo_write_count,
        }
        for threshold, trigger in self._triggers.items():
            data[f"threshold_{threshold}"] = dict(trigger)
        return data
