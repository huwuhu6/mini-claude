"""Explicit run and tool-batch state transitions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from core.run_state import (
    RunState,
    RunStatus,
    ToolBatchAdvance,
    ToolBatchState,
    ToolBatchStatus,
)


def _tool_call(call_id):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }


def test_batch_records_ordered_result_and_pauses_without_resolving_pending_calls():
    run = RunState(max_iterations=4, require_tool_call=True)
    batch = ToolBatchState(
        tool_calls=[_tool_call("A"), _tool_call("B"), _tool_call("C")],
        assistant_message_index=2,
        model_iteration=0,
    )
    batch.start()
    run.tool_batch = batch
    run.request_pause()

    advance = run.record_tool_result("A", "EXECUTED_SUCCESS", "A result")

    assert advance is ToolBatchAdvance.PAUSED
    assert run.status is RunStatus.PAUSED
    assert batch.status is ToolBatchStatus.PAUSED
    assert batch.next_call_index == 1
    assert list(batch.results) == ["A"]
    assert batch.results["A"].content == "A result"
    assert batch.pending_call_ids == ["B", "C"]


def test_continuing_batch_keeps_results_and_advances_each_call_once():
    run = RunState()
    batch = ToolBatchState(
        tool_calls=[_tool_call("A"), _tool_call("B"), _tool_call("C")],
        assistant_message_index=0,
        model_iteration=0,
    )
    batch.start()
    run.tool_batch = batch
    run.request_pause()
    run.record_tool_result("A", "EXECUTED_SUCCESS", "A result")

    run.resume()
    assert run.record_tool_result("B", "EXECUTED_SUCCESS", "B result") is ToolBatchAdvance.CALL_COMPLETED
    assert run.record_tool_result("C", "EXECUTED_SUCCESS", "C result") is ToolBatchAdvance.BATCH_COMPLETED

    assert run.status is RunStatus.RUNNING
    assert batch.status is ToolBatchStatus.COMPLETED
    assert batch.next_call_index == 3
    assert list(batch.results) == ["A", "B", "C"]
    with pytest.raises(RuntimeError):
        run.record_tool_result("C", "EXECUTED_SUCCESS", "duplicate")


def test_pause_after_last_call_stops_run_before_next_model_turn():
    run = RunState()
    batch = ToolBatchState(
        tool_calls=[_tool_call("A")],
        assistant_message_index=0,
        model_iteration=0,
    )
    batch.start()
    run.tool_batch = batch
    run.request_pause()

    advance = run.record_tool_result("A", "EXECUTED_SUCCESS", "A result")

    assert advance is ToolBatchAdvance.BATCH_COMPLETED
    assert run.status is RunStatus.PAUSED
    assert not run.has_pending_tool_batch
    assert batch.status is ToolBatchStatus.COMPLETED

    run.resume()

    assert run.status is RunStatus.RUNNING
    assert batch.status is ToolBatchStatus.COMPLETED


def test_distinct_runs_do_not_share_batch_identity_or_cursor():
    first = RunState()
    second = RunState()
    first.tool_batch = ToolBatchState(
        tool_calls=[_tool_call("same-provider-id")],
        assistant_message_index=0,
        model_iteration=0,
    )
    second.tool_batch = ToolBatchState(
        tool_calls=[_tool_call("same-provider-id")],
        assistant_message_index=0,
        model_iteration=0,
    )
    first.tool_batch.start()
    second.tool_batch.start()

    first.record_tool_result("same-provider-id", "EXECUTED_SUCCESS", "one")

    assert first.run_id != second.run_id
    assert first.tool_batch.next_call_index == 1
    assert second.tool_batch.next_call_index == 0
    assert second.tool_batch.results == {}
