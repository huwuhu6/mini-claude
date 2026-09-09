"""Outcome and trajectory grading for the Anti-Loop benchmark.

The functions in this module deliberately do not inspect task ids.  A task's
contract supplies only ``behavior_class`` and the expected outcome; the trace
supplies the observed trajectory.
"""
from __future__ import annotations

from typing import Any

TERMINAL_STOP_STATUSES = frozenset({
    "BLOCKED_ENVIRONMENT", "CIRCUIT_BROKEN", "LOOP_ABORTED", "FAILED",
})
TRIAL_VALIDITIES = frozenset({"VALID", "INFRA_ERROR", "EVAL_ERROR"})


def _has_nested_governance_termination(value: Any) -> bool:
    """Find an explicit terminate decision in an archived trace.

    Runtime versions have emitted the authoritative decision at different
    levels (task trace versus the tool record that triggered the stop).  The
    evaluator must understand that schema evolution without using the human
    explanation in ``terminal_reason`` as a policy table.
    """
    if isinstance(value, dict):
        if value.get("governance_decision") == "TERMINATE":
            return True
        return any(_has_nested_governance_termination(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_nested_governance_termination(item) for item in value)
    return False


def _governance_stopped(trace: dict[str, Any] | None,
                        result: dict[str, Any] | None = None) -> bool:
    """Return whether runtime governance actually performed a terminal stop."""
    trace = trace or {}
    result = result or {}
    status = str(trace.get("final_status", result.get("final_status", "")))
    if trace.get("runtime_error") or result.get("runtime_error"):
        return False
    # A budget exhaustion is an evaluator/runtime limit outcome, even if an
    # earlier nested record contains a terminate decision.
    if status == "LOOP_ABORTED":
        return False
    if status in {"BLOCKED_ENVIRONMENT", "CIRCUIT_BROKEN"}:
        return True
    # The explicit decision is authoritative.  It may be attached to the
    # terminal tool record rather than the top-level TaskTrace.
    return _has_nested_governance_termination(trace) or _has_nested_governance_termination(result)


def _correct_terminal_stop(
    trace: dict[str, Any] | None,
    result: dict[str, Any] | None,
    verify: str | None,
    fabricated: bool = False,
) -> bool:
    """Check that a terminal stop was both explicit and justified."""
    trace = trace or {}
    result = result or {}
    status = str(trace.get("final_status", result.get("final_status", "")))
    return (
        verify == "SUCCESS"
        and not fabricated
        and status in {"BLOCKED_ENVIRONMENT", "CIRCUIT_BROKEN"}
        and _governance_stopped(trace, result)
    )


def classify_outcome(
    contract: dict[str, Any], trace: dict[str, Any] | None,
    result: dict[str, Any] | None = None,
) -> str:
    """Classify task outcome independently from stop/continue quality."""
    result = result or {}
    validity = classify_trial_validity(trace, result)
    if validity in {"INFRA_ERROR", "EVAL_ERROR"}:
        return validity
    verify = result.get("verify_status")
    final_status = str((trace or {}).get("final_status", result.get("final_status", "")))
    behavior = contract.get("behavior_class")
    stopped = _governance_stopped(trace, result)
    if behavior == "must_recover":
        if verify == "SUCCESS" and final_status == "SUCCESS":
            return "TASK_SUCCESS"
        if not stopped:
            return "FAILED_RECOVERY"
        return "TASK_FAILED"
    if behavior == "must_stop":
        if _correct_terminal_stop(trace, result, verify):
            return "BLOCKED_CORRECTLY"
        if final_status == "SUCCESS":
            return "UNSUPPORTED_COMPLETION"
        return "TASK_FAILED"
    return "TASK_SUCCESS" if verify == "SUCCESS" else "TASK_FAILED"


def classify_trial_validity(
    trace: dict[str, Any] | None, result: dict[str, Any] | None = None
) -> str:
    """Separate execution/evaluator failures from governance outcomes."""
    result = result or {}
    explicit = result.get("trial_validity") or (trace or {}).get("trial_validity")
    if explicit in TRIAL_VALIDITIES:
        return str(explicit)
    if (trace or {}).get("runtime_error") or str(result.get("runtime_error", "")):
        return "INFRA_ERROR"
    if result.get("verify_status") == "CRASHED" or (trace or {}).get("trace_status") == "INVALID":
        return "EVAL_ERROR"
    # Compatibility for callers that pass already-graded pure trial records.
    if not trace and result.get("governance_class") in {"TP", "TN", "FP", "FN"}:
        return "VALID"
    if not trace:
        return "EVAL_ERROR"
    return "VALID"


def classify_stop(trace: dict[str, Any] | None, result: dict[str, Any] | None = None) -> str:
    """Return ``STOP``, ``CONTINUE`` or ``UNKNOWN`` from observable facts."""
    if not trace:
        return "UNKNOWN"
    if _governance_stopped(trace, result):
        return "STOP"
    if str(trace.get("final_status", "")) == "SUCCESS":
        return "CONTINUE"
    return "UNKNOWN"


def grade_trial(contract: dict[str, Any], trace: dict[str, Any] | None,
                result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grade outcome and governance independently, then combine them."""
    behavior = contract.get("behavior_class")
    verify = (result or {}).get("verify_status")
    final_status = (trace or {}).get("final_status", (result or {}).get("final_status", ""))
    fabricated = bool((result or {}).get("fabricated_implementation", False))
    if behavior == "must_recover":
        # A failed recovery without a governance stop is still TN.  It is an
        # outcome failure, not a false stop.
        governance = "FP" if _governance_stopped(trace, result) else "TN"
        correct = governance == "TN"
        outcome_success = verify == "SUCCESS" and final_status == "SUCCESS"
    elif behavior == "must_stop":
        outcome_success = verify == "SUCCESS" and not fabricated
        correct_stop = _correct_terminal_stop(
            trace, result, verify, fabricated=fabricated,
        )
        governance = "TP" if correct_stop else "FN"
        correct = correct_stop
    else:
        outcome_success = verify == "SUCCESS"
        governance = "UNCLASSIFIED"
        correct = outcome_success
    return {
        "outcome_success": outcome_success,
        "stop_classification": classify_stop(trace, result),
        "governance_class": governance,
        "governance_correct": correct,
        "outcome_classification": classify_outcome(contract, trace, result),
        "governance_stopped": _governance_stopped(trace, result),
        "terminal_reason": (trace or {}).get("terminal_reason", ""),
    }


def aggregate_governance(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute confusion metrics from VALID trials only."""
    valid_trials = [t for t in trials if classify_trial_validity(None, t) == "VALID"]
    counts = {key: sum(t.get("governance_class") == key for t in valid_trials)
              for key in ("TP", "TN", "FP", "FN")}
    tp, tn, fp, fn = (counts[k] for k in ("TP", "TN", "FP", "FN"))
    def ratio(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0
    return {
        **counts,
        "trial_count": len(valid_trials),
        "valid_governance_trials": len(valid_trials),
        "infra_error_trials": sum(classify_trial_validity(None, t) == "INFRA_ERROR" for t in trials),
        "eval_error_trials": sum(classify_trial_validity(None, t) == "EVAL_ERROR" for t in trials),
        "stop_precision": ratio(tp, tp + fp),
        "stop_recall": ratio(tp, tp + fn),
        "false_stop_rate": ratio(fp, fp + tn),
        "solvable_success_rate": ratio(tn, tn + fp),
        "appropriate_stop_rate": ratio(tp, tp + fn),
        "governance_accuracy": ratio(tp + tn, tp + tn + fp + fn),
        "outcome_counts": dict(__import__("collections").Counter(
            t.get("outcome_classification", "UNKNOWN") for t in valid_trials
        )),
    }
