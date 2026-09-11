"""Equivalent implementation shapes must satisfy invariant-based graders."""
import json
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
TASKS = ROOT / 'sandbox' / 'tasks'


def _run_variant(case_prefix, setup=None, controller=False):
    case = next(TASKS.glob(case_prefix + '_*'))
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        shutil.copytree(case / 'reference_solution', work, dirs_exist_ok=True)
        shutil.copy2(case / 'verify.py', work / 'verify.py')
        shutil.copy2(ROOT / 'sandbox' / 'eval_runtime' / 'verification_support.py',
                     work / 'verification_support.py')
        if case_prefix == 'task_022':
            p = work / 'start_order_service.py'
            p.write_text(p.read_text().replace("data=b'{}'", "data=b'{\"variant\":true}'"), encoding='utf-8')
        elif case_prefix == 'task_024':
            p = work / 'vendor' / 'metrics_core.py'
            p.write_text("def score(event):\n    return event['amount'] / 10\n", encoding='utf-8')
        elif case_prefix == 'task_025':
            p = work / 'discounts.py'
            p.write_text(p.read_text().replace('total*=0.9', 'total=total-(total*0.1)'), encoding='utf-8')
        elif case_prefix == 'task_023':
            (work / 'cache_config.ini').write_text('output_dir=runtime/variant_output\nformat=json\n', encoding='utf-8')
            p = work / 'build_export.py'
            p.write_text(p.read_text().replace('runtime/output', 'runtime/variant_output'), encoding='utf-8')
        env = dict(os.environ)
        process = None
        if controller:
            sys.path.insert(0, str(ROOT))
            from eval_runner import _start_fixture_controller, _stop_fixture_controller
            process, fixture_env, _verifier_env = _start_fixture_controller(case.name, 'variant')
            env.update(fixture_env)
        try:
            if setup:
                assert subprocess.run(setup, cwd=work, env=env, capture_output=True).returncode == 0
            result = subprocess.run([sys.executable, 'verify.py'], cwd=work, env={**env, 'EVAL_REFERENCE_CHECK': '1'}, capture_output=True)
            assert result.returncode == 0, result.stderr.decode(errors='replace')
        finally:
            if controller:
                _stop_fixture_controller(process)


def test_service_reference_variant():
    _run_variant('task_022', setup=[sys.executable, 'start_order_service.py'], controller=True)


def _run_task022_verifier_with_audit(*, actions, trace_events):
    """Run the real task022 verifier with evaluator-owned fixture evidence."""
    from eval_runner import _stop_fixture_controller

    case = next(TASKS.glob('task_022_*'))
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        shutil.copy2(case / 'verify.py', work / 'verify.py')
        shutil.copy2(ROOT / 'sandbox' / 'eval_runtime' / 'verification_support.py',
                     work / 'verification_support.py')
        process, agent_env, verifier_env = _start_task022_controller()
        try:
            actions(agent_env)
            trace = work / 'trace.json'
            trace.write_text(json.dumps({
                'final_status': 'SUCCESS',
                'attempt_events': trace_events,
            }), encoding='utf-8')
            env = {**os.environ, **verifier_env, 'EVAL_TRACE_PATH': str(trace)}
            env.pop('EVAL_FIXTURE_TOKEN', None)
            return subprocess.run(
                [sys.executable, 'verify.py'], cwd=work, env=env,
                capture_output=True, text=True,
            )
        finally:
            _stop_fixture_controller(process)


def _start_task022_controller():
    from eval_runner import _start_fixture_controller
    process, agent_env, verifier_env = _start_fixture_controller('task_022_connection_refused_recoverable', 'variant-audit')
    assert process is not None
    return process, agent_env, verifier_env


def _task022_recovery_trace():
    return [
        {'subject_key': 'bash:health_check', 'intent_key': 'bash:health_check',
         'status': 'FAILURE', 'execution_success': False, 'observed_failure': True,
         'observation_fingerprint': 'before'},
        {'subject_key': 'bash:health_check', 'intent_key': 'bash:health_check',
         'status': 'SUCCESS', 'execution_success': True, 'observed_failure': False,
         'observation': 'HEALTHY_TRUE', 'observation_fingerprint': 'after',
         'resolution_key': 'bash:health_check'},
    ]


