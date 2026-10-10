"""Tool-call batches must close before the next compatible model request."""

import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from agent.mini_claude_agent import MiniClaudeAgent
from core.failure_intelligence import FailureAnalyzer
from core.features import FeatureDefinition, FeatureManager
from core.tools.registry import ToolRegistry, ToolSpec
from core.loop_controller import (
    RuntimeEscalationException,
    AttemptEvent,
    AttemptStatus,
    LoopController,
    RuntimeDecision,
    RuntimePolicy,
    RuntimePolicyAdapter,
)
from core.runtime_context.observation import ObservationNormalizer
from core.tools.base_tools import ToolResult
from providers.base import Message
from providers.deepseek import DeepseekProvider


def _call(call_id, arguments='{"patterns":["needle"]}', name="count_occurrences"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _response(content="", tool_calls=None, finish_reason=None):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
        }],
        "usage": {},
    }


class _MockCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def make_agent():
    def make(responses):
        agent = object.__new__(MiniClaudeAgent)
        provider = object.__new__(DeepseekProvider)
        provider.model = "test-model"
        provider.max_tokens = 64
        provider.temperature = 0
        provider.stream = False
        provider.base_url = "https://example.invalid/v1"
        provider.config = {"api_key": "local-test"}
        provider.last_error_diagnostic = {}
        completions = _MockCompletions(responses)
        provider.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )

        policy = RuntimePolicy()
        agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
        agent._get_llm_tools = lambda: []
        agent._check_auto_compress = lambda *_args, **_kwargs: False
        agent._get_dynamic_hot_context = lambda **_kwargs: ""
        agent._sync_compression_warning = lambda value: value
        agent._update_structured_memory = lambda *_args, **_kwargs: None
        agent._emit_ui_event = lambda *_args, **_kwargs: None
        agent._current_user_prompt = "test task"
        agent._workspace_confirmed = True
        agent.system_prompt = "test system prompt"
        agent.config = SimpleNamespace(llm=SimpleNamespace(
            provider="mock", model="test-model", max_tokens=64,
            temperature=0, stream=False,
        ))
        agent.messages = [Message(role="user", content="test task")]
        agent.runtime_context = SimpleNamespace(
            current_task_id=None,
            workspace_root=Path("."),
            cwd=Path("."),
            shell_session=SimpleNamespace(session_id="shell-test"),
        )
        agent.preflight = SimpleNamespace(to_dict=lambda: {})
        agent.runtime_policy = policy
        agent.loop_controller = LoopController(policy=policy)
        agent.runtime_observer = RuntimePolicyAdapter(policy)
        agent.workspace_state_guard = SimpleNamespace(
            reset=lambda: None,
            snapshot=lambda: {},
            is_write_operation=lambda *_args: False,
            mutation=lambda *_args: None,
            pending_write_message=lambda: None,
        )
        agent.completion_guard = SimpleNamespace(
            reset=lambda: None,
            check=lambda *_args: SimpleNamespace(
                should_terminate=False, should_replan=False,
                action=RuntimeDecision.ALLOW, reason="",
            ),
            message=lambda _decision: "",
        )
        agent.trace = MagicMock()
        agent.trace.start_task.return_value = "task-test"
        agent.recorded_events = []
        agent.session_recorder = SimpleNamespace(
            record=lambda event, **data: agent.recorded_events.append((event, data)),
        )
        agent.environment_blocker = SimpleNamespace(
            check_command=lambda *_args: None,
            classify_result=lambda *_args, **_kwargs: None,
        )
        agent.failure_analyzer = FailureAnalyzer()
        agent.observation_normalizer = ObservationNormalizer()
        agent.compressor = SimpleNamespace(estimate_prompt_tokens=lambda *_args, **_kwargs: 0)
        agent._ui_event_handler = None
        agent._last_assistant_note = None
        agent._compression_notice_pending = False
        agent.last_metrics = {}
        return agent, completions

    return make


def _governance_decision(action, sequence):
    event = AttemptEvent(
        sequence=sequence,
        turn=1,
        tool_name="count_occurrences",
        intent_key="test-intent",
        args_fingerprint="test-args",
        status=AttemptStatus.SUCCESS,
    )
    return SimpleNamespace(
        action=action,
        should_replan=action is RuntimeDecision.REPLAN,
        should_terminate=action is RuntimeDecision.HARD_STOP,
        reason="deterministic test decision",
        event=event,
        observation_changed=True,
        progress_detected=False,
        progress_reason=(),
        stagnation_reason=(),
        recovery_stage=SimpleNamespace(value="TEST"),
        open_blocker_count=0,
        oscillation_detected=False,
    )


