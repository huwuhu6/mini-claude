"""
Failure Intelligence Layer — Runtime Failure Semantic Understanding.

Provides failure classification, strategy diversity detection, failure
memory (per-task), and escalation policy to help the agent understand
failure patterns rather than blindly retrying.

Usage:
    analyzer = FailureAnalyzer()
    sig = analyzer.analyze("bash", {"command": "pip install ..."}, result_text)

    memory = FailureMemory()
    memory.set_task("task_001")
    memory.record(sig.category.value, sig.strategy_fingerprint)

    policy = FailureEscalationPolicy()
    should_esc, reason = policy.should_escalate(sig, cat_count, stg_div)
"""
from .models import FailureCategory, Recoverability, FailureSignature
from .signatures import FailureSignatureMatcher, infer_strategy_fingerprint
from .memory import FailureMemory
from .policy import FailureEscalationPolicy, build_escalation_message
from .analyzer import FailureAnalyzer

__all__ = [
    'FailureCategory',
    'Recoverability',
    'FailureSignature',
    'FailureSignatureMatcher',
    'infer_strategy_fingerprint',
    'FailureMemory',
    'FailureEscalationPolicy',
    'build_escalation_message',
    'FailureAnalyzer',
]
