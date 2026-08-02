#!/usr/bin/env python3
"""
eval_runner.py — WF-2 工业级沙箱评测引擎

用法:
  py eval_runner.py                           # 使用默认版本 "baseline"
  py eval_runner.py --version v4_fault        # 归档到 sandbox/eval_results/v4_fault/
  py eval_runner.py -v baseline               # -v 是 --version 的简写
  py eval_runner.py -t task_001              # 只跑指定任务（快速调试）
  py eval_runner.py -t task_001,task_002     # 逗号分隔多个任务
  py eval_runner.py --validate-only          # 只校验任务契约，不启动 Agent

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
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
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


def _sha256_file(path: Path) -> str:
    """Return a content hash used to identify task fixtures."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(path: Path) -> str:
    """Hash relative file names and contents in a deterministic order."""
    digest = hashlib.sha256()
    ignored_dirs = {"node_modules", "__pycache__"}
    files = (
        p for p in path.rglob("*")
        if p.is_file() and not ignored_dirs.intersection(p.relative_to(path).parts)
    )
    for file_path in sorted(files):
        digest.update(file_path.relative_to(path).as_posix().encode("utf-8"))
        digest.update(_sha256_file(file_path).encode("ascii"))
    return digest.hexdigest()


def _git_metadata() -> dict[str, str | bool]:
    """Capture lightweight source provenance without changing the worktree."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(BASE_DIR), capture_output=True, text=True, check=True,
        ).stdout.strip())
        return {"commit": commit, "worktree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": "unknown", "worktree_dirty": True}


def _validate_task(case_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate the small task contract before an Agent is started."""
    errors: list[str] = []
    config_file = case_dir / "config.json"
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"config.json 无法读取: {exc}"]

    if not isinstance(config, dict):
        return None, ["config.json 顶层必须是 JSON 对象"]

    if config.get("case_id") != case_dir.name:
        errors.append("case_id 必须与任务目录名一致")
    if not isinstance(config.get("prompt"), str) or not config["prompt"].strip():
        errors.append("prompt 必须是非空字符串")

    baseline_dir = case_dir / "baseline"
    if not baseline_dir.is_dir():
        errors.append("缺少 baseline/ 目录")

    verify_name = config.get("verify_script_file")
    if verify_name is not None:
        if not isinstance(verify_name, str) or not verify_name.strip():
            errors.append("verify_script_file 必须是文件名或显式为 null")
        else:
            verify_path = (case_dir / verify_name).resolve()
            try:
                verify_path.relative_to(case_dir.resolve())
            except ValueError:
                errors.append("verify_script_file 不得越出任务目录")
            if not verify_path.is_file():
                errors.append(f"验证脚本不存在: {verify_name}")
    elif (case_dir / "verify.py").is_file():
        errors.append("存在 verify.py，但 config.json 未声明 verify_script_file")

    return (config if not errors else None), errors


def _task_metadata(case_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Describe the exact fixture used by a run."""
    verify_name = config.get("verify_script_file")
    verify_path = case_dir / verify_name if verify_name else None
    return {
        "case_id": case_dir.name,
        "task_version": config.get("task_version", 1),
        "config_sha256": _sha256_file(case_dir / "config.json"),
        "baseline_sha256": _sha256_tree(case_dir / "baseline"),
        "verify_sha256": _sha256_file(verify_path) if verify_path else None,
    }


def _write_run_manifest(
    version: str, run_id: str, case_dirs: list[Path], configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Persist enough provenance to reproduce and interpret a result directory."""
    tasks = [_task_metadata(case_dir, configs[case_dir.name]) for case_dir in case_dirs]
    suite_digest = hashlib.sha256(
        json.dumps(tasks, sort_keys=True).encode("utf-8")
    ).hexdigest()
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "version_label": version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "agent": _git_metadata(),
        "eval_runner": {"path": str(Path(__file__).relative_to(BASE_DIR))},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "task_suite_sha256": suite_digest,
        "tasks": tasks,
    }
    report_dir = OUTPUT_ROOT / version
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = report_dir / f"run_manifest_{run_id}.json"
    manifest_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  💾 运行元数据已归档: {manifest_path.relative_to(BASE_DIR)}")
    return metadata


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
    """Compute process metrics; definitions live in local evaluation_evolution.md."""
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


