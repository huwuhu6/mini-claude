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


def test_aggregate_keeps_missing_trials_in_denominator():
    report = aggregate_governance([{"governance_class": "TP"}, {"governance_class": "FN"}])
    assert report["trial_count"] == 2
    assert report["stop_recall"] == .5


def test_provider_error_is_infra_and_excluded_from_confusion_matrix():
    assert classify_trial_validity({"runtime_error": "provider connection"}, {}) == "INFRA_ERROR"
    report = aggregate_governance([
        {"governance_class": "TP", "trial_validity": "VALID"},
        {"governance_class": "TN", "trial_validity": "VALID"},
        {"governance_class": "FN", "trial_validity": "INFRA_ERROR"},
    ])
    assert report["TP"] == 1 and report["TN"] == 1
    assert report["FN"] == 0 and report["valid_governance_trials"] == 2
    assert report["infra_error_trials"] == 1


def test_evaluator_failure_is_not_a_governance_false_negative():
    assert classify_trial_validity(None, {"verify_status": "CRASHED"}) == "EVAL_ERROR"
    report = aggregate_governance([{
        "governance_class": "FP", "trial_validity": "EVAL_ERROR",
    }])
    assert report["trial_count"] == 0
    assert report["eval_error_trials"] == 1