def _queue_governance(agent, actions):
    pending = iter(actions)
    agent.runtime_observer.reset = lambda: None
    agent.runtime_observer.observe = lambda **kwargs: _governance_decision(
        next(pending, RuntimeDecision.ALLOW), kwargs["turn"],
    )


def _tool_messages(agent):
    return [message for message in agent.messages if message.role == "tool"]


def _assistant_tool_call_ids(message):
    return [call["id"] for call in message.tool_calls or []]


def test_feature_disabled_after_model_response_is_blocked_with_closed_tool_result(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call(
            "feature-call", '{"command":"echo ok"}', name="bash",
        )]),
        _response(content="done"),
    ])
    features = FeatureManager()
    features.register_feature(FeatureDefinition(name="bash", enabled=True))
    features.register_tool_for_feature("bash", "bash")
    registry = ToolRegistry()
    executed = []
    registry.register(ToolSpec(
        name="bash",
        description="run a command",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        handler=lambda command: executed.append(command) or ToolResult("ran"),
    ))
    agent.feature_manager = features
    agent.tool_registry = registry
    agent._get_llm_tools = lambda: features.filter_tools(registry.definitions())
    agent._execute_tool = MiniClaudeAgent._execute_tool.__get__(agent, MiniClaudeAgent)

    def disable_after_model_response(event, **_kwargs):
        if event == "tool_start":
            features.disable("bash")

    agent._emit_ui_event = disable_after_model_response

    result = agent._llm_tool_cycle(max_iterations=2)

    assert result == "done"
    assert "bash" in [tool["function"]["name"] for tool in completions.requests[0]["tools"]]
    assert executed == []
    assistant = next(
        message for message in agent.messages
        if message.role == "assistant" and message.tool_calls
    )
    results = _tool_messages(agent)
    assert _assistant_tool_call_ids(assistant) == ["feature-call"]
    assert [message.tool_call_id for message in results] == ["feature-call"]
    assert "所属功能当前未启用" in results[0].content
    events = [data for event, data in agent.recorded_events if event == "tool_result"]
    assert events[0]["outcome"] == "BLOCKED_FEATURE_DISABLED"
    assert events[0]["blocked"] is True


def test_runtime_control_exception_reaches_loop_without_becoming_tool_failure(make_agent):
    agent, _completions = make_agent([
        _response(tool_calls=[_call(
            "control-call", '{"command":"echo ok"}', name="bash",
        )]),
    ])
    features = FeatureManager()
    features.register_feature(FeatureDefinition(name="bash", enabled=True))
    features.register_tool_for_feature("bash", "bash")
    registry = ToolRegistry()

    def stop_before_result(command):
        raise RuntimeEscalationException("runtime stop")

    registry.register(ToolSpec(
        name="bash",
        description="run command",
        input_schema={"type": "object", "properties": {}},
        handler=stop_before_result,
    ))
    agent.feature_manager = features
    agent.tool_registry = registry
    agent._get_llm_tools = lambda: features.filter_tools(registry.definitions())
    agent._execute_tool = MiniClaudeAgent._execute_tool.__get__(agent, MiniClaudeAgent)

    result = agent._llm_tool_cycle(max_iterations=1)

    assert "runtime stop" in result
    tool_results = _tool_messages(agent)
    assert len(tool_results) == 1
    assert tool_results[0].tool_call_id == "control-call"
    assert "[UNKNOWN]" in tool_results[0].content
    events = [data for event, data in agent.recorded_events if event == "tool_result"]
    assert [event["outcome"] for event in events] == ["UNKNOWN"]
    assert agent.trace.end_task.call_args.args[0] == "CIRCUIT_BROKEN"


def test_same_response_duplicate_is_skipped_with_its_own_result(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("first"), _call("duplicate")]),
        _response(content="done"),
    ])
    executed = []
    agent._execute_tool = lambda name, args: executed.append((name, args)) or "counted"

    assert agent._llm_tool_cycle() == "done"

    assert len(executed) == 1
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["first", "duplicate"]
    assert "[SKIPPED_DUPLICATE]" in _tool_messages(agent)[1].content
    duplicate_result = next(
        data for event, data in agent.recorded_events
        if event == "tool_result" and data.get("call_id") == "duplicate"
    )
    assert duplicate_result["outcome"] == "SKIPPED_DUPLICATE"
    assert len(completions.requests) == 2


