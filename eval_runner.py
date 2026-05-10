#!/usr/bin/env python3
"""
eval_runner.py — Automated benchmark for MiniClaudeAgent.

Runs a set of benchmark tasks against the agent, collects metrics
(turns, tokens, errors, duration), and prints a Markdown report.
Results are also appended to logs/eval_results.csv.
"""

from __future__ import annotations
import sys
import time
import csv
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional, List
from unittest import mock

# ── Ensure stdout uses UTF-8 on Windows ────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Ensure src package is importable ──────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agent.mini_claude_agent import MiniClaudeAgent
from core.tools.base_tools import BaseTools
from core.evaluation import TraceAnalyzer


# ═══════════════════════════════════════════════════════════════
# EvalTask Dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class EvalTask:
    id: str
    name: str
    prompt: str
    verify: Callable[[Path, str], bool]
    cleanup: Callable[[Path], Any]
    setup:      Optional[Callable[[Path], Any]] = None
    max_turns:  int = 0   # 0 = no limit; >0 = hard cap, exceeded → FAIL
    min_turns:  int = 0   # 0 = no minimum
    compression_threshold: int = 0   # 0 = no override
    fault_inject: Optional[Callable[[], Any]] = None
    # ^ Returns a context-manager that patches tools for fault injection.
    #   The runner enters the context before agent.chat() and exits after.
    prompts: Optional[List[str]] = None
    # ^ Multi-turn: if set, the runner calls agent.chat() once per string
    #   on the SAME agent instance.  ``prompt`` is ignored in this mode.
    #   The ``verify`` callback receives the LAST response.
    post_agent_init: Optional[Callable[[Any], None]] = None
    # ^ Called immediately after agent creation, receives the agent instance.
    #   Use for per-task configuration that needs the live agent object
    #   (e.g. lowering subagent iteration caps for fast-fail tests).


# ═══════════════════════════════════════════════════════════════
# Fault-Injection Helpers (Task F)
# ═══════════════════════════════════════════════════════════════

def _patch_subagent_for_fast_fail(agent) -> None:
    """Monkey-patch subagent_manager.run to inject fail_after=3 for Task I."""
    _original_run = agent.subagent_manager.run

    def _fast_fail_run(prompt, agent_type=None, workdir=None,
                       max_iterations=30, **kw):
        return _original_run(
            prompt, agent_type=agent_type or "general-purpose",
            workdir=workdir, max_iterations=max_iterations,
            fail_after_tool_calls=1,
        )

    agent.subagent_manager.run = _fast_fail_run


def _task_f_fault_inject():
    """Context manager: first read_file call crashes, subsequent calls recover."""
    _original_read = BaseTools.read_file
    _fault_counter = [0]

    def _injected(self_, path, limit=None):
        _fault_counter[0] += 1
        if _fault_counter[0] == 1:
            raise Exception(
                "CRITICAL SYSTEM FAILURE: Disk read timeout "
                "(Error 0x8007045D)"
            )
        return _original_read(self_, path, limit)

    return mock.patch.object(BaseTools, 'read_file', _injected)


# ═══════════════════════════════════════════════════════════════
# Benchmark Task Definitions
# ═══════════════════════════════════════════════════════════════

