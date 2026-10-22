"""Regression tests for durable recovery entry points; no external provider."""
import json

import pytest

from test_run_snapshot import agents, calls
from cli.commands import create_agent_console, execute_agent_command
from core.checkpoint_store import CheckpointError
from core.tools.base_tools import ToolResult


def write_call(path, content="stale overwrite"):
    return {"id": "write-1", "type": "function", "function": {
        "name": "write_file", "arguments": json.dumps({"path": str(path), "content": content})}}


def test_run_id_history_selects_its_own_conversation(agents):
    agent, provider = agents([{}, {}, {}], pause_after=None)
    agent.enable_checkpoints()
    agent.enable_hitl()
    agent.run("conversation A")
    run_a, identity_a = agent._run_state.run_id, agent._conversation_id
    agent.start_new_conversation()
    agent.run("conversation B")
    execute_agent_command(create_agent_console(agent), agent, "/resume " + run_a)
    assert agent._conversation_id == identity_a
    agent.run("continue A")
    assert agent._checkpoint_store.conversation_for_run(agent._run_state.run_id) == identity_a
    assert len(provider.requests) == 3


def test_run_id_unknown_history_blocks_new_execution(agents):
    agent, _ = agents([{"tool_calls": calls("A")}], pause_after=None)

    def cancel(label):
        return ToolResult("interrupted", success=False, cancelled=True)

    agent.tool_registry.get_spec("probe").handler = cancel
    agent.enable_checkpoints()
    agent.enable_hitl()
    agent.run("uncertain A")
    run_a = agent._run_state.run_id
    approval = agent.get_pending_approval(run_a)
    agent.decide_approval(run_a, approval.approval_id, approved=True, actor="test")
    agent.resume_checkpoint(run_a)
    receiver, provider = agents([{}], pause_after=None)
    receiver.enable_checkpoints(agent._checkpoint_store.path)
    receiver.enable_hitl()
    receiver.run("unrelated B")
    execute_agent_command(create_agent_console(receiver), receiver, "/resume " + run_a)
    assert receiver.get_session_info()["execution_blocked"]
    assert "UNKNOWN" in receiver.run("continue A")
    assert len(provider.requests) == 1
    assert receiver._checkpoint_store.load(run_a)["journal"][0]["state"] == "UNKNOWN"


def test_native_continue_checks_the_same_workspace_as_fresh_resume(agents):
    agent, _ = agents([{"tool_calls": [calls("A")[0], write_call("file.txt")]}, {}])
    agent.enable_checkpoints()
    agent.run("task")
    target = agent.workdir / "file.txt"
    target.write_text("USER EDIT", encoding="utf-8")
    receiver, _ = agents([{}], pause_after=None)
    receiver.enable_checkpoints(agent._checkpoint_store.path)
    with pytest.raises(ValueError, match="工作区内容"):
        receiver.resume_checkpoint(agent._run_state.run_id)
    with pytest.raises(ValueError, match="工作区内容"):
        agent._continue_run()
    assert target.read_text(encoding="utf-8") == "USER EDIT"
    assert agent.executions == ["A"]


@pytest.mark.parametrize("kind", ["git", "build", "external"])
def test_approved_file_target_change_is_rejected(agents, kind):
    agent, provider = agents([{}, {}], pause_after=None)
    directory = agent.workdir.parent / "extra" if kind == "external" else agent.workdir / (".git" if kind == "git" else "build")
    directory.mkdir()
    if kind == "external":
        agent.workspace_authority.add_root(str(directory))
    target = directory / "target.txt"
    target.write_text("original", encoding="utf-8")
    provider.responses[0] = {"tool_calls": [write_call(target)]}
    agent.enable_checkpoints()
    agent.enable_hitl()
    agent.run("write")
    run = agent._run_state.run_id
    approval = agent.get_pending_approval(run)
    agent.decide_approval(run, approval.approval_id, approved=True, actor="test")
    target.write_text("USER EDIT", encoding="utf-8")
    with pytest.raises(ValueError, match="目标|前置"):
        agent.resume_checkpoint(run)
    assert target.read_text(encoding="utf-8") == "USER EDIT"
    assert agent._checkpoint_store.load(run)["journal"][0]["state"] == "PREPARED"
    assert agent._approval_store.get(approval.approval_id).state.value == "INVALIDATED"


def test_started_without_snapshot_can_be_archived_without_inventing_history(agents):
    agent, _ = agents([], pause_after=None)
    agent.enable_checkpoints()
    store = agent._checkpoint_store
    store.acquire("conversation:bootstrap")
    store.acquire("bootstrap-run")
    try:
        store.create_run("bootstrap-run", conversation_id="bootstrap",
                         workspace=str(agent.workdir.resolve()), title="interrupted")
    finally:
        store.release("bootstrap-run")
        store.release("conversation:bootstrap")
    with pytest.raises(CheckpointError, match="尚未提交"):
        agent.open_conversation("bootstrap")
    before = store.load("bootstrap-run")
    agent.archive_checkpoint("bootstrap-run", "abandon interrupted initialization")
    after = store.load("bootstrap-run")
    assert after["snapshot"] is None and after["journal"] == before["journal"] == []
    assert after["status"] == "started" and store.archive_info("bootstrap-run")
    agent.start_new_conversation()
    assert not store._locks


def test_unknown_scope_shell_cannot_resume_old_prepared_call(agents):
    shell = {"id": "shell-1", "type": "function", "function": {
        "name": "bash", "arguments": json.dumps({"command": "echo should-not-run"})}}
    agent, provider = agents([{"tool_calls": [calls("A")[0], shell]}, {}])
    agent.enable_checkpoints()
    agent.run("task")
    with pytest.raises((ValueError, CheckpointError), match="Shell|作用域"):
        agent._continue_run()
    assert len(provider.requests) == 1
    assert agent._checkpoint_store.load(agent._run_state.run_id)["journal"][1]["state"] == "PREPARED"


def test_empty_snapshot_archive_checks_durable_workspace_owner(agents):
    agent, _ = agents([], pause_after=None)
    agent.enable_checkpoints()
    store = agent._checkpoint_store
    store.acquire("conversation:foreign")
    store.acquire("foreign-run")
    try:
        store.create_run("foreign-run", conversation_id="foreign",
                         workspace=str(agent.workdir.parent.resolve()), title="other workspace")
    finally:
        store.release("foreign-run")
        store.release("conversation:foreign")
    with pytest.raises(ValueError, match="归档工作区"):
        agent.archive_checkpoint("foreign-run", "must not archive another workspace")
    assert store.archive_info("foreign-run") is None and not store._locks
