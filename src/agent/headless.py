"""One-shot, workspace-bound execution for automation and benchmark adapters."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from agent.mini_claude_agent import MiniClaudeAgent
from core.runtime_data import RuntimeDataPaths


@dataclass(frozen=True)
class AgentRunResult:
    final_response: str
    final_status: str
    trace_path: Path | None
    task_id: str | None


def run_agent_once(
    *,
    workspace_root: Path,
    instruction: str,
    config_path: Path | None = None,
    runtime_data_root: Path | None = None,
) -> AgentRunResult:
    """Run exactly one instruction in an explicitly chosen workspace."""
    workspace = Path(workspace_root).resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    if not instruction.strip():
        raise ValueError("instruction must not be empty")

    data_paths = RuntimeDataPaths.for_workspace(workspace, data_root=runtime_data_root)
    existing = set(data_paths.traces.glob("task_*.json")) if data_paths.traces.exists() else set()
    agent = MiniClaudeAgent(
        config_path=config_path,
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=runtime_data_root,
    )
    try:
        response = agent.chat(instruction)
    finally:
        agent.shutdown()

    new_traces = set(data_paths.traces.glob("task_*.json")) - existing
    trace_path = max(new_traces, key=lambda p: p.stat().st_mtime_ns) if new_traces else None
    if trace_path is None:
        return AgentRunResult(response, "TRACE_MISSING", None, None)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    return AgentRunResult(response, trace.get("final_status", "UNKNOWN"), trace_path, trace.get("task_id"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行一次非交互 MiniClaude 任务")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--instruction-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--result-file", type=Path)
    args = parser.parse_args(argv)
    result = run_agent_once(
        workspace_root=args.workspace,
        instruction=args.instruction_file.read_text(encoding="utf-8"),
        config_path=args.config,
        runtime_data_root=args.data_root,
    )
    summary = json.dumps({
        "final_response": result.final_response,
        "final_status": result.final_status,
        "trace_path": str(result.trace_path) if result.trace_path else None,
        "task_id": result.task_id,
    }, ensure_ascii=False)
    if args.result_file:
        args.result_file.write_text(summary + "\n", encoding="utf-8")
    else:
        print(summary)
    return 0 if result.trace_path is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
