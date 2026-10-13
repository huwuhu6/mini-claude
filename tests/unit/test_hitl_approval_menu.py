from io import StringIO
from types import SimpleNamespace

import pytest
from test_run_snapshot import agents
from test_hitl_cli import waiting_cli, identity, closed
from cli.entrypoint import _present_response
from cli.ui import TerminalUI


class MenuUI:
    def __init__(self, choices):
        self.choices = iter(choices)
        self.operations = []
        self.answers = []
    def approval_choice(self, operation, reason, *, allow_rules=True):
        self.operations.append(operation)
        return next(self.choices)
    def print_answer(self, text):
        self.answers.append(text)


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)


def test_menu_approve_continues_original_suffix(agents, terminal):
    a,p,c,out = waiting_cli(agents)
    ui = MenuUI(['y'])
    assert not _present_response(a,ui,out)
    assert a.executions == ['A','B','C'] and len(p.requests) == 2
    assert ui.operations[0].provider_call_id == 'call-1'
    closed(a)


def test_menu_sequential_asks(agents, terminal):
    a,p,c,out = waiting_cli(agents,all_ask=True)
    ui = MenuUI(['1','1','1'])
    assert not _present_response(a,ui,out)
    assert [op.provider_call_id for op in ui.operations] == ['call-0','call-1','call-2']
    assert a.executions == ['A','B','C'] and len(p.requests) == 2
    closed(a)


def test_menu_rejection_closes_suffix(agents, terminal):
    a,p,c,out = waiting_cli(agents)
    assert not _present_response(a,MenuUI(['n']),out)
    assert a.executions == ['A'] and len(p.requests) == 1
    assert a._run_state.status.value == 'terminated'
    closed(a)


@pytest.mark.parametrize('choice,exits',[('',False),('p',False),('q',True)])
def test_menu_pause_never_decides_or_dispatches(agents,terminal,choice,exits):
    a,p,c,out = waiting_cli(agents)
    run,aid = identity(a)
    assert _present_response(a,MenuUI([choice]),out) is exits
    assert a.get_pending_approval(run).state.value == 'PENDING'
    assert a.executions == ['A'] and len(p.requests) == 1
    assert not a._checkpoint_store.holds_lock(run)


def test_menu_details_invalid_choice_and_fault_do_not_resume(agents,terminal,monkeypatch):
    a,p,c,out = waiting_cli(agents)
    ui = MenuUI(['d','bad','1'])
    def fail(*args,**kwargs):
        raise OSError('decision write failed')
    monkeypatch.setattr(a,'decide_approval',fail)
    with pytest.raises(OSError):
        _present_response(a,ui,out)
    assert len(ui.operations) == 3
    assert a.executions == ['A'] and len(p.requests) == 1


def test_non_terminal_preserves_explicit_commands(agents,monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    a,p,c,out = waiting_cli(agents)
    ui=MenuUI([])
    assert not _present_response(a,ui,out)
    assert ui.answers == [out] and ui.operations == []
    assert a.executions == ['A']


def test_card_redacts_and_displays_full_command(monkeypatch):
    stream=StringIO()
    ui=TerminalUI(stream)
    monkeypatch.setattr('builtins.input',lambda prompt:'1')
    operation=SimpleNamespace(tool_name='bash',workspace='workspace',cwd='cwd',
        arguments={'command':'echo complete-operation','api_key':'never-display'})
    assert ui.approval_choice(operation,'writes file') == '1'
    output=stream.getvalue()
    assert 'complete-operation' in output and 'never-display' not in output
    assert '允许一次并继续' in output and '保存暂停并退出' in output

@pytest.mark.parametrize('signal',[EOFError,KeyboardInterrupt])
def test_menu_interrupt_preserves_pending_and_releases_lock(agents,terminal,signal):
    a,p,c,out=waiting_cli(agents)
    class Interrupted(MenuUI):
        def approval_choice(self,*args,**kwargs):
            raise signal()
    assert _present_response(a,Interrupted([]),out)
    run,aid=identity(a)
    assert a.get_pending_approval(run).state.value=='PENDING'
    assert a.executions==['A'] and len(p.requests)==1
    assert not a._checkpoint_store.holds_lock(run)


def test_error_response_does_not_open_an_approval_menu(agents,terminal):
    a,p,c,out=waiting_cli(agents)
    ui=MenuUI([])
    assert not _present_response(a,ui,'错误: 当前 Run 尚未完成')
    assert ui.operations==[] and len(p.requests)==1
    assert a.executions==['A']


def test_repl_routes_waiting_run_into_menu_without_id_commands(agents,terminal,monkeypatch):
    from cli import entrypoint
    from test_run_snapshot import calls
    from test_hitl_runtime import configure
    a,p=agents([{'tool_calls':calls('A','B','C')},{}],pause_after=None)
    configure(a)
    class ReplUI(MenuUI):
        _BLUE=_DIM=''
        prompt_toolkit_available=False
        def _paint(self,text,color):
            return text
        def read_input(self):
            return next(self.inputs)
    ui=ReplUI(['1'])
    ui.inputs=iter(['task','exit'])
    monkeypatch.setattr(entrypoint,'TerminalUI',lambda:ui)
    entrypoint._run_repl(a)
    assert a.executions==['A','B','C'] and len(p.requests)==2
    assert len(ui.operations)==1
    closed(a)


@pytest.mark.parametrize('keys,expected,search',[
    ('\x1b[B\r','two',False), ('\x1b[B\x1b[A\r','one',False),
    ('\x1b','',False), ('/second\r','two',True), ('/missing\r','',True),
])
def test_real_prompt_toolkit_selection_and_search(monkeypatch,keys,expected,search):
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    import prompt_toolkit
    class TTYStream(StringIO):
        def isatty(self): return True
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        session = prompt_toolkit.PromptSession
        monkeypatch.setattr(prompt_toolkit,'PromptSession',lambda **kwargs:
            session(input=pipe,output=DummyOutput(),**kwargs))
        assert TerminalUI(TTYStream())._select([('one','first'),('two','second')],search=search)==expected


@pytest.mark.parametrize('choice,scope',[('2','conversation'),('3','workspace')])
def test_menu_rule_scope_and_extra_confirmation(agents,terminal,choice,scope):
    a,p,c,out=waiting_cli(agents)
    class RuleUI(MenuUI):
        def confirm_rule(self,operation,*,persistent):
            assert persistent==(scope=='workspace')
            return True
    assert not _present_response(a,RuleUI([choice]),out)
    assert a.executions==['A','B','C'] and len(a.list_permissions())==1
    assert a.list_permissions()[0]['scope']==scope
    closed(a)


def test_sensitive_file_card_omits_reusable_rule_options(monkeypatch):
    stream=StringIO(); ui=TerminalUI(stream)
    monkeypatch.setattr('builtins.input',lambda prompt:'p')
    op=SimpleNamespace(tool_name='read_file',workspace='w',cwd='w',arguments={'path':'.env'})
    assert ui.approval_choice(op,'sensitive',allow_rules=False)=='p'
    assert '记住' not in stream.getvalue() and '允许一次' in stream.getvalue()


def test_missing_keyboard_dependency_uses_explicit_numeric_input(monkeypatch):
    import builtins
    class TTYStream(StringIO):
        def isatty(self): return True
    original=builtins.__import__
    def missing(name,*args,**kwargs):
        if name.startswith('prompt_toolkit'): raise ImportError('not installed')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',missing)
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr(builtins,'input',lambda prompt:'2')
    assert TerminalUI(TTYStream())._select([('1','first'),('2','second')])=='2'
