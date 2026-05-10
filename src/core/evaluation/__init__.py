"""
Trace-Driven Eval System — Agent Runtime Process Quality Analysis.

Analyzes Runtime Trace files to compute process quality metrics:
duplicate detection, recovery rates, degradation scoring, etc.

Usage:
    analyzer = TraceAnalyzer(workdir)
    analyzer.load_latest_trace()
    metrics = analyzer.compute_metrics()
"""
from .analyzer import TraceAnalyzer
from .metrics import (
    compute_duplicate_tool_ratio,
    compute_avg_tools_per_turn,
    compute_reflection_recovery_rate,
    compute_compression_survival,
    compute_rollback_occurred,
    compute_degradation_score,
)

__all__ = [
    'TraceAnalyzer',
    'compute_duplicate_tool_ratio',
    'compute_avg_tools_per_turn',
    'compute_reflection_recovery_rate',
    'compute_compression_survival',
    'compute_rollback_occurred',
    'compute_degradation_score',
]
