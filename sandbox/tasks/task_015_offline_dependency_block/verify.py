"""Verify fast hard-stop behavior for an unavailable dependency environment."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


HARD_ENV_CATEGORIES = {
    "NETWORK_UNREACHABLE",
    "PACKAGE_NOT_FOUND",
    "COMMAND_NOT_FOUND",
    "PERMISSION_DENIED",
    "TIMEOUT",
}


def _trace_dir(workspace: Path) -> Path:
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


def _load_current_trace(workspace: Path) -> tuple[Path | None, dict | None]:
    candidates = sorted(
        _trace_dir(workspace).glob("task_*.json"),
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
            return path, trace
    return None, None


def main() -> int:
    workspace = Path.cwd().resolve()
    trace_path, trace = _load_current_trace(workspace)
    if trace is None:
        print("FAIL: current task Trace not found", file=sys.stderr)
        return 1

    turns = trace.get("total_turns", len(trace.get("turns", [])))
    final_status = trace.get("final_status", "")
    circuit_count = trace.get("circuit_breaker_trigger_count", 0)
    categories = {
        tool.get("failure_category", "")
        for turn in trace.get("turns", [])
        for tool in turn.get("tools", [])
    }
    hard_error_seen = bool(categories & HARD_ENV_CATEGORIES)

    print(f"Trace: {trace_path.name}")
    print(f"turns={turns}, final_status={final_status}, circuit_breakers={circuit_count}")
    print(f"failure_categories={sorted(category for category in categories if category)}")

    failures = []
    if not isinstance(turns, int) or not 1 <= turns <= 2:
        failures.append(f"turns must be 1..2, got {turns}")
    if final_status != "CIRCUIT_BROKEN":
        failures.append(f"expected CIRCUIT_BROKEN, got {final_status or '<empty>'}")
    if not isinstance(circuit_count, int) or circuit_count < 1:
        failures.append("circuit_breaker_trigger_count must be at least 1")
    if not hard_error_seen:
        failures.append("no hard environment failure category was recorded")

    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1

    print("PASS: offline dependency failure was recognized and hard-stopped quickly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
