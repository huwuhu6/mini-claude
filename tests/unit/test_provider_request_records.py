"""Completed request evidence survives process loss without a real provider."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core.session_recorder import SessionRecorder
from core.compression import Compressor
from providers.base import Message
from providers.deepseek import DeepseekProvider
from test_run_snapshot import agents
from test_output_limit_recovery import truncated, wire
from test_context_reliability import _SummaryProvider


def request_events(path):
    return [e for line in path.read_text(encoding='utf-8').splitlines()
            if (e := json.loads(line)).get('type', '').startswith('provider_request_')]


@pytest.mark.parametrize('usage,expected', [
    (None, None),
    ({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
     {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}),
    ({'prompt_tokens': 19, 'completion_tokens': -1, 'total_tokens': True}, {'prompt_tokens': 19}),
])
@pytest.mark.parametrize('redact', [False, True])
def test_reported_usage_preserves_unknown_zero_and_partial_values(tmp_path, usage, expected, redact):
    provider = object.__new__(DeepseekProvider)
    response = {'choices': [{'message': {'content': 'PRIVATE_RESPONSE'}, 'finish_reason': 'stop'}]}
    if usage is not None:
        response['usage'] = usage
    recorder = SessionRecorder(tmp_path)
    recorder.redact_display = redact
    with recorder.provider_request(source='main', run_id='run', turn=1,
            estimated_prompt_tokens=123,
            parameters={'max_tokens': 8000, 'api_key': 'PRIVATE_KEY'}) as observed:
        observed.update(provider.parse_response(response))
    start, finish = request_events(recorder.path)
    assert start['request_id'] == finish['request_id']
    assert start['estimated_prompt_tokens'] == finish['estimated_prompt_tokens'] == 123
    assert finish['usage'] == expected and finish['usage_origin'] == 'reported'
    assert 'PRIVATE_' not in recorder.path.read_text(encoding='utf-8')


def test_normalized_input_output_counts_remain_visible_with_redaction(tmp_path):
    recorder = SessionRecorder(tmp_path)
    recorder.redact_display = True
    with recorder.provider_request(source='main', parameters={'max_tokens': 8000}) as observed:
        observed['usage'] = {'input_tokens': 4, 'output_tokens': 6, 'total_tokens': 10}
    _, finish = request_events(recorder.path)
    assert finish['usage'] == observed['usage']
    assert finish['usage_origin'] == 'normalized'


def test_main_request_error_is_recorded_without_exception_payload(agents):
    a, ap = agents([], pause_after=None)
    ap.create_message = lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError('PRIVATE_KEY'))
    a.run('task')
    start, finish = request_events(a.session_recorder.path)
    assert start['request_id'] == finish['request_id']
    assert finish['status'] == 'error' and finish['error_type'] == 'TimeoutError'
    assert finish['usage'] is None and finish['source'] == 'main'
    assert 'PRIVATE_KEY' not in json.dumps([start, finish])


def test_log_io_failure_does_not_change_recovery_or_budget(agents, tmp_path):
    a, ap = agents([truncated(), {'content': 'complete', 'finish_reason': 'stop'}], pause_after=None)
    wire(ap)
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('unchanged', encoding='utf-8')
    a.session_recorder.sessions_dir = blocked
    assert a.run('task') == 'complete'
    assert len(ap.requests) == 2 and a._run_state.output_limit_retries == 1
    assert a._run_state.output_limit_reserved_tokens > 8000
    assert blocked.read_text(encoding='utf-8') == 'unchanged'


@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
def test_summary_records_reported_response_even_when_summary_is_unusable(tmp_path, finish_reason):
    compressor = Compressor()
    recorder = SessionRecorder(tmp_path)
    compressor.set_request_observer(recorder.provider_request)
    compressor.set_provider(_SummaryProvider(response={'content': 'summary', 'finish_reason': finish_reason,
        'usage': {'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}}))
    if finish_reason == 'length':
        with pytest.raises(ValueError, match='上限'):
            compressor._llm_summarize([Message('user', 'PRIVATE_PROMPT')])
    else:
        assert compressor._llm_summarize([Message('user', 'PRIVATE_PROMPT')]) == 'summary'
    start, finish = request_events(recorder.path)
    assert start['source'] == finish['source'] == 'summary'
    assert start['parameters'] == {'max_tokens': 600, 'temperature': 0.3}
    assert finish['status'] == 'received' and finish['finish_reason'] == finish_reason
    assert finish['usage']['total_tokens'] == 25
    assert 'PRIVATE_PROMPT' not in recorder.path.read_text(encoding='utf-8')


def test_summary_transport_error_is_recorded_without_fabricating_usage(tmp_path):
    compressor = Compressor()
    recorder = SessionRecorder(tmp_path)
    compressor.set_request_observer(recorder.provider_request)
    compressor.set_provider(_SummaryProvider(error=ConnectionError('PRIVATE_KEY')))
    with pytest.raises(ConnectionError):
        compressor._llm_summarize([Message('user', 'prompt')])
    _, finish = request_events(recorder.path)
    assert finish['status'] == 'error' and finish['usage'] is None
    assert finish['error_type'] == 'ConnectionError'
    assert 'PRIVATE_KEY' not in recorder.path.read_text(encoding='utf-8')


def test_hard_kill_keeps_completed_requests_and_marks_inflight_as_unknown(tmp_path):
    base = tmp_path
    (base / 'workspace').mkdir()
    (base / 'config.yaml').write_text('features:\n  skills: false\n', encoding='utf-8')
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / 'src'), str(Path(__file__).parent)]),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    worker = Path(__file__).with_name('provider_request_crash_probe.py')
    with (base / 'worker.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(worker), str(base)],
                                 env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            marker = base / 'ready.json'
            deadline = time.monotonic() + 35
            while not marker.exists():
                if child.poll() is not None or time.monotonic() > deadline:
                    pytest.fail((base / 'worker.log').read_text(encoding='utf-8'))
                time.sleep(0.03)
            child.kill()
            child.wait(timeout=10)
            assert child.returncode != 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    marker_data = json.loads(marker.read_text(encoding='utf-8'))
    events = request_events(Path(marker_data['session_path']))
    starts = [e for e in events if e['type'] == 'provider_request_started']
    finishes = [e for e in events if e['type'] == 'provider_request_finished']
    assert len(starts) == 3 and len(finishes) == 2
    assert [e['parameters']['reasoning_effort'] for e in starts] == ['high', 'low', 'low']
    assert [e['turn'] for e in starts] == [1, 1, 2]
    assert [e['attempt'] for e in starts] == [0, 1, 0]
    assert [e['finish_reason'] for e in finishes] == ['length', 'tool_calls']
    assert sum(e['usage']['total_tokens'] for e in finishes) == 8125
    assert starts[-1]['request_id'] not in {e['request_id'] for e in finishes}
    assert starts[-1].get('usage') is None
    assert len({e['run_id'] for e in events}) == 1
    assert not list((base / 'runtime/traces').glob('task_*.json'))
    print(json.dumps({'evidence': 'hard_kill', 'completed_requests_preserved': len(finishes),
                      'known_total_tokens': 8125, 'inflight_requests_without_usage': 1}))
