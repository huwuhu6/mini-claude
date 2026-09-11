"""
Failure Intelligence Layer — Runtime Failure Semantic Understanding.

Provides stateless failure classification and strategy fingerprints.  Runtime
history and escalation decisions belong to ``core.loop_controller``.

Usage:
    analyzer = FailureAnalyzer()
    sig = analyzer.analyze("bash", {"command": "pip install ..."}, result_text)

    # RuntimePolicy consumes the resulting signature through AttemptHistory.
"""
from .models import FailureCategory, Recoverability, FailureSignature
from .signatures import FailureSignatureMatcher, infer_strategy_fingerprint
from .memory import FailureMemory
from .analyzer import FailureAnalyzer

__all__ = [
    'FailureCategory',
    'Recoverability',
    'FailureSignature',
    'FailureSignatureMatcher',
    'infer_strategy_fingerprint',
    'FailureMemory',
    'FailureAnalyzer',
]
