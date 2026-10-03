"""Deterministic coverage for the observational pre-first-mutation shadow."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.loop_controller import RuntimeDecision, RuntimePolicy
from core.runtime_context.execution_shadow import (
    MutationRequirement,
    PreFirstMutationShadow,
    classify_mutation_requirement,
)
from core.runtime_context.shell_session import ShellSession
from core.runtime_context.workspace_state import WorkspaceStateGuard
from core.tracing.manager import TraceManager
from agent.mini_claude_agent import MiniClaudeAgent
from providers.deepseek import DeepseekProvider


@pytest.fixture
def tmp_path():
    path = Path.cwd() / f".test-pre-mutation-shadow-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        if path.resolve().parent == Path.cwd().resolve():
            shutil.rmtree(path)


def _feed(shadow, count, *, turns=None, mutation_at=None):
    for index in range(1, count + 1):
        turn = turns(index) if turns else (index - 1) // 3 + 1
        shadow.observe_model_turn(turn)
        shadow.observe_tool_action(
            tool_name="read_file",
            turn=turn,
            executed=True,
            changed_paths=("src/example.py",) if mutation_at == index else (),
        )
    return shadow.snapshot()


def test_classifier_is_conservative_and_task_scoped():
    assert classify_mutation_requirement(
        "I have provided the source tree. Please implement the requested feature."
    ) is MutationRequirement.REQUIRED
    assert classify_mutation_requirement("Fix the parser bug in the existing code.") is MutationRequirement.REQUIRED
    assert classify_mutation_requirement("请基于当前代码完成一次错误处理修复。") is MutationRequirement.REQUIRED
    assert classify_mutation_requirement("请帮我分析原因并修复它。") is MutationRequirement.REQUIRED
    assert classify_mutation_requirement("Please inspect the repository and summarize the architecture.") is MutationRequirement.NOT_REQUIRED
    assert classify_mutation_requirement("请分析当前实现并说明风险。") is MutationRequirement.NOT_REQUIRED
    assert classify_mutation_requirement("What would be a good approach to this problem?") is MutationRequirement.UNKNOWN
    assert classify_mutation_requirement("Explain how to fix this bug.") is MutationRequirement.NOT_REQUIRED


def test_read_only_and_unknown_tasks_never_trigger():
    for requirement in (MutationRequirement.NOT_REQUIRED, MutationRequirement.UNKNOWN):
        shadow = PreFirstMutationShadow(requirement)
        result = _feed(shadow, 30)
        assert result["pre_mutation_action_count"] == 0
        assert all(not result[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12))


def test_simple_coding_task_mutates_before_threshold():
    shadow = PreFirstMutationShadow(MutationRequirement.REQUIRED)
    for index, tool in enumerate(("read_file", "search_code", "read_file", "edit_file"), 1):
        shadow.observe_model_turn(index)
        shadow.observe_tool_action(
            tool_name=tool,
            turn=index,
            executed=True,
            changed_paths=("src/example.py",) if tool == "edit_file" else (),
        )
    result = shadow.snapshot()
    assert result["first_workspace_mutation_action_index"] == 4
    assert result["first_workspace_mutation_turn"] == 4
    assert result["pre_mutation_action_count"] == 3
    assert result["pre_mutation_model_turn_count"] == 4
    assert all(not result[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12))


def test_threshold_candidates_8_10_and_12():
    at_8 = _feed(PreFirstMutationShadow("REQUIRED"), 8)
    assert [at_8[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12)] == [True, False, False]
    assert at_8["threshold_8"]["trigger_turn"] == 3
    assert at_8["threshold_8"]["trigger_action_index"] == 8

    at_10 = _feed(PreFirstMutationShadow("REQUIRED"), 10)
    assert [at_10[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12)] == [True, True, False]
    assert at_10["threshold_10"]["trigger_action_index"] == 10

    at_12 = _feed(PreFirstMutationShadow("REQUIRED"), 12)
    assert [at_12[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12)] == [True, True, True]


def test_minimum_turn_count_is_required_for_threshold_candidates():
    shadow = PreFirstMutationShadow("REQUIRED")
    for index in range(8):
        shadow.observe_model_turn(1)
        state = shadow.observe_tool_action(
            tool_name="read_file", turn=1, executed=True,
        )
    assert state["pre_mutation_action_count"] == 8
    assert state["pre_mutation_model_turn_count"] == 1
    assert not state["threshold_8"]["would_trigger"]

    shadow.observe_model_turn(2)
    shadow.observe_tool_action(tool_name="read_file", turn=2, executed=True)
    shadow.observe_model_turn(3)
    state = shadow.observe_tool_action(tool_name="read_file", turn=3, executed=True)
    assert state["threshold_8"]["would_trigger"]
    assert state["threshold_8"]["trigger_action_index"] == 10


def test_mutation_at_nine_preserves_only_threshold_8_candidate():
    shadow = PreFirstMutationShadow("REQUIRED")
    result = _feed(shadow, 9, mutation_at=9)
    assert result["pre_mutation_action_count"] == 8
    assert result["first_workspace_mutation_action_index"] == 9
    assert result["first_workspace_mutation_turn"] == 3
    assert result["pre_mutation_model_turn_count"] == 3
    assert result["first_workspace_changed_paths"] == ["src/example.py"]
    assert [result[f"threshold_{n}"]["would_trigger"] for n in (8, 10, 12)] == [True, False, False]
    shadow.observe_model_turn(5)
    shadow.observe_tool_action(tool_name="read_file", turn=5, executed=True)
    assert shadow.snapshot()["pre_mutation_action_count"] == 8
    assert shadow.snapshot()["pre_mutation_model_turn_count"] == 3


def test_bash_write_is_detected_from_workspace_snapshot(tmp_path):
    guard = WorkspaceStateGuard(tmp_path)
    before = guard.snapshot()
    result = ShellSession(tmp_path).execute("echo shadow-write > created-by-bash.txt")
    assert result["execution_success"]
    changed = guard.mutation(before, guard.snapshot()).changed_paths
    assert changed == ("created-by-bash.txt",)

    shadow = PreFirstMutationShadow("REQUIRED")
    state = shadow.observe_tool_action(
        tool_name="bash", turn=1, executed=True, changed_paths=changed,
    )
    assert state["first_workspace_mutation_seen"]
    assert state["first_workspace_changed_paths"] == ["created-by-bash.txt"]
    assert state["pre_mutation_action_count"] == 0


def test_outside_workspace_change_does_not_count_as_mutation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-tmp-like.txt"
    guard = WorkspaceStateGuard(workspace)
    before = guard.snapshot()
    outside.write_text("outside", encoding="utf-8")
    try:
        changed = guard.mutation(before, guard.snapshot()).changed_paths
        shadow = PreFirstMutationShadow("REQUIRED")
        result = shadow.observe_tool_action(
            tool_name="bash", turn=1, executed=True, changed_paths=changed,
        )
        assert not result["first_workspace_mutation_seen"]
        assert result["pre_mutation_action_count"] == 1
    finally:
        outside.unlink(missing_ok=True)


def test_ignored_workspace_artifacts_do_not_end_shadow(tmp_path):
    for relative in (".agent/logs/out.txt", "build/out.bin", "dist/out.js"):
        guard = WorkspaceStateGuard(tmp_path)
        before = guard.snapshot()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ignored", encoding="utf-8")
        try:
            changed = guard.mutation(before, guard.snapshot()).changed_paths
            result = PreFirstMutationShadow("REQUIRED").observe_tool_action(
                tool_name="bash", turn=1, executed=True, changed_paths=changed,
            )
            assert not result["first_workspace_mutation_seen"]
            assert result["pre_mutation_action_count"] == 1
        finally:
            path.unlink(missing_ok=True)


def test_blocked_action_is_excluded_but_executed_failure_counts():
    shadow = PreFirstMutationShadow("REQUIRED")
    blocked = shadow.observe_tool_action(tool_name="bash", turn=1, executed=False)
    assert blocked["pre_mutation_action_count"] == 0
    failed_execution = shadow.observe_tool_action(tool_name="bash", turn=1, executed=True)
    assert failed_execution["pre_mutation_action_count"] == 1

    external_mutation = shadow.observe_tool_action(
        tool_name="bash", turn=2, executed=False, changed_paths=("src/external.py",),
    )
    assert external_mutation["first_workspace_mutation_seen"]


def test_todowrite_is_recorded_separately_and_not_counted():
    shadow = PreFirstMutationShadow("REQUIRED")
    result = shadow.observe_tool_action(tool_name="TodoWrite", turn=1, executed=True)
    assert result["todo_write_count"] == 1
    assert result["pre_mutation_action_count"] == 0
    assert result["threshold_8"]["would_trigger"] is False

    note_update = shadow.observe_tool_action(
        tool_name="update_agent_note", turn=2, executed=True,
    )
    assert note_update["pre_mutation_action_count"] == 0


def test_shadow_observation_does_not_change_runtime_decisions():
    def run(with_shadow):
        policy = RuntimePolicy()
        shadow = PreFirstMutationShadow("REQUIRED") if with_shadow else None
        actions = []
        for index in range(5):
            event, decision = policy.record_attempt(
                turn=index + 1,
                tool_name="bash",
                intent_key="bash:probe:service",
                args_fingerprint=str(index),
                success=False,
                result_text="HTTP 503",
                failure_category="NETWORK_UNREACHABLE",
                observed_failure=True,
                semantic_status="UNHEALTHY",
                observation="HTTP_503",
            )
            actions.append((decision.action, decision.reason))
            if shadow:
                shadow.observe_tool_action(
                    tool_name=event.tool_name,
                    turn=event.turn,
                    executed=True,
                    changed_paths=event.changed_paths,
                )
        return actions

    assert run(with_shadow=True) == run(with_shadow=False)
    assert run(with_shadow=True)[-1][0] is RuntimeDecision.HARD_STOP


def test_shadow_evidence_is_written_once_at_task_level(tmp_path):
    trace_manager = TraceManager(trace_dir=tmp_path)
    trace_manager.start_task(user_prompt="Implement a small feature.")
    trace_manager.start_turn(0)
    shadow = PreFirstMutationShadow("REQUIRED")
    trace_manager.record_execution_commitment_shadow(shadow.snapshot())
    shadow.observe_tool_action(tool_name="read_file", turn=1, executed=True)
    trace_manager.record_execution_commitment_shadow(shadow.snapshot())
    path = trace_manager.end_task("FAILED")
    persisted = json.loads(Path(path).read_text(encoding="utf-8"))
    assert persisted["execution_commitment_shadow"]["pre_mutation_action_count"] == 1
    assert "execution_commitment_shadow" not in persisted["turns"][0]


def test_agent_wiring_records_real_executed_actions_without_changing_request_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=runtime,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    responses = iter([
        {
            "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call-1", "type": "function", "function": {
                    "name": "bash", "arguments": '{"command":"echo observed"}',
                },
            }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        {
            "choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": []},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    ])
    requests = []

    def create_message(messages, tools, *, system, **_kwargs):
        requests.append((tuple(tool.name for tool in tools), system))
        return next(responses)

    provider.create_message = create_message
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    try:
        assert agent.chat("Please implement a small feature.") == "done"
        trace_path = next((runtime / "traces").glob("task_*.json"))
        persisted = json.loads(trace_path.read_text(encoding="utf-8"))
        evidence = persisted["execution_commitment_shadow"]
        assert evidence["mutation_requirement"] == "REQUIRED"
        assert evidence["pre_mutation_action_count"] == 1
        assert evidence["pre_mutation_model_turn_count"] == 2
        assert persisted["final_status"] == "SUCCESS"
        assert persisted["turns"][0]["tools"][0]["tool_name"] == "bash"
        assert requests[0][0] == tuple(tool["name"] for tool in agent._get_llm_tools())
        assert all(system == agent.system_prompt for _tools, system in requests)
    finally:
        agent.shutdown()
