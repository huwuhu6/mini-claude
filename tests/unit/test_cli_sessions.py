from copy import deepcopy
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_run_snapshot import agents, calls
from test_hitl_runtime import configure
from cli.entrypoint import parse_args, _select_session
from cli.ui import TerminalUI


def test_normal_entrypoints_default_to_approvals():
    assert parse_args([]).hitl and parse_args([]).path == '.'
    assert parse_args(['--continue']).continue_session
    assert parse_args(['--resume']).resume == ''
    assert parse_args(['--resume','id']).resume == 'id'
    assert not parse_args(['--no-hitl']).hitl
    with pytest.raises(SystemExit): parse_args(['--continue','--resume'])
    with pytest.raises(SystemExit): parse_args(['--no-hitl','--continue'])


def test_completed_history_load_does_not_dispatch_and_new_chat_keeps_context(agents):
    a,p=agents([{'content':'remember blue' }],pause_after=None)
    configure(a)
    a.chat('my project colour is blue')
    run=a._run_state.run_id
    before=deepcopy(a.messages)
    a.agent_note.replace('saved project note')
    b,q=agents([{'content':'blue' }],pause_after=None)
    configure(b,a._checkpoint_store.path)
    # Workspace can evolve after a completed task; this is context, not authorization.
    (b.workdir/'new.txt').write_text('external edit',encoding='utf-8')
    result=b.open_checkpoint_conversation(run)
    assert 'COMPLETED' in result and b.messages==before
    assert b.executions==[] and q.requests==[] and b._run_state is None
    b.chat('what colour did I mention?')
    assert any(m.content=='my project colour is blue' for m in q.requests[0])
    assert b._run_state.run_id!=run and len(q.requests)==1
    assert b._checkpoint_store.load(run)['status']=='completed'


def test_pending_history_goes_through_approval_recovery(agents):
    a,p=agents([{'tool_calls':calls('A','B','C')}],pause_after=None)
    configure(a)
    a.chat('task')
    run=a._run_state.run_id
    b,q=agents([],pause_after=None)
    configure(b,a._checkpoint_store.path)
    b.open_checkpoint_conversation(run)
    assert b.has_active_run and b._run_state.status.value=='waiting_approval'
    assert b._run_state.tool_batch.next_call_index==1
    assert b.executions==[] and q.requests==[]


def test_completed_load_rejects_other_workspace_and_active_run(agents):
    a,p=agents([{'content':'done'}],pause_after=None)
    configure(a)
    a.chat('first')
    run=a._run_state.run_id
    other=a.workdir.parent/'other-workspace'
    other.mkdir()
    b,q=agents([],pause_after=None,root=other)
    configure(b,a._checkpoint_store.path)
    with pytest.raises(ValueError,match='Workspace'): b.open_checkpoint_conversation(run)
    assert q.requests==[] and not b._checkpoint_store.holds_lock(run)
    c,r=agents([{'tool_calls':calls('A','B')}],pause_after=None)
    configure(c,a._checkpoint_store.path)
    c.chat('active')
    before=deepcopy(c.messages)
    with pytest.raises(Exception,match='活动 Run'): c.open_checkpoint_conversation(run)
    assert c.messages==before


def test_multiple_runs_share_one_conversation(agents):
    a,p=agents([{'content':'first'},{'content':'second'}],pause_after=None)
    configure(a)
    a.chat('first'); first=a._run_state.run_id
    a.chat('second'); second=a._run_state.run_id
    assert a._checkpoint_store.list_run_ids()[:2]==[second,first]
    rows = a.list_conversations()
    assert len(rows) == 1 and rows[0]['head_run_id'] == second
    assert _select_session(a,latest=True)==rows[0]['conversation_id']


