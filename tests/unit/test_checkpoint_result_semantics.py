"""Execution outcome and effect certainty are independent durable facts."""
import json

import pytest

from test_run_snapshot import agents, calls
from test_checkpoint_store import snapshot, completed, ready
from core.run_snapshot import decode_run
from core.tools.base_tools import ToolResult


def test_result_defaults_and_timeout_are_conservative():
    failed = ToolResult("failure", success=False)
    assert (failed.result, failed.effect) == ("FAILED", "UNCERTAIN")
    timeout = ToolResult("timeout", timed_out=True)
    assert (timeout.result, timeout.effect) == ("UNKNOWN", "UNCERTAIN")
    assert not timeout.execution_success and not timeout.success


@pytest.mark.parametrize("mode", ["timeout", "uncertain_failure", "false_none"])
def test_effect_after_dispatch_cannot_be_retried_by_next_model_turn(agents, mode):
    agent, provider = agents([{"tool_calls": calls("A", "B")}, {"tool_calls": calls("A")}, {}], pause_after=None)

    def uncertain(label):
        agent.executions.append(label)
        if mode == "timeout":
            return ToolResult("response lost after effect", success=False, timed_out=True)
        if mode == "false_none":
            return ToolResult("handler cannot prove it never started", success=False, effect="NONE")
        return ToolResult("partial operation failed", success=False)

    agent.tool_registry.get_spec("probe").handler = uncertain
    agent.enable_checkpoints()
    agent.run("task")
    record = agent._checkpoint_store.load(agent._run_state.run_id)
    assert [row["state"] for row in record["journal"]] == ["UNKNOWN", "NOT_EXECUTED"]
    fact = json.loads(record["journal"][0]["result_json"])
    assert fact["effect"] == "UNCERTAIN"
    assert fact["result"] == ("UNKNOWN" if mode == "timeout" else "FAILED")
    assert agent.executions == ["A"] and len(provider.requests) == 1
    assert "UNKNOWN" in agent.run("try again")


def test_legacy_failure_does_not_invent_absence_of_effects():
    data = completed(snapshot(), outcome="EXECUTED_FAILURE")
    fact = decode_run(data["run"]).tool_batch.results["id-0"]
    assert (fact.result, fact.effect) == ("FAILED", "UNCERTAIN")
    assert set(data["run"]["tool_batch"]["results"]["id-0"]) == {"call_id", "outcome", "content"}


def test_legacy_unknown_never_becomes_no_effect():
    from core.run_state import ToolCallResultState
    fact = ToolCallResultState("id", "UNKNOWN", "unknown")
    assert (fact.result, fact.effect) == ("UNKNOWN", "UNCERTAIN")


def test_explicit_unknown_none_snapshot_is_rejected():
    data = completed(snapshot(), outcome="EXECUTED_FAILURE")
    data["run"]["tool_batch"]["results"]["id-0"].update(result="UNKNOWN", effect="NONE")
    with pytest.raises(ValueError):
        decode_run(data["run"])


def test_legacy_journal_results_are_not_rewritten_when_projection_gains_fields(ready):
    from copy import deepcopy
    from core.run_snapshot import encode_run
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    data = completed(data)
    revision = store.save_snapshot("run-1", revision, data, paused=True)
    original = store.load("run-1")["journal"][0]["result_json"]
    upgraded = deepcopy(data)
    upgraded["run"] = encode_run(decode_run(data["run"]))
    store.save_snapshot("run-1", revision, upgraded, paused=True)
    assert store.load("run-1")["journal"][0]["result_json"] == original
    assert set(json.loads(original)) == {"call_id", "outcome", "content"}
