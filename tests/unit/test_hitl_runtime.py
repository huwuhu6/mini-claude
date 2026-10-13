"""Durable HITL at the existing Main Agent dispatch boundary; no paid provider."""
from copy import deepcopy
import json
import sqlite3
import pytest

from test_run_snapshot import agents, calls
from core.approvals import ApprovalStatus
from core.checkpoint_store import CheckpointError, CheckpointCommitError, RunBusyError
from core.permissions import PermissionDecision, PermissionKind
from core.run_state import RunStatus


def configure(agent, database=None):
    agent.enable_checkpoints(database)
    agent.enable_hitl()
    # Deterministic test policy. Production defaults still classify custom tools ASK.
    real = agent._permission_policy.evaluate
    def evaluate(op):
        decision = real(op)
        if decision.kind is PermissionKind.ASK and op.arguments.get("label") in {"A", "C"}:
            return PermissionDecision(PermissionKind.ALLOW, op, "test.read_only", "deterministic mock")
        return decision
    agent._permission_policy.evaluate = evaluate


def waiting(agents):
    a, provider = agents([{"content": "batch", "tool_calls": calls("A", "B", "C")}, {}], pause_after=None)
    configure(a)
    result = a.run("original task", require_tool_call=True)
    assert "审批" in result
    assert a._run_state.status is RunStatus.WAITING_APPROVAL
    return a, provider


def receiver(agents, a):
    b, provider = agents([{}], pause_after=None)
    configure(b, a._checkpoint_store.path)
    return b, provider


def assert_closed(agent):
    messages = [m for m in agent.messages if m.role == "tool"]
    assert [m.tool_call_id for m in messages] == ["call-0", "call-1", "call-2"]
    assert len({m.tool_call_id for m in messages}) == 3


def test_hitl_requires_checkpoint(agents):
    a, _ = agents([], pause_after=None)
    with pytest.raises(CheckpointError):
        a.enable_hitl()


def test_waiting_keeps_prefix_and_prepared_and_releases_lock(agents):
    a, provider = waiting(agents)
    state = a._run_state
    record = a.get_pending_approval(state.run_id)
    row = a._checkpoint_store.load(state.run_id)
    assert record.state is ApprovalStatus.PENDING
    assert record.decision.operation.provider_call_id == "call-1"
    assert row["status"] == "waiting_approval"
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "PREPARED", "PREPARED"]
    assert state.tool_batch.next_call_index == 1
    assert list(state.tool_batch.results) == ["call-0"]
    assert [m.tool_call_id for m in a.messages if m.role == "tool"] == ["call-0"]
    assert a.executions == ["A"] and len(provider.requests) == 1
    assert not a._checkpoint_store.holds_lock(state.run_id)
    history = deepcopy(a.attempt_history.export_state())
    assert "审批" in a._continue_run()
    assert a.attempt_history.export_state() == history
    assert a.executions == ["A"] and len(provider.requests) == 1


def test_fresh_pending_then_approved_executes_original_suffix_once(agents):
    a, _ = waiting(agents)
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    b, provider = receiver(agents, a)
    assert b.get_pending_approval(run).approval_id == approval.approval_id
    assert "审批" in b.resume_checkpoint(run)
    assert b.executions == [] and provider.requests == []
    assert b._run_state.status is RunStatus.WAITING_APPROVAL
    before = deepcopy(b.messages)
    assert "错误" in b.chat("cannot overwrite waiting Run")
    assert "错误" in b.run("cannot overwrite waiting Run")
    assert b.messages == before
    first = b.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    assert b.decide_approval(run, approval.approval_id, approved=True, actor="tester") == first
    assert b.resume_checkpoint(run) == "done"
    assert b.executions == ["B", "C"] and len(provider.requests) == 1
    assert b._run_state.model_turns_used == 2
    assert_closed(b)
    assert b._approval_store.get(approval.approval_id).state is ApprovalStatus.CONSUMED
    assert b.resume_checkpoint(run) == "done"
    assert b.executions == ["B", "C"] and len(provider.requests) == 1


