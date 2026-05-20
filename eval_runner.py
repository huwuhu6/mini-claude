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
import json
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

# ── 评测持久化路径 ─────────────────────────────────────
_ARCHIVE_DIR = (
    _PROJECT_ROOT / "sandbox" / "eval_results" / "v3_circuit_breaker"
).resolve()
_ARCHIVE_VERSION = _ARCHIVE_DIR.name
_MATRIX_DB_PATH = (
    _PROJECT_ROOT / "sandbox" / "eval_results" / "matrix_db.json"
).resolve()
_BENCHMARKS_MD_PATH = (
    _PROJECT_ROOT / "docs" / "benchmarks.md"
).resolve()


# ═══════════════════════════════════════════════════════════════
# CLI 过滤
# ═══════════════════════════════════════════════════════════════

def _resolve_task_filters() -> list[str]:
    """解析 sys.argv 获取任务名过滤条件。空列表 = 运行全部。"""
    return [a for a in sys.argv[1:] if a.strip()]


# ═══════════════════════════════════════════════════════════════
# matrix_db 持久化（展现与存储分离）
# ═══════════════════════════════════════════════════════════════

def _load_matrix_db() -> dict:
    if _MATRIX_DB_PATH.exists():
        return json.loads(_MATRIX_DB_PATH.read_text(encoding="utf-8"))
    return {}


