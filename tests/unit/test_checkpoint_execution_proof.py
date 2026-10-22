"""NONE requires positive pre-handler evidence, never an exception's class."""
import json

import pytest

from test_run_snapshot import agents, calls
from test_checkpoint_store import ready, completed
from core.checkpoint_store import CheckpointError
from core.run_state import ToolCallResultState
from core.tools.registry import ToolRegistry, ToolSpec


@pytest.mark.parametrize("hitl", [False, True])
def test_nested_binding_error_after_effect_must_not_be_none(agents, hitl):
    agent, provider = agents([{"tool_calls": calls("A", "B")}, {}, {}, {}], pause_after=None)
    ledger = agent.workdir / "external-effect.txt"
    nested = ToolRegistry()

    def requires_argument(required):
        return required

    nested.register(ToolSpec("nested", "nested tool", {"type": "object"}, requires_argument))

    def effect_then_nested_error(label):
        agent.executions.append(label)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(label + "\n")
        nested.execute("nested", {})

    agent.tool_registry.get_spec("probe").handler = effect_then_nested_error
    agent.enable_checkpoints()
    if hitl:
        agent.enable_hitl()
    agent.run("outer tool with effect")
    if hitl:
        run = agent._run_state.run_id
        approval = agent.get_pending_approval(run)
        agent.decide_approval(run, approval.approval_id, approved=True, actor="regression")
        agent.resume_checkpoint(run)
    record = agent._checkpoint_store.load(agent._run_state.run_id)
    fact = json.loads(record["journal"][0]["result_json"])
    assert fact["effect"] == "UNCERTAIN"
    assert fact["outcome"] == "UNKNOWN"
    assert [row["state"] for row in record["journal"]] == ["UNKNOWN", "NOT_EXECUTED"]
    assert ledger.read_text(encoding="utf-8") == "A\n"
    assert agent.executions == ["A"] and len(provider.requests) == 1
    assert "UNKNOWN" in agent.resume_checkpoint(agent._run_state.run_id)
    assert "UNKNOWN" in agent.run("do not retry")
    assert agent.executions == ["A"] and len(provider.requests) == 1


@pytest.mark.parametrize("point", ["handler", "nested_registry", "started_event", "result_string", "snapshot_encoding"])
def test_exceptions_after_entry_never_become_none(agents, monkeypatch, point):
    from core.tools.registry import ToolArgumentBindingError
    agent, provider = agents([{"tool_calls": calls("A", "B")}, {}], pause_after=None)

    class BadString:
        def __str__(self):
            raise ToolArgumentBindingError("result conversion failed")

    def handler(label):
        agent.executions.append(label)
        if point == "handler":
            raise TypeError("handler failed after effect")
        if point == "nested_registry":
            raise ToolArgumentBindingError("nested bind failed after effect")
        if point == "result_string":
            return BadString()
        return "done"

    agent.tool_registry.get_spec("probe").handler = handler
    agent.enable_checkpoints()
    store = agent._checkpoint_store
    emit = agent._emit_ui_event
    encode = store._json

    def event(event_type, **kwargs):
        if event_type == "tool_execution_started" and point == "started_event":
            raise ToolArgumentBindingError("event failed after entry marker")
        return emit(event_type, **kwargs)

    def encoding(value):
        if point == "snapshot_encoding" and isinstance(value, dict) and (value.get("run", {}).get("tool_batch") or {}).get("results"):
            raise ValueError("injected snapshot serialization failure")
        return encode(value)

    monkeypatch.setattr(agent, "_emit_ui_event", event)
    monkeypatch.setattr(store, "_json", encoding)
    try:
        agent.run("injected failure")
    except (CheckpointError, ValueError):
        pass
    monkeypatch.setattr(store, "_json", encode)
    store.acquire(agent._run_state.run_id)
    record = store.recover(agent._run_state.run_id)
    first = record["journal"][0]
    assert first["state"] == "UNKNOWN"
    if first["result_json"]:
        assert json.loads(first["result_json"])["effect"] != "NONE"
    assert agent.executions == ([] if point == "started_event" else ["A"])
    assert len(provider.requests) == 1


@pytest.mark.parametrize("outcome", ["INVALID_ARGUMENTS", "BLOCKED_PERMISSION_DENIED", "NOT_EXECUTED"])
def test_unspecified_effect_is_conservative(outcome):
    assert ToolCallResultState("call", outcome, "legacy fact").effect == "UNCERTAIN"