def test_rejection_closes_suffix_without_execution_or_failure_history(agents):
    a, _ = waiting(agents)
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    history = a.attempt_history.export_state()
    b, provider = receiver(agents, a)
    b.decide_approval(run, approval.approval_id, approved=False, actor="tester")
    create = provider.create_message
    def verify_history(*args, **kwargs):
        assert b.attempt_history.export_state() == history
        return create(*args, **kwargs)
    provider.create_message = verify_history
    assert b.resume_checkpoint(run) == "done"
    assert b.executions == [] and len(provider.requests) == 1
    assert_closed(b)
    # Normal final-answer completion metadata may annotate A; no new attempts.
    assert len(b.attempt_history.all()) == len(history["events"])
    row = b._checkpoint_store.load(run)
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED", "NOT_EXECUTED"]
    assert all("NOT_EXECUTED" in m.content for m in b.messages if m.tool_call_id in {"call-1", "call-2"})


def test_waiting_cannot_use_json_or_new_input(agents):
    a, _ = waiting(agents)
    before = deepcopy(a.messages)
    assert "错误" in a.chat("overwrite")
    assert "错误" in a.run("overwrite")
    assert a.messages == before
    with pytest.raises(ValueError):
        a.export_run_snapshot()
    data = a._checkpoint_store.load(a._run_state.run_id)["snapshot"]
    b, _ = receiver(agents, a)
    with pytest.raises(ValueError):
        b.restore_run_snapshot(data)
    data["schema_version"] = 1
    data.pop("checkpoint_binding")
    with pytest.raises(ValueError):
        b.restore_run_snapshot(data)


@pytest.mark.parametrize("kind", ["read", "feature", "command", "outside", "unknown"])
def test_actual_default_policy_allow_deny_and_unknown_ask(agents, kind):
    definition = {"read": ("read_file", {"path": "file.txt"}),
        "feature": ("probe", {"label": "B"}), "command": ("bash", {"command": "rm -rf /"}),
        "outside": ("write_file", {"path": "../outside.txt", "content": "no"}),
        "unknown": ("probe", {"label": "B"})}[kind]
    call = {"id": "specific", "type": "function", "function": {
        "name": definition[0], "arguments": json.dumps(definition[1])}}
    a, provider = agents([{"tool_calls": [call]}, {}], pause_after=None)
    a.enable_checkpoints()
    a.enable_hitl()  # Actual default policy, without configure's mock allowance.
    if kind == "feature":
        a.feature_manager.disable("probe_feature")
    result = a.run("task")
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == []
    if kind == "unknown":
        assert row["status"] == "waiting_approval" and row["journal"][0]["state"] == "PREPARED"
        assert len(provider.requests) == 1
    else:
        assert row["status"] == "completed" and len(provider.requests) == 2
        assert row["journal"][0]["state"] == ("COMPLETED" if kind == "read" else "NOT_EXECUTED")
        assert len([m for m in a.messages if m.tool_call_id == "specific"]) == 1
        if kind != "read":
            assert "NOT_EXECUTED" in a.messages[-2].content
            assert len(a.attempt_history.all()) == 0


@pytest.mark.parametrize("change", ["feature", "workspace", "handler", "command", "authority", "cwd"])
def test_approved_context_change_fails_closed_and_invalidates(agents, change):
    a, _ = waiting(agents)
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    b, provider = receiver(agents, a)
    b.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    if change == "feature":
        b.feature_manager.disable("probe_feature")
    elif change == "workspace":
        (b.workdir / "file.txt").write_text("external change", encoding="utf-8")
    elif change == "handler":
        b.tool_registry._tools["probe"].handler = lambda label: b.executions.append("wrong")
    elif change == "command":
        import re
        b.command_policy.DESTRUCTIVE = [*b.command_policy.DESTRUCTIVE, re.compile("newly forbidden")]
    elif change == "authority":
        # Even expanded grants must not silently expand an old approval's context.
        b.workspace_authority.additional_roots.append(b.workdir.parent.resolve())
    else:
        b.workspace_authority.primary_root = b.workdir / "src"
    with pytest.raises((CheckpointError, ValueError)):
        b.resume_checkpoint(run)
    assert b.executions == [] and provider.requests == []
    assert b._approval_store.get(approval.approval_id).state is ApprovalStatus.INVALIDATED
    assert b._checkpoint_store.load(run)["journal"][1]["state"] == "PREPARED"


