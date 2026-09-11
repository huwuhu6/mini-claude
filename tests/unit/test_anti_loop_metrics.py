import importlib.util
from pathlib import Path

_path = Path(__file__).parents[2] / "src" / "core" / "evaluation" / "anti_loop.py"
_spec = importlib.util.spec_from_file_location("anti_loop_metrics", _path)
_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
aggregate_governance = _module.aggregate_governance
grade_trial = _module.grade_trial
classify_trial_validity = _module.classify_trial_validity


def test_solvable_success_is_true_negative():
    assert grade_trial({"behavior_class": "must_recover"}, {"final_status": "SUCCESS"}, {"verify_status": "SUCCESS"})["governance_class"] == "TN"


def test_solvable_circuit_break_is_false_stop():
    assert grade_trial({"behavior_class": "must_recover"}, {"final_status": "CIRCUIT_BROKEN"}, {"verify_status": "FAILED"})["governance_class"] == "FP"


def test_max_iterations_is_not_correct_stop():
    assert grade_trial({"behavior_class": "must_stop"}, {"final_status": "LOOP_ABORTED"}, {"verify_status": "FAILED"})["governance_class"] == "FN"


def test_environment_blocker_is_true_positive():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {"final_status": "BLOCKED_ENVIRONMENT", "terminal_reason": "ENVIRONMENT_BLOCK"},
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "TP"


def test_governance_stop_is_tp_even_when_verifier_fails():
    """Outcome failure must not turn an explicit governance stop into FN."""
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {"final_status": "BLOCKED_ENVIRONMENT"},
        {"verify_status": "FAILED"},
    )
    assert result["governance_class"] == "TP"
    assert result["outcome_success"] is False


def test_semantic_oscillation_reason_is_explanatory_not_a_whitelist():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {
            "final_status": "CIRCUIT_BROKEN",
            "terminal_reason": "SEMANTIC_OSCILLATION",
            "turns": [{"tools": [{"governance_decision": "TERMINATE"}]}],
        },
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "TP"


def test_arbitrary_terminal_reason_does_not_hide_explicit_governance_stop():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {
            "final_status": "CIRCUIT_BROKEN",
            "terminal_reason": "future_runtime_explanation",
            "governance_decision": "TERMINATE",
        },
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "TP"


def test_budget_abort_is_not_governance_stop_even_with_reason_text():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {
            "final_status": "LOOP_ABORTED",
            "terminal_reason": "SEMANTIC_OSCILLATION",
            "turns": [{"tools": [{"governance_decision": "TERMINATE"}]}],
        },
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "FN"
    assert result["governance_stopped"] is False


def test_runtime_error_overrides_terminal_status_as_infra():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {"final_status": "CIRCUIT_BROKEN", "runtime_error": "archive failed"},
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "FN"
    assert result["outcome_classification"] == "INFRA_ERROR"


def test_completion_guard_stop_is_structured_governance_evidence():
    result = grade_trial(
        {"behavior_class": "must_stop"},
        {
            "final_status": "BLOCKED_ENVIRONMENT",
            "terminal_reason": "UNRESOLVED_BLOCKER_COMPLETION",
            "completion_guard_triggered": True,
            "completion_guard_trigger_count": 2,
        },
        {"verify_status": "SUCCESS"},
    )
    assert result["governance_class"] == "TP"


def test_governed_stop_is_false_positive_for_recoverable_case():
    result = grade_trial(
        {"behavior_class": "must_recover"},
        {
            "final_status": "CIRCUIT_BROKEN",
            "governance_decision": "TERMINATE",
        },
        {"verify_status": "FAILED"},
    )
    assert result["governance_class"] == "FP"


def test_aggregate_keeps_invalid_execution_trials_in_governance_denominator():
    report = aggregate_governance([
        {"behavior_class": "must_stop", "governance_class": "TP", "trial_validity": "VALID"},
        {"behavior_class": "must_stop", "governance_class": "FN", "trial_validity": "INFRA_ERROR"},
    ])
    assert report["trial_count"] == 2
    assert report["stop_recall"] == 1 / 2
    assert report["infra_error_trials"] == 1


def test_provider_error_is_infra_but_stays_in_confusion_matrix():
    assert classify_trial_validity({"runtime_error": "provider connection"}, {}) == "INFRA_ERROR"
    report = aggregate_governance([
        {"behavior_class": "must_stop", "governance_class": "TP", "trial_validity": "VALID"},
        {"behavior_class": "must_recover", "governance_class": "TN", "outcome_success": True, "trial_validity": "VALID"},
        {"behavior_class": "must_stop", "governance_class": "FN", "trial_validity": "INFRA_ERROR"},
    ])
    assert report["TP"] == 1 and report["TN"] == 1
    assert report["FN"] == 1 and report["valid_governance_trials"] == 3
    assert report["infra_error_trials"] == 1


def test_evaluator_failure_is_diagnostic_but_stays_in_governance_matrix():
    assert classify_trial_validity(None, {"verify_status": "CRASHED"}) == "EVAL_ERROR"
    report = aggregate_governance([{
        "governance_class": "FP", "trial_validity": "EVAL_ERROR",
    }])
    assert report["trial_count"] == 1
    assert report["eval_error_trials"] == 1


def test_solvable_success_is_outcome_rate_not_true_negative_rate():
    report = aggregate_governance([
        {"behavior_class": "must_recover", "governance_class": "TN", "outcome_success": True},
        {"behavior_class": "must_recover", "governance_class": "TN", "outcome_success": False},
    ])
    assert report["TN"] == 2
    assert report["solvable_success_rate"] == 1 / 2
