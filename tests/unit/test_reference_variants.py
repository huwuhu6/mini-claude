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