def test_same_instance_approval_refresh_does_not_reset_state(agents):
    a, provider = waiting(agents)
    run = a._run_state.run_id
    a.todo.update([{"content": "keep plan", "status": "in_progress", "activeForm": "working"}])
    a.agent_note.replace("durable note")
    # Restore must retain persisted facts, not edits made only in old memory.
    saved = a._checkpoint_store.load(run)["snapshot"]
    approval = a.get_pending_approval(run)
    a.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    assert a._continue_run() == "done"
    assert a.todo.items == saved["todo"] and a.agent_note.read() == saved["agent_note"]
    assert a.executions == ["A", "B", "C"] and len(provider.requests) == 2
    assert a._run_state.run_id == run and a._run_state.model_turns_used == 2


@pytest.mark.parametrize("stage", ["request", "request_committed", "consume", "consume_committed", "result", "result_committed"])
def test_transaction_failures_never_dispatch_or_replay_uncertain_tools(agents, monkeypatch, stage):
    if stage.startswith("request"):
        a, provider = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
        configure(a)
        run = None
    else:
        original, _ = waiting(agents)
        run = original._run_state.run_id
        approval = original.get_pending_approval(run)
        a, provider = receiver(agents, original)
        a.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    store = a._checkpoint_store
    commit = store._commit
    def fail(connection):
        approval_row = connection.execute("SELECT state FROM approvals LIMIT 1").fetchone()
        row = connection.execute("SELECT payload FROM runs LIMIT 1").fetchone()
        data = json.loads(row[0]) if row and row[0] else None
        batch = data["run"]["tool_batch"] if data else None
        cursor = batch["next_call_index"] if batch else -1
        hit = ((stage.startswith("request") and approval_row and approval_row[0] == "PENDING")
               or (stage.startswith("consume") and approval_row and approval_row[0] == "CONSUMED" and cursor == 1)
               or (stage.startswith("result") and cursor == 2))
        if hit:
            if stage.endswith("committed"):
                commit(connection)
            raise sqlite3.OperationalError("HITL injected transaction fault")
        commit(connection)
    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(CheckpointCommitError) as error:
        if stage.startswith("request"):
            a.run("task")
            pytest.fail("fault not reached")
        else:
            a.resume_checkpoint(run)
    run = run or a._run_state.run_id
    assert error.value.committed is stage.endswith("committed")
    assert a.executions == (["A"] if stage.startswith("request") else ["B"] if stage.startswith("result") else [])
    assert not store.holds_lock(run)
    monkeypatch.setattr(store, "_commit", commit)
    row = store.load(run)
    if stage == "request":
        with store._connection() as connection:
            assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert row["status"] == "active" and row["snapshot"]["run"]["tool_batch"]["next_call_index"] == 1
    elif stage == "request_committed":
        assert row["status"] == "waiting_approval"
    elif stage == "consume":
        assert row["status"] == "waiting_approval" and a.get_pending_approval(run).state is ApprovalStatus.APPROVED
    elif stage in {"consume_committed", "result"}:
        b, bp = receiver(agents, a)
        with pytest.raises(CheckpointError, match="UNKNOWN"):
            b.resume_checkpoint(run)
        assert b.executions == [] and bp.requests == []
        assert b._checkpoint_store.load(run)["status"] == "unknown"
    else:
        b, bp = receiver(agents, a)
        assert b.resume_checkpoint(run) == "done"
        assert b.executions == ["C"] and len(bp.requests) == 1


