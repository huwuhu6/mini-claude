"""Resource grants and terminal previews; real runtime, temporary data, no LLM API."""
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_run_snapshot import agents
from test_tool_batch_message_closure import make_agent, _call, _response
from test_hitl_runtime import configure
from test_permissions import bound
from core.permission_grants import grant_fingerprint
from core.permissions import canonical, digest, PermissionKind
from cli.operation_preview import operation_preview
from cli.commands import create_agent_console, execute_agent_command
from cli.ui import TerminalUI, wrap_lines


def call(name, args, identity="file-call"):
    return {"id": identity, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}


def waiting_file(agents, suffix=()):
    edit = call("edit_file", {"path": "file.txt", "edits": [
        {"search": "original", "replace": "edited"}]})
    a, provider = agents([{"tool_calls": [edit, *suffix]}, {}], pause_after=None)
    configure(a)
    a.chat("edit this file")
    run = a._run_state.run_id
    approval = a.get_pending_approval(run)
    assert approval.state.value == "PENDING"
    assert Path(approval.decision.operation.workspace, "file.txt").read_text() == "original"
    return a, provider, approval


def remember(a, approval, scope="conversation"):
    a.decide_approval(approval.decision.operation.run_id, approval.approval_id, approved=True, actor="local user")
    a.remember_approval(approval.decision.operation.run_id, approval.approval_id, scope)


def new_operation(a, old, name, arguments):
    return a._permission_policy.bind(old.run_id, old.turn, old.ordinal,
        call(name, arguments), workspace_digest=a.workspace_state_guard.content_digest(),
        runtime_digest=digest(a._snapshot_compatibility()))


@pytest.mark.parametrize("scope", ["conversation", "workspace"])
def test_file_grant_groups_edits_and_writes_without_replaying_batch(agents, scope):
    suffix = call("write_file", {"path": "file.txt", "content": "different contents"}, "write-call")
    a, p, approval = waiting_file(agents, [suffix])
    remember(a, approval, scope)
    assert a.resume_checkpoint(approval.decision.operation.run_id) == "done"
    assert len(p.requests) == 2
    assert Path(approval.decision.operation.workspace, "file.txt").read_text() == "different contents"
    results = [m for m in a.messages if m.role == "tool"]
    assert [m.tool_call_id for m in results] == ["file-call", "write-call"]
    assert all("错误" not in m.content for m in results)
    assert [row["state"] for row in a._checkpoint_store.load(approval.decision.operation.run_id)["journal"]] == ["COMPLETED", "COMPLETED"]
    assert a.resume_checkpoint(approval.decision.operation.run_id) == "done" and len(p.requests) == 2


@pytest.mark.parametrize("scope", ["conversation", "workspace"])
def test_file_rule_restart_same_conversation_and_new_conversation_scope(agents, scope):
    a, _, approval = waiting_file(agents)
    remember(a, approval, scope)
    a.resume_checkpoint(approval.decision.operation.run_id)
    conversation = a.get_session_info()["conversation_id"]
    b, p = agents([{"tool_calls": [call("write_file", {"path": "file.txt", "content": "restored"})]}, {},
                   {"tool_calls": [call("write_file", {"path": "file.txt", "content": "new conversation"})]}, {}], pause_after=None)
    configure(b, a._checkpoint_store.path)
    b.open_conversation(conversation)
    assert p.requests == []
    b.chat("a different edit after restart")
    assert b._run_state.status.value == "completed" and len(p.requests) == 2
    assert Path(approval.decision.operation.workspace, "file.txt").read_text() == "restored"
    b.start_new_conversation()
    b.chat("new conversation")
    assert b.get_session_info()["conversation_id"] != conversation
    assert b._run_state.status.value == ("waiting_approval" if scope == "conversation" else "completed")
    assert len(p.requests) == (3 if scope == "conversation" else 4)


def test_file_rule_matches_normalized_target_not_content_or_shell_cwd(agents):
    a, _, approval = waiting_file(agents)
    remember(a, approval)
    old = approval.decision.operation
    absolute = str(Path(old.workspace, "file.txt"))
    operation = new_operation(a, old, "write_file", {"path": absolute, "content": "another change"})
    assert a._permission_grants.allows(operation)
    a.runtime_context.shell_session.cwd = Path(old.workspace, "src")
    assert a._permission_grants.allows(new_operation(a, old, "write_file", {"path": "./file.txt", "content": "new"}))
    assert not a._permission_grants.allows(new_operation(a, old, "write_file", {"path": "other.txt", "content": "new"}))