def _save_to_matrix_db(task_id: str, metrics: dict) -> None:
    """增量追加或更新当前版本任务的评测记录。"""
    db = _load_matrix_db()
    if _ARCHIVE_VERSION not in db:
        db[_ARCHIVE_VERSION] = {}
    db[_ARCHIVE_VERSION][task_id] = metrics
    _MATRIX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MATRIX_DB_PATH.write_text(
        json.dumps(db, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_benchmarks_md() -> None:
    """读取 matrix_db.json，自动渲染 docs/benchmarks.md。"""
    db = _load_matrix_db()
    if not db:
        return

    lines = [
        "# Benchmarks",
        "",
        "> 自动由 eval_runner.py 生成 — 请勿手动编辑",
        "",
    ]
    versions = sorted(db.keys())
    for ver in versions:
        tasks = db[ver]
        lines.append(f"## {ver}")
        lines.append("")
        lines.append("| 任务 | 状态 | Token | 轮次 | Agent 终态 | 耗时 |")
        lines.append("|------|------|-------|------|------------|------|")
        for tid in sorted(tasks.keys()):
            m = tasks[tid]
            status = "✅ PASS" if m.get("verify_status") == "PASS" else "❌ FAIL"
            lines.append(
                f"| {tid} | {status} | {m.get('tokens', '—')} | "
                f"{m.get('total_turns', '—')} | {m.get('agent_status', '—')} | "
                f"{m.get('duration_s', '—')}s |"
            )
        lines.append("")

    # 跨版本对照表
    if len(versions) >= 2:
        lines.append("## 跨版本对照")
        lines.append("")
        header = "| 指标 | " + " | ".join(versions) + " |"
        sep = "|------|" + "|".join("---" for _ in versions) + "|"
        lines.append(header)
        lines.append(sep)

        all_tasks: set[str] = set()
        for ver in versions:
            all_tasks.update(db[ver].keys())

        for tid in sorted(all_tasks):
            row = [f"**{tid}**"]
            for ver in versions:
                m = db[ver].get(tid, {})
                if m:
                    row.append(
                        f"PASS={m.get('verify_status','?')} / "
                        f"{m.get('tokens','?')}tok / "
                        f"{m.get('agent_status','?')}"
                    )
                else:
                    row.append("—")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    _BENCHMARKS_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _BENCHMARKS_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ● benchmarks.md 已自动渲染 → {_BENCHMARKS_MD_PATH}")


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
    dest = archive_dir / f"{task_id}_v3_trace.json"
    shutil.copy2(trace_files[-1], dest)
    return True


def _salvage_transcripts(workspace_root: Path, archive_dir: Path, task_id: str) -> bool:
    """将 .workspace/.transcripts/ 完整复制到归档目录。"""
    src = (workspace_root / ".transcripts").resolve()
    if not src.is_dir():
        return False
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / f"{task_id}_v3_transcripts"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return True


# ═══════════════════════════════════════════════════════════════
# 运行器
# ═══════════════════════════════════════════════════════════════

def run_benchmark(task_filters: list[str] | None = None) -> list[dict[str, Any]]:
    """扫描 sandbox/tasks/，逐个执行评测任务。

    Args:
        task_filters: 可选任务名列表，仅运行匹配的任务。
    """
    tasks_dir = (_PROJECT_ROOT / "sandbox" / "tasks").resolve()
    workspace_root = (_PROJECT_ROOT / "sandbox" / "runner" / ".workspace").resolve()
    results: list[dict[str, Any]] = []

    if not tasks_dir.is_dir():
        print(f"错误：任务目录不存在：{tasks_dir}")
        return results

    task_dirs = sorted([d for d in tasks_dir.iterdir() if d.is_dir()])

    # ── CLI 精准过滤 ──────────────────────────────────────
    if task_filters:
        matched = [td for td in task_dirs if td.name in task_filters]
        if not matched:
            names = [td.name for td in task_dirs]
            print(f"错误：未找到匹配的任务。")
            print(f"  过滤条件: {task_filters}")
            print(f"  可用任务: {names}")
            sys.exit(1)
        task_dirs = matched

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
            print(f"  ● trace 已归档 → {_ARCHIVE_DIR / f'{task_id}_v3_trace.json'}")
        else:
            print("  ⚠ trace 未生成（.traces/ 为空或不存在）")

        salvaged_transcripts = _salvage_transcripts(workspace_root, _ARCHIVE_DIR, task_id)
        if salvaged_transcripts:
            print(f"  ● transcripts 已归档 → {_ARCHIVE_DIR / f'{task_id}_v2_transcripts/'}")

        # ── 从已归档的 trace 读取 Agent 终态 ──────────────
        trace_summary: dict[str, Any] = {
            "total_turns": 0, "total_tokens": 0, "final_status": "UNKNOWN",
        }
        if salvaged_trace:
            try:
                trace_path = _ARCHIVE_DIR / f"{task_id}_v3_trace.json"
                raw = json.loads(trace_path.read_text(encoding="utf-8"))
                trace_summary["total_turns"] = raw.get("total_turns", 0)
                trace_summary["total_tokens"] = raw.get("total_tokens", 0)
                trace_summary["final_status"] = raw.get("final_status", "UNKNOWN")
            except Exception:
                pass

        # ── P0.4 熔断状态泄漏修复 ─────────────────────────
        is_circuit_broken = trace_summary["final_status"] == "CIRCUIT_BROKEN"
        if is_circuit_broken:
            if workspace_root.exists():
                shutil.rmtree(workspace_root)
            workspace_root.mkdir(parents=True, exist_ok=True)
            print("  ● 熔断状态泄漏修复：工作区已物理清空，verify.py 将看到废墟")

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
        agent_status = trace_summary["final_status"]
        verify_status = "PASS" if success else "FAIL"

        result = {
            "timestamp":           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id":             task_id,
            "success":             success,
            "trace_salvaged":      salvaged_trace,
            "agent_duration_s":    agent_duration,
            "verify_duration_s":   verify_duration,
            "total_duration_s":    round(t_total_end - t_total_start, 2),
            "total_turns":         trace_summary["total_turns"],
            "total_tokens":        trace_summary["total_tokens"],
            "agent_status":        agent_status,
            "verify_status":       verify_status,
            "is_circuit_broken":   is_circuit_broken,
        }
        results.append(result)

        # ── 增量保存到 matrix_db.json ─────────────────────
        _save_to_matrix_db(task_id, {
            "total_turns":   trace_summary["total_turns"],
            "tokens":        trace_summary["total_tokens"],
            "verify_status": verify_status,
            "agent_status":  agent_status,
            "duration_s":    agent_duration,
        })

        status = "[PASS]" if success else "[FAIL]"
        cb_mark = " 🔴CB" if is_circuit_broken else ""
        trace_mark = "✔" if salvaged_trace else "✘"
        print(
            f"  {status}{cb_mark} | agent={agent_duration}s | "
            f"verify={verify_duration}s | trace={trace_mark} | "
            f"{trace_summary['total_turns']}turns | "
            f"{trace_summary['final_status']}"
        )

    return results


# ═══════════════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════════════

def print_report(results: list[dict[str, Any]]) -> None:
    """打印 Markdown 格式评测报告。"""
    print("\n## WF-2 沙箱评测结果\n")
    print("| 任务 | 结果 | CB | Trace | Turns | Agent 终态 | Token | Agent(s) | Verify(s) |")
    print("|------|------|----|-------|-------|------------|-------|----------|-----------|")
    for r in results:
        check = "✅ PASS" if r["success"] else "❌ FAIL"
        cb = "🔴" if r.get("is_circuit_broken") else "—"
        trace_mark = "✔" if r.get("trace_salvaged") else "✘"
        print(
            f"| {r['task_id']} | {check} | {cb} | {trace_mark} | "
            f"{r.get('total_turns', '—')} | {r.get('agent_status', '—')} | "
            f"{r.get('total_tokens', '—')} | "
            f"{r['agent_duration_s']}s | {r['verify_duration_s']}s |"
        )

    passed = sum(1 for r in results if r["success"])
    total = len(results)
    traced = sum(1 for r in results if r.get("trace_salvaged"))
    cb_count = sum(1 for r in results if r.get("is_circuit_broken"))
    print(f"\n**{passed}/{total} 任务通过，{traced}/{total} trace 已打捞，"
          f"{cb_count} 次熔断**\n")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    filters = _resolve_task_filters()
    if filters:
        print(f"  ▷ 单任务模式：{filters}")
    results = run_benchmark(task_filters=filters)
    if not results:
        print("没有可执行的任务。请检查 sandbox/tasks/ 目录。")
        sys.exit(1)
    print_report(results)
    _render_benchmarks_md()
