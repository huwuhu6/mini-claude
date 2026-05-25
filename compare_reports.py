#!/usr/bin/env python3
"""
compare_reports.py — 多版本评测指标对账报告生成器

扫描 sandbox/eval_results/ 下所有版本文件夹（每个版本一个子目录），
读取各版本内的 trace_{case_id}.json，生成横向对比报告。

用法:
  py compare_reports.py
  py compare_reports.py --output report.md      # 自定义输出路径

报告路径: sandbox/eval_results/eval_summary.md（默认）
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# ── 确认 stdout 使用 UTF-8 ───────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
EVAL_ROOT = BASE_DIR / "sandbox" / "eval_results"
DEFAULT_OUTPUT = EVAL_ROOT / "eval_summary.md"

# 需要从 trace 中读取的指标字段（优先读增强字段，回退到原生字段）
_METRIC_FIELDS = (
    "eval_result",
    "final_status",
    "total_turns",
    "total_tokens",
    "total_tool_calls",
    "total_latency_seconds",
    "tool_call_precision",
    "loop_guard_blocking_rate",
    "self_healing_convergence_speed",
    "compression_count",
    "circuit_breaker_trigger_count",
)


# ═══════════════════════════════════════════════════════════════
# 版本发现
# ═══════════════════════════════════════════════════════════════

def _discover_versions() -> list[tuple[str, Path]]:
    """扫描 EVAL_ROOT，返回 (版本名, 目录Path) 列表，baseline 排第一。"""
    if not EVAL_ROOT.is_dir():
        print(f"错误: 目录不存在 {EVAL_ROOT}")
        sys.exit(1)

    entries = []
    for p in sorted(EVAL_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("_"):
            continue
        entries.append((p.name, p))

    entries.sort(key=lambda x: (0, "") if x[0] == "baseline" else (1, x[0]))
    return entries


# ═══════════════════════════════════════════════════════════════
# Trace 加载
# ═══════════════════════════════════════════════════════════════

def _compute_precision_from_turns(raw: dict) -> float | None:
    """从 trace 的 turns 数据中计算 tool_call_precision。"""
    total = 0
    succ = 0
    for turn in raw.get("turns", []):
        for tc in turn.get("tools", []):
            total += 1
            if tc.get("success", False):
                succ += 1
    return succ / total if total > 0 else None


def _load_trace_metrics(trace_path: Path) -> dict[str, Any]:
    """加载单个 trace JSON，返回标准化的度量字典。

    优先读取增强字段（eval_runner 注入），缺失时从原始 trace 兜底计算。
    """
    raw = json.loads(trace_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {}

    for field in _METRIC_FIELDS:
        val = raw.get(field)
        if val is not None:
            result[field] = val

    # 无增强字段时，用 final_status 推断 eval_result
    if "eval_result" not in result and "final_status" in result:
        fs = result["final_status"]
        result["eval_result"] = "SUCCESS" if fs == "SUCCESS" else "FAILED"

    # 补充原生字段（trace 必含的字段，防止增强字段不存在时完全缺失）
    for native in ("total_turns", "total_tokens"):
        if native in raw:
            result.setdefault(native, raw[native])

    # ── 兜底计算：total_latency_seconds ─────────────────
    if "total_latency_seconds" not in result:
        s = raw.get("started_at")
        e = raw.get("finished_at")
        if s is not None and e is not None:
            result["total_latency_seconds"] = round(e - s, 2)

    # ── 兜底计算：tool_call_precision ──────────────────
    if "tool_call_precision" not in result:
        prec = _compute_precision_from_turns(raw)
        if prec is not None:
            result["tool_call_precision"] = round(prec, 4)

    return result


def _load_all_metrics(
    versions: list[tuple[str, Path]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """返回 {case_id: {version: metrics_dict}}。"""
    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    for ver_name, ver_dir in versions:
        for tf in sorted(ver_dir.glob("trace_*.json")):
            case_id = tf.name.replace(".json", "").replace("trace_", "", 1)
            metrics = _load_trace_metrics(tf)
            matrix.setdefault(case_id, {})[ver_name] = metrics
    return matrix


# ═══════════════════════════════════════════════════════════════
# 格式化辅助
# ═══════════════════════════════════════════════════════════════

def _s(val: Any) -> str:
    """安全显示：None → '-'，否则原样返回。"""
    return "-" if val is None else str(val)


def _fmt_tokens(val: Any) -> str:
    """Token 友好显示：1300 → 1.3k, 196383 → 196.4k。"""
    if val is None:
        return "-"
    n = int(val)
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    return str(n)


# ═══════════════════════════════════════════════════════════════
# 单元格格式化
# ═══════════════════════════════════════════════════════════════

def _fmt_cell(metrics: dict[str, Any]) -> str:
    """格式化为信息密集单元格。

    SUCCESS → ✅ Nturns Ntok N%hit Ncomp Ns
    FAILED  → ❌ STATUS Nturns Ntok Ncomp
    缺失字段用 - 占位。
    """
    if not metrics:
        return "-"

    result = metrics.get("eval_result", "—")
    turns = _s(metrics.get("total_turns"))
    tokens = _fmt_tokens(metrics.get("total_tokens"))
    comp = _s(metrics.get("compression_count"))
    precision = metrics.get("tool_call_precision")
    latency = metrics.get("total_latency_seconds")

    if result == "SUCCESS":
        prec_str = f" {precision:.0%}hit" if precision is not None else " -hit"
        lat_str = f" {latency}s" if latency is not None else " -s"
        return f"✅ {turns}turns {tokens}tok{prec_str} {comp}comp{lat_str}"
    elif result == "FAILED":
        status = metrics.get("final_status", "FAILED")
        blocking = metrics.get("loop_guard_blocking_rate")
        block_str = f" {blocking:.0%}block" if blocking is not None and blocking > 0 else ""
        return f"❌ {status} {turns}turns {tokens}tok {comp}comp{block_str}"
    else:
        return f"⏭ {result}"


# ═══════════════════════════════════════════════════════════════
# 报告渲染
# ═══════════════════════════════════════════════════════════════

def _render_global_board(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """全局核心指标演进看板。"""
    lines = [
        "## 全局核心指标演进看板\n",
        "| 版本 | 综合通过率 | 全量 Token | 全量轮次 | 平均命中率 | 平均压缩 | 平均耗时 | 相比基线成本 | 用例数 |",
        "|------|-----------|-----------|---------|-----------|---------|---------|-------------|-------|",
    ]

    baseline_total: int | None = None

    for ver_name, _ in versions:
        case_metrics = {}
        for cid, cdata in matrix.items():
            if ver_name in cdata:
                case_metrics[cid] = cdata[ver_name]

        total = len(case_metrics)
        passed = sum(1 for m in case_metrics.values() if m.get("eval_result") == "SUCCESS")
        token_total = sum((m.get("total_tokens") or 0) for m in case_metrics.values())
        turn_total = sum((m.get("total_turns") or 0) for m in case_metrics.values())

        # 平均命中率
        prec_vals = [m.get("tool_call_precision") for m in case_metrics.values() if m.get("tool_call_precision") is not None]
        avg_prec = f"{sum(prec_vals)/len(prec_vals):.0%}" if prec_vals else "-"

        # 平均压缩次数
        comp_vals = [m.get("compression_count") for m in case_metrics.values() if m.get("compression_count") is not None]
        avg_comp = f"{sum(comp_vals)/len(comp_vals):.1f}" if comp_vals else "-"

        # 平均耗时
        lat_vals = [m.get("total_latency_seconds") for m in case_metrics.values() if m.get("total_latency_seconds") is not None]
        avg_lat = f"{sum(lat_vals)/len(lat_vals):.1f}s" if lat_vals else "-"

        rate_str = f"{passed}/{total}"
        token_str = _fmt_tokens(token_total)

        if ver_name == versions[0][0]:
            baseline_total = token_total
            cost_str = "— (基线)"
        elif baseline_total is not None and baseline_total > 0:
            pct = (token_total - baseline_total) / baseline_total * 100
            if pct < 0:
                cost_str = f"🔻 {abs(pct):.1f}%"
            elif pct > 0:
                cost_str = f"🔺 {pct:.1f}%"
            else:
                cost_str = "➡️ 持平"
        else:
            cost_str = "—"

        lines.append(
            f"| **{ver_name}** | {rate_str} | {token_str} | {turn_total} | {avg_prec} | {avg_comp} | {avg_lat} | {cost_str} | {total} |"
        )

    lines.append("")
    return lines


def _render_comparison_table(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """多版本量化对比宽表。"""
    ver_names = [v[0] for v in versions]

    header = "| 测试用例 | " + " | ".join(ver_names) + " |"
    sep = "|---------|" + "|".join("-----------" for _ in ver_names) + "|"
    lines = [header, sep]

    for case_id in sorted(matrix.keys()):
        case_data = matrix[case_id]
        row = [f"**{case_id}**"]
        for ver_name in ver_names:
            row.append(_fmt_cell(case_data.get(ver_name, {})))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    return lines


def _render_report(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> str:
    """组装完整 Markdown 报告。"""
    lines: list[str] = [
        "# 多版本评测指标对账报告\n",
        f"> 自动由 `compare_reports.py` 生成 — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "### 快速使用",
        "",
        "逐版本运行评测：",
        "",
        "```bash",
        "py eval_runner.py                          # 默认为 baseline",
        "py eval_runner.py -v v2_fault_injection     # 写入 v2_fault_injection",
        "py eval_runner.py -v v3_circuit_breaker     # 写入 v3_circuit_breaker",
        "py eval_runner.py -t task_001              # 只跑单个任务（快速调试）",
        "py eval_runner.py -t task_001,task_002     # 逗号分隔多个任务",
        "```",
        "",
        "生成此报告：",
        "",
        "```bash",
        "py compare_reports.py",
        "```",
        "",
        "**产物结构**：",
        "",
        "```",
        "sandbox/eval_results/",
        "  ├── baseline/              # trace_{case_id}.json（完整 Trace + 增强指标）",
        "  ├── v2_fault_injection/",
        "  ├── v3_circuit_breaker/",
        "  └── eval_summary.md         # ← 本文件（多版本对账报告）",
        "```",
        "",
    ]

    lines.extend(_render_global_board(versions, matrix))
    lines.append("## 多版本量化对比\n")
    lines.append("> SUCCESS → ✅ turns tok 命中率 comp次数 耗时\n")
    lines.append("> FAILED  → ❌ 终态 turns tok comp次数\n")
    lines.append("> SKIPPED → ⏭ 原因（如缺少 verify 脚本、config 配置不完整等）\n")
    lines.append("> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位\n")
    lines.extend(_render_comparison_table(versions, matrix))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="多版本评测指标对账报告生成器")
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
                        help=f"输出路径（默认: {DEFAULT_OUTPUT}）")
    args = parser.parse_args()
    output_path = Path(args.output)

    print(f"  🔍 扫描目录: {EVAL_ROOT}")

    versions = _discover_versions()
    if not versions:
        print("  ❌ 未找到任何版本文件夹")
        sys.exit(1)

    print(f"  📋 发现 {len(versions)} 个版本:")
    for vn, _ in versions:
        print(f"     - {vn}")

    matrix = _load_all_metrics(versions)
    total_cases = len(matrix)
    print(f"  📋 共 {total_cases} 个用例")

    report = _render_report(versions, matrix)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"\n  ✅ 报告已生成: {output_path}")


if __name__ == "__main__":
    main()
