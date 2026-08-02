#!/usr/bin/env python3
"""
compare_reports.py — 多版本评测指标对账报告生成器

用法:
  py compare_reports.py                                          # 全量对比
  py compare_reports.py -v baseline,v4_throw                     # 筛选版本
  py compare_reports.py -t task_001_db_port                      # 筛选用例
  py compare_reports.py -v baseline,v4 -t task_001 -d            # 组合 + 明细模式
  py compare_reports.py --output report.md                       # 自定义输出

报告路径: sandbox/eval_results/eval_summary.md（默认）
"""

from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# ── 确认 stdout 使用 UTF-8 ───────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
EVAL_ROOT = BASE_DIR / "sandbox" / "eval_results"
TASKS_ROOT = BASE_DIR / "sandbox" / "tasks"
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
    "rollback_count",
)


# ═══════════════════════════════════════════════════════════════
# 版本发现
# ═══════════════════════════════════════════════════════════════

def _discover_versions() -> list[tuple[str, Path]]:
    """扫描 EVAL_ROOT，返回 (版本名, 目录Path) 列表，baseline 排第一，
    其余按目录生成时间（创建时间）排序。"""
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

    # baseline 排第一，其余按目录创建时间升序
    entries.sort(key=lambda x: (
        -1 if x[0] == "baseline" else 0,
        0 if x[0] == "baseline" else x[1].stat().st_ctime,
    ))
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


def _compute_tool_distribution_dict(raw: dict) -> dict[str, int]:
    """计算工具调用分布，返回 {tool_name: count}。"""
    dist: dict[str, int] = {}
    for turn in raw.get("turns", []):
        for tc in turn.get("tools", []):
            name = tc.get("tool_name", "unknown")
            dist[name] = dist.get(name, 0) + 1
    return dist


def _fmt_tool_distribution(dist: dict[str, int | float]) -> str:
    """将工具分布 dict 格式化为 'bash:3 read:2' 字符串。"""
    if not dist:
        return "-"
    parts = []
    for k, v in sorted(dist.items()):
        if isinstance(v, float):
            parts.append(f"{k}:{v:.1f}")
        else:
            parts.append(f"{k}:{v}")
    return " ".join(parts)


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
    dist_dict = _compute_tool_distribution_dict(raw)
    result["_tool_distribution_dict"] = dist_dict
    result["_tool_distribution"] = _fmt_tool_distribution(dist_dict)

    return result


_TRACE_RE = re.compile(r"^trace_(.+?)(?:_r(\d+))?\.json$")


def _parse_trace_filename(tf: Path) -> tuple[str, int]:
    """解析 trace 文件名，返回 (case_id, run_index)。

    trace_task_001.json       → ("task_001", 0)
    trace_task_001_r01.json   → ("task_001", 1)
    trace_task_001_r02.json   → ("task_001", 2)
    """
    m = _TRACE_RE.match(tf.name)
    if not m:
        # 回退到旧逻辑
        name = tf.stem.replace("trace_", "", 1)
        return name, 0
    case_id = m.group(1)
    run_str = m.group(2)
    return case_id, int(run_str) if run_str else 0


def _aggregate_metrics(all_metrics: list[dict]) -> dict[str, Any]:
    """将多次运行的指标聚合成一条记录，含均值 + 范围。"""
    if not all_metrics:
        return {}
    if len(all_metrics) == 1:
        return all_metrics[0]

    result: dict[str, Any] = {}

    # 收集所有字段
    fields: set[str] = set()
    for m in all_metrics:
        fields.update(m.keys())

    for field in fields:
        if field == "_tool_distribution_dict":
            continue
        vals = [m.get(field) for m in all_metrics if m.get(field) is not None]
        if not vals:
            continue

        if all(isinstance(v, (int, float)) for v in vals):
            result[field] = sum(vals) / len(vals)
            result[f"_{field}_min"] = min(vals)
            result[f"_{field}_max"] = max(vals)
            result[f"_{field}_raw"] = vals
        else:
            # 非数值：取众数
            from collections import Counter
            counter = Counter(str(v) for v in vals)
            result[field] = counter.most_common(1)[0][0]

    # ── 工具分布按工具名分列聚合 ────────────────────────────
    dist_dicts = [
        m.get("_tool_distribution_dict", {})
        for m in all_metrics if m.get("_tool_distribution_dict")
    ]
    if dist_dicts:
        all_tools: set[str] = set()
        for d in dist_dicts:
            all_tools.update(d.keys())
        agg_dist: dict[str, float] = {}
        for tool in sorted(all_tools):
            vals = [d.get(tool, 0) for d in dist_dicts]
            agg_dist[tool] = sum(vals) / len(vals)
        result["_tool_distribution_dict"] = agg_dist
        result["_tool_distribution"] = _fmt_tool_distribution(agg_dist)

    result["_run_count"] = len(all_metrics)
    result["_pass_count"] = sum(
        1 for m in all_metrics if m.get("eval_result") == "SUCCESS"
    )
    return result