def test_file_rule_does_not_authorize_replacement_handler_or_disabled_feature(agents):
    a, _, approval = waiting_file(agents)
    remember(a, approval)
    old = approval.decision.operation
    a.tool_registry.get_spec("write_file").handler = lambda path, content: pytest.fail("must not execute")
    changed = new_operation(a, old, "write_file", {"path": "file.txt", "content": "new"})
    assert not a._permission_grants.allows(changed)
    a.feature_manager.register_tool_for_feature("edit_file", "probe_feature")
    a.feature_manager.disable("probe_feature")
    assert a._permission_policy.evaluate(new_operation(a, old, "edit_file", old.arguments)).kind is PermissionKind.DENY


@pytest.mark.parametrize("path", [".env", ".env.production", ".git/config", "../outside.txt"])
def test_sensitive_protected_and_outside_file_cannot_be_remembered(agents, path):
    from core.permission_grants import can_remember
    a, _ = agents([], pause_after=None)
    configure(a)
    _, op = bound(a, "write_file", {"path": path, "content": "no"})
    assert not can_remember(op)
    with pytest.raises(ValueError):
        a._permission_grants.remember(op, "workspace")


def test_revocation_applies_to_other_instance(agents):
    a, _, approval = waiting_file(agents)
    remember(a, approval, "workspace")
    b, _ = agents([], pause_after=None)
    configure(b, a._checkpoint_store.path)
    assert b._permission_grants.allows(approval.decision.operation)
    assert a.revoke_permission(a.list_permissions()[0]["grant_id"])
    assert not b._permission_grants.allows(approval.decision.operation)


def test_legacy_exact_rules_remain_exact_not_file_rules(agents):
    a, _, approval = waiting_file(agents)
    op = approval.decision.operation
    store = a._checkpoint_store
    store.acquire(op.run_id)
    try:
        store.remember_permission(op.run_id, op.workspace, grant_fingerprint(op), canonical(op.to_dict()))
    finally:
        store.release(op.run_id)
    assert a._permission_grants.allows(op)
    changed = new_operation(a, op, "edit_file", {"path": "file.txt", "edits": [{"search": "original", "replace": "different"}]})
    assert not a._permission_grants.allows(changed)


def test_exact_shell_rule_binds_complete_command_cwd_and_platform(agents):
    from test_hitl_cli import waiting_cli, identity
    a, _, _, _ = waiting_cli(agents)
    run, _ = identity(a)
    _, op = bound(a, "bash", {"command": "echo literal && echo more"})
    op = replace(op, run_id=run)
    a._checkpoint_store.acquire(run)
    try:
        a._permission_grants.remember(op, "conversation")
    finally:
        a._checkpoint_store.release(run)
    assert a._permission_grants.allows(op)
    assert not a._permission_grants.allows(new_operation(a, op, "bash", {"command": "echo literal && echo other"}))
    a.runtime_context.shell_session.cwd = Path(op.workspace, "src")
    assert not a._permission_grants.allows(new_operation(a, op, "bash", op.arguments))


def test_preview_uses_full_file_global_lines_and_same_crlf_edit_preparation(agents):
    a, _ = agents([], pause_after=None)
    path = a.workdir / "file.txt"
    before = "\r\n".join(f"line {i}" for i in range(1, 61)) + "\r\n"
    path.write_bytes(before.encode())
    args = {"path": "file.txt", "edits": [{"search": "line 30\n", "replace": "changed 30\nextra\n"},
                                              {"search": "line 55\n", "replace": "changed 55\n"}]}
    _, op = bound(a, "edit_file", args)
    preview = operation_preview(op)
    assert "@@ -27,7 +27,8 @@" in preview and "-line 30" in preview and "+extra" in preview
    assert "-line 55" in preview and "+changed 55" in preview
    assert path.read_bytes() == before.encode()
    result = a.tools.edit_file(**args)
    assert result.success
    expected = before.replace("line 30\r\n", "changed 30\r\nextra\r\n").replace("line 55\r\n", "changed 55\r\n")
    assert path.read_bytes() == expected.encode()


