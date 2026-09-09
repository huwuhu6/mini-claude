"""Progress tracker, blocker ledger, and bounded completion gate."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any, Dict, Iterable, Mapping, Optional

from .models import (
    BlockerLifecycle,
    BlockerRecord,
    GovernanceAction,
    GovernanceDecision,
    ProgressEvent,
    RecoveryStage,
)
from .normalizer import ObservationNormalizer


class BlockerLedger:
    """Keep blocker evidence alive across strategy changes and final answers."""

    _CATEGORIES = frozenset({
        "NETWORK_UNREACHABLE", "PACKAGE_NOT_FOUND", "PERMISSION_DENIED",
        "COMMAND_NOT_FOUND", "FILE_NOT_FOUND", "TIMEOUT", "OUT_OF_MEMORY",
        "DISK_FULL", "CAPABILITY_UNAVAILABLE",
    })

    def __init__(self) -> None:
        self._records: Dict[str, BlockerRecord] = {}

    @staticmethod
    def _key(category: str, root: str) -> str:
        return f"{category}::{root}"

    def record_failure(
        self, *, turn: int, category: str, observation_fingerprint: str,
        intent_key: str, strategy_fingerprint: str,
    ) -> Optional[BlockerRecord]:
        if not category or category not in self._CATEGORIES:
            return None
        root = hashlib.sha256(f"{category}:{observation_fingerprint}".encode()).hexdigest()[:16]
        key = self._key(category, root)
        item = self._records.get(key)
        if item is None:
            item = BlockerRecord(
                category=category, root_cause_fingerprint=root,
                first_seen=turn, last_seen=turn, related_intent=intent_key,
                observation_fingerprint=observation_fingerprint,
            )
            self._records[key] = item
        else:
            item.last_seen = turn
            item.evidence_count += 1
            item.lifecycle = BlockerLifecycle.OPEN
        if strategy_fingerprint and strategy_fingerprint not in item.strategy_attempts:
            item.strategy_attempts.append(strategy_fingerprint)
        return item

    @staticmethod
    def _related(item: BlockerRecord, intent_key: str, strategy: str) -> bool:
        if not intent_key:
            return False
        if intent_key == item.related_intent or (strategy and strategy in item.strategy_attempts):
            return True
        # Intent keys are intentionally generic ``tool:action:target`` values.
        # A shared target is useful evidence; an unrelated command is not.
        old_target = item.related_intent.rsplit(":", 1)[-1]
        new_target = intent_key.rsplit(":", 1)[-1]
        return bool(old_target and new_target and old_target == new_target)

    def observe_recovery(
        self, *, turn: int, intent_key: str, strategy_fingerprint: str,
        success: bool, progress_detected: bool,
        alternative_evidence: bool = False,
    ) -> None:
        if not success and not progress_detected:
            return
        for item in self._records.values():
            if item.lifecycle in {BlockerLifecycle.RESOLVED, BlockerLifecycle.TERMINAL}:
                continue
            if not self._related(item, intent_key, strategy_fingerprint) and not alternative_evidence:
                continue
            if progress_detected:
                marker = f"turn:{turn}"
                if marker not in item.progress_since_blocker:
                    item.progress_since_blocker.append(marker)
            # A successful command or a workspace mutation is not, by itself,
            # proof that the blocked business operation recovered.  Require
            # generic verification-shaped evidence for resolution; this keeps
            # reports, writes, and unrelated exit=0 activity as weak evidence.
            if success and progress_detected and alternative_evidence:
                item.lifecycle = BlockerLifecycle.RESOLVED
            elif progress_detected:
                item.lifecycle = BlockerLifecycle.MITIGATED

    def unresolved(self) -> list[BlockerRecord]:
        return [
            item for item in self._records.values()
            if item.lifecycle in {BlockerLifecycle.OPEN, BlockerLifecycle.MITIGATED}
        ]

    def all(self) -> list[BlockerRecord]:
        return list(self._records.values())

    def reset(self) -> None:
        self._records.clear()


class ProgressTracker:
    """Combine action, observation, state, failure, and history evidence."""

    def __init__(self, *, history_limit: int = 24, oscillation_cycles: int = 2) -> None:
        self.history_limit = history_limit
        self.oscillation_cycles = oscillation_cycles
        self.events: deque[ProgressEvent] = deque(maxlen=history_limit)
        self.blockers = BlockerLedger()
        self.no_progress_streak = 0
        self.replan_count = 0
        self._replan_pending = False

    @staticmethod
    def _digest(snapshot: Optional[Mapping[str, str] | str]) -> str:
        if snapshot is None:
            return ""
        if isinstance(snapshot, str):
            return snapshot
        payload = json.dumps(dict(sorted(snapshot.items())), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @staticmethod
    def _changed_paths(before: Optional[Mapping[str, str]], after: Optional[Mapping[str, str]]) -> tuple[str, ...]:
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            return ()
        return tuple(sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path)))

    @staticmethod
    def _has_substantive_success(text: str) -> bool:
        """Recognise generic verification-shaped output, not arbitrary success text."""
        normalized = ObservationNormalizer.normalize(text)
        if len(normalized) < 20:
            return False
        markers = (
            "pass", "passed", "ready", "healthy", "verified", "compiled",
            "artifact", "score", "format", "provider", "test result",
        )
        return any(marker in normalized for marker in markers)

    def reset(self) -> None:
        self.events.clear()
        self.blockers.reset()
        self.no_progress_streak = 0
        self.replan_count = 0
        self._replan_pending = False

    _SEMANTIC_STATE = re.compile(
        r"(?:^|[\s\[({,;])(?:observed|state|status|phase)\s*[:=]\s*"
        r"([A-Za-z][A-Za-z0-9_.-]*)\b", re.IGNORECASE,
    )
    _GENERIC_STATE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*)$")
    _CAPABILITY_UNAVAILABLE = re.compile(
        r"\b(?:unavailable|not available|does not exist|not supported|"
        r"unsupported capability|cannot be provided)\b", re.IGNORECASE,
    )

    @classmethod
    def _infer_blocker_category(cls, text: str) -> str:
        normalized = ObservationNormalizer.normalize(text)
        if cls._CAPABILITY_UNAVAILABLE.search(normalized):
            return "CAPABILITY_UNAVAILABLE"
        if re.search(r"\b(?:permission denied|access denied)\b", normalized):
            return "PERMISSION_DENIED"
        if re.search(r"\b(?:connection refused|network unreachable)\b", normalized):
            return "NETWORK_UNREACHABLE"
        if re.search(r"\b(?:command not found|not recognized as a command)\b", normalized):
            return "COMMAND_NOT_FOUND"
        return ""

    @classmethod
    def _semantic_state(cls, text: str) -> str:
        """Extract generic state tokens; otherwise use normalized output."""
        normalized = ObservationNormalizer.normalize(text)
        matches = [m.group(1).lower() for m in cls._SEMANTIC_STATE.finditer(normalized)]
        if matches:
            return "|".join(matches)
        match = cls._GENERIC_STATE.match(normalized.strip())
        return match.group(1).lower() if match else ObservationNormalizer.fingerprint(text)

    @staticmethod
    def _verification_improved(text: str) -> bool:
        """Recognise explicit generic validation improvement evidence."""
        normalized = ObservationNormalizer.normalize(text)
        return bool(re.search(
            r"\b(?:pass|passed|success|successful|ready|healthy|verified|"
            r"compiled|valid|0\s+failed|0\s+errors?)\b",
            normalized,
            re.IGNORECASE,
        ))

    def _oscillates(self, candidate: tuple[str, bool] | None = None) -> bool:
        values: list[tuple[str, bool]] = []
        for event in self.events:
            state = event.semantic_state or event.observation_fingerprint
            values.extend(
                (part, event.verification_improved)
                for part in state.split("|") if part
            )
        if candidate is not None:
            values.extend(
                (part, candidate[1])
                for part in candidate[0].split("|") if part
            )
        for period in (2, 3):
            needed = period * self.oscillation_cycles
            if len(values) < needed:
                continue
            tail = values[-needed:]
            left = [(e[0], e[1]) for e in tail[:period]]
            if not left or not all(
                [(e[0], e[1]) for e in tail[i:i + period]] == left
                for i in range(period, needed, period)
            ):
                continue
            states = [e[0] for e in tail]
            if len(set(states)) > 1 and not any(e[1] for e in tail):
                return True
        return False

    def observe(
        self, *, turn: int, tool_name: str, intent_key: str = "",
        strategy_fingerprint: str = "", success: bool = True,
        result_text: str = "", failure_category: str = "",
        blocker_category: str = "", workspace_before: Optional[Mapping[str, str] | str] = None,
        workspace_after: Optional[Mapping[str, str] | str] = None,
        workspace_root: str = "", external_state_changed: bool = False,
        command_blocked: bool = False,
    ) -> GovernanceDecision:
        observation = ObservationNormalizer.fingerprint(result_text, workspace_root)
        semantic_state = self._semantic_state(result_text)
        before_digest = self._digest(workspace_before)
        after_digest = self._digest(workspace_after)
        changed_paths = self._changed_paths(workspace_before, workspace_after)
        previous = self.events[-1] if self.events else None
        verification_improved = self._verification_improved(result_text)
        progress_reasons: list[str] = []
        stagnation_reasons: list[str] = []

        if previous is not None:
            observation_changed = observation != previous.observation_fingerprint
            failure_changed = failure_category != previous.failure_category
            same_intent = bool(intent_key and intent_key == previous.intent_key)
            if observation_changed:
                progress_reasons.append("OBSERVATION_CHANGED")
            if failure_changed and failure_category:
                progress_reasons.append("FAILURE_CHANGED")
            if external_state_changed:
                progress_reasons.append("EXTERNAL_STATE_CHANGED")
            # A workspace mutation is only evidence when semantic observation
            # or failure also changes.  This prevents diff-only fake progress.
            if changed_paths and (observation_changed or failure_changed):
                progress_reasons.append("WORKSPACE_CHANGED")
            if same_intent and observation_changed:
                progress_reasons.append("SAME_ACTION_NEW_OBSERVATION")
            if not progress_reasons:
                if not observation_changed:
                    stagnation_reasons.append("SAME_OBSERVATION")
                if failure_category and failure_category == previous.failure_category:
                    stagnation_reasons.append("SAME_FAILURE")
                if same_intent:
                    stagnation_reasons.append("REPEATED_INTENT")
                elif intent_key:
                    stagnation_reasons.append("STRATEGY_CHANGED_WITHOUT_PROGRESS")
        progress = bool(progress_reasons)
        if progress:
            self.no_progress_streak = 0
            self._replan_pending = False
        else:
            self.no_progress_streak += 1

        blocker_category = (
            blocker_category if blocker_category in self.blockers._CATEGORIES
            else failure_category if failure_category in self.blockers._CATEGORIES
            else self._infer_blocker_category(result_text)
        )
        if blocker_category:
            self.blockers.record_failure(
                turn=turn, category=blocker_category,
                observation_fingerprint=observation, intent_key=intent_key,
                strategy_fingerprint=strategy_fingerprint,
            )

        self.blockers.observe_recovery(
            turn=turn, intent_key=intent_key,
            strategy_fingerprint=strategy_fingerprint,
            success=success, progress_detected=progress,
            alternative_evidence=(
                success and progress and self._has_substantive_success(result_text)
            ),
        )
        oscillation = self._oscillates((semantic_state, verification_improved))
        if oscillation:
            stagnation_reasons.append("STATE_OSCILLATION")

        if oscillation or self.no_progress_streak >= 2:
            stage = RecoveryStage.SUSPECTED_STALL
        else:
            stage = RecoveryStage.HEALTHY
        action = GovernanceAction.ALLOW if progress or self.no_progress_streak < 2 else GovernanceAction.WARN
        reason = "progress evidence" if progress else ", ".join(stagnation_reasons) or "initial evidence"
        if oscillation:
            stage = RecoveryStage.TERMINAL
            action = GovernanceAction.TERMINATE
            reason = "SEMANTIC_OSCILLATION"
        elif self._replan_pending and self.no_progress_streak >= 2:
            stage = RecoveryStage.TERMINAL
            action = GovernanceAction.TERMINATE
            reason = "NO_PROGRESS_AFTER_REPLAN"
        elif self.no_progress_streak >= 3 and not self._replan_pending:
            stage = RecoveryStage.REPLAN_REQUIRED
            action = GovernanceAction.REPLAN
            self._replan_pending = True
            self.replan_count += 1
            reason = ", ".join(stagnation_reasons) or "NO_PROGRESS_AFTER_REPLAN"
        elif command_blocked:
            action = GovernanceAction.BLOCK_COMMAND
            reason = "deterministic preflight blocker"
        event = ProgressEvent(
            turn=turn, tool_name=tool_name, intent_key=intent_key,
            strategy_fingerprint=strategy_fingerprint, success=success,
            failure_category=failure_category, observation_fingerprint=observation,
            workspace_before_digest=before_digest, workspace_after_digest=after_digest,
            changed_paths=changed_paths, blocker_category=blocker_category,
            progress_detected=progress, progress_reason=tuple(progress_reasons),
            stagnation_reason=tuple(dict.fromkeys(stagnation_reasons)),
            recovery_stage=stage.value,
            open_blocker_count=len(self.blockers.unresolved()),
            oscillation_detected=oscillation,
            verification_improved=verification_improved,
            governance_decision=action.value,
            semantic_state=semantic_state,
        )
        self.events.append(event)
        return GovernanceDecision(
            action=action, reason=reason, progress_detected=progress,
            progress_reason=tuple(progress_reasons),
            stagnation_reason=tuple(dict.fromkeys(stagnation_reasons)),
            recovery_stage=stage, oscillation_detected=oscillation,
            open_blocker_count=len(self.blockers.unresolved()), event=event,
        )


class CompletionGuard:
    """Require evidence before accepting a final answer after a blocker."""

    def __init__(self, max_intercepts: int = 1) -> None:
        self.max_intercepts = max_intercepts
        self.intercepts = 0

    def reset(self) -> None:
        self.intercepts = 0

    def check(self, tracker: ProgressTracker) -> GovernanceDecision:
        unresolved = [
            item for item in tracker.blockers.unresolved()
            if item.evidence_count >= 2 or item.category == "CAPABILITY_UNAVAILABLE"
        ]
        if not unresolved:
            return GovernanceDecision(open_blocker_count=0)
        if self.intercepts < self.max_intercepts:
            self.intercepts += 1
            categories = ", ".join(sorted({item.category for item in unresolved}))
            return GovernanceDecision(
                action=GovernanceAction.REPLAN,
                reason="UNRESOLVED_BLOCKER",
                recovery_stage=RecoveryStage.REPLAN_REQUIRED,
                open_blocker_count=len(unresolved),
            )
        return GovernanceDecision(
            action=GovernanceAction.TERMINATE,
            reason="UNRESOLVED_BLOCKER_COMPLETION",
            recovery_stage=RecoveryStage.TERMINAL,
            open_blocker_count=len(unresolved),
        )

    @staticmethod
    def message(decision: GovernanceDecision) -> str:
        if decision.action is GovernanceAction.REPLAN:
            return (
                "[Progress Governance: completion verification required]\n"
                "此前存在尚未证明解决的环境或工具阻断。请不要仅用文字宣布完成；"
                "使用工具验证原目标，或明确报告该阻断及其影响。"
            )
        return (
            "[Progress Governance: completion blocked]\n"
            "仍存在未解决且未被验证恢复的阻断，不能接受 unsupported completion。"
            "请向用户报告真实阻断。"
        )
