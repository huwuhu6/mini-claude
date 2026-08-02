"""Regression tests for the evaluation task contract and fixture identity."""

import json
import hashlib
from argparse import ArgumentTypeError
from unittest.mock import patch
from pathlib import Path

import pytest

from eval_runner import (
    TASKS_ROOT,
    _positive_int,
    _sha256_tree,
    _truncate_output,
    _validate_task,
    _version_label,
)
from compare_reports import (
    _include_manifest_cases,
    _include_result_cases,
    _load_all_metrics,
    _fmt_cell,
    _render_coverage_notes,
    _render_provenance,
)


def test_all_task_contracts_are_valid():
    case_dirs = sorted(
        path for path in TASKS_ROOT.iterdir()
        if path.is_dir() and (path / "config.json").is_file()
    )

    assert len(case_dirs) == 12
    errors = []
    for case_dir in case_dirs:
        _, task_errors = _validate_task(case_dir)
        errors.extend(f"{case_dir.name}: {error}" for error in task_errors)

    assert errors == []


def test_task_007_declares_its_verifier():
    config_path = TASKS_ROOT / "task_007_java_cognitive_noise_rebuild" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["verify_script_file"] == "verify.py"


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
    assert "case_exception:RuntimeError" in _fmt_cell(matrix["task_crashed"]["version"] | {
        "_failure_reason": "case_exception:RuntimeError: boom"
    })


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