@pytest.mark.parametrize("search", ["not present", "same"])
def test_invalid_preview_is_explicit_and_execution_does_not_write(agents, search):
    a, _ = agents([], pause_after=None)
    path = a.workdir / "file.txt"
    path.write_text("same\nsame\n", encoding="utf-8")
    before = path.read_bytes()
    args = {"path": "file.txt", "edits": [{"search": search, "replace": "new"}]}
    _, op = bound(a, "edit_file", args)
    assert "无法确认文件预览" in operation_preview(op)
    assert not a.tools.edit_file(**args).success
    assert path.read_bytes() == before


def test_write_preview_and_sensitive_values_are_display_only(agents):
    a, _ = agents([], pause_after=None)
    args = {"path": "new.txt", "content": "api_key = never-print\nnew content\n"}
    _, op = bound(a, "write_file", args)
    text = operation_preview(op)
    assert "新建文件" in text and "+2 / -0" in text
    assert "never-print" not in text and "已脱敏" in text
    assert op.arguments == args and not (a.workdir / "new.txt").exists()


@pytest.mark.parametrize("name,args", [
    ("edit_file", {"path": "file.txt", "edits": [{"search": ["original"], "replace": "new"}]}),
    ("write_file", {"path": "file.txt", "content": 123}),
])
def test_invalid_nested_parameter_types_have_explicit_preview_fallback(agents, name, args):
    a, _ = agents([], pause_after=None)
    _, operation = bound(a, name, args)
    assert "无法确认文件预览" in operation_preview(operation)
    assert (a.workdir / "file.txt").read_text() == "original"
    assert operation.arguments == args


def test_approval_rendered_once_and_file_has_no_shell_warning(agents, monkeypatch):
    a, _, approval = waiting_file(agents)
    out = StringIO()
    ui = TerminalUI(out)
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert ui.approval_choice(approval.decision.operation, "modify file") == ""
    text = out.getvalue()
    assert text.count("允许一次并继续") == 1
    assert text.count("本会话允许修改此文件") == 1
    assert "Shell 可产生" not in text and "原片段" not in text
    assert a.get_pending_approval(approval.decision.operation.run_id).state.value == "PENDING"


def test_numeric_fallback_rule_confirmation_and_resume(monkeypatch):
    ui = TerminalUI(StringIO())
    op = SimpleNamespace(tool_name="write_file", arguments={"path": "x"})
    monkeypatch.setattr("builtins.input", lambda _: "1")
    assert ui.confirm_rule(op, persistent=False)
    assert ui.continuation_choice("APPROVED") == "resume"
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert not ui.confirm_rule(op, persistent=False)
    assert ui.continuation_choice("APPROVED") == ""


@pytest.mark.parametrize("keys,expected", [("\r", ""), ("\x1b[B\r", "1"), ("y", "1"),
                                           ("\x7f\r", ""), ("\x08\r", "")])
def test_real_keyboard_initial_enter_is_pause_not_approval(monkeypatch, keys, expected):
    import prompt_toolkit
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    class TTYStream(StringIO):
        def isatty(self): return True
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        real = prompt_toolkit.PromptSession
        def session(**kwargs):
            assert kwargs["erase_when_done"] is True
            return real(input=pipe, output=DummyOutput(), **kwargs)
        monkeypatch.setattr(prompt_toolkit, "PromptSession", session)
        assert TerminalUI(TTYStream())._select([("1", "allow"), ("4", "reject")], default=None,
                                              shortcuts={"y": "1"}) == expected


def test_wrapping_never_loses_command_arguments():
    text = "命令：" + "a very long command 中文 arguments " * 12
    lines = wrap_lines(text, 25)
    assert "".join(lines) == text
    assert all(sum(2 if ord(c) > 127 else 1 for c in line) <= 24 for line in lines)


def test_status_and_permissions_show_ids_paths_and_resources_not_raw_calls(agents):
    a, _, approval = waiting_file(agents)
    remember(a, approval)
    info = a.get_session_info()
    assert info["conversation_id"] and info["run_id"] == approval.decision.operation.run_id
    assert Path(info["checkpoint_db"]) == a._checkpoint_store.path
    console = create_agent_console(a)
    status = execute_agent_command(console, a, "/status")
    assert info["conversation_id"] in status and info["checkpoint_db"] in status
    rules = execute_agent_command(console, a, "/permissions")
    assert "修改文件" in rules and "file.txt" in rules and "当前对话" in rules
    assert "original" not in rules and "provider_call_id" not in rules and "arguments" not in rules
    help_text = console.get_help_text()
    assert "/approve" not in help_text and "/status" in help_text
    assert "/approve" in console.get_help_text("advanced")


