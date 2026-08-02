"""Regression tests for the evaluation task contract and fixture identity."""

import json
import hashlib
from unittest.mock import patch
from pathlib import Path

from eval_runner import TASKS_ROOT, _sha256_tree, _validate_task
from compare_reports import _load_all_metrics, _render_provenance


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
