"""Progress-aware failure and loop governance.

The module deliberately knows nothing about benchmark case IDs or a particular
language.  It turns tool evidence into explainable progress decisions while
leaving the existing LoopGuard, Failure Intelligence and workspace snapshot
implementations as independent evidence producers.
"""

from .models import (
    BlockerLifecycle,
    BlockerRecord,
    GovernanceAction,
    GovernanceDecision,
    ProgressEvent,
    RecoveryStage,
)
from .normalizer import ObservationNormalizer
from .tracker import BlockerLedger, CompletionGuard, ProgressTracker

__all__ = [
    "BlockerLifecycle",
    "BlockerLedger",
    "BlockerRecord",
    "CompletionGuard",
    "GovernanceAction",
    "GovernanceDecision",
    "ObservationNormalizer",
    "ProgressEvent",
    "ProgressTracker",
    "RecoveryStage",
]
