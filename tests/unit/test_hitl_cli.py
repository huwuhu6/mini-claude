"""CLI-level HITL contracts, mocks only, including genuinely separate processes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from test_run_snapshot import agents, calls
from test_hitl_runtime import configure, stateful_handler
from cli.commands import create_agent_console, execute_agent_command
from cli.entrypoint import _dispatch_input, main, parse_args
from core.approvals import ApprovalStatus
from core.checkpoint_store import CheckpointError, SqliteCheckpointStore
from core.display_safety import redact, safe_text
from core.run_state import RunStatus


def waiting_cli(agents, *, all_ask=False, stateful=False, database=None):
    a, provider = agents([{'tool_calls': calls('A', 'B', 'C')}, {}], pause_after=None)
    if stateful:
        stateful_handler(a)
    if all_ask:
        a.enable_checkpoints()
        a.enable_hitl()
    else:
        configure(a,database)
    a.session_recorder.redact_display = True
    console = create_agent_console(a)
    output = _dispatch_input(a, console, 'original task')
    return a, provider, console, output


def issue(a, c, command):
    return _dispatch_input(a, c, command)


def identity(a):
    run = a._run_state.run_id
    return run, a.get_pending_approval(run).approval_id


def closed(a):
    tools = [m for m in a.messages if m.role == 'tool']
    assert [m.tool_call_id for m in tools] == ['call-0', 'call-1', 'call-2']
    assert len(set(m.tool_call_id for m in tools)) == 3
    start = next(i for i, m in enumerate(a.messages) if m.tool_calls)
    assert [m.role for m in a.messages[start:start+4]] == ['assistant', 'tool', 'tool', 'tool']


def test_ask_display_and_unapproved_resume_preserve_prefix(agents):
    a, p, c, output = waiting_cli(agents)
    run, aid = identity(a)
    assert all(value in output for value in ['WAITING_APPROVAL', run, aid, 'probe', 'call-1', '"label": "B"', str(a.workdir), '审批原因', '/resume'])
    original = deepcopy(a.messages)
    assert 'PENDING' in issue(a, c, '/resume ' + run)
    assert a.messages == original and a.executions == ['A'] and len(p.requests) == 1
    assert a._run_state.tool_batch.next_call_index == 1
    assert not a._checkpoint_store.holds_lock(run)
    assert run in issue(a, c, '/runs')


def test_approve_idempotence_explicit_resume_and_complete_repeat(agents):
    a, p, c, _ = waiting_cli(agents, stateful=True)
    run, aid = identity(a)
    todo, note, history = deepcopy(a.todo.items), a.agent_note.read(), deepcopy(a.attempt_history.export_state())
    trace = a._run_state.trace_task_id
    for _ in range(2):
        assert 'APPROVED' in issue(a, c, f'/approve {run} {aid}')
        assert a.executions == ['A'] and len(p.requests) == 1
        assert a.attempt_history.export_state() == history and a.todo.items == todo
    assert 'COMPLETED' in issue(a, c, '/resume ' + run)
    assert a.executions == ['A', 'B', 'C'] and len(p.requests) == 2
    assert a._run_state.model_turns_used == 2 and a.todo.items == todo and a.agent_note.read() == note
    assert a._run_state.trace_task_id and trace
    closed(a)
    assert 'CONSUMED' in issue(a, c, '/approval ' + run)
    assert '操作失败' in issue(a, c, f'/approve {run} {aid}')
    assert 'COMPLETED' in issue(a, c, '/resume ' + run)
    assert a.executions == ['A', 'B', 'C'] and len(p.requests) == 2


def test_reject_idempotence_and_replan_suffix_without_failure(agents):
    a, p, c, _ = waiting_cli(agents)
    run, aid = identity(a)
    attempts = len(a.attempt_history.all())
    for _ in range(2):
        assert 'REJECTED' in issue(a, c, f'/reject {run} {aid}')
        assert a.executions == ['A'] and len(p.requests) == 1
    assert '操作失败' in issue(a, c, f'/approve {run} {aid}')
    assert 'COMPLETED' in issue(a, c, '/resume ' + run)
    assert a.executions == ['A'] and len(p.requests) == 2
    assert len(a.attempt_history.all()) == attempts
    closed(a)
    assert all('NOT_EXECUTED' in m.content for m in a.messages if m.tool_call_id in {'call-1', 'call-2'})
    prompt = p.requests[-1]
    last_tool = max(i for i,m in enumerate(prompt) if m.role == 'tool')
    assert any(m.role == 'user' and '用户拒绝' in m.content for m in prompt[last_tool+1:])


def test_sequential_asks_keep_original_batch_and_do_not_implicitly_resume(agents):
    a, p, c, _ = waiting_cli(agents, all_ask=True)
    run = a._run_state.run_id
    ids = []
    for index in range(3):
        aid = identity(a)[1]
        ids.append(aid)
        assert a._run_state.tool_batch.next_call_index == index
        assert issue(a, c, f'/approve {run} {aid}').startswith('已记录 APPROVED')
        assert a.executions == list(('A','B','C')[:index])
        out = issue(a, c, '/resume ' + run)
        assert a.executions == list(('A','B','C')[:index+1])
        assert len(p.requests) == (2 if index == 2 else 1)
        assert ('COMPLETED' if index == 2 else 'WAITING_APPROVAL') in out
    assert len(set(ids)) == 3
    closed(a)


def test_explicit_ids_cross_run_and_input_guards(agents):
    a,p,c,_ = waiting_cli(agents)
    b,_,_,_ = waiting_cli(agents)
    run,aid = identity(a)
    other,other_aid = identity(b)
    before = deepcopy(a.messages)
    for text in ['/approve', '/reject '+run, f'/approve {run} {other_aid}', f'/approve {other} {aid}']:
        out = issue(a,c,text)
        assert '用法' in out or '操作失败' in out
    for text in ['/clear', '/compact', 'new instruction']:
        assert '尚未完成' in issue(a,c,text)
    assert a.messages == before and a.executions == ['A'] and len(p.requests) == 1
    assert a.get_pending_approval(run).state is ApprovalStatus.PENDING


@pytest.mark.parametrize('change',['feature','workspace','cwd','handler','policy'])
def test_context_change_after_cli_approval_is_fail_closed(agents,change):
    a,_,c,_ = waiting_cli(agents)
    run,aid = identity(a)
    assert 'APPROVED' in issue(a,c,f'/approve {run} {aid}')
    if change == 'feature':
        assert '已disable' in issue(a,c,'/features disable probe_feature')
    elif change == 'workspace':
        (a.workdir/'file.txt').write_text('external change',encoding='utf-8')
    elif change == 'cwd':
        a.workspace_authority.primary_root = a.workdir/'src'
    elif change == 'handler':
        a.tool_registry._tools['probe'].handler = lambda label: pytest.fail('new handler must not run')
    else:
        a.command_policy.DESTRUCTIVE = []
    out = issue(a,c,'/resume '+run)
    assert '操作失败' in out
    assert a.executions == ['A']
    assert a._approval_store.get(aid).state is ApprovalStatus.INVALIDATED
    assert 'INVALIDATED' in issue(a,c,'/approval '+run)
    assert '操作失败' in issue(a,c,f'/approve {run} {aid}')


def test_other_workspace_cannot_record_approval(agents):
    a,_,_,_ = waiting_cli(agents)
    run,aid = identity(a)
    root = a.workdir.parent/'other'
    root.mkdir()
    b,_ = agents([],pause_after=None,root=root)
    configure(b,a._checkpoint_store.path)
    c=create_agent_console(b)
    assert 'Workspace 不一致' in issue(b,c,'/runs')
    assert 'Workspace 与原 Run 不一致' in issue(b,c,f'/approve {run} {aid}')
    assert a.get_pending_approval(run).state is ApprovalStatus.PENDING


def test_expired_and_invalidated_approval_display_and_block(agents):
    a,_,c,_=waiting_cli(agents)
    run,aid=identity(a)
    record=a.get_pending_approval(run)
    # Deterministic expiry fault injection only; CLI itself never writes SQLite.
    with a._checkpoint_store._connection() as conn:
        conn.execute('UPDATE approvals SET expires_at=? WHERE approval_id=?',(record.to_dict()['created_at']+1,aid))
    a._approval_store.clock=lambda:record.to_dict()['created_at']+2
    assert '已到期' in issue(a,c,'/approval '+run)
    assert '操作失败' in issue(a,c,'/resume '+run)
    assert 'INVALIDATED' in issue(a,c,'/approval '+run)
    assert a.executions == ['A']


def test_competing_resume_does_not_dispatch(agents):
    a,_,c,_=waiting_cli(agents)
    run,aid=identity(a)
    issue(a,c,f'/approve {run} {aid}')
    b,p=agents([],pause_after=None)
    configure(b,a._checkpoint_store.path)
    other=create_agent_console(b)
    a._checkpoint_store.acquire(run)
    try:
        assert 'RunBusyError' in issue(b,other,'/resume '+run)
        assert b.executions==[] and p.requests==[]
    finally:
        a._checkpoint_store.release(run)


def test_display_redaction_never_changes_authorized_arguments(agents):
    from core.tools.registry import ToolSpec
    a,p=agents([],pause_after=None)
    raw={'label':'B','api_key':'hidden-key','nested':{'access_token':'hidden-token'},'command':'TOKEN="hidden-env" echo ok --password hidden-password','escape':'\x1b[2J'}
    a.tool_registry._tools['probe'].handler = lambda **kwargs: pytest.fail('must wait')
    p.responses=[{'tool_calls':[{'id':'secret-call','type':'function','function':{'name':'probe','arguments':json.dumps(raw)}}]}]
    configure(a)
    a.session_recorder.redact_display=True
    c=create_agent_console(a)
    out=issue(a,c,'task')
    run,aid=identity(a)
    for secret in ['hidden-key','hidden-token','hidden-env','hidden-password','\x1b']:
        assert secret not in out
    assert a.get_pending_approval(run).decision.operation.arguments==raw
    assert '已脱敏' in out and 'echo ok' in out
    assert issue(a,c,'/approval '+run)==out
    assert c.get_history()==[]
    session=a.session_recorder.path.read_text(encoding='utf-8')
    assert not any(x in session for x in ['hidden-key','hidden-token','hidden-env','hidden-password'])


def test_default_cli_and_model_text_do_not_execute_control_commands(agents):
    a,p=agents([{'content':'/approve fake-run fake-id'}],pause_after=None)
    c=create_agent_console(a)
    assert c.get_command('approve') is None
    assert issue(a,c,'task')=='/approve fake-run fake-id'
    assert a.executions==[] and len(p.requests)==1
    assert 'cli_control' not in a.session_recorder.path.read_text(encoding='utf-8')


@pytest.mark.parametrize('argv',[['--resume','r'],['--checkpoint-db','x'],['--init-checkpoints']])
def test_hitl_flags_are_explicit(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


@pytest.mark.parametrize('initialize,resume',[(False,False),(False,True),(True,True)])
def test_missing_database_fails_before_agent_creation(tmp_path,monkeypatch,initialize,resume):
    from cli import entrypoint
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **k:pytest.fail('must not initialize'))
    args=[str(tmp_path),'--yes','--hitl','--checkpoint-db',str(tmp_path/'outside'/'missing.sqlite3')]
    if initialize:args+=['--init-checkpoints']
    if resume:args+=['--resume','run']
    with pytest.raises(SystemExit) as caught:main(args)
    assert caught.value.code==1
    assert not (tmp_path/'outside').exists()


def test_existing_database_disappearance_never_recreates_it(tmp_path):
    db=tmp_path/'db.sqlite3'
    SqliteCheckpointStore(db).close()
    store=SqliteCheckpointStore(db,require_existing=True)
    db.unlink()
    with pytest.raises(Exception):store.list_run_ids()
    assert not db.exists()
    store.close()


def test_command_database_failure_is_not_a_decision(agents,monkeypatch):
    a,_,c,_=waiting_cli(agents)
    run,aid=identity(a)
    def fail(*args,**kwargs):raise OSError('database fault')
    monkeypatch.setattr(a._checkpoint_store,'_connection',fail)
    assert '操作失败' in issue(a,c,f'/approve {run} {aid}')
    assert a.executions==['A']


def child(base,mode,commands,*,wait=True,startup_run=None):
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[2]/'src'),PYTHONIOENCODING='utf-8',NO_COLOR='1')
    args=[sys.executable,'-B',str(Path(__file__).with_name('hitl_cli_process_probe.py')),str(base),mode]
    if startup_run:
        args.append(startup_run)
    if wait:
        return subprocess.run(args,input=commands,env=env,text=True,encoding='utf-8',capture_output=True,timeout=45)
    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,text=True,encoding='utf-8')
    proc.stdin.write(commands);proc.stdin.flush()
    return proc


def setup_process(tmp_path):
    (tmp_path/'workspace').mkdir()
    (tmp_path/'config.yaml').write_text('features:\n  skills: false\n',encoding='utf-8')
    return tmp_path


def report(proc):
    assert proc.returncode==0,proc.stdout+proc.stderr
    return json.loads(next(line[len('CLI_REPORT='):] for line in proc.stdout.splitlines() if line.startswith('CLI_REPORT=')))


def effects(base):
    return [json.loads(line)['label'] for line in (base/'effects.jsonl').read_text(encoding='utf-8').splitlines()]


def process_start(base):
    proc=child(base,'start','task\n/runs\nexit\n')
    data=report(proc)
    assert data['status']=='waiting_approval' and data['executions']==['A'] and data['requests']==1 and not data['locked']
    run=data['run_id']
    line=next(line for line in proc.stdout.splitlines() if line.startswith('Approval ID:'))
    return run,line.split(': ',1)[1],proc


@pytest.mark.parametrize('decision',['approve','reject'])
def test_real_cli_exit_decide_restart_resume_and_repeat(tmp_path,decision):
    base=setup_process(tmp_path)
    run,aid,start=process_start(base)
    assert '/approve forged-run forged-approval' in start.stdout # model text never became a command
    pending=child(base,'pending',f'/runs\n/approval {run}\n/resume {run}\nnew input\n/compact\n/clear\nexit\n')
    pdata=report(pending)
    assert 'WAITING_APPROVAL' in pending.stdout and '尚未完成' in pending.stdout
    assert pdata['requests']==0 and pdata['executions']==[] and effects(base)==['A']
    decided=child(base,'decide',f'/{decision} {run} {aid}\n/{decision} {run} {aid}\nexit\n')
    assert decision.upper().replace('APPROVE','APPROVED').replace('REJECT','REJECTED') in decided.stdout
    assert report(decided)['requests']==0 and effects(base)==['A']
    resumed=child(base,'resume',f'/approval {run}\n/resume {run}\nexit\n',startup_run=run)
    data=report(resumed)
    assert data['requests']==1 and data['executions']==(['B','C'] if decision=='approve' else [])
    assert effects(base)==(['A','B','C'] if decision=='approve' else ['A'])
    assert [m['id'] for m in data['messages'] if m['role']=='tool']==['call-0','call-1','call-2']
    assert 'COMPLETED' in resumed.stdout
    assert len({report(x)['pid'] for x in [start,pending,decided,resumed]})==4


def test_real_cli_live_executor_exclusion_and_unknown_after_death(tmp_path):
    base=setup_process(tmp_path)
    run,aid,_=process_start(base)
    report(child(base,'approve',f'/approve {run} {aid}\nexit\n'))
    holder=child(base,'hold',f'/resume {run}\n',wait=False)
    try:
        deadline=time.monotonic()+25
        while not (base/'ready').exists() and holder.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert (base/'ready').exists(),'handler not reached'
        contender=child(base,'contender',f'/resume {run}\nexit\n')
        assert 'RunBusyError' in contender.stdout
        assert report(contender)['executions']==[] and effects(base)==['A','B']
        holder.kill();holder.communicate(timeout=10)
        blocked=child(base,'blocked',f'/resume {run}\n/approval {run}\n/approve {run} {aid}\nexit\n')
        assert 'UNKNOWN' in blocked.stdout and '禁止' in blocked.stdout and 'CONSUMED' in blocked.stdout
        assert report(blocked)['requests']==0 and effects(base)==['A','B']
    finally:
        if holder.poll() is None:holder.kill();holder.communicate(timeout=10)


@pytest.mark.parametrize('committed',[False,True])
def test_cli_approval_transaction_fault_requires_query_not_execution(agents,monkeypatch,committed):
    import sqlite3
    a,p,c,_=waiting_cli(agents)
    run,aid=identity(a)
    commit=a._checkpoint_store._commit
    def fail(connection):
        if committed:commit(connection)
        raise sqlite3.OperationalError('decision transaction failure')
    with monkeypatch.context() as patch:
        patch.setattr(a._checkpoint_store,'_commit',fail)
        out=issue(a,c,f'/approve {run} {aid}')
        assert '操作失败' in out and '/approval' in out
    assert a._approval_store.get(aid).state is (ApprovalStatus.APPROVED if committed else ApprovalStatus.PENDING)
    assert a.executions==['A'] and len(p.requests)==1


def test_corrupted_existing_database_fails_closed_on_startup(agents,tmp_path,monkeypatch,capsys):
    from cli import entrypoint
    a,p=agents([],pause_after=None)
    db=tmp_path/'corrupt.sqlite3'
    db.write_bytes(b'not a database')
    monkeypatch.setattr(entrypoint,'MiniClaudeAgent',lambda **kwargs:a)
    with pytest.raises(SystemExit) as caught:
        main([str(a.workdir),'--yes','--hitl','--checkpoint-db',str(db)])
    assert caught.value.code==1 and '未降级' in capsys.readouterr().err
    assert p.requests==[] and a.executions==[] and db.read_bytes()==b'not a database'


@pytest.mark.parametrize('status',['failed','cancelled','completed','unknown'])
def test_terminal_state_display_does_not_offer_retry(agents,monkeypatch,status):
    from cli.hitl_commands import describe_run
    a,p=agents([],pause_after=None)
    info={'run_id':'terminal','status':status,'workspace':str(a.workdir),'cwd':str(a.workdir),
        'result':'stored result','calls':[],'approvals':[],'approval_id':None}
    monkeypatch.setattr(a,'get_checkpoint_info',lambda run:info)
    out=describe_run(a,'terminal')
    assert status.upper() in out
    assert ('禁止' if status=='unknown' else '已终结') in out
    assert '/approve' not in out and p.requests==[] and a.executions==[]


@pytest.mark.parametrize('tool,args',[
    ('TodoWrite',{'items':[{'content':'plan','status':'pending','activeForm':'planning'}]}),
    ('bash',{'command':'echo query'}),('bash',{'command':'python -m pytest -q'}),
    ('bash',{'command':'echo build'}),('update_agent_note',{'content':'remember'})])
def test_actual_conservative_policy_frequency_is_visible(agents,tool,args):
    call={'id':'frequency','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}
    a,p=agents([{'tool_calls':[call]}],pause_after=None)
    a.enable_checkpoints();a.enable_hitl()
    out=issue(a,create_agent_console(a),'task')
    assert 'WAITING_APPROVAL' in out and tool in out
    assert len(p.requests)==1 and a.executions==[]
    assert a.get_pending_approval(a._run_state.run_id).state is ApprovalStatus.PENDING


def test_refusal_replanning_same_operation_requires_a_fresh_decision(agents):
    a,p,c,_=waiting_cli(agents)
    p.responses=[{'tool_calls':calls('B')}]
    run,aid=identity(a)
    issue(a,c,f'/reject {run} {aid}')
    out=issue(a,c,'/resume '+run)
    assert 'WAITING_APPROVAL' in out
    new=a.get_pending_approval(run)
    assert new.approval_id!=aid and new.decision.operation.turn==1
    assert a.executions==['A'] and len(p.requests)==2
    assert a._approval_store.get(aid).state is ApprovalStatus.REJECTED


def test_redaction_logging_and_metrics_do_not_mutate_original_data():
    import logging
    from core.display_safety import DisplayLogFilter
    raw={'authorization':'Bearer classified','prompt_tokens':42,'command':'curl https://user:pw@host --token classified-token'}
    old=deepcopy(raw)
    out=redact(raw)
    assert raw==old and out['prompt_tokens']==42
    assert 'classified' not in json.dumps(out) and 'user:pw' not in json.dumps(out)
    rec=logging.LogRecord('test',logging.INFO,'file',1,'args=%s',(raw,),None)
    assert DisplayLogFilter().filter(rec)
    assert 'classified' not in rec.getMessage() and 'user:pw' not in rec.getMessage()


def test_empty_existing_file_is_not_initialized_for_recovery(tmp_path):
    import sqlite3
    db=tmp_path/'empty.sqlite3'
    db.touch()
    with pytest.raises(CheckpointError,match='Schema'):
        SqliteCheckpointStore(db,require_existing=True)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[]


def test_invalidated_wait_does_not_advertise_approval_commands(agents):
    a,_,c,_=waiting_cli(agents)
    run,aid=identity(a)
    store=a._checkpoint_store
    store.acquire(run)
    try:a._approval_store.invalidate(aid,'explicit context loss')
    finally:store.release(run)
    out=issue(a,c,'/approval '+run)
    assert 'INVALIDATED' in out and '旧批准不能' in out
    assert '/approve' not in out and '/reject' not in out
    assert a.executions==['A']


def test_multiple_waiting_runs_share_database_but_not_approval_identity(agents):
    a,p,c,_=waiting_cli(agents)
    b,q,d,_=waiting_cli(agents,database=a._checkpoint_store.path)
    run,aid=identity(a)
    other,bid=identity(b)
    listing=issue(a,c,'/runs')
    assert run in listing and other in listing and aid in listing and bid in listing
    assert '操作失败' in issue(a,c,f'/approve {run} {bid}')
    assert 'APPROVED' in issue(a,c,f'/approve {run} {aid}')
    assert b.get_pending_approval(other).state is ApprovalStatus.PENDING
    assert a.executions==b.executions==['A'] and len(p.requests)==len(q.requests)==1


def test_runtime_cli_queries_are_read_only_and_do_not_take_execution_lock(agents):
    a,p,c,_=waiting_cli(agents)
    run,aid=identity(a)
    before=deepcopy(a._checkpoint_store.load(run))
    for command in ['/runs','/approval '+run]:
        assert run in issue(a,c,command)
    assert a._checkpoint_store.load(run)==before
    assert not a._checkpoint_store.holds_lock(run)
    assert a.executions==['A'] and len(p.requests)==1


def test_quoted_control_name_uses_same_parser_and_never_logs_details(agents):
    a,p,c,_=waiting_cli(agents)
    run,aid=identity(a)
    assert aid in issue(a,c,f'/"approval" {run}')
    events=[json.loads(line) for line in a.session_recorder.path.read_text(encoding='utf-8').splitlines()]
    assert not any(event['type']=='command_result' for event in events)
    assert [event['command'] for event in events if event['type']=='cli_control']==['approval']
    assert c.get_history()==[] and a.executions==['A'] and len(p.requests)==1