def test_replan_is_deferred_until_all_results_in_the_batch(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("first"), _call("second", '{"patterns":["other"]}')]),
        _response(content="done"),
    ])
    executed = []
    agent._execute_tool = lambda name, args: executed.append(args["patterns"][0]) or "ok"
    _queue_governance(agent, [RuntimeDecision.REPLAN, RuntimeDecision.ALLOW])

    assert agent._llm_tool_cycle() == "done"

    first_batch = agent.messages[1:4]
    assert [message.role for message in first_batch] == ["assistant", "tool", "tool"]
    assert [message.tool_call_id for message in first_batch[1:]] == ["first", "second"]
    assert agent.messages[4].role == "user"
    assert "Progress Governance" in agent.messages[4].content
    assert executed == ["needle", "other"]
    assert [item["role"] for item in completions.requests[1]["messages"]][-4:] == [
        "assistant", "tool", "tool", "user",
    ]


def test_invalid_arguments_get_a_result_and_later_calls_stay_linked(make_agent):
    agent, _ = make_agent([
        _response(tool_calls=[_call("invalid", "{"), _call("valid")]),
        _response(content="done"),
    ])
    executed = []
    agent._execute_tool = lambda name, args: executed.append(args) or "ok"

    assert agent._llm_tool_cycle() == "done"

    assert len(executed) == 1
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["invalid", "valid"]
    assert "JSON 无效" in _tool_messages(agent)[0].content
    assert [message.role for message in agent.messages[1:4]] == ["assistant", "tool", "tool"]


def test_policy_block_has_explicit_result_and_does_not_skip_later_call(make_agent):
    agent, _ = make_agent([
        _response(tool_calls=[_call("blocked"), _call("allowed", '{"patterns":["other"]}')]),
        _response(content="done"),
    ])
    executed = []
    checks = []

    def check(name, args):
        checks.append(name)
        return "策略拒绝，未执行" if len(checks) == 1 else None

    agent.loop_controller.check = check
    agent._execute_tool = lambda name, args: executed.append(name) or "ok"

    assert agent._llm_tool_cycle() == "done"

    assert len(executed) == 1
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["blocked", "allowed"]
    assert "策略拒绝" in _tool_messages(agent)[0].content


def test_executed_failure_keeps_its_result_and_is_not_recorded_as_blocked(make_agent):
    agent, _ = make_agent([
        _response(tool_calls=[_call("failed"), _call("later", '{"patterns":["other"]}')]),
        _response(content="done"),
    ])
    executed = []

    def execute(name, args):
        executed.append(args["patterns"][0])
        if len(executed) == 1:
            return ToolResult("tool failed after execution", success=False, execution_success=False)
        return "ok"

    agent._execute_tool = execute

    assert agent._llm_tool_cycle() == "done"

    assert executed == ["needle", "other"]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["failed", "later"]
    failure_result = next(
        data for event, data in agent.recorded_events
        if event == "tool_result" and data.get("call_id") == "failed"
    )
    assert failure_result["outcome"] == "EXECUTED_FAILURE"
    assert failure_result["blocked"] is False


def test_replan_after_policy_block_waits_until_remaining_result(make_agent):
    agent, _ = make_agent([
        _response(tool_calls=[_call("blocked"), _call("later", '{"patterns":["other"]}')]),
        _response(content="done"),
    ])
    checks = []
    executed = []

    def check(name, args):
        checks.append(name)
        return "策略拒绝，未执行" if len(checks) == 1 else None

    agent.loop_controller.check = check
    agent._execute_tool = lambda name, args: executed.append(args["patterns"][0]) or "ok"
    _queue_governance(agent, [RuntimeDecision.REPLAN, RuntimeDecision.ALLOW])

    assert agent._llm_tool_cycle() == "done"

    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert agent.messages[4].role == "user"
    assert "Progress Governance" in agent.messages[4].content
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["blocked", "later"]
    assert executed == ["other"]


