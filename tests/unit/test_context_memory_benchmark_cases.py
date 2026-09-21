import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TASKS = (
    "task_036_memory_repeated_read",
    "task_037_memory_freshness_after_edit",
    "task_038_memory_external_drift",
    "task_039_memory_irrelevant_context",
)


def _verify(case_name: str, source: str, tmp_path) -> subprocess.CompletedProcess:
    case = ROOT / "sandbox" / "tasks" / case_name
    workspace = tmp_path / case_name / source
    shutil.copytree(case / source, workspace)
    shutil.copy2(case / "verify.py", workspace / "verify.py")
    return subprocess.run(
        [sys.executable, "verify.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_memory_cases_have_explicit_dev_metadata():
    for case_name in TASKS:
        config = json.loads((ROOT / "sandbox" / "tasks" / case_name / "config.json").read_text(encoding="utf-8"))
        assert config["case_id"] == case_name
        assert config["evaluation"]["suite"] == "context_memory"
        assert config["evaluation"]["split"] == "dev"


def test_memory_case_baselines_fail_and_reference_solutions_pass(tmp_path):
    for case_name in TASKS:
        assert _verify(case_name, "baseline", tmp_path).returncode != 0
        reference = _verify(case_name, "reference_solution", tmp_path)
        assert reference.returncode == 0, f"{case_name}: {reference.stdout}\n{reference.stderr}"