def _truncate_output(text: str, limit: int = 4000) -> str | None:
    """保留验证输出的尾部，避免 run results 被异常日志无限膨胀。"""
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"...<truncated>...\n{text[-limit:]}"


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
            if item.name in {"node_modules", "__pycache__"}:
                continue
            dst = SHADOW_WORKSPACE / item.name
            if item.is_file():
                shutil.copy2(item, dst)
            elif item.is_dir():
                shutil.copytree(item, dst)
        file_count = sum(
            1 for p in baseline_dir.rglob("*")
            if p.is_file() and not {"node_modules", "__pycache__"}.intersection(
                p.relative_to(baseline_dir).parts
            )
        )
        print(f"  📁 baseline 已复制 ({file_count} files)")


def _run_agent(prompt: str) -> tuple[Optional[Path], float, str | None]:
    """实例化 Agent 并执行 prompt，返回 trace、耗时和异常原因。"""
    # 延迟导入，使 --validate-only 不依赖 LLM、tiktoken 或 API 环境。
    from agent.mini_claude_agent import MiniClaudeAgent

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
        return _find_latest_trace(SHADOW_WORKSPACE), elapsed, None
    except Exception as exc:
        print(f"  ❌ Agent 异常: {exc}")
        return None, 0.0, f"agent_exception:{type(exc).__name__}: {exc}"
    finally:
        if agent:
            try:
                agent.shutdown()
            except Exception as exc:
                print(f"  ⚠ shutdown 异常: {exc}")


# ═══════════════════════════════════════════════════════════════
# 单 Case 运行器
# ═══════════════════════════════════════════════════════════════