BENCHMARK_TASKS: List[EvalTask] = [
    EvalTask(
        id="A",
        name="File IO",
        prompt=(
            '在当前目录创建一个名为 "eval_test.txt" 的文件，'
            "内容为 'hello benchmark'。"
        ),
        verify=lambda wd, _r: (
            (wd / "eval_test.txt").exists()
            and "hello benchmark" in (wd / "eval_test.txt").read_text()
        ),
        cleanup=lambda wd: (wd / "eval_test.txt").unlink(missing_ok=True),
        max_turns=4,
    ),
    EvalTask(
        id="B",
        name="环境探针",
        prompt=(
            "读取操作系统的环境变量，找出 PATH 变量包含多少个路径，"
            "将数字写入 'path_count.txt'。"
        ),
        verify=lambda wd, _r: (
            (wd / "path_count.txt").exists()
            and (wd / "path_count.txt").read_text().strip().isdigit()
        ),
        cleanup=lambda wd: (wd / "path_count.txt").unlink(missing_ok=True),
        max_turns=6,
    ),
    EvalTask(
        id="C",
        name="容错测试",
        prompt=(
            "读取一个不存在的文件 'ghost.md'，处理报错"
            "并输出一段话说明文件找不到。"
        ),
        verify=lambda wd, resp: (
            any(kw in resp.lower() for kw in
                ("找不到", "not found", "不存在", "does not exist",
                 "no such file", "无法", "错误", "error"))
        ),
        cleanup=lambda wd: None,
        max_turns=3,
    ),
    EvalTask(
        id="D",
        name="长上下文记忆与压缩容错",
        prompt=(
            "你的初始密钥是 [SECRET_TOKEN_XYZ_998]。"
            "接下来，请连续调用 4 次 bash 工具，分别 echo 数字 1 到 4（每次一行）。"
            "完成这 4 次无意义的调用后，请使用 write_file 工具，"
            "将你最初得到的密钥写入到 memory_test.txt 文件中。"
        ),
        verify=lambda wd, _r: (
            (wd / "memory_test.txt").exists()
            and "[SECRET_TOKEN_XYZ_998]" in (wd / "memory_test.txt").read_text()
        ),
        cleanup=lambda wd: (wd / "memory_test.txt").unlink(missing_ok=True),
        compression_threshold=300,
        max_turns=10,
    ),
    EvalTask(
        id="E",
        name="子代理委托测试",
        prompt=(
            "请创建一个子代理（使用 task 工具），让子代理去执行 bash 命令 "
            "'echo SUBAGENT_ALIVE > sub_test.txt'。主代理请等待子代理完成。"
        ),
        verify=lambda wd, _r: (
            (wd / "sub_test.txt").exists()
            and "SUBAGENT_ALIVE" in (wd / "sub_test.txt").read_text()
        ),
        cleanup=lambda wd: (wd / "sub_test.txt").unlink(missing_ok=True),
        max_turns=6,
    ),
    EvalTask(
        id="F",
        name="底层异常与重试容错测试",
        prompt=(
            "请读取当前目录下的 target.txt 文件。如果遇到任何系统报错，"
            "不要放弃，请在一句话内分析报错原因并立即重试读取。"
            "成功读取后，将内容写入 recovered.txt。"
        ),
        verify=lambda wd, _r: (
            (wd / "recovered.txt").exists()
            and (wd / "target.txt").exists()
            and (wd / "recovered.txt").read_text()
            == (wd / "target.txt").read_text()
        ),
        cleanup=lambda wd: (
            (wd / "target.txt").unlink(missing_ok=True),
            (wd / "recovered.txt").unlink(missing_ok=True),
        ),
        setup=lambda wd: (wd / "target.txt").write_text(
            "FAULT_INJECTION_TARGET_42"
        ),
        min_turns=3,
        max_turns=8,
        fault_inject=_task_f_fault_inject,
    ),

    # ── Task G: Multi-turn dialogue degradation ─────────────
    EvalTask(
        id="G",
        name="多轮对话退化测试",
        prompt="",  # unused — prompts list takes precedence
        verify=lambda wd, _r: (
            (wd / "recall.txt").exists()
            and "42" in (wd / "recall.txt").read_text()
        ),
        cleanup=lambda wd: (wd / "recall.txt").unlink(missing_ok=True),
        max_turns=12,
        prompts=[
            "请记住数字 42，这个数字非常重要。只需回复'已记住'。",
            "用 bash 工具执行 echo 1 到 5，每次 echo 一个数字。",
            "把你最初记住的数字写入 recall.txt 文件。",
        ],
    ),

    # ── Task H: Parameter hallucination self-healing ─────────
    EvalTask(
        id="H",
        name="参数幻觉自愈测试",
        prompt=(
            "请读取 real.txt 的内容。"
            "如果工具报错说文件不存在或参数不对，请分析错误原因，纠正后重试。"
        ),
        verify=lambda wd, resp: (
            "secret42" in resp
            or "secret42" in (wd / "real_file.txt").read_text()
            if (wd / "real_file.txt").exists()
            else False
        ),
        cleanup=lambda wd: (wd / "real_file.txt").unlink(missing_ok=True),
        setup=lambda wd: (wd / "real_file.txt").write_text("secret42"),
        max_turns=5,
    ),

    # ── Task I: Rollback state leak test ─────────────────────
    EvalTask(
        id="I",
        name="回滚状态防泄露测试",
        prompt=(
            "使用 task 工具创建一个子代理（agent_type='general-purpose'）。"
            "子代理必须严格按顺序执行："
            "第1步，使用 write_file 创建 data.jsonl，写入 'dirty_data_entry'。"
            "第2步，使用 bash 尝试执行 rm -rf /（此命令将被沙箱拦截）。"
            "主代理请等待子代理完成。"
        ),
        verify=lambda wd, _resp: not (wd / "data.jsonl").exists(),
        cleanup=lambda wd: (wd / "data.jsonl").unlink(missing_ok=True),
        max_turns=15,
        # Force the subagent to fail after 1 tool call so the shadow
        # workspace triggers ROLLBACK (not COMMIT).
        post_agent_init=_patch_subagent_for_fast_fail,
    ),

    # ── Task J: Loop Guard & Forced Reflection test ───────────
    EvalTask(
        id="J",
        name="死循环打破与反思测试",
        prompt=(
            "在当前目录下有一个 compile.py 脚本。"
            "请使用 bash 工具运行它，直到成功为止。"
            "如果运行失败，请仔细分析错误信息，找出让脚本成功运行的方法。"
        ),
        verify=lambda wd, resp: (
            (wd / "build_ok.txt").exists()
            and "BUILD SUCCESS" in (wd / "build_ok.txt").read_text()
        ),
        cleanup=lambda wd: (
            (wd / "compile.py").unlink(missing_ok=True),
            (wd / "build_ok.txt").unlink(missing_ok=True),
        ),
        setup=lambda wd: (wd / "compile.py").write_text(
            'import sys\n'
            'if "--force-override" not in sys.argv:\n'
            '    print("Error: Missing dependency. Try again.", file=sys.stderr)\n'
            '    sys.exit(1)\n'
            'from pathlib import Path\n'
            'Path("build_ok.txt").write_text("BUILD SUCCESS")\n'
            'print("BUILD SUCCESS")\n'
        ),
        max_turns=8,
        min_turns=2,  # must at least try, hit loop guard, then reflect
    ),
]


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════

