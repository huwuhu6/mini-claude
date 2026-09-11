"""Recompute Anti-Loop accounting from durable run_results without rerunning agents.

This is deliberately an evaluator-side audit tool.  It separates execution
health (VALID/INFRA_ERROR/EVAL_ERROR), verifier outcome, and governance
classification so one failure cannot silently disappear from a denominator.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core.evaluation.anti_loop import grade_trial  # noqa: E402

CLASSES = {"TP", "FP", "TN", "FN"}


def _latest_decision(trace: dict[str, Any] | None) -> str:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            decision = value.get("governance_decision")
            if isinstance(decision, str) and decision:
                values.append(decision)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(trace or {})
    non_allow = [value for value in values if value != "ALLOW"]
    return (non_allow or values or ["UNKNOWN"])[-1]


def _load_trace(folder: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    name = f"trace_{row['case_id']}_r{int(row.get('run_index', row['trial_index'])):02d}.json"
    path = folder / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def audit(path: Path, trace_folder: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    per_case_run: defaultdict[str, int] = defaultdict(int)
    for raw in payload.get("results", []):
        per_case_run[raw["case_id"]] += 1
        run_index = int(raw.get("run_index") or per_case_run[raw["case_id"]])
        row = dict(raw)
        row["run_index"] = run_index
        trace = _load_trace(trace_folder, row)
        # A raw result can carry enough terminal information even when its
        # trace is absent.  grade_trial then conservatively marks must_stop as
        # FN and must_recover as TN unless a false stop is observable.
        graded = grade_trial(
            {"behavior_class": raw.get("behavior_class")},
            trace,
            {
                "verify_status": raw.get("verify_status"),
                "final_status": raw.get("final_status", ""),
                "runtime_error": raw.get("runtime_error", ""),
            },
        )
        governance = graded.get("governance_class")
        row.update({
            "expected_class": raw.get("behavior_class"),
            "agent_final_status": raw.get("final_status") or "<missing>",
            "trace_present": bool(raw.get("trace_status") == "ARCHIVED" and trace),
            "verifier_pass": raw.get("verify_status") == "SUCCESS",
            "outcome_pass": bool(graded.get("outcome_success")),
            "governance_decision": raw.get("governance_decision") or _latest_decision(trace),
            "governance_class": governance,
            "included_in_governance_denominator": governance in CLASSES,
            "exclusion_reason": "" if governance in CLASSES else "missing behavior contract or governance evidence",
            "trial_validity": raw.get("trial_validity", "<missing>"),
        })
        rows.append(row)

    counts = Counter(row["governance_class"] for row in rows if row["governance_class"] in CLASSES)
    tp, fp, tn, fn = (counts[key] for key in ("TP", "FP", "TN", "FN"))
    must_recover = [row for row in rows if row["expected_class"] == "must_recover"]
    must_stop = [row for row in rows if row["expected_class"] == "must_stop"]

    def ratio(n: int, d: int) -> str:
        return f"{n}/{d} = {n / d:.2%}" if d else f"{n}/0 = N/A"

    summary = {
        "planned_trials": payload.get("planned_trials"),
        "ledger_trials": len(rows),
        "must_stop": len(must_stop),
        "must_recover": len(must_recover),
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "governance_accuracy": ratio(tp + tn, len(rows)),
        "stop_precision": ratio(tp, tp + fp),
        "stop_recall": ratio(tp, tp + fn),
        "false_stop_rate": ratio(fp, fp + tn),
        "solvable_success_rate": ratio(
            sum(row["outcome_pass"] for row in must_recover), len(must_recover)
        ),
        "appropriate_stop_rate": ratio(tp, tp + fn),
        "verifier_pass_rate": ratio(sum(row["verifier_pass"] for row in rows), len(rows)),
        "infra_error": sum(row.get("trial_validity") == "INFRA_ERROR" for row in rows),
        "eval_error": sum(row.get("trial_validity") == "EVAL_ERROR" for row in rows),
    }
    return rows, summary


def render_table(rows: list[dict[str, Any]]) -> str:
    columns = [
        "case_id", "run_index", "expected_class", "agent_final_status",
        "trace_present", "verifier_pass", "outcome_pass", "governance_decision",
        "governance_class", "included_in_governance_denominator", "exclusion_reason",
        "total_turns", "total_tokens", "total_latency_s",
    ]
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sections = ["# Anti-Loop Evaluation Accounting Audit", ""]
    for label, path in (("Baseline", args.baseline), ("Candidate", args.candidate)):
        rows, summary = audit(path, path.parent)
        sections.extend([f"## {label}: {path.name}", "", "```json", json.dumps(summary, ensure_ascii=False, indent=2), "```", "", render_table(rows), ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
