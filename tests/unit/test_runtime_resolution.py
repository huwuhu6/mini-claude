"""Deterministic regressions for runtime policy ownership and resolution."""

from core.loop_controller import (
    AttemptHistory,
    AttemptStatus,
    CommandNormalizer,
    LoopController,
    RuntimeDecision,
    RuntimePolicy,
)
from core.runtime_context.observation import ObservationNormalizer
from core.tools.base_tools import ToolResult


def _intent(tool, args):
    return CommandNormalizer.normalize(tool, args).to_key()


def _record(policy, tool, args, result, *, success=True, category="",
            observed_failure=False, semantic_status="", changed=False,
            resolution_evidence="", block_reason="", subject_key=""):
    intent = _intent(tool, args)
    return policy.record_attempt(
        turn=len(policy.history) + 1,
        tool_name=tool,
        intent_key=intent,
        args_fingerprint=intent,
        success=success,
        execution_success=success,
        result_text=result,
        failure_category=category,
        observed_failure=observed_failure,
        semantic_status=semantic_status,
        resolution_evidence=resolution_evidence,
        workspace_before={"state": "before"},
        workspace_after={"state": "after" if changed else "before"},
        block_reason=block_reason,
        subject_key=subject_key,
    )


def test_agent_facade_and_adapter_share_one_policy_instance():
    from agent.mini_claude_agent import _RuntimeCompletionGate
    from core.loop_controller import RuntimePolicyAdapter

    policy = RuntimePolicy(AttemptHistory())
    controller = LoopController(policy=policy)
    observer = RuntimePolicyAdapter(policy)
    completion = _RuntimeCompletionGate(policy)

    assert controller.policy is policy
    assert controller.history is policy.history
    assert observer.policy is policy
    assert completion.policy is policy
    assert not hasattr(policy, "_replan_count")
    assert not hasattr(policy, "_completion_replan_count")


def test_positive_keywords_are_not_resolution_evidence():
    texts = (
        "healthy",
        "unhealthy",
        "not ready",
        "service is not healthy",
        "0 passed, 5 failed",
        "compiled with errors",
        "verification failed",
        "README says service should be READY",
        "READY",
    )
    for text in texts:
        policy = RuntimePolicy()
        event, _ = _record(policy, "bash", {"command": "echo text"}, text)
        assert event.verification_improved is False, text


def test_health_resolution_requires_probe_scoped_structured_observation():
    bad = ObservationNormalizer.normalize(
        "health_check", {}, ToolResult(
            content='{"status_code":503}', stdout='{"status_code":503}',
            execution_success=True, exit_code=0,
        )
    )
    good = ObservationNormalizer.normalize(
        "health_check", {}, ToolResult(
            content='{"healthy":true}', stdout='{"healthy":true}',
            execution_success=True, exit_code=0,
        )
    )
    assert bad.semantic_status == "UNHEALTHY"
    assert good.semantic_status == "HEALTHY"
    assert good.resolution_evidence

    policy = RuntimePolicy()
    _record(policy, "health_check", {}, bad.evidence_text, observed_failure=True,
            category="NETWORK_UNREACHABLE", semantic_status=bad.semantic_status)
    event, _ = _record(
        policy, "health_check", {}, good.evidence_text,
        semantic_status=good.semantic_status,
        resolution_evidence=good.resolution_evidence,
    )
    assert event.verification_improved is True
    assert event.resolution_key == _intent("health_check", {})
    assert policy.finalize().action is RuntimeDecision.ALLOW


def test_echo_ready_does_not_resolve_health_failure():
    policy = RuntimePolicy()
    _record(policy, "health_check", {}, "HTTP_503", observed_failure=True,
            category="NETWORK_UNREACHABLE", semantic_status="UNHEALTHY")
    event, _ = _record(policy, "bash", {"command": "echo READY"}, "READY")
    assert event.verification_improved is False
    assert policy.finalize().action is RuntimeDecision.REPLAN


def test_unrelated_workspace_mutation_does_not_erase_network_failure():
    policy = RuntimePolicy()
    _record(policy, "bash", {"command": "curl service"}, "Connection refused",
            success=False, category="NETWORK_UNREACHABLE", observed_failure=True,
            semantic_status="UNHEALTHY")
    _record(policy, "write_file", {"path": "report.md"}, "written", changed=True)
    decision = policy.finalize()
    assert decision.action is RuntimeDecision.REPLAN
    assert decision.action is not RuntimeDecision.ALLOW


