"""Controlled SDK comparisons prove truncation recovery, not coding ability."""
from copy import deepcopy
import json

import httpx
from openai import OpenAI
import pytest

from providers.deepseek import DeepseekProvider
from test_run_snapshot import agents, calls
from test_output_limit_recovery import trace, truncated, wire
from test_provider_request_records import request_events


def response(message, reason, usage, stream):
    if not stream:
        return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': reason}], 'usage': usage})
    message = deepcopy(message)
    for i, call in enumerate(message.get('tool_calls', [])):
        call['index'] = i
    chunks = [{'choices': [{'index': 0, 'delta': message, 'finish_reason': reason}]},
              {'choices': [], 'usage': usage}]
    body = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)


def install(a, send, stream, effort=True):
    config = {'model': 'controlled-test', 'api_key': 'local-test', 'stream': stream,
              'base_url': 'https://example.test/v1'}
    if effort:
        config.update(reasoning_effort='high', supported_reasoning_efforts=['low', 'high', 'max'])
    provider = DeepseekProvider(config)
    provider.client.close()
    provider.client = OpenAI(api_key='local-test', base_url='https://example.test/v1', max_retries=0,
                            http_client=httpx.Client(transport=httpx.MockTransport(send)))
    a.provider_manager.register_provider('controlled-sdk', provider, is_primary=True)
    return provider


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mode,expected_requests,expected_tools,expected_retries,expected_status', [
    ('disabled', 1, 0, 0, 'FAILED'),
    ('request', 9, 4, 4, 'FAILED'),
    ('run', 8, 6, 1, 'SUCCESS'),
])
def test_same_response_contract_does_not_end_prematurely_after_downgrade(
        agents, stream, mode, expected_requests, expected_tools, expected_retries, expected_status):
    a, _ = agents([], pause_after=None)
    policy = a.config.llm.output_limit_recovery
    policy.enabled = mode != 'disabled'
    policy.persist_effort_for_run = mode == 'run'
    requests = []
    complete_replies = 0

    def send(request):
        nonlocal complete_replies
        payload = json.loads(request.content)
        requests.append(payload)
        if payload['reasoning_effort'] == 'high':
            message = {'content': 'incomplete', 'tool_calls': [{'type': 'function',
                'function': {'name': 'probe', 'arguments': '{"label":"DANGER'}}]}
            reason, output = 'length', 8000
        else:
            complete_replies += 1
            message = ({'tool_calls': calls(f'step-{complete_replies}')}
                       if complete_replies <= 6 else {'content': 'complete'})
            reason, output = ('tool_calls' if complete_replies <= 6 else 'stop'), 20
        usage = {'prompt_tokens': 100, 'completion_tokens': output, 'total_tokens': 100 + output}
        return response(message, reason, usage, stream)

    provider = install(a, send, stream)
    try:
        result = a.run('exercise six complete model/tool rounds')
        recorded = trace(a)
        assert recorded['final_status'] == expected_status
        assert len(requests) == expected_requests
        assert len(a.executions) == expected_tools and 'DANGER' not in a.executions
        assert a._run_state.output_limit_retries == expected_retries
        assert all(r['max_tokens'] == 8000 for r in requests)
        assert requests[0]['messages'] == requests[1]['messages'] if len(requests) > 1 else True
        if mode == 'run':
            assert result == 'complete'
            assert [r['reasoning_effort'] for r in requests] == ['high'] + ['low'] * 7
            assert a.executions == [f'step-{i}' for i in range(1, 7)]
            assert a._run_state.model_turns_used == 7
        else:
            assert recorded['terminal_reason'] == 'PROVIDER_OUTPUT_LIMIT'
        events = request_events(a.session_recorder.path)
        finishes = [e for e in events if e['type'] == 'provider_request_finished']
        starts = [e for e in events if e['type'] == 'provider_request_started']
        assert len(finishes) == len(starts) == len(requests)
        assert all(e['usage_origin'] == 'reported' for e in finishes)
        assert sum(e['usage']['total_tokens'] for e in finishes) == recorded['total_tokens']
        assert [e['parameters']['reasoning_effort'] for e in starts] == [r['reasoning_effort'] for r in requests]
        print('RECOVERY_PROOF ' + json.dumps({'mode': mode, 'stream': stream,
            'status': recorded['final_status'], 'terminal_reason': recorded['terminal_reason'],
            'requests': len(requests), 'recovery_requests': a._run_state.output_limit_retries,
            'complete_tool_rounds': len(a.executions), 'total_tokens_reported': recorded['total_tokens'],
            'request_efforts': [r['reasoning_effort'] for r in requests]}))
    finally:
        provider.client.close()


@pytest.mark.parametrize('stream', [False, True])
def test_output_growth_recovers_a_provider_without_effort_controls(agents, stream):
    a, _ = agents([], pause_after=None)
    requests = []

    def send(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            message, reason, output = {'content': 'incomplete'}, 'length', 8000
        elif len(requests) == 2:
            message, reason, output = {'tool_calls': calls('B')}, 'tool_calls', 20
        else:
            message, reason, output = {'content': 'complete'}, 'stop', 20
        return response(message, reason, {'prompt_tokens': 100, 'completion_tokens': output,
                                         'total_tokens': 100 + output}, stream)

    provider = install(a, send, stream, effort=False)
    try:
        assert a.run('task') == 'complete'
        assert a.executions == ['B']
        assert [r['max_tokens'] for r in requests] == [8000, 16000, 8000]
        assert all('reasoning_effort' not in r for r in requests)
        assert a._run_state.output_limit_retries == 1
    finally:
        provider.client.close()


@pytest.mark.parametrize('budget,request_count', [(8099, 1), (8100, 2)])
def test_exact_reservation_boundary_allows_only_affordable_recovery(agents, monkeypatch, budget, request_count):
    a, ap = agents([truncated(), {'content': 'complete', 'finish_reason': 'stop'}], pause_after=None)
    wire(ap)
    a.session_recorder.redact_display = True
    monkeypatch.setattr(a.compressor, 'estimate_prompt_tokens', lambda *args, **kwargs: 100)
    a.config.llm.output_limit_recovery.max_retry_tokens_per_run = budget
    result = a.run('task')
    assert len(ap.requests) == request_count
    assert a._run_state.output_limit_retries == request_count - 1
    assert a._run_state.output_limit_reserved_tokens == (8100 if request_count == 2 else 0)
    assert result == 'complete' if request_count == 2 else '输出达到上限' in result
    recovery_events = [json.loads(s) for s in a.session_recorder.path.read_text(encoding='utf-8').splitlines()
                       if json.loads(s).get('type', '').startswith('output_limit_recovery_')]
    assert recovery_events[-1]['retries_used'] == request_count - 1
    assert recovery_events[-1]['reserved_tokens'] == a._run_state.output_limit_reserved_tokens
