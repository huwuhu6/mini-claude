"""JSON snapshots continue real, fresh Agent instances without a paid provider."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import mini_claude_agent as module
from core.features import FeatureDefinition
from core.runtime_context.preflight import PreflightResult
from core.run_state import RunStatus
from core.tools.base_tools import ToolResult
from core.tools.registry import ToolSpec
from providers.base import Message


def calls(*labels):
    return [{"id": f"call-{i}", "type": "function", "function": {
        "name": "probe", "arguments": json.dumps({"label": label})}}
        for i, label in enumerate(labels)]


class MockProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create_message(self, messages, tools=None, **kwargs):
        self.requests.append(deepcopy(messages))
        assert self.responses, "Unexpected LLM request"
        return self.responses.pop(0)

    def parse_response(self, response):
        return {"content": "done", "tool_calls": [], "usage": {}, **response}


@pytest.fixture
def agents(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "file.txt").write_text("original", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("features:\n  skills: false\n", encoding="utf-8")
    monkeypatch.setattr(module, "run_preflight", lambda root: PreflightResult(
        "OFFLINE", workspace_root=str(root), workspace_read_write=True))
    monkeypatch.setattr(module.MiniClaudeAgent, "_setup_logging", lambda self: None)
    # Actual components are constructed; no daemon or external provider is started.
    monkeypatch.setattr(module.BackgroundProcessor, "start", lambda self: None)
    monkeypatch.setattr(module, "configure_primary_provider", lambda *args: None)
    created = []

    def make(responses=(), pause_after="A", root=None):
        agent = module.MiniClaudeAgent(config_path=config,
            workspace_root=root or workspace, workspace_confirmed=True,
            runtime_data_root=tmp_path / f"runtime-{len(created)}")
        agent.executions = []
        provider = MockProvider(responses)
        agent.provider_manager.register_provider("mock", provider, is_primary=True)
        agent.compressor.set_provider(provider)

        def probe(label):
            agent.executions.append(label)
            if label == pause_after:
                agent.todo.update([{"content": "keep plan", "status": "in_progress",
                                    "activeForm": "working"}])
                agent.agent_note.replace("remember the original task")
                agent.runtime_context.shell_session.cwd = workspace / "src"
                agent._request_run_pause()
            return ToolResult(f"actual result {label}")

        agent.tool_registry.register(ToolSpec("probe", "Deterministic probe",
            {"type": "object", "properties": {"label": {"type": "string"}},
             "required": ["label"]}, probe))
        agent.feature_manager.register_feature(FeatureDefinition("probe_feature"))
        agent.feature_manager.register_tool_for_feature("probe", "probe_feature")
        created.append(agent)
        return agent, provider

    yield make
    for agent in created:
        agent.shutdown()


def paused(agents, labels=("A", "B", "C")):
    a, provider = agents([{"content": "batch", "tool_calls": calls(*labels)}])
    assert "暂停" in a.run("original task", require_tool_call=True)
    assert a._run_state.status is RunStatus.PAUSED
    return a, json.loads(json.dumps(a.export_run_snapshot(), allow_nan=False))


def test_fresh_agent_roundtrip_preserves_state_and_continues_original_batch(agents):
    a, snapshot = paused(agents)
    b, provider = agents([{}], pause_after=None)
    original_ids = snapshot["run"]["tool_batch"]["tool_calls"]
    b.restore_run_snapshot(snapshot)
    assert b._run_state.run_id == a._run_state.run_id
    assert b._run_state is not a._run_state
    assert b.messages is not a.messages
    assert b.todo is not a.todo and b.runtime_policy is not a.runtime_policy
    assert b.runtime_context.shell_session is not a.runtime_context.shell_session
    assert b.background is not a.background and b.preflight is not a.preflight
    assert b.todo.items == a.todo.items
    assert b.agent_note.read() == a.agent_note.read()
    assert b.agent_note.path != a.agent_note.path
    assert b.runtime_context.cwd == a.runtime_context.cwd
    assert b.attempt_history.all() == a.attempt_history.all()
    assert b.runtime_policy.history is b.attempt_history
    assert b.loop_controller.policy is b.runtime_policy
    assert b._run_state.model_turns_used == 1
    assert b._run_state.max_iterations == a._run_state.max_iterations
    assert b._run_state.require_tool_call
    assert provider.requests == []
    restored_trace = b.trace.current_task
    assert b._continue_run() == "done"
    assert a.executions == ["A"]
    assert b.executions == ["B", "C"]
    assert len(provider.requests) == 1  # next turn only, never regenerate B/C
    assert b._run_state.model_turns_used == 2
    batch = b._run_state.tool_batch
    assert batch.tool_calls == original_ids
    assert batch.next_call_index == 3 and batch.finalized
    result_messages = [m for m in b.messages if m.role == "tool"]
    assert [(m.tool_call_id, m.content) for m in result_messages] == [
        (f"call-{i}", f"actual result {name}") for i, name in enumerate("ABC")]
    assert [m.role for m in provider.requests[0]][:5] == [
        "user", "assistant", "tool", "tool", "tool"]
    assert [m.tool_call_id for m in provider.requests[0] if m.role == "tool"] == [
        "call-0", "call-1", "call-2"]
    assert all(m.role == "user" for m in provider.requests[0][5:])
    assert b._continue_run() == "done"
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    assert b.executions == ["B", "C"]
    assert restored_trace.task_id != a.trace.current_task.task_id
    assert restored_trace.total_tool_calls == 2
    assert [t.result_preview for t in restored_trace.turns[0].tools] == [
        "actual result B", "actual result C"]
    assert restored_trace.request_config["run_id"] == a._run_state.run_id
    assert restored_trace.request_config["resumed_from_trace_task_id"] == a._run_state.trace_task_id
    events = [json.loads(line) for line in b.session_recorder.path.read_text(
        encoding="utf-8").splitlines()]
    link = next(e for e in events if e["type"] == "run_snapshot_restored")
    assert link["source_session_id"] == a.session_recorder.session_id
    assert link["session_id"] != a.session_recorder.session_id


def test_finished_batch_restores_before_next_turn_and_flushes_replan_once(agents):
    a, snapshot = paused(agents, ("A",))
    a._run_state.tool_batch.pending_replan_messages.append("replan feedback")
    snapshot = a.export_run_snapshot()
    b, provider = agents([{}], pause_after=None)
    b.restore_run_snapshot(snapshot)
    assert b._continue_run() == "done"
    assert b.executions == []
    assert b._run_state.model_turns_used == 2
    assert provider.requests[0][-1].role == "user"
    assert provider.requests[0][-1].content.split("\n\n<dynamic_context>")[0] == "replan feedback"
    b._continue_run()
    assert sum(m.content == "replan feedback" for m in b.messages) == 1


def test_snapshot_is_independent_and_memory_is_rebuilt(agents):
    a, snapshot = paused(agents)
    a.memory.record_observation("file.txt", 1, 1, "old observation", "old-hash")
    # The source cache is already stale when exporting; it must never be inherited.
    (a.workdir / "file.txt").write_text("external change", encoding="utf-8")
    snapshot = a.export_run_snapshot()
    a.messages[0].content = "changed later"
    a.todo.items[0]["content"] = "changed later"
    assert snapshot["messages"][0]["content"] == "original task"
    assert snapshot["todo"][0]["content"] == "keep plan"
    snapshot["todo"][0]["content"] = "snapshot edit"
    assert a.todo.items[0]["content"] == "changed later"
    b, _ = agents(pause_after=None)
    b.memory.record_observation("file.txt", 1, 1, "receiver cache", "old-hash")
    b.restore_run_snapshot(snapshot)
    assert b.memory.observations == () and b.memory.recent_files == ()
    assert b._memory_file_fingerprints == {}
    assert b.todo.items[0]["content"] == "snapshot edit"
    snapshot["messages"][0]["content"] = "edit after restore"
    assert b.messages[0].content == "original task"


def test_current_feature_is_rechecked_not_restored_as_authorization(agents):
    _, snapshot = paused(agents)
    b, _ = agents([{}], pause_after=None)
    b.feature_manager.disable("probe_feature")
    b.restore_run_snapshot(snapshot)
    b._continue_run()
    assert b.executions == []
    assert b._run_state.tool_batch.results["call-1"].outcome == "BLOCKED_FEATURE_DISABLED"
    assert b._run_state.tool_batch.results["call-2"].outcome == "BLOCKED_FEATURE_DISABLED"


@pytest.mark.parametrize("mutation", [
    lambda s: s.pop("messages"),
    lambda s: s.update(schema_version=999),
    lambda s: s["run"].update(status="running"),
    lambda s: s["run"].update(model_turns_used=0),
    lambda s: s["run"].update(max_iterations=True),
    lambda s: s["run"]["tool_batch"].update(next_call_index=2),
    lambda s: s["run"]["tool_batch"].update(resolved_call_ids=[]),
    lambda s: s["run"]["tool_batch"]["results"]["call-0"].update(content="wrong"),
    lambda s: s["run"]["tool_batch"]["results"]["call-0"].update(call_id="call-1"),
    lambda s: s["run"]["tool_batch"]["results"]["call-0"].update(outcome="UNKNOWN"),
    lambda s: s["run"]["tool_batch"]["tool_calls"][1].update(id="call-0"),
    lambda s: s["messages"].append(deepcopy(s["messages"][-1])),
    lambda s: s["messages"].insert(2, {"role": "user", "content": "interruption",
        "name": None, "tool_calls": None, "tool_call_id": None}),
    lambda s: s["run"]["tool_batch"].update(seen_signatures=[]),
    lambda s: s["history"].update(next_sequence=99),
    lambda s: s["todo"][0].update(status="unknown"),
    lambda s: s["workspace"].update(cwd="relative"),
    lambda s: s["run"]["tool_batch"].update(finalized=True),
])
def test_invalid_snapshot_rejected_without_partial_mutation(agents, mutation):
    _, snapshot = paused(agents)
    b, _ = agents(pause_after=None)
    b.messages.append(Message("user", "receiver history"))
    b.agent_note.replace("receiver note")
    before = (b._run_state, b.messages, b.todo, b.attempt_history,
              b.agent_note, b.trace, b.runtime_context.cwd)
    b.todo.update([{"content": "receiver todo", "status": "pending", "activeForm": "receiver"}])
    b.memory.record_observation("file.txt", 1, 1, "receiver observation", "receiver-hash")
    values_before = (deepcopy([asdict(m) for m in b.messages]), deepcopy(b.todo.items),
                     b.attempt_history.export_state(), b.memory.observations,
                     b.trace.current_task, b.agent_note.path, dict(b.last_metrics))
    mutation(snapshot)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    assert before == (b._run_state, b.messages, b.todo, b.attempt_history,
                      b.agent_note, b.trace, b.runtime_context.cwd)
    assert b.agent_note.read() == "receiver note"
    assert values_before == ([asdict(m) for m in b.messages], b.todo.items,
                             b.attempt_history.export_state(), b.memory.observations,
                             b.trace.current_task, b.agent_note.path, b.last_metrics)


@pytest.mark.parametrize("change", ["workspace", "cwd_missing", "cwd_unauthorized",
                                      "schema", "handler", "config", "tool_missing"])
def test_current_compatibility_checks_fail_closed(agents, change, tmp_path):
    a, snapshot = paused(agents)
    other = tmp_path / "other"
    other.mkdir()
    b, _ = agents(root=other if change == "workspace" else None)
    if change == "cwd_missing":
        Path(snapshot["workspace"]["cwd"]).rmdir()
    elif change == "cwd_unauthorized":
        snapshot["workspace"]["cwd"] = str(other.resolve())
    elif change == "schema":
        b.tool_registry._tools["probe"].input_schema["required"] = []
    elif change == "handler":
        b.tool_registry._tools["probe"].handler = lambda label: "different"
    elif change == "config":
        b.runtime_policy.loop_detector.intent_horizon += 1
    elif change == "tool_missing":
        del b.tool_registry._tools["probe"]
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    assert b._run_state is None and b.messages == [] and b.executions == []


def test_export_only_at_a_consistent_paused_boundary(agents):
    a, _ = paused(agents)
    a._run_state.status = RunStatus.RUNNING
    with pytest.raises(ValueError):
        a.export_run_snapshot()
    a._run_state.status = RunStatus.PAUSED
    a.messages[-1].tool_call_id = "wrong-call"
    with pytest.raises(ValueError):
        a.export_run_snapshot()


def test_restore_does_not_overwrite_active_run(agents):
    a, snapshot = paused(agents)
    with pytest.raises(ValueError):
        a.restore_run_snapshot(snapshot)
    assert a._run_state.status is RunStatus.PAUSED
    assert a.executions == ["A"]


def test_duplicate_signature_survives_instance_change(agents):
    _, snapshot = paused(agents, ("A", "A", "C"))
    b, _ = agents([{}], pause_after=None)
    b.restore_run_snapshot(snapshot)
    b._continue_run()
    assert b.executions == ["C"]
    assert b._run_state.tool_batch.results["call-1"].outcome == "SKIPPED_DUPLICATE"


def test_restore_keeps_retry_budget_and_second_pause_can_export_again(agents):
    a, ap = agents([{}, {"content": "batch", "tool_calls": calls("A", "B", "C")}])
    a.run("original task", require_tool_call=True)
    assert len(ap.requests) == 2
    assert a._run_state.no_tool_retry_count == 1
    a._run_state.max_iterations = 3
    b, bp = agents([{}], pause_after="B")
    b.restore_run_snapshot(a.export_run_snapshot())
    assert b._run_state.model_turns_used == 2 and b._run_state.no_tool_retry_count == 1
    assert "暂停" in b._continue_run()
    assert b.executions == ["B"] and bp.requests == []
    snapshot = json.loads(json.dumps(b.export_run_snapshot()))
    c, cp = agents([{}], pause_after=None)
    c.restore_run_snapshot(snapshot)
    assert c._continue_run() == "done"
    assert c.executions == ["C"] and len(cp.requests) == 1
    assert c._run_state.max_iterations == c._run_state.model_turns_used == 3
    assert c.todo.items == b.todo.items


def test_note_or_trace_prepare_failure_leaves_receiver_intact(agents, monkeypatch):
    _, snapshot = paused(agents)
    b, _ = agents(pause_after=None)
    b.agent_note.replace("receiver note")
    before = (b.agent_note, b.todo, b.trace, b.attempt_history, b.runtime_context.cwd)
    def fail_write(self, content):
        raise OSError("simulated Note IO failure")
    monkeypatch.setattr(module.AgentNote, "replace", fail_write)
    with pytest.raises(OSError, match="Note IO failure"):
        b.restore_run_snapshot(snapshot)
    assert b._run_state is None and b.messages == []
    assert before == (b.agent_note, b.todo, b.trace, b.attempt_history, b.runtime_context.cwd)
    assert b.agent_note.read() == "receiver note"
    monkeypatch.undo()
    def fail_trace(self, **kwargs):
        raise KeyboardInterrupt("trace preparation interrupted")
    monkeypatch.setattr(module.TraceManager, "start_task", fail_trace)
    with pytest.raises(KeyboardInterrupt, match="trace preparation"):
        b.restore_run_snapshot(snapshot)
    assert b._run_state is None and b.messages == [] and b.trace is before[2]


def test_current_command_policy_and_authority_are_used_at_dispatch(agents, tmp_path, monkeypatch):
    import re
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("outside file", encoding="utf-8")
    batch = calls("A", "B", "C")
    batch[1]["function"] = {"name": "bash", "arguments": json.dumps({"command": "echo blocked"})}
    batch[2]["function"] = {"name": "write_file", "arguments": json.dumps({
        "path": str(protected), "content": "unauthorized overwrite"})}
    a, _ = agents([{"tool_calls": batch}])
    a.workspace_authority.add_root(str(outside))
    a.run("original task", require_tool_call=True)
    b, _ = agents([{}, {}], pause_after=None)
    # Foreground BaseTools and background routing have separate policy instances.
    monkeypatch.setattr(module.CommandPolicy, "HIGH_RISK_EXECUTORS", [re.compile("echo")])
    # Recovery now rejects an unobservable/revoked file target before installing
    # the old batch; it must not regain authority through a serialized snapshot.
    with pytest.raises(ValueError, match="目标前置"):
        b.restore_run_snapshot(a.export_run_snapshot())
    assert b.executions == []
    assert b._run_state is None and b.messages == []
    assert protected.read_text(encoding="utf-8") == "outside file"
    assert b.workspace_authority.additional_roots == []


def test_history_window_precision_and_governance_decisions_are_preserved(agents):
    from core.loop_controller import AttemptHistory, AttemptStatus
    from dataclasses import replace
    a, _ = paused(agents)
    history = AttemptHistory(maxlen=32)
    for i in range(36):
        history.record(turn=1, tool_name="probe", intent_key="probe:failure:",
            args_fingerprint=str(i), status=AttemptStatus.FAILURE,
            failure_category="BUILD_FAILURE", strategy_fingerprint="same",
            timestamp=12.123456789, duration_ms=0.123456789,
            segment_exit_codes=(1, 0), changed_paths=("file.txt",))
    history.update_last(governance_decision="REPLAN", governance_reason="replan required",
                        evidence_ids=("fact-a",), governance_evidence_ids=("fact-b",))
    a.attempt_history = history
    a.runtime_policy.history = history
    b, _ = agents(pause_after=None)
    b.restore_run_snapshot(a.export_run_snapshot())
    assert b.attempt_history.all() == history.all()
    assert len(b.attempt_history) == 32
    assert b.attempt_history.export_state()["next_sequence"] == 37
    assert b.runtime_policy.failure_detector.inspect(b.attempt_history, "BUILD_FAILURE") == (
        a.runtime_policy.failure_detector.inspect(history, "BUILD_FAILURE"))
    assert b.runtime_policy._replans(b.attempt_history) == 1
    event = b.attempt_history.append(replace(history.all()[-1], sequence=1))
    assert event.sequence == 37


def test_pending_restore_bypasses_cleaning_and_compression_until_batch_closes(agents, monkeypatch):
    _, snapshot = paused(agents)
    b, _ = agents(pause_after="B")
    b.restore_run_snapshot(snapshot)
    def unexpected(*args, **kwargs):
        raise AssertionError("pending batch entered pre-LLM pipeline")
    monkeypatch.setattr(module.Compressor, "_clean_tool_chains", unexpected)
    monkeypatch.setattr(b, "_check_auto_compress", unexpected)
    b._continue_run()
    assert b.executions == ["B"]
    assert b._run_state.status is RunStatus.PAUSED
    assert b._run_state.tool_batch.next_call_index == 2
    assert len([m for m in b.messages if m.role == "tool"]) == 2
    assert b.export_run_snapshot()["run"]["tool_batch"]["next_call_index"] == 2


def test_reimport_after_subsequent_new_run_cannot_rewind_old_run(agents):
    _, snapshot = paused(agents)
    b, _ = agents([{}, {}], pause_after=None)
    b.restore_run_snapshot(snapshot)
    b._continue_run()
    b.run("new input")
    with pytest.raises(ValueError, match="拒绝回退重放"):
        b.restore_run_snapshot(snapshot)
    assert b.executions == ["B", "C"]


def test_background_task_table_is_explicitly_unsupported(agents):
    a, _ = paused(agents)
    a.background._tasks["old-job"] = SimpleNamespace(status=module.BackgroundTaskStatus.COMPLETED, created_at=1)
    with pytest.raises(ValueError, match="后台任务表"):
        a.export_run_snapshot()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object(), {1: "bad-key"}])
def test_non_json_data_rejected(agents, invalid):
    _, snapshot = paused(agents)
    b, _ = agents(pause_after=None)
    snapshot["agent_note"] = invalid
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    assert b._run_state is None and b.messages == []


def test_finalized_batch_cannot_hide_later_assistant_tool_batch(agents):
    a, _ = paused(agents, ("A",))
    a._finalize_tool_batch(a._run_state.tool_batch)
    snapshot = a.export_run_snapshot()
    snapshot["messages"].extend(deepcopy(snapshot["messages"][1:]))
    b, _ = agents(pause_after=None)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    assert b._run_state is None


def test_retry_count_outside_existing_runtime_contract_is_rejected(agents):
    _, snapshot = paused(agents)
    snapshot["run"]["no_tool_retry_count"] = 2
    b, _ = agents(pause_after=None)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)


@pytest.mark.parametrize("change", ["missing", "revoked"])
def test_cwd_is_revalidated_before_continue_not_only_at_import(agents, tmp_path, change):
    a, _ = paused(agents)
    outside = tmp_path / "authorized"
    outside.mkdir()
    a.workspace_authority.add_root(str(outside))
    a.runtime_context.shell_session.cwd = outside
    b, bp = agents([{}], pause_after=None)
    b.workspace_authority.add_root(str(outside))
    b.restore_run_snapshot(a.export_run_snapshot())
    if change == "missing":
        outside.rmdir()
    else:
        b.workspace_authority.remove_root(str(outside))
    with pytest.raises(ValueError):
        b._continue_run()
    assert b._run_state.status is RunStatus.PAUSED
    assert b._run_state.tool_batch.next_call_index == 1
    assert b.executions == [] and bp.requests == []
    if change == "missing":
        outside.mkdir()
    else:
        b.workspace_authority.add_root(str(outside))
    assert b._continue_run() == "done"
    assert b.executions == ["B", "C"]


def test_cancelled_restored_handler_closes_unknown_without_replay(agents):
    _, snapshot = paused(agents)
    b, bp = agents(pause_after=None)
    b.restore_run_snapshot(snapshot)
    def interrupted(label):
        b.executions.append(label)
        raise KeyboardInterrupt("handler interrupted")
    b.tool_registry._tools["probe"].handler = interrupted
    result = b._continue_run()
    assert b._run_state.status is RunStatus.CANCELLED
    assert b.executions == ["B"] and bp.requests == []
    results = b._run_state.tool_batch.results
    assert results["call-0"].outcome == "EXECUTED_SUCCESS"
    assert results["call-1"].outcome == "UNKNOWN"
    assert results["call-2"].outcome == "NOT_EXECUTED"
    assert [m.tool_call_id for m in b.messages if m.role == "tool"] == [
        "call-0", "call-1", "call-2"]
    assert b._continue_run() == result
    assert b.executions == ["B"]
    with pytest.raises(ValueError):
        b.export_run_snapshot()


def test_current_cwd_may_be_explicitly_authorized_but_old_grants_are_not_imported(agents, tmp_path):
    a, _ = paused(agents)
    outside = tmp_path / "explicit-root"
    outside.mkdir()
    a.workspace_authority.add_root(str(outside))
    a.runtime_context.shell_session.cwd = outside
    snapshot = a.export_run_snapshot()
    b, _ = agents(pause_after=None)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(snapshot)
    b.workspace_authority.add_root(str(outside))
    b.restore_run_snapshot(snapshot)
    assert b.runtime_context.cwd == outside
    assert b.workspace_authority.additional_roots == [outside]


def test_budget_exhausted_after_completed_batch_does_not_request_model_or_replay(agents):
    a, _ = paused(agents, ("A",))
    a._run_state.max_iterations = 1
    b, bp = agents(pause_after=None)
    b.restore_run_snapshot(a.export_run_snapshot())
    b._continue_run()
    assert b._run_state.status is RunStatus.FAILED
    assert b.executions == [] and bp.requests == []
    assert b._run_state.model_turns_used == 1
    assert len([m for m in b.messages if m.role == "tool"]) == 1


def test_changed_workspace_cannot_replay_a_stale_pending_overwrite(agents):
    batch = calls("A", "B")
    batch[1]["function"] = {"name": "write_file", "arguments": json.dumps({
        "path": "file.txt", "content": "stale planned overwrite"})}
    a, _ = agents([{"tool_calls": batch}])
    a.run("original task", require_tool_call=True)
    snapshot = a.export_run_snapshot()
    target = a.workdir / "file.txt"
    target.write_text("user edit after snapshot", encoding="utf-8")
    b, _ = agents([{}], pause_after=None)
    with pytest.raises(ValueError, match="工作区内容"):
        b.restore_run_snapshot(snapshot)
    assert target.read_text(encoding="utf-8") == "user edit after snapshot"
    assert b._run_state is None and b.messages == []


def test_workspace_change_after_import_is_rejected_before_first_dispatch(agents):
    a, snapshot = paused(agents)
    b, bp = agents(pause_after=None)
    b.restore_run_snapshot(snapshot)
    target = a.workdir / "file.txt"
    target.write_text("late external change", encoding="utf-8")
    with pytest.raises(ValueError, match="工作区内容"):
        b._continue_run()
    assert b._run_state.status is RunStatus.PAUSED
    assert b._run_state.tool_batch.next_call_index == 1
    assert b.executions == [] and bp.requests == []


def test_imported_workspace_guard_survives_continue_initialization_failure(agents, monkeypatch):
    a, snapshot = paused(agents)
    b, bp = agents(pause_after=None)
    b.restore_run_snapshot(snapshot)
    recorder = b.session_recorder.record
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("continue preparation")
    monkeypatch.setattr(b.session_recorder, "record", interrupted)
    with pytest.raises(KeyboardInterrupt):
        b._continue_run()
    assert b._run_state.status is RunStatus.PAUSED
    monkeypatch.setattr(b.session_recorder, "record", recorder)
    (a.workdir / "file.txt").write_text("late external change", encoding="utf-8")
    with pytest.raises(ValueError, match="工作区内容"):
        b._continue_run()
    assert b.executions == [] and bp.requests == []


def test_import_guard_is_consumed_after_real_write_and_new_pause_can_be_exported(agents, monkeypatch):
    batch = calls("A", "B", "C")
    batch[1]["function"] = {"name": "write_file", "arguments": json.dumps({
        "path": "file.txt", "content": "run-owned write"})}
    a, _ = agents([{"tool_calls": batch}])
    a.run("original task", require_tool_call=True)
    b, bp = agents(pause_after=None)
    b.restore_run_snapshot(a.export_run_snapshot())
    assert b._run_state.workspace_checkpoint_digest is not None
    original_write = b.tools.write_file
    writes = []
    def write_then_pause(*args, **kwargs):
        writes.append((args, kwargs))
        result = original_write(*args, **kwargs)
        b._request_run_pause()
        return result
    monkeypatch.setattr(b.tools, "write_file", write_then_pause)
    b._continue_run()
    assert len(writes) == 1 and bp.requests == []
    assert b._run_state.status is RunStatus.PAUSED
    assert b._run_state.tool_batch.next_call_index == 2
    assert b._run_state.workspace_checkpoint_digest is None
    assert (a.workdir / "file.txt").read_text(encoding="utf-8") == "run-owned write"
    c, cp = agents([{}], pause_after=None)
    c.restore_run_snapshot(b.export_run_snapshot())
    assert c._continue_run() == "done"
    assert c.executions == ["C"] and len(cp.requests) == 1
    assert len(writes) == 1