@pytest.mark.parametrize("writer", ["save_snapshot", "finish"])
@pytest.mark.parametrize("reason", [None, "NO_SUCH_REASON", "PERMISSION_DENIED"])
def test_store_rejects_none_without_matching_reason(ready, writer, reason):
    store, revision, data = ready
    data = completed(data, "INVALID_ARGUMENTS")
    raw = data["run"]["tool_batch"]["results"]["id-0"]
    raw.update(result="FAILED", effect="NONE")
    if reason is not None:
        raw["non_execution_reason"] = reason
    if writer == "finish":
        data = completed(data, "NOT_EXECUTED")
        data["run"].update(status="failed", terminal_result="stopped")
        data["run"]["tool_batch"].update(status="failed", finalized=True)
    with pytest.raises((CheckpointError, ValueError)):
        if writer == "finish":
            store.finish("run-1", revision, "failed", "stopped", data)
        else:
            store.save_snapshot("run-1", revision, data, paused=True)
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"


@pytest.mark.parametrize("writer", ["save_snapshot", "finish"])
def test_store_rejects_legacy_not_executed_after_running(ready, writer):
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    data = completed(data, "INVALID_ARGUMENTS")
    if writer == "finish":
        data = completed(data, "NOT_EXECUTED")
        data["run"].update(status="failed", terminal_result="stopped")
        data["run"]["tool_batch"].update(status="failed", finalized=True)
    with pytest.raises((CheckpointError, ValueError)):
        if writer == "finish":
            store.finish("run-1", revision, "failed", "stopped", data)
        else:
            store.save_snapshot("run-1", revision, data, paused=True)
    assert store.load("run-1")["journal"][0]["state"] == "RUNNING"


def test_old_unproven_none_is_read_as_uncertain_without_rewriting():
    from core.run_snapshot import decode_tool_result
    raw = {"call_id": "call", "outcome": "INVALID_ARGUMENTS", "content": "old",
           "result": "FAILED", "effect": "NONE"}
    assert decode_tool_result(raw).effect == "UNCERTAIN"
    assert raw["effect"] == "NONE"


def test_outer_binding_rejection_never_enters_running(agents, monkeypatch):
    agent, provider = agents([{"tool_calls": calls("A")}, {}], pause_after=None)

    def needs_more(label, required):
        raise AssertionError("must not enter handler")

    agent.tool_registry.get_spec("probe").handler = needs_more
    agent.enable_checkpoints()
    running = []
    mark_running = agent._checkpoint_store.mark_running

    def mark(*args, **kwargs):
        running.append(True)
        return mark_running(*args, **kwargs)

    monkeypatch.setattr(agent._checkpoint_store, "mark_running", mark)
    agent.run("bad outer arguments")
    fact = json.loads(agent._checkpoint_store.load(agent._run_state.run_id)["journal"][0]["result_json"])
    assert not running and not agent.executions
    assert fact["effect"] == "NONE" and fact["non_execution_reason"] == "ARGUMENTS_REJECTED"


@pytest.mark.parametrize("writer", ["save_snapshot", "finish"])
@pytest.mark.parametrize("started", [False, True])
def test_store_requires_prepared_even_with_valid_reason(ready, writer, started):
    store, revision, data = ready
    if started:
        revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    data = completed(data, "INVALID_ARGUMENTS")
    data["run"]["tool_batch"]["results"]["id-0"].update(
        result="FAILED", effect="NONE", non_execution_reason="ARGUMENTS_REJECTED")
    if writer == "finish":
        data = completed(data, "NOT_EXECUTED")
        data["run"]["tool_batch"]["results"]["id-1"].update(
            result="FAILED", effect="NONE", non_execution_reason="BATCH_ABORTED")
        data["run"].update(status="failed", terminal_result="stopped")
        data["run"]["tool_batch"].update(status="failed", finalized=True)

    def save():
        if writer == "finish":
            return store.finish("run-1", revision, "failed", "stopped", data)
        return store.save_snapshot("run-1", revision, data, paused=True)

    if started:
        with pytest.raises(CheckpointError, match="已进入执行"):
            save()
        assert store.load("run-1")["journal"][0]["state"] == "RUNNING"
    else:
        save()
        fact = json.loads(store.load("run-1")["journal"][0]["result_json"])
        assert fact["effect"] == "NONE" and fact["non_execution_reason"] == "ARGUMENTS_REJECTED"
