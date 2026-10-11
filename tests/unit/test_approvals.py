"""Approval transactions use only deterministic records, never a handler."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading
import pytest

from test_checkpoint_store import snapshot, completed
from core.approvals import ApprovalRecord, ApprovalStatus, ApprovalStore
from core.checkpoint_store import CheckpointError, CheckpointCommitError, RunBusyError, SqliteCheckpointStore
from core.features import FeatureManager, FeatureDefinition
from core.permissions import DefaultPermissionPolicy, PermissionDecision, PermissionKind, canonical, digest
from core.runtime_context.command_policy import CommandPolicy
from core.runtime_context.workspace_authority import WorkspaceAuthority
from core.tools.registry import ToolRegistry, ToolSpec


def never_execute(label):
    raise AssertionError("approval storage must never execute a handler")


@pytest.fixture
def ready(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    registry.register(ToolSpec("probe", "unknown operation", {"type": "object"}, never_execute))
    features = FeatureManager()
    features.register_feature(FeatureDefinition("probe"))
    features.register_tool_for_feature("probe", "probe")
    policy = DefaultPermissionPolicy(registry, features, WorkspaceAuthority(workspace), CommandPolicy(), lambda: workspace)
    data = snapshot()
    data["workspace"] = {"root": str(workspace.resolve()), "cwd": str(workspace.resolve()), "content_digest": "a" * 64}
    data["compatibility"] = {"tools": registry.execution_contract()}
    store = SqliteCheckpointStore(tmp_path / "checkpoints.sqlite3")
    store.acquire("run-1")
    revision = store.create_run("run-1")
    revision = store.save_snapshot("run-1", revision, data)
    operation = policy.bind("run-1", 0, 0, data["run"]["tool_batch"]["tool_calls"][0],
        workspace_digest=data["workspace"]["content_digest"], runtime_digest=digest(data["compatibility"]))
    clock = [100.0]
    approvals = ApprovalStore(store, policy, clock=lambda: clock[0])
    yield store, approvals, policy, policy.evaluate(operation), data, clock
    store.close()


def current(store):
    return store.load("run-1")["revision"]


def approved(ready, **options):
    store, approvals, _, decision, _, _ = ready
    record = approvals.create(decision, current(store), **options)
    return approvals.decide(record.approval_id, approved=True, actor="user")


def test_create_roundtrip_duplicate_and_no_execution_facts_changed(ready):
    store, approvals, _, decision, data, _ = ready
    record = approvals.create(decision, current(store))
    before = store.load("run-1")
    duplicate = approvals.create(decision, current(store))
    assert duplicate == record and before["revision"] == current(store)
    assert ApprovalRecord.from_dict(json.loads(json.dumps(record.to_dict()))) == record
    assert record.state is ApprovalStatus.PENDING and record.decision == decision
    assert store.load("run-1")["snapshot"] == data
    assert [r["state"] for r in store.load("run-1")["journal"]] == ["PREPARED", "PREPARED"]


@pytest.mark.parametrize("allow", [True, False])
def test_same_user_decision_is_idempotent_and_opposite_conflicts(ready, allow):
    store, approvals, _, decision, _, clock = ready
    record = approvals.create(decision, current(store))
    first = approvals.decide(record.approval_id, approved=allow, actor="user")
    revision = current(store)
    clock[0] += 1
    assert approvals.decide(record.approval_id, approved=allow, actor="user") == first
    assert current(store) == revision
    with pytest.raises(CheckpointError):
        approvals.decide(record.approval_id, approved=not allow, actor="user")
    with pytest.raises(CheckpointError):
        approvals.decide(record.approval_id, approved=allow, actor="different-user")
    assert approvals.get(record.approval_id) == first


def test_consumption_and_running_are_atomic_and_cannot_repeat(ready):
    store, approvals, _, decision, _, _ = ready
    record = approved(ready)
    with pytest.raises(CheckpointError):
        store.mark_running("run-1", current(store), 0, 0, "id-0")
    revision = approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert revision == current(store) and approvals.get(record.approval_id).state is ApprovalStatus.CONSUMED
    assert [r["state"] for r in store.load("run-1")["journal"]] == ["RUNNING", "PREPARED"]
    assert store.load("run-1")["snapshot"]["run"]["tool_batch"]["next_call_index"] == 0
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, revision)
    assert approvals.decide(record.approval_id, approved=True, actor="user").state is ApprovalStatus.CONSUMED
    assert store.recover("run-1")["status"] == "unknown"
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))


@pytest.mark.parametrize("kind", [PermissionKind.ALLOW, PermissionKind.DENY])
def test_only_ask_can_create_approval(ready, kind):
    store, approvals, _, decision, _, _ = ready
    with pytest.raises(ValueError):
        approvals.create(replace(decision, kind=kind), current(store))


@pytest.mark.parametrize("change", ["arguments", "tool_name", "workspace", "cwd", "workspace_digest", "runtime_digest", "policy"])
def test_changed_context_invalidates_old_approved_operation(ready, change):
    store, approvals, policy, decision, _, _ = ready
    record = approved(ready)
    op = decision.operation
    if change == "arguments":
        call = op.call
        call["function"]["arguments"] = json.dumps({"label": "changed"})
        op = replace(op, call_json=canonical(call))
    elif change == "tool_name":
        call = op.call
        call["function"]["name"] = "another-tool"
        op = replace(op, call_json=canonical(call))
    elif change in {"workspace", "cwd"}:
        op = replace(op, **{change: str(policy.authority.primary_root.parent.resolve())})
    elif change == "policy":
        policy.features.disable("probe")
        decision = policy.evaluate(op)
    else:
        context = json.loads(op.context_json)
        context[change] = "b" * 64
        op = replace(op, context_json=canonical(context))
    decision = replace(decision, operation=op)
    with pytest.raises(CheckpointError, match="失效"):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert approvals.get(record.approval_id).state is ApprovalStatus.INVALIDATED
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"
    with pytest.raises(CheckpointError):
        approvals.decide(record.approval_id, approved=True, actor="user")


@pytest.mark.parametrize("change", ["run_id", "turn", "ordinal", "provider_call_id"])
def test_cross_call_or_run_is_refused_without_invalidating_original(ready, change):
    store, approvals, _, decision, _, _ = ready
    record = approved(ready)
    op = decision.operation
    if change == "provider_call_id":
        call = op.call
        call["id"] = "another"
        op = replace(op, call_json=canonical(call))
    else:
        op = replace(op, **{change: "other-run" if change == "run_id" else 1})
    with pytest.raises(ValueError):
        approvals.consume_and_mark_running(record.approval_id, replace(decision, operation=op), current(store))
    assert approvals.get(record.approval_id) == record


def test_rejected_invalidated_and_expired_cannot_execute(ready):
    store, approvals, _, decision, _, clock = ready
    record = approved(ready, expires_at=101.0)
    clock[0] = 101.0
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert approvals.get(record.approval_id).state is ApprovalStatus.INVALIDATED
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"


@pytest.mark.parametrize("state", ["RUNNING", "UNKNOWN", "COMPLETED", "NOT_EXECUTED"])
def test_existing_execution_facts_cannot_be_authorized_again(ready, state):
    store, approvals, _, decision, data, _ = ready
    if state in {"RUNNING", "UNKNOWN", "COMPLETED"}:
        revision = store.mark_running("run-1", current(store), 0, 0, "id-0")
        if state == "UNKNOWN":
            store.recover("run-1")
        elif state == "COMPLETED":
            store.save_snapshot("run-1", revision, completed(data))
    else:
        store.save_snapshot("run-1", current(store), completed(data, "INVALID_ARGUMENTS"))
    before = store.load("run-1")
    with pytest.raises(CheckpointError):
        approvals.create(decision, current(store))
    assert store.load("run-1") == before


@pytest.mark.parametrize("after_commit", [False, True])
def test_consumption_commit_failure_never_claims_handler_success(ready, monkeypatch, after_commit):
    store, approvals, _, decision, _, _ = ready
    record = approved(ready)
    def fail(connection):
        if after_commit:
            connection.commit()
        raise sqlite3.OperationalError("injected approval commit failure")
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert error.value.committed is after_commit
    assert approvals.get(record.approval_id).state.value == ("CONSUMED" if after_commit else "APPROVED")
    assert store.load("run-1")["journal"][0]["state"] == ("RUNNING" if after_commit else "PREPARED")
    assert store.load("run-1")["journal"][0]["result_json"] is None


def test_conflicting_decisions_from_two_stores_have_one_winner(ready):
    store, approvals, policy, decision, _, _ = ready
    record = approvals.create(decision, current(store))
    store.release("run-1")
    barrier = threading.Barrier(2)
    others = [SqliteCheckpointStore(store.path), SqliteCheckpointStore(store.path)]
    def decide(index):
        other = others[index]
        try:
            barrier.wait(timeout=5)
            other.acquire("run-1")
            ApprovalStore(other, policy, clock=lambda: 101.0).decide(record.approval_id, approved=index == 0, actor="user")
            return "accepted"
        except CheckpointError:
            return "blocked"
        finally:
            other.close()
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(decide, range(2)))
    assert sorted(results) == ["accepted", "blocked"]
    assert approvals.get(record.approval_id).state in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"
    store.acquire("run-1")


def test_v1_upgrade_preserves_checkpoint_bytes_and_continue_protocol(ready):
    store, _, _, _, data, _ = ready
    revision = store.mark_running("run-1", current(store), 0, 0, "id-0")
    data = completed(data)
    store.save_snapshot("run-1", revision, data)
    before = store.load("run-1")
    store.close()
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE approvals")
        connection.execute("PRAGMA user_version=1")
    migrated = SqliteCheckpointStore(store.path)
    assert migrated.load("run-1") == before
    migrated.acquire("run-1")
    revision = migrated.mark_running("run-1", before["revision"], 0, 1, "id-1")
    migrated.save_snapshot("run-1", revision, completed(data))
    assert [r["state"] for r in migrated.load("run-1")["journal"]] == ["COMPLETED", "COMPLETED"]
    migrated.close()


def test_migration_failure_rolls_back_schema_without_touching_calls(ready, monkeypatch):
    store, _, _, _, _, _ = ready
    before = store.load("run-1")
    store.close()
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE approvals")
        connection.execute("PRAGMA user_version=1")
    upgrade = SqliteCheckpointStore._upgrade_approvals
    def fail(connection):
        upgrade(connection)
        raise sqlite3.OperationalError("migration fault")
    with monkeypatch.context() as patch:
        patch.setattr(SqliteCheckpointStore, "_upgrade_approvals", staticmethod(fail))
        with pytest.raises(sqlite3.OperationalError):
            SqliteCheckpointStore(store.path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='approvals'").fetchone() is None
    migrated = SqliteCheckpointStore(store.path)
    assert migrated.load("run-1") == before
    migrated.close()


def test_approval_write_requires_run_lock_and_stale_version_is_refused(ready):
    store, approvals, _, decision, _, _ = ready
    old = current(store)
    record = approvals.create(decision, old)
    with pytest.raises(CheckpointError):
        approvals.create(decision, old)
    store.release("run-1")
    with pytest.raises(RunBusyError):
        approvals.decide(record.approval_id, approved=True, actor="user")
    store.acquire("run-1")
    assert approvals.get(record.approval_id).state is ApprovalStatus.PENDING



@pytest.mark.parametrize("after_commit", [False, True])
def test_decision_commit_failure_never_returns_success(ready, monkeypatch, after_commit):
    store, approvals, _, decision, _, _ = ready
    record = approvals.create(decision, current(store))
    def fail(connection):
        if after_commit:
            connection.commit()
        raise sqlite3.OperationalError("decision fault")
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        approvals.decide(record.approval_id, approved=True, actor="user")
    assert error.value.committed is after_commit
    assert approvals.get(record.approval_id).state.value == ("APPROVED" if after_commit else "PENDING")
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"


def test_forged_ask_cannot_override_disabled_feature(ready):
    store, approvals, policy, decision, _, _ = ready
    policy.features.disable("probe")
    denied = policy.evaluate(decision.operation)
    assert denied.kind is PermissionKind.DENY
    with pytest.raises(ValueError):
        approvals.create(replace(denied, kind=PermissionKind.ASK), current(store))


def test_consume_rechecks_hard_feature_without_trusting_old_decision(ready):
    store, approvals, policy, decision, _, _ = ready
    record = approved(ready)
    policy.features.disable("probe")
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert approvals.get(record.approval_id).state is ApprovalStatus.INVALIDATED
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"


@pytest.mark.parametrize("state", ["PENDING", "APPROVED", "REJECTED", "INVALIDATED"])
def test_only_approved_can_be_consumed_and_invalidated_is_terminal(ready, state):
    store, approvals, _, decision, _, _ = ready
    record = approvals.create(decision, current(store))
    if state in {"APPROVED", "REJECTED"}:
        record = approvals.decide(record.approval_id, approved=state == "APPROVED", actor="user")
    if state == "INVALIDATED":
        record = approvals.invalidate(record.approval_id, "context expired")
    if state != "APPROVED":
        with pytest.raises(CheckpointError):
            approvals.consume_and_mark_running(record.approval_id, decision, current(store))
        assert store.load("run-1")["journal"][0]["state"] == "PREPARED"
    else:
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
        with pytest.raises(CheckpointError):
            approvals.invalidate(record.approval_id, "too late")


def test_backward_clock_cannot_produce_invalid_consumption_fact(ready):
    store, approvals, _, decision, _, clock = ready
    record = approved(ready)
    clock[0] = 99.0
    with pytest.raises(CheckpointError):
        approvals.consume_and_mark_running(record.approval_id, decision, current(store))
    assert approvals.get(record.approval_id) == record
    assert store.load("run-1")["journal"][0]["state"] == "PREPARED"


@pytest.mark.parametrize("after_commit", [False, True])
def test_request_commit_failure_is_atomic_and_retry_creates_one_record(ready, monkeypatch, after_commit):
    store, approvals, _, decision, data, _ = ready
    commit = store._commit
    def fail(connection):
        if after_commit:
            connection.commit()
        raise sqlite3.OperationalError("request fault")
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        approvals.create(decision, current(store))
    assert error.value.committed is after_commit
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == int(after_commit)
    monkeypatch.setattr(store, "_commit", commit)
    record = approvals.create(decision, current(store))
    assert record.state is ApprovalStatus.PENDING
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
    assert store.load("run-1")["snapshot"] == data


def test_conflicting_request_parameters_cannot_create_second_approval(ready):
    store, approvals, _, decision, _, _ = ready
    original = approvals.create(decision, current(store))
    call = decision.operation.call
    call["function"]["arguments"] = json.dumps({"label": "changed"})
    op = replace(decision.operation, call_json=canonical(call))
    with pytest.raises(CheckpointError):
        approvals.create(replace(decision, operation=op), current(store))
    assert approvals.get(original.approval_id) == original
    with store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_command_policy_hard_deny_cannot_be_relabelled_ask(tmp_path):
    registry = ToolRegistry()
    def bash(command):
        raise AssertionError("must not execute")
    registry.register(ToolSpec("bash", "shell", {}, bash))
    features = FeatureManager()
    policy = DefaultPermissionPolicy(registry, features, WorkspaceAuthority(tmp_path), CommandPolicy(), lambda: tmp_path)
    call = {"id": "bash-call", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": "rm -rf /"})}}
    operation = policy.bind("missing-run", 0, 0, call, workspace_digest="a" * 64, runtime_digest="b" * 64)
    denied = policy.evaluate(operation)
    assert denied.kind is PermissionKind.DENY
    store = SqliteCheckpointStore(tmp_path / "approval.sqlite3")
    with pytest.raises(ValueError, match="策略拒绝"):
        ApprovalStore(store, policy).create(replace(denied, kind=PermissionKind.ASK), 1)
    store.close()