def test_waiting_request_partial_write_rolls_back_both_facts(agents, monkeypatch):
    a, _ = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    configure(a)
    create = a._approval_store.create
    def crash(*args, **kwargs):
        create(*args, **kwargs)
        raise RuntimeError("after approval insert, before waiting projection")
    monkeypatch.setattr(a._approval_store, "create", crash)
    with pytest.raises(CheckpointError):
        a.run("task")
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row["status"] == "active" and a.executions == ["A"]
    with a._checkpoint_store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_expired_pending_and_approved_never_dispatch(agents):
    for approved in (False, True):
        a, _ = waiting(agents)
        run = a._run_state.run_id
        record = a.get_pending_approval(run)
        if approved:
            a.decide_approval(run, record.approval_id, approved=True, actor="tester")
        # Fixture injects expiry only, leaving all operation bindings unchanged.
        with a._checkpoint_store._connection() as connection:
            connection.execute("UPDATE approvals SET expires_at=? WHERE approval_id=?", (record.to_dict()["created_at"] + 1, record.approval_id))
        a._approval_store.clock = lambda: record.to_dict()["created_at"] + 2
        with pytest.raises(CheckpointError, match="过期"):
            a.resume_checkpoint(run)
        assert a.executions == ["A"]
        assert a._approval_store.get(record.approval_id).state is ApprovalStatus.INVALIDATED


def test_cross_run_id_and_competing_executor_are_refused(agents):
    a, _ = waiting(agents)
    b, _ = waiting(agents)
    ar, br = a._run_state.run_id, b._run_state.run_id
    aid, bid = a.get_pending_approval(ar), b.get_pending_approval(br)
    with pytest.raises(CheckpointError):
        a.decide_approval(ar, bid.approval_id, approved=True, actor="tester")
    c, provider = receiver(agents, a)
    c.decide_approval(ar, aid.approval_id, approved=True, actor="tester")
    a._checkpoint_store.acquire(ar)
    try:
        with pytest.raises(RunBusyError):
            c.resume_checkpoint(ar)
        assert c.executions == [] and provider.requests == []
    finally:
        a._checkpoint_store.release(ar)
    assert c.resume_checkpoint(ar) == "done"
    assert c.executions == ["B", "C"]


def test_hard_stop_still_stops_suffix(agents):
    from core.loop_controller import RuntimeEscalationException
    a, provider = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    configure(a)
    def stop(*args):
        raise RuntimeEscalationException("HARD_STOP test")
    a.loop_controller.check = stop
    assert "HARD_STOP" in a.run("task")
    assert a.executions == [] and len(provider.requests) == 1
    assert a._run_state.status is RunStatus.TERMINATED
    assert_closed(a)


def test_waiting_compression_preserves_calls(agents):
    a, _ = waiting(agents)
    before = deepcopy(a.messages)
    assert a.has_pending_tool_batch
    # Both manual compaction and auto compression already protect pending batches.
    a._check_auto_compress()
    from cli.commands import _compact
    assert "尚未完成" in _compact(a, [], {})
    assert a.messages == before


def process_base(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "config.yaml").write_text("features:\n  skills: false\n", encoding="utf-8")
    return tmp_path


def launch_hitl(base, mode, *, wait=True):
    import os
    import subprocess
    import sys
    from pathlib import Path
    worker = Path(__file__).with_name("hitl_process_probe.py")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    args = [sys.executable, "-B", str(worker), str(base), mode]
    if not wait:
        return subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=35)


