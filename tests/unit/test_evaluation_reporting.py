"""Pure reporting regressions for trial validity and one-trial aggregation."""

from compare_reports import _aggregate_metrics, _render_trial_validity_summary


def _metric(classification, validity="VALID"):
    return {
        "evaluation_split": "dev",
        "trial_validity": validity,
        "anti_loop_governance_class": classification,
        "eval_result": "SUCCESS" if validity == "VALID" else "FAILED",
    }


def test_single_valid_trial_populates_confusion_matrix():
    result = _aggregate_metrics([_metric("TP")])
    assert result["anti_loop_TP"] == 1
    assert result["anti_loop_TN"] == 0
    assert result["governance_trial_count"] == 1


def test_single_infra_trial_has_no_confusion_class():
    result = _aggregate_metrics([_metric("FN", "INFRA_ERROR")])
    assert result["anti_loop_FN"] == 0
    assert result["valid_governance_trials"] == 0
    assert result["infra_error_trials"] == 1


def test_multi_trial_counts_sum_only_valid_trials():
    result = _aggregate_metrics([
        _metric("TP"), _metric("TN"), _metric("FP"), _metric("FN"),
        _metric("FN", "INFRA_ERROR"), _metric("FP", "EVAL_ERROR"),
    ])
    assert [result[f"anti_loop_{key}"] for key in ("TP", "TN", "FP", "FN")] == [1, 1, 1, 1]
    assert result["governance_trial_count"] == 4
    assert result["infra_error_trials"] == 1
    assert result["eval_error_trials"] == 1


def test_trial_validity_summary_shows_execution_and_governance_denominators():
    lines = _render_trial_validity_summary({
        "smoke": {
            "planned_trials": 3,
            "attempted_trials": 3,
            "results": [
                {"trial_validity": "VALID"},
                {"trial_validity": "INFRA_ERROR"},
                {"trial_validity": "EVAL_ERROR"},
            ],
        },
    })
    report = "\n".join(lines)
    assert "| `smoke` | 3 | 3 | 1 | 1 | 1 |" in report
