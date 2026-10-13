"""Conversation identity, rule scope and archival without paid model calls."""
from copy import deepcopy
import json
import sqlite3

import pytest

from test_run_snapshot import agents, calls
from test_hitl_runtime import configure
from test_hitl_cli import waiting_cli, identity, closed
from test_hitl_approval_menu import MenuUI
from cli.entrypoint import _present_response, _select_session
from cli.commands import create_agent_console, execute_agent_command
from core.checkpoint_store import CheckpointError, SqliteCheckpointStore
from core.permissions import PermissionKind
from test_permissions import bound


@pytest.mark.parametrize("entry", ["conversation", "checkpoint"])
def test_cancelled_unknown_loads_reference_history_without_replay(agents, entry):
    from core.tools.base_tools import ToolResult
    a, provider = agents([{"tool_calls": calls("A", "C")}], pause_after=None)
    def cancel(label):
        a.executions.append(label)
        return ToolResult("interrupted", success=False, cancelled=True)
    a.tool_registry.get_spec("probe").handler = cancel
    configure(a)
    a.chat("original request")
    run = a._run_state.run_id
    cid = a._conversation_id
    store = a._checkpoint_store
    original = store.load(run)
    assert original["status"] == "cancelled"
    assert [row["state"] for row in original["journal"]] == ["UNKNOWN", "NOT_EXECUTED"]
    b, mock = agents([], pause_after=None)
    configure(b, store.path)
    cwd = b.runtime_context.cwd
    for _ in range(2):
        response = b.open_conversation(cid) if entry == "conversation" else b.resume_checkpoint(run)
        assert "只读历史" in response and "UNKNOWN" in response and "禁止" in response
        assert b.messages == a.messages and b.get_conversation_preview()
        assert b._run_state is None and b.runtime_context.cwd == cwd
        assert b.get_session_info()["execution_blocked"]
        assert not b._checkpoint_store._locks
    assert mock.requests == [] and b.executions == [] and len(provider.requests) == 1
    assert store.load(run) == original  # No reclassification, revision change or journal rewrite.
    before = deepcopy(b.messages)
    assert "UNKNOWN" in b.chat("do not overwrite")
    assert b.messages == before and not mock.requests
    console = create_agent_console(b)
    status = execute_agent_command(console, b, "/status")
    assert "UNKNOWN" in status and "只读" in status and f"消息数: {len(before)}" in status
    for command in ("/new", "/clear", "/compact"):
        assert "未完成" in execute_agent_command(console, b, command)
        assert b.messages == before


@pytest.mark.parametrize("tty", [True, False])
def test_readonly_restore_notice_is_control_output_not_model_answer(monkeypatch, tty):
    from types import SimpleNamespace
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    class UI:
        def __init__(self): self.controls = []
        def print_control(self, text): self.controls.append(text)
        def print_answer(self, text): pytest.fail("readonly restore is not a model answer")
    ui = UI()
    response = "已加载会话只读历史（CANCELLED，含 UNKNOWN）；禁止重放。"
    assert not _present_response(SimpleNamespace(_hitl_enabled=True), ui, response)
    assert ui.controls == [response]


def test_unknown_details_show_original_operation_only_for_inspection(agents):
    from core.tools.base_tools import ToolResult
    from cli.hitl_commands import describe_run
    a, provider = agents([{"tool_calls": calls("A")}], pause_after=None)
    def cancel(label):
        return ToolResult("cancelled", success=False, cancelled=True)
    a.tool_registry.get_spec("probe").handler = cancel
    configure(a)
    a.chat("task")
    original = a._checkpoint_store.load(a._run_state.run_id)
    text = describe_run(a, a._run_state.run_id)
    assert "不确定调用: probe" in text and '"label": "A"' in text
    assert "仅供核验，不执行" in text
    assert a._checkpoint_store.load(a._run_state.run_id) == original
    assert len(provider.requests) == 1