def test_relevant_success_resolves_test_failure_but_readme_change_does_not():
    test_args = {"command": "pytest tests/test_app.py"}
    pytest_policy = RuntimePolicy()
    _record(pytest_policy, "bash", test_args, "10 failed", success=False,
            category="PROCESS_FAILURE")
    _record(pytest_policy, "edit_file", {"path": "app.py", "edits": [{"search": "x", "replace": "y"}]},
            "source changed", changed=True)
    event, _ = _record(pytest_policy, "bash", test_args, "0 failed", success=True)
    assert event.verification_improved is True
    assert pytest_policy.finalize().action is RuntimeDecision.ALLOW

    readme_policy = RuntimePolicy()
    _record(readme_policy, "bash", test_args, "10 failed", success=False,
            category="PROCESS_FAILURE")
    _record(readme_policy, "edit_file", {"path": "README.md", "edits": [{"search": "x", "replace": "y"}]},
            "README changed", changed=True)
    _record(readme_policy, "bash", test_args, "10 failed", success=False,
            category="PROCESS_FAILURE")
    assert readme_policy.finalize().action is RuntimeDecision.REPLAN


def test_replan_and_completion_decisions_are_replayable_from_history():
    policy = RuntimePolicy()
    args = {"command": "pytest"}
    for _ in range(3):
        _record(policy, "bash", args, "1 failed", success=False,
                category="PROCESS_FAILURE")
    recorded_before_finalize = policy.history.all()
    original = policy.finalize()

    replay_history = AttemptHistory()
    for event in recorded_before_finalize:
        replay_history.append(event)
    replay = RuntimePolicy(replay_history)
    assert replay.finalize().action is original.action
    assert [event.governance_decision for event in recorded_before_finalize] == [
        event.governance_decision for event in replay_history
    ]


def test_history_keeps_success_failure_and_blocked_order_without_duplicate_events():
    policy = RuntimePolicy()
    _record(policy, "bash", {"command": "echo ok"}, "ok")
    _record(policy, "bash", {"command": "pytest"}, "failed", success=False,
            category="PROCESS_FAILURE")
    _record(policy, "bash", {"command": "pytest"}, "blocked", success=False,
            block_reason="REPEATED_INTENT")
    events = policy.history.all()
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.status for event in events] == [
        AttemptStatus.SUCCESS, AttemptStatus.FAILURE, AttemptStatus.BLOCKED,
    ]


def test_subject_key_is_deterministic_for_cross_tool_services_and_scopes():
    assert CommandNormalizer.subject_key(
        "bash", {"command": "curl http://localhost:8080/health"}
    ) == "service://localhost:8080"
    assert CommandNormalizer.subject_key(
        "health_check", {"port": 8080}
    ) == "service://localhost:8080"
    assert CommandNormalizer.subject_key(
        "bash", {"command": "pytest tests/user"}
    ) == "test://pytest/tests/user"
    assert CommandNormalizer.subject_key(
        "bash", {"command": "pytest tests/order"}
    ) == "test://pytest/tests/order"


