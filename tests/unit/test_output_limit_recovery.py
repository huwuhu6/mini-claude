"""Exercise regeneration through the real loop, journal and approval boundary."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import MethodType

import pytest

from test_run_snapshot import agents, calls
from core.run_state import RunStatus
from models.config import LLMConfig, OutputLimitRecoveryConfig
from providers.deepseek import DeepseekProvider
from providers.base import Message


def wire(provider, *, effort='high', supported=None):
    provider.config = {'reasoning_effort': effort,
                       'supported_reasoning_efforts': ['low', 'high', 'max'] if supported is None else supported,
                       'base_url': 'https://deployment.test/v1'}
    for name in ('output_limit_recovery_contract', 'output_limit_initial_parameters', 'output_limit_retry_parameters'):
        setattr(provider, name, MethodType(getattr(DeepseekProvider, name), provider))
    provider.parameters = []
    original = provider.create_message

    def request(*args, **kwargs):
        provider.parameters.append(deepcopy(kwargs))
        return original(*args, **kwargs)
    provider.create_message = request


def truncated(**kwargs):
    return {'content': 'incomplete', 'tool_calls': calls('DANGER'), 'finish_reason': 'length',
            'usage': {'prompt_tokens': 100, 'completion_tokens': 8000,
                      'reasoning_tokens': 7900, 'total_tokens': 8100}, **kwargs}


def trace(agent):
    return json.loads(next(agent.data_paths.traces.glob('task_*.json')).read_text(encoding='utf-8'))


def test_recovery_regenerates_same_history_and_counts_every_response(agents):
    agent, provider = agents([truncated(), {'content': 'complete'}], pause_after=None)
    wire(provider)
    assert agent.run('task') == 'complete'
    assert agent.executions == []
    assert provider.requests[0] == provider.requests[1]
    assert [p['reasoning_effort'] for p in provider.parameters] == ['high', 'low']
    assert [p['max_tokens'] for p in provider.parameters] == [8000, 8000]
    assert [m.content for m in agent.messages if m.role == 'assistant'] == ['complete']
    assert agent.last_metrics['completion_tokens'] == 8000
    assert agent._run_state.output_limit_retries == 1
    turn = trace(agent)['turns'][0]
    assert [p['finish_reason'] for p in turn['provider_attempts']] == ['length', None]
    assert turn['completion_tokens'] == 8000


@pytest.mark.parametrize('persist,expected', [(False, ['high', 'low', 'high']),
                                            (True, ['high', 'low', 'low'])])
def test_cross_turn_effort_is_opt_in_and_new_run_resets_it(agents, persist, expected):
    a, ap = agents([truncated(), {'tool_calls': calls('B'), 'finish_reason': 'tool_calls'},
                    {'content': 'complete', 'finish_reason': 'stop'}, {}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = persist
    assert a.run('task') == 'complete'
    assert [p['reasoning_effort'] for p in ap.parameters] == expected
    assert a._run_state.output_limit_effective_effort == ('low' if persist else None)
    assert a.executions == ['B']
    assert a._run_state.model_turns_used == 2
    assert ap.config['reasoning_effort'] == 'high'
    assert a.run('new task') == 'done'
    assert ap.parameters[-1]['reasoning_effort'] == 'high'
    assert a._run_state.output_limit_effective_effort is None
    assert a._run_state.output_limit_retries == 0


def test_length_increase_does_not_become_next_turn_output_limit(agents):
    a, ap = agents([truncated(), truncated(), {'tool_calls': calls('B'), 'finish_reason': 'tool_calls'},
                    {'finish_reason': 'stop'}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    assert a.run('task') == 'done'
    assert [p['max_tokens'] for p in ap.parameters] == [8000, 8000, 16000, 8000]
    assert [p['reasoning_effort'] for p in ap.parameters] == ['high', 'low', 'low', 'low']


def test_arbitrary_declared_efforts_decrease_one_step_and_survive_snapshot(agents):
    levels = ['economy', 'balanced', 'deep', 'extended', 'extra']
    assert LLMConfig(reasoning_effort='extended', supported_reasoning_efforts=levels)
    a, ap = agents([truncated(), {'tool_calls': calls('A', 'B'), 'finish_reason': 'tool_calls'}])
    wire(ap, effort='extended', supported=levels)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    a.enable_checkpoints()
    a.run('task')
    assert a._run_state.output_limit_effective_effort == 'deep'
    assert ap.parameters[-1]['reasoning_effort'] == 'deep'
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row['snapshot']['run']['output_limit_effective_effort'] == 'deep'
    b, bp = agents([{'finish_reason': 'stop'}], pause_after=None)
    wire(bp, effort='extended', supported=levels)
    b.config.llm.output_limit_recovery.persist_effort_for_run = True
    b.enable_checkpoints(a._checkpoint_store.path)
    assert b.resume_checkpoint(a._run_state.run_id) == 'done'
    assert b.executions == ['B']
    assert len(bp.requests) == 1 and bp.parameters[0]['reasoning_effort'] == 'deep'
    assert b._run_state.output_limit_retries == 1


def test_recovered_effort_is_retained_even_when_tool_json_is_invalid(agents):
    invalid = calls('DANGER')
    invalid[0]['function']['arguments'] = '{broken'
    a, ap = agents([truncated(), {'tool_calls': invalid, 'finish_reason': 'tool_calls'},
                    {'tool_calls': calls('B'), 'finish_reason': 'tool_calls'},
                    {'finish_reason': 'stop'}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    assert a.run('task') == 'done'
    assert a.executions == ['B']  # DANGER with invalid JSON never executes.
    assert [p['reasoning_effort'] for p in ap.parameters] == ['high', 'low', 'low', 'low']


@pytest.mark.parametrize('finish_reason', [None, 'content_filter', 'refusal', 'error'])
def test_non_normal_response_does_not_commit_effort(agents, finish_reason):
    a, ap = agents([truncated(), {'content': 'response', 'finish_reason': finish_reason}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    a.run('task')
    assert a._run_state.output_limit_effective_effort is None


def test_failed_candidate_does_not_commit_or_refund_reservation(agents):
    a, ap = agents([truncated()], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    original = ap.create_message

    def request(*args, **kwargs):
        if ap.requests:
            assert a._run_state.output_limit_effective_effort is None
            raise RuntimeError('transport failed')
        return original(*args, **kwargs)

    ap.create_message = request
    a.run('task')
    assert a._run_state.output_limit_effective_effort is None
    assert a._run_state.output_limit_retries == 1
    assert a._run_state.output_limit_reserved_tokens > 8000
    assert a._run_state.output_limit_last_request['reasoning_effort'] == 'low'


def test_no_control_stays_without_control_with_persistence_enabled(agents):
    a, ap = agents([truncated(), {'finish_reason': 'stop'}], pause_after=None)
    wire(ap, effort=None, supported=[])
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    assert a.run('task') == 'done'
    assert all('reasoning_effort' not in p for p in ap.parameters)
    assert a._run_state.output_limit_effective_effort is None


@pytest.mark.parametrize('kind', ['no_context', 'no_budget'])
def test_synthetic_length_never_installs_a_candidate(agents, monkeypatch, kind):
    a, ap = agents([truncated()], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    if kind == 'no_context':
        a.feature_manager.disable('compression')
        monkeypatch.setattr(a.compressor, 'estimate_prompt_tokens', lambda *args, **kwargs: 1_000_000)
    else:
        a.config.llm.output_limit_recovery.max_retry_tokens_per_run = 0
    assert '输出达到上限' in a.run('task')
    assert len(ap.requests) == (0 if kind == 'no_context' else 1)
    assert a._run_state.output_limit_effective_effort is None
    assert trace(a)['turns'][0]['provider_attempts'][-1]['status'] == 'not_requested'


@pytest.mark.parametrize('invalid', ['max', 'unknown', True])
def test_restore_rejects_effort_outside_declared_initial_ceiling(agents, invalid):
    a, ap = agents([truncated(), {'tool_calls': calls('A', 'B'), 'finish_reason': 'tool_calls'}])
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    a.run('task')
    snapshot = a.export_run_snapshot()
    snapshot['run']['output_limit_effective_effort'] = invalid
    b, bp = agents([], pause_after=None)
    wire(bp)
    b.config.llm.output_limit_recovery.persist_effort_for_run = True
    with pytest.raises(ValueError, match='强度'):
        b.restore_run_snapshot(snapshot)
    assert bp.requests == [] and b.executions == []


def test_current_snapshot_cannot_omit_effort_memory(agents):
    a, _ = agents([{'tool_calls': calls('A', 'B')}])
    a.run('task')
    snapshot = a.export_run_snapshot()
    del snapshot['run']['output_limit_effective_effort']
    b, _ = agents([])
    with pytest.raises(ValueError, match='RunState'):
        b.restore_run_snapshot(snapshot)


def test_previous_recovery_snapshot_defaults_memory_without_resetting_budget():
    from core.checkpoint_store import SqliteCheckpointStore
    from test_checkpoint_store import snapshot
    from core.run_snapshot import decode_run
    data = snapshot()
    data['schema_version'] = 7
    data['run']['output_limit_retries'] = 2
    data['run']['output_limit_reserved_tokens'] = 1234
    del data['run']['output_limit_effective_effort']
    state = SqliteCheckpointStore.validate_snapshot(data, 'run-1')
    assert state.output_limit_effective_effort is None
    assert state.output_limit_retries == 2 and state.output_limit_reserved_tokens == 1234
    with pytest.raises(ValueError, match='RunState'):
        decode_run(data['run'])


def test_deployment_contract_change_blocks_restore(agents):
    a, ap = agents([{'tool_calls': calls('A', 'B')}])
    wire(ap)
    a.run('task')
    snapshot = a.export_run_snapshot()
    b, bp = agents([])
    wire(bp)
    bp.config['base_url'] = 'https://other-deployment.test/v1'
    with pytest.raises(ValueError, match='不兼容'):
        b.restore_run_snapshot(snapshot)
    assert bp.requests == [] and b.executions == []


def test_pending_retry_cannot_raise_the_remembered_effort(agents):
    a, ap = agents([truncated(), {'tool_calls': calls('A', 'B'), 'finish_reason': 'tool_calls'}])
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = True
    a.run('task')
    snapshot = a.export_run_snapshot()
    snapshot['run']['output_limit_attempt'] = 1
    snapshot['run']['output_limit_last_request'] = {'max_tokens': 8000, 'reasoning_effort': 'high'}
    b, bp = agents([])
    wire(bp)
    b.config.llm.output_limit_recovery.persist_effort_for_run = True
    with pytest.raises(ValueError, match='强度'):
        b.restore_run_snapshot(snapshot)
    assert bp.requests == [] and b.executions == []


@pytest.mark.parametrize('value', [None, 'low', ['low', 'low'], [''], [' low'], [False]])
def test_invalid_effort_declarations_fail_before_requests(value):
    with pytest.raises(ValueError):
        LLMConfig(supported_reasoning_efforts=value)


def test_aliases_cannot_hide_conflicting_order():
    provider = object.__new__(DeepseekProvider)
    provider.config = {'base_url': 'https://api.deepseek.com', 'reasoning_effort': 'high',
                       'supported_reasoning_efforts': ['high', 'low', 'medium']}
    with pytest.raises(ValueError, match='顺序'):
        provider.output_limit_initial_parameters(8000)


def test_only_official_deepseek_endpoint_normalizes_its_aliases():
    provider = object.__new__(DeepseekProvider)
    provider.config = {'base_url': 'https://api.deepseek.com/v1', 'reasoning_effort': 'xhigh',
        'supported_reasoning_efforts': ['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']}
    assert provider.output_limit_recovery_contract()['efforts'] == ['low', 'high', 'max']
    initial = provider.output_limit_initial_parameters(8000)
    assert initial['reasoning_effort'] == 'high'
    assert provider.output_limit_retry_parameters(initial, 16000)['reasoning_effort'] == 'low'
    provider.config['base_url'] = 'https://compatible.test/v1'
    assert provider.output_limit_initial_parameters(8000)['reasoning_effort'] == 'xhigh'
    assert provider.output_limit_retry_parameters({'max_tokens': 8000, 'reasoning_effort': 'xhigh'},
                                                16000)['reasoning_effort'] == 'high'


def test_endpoint_credentials_are_excluded_from_deployment_identity():
    provider = object.__new__(DeepseekProvider)
    provider.config = {'base_url': 'https://user:secret@compatible.test/v1?key=secret'}
    first = provider.output_limit_recovery_contract()
    provider.config['base_url'] = 'https://compatible.test/v1?key=different'
    assert provider.output_limit_recovery_contract() == first
    assert 'secret' not in json.dumps(first)


def test_repeated_length_has_per_turn_limit_and_never_dispatches(agents):
    agent, provider = agents([truncated()] * 4, pause_after=None)
    wire(provider)
    assert '输出达到上限' in agent.run('task')
    assert len(provider.requests) == 3
    assert [p['max_tokens'] for p in provider.parameters] == [8000, 8000, 16000]
    assert agent.executions == []
    assert agent.last_metrics['completion_tokens'] == 24000
    assert agent._run_state.output_limit_retries == 2
    assert trace(agent)['terminal_reason'] == 'PROVIDER_OUTPUT_LIMIT'


@pytest.mark.parametrize('changes', [
    {'enabled': False}, {'max_retries_per_turn': 0}, {'max_retries_per_run': 0},
    {'max_retry_tokens_per_run': 1},
])
def test_disabled_or_exhausted_budget_never_calls_again(agents, changes):
    agent, provider = agents([truncated(), {}], pause_after=None)
    wire(provider)
    agent.config.llm.output_limit_recovery = OutputLimitRecoveryConfig(**changes)
    assert '输出达到上限' in agent.run('task')
    assert len(provider.requests) == 1


def test_no_identical_retry_and_no_undeclared_reasoning_parameter(agents):
    agent, provider = agents([truncated(), truncated(), {}], pause_after=None)
    wire(provider, effort=None)
    agent.config.llm.output_limit_recovery.max_output_tokens = 16000
    assert '输出达到上限' in agent.run('task')
    assert [p['max_tokens'] for p in provider.parameters] == [8000, 16000]
    assert all('reasoning_effort' not in p for p in provider.parameters)


def test_run_limit_survives_tool_pause_and_new_instance(agents):
    a, ap = agents([truncated(), {'tool_calls': calls('A', 'B')}], pause_after='A')
    wire(ap)
    a.config.llm.output_limit_recovery.max_retries_per_run = 1
    a.enable_checkpoints()
    a.run('task')
    assert a._run_state.status is RunStatus.PAUSED
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row['snapshot']['run']['output_limit_retries'] == 1
    assert row['snapshot']['run']['output_limit_reserved_tokens'] > 8000
    b, bp = agents([truncated(), {}], pause_after=None)
    wire(bp)
    b.config.llm.output_limit_recovery.max_retries_per_run = 1
    b.enable_checkpoints(a._checkpoint_store.path)
    assert '输出达到上限' in b.resume_checkpoint(a._run_state.run_id)
    assert a.executions == ['A'] and b.executions == ['B']
    assert len(bp.requests) == 1 and b._run_state.output_limit_retries == 1


@pytest.mark.parametrize('persist', [False, True])
def test_recovered_tool_call_still_requires_approval(agents, persist):
    a, ap = agents([truncated(), {'tool_calls': calls('B'), 'finish_reason': 'tool_calls'}, {}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = persist
    a.enable_checkpoints()
    a.enable_hitl()
    assert '审批' in a.run('task')
    assert a.executions == [] and len(ap.requests) == 2
    run_id = a._run_state.run_id
    assert '审批' in a.resume_checkpoint(run_id)
    assert len(ap.requests) == 2
    approval = a.get_pending_approval(run_id)
    a.decide_approval(run_id, approval.approval_id, approved=True, actor='test')
    assert a.resume_checkpoint(run_id) == 'done'
    assert a.executions == ['B'] and len(ap.requests) == 3
    assert ap.parameters[-1]['reasoning_effort'] == ('low' if persist else 'high')


@pytest.mark.parametrize('persist', [False, True])
def test_reserved_retry_is_not_reissued_after_crash(agents, monkeypatch, persist):
    a, ap = agents([truncated(), {}], pause_after=None)
    wire(ap)
    a.config.llm.output_limit_recovery.persist_effort_for_run = persist
    a.enable_checkpoints()
    original = ap.create_message

    def crash(*args, **kwargs):
        if len(ap.requests) == 1:
            raise SystemExit('simulated process loss')
        return original(*args, **kwargs)

    monkeypatch.setattr(ap, 'create_message', crash)
    with pytest.raises(SystemExit):
        a.run('task')
    row = a._checkpoint_store.load(a._run_state.run_id)
    assert row['boundary'] == 'before_model'
    assert row['snapshot']['run']['output_limit_attempt'] == 1
    assert row['snapshot']['run']['output_limit_effective_effort'] is None
    b, bp = agents([{'finish_reason': 'stop'}], pause_after=None)
    wire(bp)
    b.config.llm.output_limit_recovery.persist_effort_for_run = persist
    b.enable_checkpoints(a._checkpoint_store.path)
    assert b.resume_checkpoint(a._run_state.run_id) == 'done'
    assert bp.parameters[0]['reasoning_effort'] == 'low'
    assert bp.parameters[0]['max_tokens'] == 16000
    assert b._run_state.output_limit_retries == 2
    assert b._run_state.output_limit_effective_effort == ('low' if persist else None)


@pytest.mark.parametrize('field,value', [
    ('enabled', 'yes'), ('max_retries_per_turn', True), ('max_retries_per_run', -1),
    ('persist_effort_for_run', 'yes'),
    ('max_retry_tokens_per_run', 1.2), ('max_output_tokens', 0),
    ('context_safety_margin_tokens', -1),
])
def test_recovery_config_rejects_invalid_values(field, value):
    with pytest.raises(ValueError):
        OutputLimitRecoveryConfig(**{field: value})


def test_reasoning_capability_is_explicit():
    with pytest.raises(ValueError):
        LLMConfig(reasoning_effort='low')
    assert asdict(LLMConfig())['supported_reasoning_efforts'] == []


def test_missing_usage_does_not_trigger_compression_or_refund_reservation(agents, monkeypatch):
    a, ap = agents([truncated(usage={}), {}], pause_after=None)
    wire(ap)
    monkeypatch.setattr(a.compressor, 'compress', lambda messages: pytest.fail('没有上下文压力，不应压缩'))
    assert a.run('task') == 'done'
    assert a._run_state.output_limit_reserved_tokens > 8000
    assert a.last_metrics['total_tokens'] == 0


def test_context_space_caps_output_and_compression_is_bounded(agents, monkeypatch):
    a, ap = agents([truncated(), {}], pause_after=None)
    wire(ap)
    a.config.compression.context_window_tokens = 10000
    a.config.llm.output_limit_recovery.context_safety_margin_tokens = 1000
    monkeypatch.setattr(a.compressor, 'estimate_prompt_tokens', lambda *args, **kwargs: 2000)
    compactions = []
    monkeypatch.setattr(a.compressor, 'compress', lambda messages: compactions.append(list(messages)) or messages)
    assert a.run('task') == 'done'
    assert len(compactions) == 1
    assert [p['max_tokens'] for p in ap.parameters] == [7000, 7000]


def test_context_compression_releases_output_space_before_request(agents, monkeypatch):
    a, ap = agents([{}], pause_after=None)
    wire(ap)
    a.messages = [Message('user', 'old history')]
    a.config.compression.context_window_tokens = 10000
    a.config.llm.output_limit_recovery.context_safety_margin_tokens = 1000
    monkeypatch.setattr(a.compressor, 'estimate_prompt_tokens', lambda messages, **kwargs:
                        1000 if messages[0].content == 'compressed history' else 8000)
    monkeypatch.setattr(a.compressor, 'compress', lambda messages: [Message('user', 'compressed history')])
    assert a.run('task') == 'done'
    assert ap.parameters[0]['max_tokens'] == 8000
    assert trace(a)['turns'][0]['compression_triggered']


def test_no_context_space_does_not_send_invalid_request(agents, monkeypatch):
    a, ap = agents([{}], pause_after=None)
    wire(ap)
    a.feature_manager.disable('compression')
    monkeypatch.setattr(a.compressor, 'estimate_prompt_tokens', lambda *args, **kwargs: 1_000_000)
    assert '输出达到上限' in a.run('task')
    assert ap.requests == []


def test_headless_recovery_uses_normal_lifecycle_without_approval(agents, monkeypatch):
    from agent import headless
    a, ap = agents([truncated(), {}], pause_after=None)
    wire(ap)
    monkeypatch.setattr(headless, 'MiniClaudeAgent', lambda **kwargs: a)
    result = headless.run_agent_once(workspace_root=a.runtime_context.workspace_root,
                                    instruction='task', runtime_data_root=a.data_paths.root)
    assert result.final_status == 'SUCCESS' and result.final_response == 'done'
    assert not a._hitl_enabled
    assert len(ap.requests) == 2


def test_current_snapshot_cannot_drop_recovery_fields(agents):
    a, ap = agents([{'tool_calls': calls('A', 'B')}], pause_after='A')
    a.run('task')
    snapshot = a.export_run_snapshot()
    del snapshot['run']['output_limit_retries']
    b, _ = agents([], pause_after=None)
    with pytest.raises(ValueError, match='RunState'):
        b.restore_run_snapshot(snapshot)


def test_retry_reservation_charges_excess_usage_without_refunding_unused_tokens():
    from core.output_limit_recovery import charge_retry_usage, reserve_retry
    from core.run_state import RunState
    state = RunState()
    policy = OutputLimitRecoveryConfig(max_retry_tokens_per_run=1000)
    assert reserve_retry(state, policy, {'max_tokens': 100}, 100)
    charge_retry_usage(state, {'max_tokens': 100}, 100, {'total_tokens': 30})
    assert state.output_limit_reserved_tokens == 200
    charge_retry_usage(state, {'max_tokens': 100}, 100, {'prompt_tokens': 300, 'reasoning_tokens': 100})
    assert state.output_limit_reserved_tokens == 400


def test_retry_output_cap_applies_when_lowering_effort(agents):
    a, ap = agents([truncated(), {}], pause_after=None)
    wire(ap)
    a.config.llm.max_tokens = 100000
    assert a.run('task') == 'done'
    assert ap.parameters[1]['max_tokens'] == 65536


def test_recovery_settings_are_in_trace_for_comparison(agents):
    a, ap = agents([{}], pause_after=None)
    a.run('task')
    recorded = trace(a)['request_config']
    assert recorded['output_limit_recovery'] == asdict(a.config.llm.output_limit_recovery)
    assert recorded['supported_reasoning_efforts'] == []


def test_old_snapshot_without_recovery_fields_still_decodes():
    from core.checkpoint_store import SqliteCheckpointStore
    from test_checkpoint_store import snapshot
    from core.run_snapshot import decode_run
    data = snapshot()
    for key in ('output_limit_retries', 'output_limit_reserved_tokens', 'output_limit_attempt', 'output_limit_last_request',
                'output_limit_effective_effort'):
        del data['run'][key]
    state = SqliteCheckpointStore.validate_snapshot(data, 'run-1')
    assert state.output_limit_retries == state.output_limit_reserved_tokens == 0
    with pytest.raises(ValueError, match='RunState'):
        decode_run(data['run'])


@pytest.mark.parametrize('stream', [False, True])
def test_real_sdk_recovery_reissues_complete_request_without_partial_tool(agents, stream):
    import httpx
    from openai import OpenAI

    a, _ = agents([], pause_after=None)
    requests = []

    def send(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        content = {'content': 'partial', 'tool_calls': [{'type': 'function',
            'function': {'name': 'probe', 'arguments': '{"label":"DANGER'}}]} if first else {'content': 'complete'}
        reason = 'length' if first else 'stop'
        usage = {'prompt_tokens': 100, 'completion_tokens': 8000 if first else 7,
                 'total_tokens': 8100 if first else 107}
        if stream:
            if first:
                content['tool_calls'][0]['index'] = 0
            events = [{'choices': [{'index': 0, 'delta': content, 'finish_reason': reason}]},
                      {'choices': [], 'usage': usage}]
            body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)
        return httpx.Response(200, json={'choices': [{'message': content, 'finish_reason': reason}], 'usage': usage})

    provider = DeepseekProvider({'model': 'local-test', 'api_key': 'local-test', 'stream': stream,
        'reasoning_effort': 'high', 'supported_reasoning_efforts': ['low', 'high', 'max']})
    provider.client.close()
    provider.client = OpenAI(api_key='local-test', base_url='https://example.test/v1', max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(send)))
    a.provider_manager.register_provider('local-sdk', provider, is_primary=True)
    try:
        assert a.run('task') == 'complete'
        assert a.executions == []
        assert requests[0]['messages'] == requests[1]['messages']
        assert [r['reasoning_effort'] for r in requests] == ['high', 'low']
        assert trace(a)['completion_tokens'] == 8007
        assert len(trace(a)['turns'][0]['provider_attempts']) == 2
    finally:
        provider.client.close()


@pytest.mark.parametrize('key', ['output_limit_retries', 'output_limit_reserved_tokens'])
def test_store_refuses_to_refill_run_recovery_budget(agents, key):
    from core.checkpoint_store import CheckpointError
    a, ap = agents([truncated(), {'tool_calls': calls('A', 'B')}], pause_after='A')
    wire(ap)
    a.enable_checkpoints()
    a.run('task')
    run_id = a._run_state.run_id
    store = a._checkpoint_store
    row = store.load(run_id)
    changed = deepcopy(row['snapshot'])
    changed['run'][key] -= 1
    store.acquire(run_id)
    try:
        with pytest.raises(CheckpointError, match='输出恢复预算回退'):
            store.save_snapshot(run_id, row['revision'], changed, paused=True)
        assert store.load(run_id)['snapshot'] == row['snapshot']
    finally:
        store.release(run_id)


def test_rejected_recovered_call_cannot_use_retry_to_bypass_approval(agents):
    a, ap = agents([truncated(), {'tool_calls': calls('B')}, truncated(),
                    {'tool_calls': calls('B')}], pause_after=None)
    wire(ap)
    a.enable_checkpoints()
    a.enable_hitl()
    assert '审批' in a.run('task')
    run_id = a._run_state.run_id
    first = a.get_pending_approval(run_id)
    a.decide_approval(run_id, first.approval_id, approved=False, actor='test')
    assert '审批' in a.resume_checkpoint(run_id)
    second = a.get_pending_approval(run_id)
    assert second.approval_id != first.approval_id
    assert a.executions == [] and len(ap.requests) == 4
    assert ap.requests[2] == ap.requests[3]