def test_unknown_badge_survives_narrow_status_bar(monkeypatch):
    from io import StringIO
    from cli.ui import TerminalUI
    monkeypatch.setenv("COLUMNS", "30")
    ui = TerminalUI(StringIO())
    ui._session_info = {"workspace": "long-workspace-name", "status": "cancelled", "execution_blocked": True}
    assert ui._status_line().startswith("只读 UNKNOWN")


def test_history_inspection_tolerates_original_invalid_argument_json(agents):
    from core.tools.base_tools import ToolResult
    malformed, cancelled = calls("A", "A")
    malformed["function"]["arguments"] = "{invalid-json"
    a, provider = agents([{"tool_calls": [malformed, cancelled]}], pause_after=None)
    def cancel(label):
        return ToolResult("cancelled", success=False, cancelled=True)
    a.tool_registry.get_spec("probe").handler = cancel
    configure(a)
    a.chat("task")
    info = a.get_checkpoint_info(a._run_state.run_id)
    assert info["calls"][0]["arguments"] == "{invalid-json"
    assert info["calls"][1]["state"] == "UNKNOWN"
    assert len(provider.requests) == 1


def test_restart_conversation_preserves_all_turns_and_one_list_entry(agents):
    a,p=agents([{'content':'blue'}, {'content':'green'}],pause_after=None)
    configure(a)
    a.chat('first'); first=a._run_state.run_id
    conversation=a.list_conversations()[0]['conversation_id']
    a.chat('second'); second=a._run_state.run_id
    history=deepcopy(a.messages)
    b,q=agents([{'content':'third answer'}],pause_after=None)
    configure(b,a._checkpoint_store.path)
    b.open_conversation(conversation)
    assert b.messages==history and q.requests==[] and b.executions==[]
    b.chat('third')
    assert all(any(m.content==text for m in q.requests[0]) for text in ['first','second'])
    assert len(b.list_conversations())==1
    assert b._checkpoint_store.conversation_for_run(first)==conversation
    assert b._checkpoint_store.conversation_for_run(second)==conversation
    assert b.list_conversations()[0]['head_run_id']==b._run_state.run_id


def test_recent_is_last_opened_and_new_conversation_has_fresh_history(agents):
    a,p=agents([{},{}],pause_after=None)
    configure(a); a.chat('older')
    old=a.list_conversations()[0]['conversation_id']
    a.start_new_conversation(); a.chat('newer')
    assert len(a.list_conversations())==2 and _select_session(a,latest=True)!=old
    a.open_conversation(old)
    assert _select_session(a,latest=True)==old
    assert all(m.content!='newer' for m in a.messages)


def test_stale_conversation_reader_cannot_overwrite_newer_run(agents):
    a,p=agents([{},{}],pause_after=None)
    configure(a); a.chat('first')
    cid=a.list_conversations()[0]['conversation_id']
    b,q=agents([{}],pause_after=None)
    configure(b,a._checkpoint_store.path); b.open_conversation(cid)
    a.chat('second')
    with pytest.raises(CheckpointError,match='其他进程推进'):
        b.chat('stale third')
    assert q.requests==[] and b.executions==[]
    assert not b._checkpoint_store.holds_lock('conversation:'+cid)


def test_pause_prevents_new_run_from_other_instance_and_releases_conversation_lock(agents):
    a,p,c,_=waiting_cli(agents)
    cid=a.list_conversations()[0]['conversation_id']
    assert not a._checkpoint_store.holds_lock('conversation:'+cid)
    b,q=agents([],pause_after=None)
    configure(b,a._checkpoint_store.path); b.open_conversation(cid)
    before=deepcopy(b.messages)
    assert '未完成' in b.chat('do not replace')
    assert b.messages==before and q.requests==[]


def test_runtime_capability_filter_and_gate_agree(agents):
    a,p=agents([],pause_after=None)
    a.enable_checkpoints(); a.enable_hitl()
    names={tool['name'] for tool in a._get_llm_tools()}
    assert 'run_background' not in names and 'task' not in names and 'bash' in names
    for name,args in [('run_background',{'command':'echo safe'}),('task',{'prompt':'x'})]:
        policy,op=bound(a,name,args)
        assert policy.evaluate(op).kind is PermissionKind.DENY
    assert a.executions==[] and p.requests==[]