def process_effects(base):
    path = base / "effects.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def report(child, code=0):
    assert child.returncode == code, child.stdout + child.stderr
    return json.loads(child.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_real_process_wait_decide_resume_and_repeat(tmp_path, decision):
    base = process_base(tmp_path)
    start = report(launch_hitl(base, "start"))
    assert start["status"] == "waiting_approval" and start["requests"] == 1
    assert start["approval"] == "PENDING"
    pending = report(launch_hitl(base, "resume"))
    assert pending["approval"] == "PENDING" and pending["requests"] == 0
    assert [r["label"] for r in process_effects(base)] == ["A"]
    decided = report(launch_hitl(base, decision))
    assert decided["requests"] == 0
    finished = report(launch_hitl(base, "resume"))
    assert finished["status"] == "completed" and finished["requests"] == 1
    effects = process_effects(base)
    assert [r["label"] for r in effects] == (["A", "B", "C"] if decision == "approve" else ["A"])
    if decision == "approve":
        assert effects[0]["pid"] != effects[1]["pid"] == effects[2]["pid"]
    assert start["pid"] != pending["pid"] != decided["pid"] != finished["pid"]
    repeated = report(launch_hitl(base, "resume"))
    assert repeated["requests"] == 0 and process_effects(base) == effects


@pytest.mark.parametrize("mode,code,expected", [("running", 42, ["A"]),
    ("before_result", 43, ["A", "B"]), ("after_result", 44, ["A", "B", "C"])])
def test_real_process_approval_consumption_and_effect_crash(tmp_path, mode, code, expected):
    base = process_base(tmp_path)
    report(launch_hitl(base, "start"))
    report(launch_hitl(base, "approve"))
    crashed = launch_hitl(base, mode)
    assert crashed.returncode == code, crashed.stdout + crashed.stderr
    restored = report(launch_hitl(base, "resume"), 0 if mode == "after_result" else 7)
    assert restored["requests"] == (1 if mode == "after_result" else 0)
    if mode != "after_result":
        assert "UNKNOWN" in restored["error"]
        report(launch_hitl(base, "resume"), 7)
        forbidden = report(launch_hitl(base, "approve"), 7)
        assert forbidden["requests"] == 0
    assert [r["label"] for r in process_effects(base)] == expected


def test_real_process_competing_resume_live_handler_then_death_unknown(tmp_path):
    import time
    base = process_base(tmp_path)
    report(launch_hitl(base, "start"))
    report(launch_hitl(base, "approve"))
    holder = launch_hitl(base, "hold", wait=False)
    try:
        deadline = time.monotonic() + 20
        while not (base / "ready").exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (base / "ready").exists(), "worker did not reach B handler"
        contender = report(launch_hitl(base, "resume"), 7)
        assert contender["requests"] == 0 and "有效执行者" in contender["error"]
        holder.kill()
        holder.communicate(timeout=10)
        restored = report(launch_hitl(base, "resume"), 7)
        assert "UNKNOWN" in restored["error"] and restored["requests"] == 0
        assert [r["label"] for r in process_effects(base)] == ["A", "B"]
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=10)


def stateful_handler(agent):
    original = agent.tool_registry._tools["probe"].handler
    def execute(label):
        result = original(label)
        if label == "A":
            agent.todo.update([{"content": "keep plan", "status": "in_progress", "activeForm": "working"}])
            agent.agent_note.replace("remember approval task")
            agent.runtime_context.shell_session.cwd = agent.workdir / "src"
        return result
    agent.tool_registry._tools["probe"].handler = execute


def test_saved_todo_note_cwd_budget_and_history_survive_approval(agents):
    a, provider = agents([{"tool_calls": calls("A", "B", "C")}, {}], pause_after=None)
    stateful_handler(a)
    configure(a)
    a.run("original task", require_tool_call=True)
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    saved = a._checkpoint_store.load(run)["snapshot"]
    b, bp = agents([{}], pause_after=None)
    stateful_handler(b)
    configure(b, a._checkpoint_store.path)
    b.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    create = bp.create_message
    def check_before_model(*args, **kwargs):
        assert b.todo.items == saved["todo"]
        assert b.agent_note.read() == saved["agent_note"]
        assert str(b.runtime_context.cwd.resolve()) == saved["workspace"]["cwd"]
        assert b._run_state.max_iterations == saved["run"]["max_iterations"]
        assert b._run_state.require_tool_call
        assert b.executions == ["B", "C"]
        assert b._run_state.model_turns_used == 1  # next response is not yet received
        return create(*args, **kwargs)
    bp.create_message = check_before_model
    assert b.resume_checkpoint(run) == "done"
    assert len(provider.requests) == len(bp.requests) == 1
    assert b._run_state.model_turns_used == 2
    assert len(b.attempt_history.all()) == 3
    assert_closed(b)