def _agent_request(env, path, *, method='GET', data=None):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(
        env['EVAL_FIXTURE_URL'] + path,
        method=method,
        headers={'X-Fixture-Token': env['EVAL_FIXTURE_TOKEN']},
        data=data,
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        error.read()
        return None


def _valid_task022_actions(env):
    _agent_request(env, '/health')
    _agent_request(env, '/start', method='POST', data=b'{"variant":true}')
    _agent_request(env, '/health')
    _agent_request(env, '/orders/42')


def test_task022_verifier_accepts_audited_alternate_recovery():
    result = _run_task022_verifier_with_audit(
        actions=_valid_task022_actions,
        trace_events=_task022_recovery_trace(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_task022_verifier_rejects_fake_or_outcome_only_recovery():
    def fake_actions(env):
        _agent_request(env, '/health')

    fake = _run_task022_verifier_with_audit(
        actions=fake_actions,
        trace_events=_task022_recovery_trace(),
    )
    assert fake.returncode != 0

    def outcome_only_actions(env):
        _agent_request(env, '/start', method='POST', data=b'{}')
        _agent_request(env, '/orders/42')

    outcome_only = _run_task022_verifier_with_audit(
        actions=outcome_only_actions,
        trace_events=[_task022_recovery_trace()[1]],
    )
    assert outcome_only.returncode != 0


def test_task022_verifier_rejects_recovery_without_business_outcome():
    def recovery_only_actions(env):
        _agent_request(env, '/health')
        _agent_request(env, '/start', method='POST', data=b'{}')
        _agent_request(env, '/health')

    result = _run_task022_verifier_with_audit(
        actions=recovery_only_actions,
        trace_events=_task022_recovery_trace(),
    )
    assert result.returncode != 0


def test_artifact_reference_variant():
    _run_variant('task_023', setup=[sys.executable, 'build_export.py'])


def test_dependency_reference_variant():
    _run_variant('task_024', setup=[sys.executable, 'run_analytics.py'])


def test_pytest_reference_variant():
    _run_variant('task_025', setup=[sys.executable, '-m', 'pytest', '-q'])


def _run_node_verifier_variant(source_rewrite, seed):
    case = next(TASKS.glob('task_032_*'))
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        shutil.copytree(case / 'reference_solution', work, dirs_exist_ok=True)
        shutil.copy2(case / 'verify.py', work / 'verify.py')
        shutil.copy2(ROOT / 'sandbox' / 'eval_runtime' / 'verification_support.py',
                     work / 'verification_support.py')
        source = work / 'src' / 'discount.js'
        source.write_text(source_rewrite(source.read_text()), encoding='utf-8')
        trace = work / 'trace.json'
        trace.write_text(json.dumps({
            'final_status': 'SUCCESS',
            'attempt_events': [
                {'subject_key': 'node:test', 'intent_key': 'node:test',
                 'status': 'FAILURE', 'execution_success': False,
                 'observed_failure': True, 'observation_fingerprint': 'visible-failure'},
                {'subject_key': 'node:test', 'intent_key': 'node:test',
                 'status': 'SUCCESS', 'execution_success': True,
                 'observed_failure': False, 'observation_fingerprint': 'visible-recovery'},
            ],
        }), encoding='utf-8')
        env = {
            **os.environ,
            'EVAL_TRACE_PATH': str(trace),
            'EVAL_HIDDEN_SEED': seed,
        }
        return subprocess.run(
            [sys.executable, 'verify.py'], cwd=work, env=env,
            capture_output=True, text=True,
        )


def test_node_behavioral_alternate_shape_passes_dynamic_hidden_verifier():
    result = _run_node_verifier_variant(
        lambda source: source.replace(
            'return subtotal * 0.9;', 'return subtotal - (subtotal * 0.1);'
        ),
        'contract-alternate-shape',
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_node_visible_case_hardcode_fails_dynamic_hidden_verifier():
    result = _run_node_verifier_variant(
        lambda source: source.replace(
            'if (code === "VIP") return subtotal * 0.9;',
            'if (code === "VIP" && subtotal === 250) return subtotal * 0.9;'
        ),
        'contract-hardcode-attack',
    )
    assert result.returncode != 0


def _run_java_verifier_variant(source_text):
    case = next(TASKS.glob('task_031_*'))
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        shutil.copytree(case / 'reference_solution', work, dirs_exist_ok=True)
        shutil.copy2(case / 'verify.py', work / 'verify.py')
        shutil.copy2(ROOT / 'sandbox' / 'eval_runtime' / 'verification_support.py',
                     work / 'verification_support.py')
        source = work / 'src' / 'main' / 'java' / 'com' / 'example' / 'Invoice.java'
        source.write_text(source_text, encoding='utf-8')
        trace = work / 'trace.json'
        trace.write_text(json.dumps({
            'final_status': 'SUCCESS',
            'attempt_events': [
                {'subject_key': 'java:run_tests', 'intent_key': 'java:run_tests',
                 'status': 'FAILURE', 'execution_success': False,
                 'observed_failure': True, 'observation_fingerprint': 'compile-failure'},
                {'subject_key': 'java:run_tests', 'intent_key': 'java:run_tests',
                 'status': 'SUCCESS', 'execution_success': True,
                 'observed_failure': False, 'observation_fingerprint': 'compile-recovery'},
            ],
        }), encoding='utf-8')
        env = {
            **os.environ,
            'EVAL_TRACE_PATH': str(trace),
        }
        return subprocess.run(
            [sys.executable, 'verify.py'], cwd=work, env=env,
            capture_output=True, text=True,
        )


def test_java_behavioral_alternate_shape_passes_hidden_verifier():
    result = _run_java_verifier_variant('''package com.example;

public final class Invoice {
    public static int payableCents(int subtotalCents, int discountPercent) {
        if (subtotalCents < 0 || discountPercent < 0 || discountPercent > 100) {
            throw new IllegalArgumentException("invalid invoice");
        }
        int discountCents = (subtotalCents * discountPercent) / 100;
        return subtotalCents - discountCents - 25;
    }
}
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_java_visible_case_hardcode_fails_hidden_verifier():
    result = _run_java_verifier_variant('''package com.example;

public final class Invoice {
    public static int payableCents(int subtotalCents, int discountPercent) {
        if (subtotalCents < 0 || discountPercent < 0 || discountPercent > 100) {
            throw new IllegalArgumentException("invalid invoice");
        }
        if (subtotalCents == 1000 && discountPercent == 10) return 875;
        if (subtotalCents == 500 && discountPercent == 0) return 475;
        return subtotalCents;
    }
}
''')
    assert result.returncode != 0