@pytest.mark.parametrize('command',['echo safe && echo unsafe','echo $TOKEN','echo %TOKEN%',
    'echo !TOKEN!','echo safe > out.txt','powershell -Command "echo safe"','python -m pytest'])
def test_query_allowlist_does_not_accept_shell_expansion_or_wrappers(agents,command):
    a,p=agents([],pause_after=None)
    policy,op=bound(a,'bash',{'command':command})
    assert policy.evaluate(op).kind is not PermissionKind.ALLOW
    assert p.requests==[] and a.executions==[]


def test_internal_handler_replacement_is_not_auto_allowed(agents):
    a,p=agents([],pause_after=None)
    def replacement(items):
        raise AssertionError('must never run')
    a.tool_registry.get_spec('TodoWrite').handler=replacement
    policy,op=bound(a,'TodoWrite',{'items':[]})
    assert policy.evaluate(op).kind is PermissionKind.ASK


@pytest.mark.parametrize('scope',['process','workspace'])
def test_exact_grant_not_other_arguments_and_can_be_revoked(agents,scope):
    a,p,c,_=waiting_cli(agents)
    run,aid=identity(a)
    op=a.get_pending_approval(run).decision.operation
    a.decide_approval(run,aid,approved=True,actor='tester')
    a.remember_approval(run,aid,scope)
    assert a._permission_grants.allows(op)
    changed=dict(op.call); changed['function']=dict(changed['function'],arguments=json.dumps({'label':'different'}))
    other=a._permission_policy.bind(run,op.turn,op.ordinal,changed,
        workspace_digest=a.workspace_state_guard.content_digest(), runtime_digest=json.loads(op.context_json)['runtime_digest'])
    assert not a._permission_grants.allows(other)
    a.resume_checkpoint(run)
    assert a.executions==['A','B','C'] and len(p.requests)==2
    b,q=agents([],pause_after=None); configure(b,a._checkpoint_store.path)
    assert b._permission_grants.allows(op)==(scope=='workspace')
    rules=a.list_permissions()
    assert len(rules)==1 and a.revoke_permission(rules[0]['grant_id'])
    assert not a._permission_grants.allows(op)


def test_grant_never_bypasses_disabled_feature(agents):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    a.decide_approval(run,aid,approved=True,actor='tester'); a.remember_approval(run,aid,'workspace')
    a.feature_manager.disable('probe_feature')
    with pytest.raises(CheckpointError): a.resume_checkpoint(run)
    assert a.executions==['A'] and len(p.requests)==1


def test_rejection_feedback_survives_restart_and_precedes_next_model_request(agents):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    feedback={'mode':'replan','note':'Use another implementation; do not modify file.txt'}
    a.decide_approval(run,aid,approved=False,actor='tester',feedback=feedback)
    b,q=agents([{}],pause_after=None); configure(b,a._checkpoint_store.path)
    b.resume_checkpoint(run)
    assert b.executions==[] and len(q.requests)==1
    closed(b)
    messages=q.requests[0]
    last=max(i for i,m in enumerate(messages) if m.role=='tool')
    assert any(m.role=='user' and feedback['note'] in m.content for m in messages[last+1:])


