"""Synthetic invariants for language-neutral progress governance."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from core.progress_governance import (  # noqa: E402
    BlockerLifecycle,
    CompletionGuard,
    GovernanceAction,
    ObservationNormalizer,
    ProgressTracker,
)


def observe(tracker, turn, action, output, before=None, after=None, **kwargs):
    return tracker.observe(
        turn=turn,
        tool_name="bash",
        intent_key=action,
        result_text=output,
        workspace_before=before,
        workspace_after=after,
        **kwargs,
    )


def test_observation_normalizer_keeps_semantic_counts_and_removes_noise():
    first = ObservationNormalizer.normalize("5 failed\n duration=1.2s\n")
    second = ObservationNormalizer.normalize("3 failed\n duration=9.8s\n")
    assert "5 failed" in first
    assert "duration=<noise>" in first
    assert first != second
    assert ObservationNormalizer.normalize("a\r\nb") == "a\nb"


def test_same_action_with_changing_observation_is_progress():
    tracker = ProgressTracker()
    observe(tracker, 1, "test", "5 failed", {"x": "0"}, {"x": "0"})
    decision = observe(tracker, 2, "test", "3 failed", {"x": "0"}, {"x": "1"})
    assert decision.progress_detected
    assert "SAME_ACTION_NEW_OBSERVATION" in decision.progress_reason
    assert decision.action is GovernanceAction.ALLOW


def test_different_actions_with_same_observation_stagnate():
    tracker = ProgressTracker()
    for turn, action in enumerate(("curl", "wget", "probe"), 1):
        decision = observe(tracker, turn, action, "connection refused", {"x": "0"}, {"x": "0"})
    assert not decision.progress_detected
    assert "SAME_OBSERVATION" in decision.stagnation_reason
    assert decision.action is GovernanceAction.REPLAN


def test_state_oscillation_requires_repeated_cycle():
    tracker = ProgressTracker()
    states = ("A", "B", "A", "B")
    decisions = [
        observe(tracker, i, "poll", f"state={state}", {"state": state}, {"state": state})
        for i, state in enumerate(states, 1)
    ]
    assert not decisions[2].oscillation_detected
    assert decisions[3].oscillation_detected
    assert decisions[3].action is GovernanceAction.TERMINATE
    assert decisions[3].reason == "SEMANTIC_OSCILLATION"


def test_single_partial_cycle_is_not_oscillation():
    tracker = ProgressTracker()
    decisions = [
        observe(tracker, i, "poll", f"observed: {state}")
        for i, state in enumerate(("A", "B", "A"), 1)
    ]
    assert not decisions[-1].oscillation_detected
    assert decisions[-1].action is not GovernanceAction.TERMINATE


def test_period_three_semantic_oscillation_is_detected():
    tracker = ProgressTracker()
    decisions = [
        observe(tracker, i, "poll", f"phase={state}")
        for i, state in enumerate(("A", "B", "C", "A", "B", "C"), 1)
    ]
    assert not decisions[2].oscillation_detected
    assert decisions[-1].oscillation_detected
    assert decisions[-1].reason == "SEMANTIC_OSCILLATION"


def test_workspace_diff_cannot_hide_semantic_oscillation():
    tracker = ProgressTracker()
    decisions = [
        observe(
            tracker, i, "poll", f"status={state}",
            {"run": str(i - 1)}, {"run": str(i)},
        )
        for i, state in enumerate(("A", "B", "A", "B"), 1)
    ]
    assert decisions[-1].oscillation_detected
    assert decisions[-1].action is GovernanceAction.TERMINATE


def test_improving_observations_are_not_oscillation():
    tracker = ProgressTracker()
    decisions = [
        observe(tracker, i, "test", output)
        for i, output in enumerate(("5 failed", "3 failed", "1 failed", "PASS"), 1)
    ]
    assert all(not decision.oscillation_detected for decision in decisions)
    assert decisions[-1].action is not GovernanceAction.TERMINATE


def test_observation_evolution_without_workspace_mutation_is_progress():
    tracker = ProgressTracker()
    observe(tracker, 1, "poll", "INITIALIZING", {}, {})
    observe(tracker, 2, "poll", "MIGRATING", {}, {})
    decision = observe(tracker, 3, "poll", "READY", {}, {})
    assert decision.progress_detected
    assert decision.action is GovernanceAction.ALLOW


def test_related_recovery_resolves_permission_blocker():
    tracker = ProgressTracker()
    observe(
        tracker, 1, "bash:EXECUTE:export", "Permission denied",
        {"cfg": "old"}, {"cfg": "old"},
        success=False, failure_category="PERMISSION_DENIED",
    )
    observe(tracker, 2, "bash:EXECUTE:export", "config updated", {"cfg": "old"}, {"cfg": "new"})
    decision = observe(
        tracker, 3, "bash:EXECUTE:export", "success", {"cfg": "new"}, {"cfg": "new"},
    )
    assert decision.open_blocker_count == 0
    assert tracker.blockers.all()[0].lifecycle is BlockerLifecycle.RESOLVED


def test_unrelated_success_does_not_resolve_blocker():
    tracker = ProgressTracker()
    observe(
        tracker, 1, "bash:EXECUTE:build", "Package not found",
        {"x": "0"}, {"x": "0"}, success=False,
        failure_category="PACKAGE_NOT_FOUND",
    )
    observe(tracker, 2, "bash:EXECUTE:echo", "hello", {"x": "0"}, {"x": "0"})
    assert tracker.blockers.unresolved()


def test_completion_guard_intercepts_then_blocks_unsupported_completion():
    tracker = ProgressTracker()
    observe(
        tracker, 1, "build", "Command not found", {}, {}, success=False,
        failure_category="COMMAND_NOT_FOUND",
    )
    guard = CompletionGuard()
    assert guard.check(tracker).action is GovernanceAction.REPLAN
    assert guard.check(tracker).action is GovernanceAction.TERMINATE


def test_completion_guard_allows_verified_recovery():
    tracker = ProgressTracker()
    observe(
        tracker, 1, "build", "Connection refused", {"x": "0"}, {"x": "0"},
        success=False, failure_category="NETWORK_UNREACHABLE",
    )
    observe(tracker, 2, "build", "READY", {"x": "0"}, {"x": "1"})
    assert not CompletionGuard().check(tracker).should_terminate


def test_workspace_diff_alone_is_not_progress():
    tracker = ProgressTracker()
    observe(tracker, 1, "write", "still invalid", {"x": "0"}, {"x": "1"})
    decision = observe(tracker, 2, "write", "still invalid", {"x": "1"}, {"x": "2"})
    assert not decision.progress_detected
    assert "SAME_OBSERVATION" in decision.stagnation_reason