def test_resolution_matrix_has_no_false_resolve_and_only_known_boundaries():
    service = "service://localhost:8080"
    cases = []

    policy = RuntimePolicy()
    _record(policy, "bash", {"command": "curl http://localhost:8080/health"},
            "HTTP 503", success=True, category="NETWORK_UNREACHABLE",
            observed_failure=True, semantic_status="UNHEALTHY", subject_key=service)
    _record(policy, "health_check", {"port": 8080}, '{"healthy":true}',
            subject_key=service, semantic_status="HEALTHY",
            resolution_evidence="structured healthy probe")
    cases.append(("same service across tools", policy, True))

    policy = RuntimePolicy()
    _record(policy, "health_check", {"port": 8080}, '{"healthy":false}',
            category="NETWORK_UNREACHABLE", observed_failure=True,
            semantic_status="UNHEALTHY", subject_key=service)
    _record(policy, "bash", {"command": "curl http://localhost:8080/health"},
            "HTTP 200", subject_key=service, semantic_status="HEALTHY",
            resolution_evidence="structured HTTP 200 probe")
    cases.append(("same service reverse tools", policy, True))

    policy = RuntimePolicy()
    _record(policy, "bash", {"command": "curl http://localhost:8080/health"},
            "HTTP 503", category="NETWORK_UNREACHABLE", observed_failure=True,
            semantic_status="UNHEALTHY", subject_key=service)
    _record(policy, "health_check", {"port": 9090}, '{"healthy":true}',
            subject_key="service://localhost:9090", semantic_status="HEALTHY",
            resolution_evidence="structured healthy probe")
    cases.append(("different service", policy, False))

    def pytest_case(failing, succeeding, expected, label):
        p = RuntimePolicy()
        _record(p, "bash", {"command": f"pytest {failing}"}, "1 failed",
                success=False, category="PROCESS_FAILURE",
                subject_key=f"test://pytest/{failing}")
        _record(p, "bash", {"command": f"pytest {succeeding}"}, "0 failed",
                subject_key=f"test://pytest/{succeeding}")
        cases.append((label, p, expected))

    pytest_case("tests/user", "tests/order", False, "different pytest scope")
    p = RuntimePolicy()
    _record(p, "bash", {"command": "pytest tests/user"}, "1 failed",
            success=False, category="PROCESS_FAILURE",
            subject_key="test://pytest/tests/user")
    _record(p, "edit_file", {"path": "app.py", "edits": [{"search": "x", "replace": "y"}]},
            "changed", changed=True)
    _record(p, "bash", {"command": "pytest tests/user"}, "0 failed",
            subject_key="test://pytest/tests/user")
    cases.append(("same pytest scope", p, True))
    pytest_case("tests/user", "", False, "broader pytest scope")
    pytest_case("", "tests/order", False, "narrower pytest scope")

    p = RuntimePolicy()
    _record(p, "write_file", {"path": "protected/cache/output.json"},
            "Permission denied", success=False, category="PERMISSION_DENIED",
            observed_failure=True, semantic_status="RESOURCE_DENIED",
            subject_key="path://protected/cache/output.json")
    _record(p, "write_file", {"path": "workspace/report.md"}, "written",
            subject_key="path://workspace/report.md")
    cases.append(("different permission path", p, False))

    p = RuntimePolicy()
    _record(p, "write_file", {"path": "protected/output.json"},
            "Permission denied", success=False, category="PERMISSION_DENIED",
            observed_failure=True, semantic_status="RESOURCE_DENIED",
            subject_key="artifact://output.json")
    _record(p, "write_file", {"path": "workspace/output.json"}, "artifact valid",
            subject_key="artifact://output.json", resolution_evidence="artifact verified")
    cases.append(("same artifact moved to legal path", p, True))

    p = RuntimePolicy()
    _record(p, "bash", {"command": "pip install metrics-core"},
            "package not found", success=False, category="PACKAGE_NOT_FOUND",
            observed_failure=True, semantic_status="UNHEALTHY",
            subject_key="dependency://metrics-core")
    _record(p, "bash", {"command": "python fallback.py"}, "score=125",
            subject_key="dependency://metrics-core",
            resolution_evidence="fallback business command succeeded")
    cases.append(("dependency fallback with explicit subject", p, True))

    p = RuntimePolicy()
    _record(p, "bash", {"command": "pip install metrics-core"},
            "package not found", success=False, category="PACKAGE_NOT_FOUND",
            observed_failure=True, semantic_status="UNHEALTHY",
            subject_key="dependency://metrics-core")
    _record(p, "bash", {"command": "python fake_dependency.py"}, "echo ok")
    cases.append(("fake dependency fallback", p, False))

    p = RuntimePolicy()
    _record(p, "bash", {"command": "curl http://localhost:8080/health"},
            "HTTP 503", category="NETWORK_UNREACHABLE", observed_failure=True,
            semantic_status="UNHEALTHY", subject_key=service)
    _record(p, "read_file", {"path": "config.json"}, "config")
    _record(p, "list_files", {"path": "."}, "files")
    _record(p, "write_file", {"path": "report.md"}, "written")
    _record(p, "bash", {"command": "echo READY"}, "READY")
    cases.append(("ordinary diagnosis", p, False))

    for label, policy, expected_resolved in cases:
        actual_resolved = policy.finalize().action is RuntimeDecision.ALLOW
        assert actual_resolved is expected_resolved, label
