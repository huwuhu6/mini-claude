"""Equivalent implementation shapes must satisfy invariant-based graders."""
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
            process, fixture_env = _start_fixture_controller(case.name, 'variant')
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