def run_benchmark(workdir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Execute all benchmark tasks and return a list of result dicts."""
    workdir = workdir or _PROJECT_ROOT
    results: list[dict[str, Any]] = []

    for task in BENCHMARK_TASKS:
        print(f"\n{'─' * 50}")
        print(f"  Task {task.id} [{task.name}]: {task.prompt[:60]}...")
        print(f"{'─' * 50}")

        # ── Task setup ──────────────────────────────────────
        if task.setup:
            task.setup(workdir)
            print("  (setup: prerequisite files created)")

        # ── Launch agent ────────────────────────────────────
        agent = MiniClaudeAgent(workdir=workdir)

        if task.compression_threshold:
            agent.compressor.token_threshold = task.compression_threshold
            print(f"  (compression threshold: {task.compression_threshold} tokens)")

        if task.post_agent_init:
            task.post_agent_init(agent)

        response = ""
        t_start = time.perf_counter()

        # ── Snapshot existing trace files before execution ──
        trace_dir = workdir / ".traces"
        before_traces: set = set()
        if trace_dir.is_dir():
            before_traces = {str(p) for p in trace_dir.glob("task_*.json")}

        # ── Execute (with optional fault injection) ─────────
        executor = task.fault_inject if task.fault_inject else _null_context
        with executor():
            try:
                if task.prompts:
                    # Multi-turn: same agent, sequential prompts
                    for p in task.prompts[:-1]:
                        agent.chat(p)
                    response = agent.chat(task.prompts[-1]) or ""
                else:
                    response = agent.chat(task.prompt) or ""
                success = task.verify(workdir, response)
            except Exception as exc:
                response = f"[Agent crash: {exc}]"
                success = False

        t_end = time.perf_counter()

        # ── Detect new trace from this execution ────────────
        trace_metrics: dict = {}
        if trace_dir.is_dir():
            new_traces = [
                p for p in trace_dir.glob("task_*.json")
                if str(p) not in before_traces
            ]
            if new_traces:
                latest_trace = max(new_traces, key=lambda p: p.stat().st_mtime)
                analyzer = TraceAnalyzer(workdir)
                if analyzer.load_trace(latest_trace):
                    trace_metrics = analyzer.compute_metrics()

        # ── Collect metrics ─────────────────────────────────
        m = agent.last_metrics or {}
        result = {
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id":      task.id,
            "task_name":    task.name,
            "success":      success,
            "turns":        m.get("turns", 0),
            "total_tokens": m.get("total_tokens", 0),
            "api_errors":   m.get("api_errors", 0),
            "duration_s":   round(t_end - t_start, 2),
            # Trace-derived process-quality metrics
            "duplicate_ratio":      trace_metrics.get("duplicate_tool_ratio"),
            "degradation_score":    trace_metrics.get("degradation_score"),
            "reflections":          trace_metrics.get("reflection_count"),
            "rollback":             trace_metrics.get("rollback_occurred"),
        }

        # ── Enforce hard turn caps ──────────────────────────
        if task.max_turns and result["turns"] > task.max_turns:
            result["success"] = False
            print(f"  (MAX_TURNS exceeded: {result['turns']} > {task.max_turns})")

        if task.min_turns and result["turns"] < task.min_turns:
            result["success"] = False
            print(f"  (MIN_TURNS not met: {result['turns']} < {task.min_turns})")

        results.append(result)

        # ── Cleanup + shutdown ──────────────────────────────
        try:
            task.cleanup(workdir)
        except Exception:
            pass
        try:
            agent.shutdown()
        except Exception:
            pass

        # Live feedback
        status = "[PASS]" if result["success"] else "[FAIL]"
        dup_str = _fmt(result.get("duplicate_ratio"))
        deg_str = _fmt(result.get("degradation_score"), fmt_float=True)
        print(f"  {status} | turns={result['turns']} | "
              f"dup={dup_str} | degrad={deg_str} | "
              f"tokens={result['total_tokens']} | "
              f"errors={result['api_errors']} | "
              f"duration={result['duration_s']}s")

    return results


# ═══════════════════════════════════════════════════════════════
# Null context-manager (no-op for normal tasks)
# ═══════════════════════════════════════════════════════════════

from contextlib import contextmanager

@contextmanager
def _null_context():
    yield


# ═══════════════════════════════════════════════════════════════
# Report Output
# ═══════════════════════════════════════════════════════════════

def _fmt(val, fmt_float=False):
    """Format a metric cell: None → '—', bool → Y/N, float → formatted."""
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Y" if val else "N"
    if isinstance(val, float):
        if fmt_float:
            return f"{val:.1f}"
        return f"{val:.4f}"
    return str(val)


def print_markdown_table(results: list[dict[str, Any]]):
    """Print a Markdown-formatted results table to stdout."""
    print("\n## Benchmark Results (Trace-Driven Evaluation)\n")
    print("| Task | Pass | Turns | Dup Ratio | Degrad(0-100) | Refl | Rollback | Duration |")
    print("|------|------|-------|-----------|---------------|------|----------|----------|")
    for r in results:
        check = "Y" if r["success"] else "N"
        dup = _fmt(r.get("duplicate_ratio"))
        deg = _fmt(r.get("degradation_score"), fmt_float=True)
        refl = _fmt(r.get("reflections"))
        roll = _fmt(r.get("rollback"))
        print(f"| {r['task_id']} {r['task_name']} | {check} | "
              f"{r['turns']} | {dup} | {deg} | {refl} | {roll} | "
              f"{r['duration_s']} |")

    passed = sum(1 for r in results if r["success"])
    total = len(results)
    avg_dur = sum(r["duration_s"] for r in results) / max(total, 1)
    avg_deg = [r.get("degradation_score") for r in results
               if r.get("degradation_score") is not None]
    avg_deg_str = f"{sum(avg_deg)/len(avg_deg):.1f}" if avg_deg else "—"
    print(f"\n**{passed}/{total} tasks passed** — "
          f"avg duration: {avg_dur:.2f}s | "
          f"avg degradation: {avg_deg_str}\n")


def append_csv(results: list[dict[str, Any]], csv_path: Path):
    """Append results to a CSV file (create with header if new)."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        if write_header:
            writer.writeheader()
        writer.writerows(results)
    print(f"Results appended to {csv_path}")


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = run_benchmark()
    if not results:
        print("No results — benchmark did not produce any output.")
        sys.exit(1)

    print_markdown_table(results)
    append_csv(results, _PROJECT_ROOT / "logs" / "eval_results.csv")
