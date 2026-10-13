"""No permission test dispatches a handler or uses a paid provider."""
import json
import pytest

from test_run_snapshot import agents
from core.permissions import DefaultPermissionPolicy, PermissionDecision, PermissionKind, ToolOperation, digest
from core.features import FeatureDefinition


def call(name, args):
    return {"id": "permission-call", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def bound(agent, name, args):
    policy = DefaultPermissionPolicy.for_agent(agent)
    operation = policy.bind("run-permission", 0, 0, call(name, args),
        workspace_digest=agent.workspace_state_guard.content_digest(), runtime_digest=digest(agent._snapshot_compatibility()))
    return policy, operation


@pytest.mark.parametrize("name,args,kind", [
    ("read_file", {"path": "file.txt"}, "ALLOW"),
    ("list_files", {}, "ALLOW"),
    ("search_code", {"patterns": ["x"]}, "ALLOW"),
    ("count_occurrences", {"patterns": ["x"]}, "ALLOW"),
    ("get_background_status", {"job_id": "absent"}, "ALLOW"),
    ("write_file", {"path": "new.txt", "content": "new"}, "ASK"),
    ("edit_file", {"path": "file.txt", "edits": []}, "ASK"),
    ("bash", {"command": "echo safe"}, "ALLOW"),
    ("run_background", {"command": "echo safe"}, "ASK"),
    ("health_check", {"url": "https://example.invalid"}, "ASK"),
    ("TodoWrite", {"items": []}, "ALLOW"),
    ("update_agent_note", {"content": "note"}, "ALLOW"),
    ("probe", {"label": "A"}, "ASK"),
    ("not_registered", {}, "DENY"),
    ("bash", {"command": "rm -rf /"}, "DENY"),
    ("read_file", {}, "DENY"),
    ("search_code", {"patterns": ["x"], "paths": ["*.py"]}, "ASK"),
])
def test_default_decisions_are_data_only(agents, name, args, kind):
    agent, provider = agents()
    before = (list(agent.messages), agent.todo.items, agent.agent_note.read(), agent.attempt_history.export_state())
    policy, operation = bound(agent, name, args)
    decision = policy.evaluate(operation)
    assert decision.kind.value == kind and decision.rule_id and decision.reason
    assert decision.operation.provider_call_id == "permission-call"
    assert PermissionDecision.from_dict(json.loads(json.dumps(decision.to_dict()))) == decision
    assert ToolOperation.from_dict(operation.to_dict()) == operation
    assert agent.executions == [] and provider.requests == []
    assert (agent.messages, agent.todo.items, agent.agent_note.read(), agent.attempt_history.export_state()) == before


def test_disabled_feature_is_hard_deny_and_cannot_be_prompted(agents):
    agent, _ = agents()
    agent.feature_manager.disable("probe_feature")
    policy, operation = bound(agent, "probe", {"label": "A"})
    decision = policy.evaluate(operation)
    assert decision.kind is PermissionKind.DENY and decision.rule_id == "feature.disabled"
    assert agent.executions == []


@pytest.mark.parametrize("name,args", [
    ("read_file", {"path": "../outside.txt"}),
    ("write_file", {"path": "../outside.txt", "content": "x"}),
    ("search_code", {"patterns": ["x"], "paths": ["../*.py"]}),
])
def test_hard_path_boundary_precedes_approval(agents, name, args):
    agent, _ = agents()
    policy, operation = bound(agent, name, args)
    assert policy.evaluate(operation).kind is PermissionKind.DENY
    assert agent.executions == []


def test_read_name_does_not_whitelist_replaced_handler(agents):
    agent, _ = agents()
    def custom(path):
        raise AssertionError("must not execute")
    agent.tool_registry.get_spec("read_file").handler = custom
    policy, operation = bound(agent, "read_file", {"path": "file.txt"})
    assert policy.evaluate(operation).kind is PermissionKind.ASK


def test_current_policy_cwd_and_feature_changes_invalidate_old_context(agents):
    agent, _ = agents()
    policy, operation = bound(agent, "write_file", {"path": "new.txt", "content": "x"})
    assert policy.evaluate(operation).kind is PermissionKind.ASK
    agent.runtime_context.shell_session.cwd = agent.workdir / "src"
    assert policy.evaluate(operation).kind is PermissionKind.DENY
    agent.runtime_context.shell_session.cwd = agent.workdir
    agent.feature_manager.register_feature(FeatureDefinition("changed"))
    assert policy.evaluate(operation).kind is PermissionKind.DENY


def test_windows_start_cannot_bypass_disabled_background(agents, monkeypatch):
    agent, _ = agents()
    monkeypatch.setattr("core.permissions.sys.platform", "win32")
    agent.feature_manager.disable("background")
    policy, operation = bound(agent, "bash", {"command": "start echo safe"})
    decision = policy.evaluate(operation)
    assert decision.kind is PermissionKind.DENY and decision.rule_id == "feature.background_disabled"


def test_changed_workspace_contents_are_not_an_old_permission_source(agents):
    agent, _ = agents()
    policy, operation = bound(agent, "write_file", {"path": "new.txt", "content": "x"})
    (agent.workdir / "file.txt").write_text("external change", encoding="utf-8")
    assert policy.evaluate(operation).kind is PermissionKind.DENY
