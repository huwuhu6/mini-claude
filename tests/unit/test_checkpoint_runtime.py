"""Checkpoint integration uses real runtime objects and deterministic providers."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import os

import pytest

from test_run_snapshot import agents, calls
from core.checkpoint_store import CheckpointError, CheckpointCommitError, RunBusyError, SqliteCheckpointStore
from core.run_state import RunStatus
from core.tools.base_tools import ToolResult


def start(agents, *, labels=("A", "B", "C"), pause_after="A"):
    a, provider = agents([{"content": "batch", "tool_calls": calls(*labels)}, {}], pause_after=pause_after)
    a.enable_checkpoints()
    a.run("original task", require_tool_call=True)
    return a, provider


def receiver(agents, a, *, pause_after=None):
    b, provider = agents([{}], pause_after=pause_after)
    b.enable_checkpoints(a._checkpoint_store.path)
    return b, provider


def test_pause_restore_closes_batch_without_replaying_A_or_model_call(agents):
    a, ap = start(agents)
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row["status"] == "paused" and len(ap.requests) == 1
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "PREPARED", "PREPARED"]
    b, bp = receiver(agents, a)
    result = b.resume_checkpoint(a._run_state.run_id)
    assert result == "done" and a.executions == ["A"] and b.executions == ["B", "C"]
    assert len(bp.requests) == 1 and b._run_state.model_turns_used == 2
    final = b._checkpoint_store.load(b._run_state.run_id)
    assert final["status"] == "completed"
    assert all(r["state"] == "COMPLETED" for r in final["journal"])
    assert [m["tool_call_id"] for m in final["snapshot"]["messages"] if m["role"] == "tool"] == ["call-0", "call-1", "call-2"]
    assert final["snapshot"]["todo"] == a.todo.items
    assert b.resume_checkpoint(b._run_state.run_id) == result
    assert b.executions == ["B", "C"] and len(bp.requests) == 1
    with pytest.raises(CheckpointError):
        a._continue_run()  # Old memory cannot advance a newer persisted revision.
    assert a.executions == ["A"] and a._run_state.status is RunStatus.PAUSED


def test_in_memory_continue_with_persistence_and_new_run_are_compatible(agents):
    a, ap = start(agents)
    old_id = a._run_state.run_id
    assert a._continue_run() == "done"
    assert a.executions == ["A", "B", "C"] and len(ap.requests) == 2
    ap.responses.append({})
    assert a.run("new task") == "done"
    assert a._run_state.run_id != old_id
    assert a._checkpoint_store.load(old_id)["status"] == "completed"


def test_disabled_feature_and_invalid_json_do_not_enter_running(agents):
    a, _ = start(agents)
    b, _ = receiver(agents, a)
    b.feature_manager.disable("probe_feature")
    b.resume_checkpoint(a._run_state.run_id)
    row = b._checkpoint_store.load(a._run_state.run_id)
    assert b.executions == []
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED", "NOT_EXECUTED"]
    assert row["snapshot"]["run"]["tool_batch"]["results"]["call-1"]["outcome"] == "BLOCKED_FEATURE_DISABLED"


@pytest.mark.parametrize("stage", ["prepared", "running", "result", "result_committed"])
def test_sqlite_failure_stops_dispatch_and_never_becomes_tool_feedback(agents, monkeypatch, stage):
    a, ap = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    a.enable_checkpoints()
    store = a._checkpoint_store
    commit = store._commit
    def fail(connection):
        row = connection.execute("SELECT payload FROM runs").fetchone()
        data = json.loads(row[0]) if row and row[0] else None
        running = connection.execute("SELECT 1 FROM calls WHERE state='RUNNING'").fetchone()
        cursor = data["run"]["tool_batch"]["next_call_index"] if data and data["run"]["tool_batch"] else -1
        hit = ((stage == "prepared" and cursor == 0 and not running)
               or (stage == "running" and cursor == 0 and running)
               or (stage.startswith("result") and cursor == 1))
        if hit:
            if stage == "result_committed":
                commit(connection)
            raise sqlite3.OperationalError("injected storage failure")
        commit(connection)
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        a.run("task")
    assert a.executions == (["A"] if stage.startswith("result") else [])
    assert len(ap.requests) == 1
    assert error.value.committed is (stage == "result_committed")
    assert a._run_state.status is RunStatus.FAILED
    assert a._continue_run().startswith("Checkpoint 故障")
    assert all("injected storage" not in m.content for m in a.messages if m.role == "tool")
    monkeypatch.setattr(store, "_commit", commit)
    record = store.load(a._run_state.run_id)
    if stage == "result":
        b, _ = receiver(agents, a)
        with pytest.raises(CheckpointError, match="UNKNOWN"):
            b.resume_checkpoint(a._run_state.run_id)
        assert b.executions == []
    if stage == "result_committed":
        b, _ = receiver(agents, a)
        b.resume_checkpoint(a._run_state.run_id)
        assert b.executions == ["B", "C"]


def test_definite_handler_failure_is_completed_not_unknown(agents):
    a, ap = agents([{"tool_calls": calls("A", "B")}, {}], pause_after=None)
    def fail(label):
        a.executions.append(label)
        return ToolResult("definite failure", success=False, execution_success=False)
    a.tool_registry._tools["probe"].handler = fail
    a.enable_checkpoints()
    a.run("task")
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == ["A", "B"]
    assert all(r["state"] == "COMPLETED" for r in row["journal"])
    assert all(r["outcome"] == "EXECUTED_FAILURE" for r in row["snapshot"]["run"]["tool_batch"]["results"].values())


def test_handler_cancel_preserves_atomic_unknown_and_pending_not_executed(agents):
    a, _ = agents([{"tool_calls": calls("A", "B")}], pause_after=None)
    def cancel(label):
        a.executions.append(label)
        raise KeyboardInterrupt("handler cancelled")
    a.tool_registry._tools["probe"].handler = cancel
    a.enable_checkpoints()
    assert "停止" in a.run("task")
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == ["A"]
    assert row["status"] == "cancelled"
    assert [r["state"] for r in row["journal"]] == ["UNKNOWN", "NOT_EXECUTED"]
    assert [m["tool_call_id"] for m in row["snapshot"]["messages"] if m["role"] == "tool"] == ["call-0", "call-1"]


def test_workspace_changes_refuse_before_dispatch(agents):
    a, _ = start(agents)
    (a.workdir / "file.txt").write_text("external update", encoding="utf-8")
    b, bp = receiver(agents, a)
    with pytest.raises(ValueError, match="工作区内容"):
        b.resume_checkpoint(a._run_state.run_id)
    assert b.executions == [] and bp.requests == [] and b._run_state is None


def test_last_call_pause_and_deferred_replan_can_resume(agents):
    a, _ = start(agents, labels=("A",))
    a._run_state.tool_batch.pending_replan_messages.append("replan once")
    # This explicit consistent safe-boundary save simulates governance feedback.
    a._checkpoint_claim()
    a._checkpoint_save("tools")
    a._checkpoint_store.release(a._run_state.run_id)
    a._release_conversation_lock()
    assert not a._checkpoint_store._locks
    b, bp = receiver(agents, a)
    b.resume_checkpoint(a._run_state.run_id)
    assert b.executions == [] and len(bp.requests) == 1
    assert sum(m.content == "replan once" for m in b.messages) == 1
    assert b.resume_checkpoint(a._run_state.run_id) == "done"
    assert sum(m.content == "replan once" for m in b.messages) == 1


def test_dispatch_api_cannot_bypass_durable_run_boundary(agents):
    a, _ = agents()
    a.enable_checkpoints()
    with pytest.raises(CheckpointError):
        a._execute_tool("probe", {"label": "A"})
    assert a.executions == []


def test_opt_in_only_and_database_outside_workspace(agents):
    a, _ = agents([{}])
    assert a._checkpoint_store is None
    with pytest.raises(CheckpointError):
        a.enable_checkpoints(a.workdir / "bad.sqlite3")
    assert a.run("ordinary task") == "done"
    assert not a.data_paths.checkpoints.exists()


def process_workspace(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "src").mkdir()
    (tmp_path / "config.yaml").write_text("features:\n  skills: false\n", encoding="utf-8")
    return tmp_path


def launch(base, mode, *, wait=True):
    worker = Path(__file__).with_name("checkpoint_process_probe.py")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    args = [sys.executable, "-B", str(worker), str(base), mode]
    if not wait:
        return subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
    return result


def effects(base):
    path = base / "effects.jsonl"
    return [json.loads(line)["label"] for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.mark.parametrize("mode,exit_code,before,expected,requests", [
    ("before_model", 35, [], ["A", "B", "C"], 2),
    ("response_uncommitted", 37, [], ["A", "B", "C"], 2),
    ("prepared", 31, [], ["A", "B", "C"], 1),
    ("running", 32, [], [], 0),
    ("before_result", 33, ["A"], ["A"], 0),
    ("after_result", 34, ["A"], ["A", "B", "C"], 1),
    ("after_terminal", 36, ["A", "B", "C"], ["A", "B", "C"], 0),
    ("pause", 0, ["A"], ["A", "B", "C"], 1),
])
def test_real_process_crash_and_recovery(tmp_path, mode, exit_code, before, expected, requests):
    base = process_workspace(tmp_path)
    child = launch(base, mode)
    assert child.returncode == exit_code, child.stdout + child.stderr
    assert effects(base) == before
    run_id = (base / "run_id").read_text(encoding="utf-8")
    resumed = launch(base, "resume")
    report = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert report["requests"] == requests, resumed.stdout + resumed.stderr
    assert effects(base) == expected
    if mode in {"running", "before_result"}:
        assert resumed.returncode == 7 and "UNKNOWN" in report["error"]
        repeated = launch(base, "resume")
        assert repeated.returncode == 7 and effects(base) == expected
        store = SqliteCheckpointStore(base / "checkpoint.sqlite3")
        assert store.load(run_id)["status"] == "unknown"
        store.close()
    else:
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        assert report["run_id"] == run_id and report["status"] == "completed"
        assert report["journal"] == ["COMPLETED"] * 3
        repeated = launch(base, "resume")
        assert repeated.returncode == 0 and effects(base) == expected
        assert json.loads(repeated.stdout.strip().splitlines()[-1])["requests"] == 0


def test_real_process_live_handler_blocks_other_resume_then_death_is_unknown(tmp_path):
    import time
    base = process_workspace(tmp_path)
    holder = launch(base, "hold_handler", wait=False)
    try:
        deadline = time.monotonic() + 15
        while not (base / "ready").exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (base / "ready").exists(), "worker did not reach deterministic handler boundary"
        contender = launch(base, "resume")
        assert contender.returncode == 7
        assert "有效执行者" in json.loads(contender.stdout.strip().splitlines()[-1])["error"] and effects(base) == ["A"]
        holder.kill()
        holder.communicate(timeout=10)
        resumed = launch(base, "resume")
        assert resumed.returncode == 7 and "UNKNOWN" in resumed.stdout
        assert effects(base) == ["A"]
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)


@pytest.mark.parametrize("after_commit", [False, True])
def test_identity_commit_failure_does_not_leave_memory_running(agents, monkeypatch, after_commit):
    a, ap = agents([{"tool_calls": calls("A")}])
    a.enable_checkpoints()
    def fail(connection):
        if after_commit:
            connection.commit()
        raise sqlite3.OperationalError("identity commit failure")
    monkeypatch.setattr(a._checkpoint_store, "_commit", fail)
    with pytest.raises(CheckpointCommitError):
        a.run("task")
    assert a._run_state.status is RunStatus.FAILED
    assert a.executions == [] and ap.requests == []


@pytest.mark.parametrize("error", [OSError("trace fail"), KeyboardInterrupt("trace interrupted")])
def test_persistent_initialization_failure_preserves_signal_and_terminal_status(agents, monkeypatch, error):
    a, ap = agents([{}])
    a.enable_checkpoints()
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(a.trace, "start_task", fail)
    with pytest.raises(type(error)) as received:
        a.run("task")
    assert received.value is error and ap.requests == []
    expected = RunStatus.FAILED if isinstance(error, Exception) else RunStatus.CANCELLED
    assert a._run_state.status is expected
    assert a._checkpoint_store.load(a._run_state.run_id)["status"] == expected.value



def test_storage_faulted_instance_cannot_clean_pending_calls_into_new_run(agents, monkeypatch):
    a, ap = agents([{"tool_calls": calls("A", "B")}], pause_after=None)
    a.enable_checkpoints()
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("cannot mark running")
    monkeypatch.setattr(a._checkpoint_store, "mark_running", fail)
    with pytest.raises(sqlite3.OperationalError):
        a.run("task")
    before = deepcopy(a.messages)
    with pytest.raises(CheckpointError, match="新实例"):
        a.run("new task")
    assert a.messages == before and len(ap.requests) == 1 and a.executions == []


def test_two_instances_cannot_continue_same_paused_run_concurrently(agents):
    a, _ = start(agents)
    b, bp = receiver(agents, a)
    a._checkpoint_claim()
    with pytest.raises(RunBusyError):
        b.resume_checkpoint(a._run_state.run_id)
    assert b._run_state is None and b.executions == [] and bp.requests == []
    assert a._continue_run() == "done"
    assert b.resume_checkpoint(a._run_state.run_id) == "done"
    assert b.executions == [] and bp.requests == []


def test_hard_stop_preserves_A_and_never_dispatches_pending_suffix(agents):
    from core.loop_controller import RuntimeDecision
    from test_tool_batch_message_closure import _queue_governance
    a, ap = agents([{"tool_calls": calls("A", "B", "C")}, {}], pause_after=None)
    _queue_governance(a, [RuntimeDecision.HARD_STOP])
    a.enable_checkpoints()
    assert a.run("task") == "actual result A"
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == ["A"] and len(ap.requests) == 1
    assert row["status"] == "terminated"
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED", "NOT_EXECUTED"]
    assert [m["tool_call_id"] for m in row["snapshot"]["messages"] if m["role"] == "tool"] == ["call-0", "call-1", "call-2"]
    b, bp = receiver(agents, a)
    assert b.resume_checkpoint(a._run_state.run_id) == "actual result A"
    assert b.executions == [] and bp.requests == []


def test_duplicate_and_malformed_arguments_commit_unexecuted_facts(agents):
    batch = calls("A", "A", "C")
    batch[2]["function"]["arguments"] = "invalid JSON"
    a, ap = agents([{"tool_calls": batch}, {}], pause_after=None)
    a.enable_checkpoints()
    record = a.session_recorder.record
    def stop_at_argument_result(event, **data):
        record(event, **data)
        if event == "tool_result" and data.get("outcome") == "INVALID_ARGUMENTS":
            a._request_run_pause()
    a.session_recorder.record = stop_at_argument_result
    a.run("task")
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == ["A"]
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED", "NOT_EXECUTED"]
    outcomes = row["snapshot"]["run"]["tool_batch"]["results"]
    assert outcomes["call-1"]["outcome"] == "SKIPPED_DUPLICATE"
    assert outcomes["call-2"]["outcome"] == "INVALID_ARGUMENTS"
    assert len(ap.requests) == 1 and a._run_state.status is RunStatus.PAUSED


def test_feature_change_after_provider_response_is_checked_at_dispatch(agents, monkeypatch):
    a, ap = agents([{"tool_calls": calls("A", "B")}, {}], pause_after=None)
    a.enable_checkpoints()
    create = ap.create_message
    def create_and_disable(*args, **kwargs):
        response = create(*args, **kwargs)
        a.feature_manager.disable("probe_feature")
        return response
    monkeypatch.setattr(ap, "create_message", create_and_disable)
    a.run("task")
    assert a.executions == []
    assert all(r["state"] == "NOT_EXECUTED" for r in a._checkpoint_store.load(a._run_state.run_id)["journal"])


def test_restore_checks_current_tool_contract_and_trace_session_links(agents):
    a, _ = start(agents)
    b, bp = receiver(agents, a)
    b.tool_registry._tools["probe"].input_schema["required"] = []
    with pytest.raises(ValueError, match="不兼容"):
        b.resume_checkpoint(a._run_state.run_id)
    assert b.executions == [] and bp.requests == []
    c, _ = receiver(agents, a)
    c.resume_checkpoint(a._run_state.run_id)
    trace_file = c.data_paths.traces / f"task_{c._run_state.trace_task_id}.json"
    trace = json.loads(trace_file.read_text(encoding="utf-8"))
    assert trace["request_config"]["run_id"] == a._run_state.run_id
    assert trace["request_config"]["resumed_from_trace_task_id"] == a._run_state.trace_task_id
    events = [json.loads(line) for line in c.session_recorder.path.read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "checkpoint_restored" and e["run_id"] == a._run_state.run_id for e in events)



def test_memory_call_rewrite_is_refused_before_handler(agents):
    a, _ = start(agents)
    a._run_state.tool_batch.tool_calls[1]["function"]["arguments"] = json.dumps({"label": "rewritten"})
    with pytest.raises(CheckpointError, match="原调用冲突"):
        a._continue_run()
    assert a.executions == ["A"]
    assert a._checkpoint_store.load(a._run_state.run_id)["journal"][1]["state"] == "PREPARED"
    b, _ = receiver(agents, a)
    b.resume_checkpoint(a._run_state.run_id)
    assert b.executions == ["B", "C"]


def test_direct_dispatch_while_paused_is_refused(agents):
    a, _ = start(agents)
    with pytest.raises(CheckpointError):
        a._execute_tool("probe", {"label": "B"})
    assert a.executions == ["A"]
    assert a._checkpoint_store.load(a._run_state.run_id)["journal"][1]["state"] == "PREPARED"



def test_checkpoint_mode_refuses_unmanaged_background_and_subagent_entries(agents, monkeypatch):
    a, _ = agents()
    a.enable_checkpoints()
    def unexpected(*args, **kwargs):
        raise AssertionError("unmanaged execution was launched")
    monkeypatch.setattr(a.background, "launch", unexpected)
    monkeypatch.setattr(a.subagent_manager, "run", unexpected)
    assert "Checkpoint" in a.run_background("echo safe")
    assert "Checkpoint" in a._queue_background_command("echo safe").content
    assert "Checkpoint" in a.run_subagent("do something")
    assert "Checkpoint" in a._handle_task("do something")
    assert a.background.list() == []



def test_durable_snapshot_cannot_bypass_store_exclusivity_via_json_restore(agents):
    a, _ = start(agents)
    exported = a.export_run_snapshot()
    plain, provider = agents([{}], pause_after=None)
    with pytest.raises(ValueError):
        plain.restore_run_snapshot(exported)
    assert plain._run_state is None and plain.executions == [] and provider.requests == []
    b, bp = receiver(agents, a)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(exported)
    assert b._run_state is None and b.executions == [] and bp.requests == []
    assert b.resume_checkpoint(a._run_state.run_id) == "done"
    assert b.executions == ["B", "C"]
