"""Verify the edit result and the required test-driven workflow."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
MAX_TURNS = 8
MAX_TOKENS = 40_000


def _trace_directory(workspace: Path) -> Path:
    configured = os.getenv("MINI_CLAUDE_DATA_DIR")
    if configured:
        data_root = Path(configured).expanduser()
    elif sys.platform == "win32":
        data_root = Path(r"D:\02_study\code\mini-claude-project-data")
    else:
        data_root = Path.home() / ".local" / "share" / "mini-claude"

    digest = hashlib.sha256(
        str(workspace.resolve()).lower().encode("utf-8")
    ).hexdigest()[:8]
    project_name = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in workspace.name
    )
    return data_root / f"{project_name}-{digest}" / "traces"


def _load_current_trace(workspace: Path) -> dict | None:
    trace_dir = _trace_directory(workspace)
    candidates = sorted(
        trace_dir.glob("task_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    expected_root = str(workspace.resolve()).lower()
    for path in candidates:
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(trace.get("workspace_root", "")).lower() == expected_root:
            return trace
    return None


def _verify_test_execution(trace: dict) -> list[str]:
    failures: list[str] = []
    turns = trace.get("total_turns")
    tokens = trace.get("total_tokens")
    tools = [
        tool
        for turn in trace.get("turns", [])
        for tool in turn.get("tools", [])
    ]
    edit_calls = [tool for tool in tools if tool.get("tool_name") == "edit_file"]
    test_evidence = " ".join(
        str(tool.get("result_preview", ""))
        for tool in tools
        if tool.get("tool_name") in {"bash", "run_command", "execute_command"}
    )

    if not isinstance(turns, int) or not 1 <= turns <= MAX_TURNS:
        failures.append(f"total_turns must be 1..{MAX_TURNS}, got {turns}")
    if not isinstance(tokens, int) or tokens > MAX_TOKENS:
        failures.append(f"total_tokens must be <= {MAX_TOKENS}, got {tokens}")
    if not edit_calls:
        failures.append("no edit_file call was recorded")
    if not any(
        marker in test_evidence
        for marker in ("Ran 1 test", "Ran 2 tests", "1 passed", "2 passed")
    ):
        failures.append("no successful test execution was recorded")
    return failures


def _verify_workspace() -> list[str]:
    failures: list[str] = []
    test_result = subprocess.run(
        [sys.executable, "tests/test_math.py"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
    )
    if test_result.returncode != 0:
        failures.append(f"tests/test_math.py failed: {test_result.stderr.strip()}")

    sys.path.insert(0, str(WORKSPACE))
    from math_utils import calculate_ratio

    if calculate_ratio(10, 2) != 5.0:
        failures.append("calculate_ratio no longer performs normal division")
    try:
        calculate_ratio(10, 0)
    except ZeroDivisionError:
        pass
    else:
        failures.append("calculate_ratio was changed to handle division by zero")
    return failures


def main() -> int:
    trace = _load_current_trace(WORKSPACE)
    if trace is None:
        print("FAILED: current task Trace not found", file=sys.stderr)
        return 1

    failures = _verify_test_execution(trace)
    failures.extend(_verify_workspace())
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1

    print("SUCCESS: edit result, test execution, and convergence limits passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
