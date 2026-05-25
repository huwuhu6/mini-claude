#!/usr/bin/env python3
"""
eval_runner.py — WF-2 工业级沙箱评测引擎

用法:
  py eval_runner.py                           # 使用默认版本 "baseline"
  py eval_runner.py --version v4_fault        # 归档到 sandbox/eval_results/v4_fault/
  py eval_runner.py -v baseline               # -v 是 --version 的简写
  py eval_runner.py -t task_001              # 只跑指定任务（快速调试）
  py eval_runner.py -t task_001,task_002     # 逗号分隔多个任务

工作流:
  1. 解析 CLI 参数（argparse --version/-v），确定版本名称
  2. 扫描 sandbox/tasks/ 下所有内含 config.json 的 case 文件夹
  3. 为每个 case 创建隔离沙箱 sandbox/shadow_workspace/，复制 baseline 纯净文件
  4. 实例化 MiniClaudeAgent，执行 prompt
  5. 动态路由断言：若 config 指定 verify_script_file，subprocess 执行验证
  6. 黄雀在后解析器：读取原始 Trace JSON，注入 5 维工业级度量指标
  7. 数据归档：增强后的 Trace 存入 sandbox/eval_results/{version}/，Transcripts 同步归档
  8. 毁灭现场：gc.collect + 多级暴力清除，确保 100% 无残留
"""

from __future__ import annotations
import argparse
import gc
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

