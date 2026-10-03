"""Replay pre-first-mutation shadow thresholds against persisted TaskTrace JSON."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.runtime_context.execution_shadow import (  # noqa: E402
    MutationRequirement,
    PreFirstMutationShadow,
    classify_mutation_requirement,
)

_NON_ACTION_TOOLS = {"todowrite", "update_agent_note"}
_NON_EXECUTED_FAILURES = {
    "INVALID_ARGUMENTS", "SAME_RESPONSE_DUPLICATE", "USER_CANCELLED", "CIRCUIT_BREAKER",
}


def _tool_facts(trace: dict[str, Any]) -> dict[tuple[int, str], list[dict[str, Any]]]:
    facts: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for turn in trace.get("turns", []):
        turn_number = int(turn.get("iteration", 0)) + 1
        for tool in turn.get("tools", []):
            facts.setdefault((turn_number, str(tool.get("tool_name", ""))), []).append(tool)
    return facts


def _is_executed_attempt(event: dict[str, Any]) -> bool:
    if event.get("status") == "BLOCKED" or event.get("block_reason"):
        return False
    if event.get("failure_category") in _NON_EXECUTED_FAILURES:
        return False
    return True


def replay_task_trace(path: Path, trace: dict[str, Any]) -> dict[str, Any] | None:
    prompt = str(trace.get("user_prompt", ""))
    requirement = classify_mutation_requirement(prompt)
    if requirement is not MutationRequirement.REQUIRED:
        return None

    shadow = PreFirstMutationShadow(requirement)
    tool_facts = _tool_facts(trace)
    actions: list[tuple[int, int, str]] = []
    native_events = trace.get("attempt_events") or []
    events = native_events
    if not events:
        # Older traces without AttemptHistory can still be replayed from tool facts.
        events = [
            {
                "turn": int(turn.get("iteration", 0)) + 1,
                "tool_name": tool.get("tool_name", ""),
                "status": "SUCCESS" if tool.get("success", True) else "FAILURE",
                "failure_category": tool.get("failure_category", ""),
                "block_reason": tool.get("guard_reason", "") if tool.get("loop_guard_blocked") else "",
                "changed_paths": tool.get("changed_paths", []),
                "workspace_changed": bool(tool.get("changed_paths")),
                "workspace_before_digest": tool.get("workspace_before_digest", ""),
                "workspace_after_digest": tool.get("workspace_after_digest", ""),
                "legacy_loop_guard_blocked": tool.get("loop_guard_blocked", False),
            }
            for turn in trace.get("turns", [])
            for tool in turn.get("tools", [])
        ]

    workspace_evidence_complete = bool(events) and all(
        ("workspace_before_digest" in event and "workspace_after_digest" in event)
        or "changed_paths" in event
        for event in events
        if isinstance(event, dict) and _is_executed_attempt(event)
    )

    last_model_turn = 0
    for event in events:
        name = str(event.get("tool_name", ""))
        turn = int(event.get("turn", 0))
        for model_turn in range(last_model_turn + 1, turn + 1):
            shadow.observe_model_turn(model_turn)
        last_model_turn = max(last_model_turn, turn)
        key = (turn, name)
        fact = tool_facts.get(key, []).pop(0) if tool_facts.get(key) else {}
        if event.get("legacy_loop_guard_blocked") or fact.get("loop_guard_blocked"):
            continue
        if not _is_executed_attempt(event):
            continue
        if name.casefold() in _NON_ACTION_TOOLS:
            shadow.observe_tool_action(tool_name=name, turn=turn, executed=True)
            continue

        changed_paths = event.get("changed_paths") or fact.get("changed_paths") or []
        digests_changed = bool(
            event.get("workspace_before_digest")
            and event.get("workspace_after_digest")
            and event.get("workspace_before_digest") != event.get("workspace_after_digest")
        )
        changed = bool(changed_paths or event.get("workspace_changed") or digests_changed)
        before_count = shadow.pre_mutation_action_count
        state = shadow.observe_tool_action(
            tool_name=name,
            turn=turn,
            executed=True,
            changed_paths=(changed_paths or ["<changed-path-not-retained>" ] if changed else []),
        )
        if state["first_workspace_mutation_seen"] and state["first_workspace_mutation_action_index"] == before_count + 1:
            mutation_index = state["first_workspace_mutation_action_index"]
            actions.append((turn, mutation_index, name))
            break
        if state["pre_mutation_action_count"] > before_count:
            actions.append((turn, state["pre_mutation_action_count"], name))

    if not shadow.first_workspace_mutation_seen:
        for model_turn in range(last_model_turn + 1, len(trace.get("turns", [])) + 1):
            shadow.observe_model_turn(model_turn)

    evidence = shadow.snapshot()
    mutation_index = evidence["first_workspace_mutation_action_index"]
    trigger_positions = {}
    for threshold in (8, 10, 12):
        trigger = evidence[f"threshold_{threshold}"]
        trigger_positions[threshold] = (
            f"T{trigger['trigger_turn']}/A{trigger['trigger_action_index']}"
            if trigger["would_trigger"] else "never"
        )
    trigger_tools = {}
    for threshold in (8, 10, 12):
        trigger = evidence[f"threshold_{threshold}"]
        if trigger["would_trigger"]:
            trigger_tools[threshold] = [
                name for _turn, action_index, name in actions
                if action_index <= trigger["trigger_action_index"]
            ]
        else:
            trigger_tools[threshold] = []
    return {
        "path": path.as_posix(),
        "requirement": requirement.value,
        "first_mutation_action": mutation_index,
        "first_mutation_turn": evidence["first_workspace_mutation_turn"],
        "first_mutation_paths": evidence["first_workspace_changed_paths"],
        "actions_before_first_mutation": evidence["pre_mutation_action_count"],
        "turns_before_first_mutation": evidence["pre_mutation_model_turn_count"],
        "thresholds": trigger_positions,
        "trigger_tools": trigger_tools,
        "evidence_available": workspace_evidence_complete,
        "mutation_observed": evidence["first_workspace_mutation_seen"],
        "final_status": trace.get("final_status", ""),
        "tool_names": [name for _turn, _index, name in actions],
    }


def replay_session(path: Path) -> dict[str, Any] | None:
    """Replay session calls as conditional evidence; sessions lack workspace diffs."""
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return None
    user_event = next((event for event in events if event.get("type") == "user_input"), None)
    if not user_event:
        return None
    requirement = classify_mutation_requirement(user_event.get("content", ""))
    if requirement is not MutationRequirement.REQUIRED:
        return None
    shadow = PreFirstMutationShadow(requirement)
    active_turn = 0
    calls: dict[str, tuple[str, int]] = {}
    actions: list[tuple[int, int, str]] = []
    for event in events:
        kind = event.get("type")
        if kind == "model_request_started":
            active_turn = int(event.get("turn", active_turn + 1))
            shadow.observe_model_turn(active_turn)
        elif kind == "tool_call":
            calls[str(event.get("call_id", ""))] = (str(event.get("tool", "")), active_turn)
        elif kind == "tool_result":
            call_id = str(event.get("call_id", ""))
            tool_name, turn = calls.pop(call_id, (str(event.get("tool", "")), active_turn))
            blocked = bool(event.get("blocked")) or bool(event.get("cancelled"))
            before_count = shadow.pre_mutation_action_count
            state = shadow.observe_tool_action(
                tool_name=tool_name,
                turn=turn,
                executed=not blocked,
            )
            if state["pre_mutation_action_count"] > before_count:
                actions.append((state["pre_mutation_action_count"], turn, tool_name))
    evidence = shadow.snapshot()
    return {
        "path": path.as_posix(),
        "requirement": requirement.value,
        "first_mutation_action": None,
        "first_mutation_turn": None,
        "actions_before_first_mutation": evidence["pre_mutation_action_count"],
        "turns_before_first_mutation": evidence["pre_mutation_model_turn_count"],
        "thresholds": {
            threshold: (
                f"T{evidence[f'threshold_{threshold}']['trigger_turn']}/"
                f"A{evidence[f'threshold_{threshold}']['trigger_action_index']} [session-only; conditional]"
                if evidence[f"threshold_{threshold}"]["would_trigger"] else "never"
            )
            for threshold in (8, 10, 12)
        },
        "evidence_available": False,
        "mutation_observed": False,
        "final_status": "SESSION_ONLY",
        "trigger_tools": {
            threshold: [
                name for index, _turn, name in actions
                if index <= evidence[f"threshold_{threshold}"]["trigger_action_index"]
            ] if evidence[f"threshold_{threshold}"]["would_trigger"] else []
            for threshold in (8, 10, 12)
        },
    }


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _print_report(
    rows: list[dict[str, Any]],
    session_rows: list[dict[str, Any]],
    *,
    include_trigger_tools: bool = True,
) -> None:
    print("trace | requirement | first mutation | T8 | T10 | T12")
    for row in rows + session_rows:
        first = (
            f"A{row['first_mutation_action']}@T{row['first_mutation_turn']}"
            if row["first_mutation_action"] is not None
            else "none" if row["evidence_available"] else "unobserved"
        )
        thresholds = row["thresholds"]
        if not row["evidence_available"]:
            thresholds = {
                threshold: f"{value} [conditional]" if value != "never" else "unobserved"
                for threshold, value in thresholds.items()
            }
        print(
            f"{Path(row['path']).name} | {row['requirement']} | {first} | "
            f"{thresholds[8]} | {thresholds[10]} | {thresholds[12]}"
        )
        if include_trigger_tools and "trigger_tools" in row:
            for threshold, tools in row["trigger_tools"].items():
                trigger = row["thresholds"][threshold]
                if trigger != "never":
                    print(f"  T{threshold} tools: {', '.join(tools)}")

    confirmed = [row for row in rows if row["evidence_available"] and row["mutation_observed"]]
    actions = [row["actions_before_first_mutation"] for row in confirmed]
    turns = [row["turns_before_first_mutation"] for row in confirmed]
    print(f"confirmed first-mutation traces: n={len(confirmed)}")
    for label, values in (("actions_before_first_mutation", actions), ("turns_before_first_mutation", turns)):
        print(
            f"{label}: min={min(values) if values else 'n/a'} "
            f"median={_percentile(values, .5)} p75={_percentile(values, .75)} "
            f"p90={_percentile(values, .9)} max={max(values) if values else 'n/a'} n={len(values)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="TaskTrace JSON or session JSONL files")
    parser.add_argument(
        "--roots", nargs="*", type=Path,
        default=None,
        help="Directories recursively scanned for TaskTrace JSON files",
    )
    parser.add_argument("--quiet-tools", action="store_true", help="Omit per-threshold tool lists")
    args = parser.parse_args()
    paths = list(args.paths)
    roots = args.roots
    if roots is None:
        roots = [] if args.paths else [Path("sandbox/eval_results"), Path("benchmark/harbor/jobs")]
    for root in roots:
        if root.exists():
            paths.extend(sorted(root.rglob("*.json")))
    unique_paths = list(dict.fromkeys(path.resolve() for path in paths))
    rows = []
    sessions = []
    for path in unique_paths:
        if path.suffix.casefold() == ".jsonl":
            row = replay_session(path)
            if row:
                sessions.append(row)
            continue
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(trace, dict) or not isinstance(trace.get("turns"), list):
            continue
        row = replay_task_trace(path, trace)
        if row:
            rows.append(row)
    _print_report(rows, sessions, include_trigger_tools=not args.quiet_tools)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
