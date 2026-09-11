"""Re-score archived Anti-Loop runs without modifying their source files."""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = ROOT / "src" / "core" / "evaluation" / "anti_loop.py"
SPEC = importlib.util.spec_from_file_location("anti_loop_metrics", METRICS_PATH)
assert SPEC and SPEC.loader
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _trace_for(source: Path, case_id: str, ordinal: int) -> dict[str, Any] | None:
    candidates = [
        source / f"trace_{case_id}_r{ordinal:02d}.json",
        source / f"trace_{case_id}.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return _load(candidate)
    return None


def _rescore(label: str, source: Path) -> dict[str, Any]:
    result_files = sorted(source.glob("run_results_*.json"))
    if not result_files:
        raise FileNotFoundError(f"no run_results JSON in {source}")
    run_path = result_files[-1]
    run = _load(run_path)
    ordinals: defaultdict[str, int] = defaultdict(int)
    rescored: list[dict[str, Any]] = []
    for row in run.get("results", []):
        case_id = str(row.get("case_id", ""))
        ordinals[case_id] += 1
        trace = _trace_for(source, case_id, ordinals[case_id])
        contract = {
            "behavior_class": row.get("behavior_class"),
            **(row.get("evaluation") or {}),
        }
        graded = METRICS.grade_trial(contract, trace, row)
        validity = METRICS.classify_trial_validity(trace, row)
        rescored.append({
            "case_id": case_id,
            "trial_index": row.get("trial_index"),
            "old_governance_class": (row.get("anti_loop") or {}).get("governance_class"),
            "new_governance_class": graded["governance_class"],
            "old_outcome_classification": (row.get("anti_loop") or {}).get("outcome_classification"),
            "new_outcome_classification": graded["outcome_classification"],
            "old_governance_stopped": (row.get("anti_loop") or {}).get("governance_stopped"),
            "new_governance_stopped": graded["governance_stopped"],
            "terminal_reason": (trace or {}).get("terminal_reason", row.get("terminal_reason")),
            "final_status": (trace or {}).get("final_status", row.get("final_status")),
            "trace_found": trace is not None,
            "trial_validity": validity,
        })
    valid = [r for r in rescored if r["trial_validity"] == "VALID"]
    counts = {k: sum(r["new_governance_class"] == k for r in valid) for k in ("TP", "TN", "FP", "FN")}
    by_case: dict[str, dict[str, int]] = {}
    for row in rescored:
        bucket = by_case.setdefault(row["case_id"], {k: 0 for k in ("TP", "TN", "FP", "FN")})
        if row["new_governance_class"] in bucket and row["trial_validity"] == "VALID":
            bucket[row["new_governance_class"]] += 1
    return {
        "label": label,
        "source": str(source),
        "run_results": str(run_path),
        "run_id": run.get("run_id"),
        "planned_trials": run.get("planned_trials"),
        "rows": len(rescored),
        "valid_trials": len(valid),
        "infra_error_trials": sum(r["trial_validity"] == "INFRA_ERROR" for r in rescored),
        "eval_error_trials": sum(r["trial_validity"] == "EVAL_ERROR" for r in rescored),
        **counts,
        "governance_accuracy": round((counts["TP"] + counts["TN"]) / len(valid), 4) if valid else 0.0,
        "by_case": by_case,
        "changes": [r for r in rescored if r["old_governance_class"] != r["new_governance_class"]],
        "trials": rescored,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True, metavar="LABEL=DIR")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    sources = []
    for item in args.source:
        label, separator, directory = item.partition("=")
        if not separator or not label or not directory:
            parser.error(f"source must be LABEL=DIR: {item}")
        sources.append(_rescore(label, Path(directory).resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
