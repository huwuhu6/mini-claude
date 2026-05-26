#!/usr/bin/env python3
"""
compare_reports.py — 多版本评测指标对账报告生成器

用法:
  py compare_reports.py                                          # 全量对比
  py compare_reports.py -v baseline,v4_throw                     # 筛选版本
  py compare_reports.py -c task_001_db_port                      # 筛选用例
  py compare_reports.py -v baseline,v4 -c task_001 -d            # 组合 + 明细模式
  py compare_reports.py --output report.md                       # 自定义输出

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
    "loop_guard_trigger_count",
    "reflection_count",
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
# Trace 加载与衍生指标
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


def _compute_failure_count(raw: dict) -> int:
    """从 turns 计算工具调用失败次数。"""
    total = 0
    succ = 0
    for turn in raw.get("turns", []):
        for tc in turn.get("tools", []):
            total += 1
            if tc.get("success", False):
                succ += 1
    return total - succ


def _compute_avg_tokens_per_turn(raw: dict) -> float | None:
    """每轮平均 Token 消耗。"""
    t = raw.get("total_tokens")
    n = raw.get("total_turns")
    if t is not None and n and n > 0:
        return round(t / n, 1)
    return None


def _compute_avg_tool_latency(raw: dict) -> float | None:
    """工具调用平均延迟（毫秒）。"""
    total_ms = 0.0
    count = 0
    for turn in raw.get("turns", []):
        for tc in turn.get("tools", []):
            lat = tc.get("latency_ms")
            if lat is not None:
                total_ms += lat
                count += 1
    return round(total_ms / count, 1) if count > 0 else None


def _compute_tool_distribution(raw: dict) -> str:
    """工具调用分布统计，返回 'bash:3 read:2 write:1' 格式。"""
    dist: dict[str, int] = {}
    for turn in raw.get("turns", []):
        for tc in turn.get("tools", []):
            name = tc.get("tool_name", "unknown")
            dist[name] = dist.get(name, 0) + 1
    if not dist:
        return "-"
    return " ".join(f"{k}:{v}" for k, v in sorted(dist.items()))


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

    # 补充原生字段（trace 必含的字段）
    for native in ("total_turns", "total_tokens"):
        if native in raw:
            result.setdefault(native, raw[native])

    # ── 兜底计算 ─────────────────────────────────────
    if "total_latency_seconds" not in result:
        s = raw.get("started_at")
        e = raw.get("finished_at")
        if s is not None and e is not None:
            result["total_latency_seconds"] = round(e - s, 2)

    if "tool_call_precision" not in result:
        prec = _compute_precision_from_turns(raw)
        if prec is not None:
            result["tool_call_precision"] = round(prec, 4)

    # ── 衍生指标 ──────────────────────────────────────
    result["_tool_failure_count"] = _compute_failure_count(raw)
    result["_avg_tokens_per_turn"] = _compute_avg_tokens_per_turn(raw)
    result["_avg_tool_latency_ms"] = _compute_avg_tool_latency(raw)
    result["_tool_distribution"] = _compute_tool_distribution(raw)

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
# Δ 差值辅助（仅 2 版本对比时使用）
# ═══════════════════════════════════════════════════════════════

def _delta_val(a: Any, b: Any, key: str = "") -> str:
    """通用 Δ 差值格式化。a=基准，b=对比。"""
    if a is None or b is None:
        return "—"
    try:
        va = float(a)
        vb = float(b)
    except (ValueError, TypeError):
        sa, sb = str(a), str(b)
        return "—" if sa == sb else f"{sa} → {sb}"

    diff = vb - va
    if abs(diff) < 0.001:
        return "持平"

    # 百分比字段 (0-1 float → percentage points)
    if key in ("tool_call_precision", "loop_guard_blocking_rate"):
        return f"{'+' if diff > 0 else ''}{diff*100:.1f}pp"

    # 普通数值 + 百分比变化
    prefix = "+" if diff > 0 else ""
    if abs(diff) >= 10000:
        diff_str = f"{prefix}{diff:+,.0f}"
    elif abs(diff) >= 10:
        diff_str = f"{prefix}{diff:+.0f}"
    elif abs(diff) >= 1:
        diff_str = f"{prefix}{diff:+.1f}"
    else:
        diff_str = f"{prefix}{diff:+.2f}"

    if va != 0 and abs(diff / va) > 0.01:
        pct = abs(diff) / va * 100
        arrow = "🔺" if diff > 0 else "🔻"
        return f"{diff_str} ({arrow}{pct:.1f}%)"
    return diff_str


# ═══════════════════════════════════════════════════════════════
# 单元格格式化
# ═══════════════════════════════════════════════════════════════

def _fmt_cell(metrics: dict[str, Any]) -> str:
    """格式化为精简对比单元格。

    用 · 做视觉分隔，信息按语义分组。
    """
    if not metrics:
        return "-"

    result = metrics.get("eval_result", "—")
    turns = _s(metrics.get("total_turns"))
    tokens = _fmt_tokens(metrics.get("total_tokens"))
    comp = _s(metrics.get("compression_count"))
    precision = metrics.get("tool_call_precision")

    if result == "SUCCESS":
        prec_str = f" {precision:.0%}hit" if precision is not None else " -hit"
        lat = metrics.get("total_latency_seconds")
        lat_str = f" · {lat}s" if lat is not None else ""
        return f"✅ {turns}t · {tokens}tok{prec_str} · {comp}cmp{lat_str}"
    elif result == "FAILED":
        status = metrics.get("final_status", "FAILED")
        blocking = metrics.get("loop_guard_blocking_rate", 0)
        block_str = f" {blocking:.0%}blk" if blocking and blocking > 0 else ""
        return f"❌ {status} · {turns}t · {tokens}tok · {comp}cmp{block_str}"
    else:
        return f"⏭ {result}"


# ═══════════════════════════════════════════════════════════════
# 明细行格式化
# ═══════════════════════════════════════════════════════════════

def _fmt_detail_value(metrics: dict[str, Any], key: str) -> str:
    """根据指标 key 格式化明细值。"""
    val = metrics.get(key)
    if val is None:
        return "-"

    if key == "total_tokens":
        return f"{int(val):,}"
    if key in ("total_latency_seconds",):
        return f"{val}s"
    if key == "tool_call_precision":
        return f"{val:.0%}"
    if key == "_avg_tokens_per_turn":
        return f"{val:,.1f}"
    if key == "_avg_tool_latency_ms":
        return f"{val:.1f}ms"
    if key == "loop_guard_blocking_rate":
        return f"{val:.1%}"
    return str(val)


# 明细表的指标行定义：(显示名, 字段key)
_DETAIL_METRICS = [
    ("验证结果",      "eval_result"),
    ("最终状态",      "final_status"),
    ("总轮次",        "total_turns"),
    ("总 Token",      "total_tokens"),
    ("总延迟",        "total_latency_seconds"),
    ("工具调用次数",  "total_tool_calls"),
    ("工具命中率",    "tool_call_precision"),
    ("工具失败次数",  "_tool_failure_count"),
    ("每轮 Token",    "_avg_tokens_per_turn"),
    ("平均工具延迟",  "_avg_tool_latency_ms"),
    ("循环守卫触发",  "loop_guard_trigger_count"),
    ("熔断次数",      "circuit_breaker_trigger_count"),
    ("自愈收敛速度",  "self_healing_convergence_speed"),
    ("压缩次数",      "compression_count"),
    ("工具分布",      "_tool_distribution"),
]

# 数值型字段列表（用于判断是否需要计算 Δ）
_NUMERIC_KEYS = {
    "total_turns", "total_tokens", "total_tool_calls",
    "tool_call_precision", "loop_guard_blocking_rate",
    "total_latency_seconds", "_tool_failure_count",
    "_avg_tokens_per_turn", "_avg_tool_latency_ms",
    "loop_guard_trigger_count", "circuit_breaker_trigger_count",
    "self_healing_convergence_speed", "compression_count",
}

# 不参与 Δ 计算的字段（非数值且字符串对比无意义）
_SKIP_DELTA_KEYS = {"_tool_distribution"}


# ═══════════════════════════════════════════════════════════════
# 报告渲染
# ═══════════════════════════════════════════════════════════════

def _render_global_board(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """全局核心指标演进看板。

    将原有单纯均值升级为 min-avg-max 范围，避免单均值误导。
    """
    lines = [
        "## 全局核心指标演进看板\n",
        "| 版本 | 综合通过率 | 全量 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |",
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

        # 命中率范围：min-avg-max
        prec_vals = [
            m.get("tool_call_precision") for m in case_metrics.values()
            if m.get("tool_call_precision") is not None
        ]
        if prec_vals:
            prec_min = min(prec_vals)
            prec_avg = sum(prec_vals) / len(prec_vals)
            prec_max = max(prec_vals)
            if prec_min == prec_max:
                prec_range = f"{prec_min:.0%}"
            else:
                prec_range = f"{prec_min:.0%}-{prec_avg:.0%}-{prec_max:.0%}"
        else:
            prec_range = "-"

        # 压缩次数范围
        comp_vals = [
            m.get("compression_count") for m in case_metrics.values()
            if m.get("compression_count") is not None
        ]
        if comp_vals:
            comp_min = min(comp_vals)
            comp_avg = sum(comp_vals) / len(comp_vals)
            comp_max = max(comp_vals)
            if comp_min == comp_max:
                comp_range = f"{comp_min:.0f}"
            else:
                comp_range = f"{comp_min}-{comp_avg:.1f}-{comp_max}"
        else:
            comp_range = "-"

        # 平均耗时
        lat_vals = [
            m.get("total_latency_seconds") for m in case_metrics.values()
            if m.get("total_latency_seconds") is not None
        ]
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
            f"| **{ver_name}** | {rate_str} | {token_str} | {turn_total} | {prec_range} | {comp_range} | {avg_lat} | {cost_str} | {total} |"
        )

    lines.append("")
    return lines


def _render_summary_table(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """多版本精简对比表。

    单元格格式：✅ Nt · Nktok · N%hit · Ncmp · Ns
    """
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


def _render_detail_tables(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """细粒度明细对比表。

    每个用例独立小节，指标各行，版本各列。
    Δ 列仅在 2 版本比较时展示。
    """
    ver_names = [v[0] for v in versions]
    show_delta = len(versions) == 2
    lines = []

    for case_id in sorted(matrix.keys()):
        case_data = matrix[case_id]
        lines.append(f"\n### {case_id}\n")

        # 表头
        headers = ["指标"] + ver_names
        if show_delta:
            headers.append("Δ")
        sep_parts = ["|" + "|".join("--------" for _ in headers) + "|"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.extend(sep_parts)

        for label, key in _DETAIL_METRICS:
            vals = []
            for vn in ver_names:
                m = case_data.get(vn, {})
                vals.append(_fmt_detail_value(m, key))

            row = [f"**{label}**"]
            row.extend(vals)

            if show_delta:
                # Δ 列：以第一个版本为基准做差值
                if key in _SKIP_DELTA_KEYS:
                    row.append("—")
                elif key in _NUMERIC_KEYS:
                    m_a = case_data.get(ver_names[0], {})
                    m_b = case_data.get(ver_names[1], {})
                    va = m_a.get(key)
                    vb = m_b.get(key)
                    row.append(_delta_val(va, vb, key))
                else:
                    m_a = case_data.get(ver_names[0], {})
                    m_b = case_data.get(ver_names[1], {})
                    va = m_a.get(key)
                    vb = m_b.get(key)
                    row.append(_delta_val(va, vb))
            lines.append("| " + " | ".join(row) + " |")

        lines.append("")

    return lines


def _render_report(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
    detail: bool = False,
) -> str:
    """组装完整 Markdown 报告。"""
    lines: list[str] = [
        "# 多版本评测指标对账报告\n",
        f"> 自动由 `compare_reports.py` 生成 — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    # 版本筛选信息
    ver_names = [v[0] for v in versions]
    lines.append(f"> 版本: {', '.join(ver_names)}")
    lines.append(f"> 用例: {', '.join(sorted(matrix.keys())) or '(无)'}")
    lines.append("")

    lines.extend(_render_global_board(versions, matrix))

    lines.append("## 多版本精简对比\n")
    lines.append("> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp\n")
    lines.append("> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位\n")
    lines.extend(_render_summary_table(versions, matrix))

    if detail:
        lines.append("## 细粒度明细对比\n")
        lines.append("> 每个用例独立小节，指标各行，版本各列。")
        if len(versions) == 2:
            lines.append("> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）\n")
        else:
            lines.append("> （3+ 版本对比时不展示 Δ 列）\n")
        lines.extend(_render_detail_tables(versions, matrix))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="多版本评测指标对账报告生成器",
    )
    parser.add_argument("--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
                        help=f"输出路径（默认: {DEFAULT_OUTPUT}）")
    parser.add_argument("--versions", "-v", type=str, default=None,
                        help="筛选版本（逗号分隔，如 baseline,v4_throw）。默认全部")
    parser.add_argument("--cases", "-c", type=str, default=None,
                        help="筛选用例（逗号分隔，如 task_001,task_002）。默认全部")
    parser.add_argument("--detail", "-d", action="store_true",
                        help="开启细粒度明细对比表（2 版本时自动含 Δ 列）")
    args = parser.parse_args()

    print(f"  🔍 扫描目录: {EVAL_ROOT}")

    versions = _discover_versions()
    if not versions:
        print("  ❌ 未找到任何版本文件夹")
        sys.exit(1)

    # ── 版本过滤 ──────────────────────────────────────────
    if args.versions:
        ver_filter = {v.strip() for v in args.versions.split(",")}
        versions = [(n, p) for n, p in versions if n in ver_filter]
        missed = ver_filter - {n for n, _ in versions}
        if missed:
            print(f"  ⚠ 未找到匹配的版本: {missed}")
        if not versions:
            print("  ❌ 过滤后没有可用的版本")
            sys.exit(1)

    print(f"  📋 发现 {len(versions)} 个版本:")
    for vn, _ in versions:
        print(f"     - {vn}")

    matrix = _load_all_metrics(versions)

    # ── 用例过滤 ──────────────────────────────────────────
    if args.cases:
        case_filter = {c.strip() for c in args.cases.split(",")}
        matrix = {cid: data for cid, data in matrix.items() if cid in case_filter}
        missed = case_filter - set(matrix.keys())
        if missed:
            print(f"  ⚠ 未找到匹配的用例: {missed}")
        if not matrix:
            print("  ❌ 过滤后没有可用的用例")
            sys.exit(1)

    total_cases = len(matrix)
    print(f"  📋 共 {total_cases} 个用例")

    report = _render_report(versions, matrix, detail=args.detail)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"\n  ✅ 报告已生成: {output_path}")


if __name__ == "__main__":
    main()