def test_pending_proposal_does_not_report_execution_started(agents):
    edit = call("write_file", {"path": "file.txt", "content": "new"})
    a, p = agents([{"tool_calls": [edit]}, {}], pause_after=None)
    configure(a)
    events = []
    a.set_ui_event_handler(lambda event, data: events.append(event))
    a.chat("edit")
    assert "tool_start" in events and "tool_execution_started" not in events
    approval = a.get_pending_approval(a._run_state.run_id)
    a.decide_approval(approval.decision.operation.run_id, approval.approval_id, approved=True, actor="local")
    a.resume_checkpoint(approval.decision.operation.run_id)
    assert events.count("tool_execution_started") == 1 and events.count("tool_result") == 1
    assert events.index("tool_start") < events.index("tool_execution_started") < events.index("tool_result")
    assert len(p.requests) == 2


def test_shell_status_shows_command_before_handler_without_appending_logs(agents, monkeypatch):
    monkeypatch.setenv("COLUMNS", "40")
    command = "echo safe"
    shell_call = call("bash", {"command": command}, "shell-call")
    a, provider = agents([{"tool_calls": [shell_call]}, {"content": "done"}], pause_after=None)
    configure(a)
    output = StringIO()
    ui = TerminalUI(output)
    closed_status = []
    def present(event, data):
        ui.handle_event(event, data)
        if event == "tool_result":
            closed_status.append(ui._progress is None)
    a.set_ui_event_handler(present)
    invoked = []

    def handler(command):
        display = output.getvalue()
        assert ui._progress is not None
        assert "正在执行: bash" in str(ui._progress.status)
        assert command in str(ui._progress.status)
        assert "执行完成" not in display and "[开始执行]" not in display
        invoked.append(command)
        return "ok"

    a.tool_registry.get_spec("bash").handler = handler
    a.chat("execute")
    assert invoked == [] and ui._progress is None
    pending = a.get_pending_approval(a._run_state.run_id)
    a.decide_approval(a._run_state.run_id, pending.approval_id, approved=True, actor="local")
    assert a.resume_checkpoint(a._run_state.run_id) == "done"
    assert invoked == [command]
    assert closed_status == [True]
    # The following model request has its own status until the caller presents
    # the returned answer, just as the actual REPL does.
    ui.print_answer("done")
    assert ui._progress is None
    assert output.getvalue().count("执行完成: bash") == 1
    assert "[开始执行]" not in output.getvalue()
    assert len(provider.requests) == 2


def test_shell_start_display_redacts_without_changing_operation():
    ui = TerminalUI(StringIO())
    command = "echo api_key=sk-private-value\necho visible"
    ui.handle_event("tool_execution_started", {"name": "bash", "command": command, "cwd": "C:/workspace", "summary": "stale"})
    assert "sk-private-value" not in str(ui._progress.status)
    assert "visible" in str(ui._progress.status)
    assert ui.stream.getvalue() == ""
    assert command == "echo api_key=sk-private-value\necho visible"