def test_real_saved_cwd_removed_invalidates_approval(agents):
    a, _ = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    stateful_handler(a)
    configure(a)
    a.run("task")
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    b, bp = agents([{}], pause_after=None)
    stateful_handler(b)
    configure(b, a._checkpoint_store.path)
    b.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    (a.workdir / "src").rmdir()
    with pytest.raises(ValueError, match="cwd"):
        b.resume_checkpoint(run)
    assert b._approval_store.get(approval.approval_id).state is ApprovalStatus.INVALIDATED
    assert b.executions == [] and bp.requests == []


@pytest.mark.parametrize("terminal", ["cancel", "error"])
def test_approved_handler_cancel_and_exception_keep_execution_facts(agents, terminal):
    from core.tools.base_tools import ToolResult
    def install(agent):
        original = agent.tool_registry._tools["probe"].handler
        def execute(label):
            result = original(label)
            if label == "B":
                if terminal == "cancel":
                    return ToolResult("cancel after effect", success=False, cancelled=True)
                raise TypeError("inside handler after effect")
            return result
        agent.tool_registry._tools["probe"].handler = execute
    a, _ = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    install(a)
    configure(a)
    a.run("task")
    approval = a.get_pending_approval(a._run_state.run_id)
    b, bp = agents([{}], pause_after=None)
    install(b)
    configure(b, a._checkpoint_store.path)
    b.decide_approval(a._run_state.run_id, approval.approval_id, approved=True, actor="tester")
    b.resume_checkpoint(a._run_state.run_id)
    row = b._checkpoint_store.load(a._run_state.run_id)
    assert row["journal"][1]["state"] == ("UNKNOWN" if terminal == "cancel" else "COMPLETED")
    assert b.executions == (["B"] if terminal == "cancel" else ["B", "C"])
    assert len(bp.requests) == (0 if terminal == "cancel" else 1)
    assert_closed(b)


def legacy_v2(store):
    path = store.path
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in ('approval_feedback', 'permission_grants', 'run_archives', 'conversation_runs', 'conversations'):
            connection.execute('DROP TABLE ' + table)
        connection.execute("""CREATE TABLE runs_old (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN
            ('started','active','paused','completed','terminated','failed','cancelled','unknown')),
            revision INTEGER NOT NULL CHECK(revision > 0), payload TEXT, payload_hash TEXT, final_result TEXT,
            last_operation TEXT NOT NULL, journal_hash TEXT NOT NULL DEFAULT '',
            boundary TEXT NOT NULL DEFAULT 'tools' CHECK(boundary IN ('tools','before_model','terminal')))""")
        connection.execute("INSERT INTO runs_old SELECT * FROM runs")
        connection.execute("DROP TABLE runs")
        connection.execute("ALTER TABLE runs_old RENAME TO runs")
        connection.execute("PRAGMA user_version=2")
    return path


@pytest.mark.parametrize("fail", [False, True])
def test_genuine_v2_upgrade_preserves_approval_calls_and_rolls_back(agents, monkeypatch, fail):
    from core.approvals import ApprovalStore
    from core.checkpoint_store import SqliteCheckpointStore, DB_VERSION
    a, _ = agents([{"tool_calls": calls("A", "B")}], pause_after="A")
    # Establish a normal v2 projection, with a standalone 7A approval record.
    a.enable_checkpoints()
    a.run("task")
    store = a._checkpoint_store
    run = a._run_state.run_id
    from core.permissions import DefaultPermissionPolicy, digest
    policy = DefaultPermissionPolicy.for_agent(a)
    data = store.load(run)
    op = policy.bind(run, 0, 1, data["snapshot"]["run"]["tool_batch"]["tool_calls"][1],
        workspace_digest=data["snapshot"]["workspace"]["content_digest"], runtime_digest=digest(data["snapshot"]["compatibility"]))
    store.acquire(run)
    record = ApprovalStore(store, policy).create(policy.evaluate(op), data["revision"])
    before = store.load(run)
    path = legacy_v2(store)
    if fail:
        upgrade = SqliteCheckpointStore._upgrade_waiting
        def crash(connection):
            upgrade(connection)
            raise sqlite3.OperationalError("after runs rebuild")
        with monkeypatch.context() as patch:
            patch.setattr(SqliteCheckpointStore, "_upgrade_waiting", staticmethod(crash))
            with pytest.raises(sqlite3.OperationalError):
                SqliteCheckpointStore(path)
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1
            assert connection.execute("SELECT name FROM sqlite_master WHERE name='runs_upgrade'").fetchone() is None
    migrated = SqliteCheckpointStore(path)
    assert migrated.load(run) == before
    assert ApprovalStore(migrated, policy).get(record.approval_id) == record
    with migrated._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    migrated.close()