# ── 确保 stdout 使用 UTF-8（Windows 编码脱敏）───────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 确保 src 包可导入 ──────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
_src = str(BASE_DIR / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from agent.mini_claude_agent import MiniClaudeAgent

# ═══════════════════════════════════════════════════════════════
# 路径向内锁死 —— 所有评测行为路由到 sandbox/ 内部
# ═══════════════════════════════════════════════════════════════
TASKS_ROOT = BASE_DIR / "sandbox" / "tasks"
SHADOW_WORKSPACE = BASE_DIR / "sandbox" / "shadow_workspace"
OUTPUT_ROOT = BASE_DIR / "sandbox" / "eval_results"
_CONFIG_PATH = BASE_DIR / "configs" / "default.yaml"

# 自愈收敛速度统计时关注的 bash 类工具名
_BASH_LIKE_TOOLS = frozenset({"bash", "execute_command", "run_command"})


# ═══════════════════════════════════════════════════════════════
# 暴力毁灭现场 —— 针对 Windows 句柄锁的多级强删
# ═══════════════════════════════════════════════════════════════

def _hard_rmtree(path: Path) -> None:
    """多级暴力删除，确保 Windows 下句柄残留也能 100% 清除。"""
    if not path.exists():
        return

    # 第 1 级：标准删除
    try:
        shutil.rmtree(str(path))
        return
    except PermissionError:
        pass
    except Exception:
        pass

    # 第 2 级：忽略错误重试
    try:
        shutil.rmtree(str(path), ignore_errors=True)
        # 给 OS 一点时间释放
        time.sleep(1)
        if not path.exists():
            return
    except Exception:
        pass

    # 第 3 级：Windows 原生 rd /s /q 强杀
    if sys.platform == "win32":
        os.system(f'rd /s /q "{path}" 2>nul')
        time.sleep(0.5)
        if not path.exists():
            return

    # 第 4 级：最终尝试——重命名后删除
    try:
        import random
        dead = path.parent / f".dead_{path.name}_{random.randint(10000, 99999)}"
        path.rename(dead)
        shutil.rmtree(str(dead), ignore_errors=True)
    except Exception:
        pass

    if path.exists():
        print(f"  ⚠ 警告: shadow_workspace 未能完全清除，请手动删除: {path}")


# ═══════════════════════════════════════════════════════════════
# 环境治理
# ═══════════════════════════════════════════════════════════════

def _enforce_utf8_env() -> None:
    """在 os.environ 中强行注入 UTF-8 编码，防止 Windows 解码假报错。"""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")


# ═══════════════════════════════════════════════════════════════
# Trace 搜寻
# ═══════════════════════════════════════════════════════════════

def _find_latest_trace(workspace: Path) -> Optional[Path]:
    """在 workspace/.traces/ 中按修改时间查找最新的 trace JSON 文件。"""
    trace_dir = workspace / ".traces"
    if not trace_dir.is_dir():
        return None
    candidates = sorted(
        trace_dir.glob("task_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ═══════════════════════════════════════════════════════════════
# 黄雀在后：多维指标对账
# ═══════════════════════════════════════════════════════════════

def _compute_metrics(trace_data: dict) -> dict:
    """从原始 trace 中解析 5 维工业级度量指标。"""
    turns = trace_data.get("turns", [])
    total_tool_calls = 0
    success_tool_calls = 0
    loop_guard_blocked = 0
    last_bash_fail_iteration = -1
    total_iterations = len(turns)

    for turn in turns:
        iteration = turn.get("iteration", 0)
        tools = turn.get("tools", [])
        for tc in tools:
            total_tool_calls += 1
            if tc.get("success", True):
                success_tool_calls += 1
            if tc.get("loop_guard_blocked", False):
                loop_guard_blocked += 1
            if not tc.get("success", True) and tc.get("tool_name", "") in _BASH_LIKE_TOOLS:
                last_bash_fail_iteration = iteration

    # 工具命中率：成功调用 / 总调用（无工具调用时默认为 1.0）
    tool_call_precision = (success_tool_calls / total_tool_calls) if total_tool_calls > 0 else 1.0

    # 看门狗熔断率：被 LoopGuard 拦截的调用 / 总调用
    loop_guard_blocking_rate = (loop_guard_blocked / total_tool_calls) if total_tool_calls > 0 else 0.0

    # 自愈收敛速度：最后一次 bash 失败后，Agent 又花了多少轮才完成任务
    if last_bash_fail_iteration >= 0:
        self_healing_convergence = max(0, total_iterations - last_bash_fail_iteration - 1)
    else:
        self_healing_convergence = 0

    return {
        "tool_call_precision": round(tool_call_precision, 4),
        "loop_guard_blocking_rate": round(loop_guard_blocking_rate, 4),
        "self_healing_convergence_speed": self_healing_convergence,
    }


# ═══════════════════════════════════════════════════════════════
# 沙箱生命周期
# ═══════════════════════════════════════════════════════════════

def _prepare_sandbox(baseline_dir: Path) -> None:
    """清空并重建 shadow_workspace/，复制 baseline 文件。"""
    _hard_rmtree(SHADOW_WORKSPACE)
    SHADOW_WORKSPACE.mkdir(parents=True, exist_ok=True)
    print("  📁 shadow_workspace 已创建")

    if baseline_dir.is_dir():
        for item in baseline_dir.iterdir():
            dst = SHADOW_WORKSPACE / item.name
            if item.is_file():
                shutil.copy2(item, dst)
            elif item.is_dir():
                shutil.copytree(item, dst)
        file_count = sum(1 for _ in baseline_dir.rglob("*") if _.is_file())
        print(f"  📁 baseline 已复制 ({file_count} files)")


def _run_agent(prompt: str) -> tuple[Optional[Path], float]:
    """实例化 Agent 并执行 prompt，返回 (最新_trace_path, agent执行耗时)。"""
    agent = None
    try:
        agent = MiniClaudeAgent(
            config_path=_CONFIG_PATH,
            workspace_root=SHADOW_WORKSPACE,
            workspace_confirmed=True,
        )
        print("  🤖 Agent 已初始化，正在执行 prompt…")
        t0 = time.perf_counter()
        agent.chat(prompt)
        elapsed = round(time.perf_counter() - t0, 2)
        print(f"  ✅ Agent 执行完毕，耗时 {elapsed}s")
        return _find_latest_trace(SHADOW_WORKSPACE), elapsed
    except Exception as exc:
        print(f"  ❌ Agent 异常: {exc}")
        return None, 0.0
    finally:
        if agent:
            try:
                agent.shutdown()
            except Exception as exc:
                print(f"  ⚠ shutdown 异常: {exc}")


# ═══════════════════════════════════════════════════════════════
# 单 Case 运行器
# ═══════════════════════════════════════════════════════════════

def run_case(case_dir: Path, version: str) -> dict[str, Any]:
    """运行单个评测 case，返回结果字典。"""
    case_id = case_dir.name
    config_file = case_dir / "config.json"
    baseline_dir = case_dir / "baseline"

    if not config_file.exists():
        return {"case_id": case_id, "verify_status": "NO_CONFIG",
                "agent_duration_s": 0.0, "total_latency_s": 0.0}

    config = json.loads(config_file.read_text(encoding="utf-8"))
    prompt = config["prompt"]
    verify_script_name: Optional[str] = config.get("verify_script_file")

    print(f"\n{'=' * 60}")
    print(f"  🚀 Case: {case_id}")
    print(f"{'=' * 60}")

    t_start = time.perf_counter()

    # ── Step 1: 沙箱准备 ─────────────────────────────────
    _prepare_sandbox(baseline_dir)

    # ── Step 2: 启动 Agent ───────────────────────────────
    trace_path, agent_duration = _run_agent(prompt)

    # ── Step 3: 动态路由断言（黄雀在后验证） ───────────────
    verify_status = "FAILED"
    if verify_script_name and trace_path:
        script_src = case_dir / verify_script_name
        if script_src.exists():
            script_dst = SHADOW_WORKSPACE / verify_script_name
            try:
                shutil.copy2(script_src, script_dst)
                print(f"  📄 verify 脚本已复制: {verify_script_name}")
            except Exception as exc:
                print(f"  ⚠ verify 脚本复制失败: {exc}")

            _env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            try:
                if sys.platform == "win32":
                    subprocess.run(["chcp", "65001"], capture_output=True, shell=True)
                result = subprocess.run(
                    [sys.executable or "python", verify_script_name],
                    cwd=str(SHADOW_WORKSPACE),
                    capture_output=True,
                    env=_env,
                    timeout=120,
                )
                out = result.stdout.decode("utf-8", errors="replace")
                err = result.stderr.decode("utf-8", errors="replace")
                if out.strip():
                    print(f"  → verify stdout:\n{out}")
                if err.strip():
                    print(f"  → verify stderr:\n{err}")
                verify_status = "SUCCESS" if result.returncode == 0 else "FAILED"
                print(f"  {'✅' if verify_status == 'SUCCESS' else '❌'} verify: {verify_status} (rc={result.returncode})")
            except subprocess.TimeoutExpired:
                print("  ❌ verify 超时 (120s)")
                verify_status = "FAILED"
            except Exception as exc:
                print(f"  ❌ verify 异常: {exc}")
                verify_status = "CRASHED"
        else:
            print(f"  ⚠ verify_script_file='{verify_script_name}' 不存在于 case 目录")
    elif not verify_script_name:
        print("  ⏭ verify_script_file 为 null，跳过验证")
        verify_status = "SKIPPED"
    else:
        print("  ⚠ verify_script_file 已定义但 trace 未生成，跳过验证")
        verify_status = "FAILED"

    # ── Step 4: 核心对账 —— 黄雀在后解析器 ─────────────────
    total_latency = round(time.perf_counter() - t_start, 2)
    metrics: dict[str, Any] = {}
    trace_data: dict[str, Any] = {}

    if trace_path and trace_path.exists():
        try:
            trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
            metrics = _compute_metrics(trace_data)

            # 注入 5 维工业级度量指标（不覆盖原生字段 total_tokens/total_turns）
            trace_data["eval_result"] = verify_status
            trace_data["total_latency_seconds"] = total_latency
            trace_data["tool_call_precision"] = metrics.get("tool_call_precision", 1.0)
            trace_data["loop_guard_blocking_rate"] = metrics.get("loop_guard_blocking_rate", 0.0)
            trace_data["self_healing_convergence_speed"] = metrics.get("self_healing_convergence_speed", 0)

            print(f"  📊 指标对账完成 — "
                  f"precision={trace_data['tool_call_precision']}, "
                  f"block_rate={trace_data['loop_guard_blocking_rate']}, "
                  f"heal_speed={trace_data['self_healing_convergence_speed']}")

            # ── 数据归档: trace ───────────────────────────
            report_dir = OUTPUT_ROOT / version
            report_dir.mkdir(parents=True, exist_ok=True)
            dest = report_dir / f"trace_{case_id}.json"
            dest.write_text(
                json.dumps(trace_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  💾 Trace 已归档: {dest.relative_to(BASE_DIR)}")

            # 删除原始 trace，避免沙箱残留
            trace_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  ❌ Trace 对账异常: {exc}")
    else:
        print("  ⚠ 未找到 Trace JSON，跳过指标对账与归档")

    # ── Step 5: 垃圾回收 + 句柄缓冲 + 暴力毁灭现场 ────────
    gc.collect()
    time.sleep(0.5)
    _hard_rmtree(SHADOW_WORKSPACE)
    print("  🧹 shadow_workspace 已清除")

    return {
        "case_id": case_id,
        "verify_status": verify_status,
        "agent_duration_s": agent_duration,
        "total_latency_s": total_latency,
        "total_tokens": trace_data.get("total_tokens"),
        "total_turns": trace_data.get("total_turns"),
        "tool_call_precision": trace_data.get("tool_call_precision"),
        "self_healing_convergence_speed": trace_data.get("self_healing_convergence_speed"),
        "loop_guard_blocking_rate": trace_data.get("loop_guard_blocking_rate"),
        "final_status": trace_data.get("final_status"),
    }


# ═══════════════════════════════════════════════════════════════
# 报告输出
# ═══════════════════════════════════════════════════════════════

def print_summary(results: list[dict], version: str) -> None:
    """打印控制台评测汇总（不再落盘 summary JSON，数据以 trace JSON 为准）。"""
    print(f"\n{'=' * 60}")
    print("  📊 评测汇总")
    print(f"{'=' * 60}")
    print(f"  {'Case':<24} {'Result':<10} {'Agent(s)':<10} {'Total(s)':<10}")
    print(f"  {'─' * 54}")
    for r in results:
        icon = "✅" if r["verify_status"] == "SUCCESS" else "❌"
        print(f"  {icon} {r['case_id']:<22} {r['verify_status']:<10} "
              f"{r['agent_duration_s']:<10} {r['total_latency_s']:<10}")

    passed = sum(1 for r in results if r["verify_status"] == "SUCCESS")
    failed = sum(1 for r in results if r["verify_status"] == "FAILED")
    skipped = sum(1 for r in results if r["verify_status"] == "SKIPPED")
    print(f"\n  🏆 {passed}/{len(results)} 通过 | ❌ {failed} 失败 | "
          f"⏭ {skipped} 跳过")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    _enforce_utf8_env()

    parser = argparse.ArgumentParser(
        description="WF-2 沙箱评测引擎 — 工业级沙箱评测与多维指标对账",
    )
    parser.add_argument(
        "--version", "-v", type=str, default="baseline",
        help="版本名称，产物归档到 sandbox/eval_results/{version}/（默认: baseline）",
    )
    parser.add_argument(
        "--task", "-t", type=str, default=None,
        help="只运行指定任务（逗号分隔多个，如 task_001,task_002）。不指定则运行全部。",
    )
    args = parser.parse_args()
    version = args.version
    task_filter: set[str] | None = None if not args.task else {t.strip() for t in args.task.split(",")}

    print(f"  🔧 版本: {version}")
    print(f"  🔧 任务目录: {TASKS_ROOT}")
    print(f"  🔧 沙箱目录: {SHADOW_WORKSPACE}")
    print(f"  🔧 产物目录: {OUTPUT_ROOT / version}")

    if not TASKS_ROOT.is_dir():
        print(f"  ❌ 任务目录不存在: {TASKS_ROOT}")
        sys.exit(1)

    case_dirs = sorted([
        d for d in TASKS_ROOT.iterdir()
        if d.is_dir() and (d / "config.json").is_file()
    ])
    if not case_dirs:
        print("  ❌ 未找到包含 config.json 的 case 目录")
        sys.exit(1)

    # ── 可选过滤 ──────────────────────────────────────────
    if task_filter is not None:
        matched = [d for d in case_dirs if d.name in task_filter]
        missed = task_filter - {d.name for d in case_dirs}
        if missed:
            print(f"  ⚠ 未找到匹配的任务: {missed}")
        case_dirs = matched
        if not case_dirs:
            print("  ❌ 过滤后没有可运行的任务")
            sys.exit(1)
        print(f"  🔧 任务过滤: {', '.join(sorted(task_filter))} → 匹配 {len(case_dirs)} 个")

    print(f"  📋 扫描到 {len(case_dirs)} 个 case\n")

    results: list[dict[str, Any]] = []
    for case_dir in case_dirs:
        try:
            result = run_case(case_dir, version)
            results.append(result)
        except Exception as exc:
            print(f"  ❌ Case [{case_dir.name}] 崩溃: {exc}")
            gc.collect()
            time.sleep(0.5)
            _hard_rmtree(SHADOW_WORKSPACE)
            print("  🧹 shadow_workspace 已紧急清理")
            results.append({
                "case_id": case_dir.name,
                "verify_status": "CRASHED",
                "agent_duration_s": 0.0,
                "total_latency_s": 0.0,
            })

    print_summary(results, version)


if __name__ == "__main__":
    main()
