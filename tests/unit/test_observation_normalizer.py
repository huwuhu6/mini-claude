"""Regression tests for structured process facts versus target observations."""

import sys
from pathlib import Path

from core.loop_controller import AttemptStatus, RuntimePolicy
from core.runtime_context.observation import ObservationNormalizer
from core.runtime_context.shell_session import ShellSession
from core.tools.base_tools import ToolResult


def _bash_result(*, stdout="", stderr="", exit_code=0, execution_success=True,
                 segment_exit_codes=None):
    return ToolResult(
        content=f"[Exit Code: {exit_code}]\n{stdout}{stderr}",
        success=execution_success,
        execution_success=execution_success,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        segment_exit_codes=segment_exit_codes or [],
    )


def test_http_503_is_observed_without_changing_process_success():
    evidence = ObservationNormalizer.normalize(
        "bash", {"command": "curl http://127.0.0.1:8080/health"},
        _bash_result(stdout="HTTP_CODE=503\n{\"healthy\": false}"),
    )
    assert evidence.observed_failure is True
    assert evidence.semantic_status == "UNHEALTHY"
    assert evidence.observation == "HTTP_503"


def test_ordinary_error_words_are_not_resource_failures():
    documented = ObservationNormalizer.normalize(
        "bash", {"command": 'echo "HTTP 503 is documented behaviour"'},
        _bash_result(stdout="HTTP 503 is documented behaviour"),
    )
    grep = ObservationNormalizer.normalize(
        "bash", {"command": 'grep "Permission denied" README.md'},
        _bash_result(stdout="README.md: Permission denied is documented here"),
    )
    warning = ObservationNormalizer.normalize(
        "bash", {"command": "pytest"},
        _bash_result(stderr="DeprecationWarning: old API"),
    )
    assert documented.observed_failure is False
    assert grep.observed_failure is False
    assert warning.observed_failure is False


def test_masked_child_exit_is_structured_evidence():
    evidence = ObservationNormalizer.normalize(
        "bash", {"command": "python probe.py & echo PROBE_EXIT=0"},
        _bash_result(stdout="PROBE_EXIT=0", segment_exit_codes=[1]),
    )
    assert evidence.observed_failure is True
    assert evidence.semantic_status == "PARTIAL_PROCESS_FAILURE"
    assert evidence.observation == "SEGMENT_EXIT_1"


def test_traceback_from_wrapper_is_observed_failure():
    evidence = ObservationNormalizer.normalize(
        "bash", {"command": "python resource_probe.py & echo PROBE_EXIT=0"},
        _bash_result(stdout="Traceback (most recent call last):\n  File 'resource_probe.py'"),
    )
    assert evidence.observed_failure is True
    assert evidence.observation == "TRACEBACK"


def test_background_status_exposes_completed_process_failure():
    evidence = ObservationNormalizer.normalize(
        "get_background_status", {},
        '{"status":"failed","exit_code":2,"error":"child failed"}',
    )
    assert evidence.observed_failure is True
    assert evidence.observation == "BACKGROUND_EXIT_2"


def test_semantic_failure_keeps_attempt_status_success_but_is_history_evidence():
    policy = RuntimePolicy()
    event, _ = policy.record_attempt(
        turn=1,
        tool_name="bash",
        intent_key="bash:curl:health",
        args_fingerprint="args",
        success=True,
        execution_success=True,
        observed_failure=True,
        semantic_status="UNHEALTHY",
        observation="HTTP_503",
        failure_category="NETWORK_UNREACHABLE",
    )
    assert event.status is AttemptStatus.SUCCESS
    assert event.execution_success is True
    assert event.observed_failure is True
    assert event.semantic_status == "UNHEALTHY"
    assert event.observation == "HTTP_503"
    assert policy.history.all() == (event,)


def test_shell_session_preserves_masked_segment_exit_code():
    session = ShellSession(Path.cwd())
    if sys.platform == "win32":
        command = "cmd /c exit 7 & echo WRAPPER_OK"
    else:
        command = "sh -c 'exit 7'; echo WRAPPER_OK"
    result = session.execute(command)
    assert result["success"] is True
    assert result["execution_success"] is True
    assert result["exit_code"] == 0
    assert result["segment_exit_codes"] == [7]
    assert "WRAPPER_OK" in result["stdout"]