def test_legacy_json_run_fields_decode_without_approval(agents):
    from test_run_snapshot import paused
    a, snapshot = paused(agents)
    del snapshot["run"]["approval_id"]
    b, _ = agents([{}], pause_after=None)
    b.restore_run_snapshot(snapshot)
    assert b._run_state.approval_id is None
    assert b._continue_run() == "done"


@pytest.mark.parametrize("error", [KeyboardInterrupt("post wait interrupt"), OSError("post wait diagnostic")])
def test_control_return_failure_preserves_durable_pending_and_signal(agents, monkeypatch, error):
    a, provider = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    configure(a)
    def fail(*args):
        raise error
    monkeypatch.setattr(a, "_approval_wait_message", fail)
    with pytest.raises(type(error)) as caught:
        a.run("task")
    assert caught.value is error
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row["status"] == "waiting_approval"
    assert a.executions == ["A"] and len(provider.requests) == 1
    assert [m.tool_call_id for m in a.messages if m.role == "tool"] == ["call-0"]
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "PREPARED", "PREPARED"]
    assert not a._checkpoint_store.holds_lock(a._run_state.run_id)


@pytest.mark.parametrize("error", [OSError("cannot observe context"), ValueError("policy contract broken"), TypeError("policy bug")])
def test_policy_io_failure_is_not_unknown_or_handler_failure(agents, monkeypatch, error):
    a, _ = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    configure(a)
    evaluate = a._permission_policy.evaluate
    def fail(op):
        if op.arguments.get("label") == "B":
            raise error
        return evaluate(op)
    monkeypatch.setattr(a._permission_policy, "evaluate", fail)
    with pytest.raises(type(error)) as caught:
        a.run("task")
    assert caught.value is error
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert a.executions == ["A"]
    assert row["journal"][1]["state"] == "PREPARED"
    assert [m.tool_call_id for m in a.messages if m.role == "tool"] == ["call-0"]


def test_each_ask_keeps_one_current_approval_and_never_regenerates_batch(agents):
    a, provider = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    a.enable_checkpoints()
    a.enable_hitl()
    a.run("task")
    run = a._run_state.run_id
    approval_ids = []
    for index, label in enumerate(("A", "B", "C")):
        b, bp = agents([{}] if label == "C" else [], pause_after=None)
        b.enable_checkpoints(a._checkpoint_store.path)
        b.enable_hitl()
        record = b.get_pending_approval(run)
        approval_ids.append(record.approval_id)
        assert record.decision.operation.ordinal == index
        assert record.decision.operation.arguments == {"label": label}
        b.decide_approval(run, record.approval_id, approved=True, actor="tester")
        b.resume_checkpoint(run)
        assert b.executions == [label]
        assert len(bp.requests) == (1 if label == "C" else 0)
    assert len(set(approval_ids)) == 3
    assert len(provider.requests) == 1
    assert all(b._approval_store.get(ident).state is ApprovalStatus.CONSUMED for ident in approval_ids)
    assert_closed(b)


