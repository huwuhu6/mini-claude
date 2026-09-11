"""Regression tests for the evaluation task contract and fixture identity."""

import json
import hashlib
from argparse import ArgumentTypeError
from unittest.mock import patch
from pathlib import Path

import pytest

from eval_runner import (
    TASKS_ROOT,
    _find_latest_trace,
    _positive_int,
    _select_cases,
    _sha256_tree,
    _truncate_output,
    _validate_reference_solution,
    _validate_task,
    _version_label,
    write_run_results,
)
from core.runtime_data import RuntimeDataPaths
from compare_reports import (
    _include_manifest_cases,
    _include_result_cases,
    _load_all_metrics,
    _compute_peak_turn_tokens,
    _compute_saved_log_read,
    _compute_tool_sequence,
    _fmt_cell,
    _apply_governance_counts,
    _render_anti_loop_summary,
    _render_coverage_notes,
    _render_provenance,
)


def test_all_task_contracts_are_valid():
    case_dirs = sorted(
        path for path in TASKS_ROOT.iterdir()
        if path.is_dir() and (path / "config.json").is_file()
    )

    assert len(case_dirs) == 35
    errors = []
    for case_dir in case_dirs:
        _, task_errors = _validate_task(case_dir)
        errors.extend(f"{case_dir.name}: {error}" for error in task_errors)

    assert errors == []


def test_must_recover_contracts_validate_baseline_failure_and_reference_integrity():
    case_dirs = sorted(
        path for path in TASKS_ROOT.iterdir()
        if path.is_dir() and (path / "config.json").is_file()
    )
    errors = []
    for case_dir in case_dirs:
        config = json.loads((case_dir / "config.json").read_text(encoding="utf-8"))
        evaluation = config.get("evaluation", {})
        if (
            evaluation.get("suite") != "anti_loop"
            or evaluation.get("split") != "dev"
            or evaluation.get("behavior_class") != "must_recover"
        ):
            continue
        errors.extend(
            f"{case_dir.name}: {error}"
            for error in _validate_reference_solution(case_dir, config)
        )

    assert errors == []


def test_reference_only_command_entrypoint_is_valid_for_reference_solution():
    case_dir = TASKS_ROOT / "task_027_changing_poll_observation"
    config = json.loads((case_dir / "config.json").read_text(encoding="utf-8"))

    assert _validate_reference_solution(case_dir, config) == []


def test_missing_reference_command_entrypoint_is_rejected(tmp_path):
    import shutil

    source = TASKS_ROOT / "task_027_changing_poll_observation"
    case_dir = tmp_path / source.name
    shutil.copytree(source, case_dir)
    (case_dir / "reference_solution" / "reference_flow.py").unlink()
    config = json.loads((case_dir / "config.json").read_text(encoding="utf-8"))

    errors = _validate_reference_solution(case_dir, config)

    assert any("reference solution 执行失败" in error for error in errors)


def _task_configs(case_dirs):
    return {
        path.name: json.loads((path / "config.json").read_text(encoding="utf-8"))
        for path in case_dirs
    }


def test_suite_only_selects_all_anti_loop_cases_from_metadata():
    case_dirs = sorted(p for p in TASKS_ROOT.iterdir() if (p / "config.json").is_file())
    selected = _select_cases(case_dirs, _task_configs(case_dirs), suite="anti_loop")
    assert len(selected) == 17
    assert all("anti_loop" == _task_configs([p])[p.name]["evaluation"]["suite"] for p in selected)


def test_split_only_preserves_historical_case_compatibility():
    case_dirs = sorted(p for p in TASKS_ROOT.iterdir() if (p / "config.json").is_file())
    configs = _task_configs(case_dirs)
    selected = _select_cases(case_dirs, configs, split="dev")
    assert TASKS_ROOT / "task_030_long_running_daemon_lifecycle_legacy" in selected
    assert TASKS_ROOT / "task_026_multi_scope_validation" not in selected


