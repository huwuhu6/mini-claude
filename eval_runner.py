#!/usr/bin/env python3
"""
eval_runner.py — WF-2 沙箱评测运行器（真实 Agent Loop）

自动扫描 sandbox/tasks/ 目录加载评测任务，为每个任务创建隔离影子工作区，
实例化真实 MiniClaudeAgent 执行 problem.md 中的任务，
运行 verify.py 子进程客观判定 Pass/Fail。
"""

from __future__ import annotations
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Any

# ── 确保 stdout 使用 UTF-8（Windows 编码脱敏）───────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 确保 src 包可导入 ──────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agent.mini_claude_agent import MiniClaudeAgent

# ── 评测归档目录（保持 tasks/ 只读）────────────────────────
_ARCHIVE_DIR = (
    _PROJECT_ROOT / "sandbox" / "eval_results" / "v2_baseline_fi_soft"
).resolve()


# ═══════════════════════════════════════════════════════════════
# 现场打捞（写入归档目录，不污染 tasks/ 只读用例）
# ═══════════════════════════════════════════════════════════════

def _salvage_trace(workspace_root: Path, archive_dir: Path, task_id: str) -> bool:
    """将 .workspace/.traces/ 中最新的 trace 命名归档。"""
    trace_dir = (workspace_root / ".traces").resolve()
    if not trace_dir.is_dir():
        return False
    trace_files = sorted(
        trace_dir.glob("task_*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not trace_files:
        return False
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{task_id}_v2_trace.json"
    shutil.copy2(trace_files[-1], dest)
    return True


def _salvage_transcripts(workspace_root: Path, archive_dir: Path, task_id: str) -> bool:
    """将 .workspace/.transcripts/ 完整复制到归档目录。"""
    src = (workspace_root / ".transcripts").resolve()
    if not src.is_dir():
        return False
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / f"{task_id}_v2_transcripts"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


# ═══════════════════════════════════════════════════════════════
# 运行器
# ═══════════════════════════════════════════════════════════════

def run_benchmark() -> list[dict[str, Any]]:
    """扫描 sandbox/tasks/，逐个执行评测任务。"""
    tasks_dir = (_PROJECT_ROOT / "sandbox" / "tasks").resolve()
    workspace_root = (_PROJECT_ROOT / "sandbox" / "runner" / ".workspace").resolve()
    results: list[dict[str, Any]] = []

    if not tasks_dir.is_dir():
        print(f"错误：任务目录不存在：{tasks_dir}")
        return results

    task_dirs = sorted([d for d in tasks_dir.iterdir() if d.is_dir()])

    for task_dir in task_dirs:
        problem_path = task_dir / "problem.md"
        verify_path = task_dir / "verify.py"
        baseline_dir = task_dir / "baseline"

        if not problem_path.exists() or not verify_path.exists():
            print(f"  跳过 {task_dir.name}：缺少 problem.md 或 verify.py")
            continue

        task_id = task_dir.name
        t_total_start = time.perf_counter()

        print(f"\n{'═' * 55}")
        print(f"  ▏任务 [{task_id}]")
        print(f"{'═' * 55}")

        # ── Setup：清空工作区 + 复制 baseline ─────────────
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)
        print(f"  ● 工作区已就绪：{workspace_root}")

        if baseline_dir.exists():
            for item in baseline_dir.iterdir():
                dst = workspace_root / item.name
                if item.is_file():
                    shutil.copy2(item, dst)
                elif item.is_dir():
                    shutil.copytree(item, dst)
            print("  ● baseline 文件已复制")

        # ── 运行真实 Agent ────────────────────────────────
        t_agent_start = time.perf_counter()
        agent = None
        try:
            prompt = problem_path.read_text(encoding="utf-8").strip()
            print(
                f"  ● 加载问题：{prompt[:80]}"
                f"{'…' if len(prompt) > 80 else ''}"
            )
            print(f"  ▷ 正在启动 Agent Loop（workspace={workspace_root.name}）…")

            agent = MiniClaudeAgent(
                workspace_root=workspace_root,
                workspace_confirmed=True,
            )
            response = agent.chat(prompt) or ""
            resp_preview = response.strip()[:120].replace("\n", " ")
            print(f"  ✔ Agent 返回（{len(response)} chars）：{resp_preview}…")
        except Exception as exc:
            print(f"  ✘ Agent 异常：{exc}")
        finally:
            if agent:
                try:
                    agent.shutdown()
                except Exception as exc:
                    print(f"  ⚠ shutdown 异常：{exc}")
        t_agent_end = time.perf_counter()
        agent_duration = round(t_agent_end - t_agent_start, 2)
        print(f"  ◇ Agent 耗时：{agent_duration}s")

        # ── Trace 现场打捞（写入归档，不污染用例目录） ─────
        salvaged_trace = _salvage_trace(workspace_root, _ARCHIVE_DIR, task_id)
        if salvaged_trace:
            print(f"  ● trace 已归档 → {_ARCHIVE_DIR / f'{task_id}_v2_trace.json'}")
        else:
            print("  ⚠ trace 未生成（.traces/ 为空或不存在）")

        salvaged_transcripts = _salvage_transcripts(workspace_root, _ARCHIVE_DIR, task_id)
        if salvaged_transcripts:
            print(f"  ● transcripts 已归档 → {_ARCHIVE_DIR / f'{task_id}_v2_transcripts/'}")

        # ── 执行 verify.py（独立子进程客观判定） ──────────
        t_verify_start = time.perf_counter()
        _env = {**os.environ, "PYTHONUTF8": "1"}
        try:
            verify_result = subprocess.run(
                ["python", str(verify_path.resolve())],
                cwd=str(workspace_root),
                capture_output=True,
                env=_env,
                timeout=60,
            )
            stdout_output = verify_result.stdout.decode(
                "utf-8", errors="replace"
            )
            stderr_output = verify_result.stderr.decode(
                "utf-8", errors="replace"
            )
            if stdout_output.strip():
                print(f"  → verify 输出：\n{stdout_output}")
            if stderr_output.strip():
                print(f"  → verify 错误：\n{stderr_output}")
            success = verify_result.returncode == 0
        except subprocess.TimeoutExpired:
            success = False
            print("  ✘ verify 执行超时")
        except Exception as exc:
            success = False
            print(f"  ✘ verify 异常：{exc}")
        t_verify_end = time.perf_counter()
        verify_duration = round(t_verify_end - t_verify_start, 2)

        # ── Teardown：清空工作区 ──────────────────────────
        if workspace_root.exists():
            shutil.rmtree(workspace_root)
            print("  ● 工作区已清理")

        t_total_end = time.perf_counter()
        result = {
            "timestamp":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id":             task_id,
            "success":             success,
            "trace_salvaged":      salvaged_trace,
            "agent_duration_s":    agent_duration,
            "verify_duration_s":   verify_duration,
            "total_duration_s":    round(t_total_end - t_total_start, 2),
        }
        results.append(result)

        status = "[PASS]" if success else "[FAIL]"
        trace_mark = "✔" if salvaged_trace else "✘"
        print(
            f"  {status} | agent={agent_duration}s | "
            f"verify={verify_duration}s | trace={trace_mark}"
        )

    return results


# ═══════════════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════════════

def print_report(results: list[dict[str, Any]]) -> None:
    """打印 Markdown 格式评测报告。"""
    print("\n## WF-2 沙箱评测结果\n")
    print("| 任务 | 结果 | Trace | Agent(s) | Verify(s) | 总耗时 |")
    print("|------|------|-------|----------|-----------|--------|")
    for r in results:
        check = "✅ PASS" if r["success"] else "❌ FAIL"
        trace_mark = "✔" if r.get("trace_salvaged") else "✘"
        print(
            f"| {r['task_id']} | {check} | {trace_mark} | "
            f"{r['agent_duration_s']} | {r['verify_duration_s']} | "
            f"{r['total_duration_s']}s |"
        )

    passed = sum(1 for r in results if r["success"])
    total = len(results)
    traced = sum(1 for r in results if r.get("trace_salvaged"))
    print(f"\n**{passed}/{total} 任务通过，{traced}/{total} trace 已打捞**\n")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = run_benchmark()
    if not results:
        print("没有可执行的任务。请检查 sandbox/tasks/ 目录。")
        sys.exit(1)
    print_report(results)
