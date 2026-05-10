"""
Derived Metrics — pure functions that compute process quality indicators
from a parsed TaskTrace dictionary.

Each function accepts a parsed trace dict (from TaskTrace.to_dict()) and
returns a single value or None when data is insufficient.
"""
from __future__ import annotations
from typing import Any, Dict, Optional


def compute_duplicate_tool_ratio(data: Dict[str, Any]) -> Optional[float]:
    """Ratio of consecutively-duplicate tool calls to total tool calls.

    "Duplicate" means the same (tool_name, args_hash) pair appears on
    two adjacent tool calls in the flattened sequence across all turns.

    Returns:
        0.0–1.0 float, or None if fewer than 2 tool calls exist.
    """
    turns = data.get("turns", [])
    all_tools: list = []
    for turn in turns:
        all_tools.extend(turn.get("tools", []))

    total = len(all_tools)
    if total < 2:
        return None

    duplicates = sum(
        1 for i in range(1, total)
        if all_tools[i]["args_hash"] == all_tools[i - 1]["args_hash"]
    )
    return round(duplicates / total, 4)


def compute_avg_tools_per_turn(data: Dict[str, Any]) -> Optional[float]:
    """Average number of tool calls per LLM iteration.

    Returns:
        Float, or None if no turns were recorded.
    """
    total_turns = data.get("total_turns", 0)
    total_tools = data.get("total_tool_calls", 0)
    if total_turns == 0:
        return None
    return round(total_tools / total_turns, 2)


def compute_reflection_recovery_rate(data: Dict[str, Any]) -> Optional[float]:
    """Whether the agent recovered after forced reflection.

    If at least one reflection event occurred:
      - final_status == "SUCCESS" → 1.0 (recovered)
      - otherwise                → 0.0 (did not recover)

    Returns:
        1.0 / 0.0 if reflections existed, None if no reflections.
    """
    reflection_count = data.get("reflection_count", 0)
    if reflection_count == 0:
        return None
    return 1.0 if data.get("final_status") == "SUCCESS" else 0.0


def compute_compression_survival(data: Dict[str, Any]) -> Optional[bool]:
    """Whether the task succeeded despite context compression.

    Returns:
        True  → compression occurred AND task SUCCESS
        False → compression occurred AND task NOT SUCCESS
        None  → no compression occurred
    """
    compression_count = data.get("compression_count", 0)
    if compression_count == 0:
        return None
    return data.get("final_status") == "SUCCESS"


def compute_rollback_occurred(data: Dict[str, Any]) -> bool:
    """Whether a Shadow Workspace rollback event was recorded."""
    return data.get("rollback_count", 0) > 0


def compute_degradation_score(data: Dict[str, Any]) -> Optional[float]:
    """Quantify runtime degradation on a 0 (clean) → 100 (severely degraded) scale.

    Formula (weighted linear combination):
        score = dup_ratio × 30
              + min(turns / 35, 1) × 20
              + min(reflections / 5, 1) × 25
              + min(loop_guards / 10, 1) × 25

    Component weights reflect severity:
      - 30 %  — Duplicate tool ratio (suggests confused / stuck behavior)
      - 20 %  — Normalised turn count (more iterations = more struggle)
      - 25 %  — Reflection frequency (forced meta-pauses signal deep loops)
      - 25 %  — LoopGuard trigger frequency (hard-intercepted loops)

    Returns:
        0.0–100.0 float, or None if no tasks exist.
    """
    total_tool_calls = data.get("total_tool_calls", 0)
    if total_tool_calls == 0:
        return None

    # ── Duplicate ratio ──────────────────────────────────────
    turns_data = data.get("turns", [])
    all_tools: list = []
    for turn in turns_data:
        all_tools.extend(turn.get("tools", []))
    total = len(all_tools)
    if total >= 2:
        duplicates = sum(
            1 for i in range(1, total)
            if all_tools[i]["args_hash"] == all_tools[i - 1]["args_hash"]
        )
        dup_ratio = duplicates / total
    else:
        dup_ratio = 0.0

    # ── Component scores (each 0–1, then weighted) ────────────
    total_turns = data.get("total_turns", 0)
    reflection_count = data.get("reflection_count", 0)
    loop_guard_count = data.get("loop_guard_trigger_count", 0)

    score = (
        dup_ratio * 30.0
        + min(total_turns / 35.0, 1.0) * 20.0
        + min(reflection_count / 5.0, 1.0) * 25.0
        + min(loop_guard_count / 10.0, 1.0) * 25.0
    )
    return round(min(score, 100.0), 1)