def test_duplicate_after_approved_call_keeps_original_dedup_semantics(agents):
    batch = calls("A", "B", "B", "C")
    a, _ = agents([{"tool_calls": batch}], pause_after=None)
    configure(a)
    a.run("task")
    run = a._run_state.run_id
    record = a.get_pending_approval(run)
    b, bp = receiver(agents, a)
    b.decide_approval(run, record.approval_id, approved=True, actor="tester")
    assert b.resume_checkpoint(run) == "done"
    assert b.executions == ["B", "C"] and len(bp.requests) == 1
    row = b._checkpoint_store.load(run)
    assert row["journal"][2]["state"] == "NOT_EXECUTED"
    assert row["snapshot"]["run"]["tool_batch"]["results"]["call-2"]["outcome"] == "SKIPPED_DUPLICATE"
    assert [m.tool_call_id for m in b.messages if m.role == "tool"] == [c["id"] for c in batch]
    with b._checkpoint_store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_hard_stop_after_approval_never_consumes_or_dispatches(agents):
    from core.loop_controller import RuntimeEscalationException
    a, _ = waiting(agents)
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    b, bp = receiver(agents, a)
    b.decide_approval(run, approval.approval_id, approved=True, actor="tester")
    def stop(*args):
        raise RuntimeEscalationException("HARD_STOP approved suffix")
    b.loop_controller.check = stop
    assert "HARD_STOP" in b.resume_checkpoint(run)
    assert b.executions == [] and bp.requests == []
    assert b._approval_store.get(approval.approval_id).state is ApprovalStatus.APPROVED
    row = b._checkpoint_store.load(run)
    assert row["status"] == "terminated"
    assert [r["state"] for r in row["journal"]] == ["COMPLETED", "NOT_EXECUTED", "NOT_EXECUTED"]
    assert_closed(b)
    assert "HARD_STOP" in b.resume_checkpoint(run)


@pytest.mark.parametrize("tamper", ["parameters", "provider_id", "run_id"])
def test_corrupted_approval_binding_cannot_dispatch(agents, tamper):
    from core.permissions import canonical
    a, _ = waiting(agents)
    run = a._run_state.run_id
    record = a.get_pending_approval(run)
    a.decide_approval(run, record.approval_id, approved=True, actor="tester")
    data = record.decision.to_dict()
    operation = data["operation"]
    if tamper == "run_id":
        operation["run_id"] = "other-run"
    else:
        call = json.loads(operation["call_json"])
        if tamper == "parameters":
            call["function"]["arguments"] = json.dumps({"label": "modified"})
        else:
            call["id"] = "different-provider-id"
        operation["call_json"] = canonical(call)
    # Corruption injection must not be mistaken for an authorized new operation.
    with a._checkpoint_store._connection() as connection:
        connection.execute("UPDATE approvals SET decision_json=? WHERE approval_id=?", (canonical(data), record.approval_id))
    b, bp = receiver(agents, a)
    with pytest.raises(CheckpointError):
        b.resume_checkpoint(run)
    assert b.executions == [] and bp.requests == []


@pytest.mark.parametrize("arguments", ["null", "[]", "42", "{bad json", '{"label":NaN}'])
def test_bad_arguments_do_not_become_handler_or_approval(agents, arguments):
    batch = calls("A", "B", "C")
    batch[1]["function"]["arguments"] = arguments
    a, provider = agents([{"tool_calls": batch}, {}], pause_after=None)
    configure(a)
    a.run("task")
    assert a.executions == ["A", "C"] and len(provider.requests) == 2
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row["status"] == "completed" and row["journal"][1]["state"] == "NOT_EXECUTED"
    assert row["snapshot"]["run"]["tool_batch"]["results"]["call-1"]["outcome"] == "INVALID_ARGUMENTS"
    assert_closed(a)
    with a._checkpoint_store._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_hitl_disabled_cannot_restore_approval_wait(agents):
    a, _ = waiting(agents)
    b, bp = agents([{}], pause_after=None)
    b.enable_checkpoints(a._checkpoint_store.path)
    with pytest.raises(CheckpointError, match="HITL"):
        b.resume_checkpoint(a._run_state.run_id)
    assert b.executions == [] and bp.requests == [] and b._run_state is None
