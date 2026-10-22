"""File evidence never rewrites an UNKNOWN execution fact or authorizes a retry."""
import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from test_run_snapshot import agents
from test_checkpoint_recovery_guards import write_call
from core.checkpoint_store import CheckpointCommitError, CheckpointError


def unknown_file(agents, monkeypatch, *, edit=False, content="new\ncontent\n", original=None):
    call = write_call("file.txt", content)
    if edit:
        call["function"] = {"name": "edit_file", "arguments": json.dumps({
            "path": "file.txt", "edits": [{"search": "original", "replace": content}]})}
    agent, provider = agents([{"tool_calls": [call]}, {}], pause_after=None)
    if original is not None:
        (agent.workdir / "file.txt").write_bytes(original)
    agent.enable_checkpoints()
    agent.enable_hitl()
    agent.run("write file")
    run = agent._run_state.run_id
    store = agent._checkpoint_store
    approval = agent.get_pending_approval(run)
    agent.decide_approval(run, approval.approval_id, approved=True, actor="test")
    commit = store._commit

    def fail_result(connection):
        row = connection.execute("SELECT state FROM calls WHERE run_id=?", (run,)).fetchone()
        if row[0] == "COMPLETED":
            raise sqlite3.OperationalError("effect occurred; result commit failed")
        commit(connection)

    monkeypatch.setattr(store, "_commit", fail_result)
    with pytest.raises(CheckpointCommitError):
        agent.resume_checkpoint(run)
    monkeypatch.setattr(store, "_commit", commit)
    store.acquire(run)
    try:
        record = store.recover(run)
    finally:
        store.release(run)
    assert record["journal"][0]["state"] == "UNKNOWN"
    return agent, provider, run, call


@pytest.mark.parametrize("edit", [False, True])
def test_prepared_evidence_predicts_actual_file_bytes_and_is_read_only(agents, monkeypatch, edit):
    agent, provider, run, call = unknown_file(agents, monkeypatch, edit=edit)
    store = agent._checkpoint_store
    before = deepcopy(store.load(run))
    evidence = agent.get_file_recovery_evidence(run, call["id"])
    assert evidence["classification"] == "POSTCONDITION_SATISFIED"
    assert evidence["expected_sha256"] == hashlib.sha256((agent.workdir / "file.txt").read_bytes()).hexdigest()
    assert evidence["before"]["sha256"] == hashlib.sha256(b"original").hexdigest()
    assert evidence["call_id"] == call["id"] and evidence["run_id"] == run
    from cli.hitl_commands import describe_run
    assert "后置条件已满足" in describe_run(agent, run)
    assert store.load(run) == before
    with pytest.raises(CheckpointError):
        agent.run("continue")  # The original storage-faulted instance stays isolated.
    assert len(provider.requests) == 1


@pytest.mark.parametrize("observed,classification", [
    ("original", "PRECONDITION_MATCH"), ("conflicting change", "CONFLICT"),
])
def test_aba_and_conflict_do_not_resolve_unknown(agents, monkeypatch, observed, classification):
    agent, _, run, call = unknown_file(agents, monkeypatch)
    (agent.workdir / "file.txt").write_text(observed, encoding="utf-8")
    before = deepcopy(agent._checkpoint_store.load(run))
    evidence = agent.get_file_recovery_evidence(run, call["id"])
    assert evidence["classification"] == classification
    assert agent._checkpoint_store.load(run) == before
    with pytest.raises(CheckpointError, match="UNKNOWN"):
        agent.resume_checkpoint(run)


def test_unreadable_observation_is_unverifiable(agents, monkeypatch):
    agent, _, run, call = unknown_file(agents, monkeypatch)
    from pathlib import Path
    read_bytes = Path.read_bytes

    def denied(path):
        if path == agent.workdir / "file.txt":
            raise PermissionError("injected")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert agent.get_file_recovery_evidence(run, call["id"])["classification"] == "UNVERIFIABLE"


def test_evidence_commit_failure_prevents_dispatch(agents, monkeypatch):
    agent, provider = agents([{"tool_calls": [write_call("file.txt")]}, {}], pause_after=None)
    agent.enable_checkpoints()
    store = agent._checkpoint_store
    commit = store._commit

    def fail(connection):
        if connection.execute("SELECT 1 FROM call_file_evidence").fetchone():
            raise sqlite3.OperationalError("prepared evidence commit failed")
        commit(connection)

    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError):
        agent.run("write")
    assert (agent.workdir / "file.txt").read_text(encoding="utf-8") == "original"
    assert len(provider.requests) == 1


