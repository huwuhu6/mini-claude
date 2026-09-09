"""Cheap mutation checks for benchmark graders; no Agent or network involved."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
TASKS = ROOT / "sandbox" / "tasks"
RECOVER = [f"task_{n:03d}" for n in range(22, 28)]
STOP = [f"task_{n:03d}" for n in (18, 19, 20, 21, 28, 29)]


def _config(case_id):
    path = next(TASKS.glob(case_id + "_*/config.json"))
    return path.parent, json.loads(path.read_text(encoding="utf-8"))


def test_all_recover_cases_have_reference_solution():
    for case_id in RECOVER:
        case, config = _config(case_id)
        assert config["evaluation"]["behavior_class"] == "must_recover"
        assert (case / "reference_solution").is_dir()


def test_recover_noop_and_sentinel_without_trace_fail():
    for case_id in RECOVER:
        case, config = _config(case_id)
        verify = case / config["verify_script_file"]
        with __import__("tempfile").TemporaryDirectory() as temp:
            work = Path(temp)
            (work / "solution.txt").write_text("RECOVERED", encoding="utf-8")
            (work / verify.name).write_text(verify.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run([sys.executable, verify.name], cwd=work,
                                    env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                                    capture_output=True, text=True)
            assert result.returncode != 0, f"{case_id} accepts a hardcoded sentinel"


def test_stop_mutations_do_not_accept_fabricated_artifacts():
    for case_id in STOP:
        case, config = _config(case_id)
        verify = case / config["verify_script_file"]
        with __import__("tempfile").TemporaryDirectory() as temp:
            work = Path(temp)
            for item in (case / "baseline").iterdir():
                if item.is_file():
                    (work / item.name).write_bytes(item.read_bytes())
            (work / "fake_dependency.py").write_text("# fake", encoding="utf-8")
            (work / "build.out").write_text("fake", encoding="utf-8")
            (work / "release.out").write_text("fake", encoding="utf-8")
            (work / verify.name).write_text(verify.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run([sys.executable, verify.name], cwd=work,
                                    env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                                    capture_output=True, text=True)
            assert result.returncode != 0, f"{case_id} accepts fabricated dependency/artifact"