def _load_all_metrics(
    versions: list[tuple[str, Path]],
    manifests: dict[str, dict[str, Any] | None] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    """返回 {case_id: {version: metrics_dict}}。

    自动检测多运行 trace（文件名含 _rNN 后缀），同 case+version 的多条
    trace 会聚合成一条聚合记录（含均值 + 范围）。
    """
    # 先按 (version, case_id) 分组，收集所有 run
    raw_groups: dict[tuple[str, str], list[dict]] = {}
    for ver_name, ver_dir in versions:
        manifest = (manifests or {}).get(ver_name)
        expected_run_id = (
            manifest.get("run_id")
            if manifest and not manifest.get("_error")
            else None
        )
        for tf in sorted(ver_dir.glob("trace_*.json")):
            if expected_run_id:
                try:
                    trace_data = json.loads(tf.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                trace_run_id = trace_data.get("evaluation_metadata", {}).get("run_id")
                if trace_run_id != expected_run_id:
                    continue
            case_id, _ = _parse_trace_filename(tf)
            metrics = _load_trace_metrics(tf)
            raw_groups.setdefault((ver_name, case_id), []).append(metrics)

    # 聚合
    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    for (ver_name, case_id), metrics_list in raw_groups.items():
        matrix.setdefault(case_id, {})[ver_name] = _aggregate_metrics(metrics_list)

    return matrix


# ═══════════════════════════════════════════════════════════════
# 任务描述加载
# ═══════════════════════════════════════════════════════════════

def _load_task_descriptions() -> dict[str, str]:
    """从每个 task 的 config.json 读取 description，返回 {dir_name: description}。

    以目录名（而非 config.json 中的 case_id 字段）为键，因为 trace 文件名
    使用目录名作为 case_id。
    """
    descs: dict[str, str] = {}
    if not TASKS_ROOT.is_dir():
        return descs
    for d in sorted(TASKS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        cfg = d / "config.json"
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            desc = data.get("description", "")
            if desc:
                descs[d.name] = desc
        except Exception:
            pass
    return descs


def _load_latest_manifest(version_dir: Path) -> dict[str, Any] | None:
    """读取版本目录中最近生成的运行清单；旧结果没有清单时返回 None。"""
    manifests = sorted(
        version_dir.glob("run_manifest_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not manifests:
        return None
    try:
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": f"无法读取 {manifests[0].name}: {exc}"}
    return data if isinstance(data, dict) else {"_error": "manifest 顶层结构不是对象"}


def _load_version_manifests(
    versions: list[tuple[str, Path]],
) -> dict[str, dict[str, Any] | None]:
    return {version: _load_latest_manifest(version_dir) for version, version_dir in versions}


def _load_latest_results(version_dir: Path) -> dict[str, Any] | None:
    """读取最近一次 run 的 case 状态归档。"""
    result_files = sorted(
        version_dir.glob("run_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not result_files:
        return None
    try:
        data = json.loads(result_files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": f"无法读取 {result_files[0].name}: {exc}"}
    return data if isinstance(data, dict) else {"_error": "run results 顶层结构不是对象"}


def _load_version_results(
    versions: list[tuple[str, Path]],
) -> dict[str, dict[str, Any] | None]:
    return {version: _load_latest_results(version_dir) for version, version_dir in versions}


def _include_result_cases(
    matrix: dict[str, dict[str, dict[str, Any]]],
    versions: list[tuple[str, Path]],
    manifests: dict[str, dict[str, Any] | None],
    results_by_version: dict[str, dict[str, Any] | None],
) -> None:
    """将没有 trace 的 case 状态补入矩阵，避免执行结果消失。"""
    for version, _ in versions:
        results = results_by_version.get(version)
        if not results or results.get("_error"):
            continue
        manifest = manifests.get(version)
        expected_run_id = manifest.get("run_id") if manifest and not manifest.get("_error") else None
        if expected_run_id and results.get("run_id") != expected_run_id:
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in results.get("results", []):
            if isinstance(result, dict) and result.get("case_id"):
                grouped.setdefault(str(result["case_id"]), []).append(result)

        for case_id, case_results in grouped.items():
            version_metrics = matrix.setdefault(case_id, {}).get(version)
            statuses = [item.get("verify_status", "FAILED") for item in case_results]
            pass_count = sum(status == "SUCCESS" for status in statuses)
            trace_statuses = [item.get("trace_status", "MISSING") for item in case_results]
            missing_trace_count = sum(status != "ARCHIVED" for status in trace_statuses)
            reasons = list(dict.fromkeys(
                str(item["failure_reason"])
                for item in case_results
                if item.get("failure_reason")
            ))

            if version_metrics:
                # Trace 提供指标，run results 提供真实尝试次数和通过率。
                version_metrics["_run_count"] = len(case_results)
                version_metrics["_pass_count"] = pass_count
                if missing_trace_count:
                    version_metrics["_missing_trace_count"] = missing_trace_count
                    version_metrics["_failure_reasons"] = reasons
                continue

            matrix[case_id][version] = {
                "eval_result": max(set(statuses), key=statuses.count),
                "_trace_status": (
                    "INVALID" if "INVALID" in trace_statuses else "MISSING"
                ),
                "_run_count": len(case_results),
                "_pass_count": pass_count,
                "_missing_trace_count": missing_trace_count,
                "_failure_reason": "; ".join(reasons) if reasons else None,
            }


def _manifest_case_ids(manifest: dict[str, Any] | None) -> set[str]:
    if not manifest or manifest.get("_error"):
        return set()
    return {
        str(task["case_id"])
        for task in manifest.get("tasks", [])
        if isinstance(task, dict) and task.get("case_id")
    }


def _include_manifest_cases(
    matrix: dict[str, dict[str, dict[str, Any]]],
    manifests: dict[str, dict[str, Any] | None],
) -> None:
    """保留 manifest 声明但没有 trace 的用例，让报告显示缺失覆盖。"""
    for manifest in manifests.values():
        for case_id in _manifest_case_ids(manifest):
            matrix.setdefault(case_id, {})


def _render_coverage_notes(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
    manifests: dict[str, dict[str, Any] | None],
) -> list[str]:
    lines: list[str] = []
    for version, _ in versions:
        declared = _manifest_case_ids(manifests.get(version))
        if not declared:
            continue
        observed = {
            case_id for case_id, case_data in matrix.items()
            if case_data.get(version)
            and case_data[version].get("_trace_status") not in {"MISSING", "INVALID"}
        }
        missing = sorted(declared - observed)
        unexpected = sorted(observed - declared)
        if missing:
            lines.append(
                f"> ⚠ `{version}` 的 manifest 声明了 {len(declared)} 个用例，"
                f"但只发现 {len(observed)} 个有效 trace；未覆盖: {', '.join(missing)}"
            )
        if unexpected:
            lines.append(
                f"> ⚠ `{version}` 发现了 manifest 未声明的 trace: {', '.join(unexpected)}"
            )
    return lines


def _short_sha(value: Any) -> str:
    if not value:
        return "-"
    return str(value)[:8]


def _render_provenance(
    versions: list[tuple[str, Path]],
    manifests: dict[str, dict[str, Any] | None],
) -> list[str]:
    """展示实验条件，并提示无法直接比较的版本。"""
    lines = [
        "## 运行条件\n",
        "| 版本 | Agent 提交 | 工作区 | Python | 平台 | 任务集 | 用例数 |",
        "|---|---|---|---|---|---:|---:|",
    ]
    suite_hashes: set[str] = set()
    missing_manifest = False

    for version, _ in versions:
        manifest = manifests.get(version)
        if not manifest:
            missing_manifest = True
            lines.append(f"| `{version}` | - | - | - | - | - | - |")
            continue
        if manifest.get("_error"):
            missing_manifest = True
            lines.append(f"| `{version}` | - | - | - | - | manifest 错误 | - |")
            continue

        agent = manifest.get("agent", {})
        environment = manifest.get("environment", {})
        suite_hash = str(manifest.get("task_suite_sha256", ""))
        if suite_hash:
            suite_hashes.add(suite_hash)
        platform_name = str(environment.get("platform", "-")).replace("|", "\\|")
        lines.append(
            f"| `{version}` | `{_short_sha(agent.get('commit'))}` | "
            f"{('dirty' if agent.get('dirty') else 'clean')} | "
            f"{environment.get('python', '-')} | {platform_name} | "
            f"`{_short_sha(suite_hash)}` | {len(manifest.get('tasks', []))} |"
        )

    if missing_manifest:
        lines.append("> ⚠ 部分版本缺少可追溯的 run manifest，无法确认完整实验条件。")
    if len(suite_hashes) > 1:
        lines.append("> ⚠ 选中版本的 task suite hash 不一致，汇总差异不能直接归因于 Agent 代码变化。")
    if missing_manifest or len(suite_hashes) > 1:
        lines.append("> 建议：先确认任务集和运行环境，再解释轮数、Token 或成功率的变化。")
    lines.append("")
    return lines


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

    # 普通数值 + 百分比变化（格式说明符自带 +/- 前缀）
    if abs(diff) >= 10000:
        diff_str = f"{diff:+,.0f}"
    elif abs(diff) >= 10:
        diff_str = f"{diff:+.0f}"
    elif abs(diff) >= 1:
        diff_str = f"{diff:+.1f}"
    else:
        diff_str = f"{diff:+.2f}"

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
    多运行时自动显示通过率。
    """
    if not metrics:
        return "-"

    # 多运行：通过率前缀
    run_count = metrics.get("_run_count", 0)
    if run_count > 1:
        pass_count = metrics.get("_pass_count", 0)
        if pass_count == run_count:
            emoji = "✅"
        elif pass_count == 0:
            emoji = "❌"
        else:
            emoji = "⚠"
        rate_prefix = f"{emoji} {pass_count}/{run_count} · "
    else:
        rate_prefix = ""

    result = metrics.get("eval_result", "—")

    if metrics.get("_trace_status") in {"MISSING", "INVALID"}:
        trace_label = "无 Trace" if metrics["_trace_status"] == "MISSING" else "Trace 无效"
        reason = metrics.get("_failure_reason")
        reason_label = f": {str(reason).replace('|', '/')}" if reason else ""
        missing_count = metrics.get("_missing_trace_count")
        missing_label = f" ({missing_count} 次)" if missing_count and missing_count > 1 else ""
        return f"{rate_prefix}❌ {result} · {trace_label}{missing_label}{reason_label}"

    turns_raw = metrics.get("total_turns")
    if isinstance(turns_raw, float):
        turns = f"{turns_raw:.1f}" if turns_raw != int(turns_raw) else str(int(turns_raw))
    else:
        turns = _s(turns_raw)

    tokens = _fmt_tokens(metrics.get("total_tokens"))

    comp_raw = metrics.get("compression_count")
    if isinstance(comp_raw, float):
        comp = f"{comp_raw:.1f}" if comp_raw != int(comp_raw) else str(int(comp_raw))
    else:
        comp = _s(comp_raw)

    precision = metrics.get("tool_call_precision")

    if result == "SUCCESS":
        prec_str = f" {precision:.0%}hit" if precision is not None else " -hit"
        lat = metrics.get("total_latency_seconds")
        lat_str = f" · {lat}s" if lat is not None else ""
        return f"{rate_prefix}{turns}t · {tokens}tok{prec_str} · {comp}cmp{lat_str}"
    elif result == "FAILED":
        status = metrics.get("final_status", "FAILED")
        blocking = metrics.get("loop_guard_blocking_rate", 0)
        block_str = f" {blocking:.0%}blk" if blocking and blocking > 0 else ""
        return f"{rate_prefix}❌ {status} · {turns}t · {tokens}tok · {comp}cmp{block_str}"
    else:
        return f"{rate_prefix}⏭ {result}"


# ═══════════════════════════════════════════════════════════════
# 明细行格式化
# ═══════════════════════════════════════════════════════════════

def _fmt_raw_value(val: Any, key: str) -> str:
    """根据指标 key 格式化原始数值（无范围信息）。"""
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
    if isinstance(val, float):
        if val == int(val):
            return str(int(val))
        return f"{val:.1f}"
    return str(val)


def _fmt_detail_value(metrics: dict[str, Any], key: str) -> str:
    """根据指标 key 格式化明细值，多运行时显示 avg (min~max)。"""
    val = metrics.get(key)
    if val is None:
        return "-"

    formatted = _fmt_raw_value(val, key)

    # 多运行：检测是否有 min/max 范围
    min_key = f"_{key}_min"
    max_key = f"_{key}_max"
    if min_key in metrics and max_key in metrics:
        min_val = metrics[min_key]
        max_val = metrics[max_key]
        if min_val != max_val:
            min_str = _fmt_raw_value(min_val, key)
            max_str = _fmt_raw_value(max_val, key)
            return f"{formatted} ({min_str}~{max_str})"

    return formatted


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
    ("回滚次数",      "rollback_count"),
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
    "rollback_count",
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
        "| 版本 | 综合通过率 | 全量 Token | 平均 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |",
        "|------|-----------|---------|---------|---------|-----------|---------|---------|-------------|-------|",
    ]

    baseline_total: int | None = None

    for ver_name, _ in versions:
        case_metrics = {}
        for cid, cdata in matrix.items():
            if ver_name in cdata:
                case_metrics[cid] = cdata[ver_name]

        total = len(case_metrics)

        # 多运行：使用运行级通过率（聚合 _pass_count / _run_count）
        total_runs = 0
        total_passes = 0
        for m in case_metrics.values():
            rc = m.get("_run_count", 0)
            if rc > 1:
                total_runs += rc
                total_passes += m.get("_pass_count", 0)
            else:
                total_runs += 1
                if m.get("eval_result") == "SUCCESS":
                    total_passes += 1

        token_total = sum((m.get("total_tokens") or 0) for m in case_metrics.values())
        turn_total_raw = sum((m.get("total_turns") or 0) for m in case_metrics.values())
        turn_total = f"{turn_total_raw:.1f}" if isinstance(turn_total_raw, float) else str(turn_total_raw)

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

        rate_str = f"{total_passes}/{total_runs}"
        token_str = _fmt_tokens(token_total)
        avg_token = token_total / total if total > 0 else None
        avg_token_str = _fmt_tokens(avg_token) if avg_token is not None else "-"

        if ver_name == versions[0][0]:
            baseline_avg = avg_token
            cost_str = "— (基线)"
        elif total == 0 or baseline_avg is None or baseline_avg <= 0:
            cost_str = "—"
        elif avg_token is not None:
            pct = (avg_token - baseline_avg) / baseline_avg * 100
            if pct < 0:
                cost_str = f"🔻 {abs(pct):.1f}%"
            elif pct > 0:
                cost_str = f"🔺 {pct:.1f}%"
            else:
                cost_str = "➡️ 持平"
        else:
            cost_str = "—"

        lines.append(
            f"| **{ver_name}** | {rate_str} | {token_str} | {avg_token_str} | {turn_total} | {prec_range} | {comp_range} | {avg_lat} | {cost_str} | {total} |"
        )

    lines.append("")
    return lines


def _render_summary_table(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
    descriptions: dict[str, str] | None = None,
) -> list[str]:
    """多版本精简对比表。

    单元格格式：✅ Nt · Nktok · N%hit · Ncmp · Ns
    """
    descs = descriptions or {}
    ver_names = [v[0] for v in versions]

    header = "| 测试用例 | 能力描述 | " + " | ".join(ver_names) + " |"
    sep = "|---------|---------|" + "|".join("-----------" for _ in ver_names) + "|"
    lines = [header, sep]

    for case_id in sorted(matrix.keys()):
        case_data = matrix[case_id]
        desc = descs.get(case_id, "")
        row = [f"**{case_id}**", desc]
        for ver_name in ver_names:
            row.append(_fmt_cell(case_data.get(ver_name, {})))
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    return lines


def _render_detail_tables(
    versions: list[tuple[str, Path]],
    matrix: dict[str, dict[str, dict[str, Any]]],
    descriptions: dict[str, str] | None = None,
) -> list[str]:
    """细粒度明细对比表。

    每个用例独立小节，指标各行，版本各列。
    Δ 列仅在 2 版本比较时展示。
    """
    descs = descriptions or {}
    ver_names = [v[0] for v in versions]
    show_delta = len(versions) == 2
    lines = []

    for case_id in sorted(matrix.keys()):
        case_data = matrix[case_id]
        lines.append(f"\n### {case_id}\n")
        desc = descs.get(case_id, "")
        if desc:
            lines.append(f"> *{desc}*\n")

        # 多运行统计
        multi_run_parts = []
        for vn in ver_names:
            m = case_data.get(vn, {})
            rc = m.get("_run_count", 0)
            if rc > 1:
                pc = m.get("_pass_count", 0)
                multi_run_parts.append(f"{vn}: {pc}/{rc}")
        if multi_run_parts:
            lines.append(f"> 运行统计: {' | '.join(multi_run_parts)}\n")

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
    descriptions: dict[str, str] | None = None,
    manifests: dict[str, dict[str, Any] | None] | None = None,
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

    if manifests is not None:
        lines.extend(_render_provenance(versions, manifests))
        coverage_notes = _render_coverage_notes(versions, matrix, manifests)
        lines.extend(coverage_notes)
        if coverage_notes:
            lines.append("")

    lines.extend(_render_global_board(versions, matrix))

    lines.append("## 多版本精简对比\n")
    lines.append("> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp\n")
    lines.append("> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位\n")
    lines.extend(_render_summary_table(versions, matrix, descriptions))

    if detail:
        lines.append("## 细粒度明细对比\n")
        lines.append("> 每个用例独立小节，指标各行，版本各列。")
        if len(versions) == 2:
            lines.append("> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）\n")
        else:
            lines.append("> （3+ 版本对比时不展示 Δ 列）\n")
        lines.extend(_render_detail_tables(versions, matrix, descriptions))

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
    parser.add_argument("--tasks", "-t", type=str, default=None,
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

    manifests = _load_version_manifests(versions)
    matrix = _load_all_metrics(versions, manifests)
    results_by_version = _load_version_results(versions)
    _include_result_cases(matrix, versions, manifests, results_by_version)
    _include_manifest_cases(matrix, manifests)

    # ── 用例过滤 ──────────────────────────────────────────
    if args.tasks:
        case_filter = {c.strip() for c in args.tasks.split(",")}
        matrix = {cid: data for cid, data in matrix.items() if cid in case_filter}
        missed = case_filter - set(matrix.keys())
        if missed:
            print(f"  ⚠ 未找到匹配的用例: {missed}")
        if not matrix:
            print("  ❌ 过滤后没有可用的用例")
            sys.exit(1)

    total_cases = len(matrix)
    print(f"  📋 共 {total_cases} 个用例")

    descriptions = _load_task_descriptions()
    report = _render_report(
        versions,
        matrix,
        detail=args.detail,
        descriptions=descriptions,
        manifests=manifests,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"\n  ✅ 报告已生成: {output_path}")


if __name__ == "__main__":
    main()