def test_v4_upgrade_preserves_existing_facts_without_inventing_evidence(agents, monkeypatch):
    agent, _, run, call = unknown_file(agents, monkeypatch)
    store = agent._checkpoint_store
    before = store.load(run)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE call_file_evidence")
        connection.execute("PRAGMA user_version=4")
    from core.checkpoint_store import SqliteCheckpointStore
    upgraded = SqliteCheckpointStore(store.path)
    try:
        after = upgraded.load(run)
        assert after["journal"] == before["journal"]
        assert after["payload"] == before["payload"] and after["revision"] == before["revision"]
        assert after["file_evidence"] == []
    finally:
        upgraded.close()
    assert agent.get_file_recovery_evidence(run, call["id"])["classification"] == "UNVERIFIABLE"


@pytest.mark.parametrize("edit,original,content", [
    (False, b"original", "中文\r\nsecond\n"),
    (True, b"original\r\ntrailing\r\n", "中文\nsecond"),
])
def test_expected_hash_matches_real_encoding_and_newline_rules(agents, monkeypatch, edit, original, content):
    agent, _, run, call = unknown_file(agents, monkeypatch, edit=edit, original=original, content=content)
    assert agent.get_file_recovery_evidence(run, call["id"])["classification"] == "POSTCONDITION_SATISFIED"


def test_prepared_evidence_is_durable_before_approval_and_dispatch(agents):
    agent, provider = agents([{"tool_calls": [write_call("file.txt")]}, {}], pause_after=None)
    agent.enable_checkpoints()
    agent.enable_hitl()
    agent.run("write")
    record = agent._checkpoint_store.load(agent._run_state.run_id)
    assert record["journal"][0]["state"] == "PREPARED"
    evidence = json.loads(record["file_evidence"][0]["evidence_json"])
    assert evidence["before"]["sha256"] == hashlib.sha256(b"original").hexdigest()
    assert evidence["expected_sha256"] is not None
    assert (agent.workdir / "file.txt").read_text(encoding="utf-8") == "original"
    assert len(provider.requests) == 1


def test_corrupt_evidence_is_rejected_without_touching_execution_facts(agents, monkeypatch):
    agent, _, run, _ = unknown_file(agents, monkeypatch)
    store = agent._checkpoint_store
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE call_file_evidence SET evidence_json='{}' WHERE run_id=?", (run,))
    with pytest.raises(CheckpointError, match="证据摘要"):
        store.load(run)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT state FROM calls WHERE run_id=?", (run,)).fetchone()[0] == "UNKNOWN"


def test_second_file_call_prepares_against_first_committed_effect(agents, monkeypatch):
    first, second = write_call("file.txt", "one"), write_call("file.txt", "two")
    second["id"] = "write-2"
    agent, provider = agents([{"tool_calls": [first, second]}, {}], pause_after=None)
    agent.enable_checkpoints()
    store = agent._checkpoint_store
    commit = store._commit

    def fail_second_result(connection):
        row = connection.execute("SELECT state FROM calls WHERE call_id='write-2'").fetchone()
        if row and row[0] == "COMPLETED":
            raise sqlite3.OperationalError("second result lost")
        commit(connection)

    monkeypatch.setattr(store, "_commit", fail_second_result)
    with pytest.raises(CheckpointCommitError):
        agent.run("two dependent writes")
    monkeypatch.setattr(store, "_commit", commit)
    run = agent._run_state.run_id
    store.acquire(run)
    try:
        record = store.recover(run)
    finally:
        store.release(run)
    assert [row["state"] for row in record["journal"]] == ["COMPLETED", "UNKNOWN"]
    first_evidence, second_evidence = [json.loads(row["evidence_json"]) for row in record["file_evidence"]]
    assert first_evidence["before"]["sha256"] == hashlib.sha256(b"original").hexdigest()
    assert second_evidence["before"]["sha256"] == hashlib.sha256(b"one").hexdigest()
    assert agent.get_file_recovery_evidence(run, "write-2")["classification"] == "POSTCONDITION_SATISFIED"
    assert len(provider.requests) == 1
