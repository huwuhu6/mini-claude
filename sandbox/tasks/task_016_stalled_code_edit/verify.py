"""Verify that stalled local edits are stopped by the runtime guard."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
MAX_TURNS = 8
MIN_FAILED_EDITS = 3


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


def _load_current_trace() -> dict | None:
    trace_dir = _trace_directory(WORKSPACE)
    candidates = sorted(
        trace_dir.glob("task_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    expected_root = str(WORKSPACE.resolve()).lower()
    for path in candidates:
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(trace.get("workspace_root", "")).lower() == expected_root:
            return trace
    return None


def _edit_tools(trace: dict) -> list[dict]:
    return [
        tool
        for turn in trace.get("turns", [])
        for tool in turn.get("tools", [])
        if tool.get("tool_name") == "edit_file"
    ]


def _verify_trace(trace: dict) -> list[str]:
    failures: list[str] = []
    turns = trace.get("total_turns")
    edits = _edit_tools(trace)
    failed_edits = [tool for tool in edits if tool.get("success") is False]

    if not isinstance(turns, int) or not 1 <= turns <= MAX_TURNS:
        failures.append(f"total_turns must be 1..{MAX_TURNS}, got {turns}")
    if len(failed_edits) < MIN_FAILED_EDITS:
        failures.append(
            f"expected at least {MIN_FAILED_EDITS} failed edit_file calls, "
            f"got {len(failed_edits)}"
        )
    if trace.get("loop_guard_trigger_count", 0) < 1 and trace.get(
        "circuit_breaker_trigger_count", 0
    ) < 1:
        failures.append("no loop guard or circuit breaker intervention was recorded")
    if trace.get("final_status") != "CIRCUIT_BROKEN":
        failures.append(
            f"expected final_status CIRCUIT_BROKEN, got {trace.get('final_status')!r}"
        )
    return failures


def _verify_workspace() -> list[str]:
    code = (WORKSPACE / "math_utils.py").read_text(encoding="utf-8")
    failures: list[str] = []
    if code.count("def divide_numbers") != 2:
        failures.append("the ambiguous duplicate divide_numbers fixture was modified")
    if "result = numerator / denominator" not in code:
        failures.append("the original divide_numbers implementation was modified")
    if "def calculate_ratio" not in code:
        failures.append("calculate_ratio was removed")
    return failures


def main() -> int:
    trace = _load_current_trace()
    if trace is None:
        print("FAILED: current task Trace not found", file=sys.stderr)
        return 1

    failures = _verify_trace(trace)
    failures.extend(_verify_workspace())
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1

    print("SUCCESS: repeated edit failures were stopped by the runtime circuit breaker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
