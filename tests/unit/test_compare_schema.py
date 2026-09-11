import json

from compare_reports import _load_trace_metrics


def test_legacy_trace_is_not_rescored_as_v2(tmp_path):
    path = tmp_path / "trace_task_019.json"
    path.write_text(json.dumps({
        "final_status": "CIRCUIT_BROKEN",
        "anti_loop": {"governance_class": "TP", "grounded_capability_success": True},
    }), encoding="utf-8")
    metrics = _load_trace_metrics(path)
    assert metrics["anti_loop_schema_status"] == "LEGACY_NOT_COMPARABLE"
    assert "anti_loop_governance_class" not in metrics
    assert metrics["legacy_raw_governance_class"] == "TP"


def test_current_trace_exposes_v2_grading_fields(tmp_path):
    path = tmp_path / "trace_task_019.json"
    path.write_text(json.dumps({
        "final_status": "CIRCUIT_BROKEN",
        "anti_loop_grading_schema_version": 2,
        "benchmark_contract_version": 2,
        "anti_loop": {"governance_class": "TP", "grounded_capability_success": True},
    }), encoding="utf-8")
    metrics = _load_trace_metrics(path)
    assert metrics["anti_loop_schema_status"] == "CURRENT"
    assert metrics["anti_loop_governance_class"] == "TP"
