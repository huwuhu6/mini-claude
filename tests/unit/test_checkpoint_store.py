"""Deterministic store-level transactions, CAS and kernel lock checks."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from core.checkpoint_store import CheckpointError, CheckpointCommitError, RunBusyError, SqliteCheckpointStore, DB_VERSION
from core.run_state import RunState, RunStatus, ToolBatchState, ToolBatchStatus
from core.run_snapshot import encode_run
from providers.base import Message
from dataclasses import asdict


def snapshot():
    calls = [{"id": f"id-{i}", "type": "function", "function": {
        "name": "probe", "arguments": json.dumps({"label": label})}} for i, label in enumerate("AB")]
    batch = ToolBatchState(calls, 1, 0, "batch", status=ToolBatchStatus.PAUSED)
    state = RunState(run_id="run-1", status=RunStatus.PAUSED, model_turns_used=1,
        tool_call_seen=True, trace_started=True, trace_task_id="trace-1", tool_batch=batch)
    return {"schema_version": 1, "run": encode_run(state),
        "messages": [asdict(Message("user", "task")), asdict(Message("assistant", "batch", tool_calls=calls))],
        "workspace": {}, "compatibility": {}, "history": {}, "todo": [], "agent_note": "",
        "memory": {}, "context": {}, "diagnostics": {}}


def completed(data, outcome="EXECUTED_SUCCESS"):
    data = deepcopy(data)
    batch = data["run"]["tool_batch"]
    call = batch["tool_calls"][batch["next_call_index"]]
    result = {"call_id": call["id"], "outcome": outcome, "content": "actual " + call["id"]}
    batch["results"][call["id"]] = result
    batch["resolved_call_ids"].append(call["id"])
    batch["seen_signatures"].append([call["function"]["name"], call["function"]["arguments"]])
    batch["next_call_index"] += 1
    if batch["next_call_index"] == len(batch["tool_calls"]):
        batch["status"] = "completed"
    data["messages"].append(asdict(Message("tool", result["content"], tool_call_id=call["id"])))
    return data


@pytest.fixture
def ready(tmp_path):
    store = SqliteCheckpointStore(tmp_path / "checkpoints.sqlite3")
    store.acquire("run-1")
    revision = store.create_run("run-1")
    data = snapshot()
    revision = store.save_snapshot("run-1", revision, data)
    yield store, revision, data
    store.close()


def test_schema_and_snapshot_roundtrip(ready):
    store, revision, data = ready
    row = store.load("run-1")
    assert row["snapshot"] == data and row["revision"] == revision
    assert [r["state"] for r in row["journal"]] == ["PREPARED", "PREPARED"]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with store._connection() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


@pytest.mark.parametrize("version", [DB_VERSION + 1, 999])
def test_incompatible_schema_refused(tmp_path, version):
    path = tmp_path / "db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    with pytest.raises(CheckpointError):
        SqliteCheckpointStore(path)


def test_result_message_cursor_commit_as_one_version(ready):
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    revision = store.save_snapshot("run-1", revision, completed(data))
    row = store.load("run-1")
    assert row["revision"] == revision
    assert row["journal"][0]["state"] == "COMPLETED"
    assert row["snapshot"]["run"]["tool_batch"]["next_call_index"] == 1
    assert row["snapshot"]["messages"][-1]["tool_call_id"] == "id-0"
    with pytest.raises(CheckpointError):
        store.save_snapshot("run-1", revision, data)


def test_running_requires_exact_current_call_and_revision(ready):
    store, revision, _ = ready
    with pytest.raises(CheckpointError):
        store.mark_running("run-1", revision, 0, 1, "id-1")
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    with pytest.raises(CheckpointError):
        store.mark_running("run-1", revision, 0, 0, "id-0")
    with pytest.raises(CheckpointError):
        store.mark_running("run-1", revision - 1, 0, 0, "id-0")


def test_recovery_running_becomes_unknown_and_never_replays(ready):
    store, revision, data = ready
    store.mark_running("run-1", revision, 0, 0, "id-0")
    row = store.recover("run-1")
    assert row["status"] == "unknown"
    assert [r["state"] for r in row["journal"]] == ["UNKNOWN", "PREPARED"]
    with pytest.raises(CheckpointError):
        store.save_snapshot("run-1", row["revision"], data)
    assert store.recover("run-1")["revision"] == row["revision"]


def test_prepared_can_recover_and_definite_failure_is_completed(ready):
    store, revision, data = ready
    assert store.recover("run-1")["snapshot"] == data
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    store.save_snapshot("run-1", revision, completed(data, "EXECUTED_FAILURE"))
    assert store.recover("run-1")["journal"][0]["state"] == "COMPLETED"


@pytest.mark.parametrize("after_commit", [False, True])
def test_commit_error_rechecked_and_atomic(ready, monkeypatch, after_commit):
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    def fail(connection):
        if after_commit:
            connection.commit()
        raise sqlite3.OperationalError("injected ambiguous commit")
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        store.save_snapshot("run-1", revision, completed(data))
    assert error.value.committed is after_commit
    row = store.load("run-1")
    assert row["snapshot"] == (completed(data) if after_commit else data)
    assert row["journal"][0]["state"] == ("COMPLETED" if after_commit else "RUNNING")
    assert row["revision"] == revision + int(after_commit)


def test_validation_failure_rolls_back_partial_journal(ready):
    store, revision, data = ready
    altered = completed(data)
    altered["run"]["tool_batch"]["tool_calls"][1]["function"]["name"] = "different"
    altered["messages"][1]["tool_calls"] = deepcopy(altered["run"]["tool_batch"]["tool_calls"])
    with pytest.raises(CheckpointError):
        store.save_snapshot("run-1", revision, altered)
    assert store.load("run-1")["snapshot"] == data
    assert [r["state"] for r in store.load("run-1")["journal"]] == ["PREPARED", "PREPARED"]


@pytest.mark.parametrize("change", ["id", "payload", "journal"])
def test_corrupt_projection_refused(ready, change):
    store, _, _ = ready
    with sqlite3.connect(store.path) as connection:
        if change == "payload":
            connection.execute("UPDATE runs SET payload='{}'")
        elif change == "id":
            connection.execute("UPDATE runs SET run_id='wrong'")
        else:
            connection.execute("UPDATE calls SET call_id='wrong' WHERE ordinal=0")
    with pytest.raises(CheckpointError):
        store.load("wrong" if change == "id" else "run-1")


def test_same_run_lock_is_exclusive_across_stores_and_real_process(ready):
    store, _, _ = ready
    other = SqliteCheckpointStore(store.path)
    with pytest.raises(RunBusyError):
        other.acquire("run-1")
    script = "from core.checkpoint_store import *; from pathlib import Path; import sys; s=SqliteCheckpointStore(Path(sys.argv[1])); s.acquire('run-1')"
    child = subprocess.run([sys.executable, "-B", "-c", script, str(store.path)], capture_output=True, text=True, timeout=20,
                           env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')})
    assert child.returncode != 0 and "RunBusyError" in child.stderr
    store.release("run-1")
    other.acquire("run-1")
    other.close()


def test_kernel_releases_lock_when_process_dies(tmp_path):
    path = tmp_path / "db"
    script = "from core.checkpoint_store import *; from pathlib import Path; import os,sys; s=SqliteCheckpointStore(Path(sys.argv[1])); s.acquire('run-1'); s.create_run('run-1'); os._exit(19)"
    child = subprocess.run([sys.executable, "-B", "-c", script, str(path)], capture_output=True, text=True, timeout=20,
                           env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')})
    assert child.returncode == 19, child.stderr
    store = SqliteCheckpointStore(path)
    store.acquire("run-1")
    assert store.load("run-1")["status"] == "started"
    store.close()


def test_terminal_projection_has_same_results_messages_cursor_as_journal(ready):
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    final = completed(completed(data), "NOT_EXECUTED")
    final["run"].update(status="terminated", terminal_result="stop")
    final["run"]["tool_batch"].update(status="terminated", finalized=True)
    store.finish("run-1", revision, "terminated", "stop", final)
    row = store.load("run-1")
    assert row["snapshot"] == final
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED"]
    assert row["snapshot"]["run"]["tool_batch"]["next_call_index"] == 2
    with pytest.raises(CheckpointError):
        store.mark_running("run-1", row["revision"], 0, 1, "id-1")



def test_versioned_database_with_missing_table_is_not_silently_rebuilt(ready):
    store, _, _ = ready
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE calls")
    with pytest.raises(CheckpointError, match="结构损坏"):
        SqliteCheckpointStore(store.path)


def test_commit_then_non_sqlite_io_error_is_rechecked(ready, monkeypatch):
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    def fail(connection):
        connection.commit()
        raise OSError("post-commit IO failure")
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        store.save_snapshot("run-1", revision, completed(data))
    assert error.value.committed is True
    assert store.load("run-1")["snapshot"] == completed(data)


def test_commit_verification_can_be_unknown_and_still_does_not_replay(ready, monkeypatch):
    from contextlib import contextmanager
    store, revision, data = ready
    revision = store.mark_running("run-1", revision, 0, 0, "id-0")
    connect = store._connection
    verification_failed = False
    @contextmanager
    def connection():
        if verification_failed:
            raise sqlite3.OperationalError("database unavailable during verification")
        with connect() as current:
            yield current
    def fail(current):
        nonlocal verification_failed
        verification_failed = True
        raise sqlite3.OperationalError("cannot confirm commit")
    monkeypatch.setattr(store, "_connection", connection)
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        store.save_snapshot("run-1", revision, completed(data))
    assert error.value.committed is None
    verification_failed = False
    assert store.load("run-1")["snapshot"] == data
    assert store.load("run-1")["journal"][0]["state"] == "RUNNING"



def test_load_reads_run_and_journal_from_same_transaction(ready, monkeypatch):
    from contextlib import contextmanager
    store, revision, data = ready
    connect = store._connection
    fired = False
    class Proxy:
        def __init__(self, current):
            self.current = current
        def __getattr__(self, key):
            return getattr(self.current, key)
        def execute(self, query, parameters=()):
            nonlocal fired
            if "SELECT * FROM calls WHERE run_id=? ORDER BY" in query and not fired:
                fired = True
                current_revision = store.mark_running("run-1", revision, 0, 0, "id-0")
                store.save_snapshot("run-1", current_revision, completed(data))
            return self.current.execute(query, parameters)
    @contextmanager
    def connection():
        with connect() as current:
            yield Proxy(current)
    monkeypatch.setattr(store, "_connection", connection)
    row = store.load("run-1")
    assert row["snapshot"] == data and row["revision"] == revision
    assert [r["state"] for r in row["journal"]] == ["PREPARED", "PREPARED"]
    assert store.load("run-1")["snapshot"] == completed(data)


@pytest.mark.parametrize("field", ["call_id", "content"])
def test_archived_journal_result_call_id_corruption_is_rejected(ready, field):
    store, revision, data = ready
    for ordinal in range(2):
        revision = store.mark_running("run-1", revision, 0, ordinal, f"id-{ordinal}")
        data = completed(data)
        revision = store.save_snapshot("run-1", revision, data)
    data["run"]["tool_batch"]["finalized"] = True
    revision = store.save_snapshot("run-1", revision, data)
    data["run"]["tool_batch"] = None
    store.save_snapshot("run-1", revision, data, boundary="before_model")
    with sqlite3.connect(store.path) as connection:
        raw = json.loads(connection.execute("SELECT result_json FROM calls WHERE ordinal=0").fetchone()[0])
        raw[field] = "corrupted archived fact"
        connection.execute("UPDATE calls SET result_json=? WHERE ordinal=0", (json.dumps(raw),))
    with pytest.raises(CheckpointError):
        store.load("run-1")