def test_suite_and_split_are_an_intersection():
    case_dirs = sorted(p for p in TASKS_ROOT.iterdir() if (p / "config.json").is_file())
    selected = _select_cases(case_dirs, _task_configs(case_dirs), "anti_loop", "dev")
    assert len(selected) == 12
    assert all("task_026" not in path.name for path in selected)


def test_unknown_suite_selects_nothing_without_id_hardcoding():
    case_dirs = sorted(p for p in TASKS_ROOT.iterdir() if (p / "config.json").is_file())
    assert _select_cases(case_dirs, _task_configs(case_dirs), "not_a_suite") == []


def test_evaluation_runtime_root_is_writable_and_trials_are_isolated(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = RuntimeDataPaths.for_workspace(workspace, tmp_path / "run" / "case" / "trial_0001")
    second = RuntimeDataPaths.for_workspace(workspace, tmp_path / "run" / "case" / "trial_0002")
    first.traces.mkdir(parents=True)
    second.traces.mkdir(parents=True)
    trace = first.traces / "task_example.json"
    trace.write_text("{}", encoding="utf-8")
    assert first.root != second.root
    assert _find_latest_trace(workspace, first.root) == trace
    assert _find_latest_trace(workspace, second.root) is None
    import shutil
    shutil.rmtree(first.root)
    assert _find_latest_trace(workspace, first.root) is None


def test_run_results_is_atomic_and_preserves_denominator(tmp_path, monkeypatch):
    import eval_runner
    monkeypatch.setattr(eval_runner, "OUTPUT_ROOT", tmp_path)
    metadata = {"run_id": "run-1", "planned_trials": 2}
    result_path = write_run_results("smoke", metadata, [{
        "case_id": "task_crash", "attempted": True,
        "verify_status": "CRASHED", "trace_status": "MISSING",
    }])
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["planned_trials"] == 2
    assert payload["attempted_trials"] == 1
    assert payload["missing_trace_trials"] == 1
    assert not result_path.with_name(result_path.name + ".tmp").exists()


def test_stalled_code_edit_task_requires_versioned_fixture():
    config_path = TASKS_ROOT / "task_016_stalled_code_edit" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["task_version"] == 4
    assert "approx_line_start" in config["prompt"]
    assert config["expected_final_status"] == "CIRCUIT_BROKEN"


def test_task_007_declares_its_verifier():
    config_path = TASKS_ROOT / "task_007_java_cognitive_noise_rebuild" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["verify_script_file"] == "verify.py"


def test_offline_dependency_task_uses_workspace_verification():
    config_path = TASKS_ROOT / "task_015_offline_dependency_block" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["task_version"] == 3
    assert "expected_final_status" not in config


def test_stateful_shell_task_requires_independent_commands():
    config_path = TASKS_ROOT / "task_017_stateful_shell_env" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["task_version"] == 1
    assert "另一次独立的 bash 调用" in config["prompt"]
    assert "第二步再次设置" in config["prompt"]
    assert config["verify_script_file"] == "verify.py"


def test_daemon_lifecycle_task_requires_background_health_check():
    config_path = TASKS_ROOT / "task_030_long_running_daemon_lifecycle_legacy" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["task_version"] == 1
    assert "run_background" in config["prompt"]
    assert "health_check" in config["prompt"]
    assert "8765" in config["prompt"]
    assert config["verify_script_file"] == "verify.py"


def test_peak_turn_tokens_uses_trace_turn_usage():
    trace = {"turns": [{"token_usage": 120}, {"token_usage": 480}, {"token_usage": 240}]}
    assert _compute_peak_turn_tokens(trace) == 480


def test_report_extracts_tool_sequence_and_saved_log_read():
    trace = {
        "turns": [{
            "tools": [
                {"tool_name": "bash", "result_preview": "[Full output saved to: .agent\\logs\\cmd.log]"},
                {"tool_name": "read_file", "args_hash": '{"path": ".agent\\logs\\cmd.log"}'},
            ]
        }]
    }

    assert _compute_tool_sequence(trace) == "bash -> read_file"
    assert _compute_saved_log_read(trace) == "yes"


def test_report_does_not_count_log_save_as_log_read():
    trace = {
        "turns": [{
            "tools": [{
                "tool_name": "bash",
                "args_hash": '{"command": "python tests/test_suite.py"}',
                "result_preview": "[Full output saved to: .agent\\logs\\cmd.log]",
            }]
        }]
    }

    assert _compute_saved_log_read(trace) == "no"


def test_verify_script_cannot_escape_task_directory():
    case_dir = TASKS_ROOT / "task_001_db_port"
    config = {
        "case_id": case_dir.name,
        "prompt": "test",
        "verify_script_file": "../verify.py",
    }

    with patch("eval_runner.Path.read_text", return_value=json.dumps(config)):
        with patch("eval_runner.Path.is_dir", return_value=True):
            with patch("eval_runner.Path.is_file", return_value=True):
                _, errors = _validate_task(case_dir)

    assert "verify_script_file 不得越出任务目录" in errors


def test_config_must_be_a_json_object():
    case_dir = TASKS_ROOT / "task_001_db_port"

    with patch("eval_runner.Path.read_text", return_value="[]"):
        _, errors = _validate_task(case_dir)

    assert errors == ["config.json 顶层必须是 JSON 对象"]


def test_verify_output_is_bounded_and_keeps_tail():
    output = _truncate_output("a" * 20, limit=10)

    assert output.startswith("...<truncated>...")
    assert output.endswith("a" * 10)
    assert _truncate_output("") is None


def test_eval_cli_rejects_invalid_run_count_and_version_path():
    with pytest.raises(ArgumentTypeError):
        _positive_int("0")
    with pytest.raises(ArgumentTypeError):
        _version_label("../outside")
    assert _positive_int("3") == 3
    assert _version_label("local_experiment") == "local_experiment"


def test_fixture_hash_ignores_generated_directories():
    baseline = TASKS_ROOT / "task_007_java_cognitive_noise_rebuild" / "baseline"
    digest = hashlib.sha256()
    for file_path in sorted(
        p for p in baseline.rglob("*")
        if p.is_file() and not {"node_modules", "__pycache__"}.intersection(
            p.relative_to(baseline).parts
        )
    ):
        digest.update(file_path.relative_to(baseline).as_posix().encode("utf-8"))
        digest.update(hashlib.sha256(file_path.read_bytes()).hexdigest().encode("ascii"))

    assert _sha256_tree(baseline) == digest.hexdigest()


def test_report_marks_versions_without_manifest_as_incomplete():
    versions = [("old_result", TASKS_ROOT)]

    report_lines = _render_provenance(versions, {"old_result": None})

    report = "\n".join(report_lines)
    assert "缺少可追溯的 run manifest" in report


def test_report_ignores_traces_from_an_older_run():
    class FakeTrace:
        def __init__(self, name, data):
            self.name = name
            self._data = data

        def read_text(self, encoding):
            return json.dumps(self._data)

        def __lt__(self, other):
            return self.name < other.name

    class FakeVersionDir:
        def glob(self, pattern):
            return [
                FakeTrace(
                    "trace_task_old_r02.json",
                    {
                        "evaluation_metadata": {"run_id": "old-run"},
                        "eval_result": "SUCCESS",
                        "total_turns": 99,
                    },
                ),
                FakeTrace(
                    "trace_task_current.json",
                    {
                        "evaluation_metadata": {"run_id": "current-run"},
                        "eval_result": "SUCCESS",
                        "total_turns": 3,
                    },
                ),
            ]

    matrix = _load_all_metrics(
        [("version", FakeVersionDir())],
        {"version": {"run_id": "current-run"}},
    )

    assert set(matrix) == {"task_current"}
    assert matrix["task_current"]["version"]["total_turns"] == 3


def test_report_keeps_failed_case_without_trace():
    matrix = {}
    versions = [("version", Path("unused"))]
    manifests = {"version": {"run_id": "run-1"}}
    results = {
        "version": {
            "run_id": "run-1",
            "results": [{
                "case_id": "task_crashed",
                "verify_status": "CRASHED",
                "trace_status": "MISSING",
            }],
        }
    }

    _include_result_cases(matrix, versions, manifests, results)

    assert matrix["task_crashed"]["version"]["eval_result"] == "CRASHED"
    assert matrix["task_crashed"]["version"]["_trace_status"] == "MISSING"
    failed_metrics = dict(matrix["task_crashed"]["version"])
    failed_metrics["_failure_reason"] = "case_exception:RuntimeError: boom"
    assert "case_exception:RuntimeError" in _fmt_cell(failed_metrics)


def test_report_uses_attempt_count_when_some_runs_have_no_trace():
    matrix = {"task_partial": {"version": {
        "eval_result": "SUCCESS",
        "total_turns": 4,
    }}}
    versions = [("version", Path("unused"))]
    manifests = {"version": {"run_id": "run-1"}}
    results = {
        "version": {
            "run_id": "run-1",
            "results": [
                {"case_id": "task_partial", "verify_status": "SUCCESS", "trace_status": "ARCHIVED"},
                {"case_id": "task_partial", "verify_status": "FAILED", "trace_status": "MISSING", "failure_reason": "trace_missing_before_verify"},
                {"case_id": "task_partial", "verify_status": "SUCCESS", "trace_status": "ARCHIVED"},
            ],
        }
    }

    _include_result_cases(matrix, versions, manifests, results)

    metrics = matrix["task_partial"]["version"]
    assert metrics["_run_count"] == 3
    assert metrics["_pass_count"] == 2
    assert metrics["_missing_trace_count"] == 1
    assert "2/3" in _fmt_cell(metrics)


def test_report_exposes_declared_but_missing_cases():
    versions = [("version", Path("unused"))]
    manifests = {"version": {"tasks": [{"case_id": "task_missing"}]}}
    matrix = {"task_done": {"version": {"eval_result": "SUCCESS"}}}

    _include_manifest_cases(matrix, manifests)
    notes = _render_coverage_notes(versions, matrix, manifests)

    assert "task_missing" in matrix
    assert "未覆盖: task_missing" in "\n".join(notes)


def test_report_keeps_raw_governance_separate_from_grounded_capability():
    metrics = [
        {"anti_loop_governance_class": "TP", "anti_loop_grounded_capability_success": False},
        {"anti_loop_governance_class": "TN", "anti_loop_grounded_capability_success": True},
    ]
    result = {}
    _apply_governance_counts(result, metrics)
    assert result["anti_loop_TP"] == 1
    assert result["anti_loop_TN"] == 1
    assert result["grounded_capability_trials"] == 2
    assert result["grounded_capability_successes"] == 1
    assert result["grounded_capability_rate"] == 0.5

    unavailable = {"anti_loop_governance_class": "FN",
                   "anti_loop_grounded_capability_success": False,
                   "anti_loop_grounded_evidence_available": False}
    result = {}
    _apply_governance_counts(result, [unavailable])
    assert result["grounded_capability_trials"] == 0

    matrix = {"task_018": {"version": {
        "evaluation_split": "dev", "anti_loop_TP": 1, "anti_loop_TN": 0,
        "anti_loop_FP": 0, "anti_loop_FN": 0,
        "grounded_capability_trials": 1, "grounded_capability_successes": 0,
    }}}
    report = "\n".join(_render_anti_loop_summary(matrix, [("version", Path("unused"))]))
    assert "Grounded Capability" in report
    assert "0/1 (0.0%)" in report