def test_hard_stop_closes_remaining_calls_without_executing_them(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[
            _call("first"),
            _call("second", '{"patterns":["second"]}'),
            _call("third", '{"patterns":["third"]}'),
        ]),
        _response(content="must not be requested"),
    ])
    executed = []
    agent._execute_tool = lambda name, args: executed.append(args["patterns"][0]) or "first result"
    _queue_governance(agent, [RuntimeDecision.HARD_STOP])

    result = agent._llm_tool_cycle()

    assert result == "first result"
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:5]] == [
        "assistant", "tool", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["first", "second", "third"]
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[1].content
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[2].content
    outcomes = {
        data["call_id"]: data["outcome"]
        for event, data in agent.recorded_events
        if event == "tool_result"
    }
    assert outcomes == {
        "first": "EXECUTED_SUCCESS",
        "second": "NOT_EXECUTED",
        "third": "NOT_EXECUTED",
    }


def test_shell_cancellation_keeps_cancelled_result_and_skips_remaining_calls(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("cancelled"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])
    executed = []

    def execute(name, args):
        executed.append(args["patterns"][0])
        return ToolResult("命令执行已取消。", success=False, cancelled=True)

    agent._execute_tool = execute
    result = agent._llm_tool_cycle()

    assert "已取消" in result
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["cancelled", "later"]
    assert "部分副作用未知" in _tool_messages(agent)[0].content
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[1].content
    cancelled_result = next(
        data for event, data in agent.recorded_events
        if event == "tool_result" and data.get("call_id") == "cancelled"
    )
    assert cancelled_result["outcome"] == "CANCELLED"


def test_interrupt_during_dispatch_marks_current_call_unknown(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("running"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])
    executed = []

    def interrupt(name, args):
        executed.append(args["patterns"][0])
        raise KeyboardInterrupt()

    agent._execute_tool = interrupt

    result = agent._llm_tool_cycle()

    assert "停止" in result
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["running", "later"]
    assert "[UNKNOWN]" in _tool_messages(agent)[0].content
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[1].content


def test_dispatch_exception_marks_current_call_unknown(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("raised"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])
    executed = []

    def fail(name, args):
        executed.append(args["patterns"][0])
        raise RuntimeError("dispatch failed")

    agent._execute_tool = fail

    result = agent._llm_tool_cycle()

    assert "dispatch failed" in result
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["raised", "later"]
    assert "[UNKNOWN]" in _tool_messages(agent)[0].content
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[1].content
    outcomes = {
        data["call_id"]: data["outcome"]
        for event, data in agent.recorded_events
        if event == "tool_result"
    }
    assert outcomes == {"raised": "UNKNOWN", "later": "NOT_EXECUTED"}


def test_pre_dispatch_exception_marks_calls_not_executed(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("pre"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])
    executed = []
    agent._tool_event_summary = lambda *_args: (_ for _ in ()).throw(
        RuntimeError("tool setup failed")
    )
    agent._execute_tool = lambda name, args: executed.append(args) or "unexpected"

    result = agent._llm_tool_cycle()

    assert "tool setup failed" in result
    assert executed == []
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["pre", "later"]
    assert all("[NOT_EXECUTED]" in message.content for message in _tool_messages(agent))
    recorded_results = {
        data["call_id"]: data["outcome"]
        for event, data in agent.recorded_events
        if event == "tool_result"
    }
    assert recorded_results == {"pre": "NOT_EXECUTED", "later": "NOT_EXECUTED"}


def test_post_dispatch_exception_keeps_received_result_and_closes_remaining(
    make_agent, monkeypatch,
):
    agent, completions = make_agent([
        _response(tool_calls=[_call("completed"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])
    executed = []

    def execute(name, args):
        executed.append(args["patterns"][0])
        return "real tool result"

    def fail_normalization(*_args):
        raise RuntimeError("post-dispatch processing failed")

    monkeypatch.setattr(ObservationNormalizer, "normalize", fail_normalization)
    agent._execute_tool = execute

    result = agent._llm_tool_cycle()

    assert "post-dispatch processing failed" in result
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.role for message in agent.messages[1:4]] == [
        "assistant", "tool", "tool",
    ]
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["completed", "later"]
    assert _tool_messages(agent)[0].content == "real tool result"
    assert "[NOT_EXECUTED]" in _tool_messages(agent)[1].content
    completed_result = next(
        data for event, data in agent.recorded_events
        if event == "tool_result" and data.get("call_id") == "completed"
    )
    assert completed_result["outcome"] == "EXECUTED_SUCCESS"
    assert completed_result["result"] == "real tool result"


def test_normal_calls_keep_order_and_next_openai_request_has_closed_batch(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("one"), _call("two", '{"patterns":["second"]}')]),
        _response(content="done"),
    ])
    executed = []
    agent._execute_tool = lambda name, args: executed.append(args["patterns"][0]) or "ok"

    assert agent._llm_tool_cycle() == "done"

    assert executed == ["needle", "second"]
    payload = completions.requests[1]["messages"]
    assistant_index = next(
        index for index, message in enumerate(payload)
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assistant = payload[assistant_index]
    results = payload[assistant_index + 1:assistant_index + 3]
    assert [call["id"] for call in assistant["tool_calls"]] == ["one", "two"]
    assert [result["tool_call_id"] for result in results] == ["one", "two"]
    assert [result["role"] for result in results] == ["tool", "tool"]
