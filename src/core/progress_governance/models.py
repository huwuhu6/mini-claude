"""Small, serialisable models used by progress governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Tuple


class RecoveryStage(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECTED_STALL = "SUSPECTED_STALL"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    TERMINAL = "TERMINAL"


class GovernanceAction(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    REPLAN = "REPLAN"
    BLOCK_COMMAND = "BLOCK_COMMAND"
    TERMINATE = "TERMINATE"


class BlockerLifecycle(str, Enum):
    OPEN = "OPEN"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class ProgressEvent:
    turn: int
    tool_name: str
    intent_key: str
    strategy_fingerprint: str
    success: bool
    failure_category: str
    observation_fingerprint: str
    workspace_before_digest: str
    workspace_after_digest: str
    changed_paths: Tuple[str, ...] = ()
    blocker_category: str = ""
    progress_detected: bool = False
    progress_reason: Tuple[str, ...] = ()
    stagnation_reason: Tuple[str, ...] = ()
    recovery_stage: str = RecoveryStage.HEALTHY.value
    open_blocker_count: int = 0
    oscillation_detected: bool = False
    verification_improved: bool = False
    governance_decision: str = GovernanceAction.ALLOW.value
    semantic_state: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn": self.turn,
            "tool_name": self.tool_name,
            "intent_key": self.intent_key,
            "strategy_fingerprint": self.strategy_fingerprint,
            "success": self.success,
            "failure_category": self.failure_category,
            "observation_fingerprint": self.observation_fingerprint,
            "workspace_before_digest": self.workspace_before_digest,
            "workspace_after_digest": self.workspace_after_digest,
            "changed_paths": list(self.changed_paths),
            "blocker_category": self.blocker_category,
            "progress_detected": self.progress_detected,
            "progress_reason": list(self.progress_reason),
            "stagnation_reason": list(self.stagnation_reason),
            "recovery_stage": self.recovery_stage,
            "open_blocker_count": self.open_blocker_count,
            "oscillation_detected": self.oscillation_detected,
            "verification_improved": self.verification_improved,
            "governance_decision": self.governance_decision,
            "semantic_state": self.semantic_state,
        }


@dataclass(frozen=True)
class GovernanceDecision:
    action: GovernanceAction = GovernanceAction.ALLOW
    reason: str = ""
    progress_detected: bool = False
    progress_reason: Tuple[str, ...] = ()
    stagnation_reason: Tuple[str, ...] = ()
    recovery_stage: RecoveryStage = RecoveryStage.HEALTHY
    oscillation_detected: bool = False
    open_blocker_count: int = 0
    event: ProgressEvent | None = None

    @property
    def should_replan(self) -> bool:
        return self.action is GovernanceAction.REPLAN

    @property
    def should_terminate(self) -> bool:
        return self.action is GovernanceAction.TERMINATE


@dataclass
class BlockerRecord:
    category: str
    root_cause_fingerprint: str
    first_seen: int
    last_seen: int
    related_intent: str = ""
    observation_fingerprint: str = ""
    strategy_attempts: list[str] = field(default_factory=list)
    progress_since_blocker: list[str] = field(default_factory=list)
    lifecycle: BlockerLifecycle = BlockerLifecycle.OPEN
    evidence_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "root_cause_fingerprint": self.root_cause_fingerprint,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "related_intent": self.related_intent,
            "observation_fingerprint": self.observation_fingerprint,
            "strategy_attempts": list(self.strategy_attempts),
            "progress_since_blocker": list(self.progress_since_blocker),
            "lifecycle": self.lifecycle.value,
            "evidence_count": self.evidence_count,
        }