def test_inspecting_status_while_paused_does_not_reopen_menu_or_request_model(agents, monkeypatch):
    from cli import entrypoint
    from test_hitl_approval_menu import MenuUI
    from test_run_snapshot import calls
    a, p = agents([{"tool_calls": calls("A", "B", "C")}], pause_after=None)
    configure(a)
    class UI(MenuUI):
        _BLUE = _DIM = ""
        prompt_toolkit_available = False
        def _paint(self, text, color): return text
        def read_input(self): return next(self.inputs)
        def print_control(self, text): self.answers.append(text)
    ui = UI(["p"])
    ui.inputs = iter(["task", "/status", "/permissions", "exit"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(entrypoint, "TerminalUI", lambda: ui)
    entrypoint._run_repl(a)
    assert len(ui.operations) == 1 and a.executions == ["A"] and len(p.requests) == 1
    assert a._run_state.status.value == "waiting_approval"
    assert [m.tool_call_id for m in a.messages if m.role == "tool"] == ["call-0"]


def test_completed_history_status_follows_selected_conversation(agents):
    a, p = agents([{}, {}], pause_after=None)
    configure(a)
    a.chat("first")
    first = a.get_session_info()
    a.start_new_conversation()
    a.chat("second")
    assert a.get_session_info()["run_id"] != first["run_id"]
    a.open_conversation(first["conversation_id"])
    info = a.get_session_info()
    assert info["conversation_id"] == first["conversation_id"]
    assert info["run_id"] == first["run_id"] and info["status"] == "completed"
    assert len(p.requests) == 2


def test_delegated_edit_implementation_is_in_runtime_compatibility_binding(agents, monkeypatch):
    a, _ = agents([], pause_after=None)
    before = digest(a._snapshot_compatibility())
    original = Path.read_text
    def changed(path, *args, **kwargs):
        contents = original(path, *args, **kwargs)
        return contents + "\n# simulated implementation change" if path.name == "file_edits.py" else contents
    monkeypatch.setattr(Path, "read_text", changed)
    assert digest(a._snapshot_compatibility()) != before


def test_corrupt_persisted_rule_and_database_failure_fail_closed(agents, monkeypatch):
    a, _, approval = waiting_file(agents)
    remember(a, approval)
    op = approval.decision.operation
    rows = a._checkpoint_store.list_permission_grants(op.workspace)
    row = dict(rows[0], operation_json="{bad json")
    monkeypatch.setattr(a._checkpoint_store, "list_permission_grants", lambda _: [row])
    with pytest.raises(ValueError):
        a._permission_grants.allows(op)
    def fail(_): raise OSError("database unavailable")
    monkeypatch.setattr(a._checkpoint_store, "list_permission_grants", fail)
    with pytest.raises(OSError):
        a._permission_grants.allows(op)


def test_result_rendering_separates_unknown_skipped_and_completed():
    output = StringIO()
    ui = TerminalUI(output)
    ui.handle_event("model_request_started", {"iteration": 3})
    ui.handle_event("tool_start", {"summary": "proposed file"})
    assert output.getvalue() == ""
    ui.handle_event("tool_result", {"name": "edit_file", "outcome": "UNKNOWN", "success": False})
    ui.handle_event("tool_result", {"name": "edit_file", "outcome": "SKIPPED_DUPLICATE", "success": False})
    ui.handle_event("tool_result", {"name": "read_file", "success": True})
    text = output.getvalue()
    assert text.count("proposed file") == 1
    assert "结果不确定" in text and "跳过重复调用" in text and "执行完成" in text
    assert "请求模型" not in text and "执行中" not in text


@pytest.mark.parametrize("kind,expected", [("duplicate", "SKIPPED_DUPLICATE"),
                                           ("cancel", "CANCELLED"), ("interrupt", "UNKNOWN")])
def test_real_loop_reports_execution_classification_to_terminal(make_agent, kind, expected):
    from core.tools.base_tools import ToolResult
    second = _call("second") if kind == "duplicate" else _call("second", '{"patterns":["different"]}')
    a, p = make_agent([_response(tool_calls=[_call("first"), second]), _response(content="done")])
    executions, events = [], []
    output = StringIO()
    ui = TerminalUI(output)
    def observe(event, data):
        events.append((event, data))
        ui.handle_event(event, data)
    # The closure fixture intentionally suppresses presentation. Restore the
    # production callback path so this test exercises Runtime -> UI delivery.
    a._emit_ui_event = type(a)._emit_ui_event.__get__(a)
    a.set_ui_event_handler(observe)
    def execute(_name, _args):
        executions.append("handler")
        if kind == "interrupt":
            raise KeyboardInterrupt()
        return ToolResult("cancelled", success=False, cancelled=True) if kind == "cancel" else ToolResult("actual result")
    a._execute_tool = execute
    a._llm_tool_cycle()
    results = [data for event, data in events if event == "tool_result"]
    classified = [data for data in results if data.get("outcome") == expected]
    assert len(classified) == 1
    target_id = "second" if kind == "duplicate" else "first"
    assert classified[0]["call_id"] == target_id
    assert a._run_state.tool_batch.results[target_id].outcome == expected
    assert executions == ["handler"]
    assert len(p.requests) == (2 if kind == "duplicate" else 1)
    text = output.getvalue()
    assert {"duplicate": "跳过重复调用", "cancel": "执行取消", "interrupt": "结果不确定"}[kind] in text
