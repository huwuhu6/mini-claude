"""Regression checks that v2 trace annotations do not alter governance."""
from core.loop_controller import RuntimeDecision, RuntimePolicy
from core.runtime_context.observation import ObservationNormalizer
from core.tools.base_tools import ToolResult


def _run(policy, result_text, evidence_ids=()):
    event, decision = policy.record_attempt(
        turn=1,
        tool_name="bash",
        intent_key="bash:probe:service",
        args_fingerprint="args",
        success=False,
        result_text=result_text,
        failure_category="NETWORK_UNREACHABLE",
        observed_failure=True,
        semantic_status="UNHEALTHY",
        observation="HTTP_503",
        subject_key="service://fixture",
        evidence_ids=evidence_ids,
    )
    return decision.action, decision.reason, event


def test_evaluator_evidence_annotations_are_decision_neutral():
    plain = _run(RuntimePolicy(), "HTTP 503")
    annotated = _run(RuntimePolicy(), '{"status_code":503,"observation_id":"obs-000001"}', ("obs-000001",))
    assert plain[:2] == annotated[:2]
    assert annotated[2].evidence_ids == ("obs-000001",)
    assert annotated[2].governance_evidence_ids == ()


def test_normalizer_preserves_evaluator_observation_id_on_failure():
    evidence = ObservationNormalizer.normalize(
        "bash", {"command": "curl http://fixture/health"},
        ToolResult(
            content='{"status_code":503,"observation_id":"obs-000007"}',
            success=False,
            execution_success=False,
            exit_code=1,
        ),
    )
    assert evidence.observed_failure
    assert evidence.evidence_ids == ("obs-000007",)


def test_replan_and_hard_stop_sequence_is_unchanged_by_ids():
    def sequence(with_ids):
        policy = RuntimePolicy()
        actions = []
        for index in range(5):
            _, decision = policy.record_attempt(
                turn=index + 1, tool_name="bash", intent_key="bash:probe:service",
                args_fingerprint=str(index), success=False, result_text="HTTP 503",
                failure_category="NETWORK_UNREACHABLE", observed_failure=True,
                semantic_status="UNHEALTHY", observation="HTTP_503",
                subject_key="service://fixture",
                evidence_ids=((f"obs-{index + 1:06d}",) if with_ids else ()),
            )
            actions.append(decision.action)
        return actions

    assert sequence(False) == sequence(True)
    assert sequence(True)[-1] is RuntimeDecision.HARD_STOP