def test_menu_instead_of_replans_without_dispatching_denied_suffix(agents,monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    a,p,c,out=waiting_cli(agents)
    class UI(MenuUI):
        def read_approval_feedback(self): return 'Choose a read-only alternative'
    _present_response(a,UI(['5']),out)
    assert a.executions==['A'] and len(p.requests)==2
    closed(a)
    assert any('read-only alternative' in m.content for m in p.requests[-1])


@pytest.mark.parametrize('kind',['pending','invalidated','unknown'])
def test_archive_preserves_execution_facts_and_prevents_old_resume(agents,kind):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    store=a._checkpoint_store
    if kind=='invalidated':
        store.acquire(run)
        try: a._approval_store.invalidate(aid,'context changed')
        finally: store.release(run)
    if kind=='unknown':
        a.decide_approval(run,aid,approved=True,actor='tester')
        store.acquire(run)
        try:
            record=store.load(run)
            decision=a._permission_policy.evaluate(a.get_pending_approval(run).decision.operation)
            a._approval_store.consume_and_mark_running(aid,decision,record['revision'])
            store.recover(run)
        finally: store.release(run)
    before=store.load(run)
    a.archive_checkpoint(run,'Manually inspected; retain uncertainty')
    after=store.load(run)
    assert after['journal']==before['journal'] and after['status']==before['status']
    assert after['snapshot']==before['snapshot'] and not a.has_active_run
    assert a.list_conversations()==[] and len(a.list_conversations(archived=True))==1
    with pytest.raises(CheckpointError,match='归档'): a.resume_checkpoint(run)
    with pytest.raises(CheckpointError,match='归档'): a.decide_approval(run,aid,approved=True,actor='tester')
    p.responses=[{}]; a.chat('fresh plan')
    assert a.executions==['A'] and a._run_state.run_id!=run


def test_v3_migration_preserves_runs_and_journal(agents):
    a,p=agents([{}],pause_after=None); configure(a); a.chat('done')
    store=a._checkpoint_store; run=a._run_state.run_id; before=store.load(run)
    path=store.path; store.close()
    with sqlite3.connect(path) as db:
        for table in ('approval_feedback','permission_grants','run_archives','conversation_runs','conversations'):
            db.execute('DROP TABLE '+table)
        db.execute('PRAGMA user_version=3')
    migrated=SqliteCheckpointStore(path)
    assert migrated.load(run)==before and migrated.list_conversations(str(a.workdir))==[]
    migrated.close()


def test_unknown_selected_conversation_cannot_be_replaced_before_archive(agents,monkeypatch):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    a.decide_approval(run,aid,approved=True,actor='tester')
    store=a._checkpoint_store; store.acquire(run)
    try:
        decision=a._permission_policy.evaluate(a.get_pending_approval(run).decision.operation)
        a._approval_store.consume_and_mark_running(aid,decision,store.load(run)['revision'])
        store.recover(run)
    finally: store.release(run)
    b,q=agents([],pause_after=None); configure(b,store.path)
    cid=a.list_conversations()[0]['conversation_id']
    with pytest.raises(CheckpointError,match='UNKNOWN'): b.open_conversation(cid)
    before=deepcopy(b.messages)
    assert 'UNKNOWN' in b.chat('overwrite')
    assert b.messages==before and b._run_state is None and not q.requests
    assert not b._checkpoint_store._locks
    console=create_agent_console(b)
    for command in ['/new','/clear','/compact']:
        assert '未完成' in execute_agent_command(console,b,command)
        assert b.messages==before and b._conversation_id==cid
    from cli.entrypoint import _run_repl
    inputs=iter(['/archive Manually inspected; no retry','exit'])
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    monkeypatch.setattr('builtins.input',lambda prompt:next(inputs))
    _run_repl(b,cid)
    assert b._checkpoint_store.archive_info(run) and not b._checkpoint_store._locks
    assert not q.requests and b.executions==[] and b._conversation_id is None


def test_corrupt_persistent_grant_does_not_authorize_operation(agents):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    op=a.get_pending_approval(run).decision.operation
    a.decide_approval(run,aid,approved=True,actor='tester'); a.remember_approval(run,aid,'workspace')
    changed=op.to_dict(); call=op.call
    call['function']['arguments']=json.dumps({'label':'different'})
    from core.permissions import canonical
    changed['call_json']=canonical(call)
    with sqlite3.connect(a._checkpoint_store.path) as db:
        db.execute('UPDATE permission_grants SET operation_json=?',(canonical(changed),))
    assert not a._permission_grants.allows(op)
    assert a.executions==['A']


@pytest.mark.parametrize('platform',['win32','linux'])
def test_portable_data_root_uses_user_location_and_case_rules(tmp_path,monkeypatch,platform):
    from core.runtime_data import RuntimeDataPaths
    monkeypatch.setattr('core.runtime_data.sys.platform',platform)
    monkeypatch.delenv('MINI_CLAUDE_DATA_DIR',raising=False)
    key='LOCALAPPDATA' if platform=='win32' else 'XDG_DATA_HOME'
    monkeypatch.setenv(key,str(tmp_path/'userdata'))
    one=RuntimeDataPaths.for_workspace(tmp_path/'Repo')
    two=RuntimeDataPaths.for_workspace(tmp_path/'repo')
    assert one.root.parent==tmp_path/'userdata'/'mini-claude'
    assert (one.root.name.rsplit('-',1)[1]==two.root.name.rsplit('-',1)[1])==(platform=='win32')


@pytest.mark.parametrize('scope',['process','workspace'])
def test_remembered_operation_executes_new_call_without_reusing_approval(agents,scope):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    a.decide_approval(run,aid,approved=True,actor='tester'); a.remember_approval(run,aid,scope)
    a.resume_checkpoint(run)
    old=a._approval_store.get(aid)
    fresh=calls('B'); fresh[0]['id']='new-call-id'
    p.responses=[{'tool_calls':fresh},{}]
    a.chat('repeat same operation')
    assert a.executions==['A','B','C','B'] and len(p.requests)==4
    assert a._approval_store.get(aid)==old and old.state.value=='CONSUMED'
    assert not a._approval_store.list_for_run(a._run_state.run_id)
    assert a._checkpoint_store.load(a._run_state.run_id)['journal'][0]['state']=='COMPLETED'
    assert sum(m.tool_call_id=='new-call-id' for m in a.messages)==1


def test_rule_database_failure_stops_before_dispatch(agents,monkeypatch):
    a,p,c,_=waiting_cli(agents); run,aid=identity(a)
    a.decide_approval(run,aid,approved=True,actor='tester'); a.resume_checkpoint(run)
    p.responses=[{'tool_calls':calls('B')}]
    def fail(*args): raise OSError('rule database unavailable')
    monkeypatch.setattr(a._checkpoint_store,'list_permission_grants',fail)
    with pytest.raises(OSError): a.chat('another operation')
    assert a.executions==['A','B','C'] and a._checkpoint_faulted
    assert not a._checkpoint_store._locks


def test_old_policy_record_is_readable_but_cannot_authorize_current_policy(agents):
    from dataclasses import replace
    from core.permissions import canonical, ToolOperation
    a,p=agents([],pause_after=None)
    policy,op=bound(a,'write_file',{'path':'file.txt','content':'x'})
    context=json.loads(op.context_json); context['policy_version']='main-conservative-v1'
    old=replace(op,context_json=canonical(context))
    assert ToolOperation.from_dict(old.to_dict())==old
    assert policy.evaluate(old).kind is PermissionKind.DENY


def test_archive_command_targets_selected_history_not_previous_terminal_run(agents):
    a,p=agents([{},{}],pause_after=None); configure(a)
    a.chat('first'); first=a._run_state.run_id
    cid=a.list_conversations()[0]['conversation_id']
    a.start_new_conversation(); a.chat('second'); second=a._run_state.run_id
    a.open_conversation(cid)
    console=create_agent_console(a)
    assert '已归档' in execute_agent_command(console,a,'/archive Keep original facts')
    assert a._checkpoint_store.archive_info(first)
    assert not a._checkpoint_store.archive_info(second)


@pytest.mark.parametrize('command',[None,42,True])
def test_bad_shell_command_type_is_denied_before_runtime_route(agents,monkeypatch,command):
    a,p=agents([],pause_after=None); a.enable_checkpoints(); a.enable_hitl()
    monkeypatch.setattr('core.permissions.sys.platform','win32')
    policy,op=bound(a,'bash',{'command':command})
    decision=policy.evaluate(op)
    assert decision.kind is PermissionKind.DENY and decision.rule_id=='arguments.invalid'
    assert a.executions==[] and p.requests==[]