def test_session_picker_binds_index_and_cancel(monkeypatch):
    ui=TerminalUI(StringIO())
    rows=[{'conversation_id':'first','status':'completed','title':'answer','updated_at':1,'opened_at':1},
          {'conversation_id':'second','status':'waiting_approval','title':'pending','updated_at':2,'opened_at':2}]
    choices=iter(['oops','9','2'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(choices))
    assert ui.choose_session(rows)=='second'
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    assert ui.choose_session(rows) is None


def test_latest_only_chooses_current_workspace(tmp_path):
    # Runtime owns workspace filtering, so the CLI never broadens the list.
    agent=SimpleNamespace(workdir=tmp_path,list_conversations=lambda:[
        {'conversation_id':'right','workspace':str(tmp_path)}])
    assert _select_session(agent,latest=True)=='right'
    agent.list_conversations=lambda:[]
    with pytest.raises(RuntimeError,match='没有历史'): _select_session(agent,latest=True)


def test_default_start_initializes_store_without_init_flag(agents,tmp_path,monkeypatch):
    from cli import entrypoint
    a,p=agents([],pause_after=None)
    db=tmp_path/'data'/'checkpoints.sqlite3'
    monkeypatch.setattr(entrypoint.RuntimeDataPaths,'for_workspace',lambda root:SimpleNamespace(checkpoints=db))
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:a)
    seen=[]
    monkeypatch.setattr(entrypoint,'_run_repl',lambda agent,selected:seen.append((agent._hitl_enabled,selected)))
    entrypoint.main([str(a.workdir),'--yes'])
    assert db.is_file() and seen==[(True,None)]
    assert p.requests==[] and a.executions==[]


def test_continue_missing_database_never_creates_it(tmp_path,monkeypatch):
    from cli import entrypoint
    db=tmp_path/'absent'/'checkpoints.sqlite3'
    monkeypatch.setattr(entrypoint.RuntimeDataPaths,'for_workspace',lambda root:SimpleNamespace(checkpoints=db))
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:pytest.fail('must not start runtime'))
    with pytest.raises(SystemExit) as caught:
        entrypoint.main([str(tmp_path),'--yes','--continue'])
    assert caught.value.code==1 and not db.parent.exists()


def test_default_corrupt_database_does_not_downgrade(agents,tmp_path,monkeypatch):
    from cli import entrypoint
    a,p=agents([],pause_after=None)
    db=tmp_path/'bad.sqlite3'
    db.write_bytes(b'invalid database')
    monkeypatch.setattr(entrypoint.RuntimeDataPaths,'for_workspace',lambda root:SimpleNamespace(checkpoints=db))
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:a)
    with pytest.raises(SystemExit): entrypoint.main([str(a.workdir),'--yes'])
    assert db.read_bytes()==b'invalid database' and p.requests==[]


def test_cli_continue_loads_latest_completed_history_on_restart(agents,monkeypatch):
    from cli import entrypoint
    a,p=agents([{'content':'old answer'}],pause_after=None)
    configure(a); a.chat('old question')
    run=a._run_state.run_id
    database=a._checkpoint_store.path
    a.shutdown()
    b,q=agents([],pause_after=None)
    seen=[]
    monkeypatch.setattr(entrypoint.RuntimeDataPaths,'for_workspace',lambda root:SimpleNamespace(checkpoints=database))
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:b)
    # Real REPL routing, deterministic terminal input; no real provider call.
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    monkeypatch.setattr('builtins.input',lambda prompt:'exit')
    entrypoint.main([str(b.workdir),'--yes','--continue'])
    assert any(m.content=='old question' for m in b.messages)
    assert q.requests==[] and b.executions==[]


def test_cli_resume_picker_cancel_does_not_start_chat(agents,monkeypatch):
    from cli import entrypoint
    a,p=agents([{'content':'old answer'}],pause_after=None)
    configure(a); a.chat('old question')
    database=a._checkpoint_store.path
    a.shutdown()
    b,q=agents([],pause_after=None)
    monkeypatch.setattr(entrypoint.RuntimeDataPaths,'for_workspace',lambda root:SimpleNamespace(checkpoints=database))
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:b)
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    monkeypatch.setattr(entrypoint,'_run_repl',lambda *args:pytest.fail('selection cancelled'))
    entrypoint.main([str(b.workdir),'--yes','--resume'])
    assert b.messages==[] and q.requests==[]