def run_case(
    case_dir: Path,
    version: str,
    run_metadata: dict[str, Any],
    run_idx: int = 1,
    total_runs: int = 1,
) -> dict[str, Any]:
    """运行单个评测 case，返回结果字典。"""
    case_id = case_dir.name
    config_file = case_dir / "config.json"
    baseline_dir = case_dir / "baseline"

    if not config_file.exists():
        return {"case_id": case_id, "verify_status": "NO_CONFIG",
                "agent_duration_s": 0.0, "total_latency_s": 0.0,
                "trace_status": "MISSING"}

    config = json.loads(config_file.read_text(encoding="utf-8"))
    prompt = config["prompt"]
    verify_script_name: Optional[str] = config.get("verify_script_file")

    print(f"\n{'=' * 60}")
    run_label = f" (Run {run_idx}/{total_runs})" if total_runs > 1 else ""
    print(f"  🚀 Case: {case_id}{run_label}")
    print(f"{'=' * 60}")

    t_start = time.perf_counter()

    # ── Step 1: 沙箱准备 ─────────────────────────────────
    _prepare_sandbox(baseline_dir)

    # ── Step 2: 启动 Agent ───────────────────────────────
    trace_path, agent_duration, agent_error = _run_agent(prompt)

    # ── Step 3: 动态路由断言（黄雀在后验证） ───────────────
    verify_status = "FAILED"
    failure_reason = agent_error
    verify_exit_code: int | None = None
    verify_stdout: str | None = None
    verify_stderr: str | None = None
    verify_duration_s: float | None = None
    if verify_script_name and trace_path:
        script_src = case_dir / verify_script_name
        if script_src.exists():
            script_dst = SHADOW_WORKSPACE / verify_script_name
            try:
                script_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script_src, script_dst)
                print(f"  📄 verify 脚本已复制: {verify_script_name}")
            except Exception as exc:
                print(f"  ⚠ verify 脚本复制失败: {exc}")

            _env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            try:
                if sys.platform == "win32":
                    subprocess.run(["chcp", "65001"], capture_output=True, shell=True)
                verify_started = time.perf_counter()
                result = subprocess.run(
                    [sys.executable or "python", verify_script_name],
                    cwd=str(SHADOW_WORKSPACE),
                    capture_output=True,
                    env=_env,
                    timeout=120,
                )
                verify_duration_s = round(time.perf_counter() - verify_started, 2)
                verify_exit_code = result.returncode
                out = result.stdout.decode("utf-8", errors="replace")
                err = result.stderr.decode("utf-8", errors="replace")
                verify_stdout = _truncate_output(out)
                verify_stderr = _truncate_output(err)
                if out.strip():
                    print(f"  → verify stdout:\n{out}")
                if err.strip():
                    print(f"  → verify stderr:\n{err}")
                verify_status = "SUCCESS" if result.returncode == 0 else "FAILED"
                if result.returncode != 0:
                    failure_reason = f"verify_exit_code:{result.returncode}"
                print(f"  {'✅' if verify_status == 'SUCCESS' else '❌'} verify: {verify_status} (rc={result.returncode})")
            except subprocess.TimeoutExpired:
                print("  ❌ verify 超时 (120s)")
                verify_status = "FAILED"
                failure_reason = "verify_timeout"
                verify_duration_s = round(time.perf_counter() - verify_started, 2)
            except Exception as exc:
                print(f"  ❌ verify 异常: {exc}")
                verify_status = "CRASHED"
                failure_reason = f"verify_exception:{type(exc).__name__}: {exc}"
        else:
            print(f"  ⚠ verify_script_file='{verify_script_name}' 不存在于 case 目录")
            failure_reason = "verify_script_missing"
    elif not verify_script_name:
        print("  ⏭ verify_script_file 为 null，跳过验证")
        verify_status = "SKIPPED"
    else:
        print("  ⚠ verify_script_file 已定义但 trace 未生成，跳过验证")
        verify_status = "FAILED"
        failure_reason = failure_reason or "trace_missing_before_verify"

    # ── Step 4: 核心对账 —— 黄雀在后解析器 ─────────────────
    total_latency = round(time.perf_counter() - t_start, 2)
    metrics: dict[str, Any] = {}
    trace_data: dict[str, Any] = {}
    trace_status = "MISSING"

    if trace_path and trace_path.exists():
        try:
            trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
            metrics = _compute_metrics(trace_data)

            # 注入 5 维工业级度量指标（不覆盖原生字段 total_tokens/total_turns）
            # 字段定义与统计口径见本地 docs/evolution/evaluation_evolution.md。
            trace_data["evaluation_metadata"] = run_metadata
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
            if total_runs > 1:
                dest = report_dir / f"trace_{case_id}_r{run_idx:02d}.json"
            else:
                dest = report_dir / f"trace_{case_id}.json"
            dest.write_text(
                json.dumps(trace_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  💾 Trace 已归档: {dest.relative_to(BASE_DIR)}")
            trace_status = "ARCHIVED"

            # 删除原始 trace，避免沙箱残留
            trace_path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"  ❌ Trace 对账异常: {exc}")
            trace_status = "INVALID"
            failure_reason = f"trace_processing_error:{type(exc).__name__}: {exc}"
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
        "trace_status": trace_status,
        "failure_reason": failure_reason,
        "verify_exit_code": verify_exit_code,
        "verify_stdout": verify_stdout,
        "verify_stderr": verify_stderr,
        "verify_duration_s": verify_duration_s,
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


def write_run_results(version: str, run_metadata: dict[str, Any], results: list[dict]) -> Path:
    """归档每个 case 的执行状态，覆盖没有生成 trace 的失败路径。"""
    report_dir = OUTPUT_ROOT / version
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"run_results_{run_metadata['run_id']}.json"
    payload = {
        "run_id": run_metadata["run_id"],
        "version_label": version,
        "results": results,
    }
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  💾 Case 执行结果已归档: {output_path.relative_to(BASE_DIR)}")
    return output_path


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
    parser.add_argument(
        "--runs", "-r", type=int, default=1,
        help="每个任务运行次数（默认: 1）。多运行时 trace 文件会标注运行序号",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="只校验任务契约，不启动 Agent 或写入评测结果",
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

    task_configs: dict[str, dict[str, Any]] = {}
    invalid_tasks: list[str] = []
    for case_dir in case_dirs:
        config, errors = _validate_task(case_dir)
        if errors:
            invalid_tasks.append(f"{case_dir.name}: {'；'.join(errors)}")
        else:
            task_configs[case_dir.name] = config  # type: ignore[assignment]
    if invalid_tasks:
        print("  ❌ 任务契约校验失败:")
        for error in invalid_tasks:
            print(f"     - {error}")
        sys.exit(1)

    print(f"  📋 扫描到 {len(case_dirs)} 个有效 case\n")
    if args.validate_only:
        print("  ✅ 任务契约校验通过，未启动 Agent。")
        return

    if args.runs > 1:
        print(f"  🔧 每任务运行 {args.runs} 次\n")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_metadata = _write_run_manifest(version, run_id, case_dirs, task_configs)

    results: list[dict[str, Any]] = []
    for case_dir in case_dirs:
        for run_idx in range(1, args.runs + 1):
            try:
                result = run_case(
                    case_dir, version, run_metadata,
                    run_idx=run_idx, total_runs=args.runs,
                )
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
                    "trace_status": "MISSING",
                    "failure_reason": f"case_exception:{type(exc).__name__}: {exc}",
                })

    write_run_results(version, run_metadata, results)
    print_summary(results, version)


if __name__ == "__main__":
    main()
