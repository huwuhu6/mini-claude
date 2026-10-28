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
from core.runtime_context.workspace_authority import WorkspaceAuthority
from core.tools.base_tools import ToolResult
from core.session_recorder import SessionRecorder
from models.todo import TodoManager
from models.config import LLMConfig
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
        agent.config = SimpleNamespace(llm=LLMConfig(
            provider="mock", model="test-model", max_tokens=64,
            temperature=0, stream=False,
            output_limit_recovery={"enabled": False},
        ))
        agent.messages = [Message(role="user", content="test task")]
        agent.runtime_context = SimpleNamespace(
            current_task_id=None,
            workspace_root=Path("."),
            cwd=Path("."),
            shell_session=SimpleNamespace(session_id="shell-test"),
        )
        agent.preflight = SimpleNamespace(to_dict=lambda: {})
        agent.workspace_authority = WorkspaceAuthority(Path("."))
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
        agent.session_recorder.provider_request = SessionRecorder.provider_request.__get__(agent.session_recorder)
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
    agent, completions = make_agent([
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
    assert agent._run_state.status.value == "terminated"
    assert agent._run_state.tool_batch.results["control-call"].outcome == "UNKNOWN"
    assert len(completions.requests) == 1
    assert "runtime stop" in agent._continue_run()
    assert agent._run_state.tool_batch.results["control-call"].outcome == "UNKNOWN"
    assert len(completions.requests) == 1


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
    state = agent._run_state
    assert state.status.value == "failed"
    assert state.tool_batch.status.value == "failed"
    assert state.tool_batch.next_call_index == 2
    assert state.tool_batch.pending_call_ids == []
    assert state.tool_batch.results["completed"].content == "real tool result"
    assert state.tool_batch.results["completed"].outcome == "EXECUTED_SUCCESS"
    assert state.tool_batch.results["later"].outcome == "NOT_EXECUTED"
    assert agent._continue_run() == result
    assert executed == ["needle"]
    assert len(completions.requests) == 1


def test_tool_result_append_failure_preserves_known_dispatch_result(
    make_agent, monkeypatch,
):
    from agent.mini_claude_agent import Compressor

    monkeypatch.setattr(
        Compressor, "_clean_tool_chains", staticmethod(lambda messages: messages),
    )
    agent, completions = make_agent([
        _response(tool_calls=[_call("completed"), _call("later", '{"patterns":["later"]}')]),
        _response(content="must not be requested"),
    ])

    class FailFirstToolAppend(list):
        def __init__(self, values):
            super().__init__(values)
            self.failed = False

        def append(self, message):
            if message.role == "tool" and not self.failed:
                self.failed = True
                raise OSError("injected Tool Result append failure")
            super().append(message)

    agent.messages = FailFirstToolAppend(agent.messages)
    executed = []
    agent._execute_tool = lambda _name, args: executed.append(
        args["patterns"][0]
    ) or "known result"

    result = agent._llm_tool_cycle()

    tool_messages = _tool_messages(agent)
    state = agent._run_state
    assert "injected Tool Result append failure" in result
    assert executed == ["needle"]
    assert len(completions.requests) == 1
    assert [message.tool_call_id for message in tool_messages] == [
        "completed", "later",
    ]
    assert tool_messages[0].content == "known result"
    assert state.tool_batch.results["completed"].outcome == "EXECUTED_SUCCESS"
    assert state.tool_batch.results["later"].outcome == "NOT_EXECUTED"
    recovered_events = [
        data for event, data in agent.recorded_events
        if event == "tool_result" and data.get("call_id") == "completed"
    ]
    assert len(recovered_events) == 1
    assert recovered_events[0]["outcome"] == "EXECUTED_SUCCESS"
    assert state.tool_batch.next_call_index == 2
    assert state.tool_batch.pending_call_ids == []
    assert state.status.value == "failed"
    assert agent._continue_run() == result
    assert executed == ["needle"]
    assert len(completions.requests) == 1


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


def test_paused_batch_continues_from_next_call_without_restarting_run(make_agent, monkeypatch):
    from agent.mini_claude_agent import Compressor

    agent, completions = make_agent([
        _response(content="I will use a tool."),
        _response(tool_calls=[
            _call("A", '{"label":"A"}'),
            _call("B", '{"label":"B"}'),
            _call("C", '{"label":"C"}'),
        ]),
        _response(content="done"),
    ])
    agent.messages = []
    todo = TodoManager()
    reset_calls = []
    todo_reset = todo.reset

    def count_reset():
        reset_calls.append(True)
        todo_reset()

    todo.reset = count_reset
    agent.todo = todo
    note = SimpleNamespace(content="note before pause")
    agent.agent_note = note
    _queue_governance(agent, [
        RuntimeDecision.REPLAN,
        RuntimeDecision.ALLOW,
        RuntimeDecision.ALLOW,
    ])
    executed = []
    cleaned_batches = []
    clean_tool_chains = Compressor._clean_tool_chains

    def track_clean(messages):
        for index, message in enumerate(messages):
            if message.role != "assistant" or not message.tool_calls:
                continue
            call_ids = {call["id"] for call in message.tool_calls}
            result_ids = {
                item.tool_call_id
                for item in messages[index + 1:]
                if item.role == "tool" and item.tool_call_id
            }
            assert call_ids <= result_ids
            cleaned_batches.append(call_ids)
        return clean_tool_chains(messages)

    monkeypatch.setattr(Compressor, "_clean_tool_chains", staticmethod(track_clean))

    def execute(_name, args):
        label = args["label"]
        executed.append(label)
        if label == "A":
            note.content = "note preserved across pause"
            todo.update([{
                "content": "Keep this plan while paused",
                "status": "in_progress",
                "activeForm": "Keeping the plan",
            }])
            agent.runtime_policy.history.record(
                turn=1,
                tool_name="pause_marker",
                intent_key="pause-marker",
                args_fingerprint="pause-marker",
                status=AttemptStatus.SUCCESS,
            )
            agent._request_run_pause()
        return ToolResult(content=f"result-{label}", success=True)

    agent._execute_tool = execute
    pause_result = agent.run("test task", require_tool_call=True)

    assert "暂停" in pause_result
    state = agent._run_state
    assert state.status.value == "paused"
    assert state.model_turns_used == 2
    assert state.no_tool_retry_count == 1
    assert state.require_tool_call is True
    assert state.tool_call_seen is True
    assert state.tool_batch.next_call_index == 1
    assert list(state.tool_batch.results) == ["A"]
    assert executed == ["A"]
    assert len(completions.requests) == 2
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["A"]
    assert agent.todo.items[0]["content"] == "Keep this plan while paused"
    assert agent.agent_note is note
    assert agent.agent_note.content == "note preserved across pause"
    assert reset_calls == [True]
    assert cleaned_batches == []
    assert agent.trace.end_task.call_count == 0
    metrics_identity = id(agent.last_metrics)
    attempt_history = agent.runtime_policy.history.all()
    trace_task_identity = agent.trace.current_task

    final_result = agent._continue_run()

    assert final_result == "done"
    assert executed == ["A", "B", "C"]
    assert len(completions.requests) == 3
    assert state.model_turns_used == 3
    assert agent._run_state is state
    assert id(agent.last_metrics) == metrics_identity
    assert agent.todo.items[0]["content"] == "Keep this plan while paused"
    assert agent.agent_note is note
    assert agent.agent_note.content == "note preserved across pause"
    assert reset_calls == [True]
    assert agent.runtime_policy.history.all()[:len(attempt_history)] == attempt_history
    assert agent.trace.start_task.call_count == 1
    assert agent.trace.current_task is trace_task_identity
    assert agent.trace.end_task.call_count == 1
    assert state.run_id != state.trace_task_id
    assert state.trace_task_id == "task-test"
    assert agent.trace.start_task.call_args.kwargs.get("task_id") != state.run_id
    assert state.tool_batch.status.value == "completed"
    assert state.status.value == "completed"
    assert len(cleaned_batches) == 2
    user_messages = [message.content for message in agent.messages if message.role == "user"]
    assert user_messages.count("test task") == 1
    assert sum("不要只描述计划" in content for content in user_messages) == 1
    assert sum("重新规划" in content for content in user_messages) == 1

    payload = completions.requests[2]["messages"]
    assistant_index = next(
        index for index, message in enumerate(payload)
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assistant = payload[assistant_index]
    results = payload[assistant_index + 1:assistant_index + 4]
    assert [call["id"] for call in assistant["tool_calls"]] == ["A", "B", "C"]
    assert [result["tool_call_id"] for result in results] == ["A", "B", "C"]
    assert [result["content"] for result in results] == [
        "result-A", "result-B", "result-C",
    ]
    assert payload[assistant_index + 4]["role"] == "user"
    assert "重新规划" in payload[assistant_index + 4]["content"]

    assert agent._continue_run() == "done"
    assert len(completions.requests) == 3
    assert executed == ["A", "B", "C"]
    assert [event for event, _data in agent.recorded_events].count("run_started") == 1
    assert [event for event, _data in agent.recorded_events].count("run_continued") == 1
    assert [event for event, _data in agent.recorded_events].count("final") == 1
    trace_link = next(
        data for event, data in agent.recorded_events if event == "run_trace_link"
    )
    assert trace_link == {
        "run_id": state.run_id,
        "trace_task_id": state.trace_task_id,
    }


def test_pause_after_final_call_waits_before_next_model_request(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("only")]),
        _response(content="done"),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW])
    executed = []

    def execute(_name, _args):
        executed.append("only")
        agent._request_run_pause()
        return "one result"

    agent._execute_tool = execute

    pause_result = agent.run("test task")

    assert "暂停" in pause_result
    assert len(completions.requests) == 1
    assert executed == ["only"]
    assert agent._run_state.status.value == "paused"
    assert agent._run_state.tool_batch.status.value == "completed"
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["only"]

    assert agent._continue_run() == "done"

    assert len(completions.requests) == 2
    assert executed == ["only"]
    assert agent._run_state.status.value == "completed"


def test_new_run_is_rejected_while_a_tool_batch_is_paused(make_agent):
    agent, _completions = make_agent([
        _response(tool_calls=[_call("A"), _call("B", '{"patterns":["second"]}')]),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW] * 2)
    executed = []

    def execute(_name, args):
        executed.append(args["patterns"][0])
        agent.todo.update([{
            "content": "Preserve paused todo",
            "status": "in_progress",
            "activeForm": "Preserving paused todo",
        }])
        agent._request_run_pause()
        return "ok"

    agent._execute_tool = execute
    agent.run("first task")
    state = agent._run_state
    message_count = len(agent.messages)
    todo_items = list(agent.todo.items)

    result = agent.run("second task")

    assert "尚未完成" in result
    assert agent._run_state is state
    assert len(agent.messages) == message_count
    assert agent.todo.items == todo_items
    assert executed == ["needle"]


def test_pending_batch_disables_automatic_and_manual_compression(make_agent):
    from cli.commands import _clear, _compact

    agent, _completions = make_agent([_response(tool_calls=[_call("A"), _call("B")])])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW] * 2)
    compress_calls = []
    agent.feature_manager = SimpleNamespace(is_enabled=lambda _name: True)
    agent.compressor = SimpleNamespace(
        compress=lambda messages: compress_calls.append("manual") or messages,
        estimate_tokens=lambda _messages: 0,
        estimate_prompt_tokens=lambda *_args, **_kwargs: 0,
        should_compress=lambda *_args, **_kwargs: True,
        should_microcompact=lambda *_args, **_kwargs: False,
        has_microcompact_work=lambda _messages: True,
    )
    agent._execute_tool = lambda *_args: (
        agent._request_run_pause() or "A result"
    )

    agent.run("test task")
    assert agent._check_auto_compress(999999) is False
    assert "未完成" in _compact(agent, [], {})
    messages_before_clear = list(agent.messages)
    assert "未完成" in _clear(agent, [], {})
    assert agent.messages == messages_before_clear
    assert compress_calls == []
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["A"]


def test_duplicate_signature_survives_pause_and_is_not_redispatched(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[
            _call("A", '{"patterns":["same"]}'),
            _call("B", '{"patterns":["same"]}'),
        ]),
        _response(content="done"),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW])
    executed = []

    def execute(_name, args):
        executed.append(args["patterns"][0])
        agent._request_run_pause()
        return "first result"

    agent._execute_tool = execute
    agent.run("test task")
    assert agent._run_state.tool_batch.seen_signatures
    assert agent._run_state.tool_batch.pending_call_ids == ["B"]

    assert agent._continue_run() == "done"

    assert executed == ["same"]
    assert len(completions.requests) == 2
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["A", "B"]
    assert "[SKIPPED_DUPLICATE]" in _tool_messages(agent)[1].content
    assert agent._run_state.tool_batch.results["B"].outcome == "SKIPPED_DUPLICATE"


def test_resumed_calls_recheck_current_feature_capability(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[
            _call("A", '{"command":"A"}', name="bash"),
            _call("B", '{"command":"B"}', name="bash"),
            _call("C", '{"command":"C"}', name="bash"),
        ]),
        _response(content="done"),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    features = FeatureManager()
    features.register_feature(FeatureDefinition(name="bash", enabled=True))
    features.register_tool_for_feature("bash", "bash")
    registry = ToolRegistry()
    executed = []

    def execute(command):
        executed.append(command)
        if command == "A":
            features.disable("bash")
            agent._request_run_pause()
        return ToolResult(content=f"result-{command}", success=True)

    registry.register(ToolSpec(
        name="bash",
        description="mock shell",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        handler=execute,
    ))
    agent.feature_manager = features
    agent.tool_registry = registry
    agent._get_llm_tools = lambda: features.filter_tools(registry.definitions())
    agent._execute_tool = MiniClaudeAgent._execute_tool.__get__(agent, MiniClaudeAgent)
    _queue_governance(agent, [RuntimeDecision.ALLOW] * 3)

    agent.run("test task")
    assert executed == ["A"]
    assert agent._run_state.tool_batch.pending_call_ids == ["B", "C"]

    assert agent._continue_run() == "done"

    assert executed == ["A"]
    assert len(completions.requests) == 2
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["A", "B", "C"]
    assert agent._run_state.tool_batch.results["A"].outcome == "EXECUTED_SUCCESS"
    assert agent._run_state.tool_batch.results["B"].outcome == "BLOCKED_FEATURE_DISABLED"
    assert agent._run_state.tool_batch.results["C"].outcome == "BLOCKED_FEATURE_DISABLED"


def test_pending_batch_finishes_without_provider_before_next_model_turn(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[
            _call("A", '{"label":"A"}'),
            _call("B", '{"label":"B"}'),
        ]),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW] * 2)
    executed = []

    def execute(_name, args):
        executed.append(args["label"])
        if args["label"] == "A":
            agent._request_run_pause()
        return f"result-{args['label']}"

    agent._execute_tool = execute
    agent.run("test task")
    agent.provider_manager.get_primary_provider = lambda: None

    result = agent._continue_run()

    assert "没有可用的 LLM 提供者" in result
    assert executed == ["A", "B"]
    assert len(completions.requests) == 1
    assert [message.tool_call_id for message in _tool_messages(agent)] == ["A", "B"]
    assert agent._run_state.tool_batch.status.value == "completed"
    assert agent._continue_run() == result
    assert executed == ["A", "B"]


def test_separate_runs_do_not_share_batch_cursor_or_tool_call_identity(make_agent):
    agent, completions = make_agent([
        _response(tool_calls=[_call("reused-id")]),
        _response(content="first done"),
        _response(tool_calls=[_call("reused-id")]),
        _response(content="second done"),
    ])
    agent.messages = []
    agent.todo = TodoManager()
    _queue_governance(agent, [RuntimeDecision.ALLOW, RuntimeDecision.ALLOW])
    executed = []
    agent._execute_tool = lambda _name, _args: executed.append("call") or "ok"

    assert agent.run("first task") == "first done"
    first_state = agent._run_state
    first_batch = first_state.tool_batch
    assert first_batch.next_call_index == 1

    assert agent.run("second task") == "second done"
    second_state = agent._run_state

    assert second_state.run_id != first_state.run_id
    assert second_state.tool_batch is not first_batch
    assert second_state.tool_batch.next_call_index == 1
    assert first_batch.results["reused-id"].content == "ok"
    assert second_state.tool_batch.results["reused-id"].content == "ok"
    assert executed == ["call", "call"]
    assert len(completions.requests) == 4


@pytest.mark.parametrize("replan", [True, False])
def test_last_call_pause_finishes_replan_before_next_request(make_agent, replan):
    agent, completions = make_agent([
        _response(tool_calls=[_call("last")]), _response(content="done"),
    ])
    _queue_governance(agent, [RuntimeDecision.REPLAN if replan else RuntimeDecision.ALLOW])
    executions = []
    def execute(_name, _args):
        executions.append("last")
        agent._request_run_pause()
        return "last-result"
    agent._execute_tool = execute
    agent._llm_tool_cycle()
    batch = agent._run_state.tool_batch
    assert batch.next_call_index == 1
    assert batch.results["last"].content == "last-result"
    assert len(completions.requests) == 1
    assert agent._continue_run() == "done"
    assert agent._continue_run() == "done"
    payload = completions.requests[1]["messages"]
    tool_index = next(i for i, m in enumerate(payload) if m.get("tool_call_id") == "last")
    replans = [m for m in payload if "Progress Governance" in (m.get("content") or "")]
    assert len(replans) == int(replan)
    if replan:
        assert payload[tool_index + 1] == replans[0]
    assert batch.pending_replan_messages == []
    assert executions == ["last"]
    assert len(completions.requests) == 2


@pytest.mark.parametrize("entry", ["run", "cycle", "continue"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_initialization_failure_has_recoverable_lifecycle(make_agent, entry, error_type):
    agent, completions = make_agent([
        _response(tool_calls=[_call("A"), _call("B", '{"patterns":["B"]}')]),
        _response(content="done"),
    ])
    agent.todo = TodoManager()
    executed = []
    def execute(_name, args):
        executed.append(args["patterns"][0])
        if len(executed) == 1:
            agent._request_run_pause()
        return "result"
    agent._execute_tool = execute
    if entry == "continue":
        agent._llm_tool_cycle()
        original = agent._run_state
        before = list(agent.messages)
    def fail():
        raise error_type("initialization interrupted")
    agent._get_llm_tools = fail
    with pytest.raises(error_type, match="initialization interrupted"):
        if entry == "run":
            agent.run("new task")
        elif entry == "cycle":
            agent._llm_tool_cycle()
        else:
            agent._continue_run()
    state = agent._run_state
    assert state.status.value == ("paused" if entry == "continue" else
                                  "cancelled" if error_type is KeyboardInterrupt else "failed")
    if entry == "continue":
        assert state is original
        assert state.tool_batch.next_call_index == 1
        assert state.tool_batch.status.value == "paused"
        assert agent.messages == before
        agent._get_llm_tools = lambda: []
        assert agent._continue_run() == "done"
        assert executed == ["needle", "B"]
        assert len(completions.requests) == 2
    else:
        assert not agent.has_active_run
        assert len(completions.requests) == 0


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("duplicate", [True, False])
@pytest.mark.parametrize("after_append", [False, True])
def test_append_failure_preserves_only_current_call_result(make_agent, monkeypatch, error_type, duplicate, after_append):
    from agent.mini_claude_agent import Compressor
    monkeypatch.setattr(Compressor, "_clean_tool_chains", staticmethod(lambda messages: messages))
    calls = [_call("A"), _call("B", '{"patterns":["needle"]}' if duplicate else '{"patterns":["B"]}'),
             _call("C", '{"patterns":["C"]}')]
    agent, completions = make_agent([_response(tool_calls=calls)])
    class FailAppend(list):
        failed = False
        def append(self, message):
            if message.role == "tool" and message.tool_call_id == "B" and not self.failed:
                self.failed = True
                if after_append:
                    super().append(message)
                raise error_type("append interrupted")
            super().append(message)
    agent.messages = FailAppend(agent.messages)
    executed = []
    agent._execute_tool = lambda _name, args: executed.append(args["patterns"][0]) or f"actual-{args['patterns'][0]}"
    agent._llm_tool_cycle()
    state = agent._run_state
    messages = _tool_messages(agent)
    assert [m.tool_call_id for m in messages] == ["A", "B", "C"]
    assert messages[0].content == "actual-needle"
    assert messages[1].content.startswith("[SKIPPED_DUPLICATE]") if duplicate else messages[1].content == "actual-B"
    assert state.tool_batch.results["B"].content == messages[1].content
    assert state.tool_batch.results["B"].outcome == ("SKIPPED_DUPLICATE" if duplicate else "EXECUTED_SUCCESS")
    assert state.tool_batch.results["C"].outcome == "NOT_EXECUTED"
    assert executed == (["needle"] if duplicate else ["needle", "B"])
    assert state.status.value == ("cancelled" if error_type is KeyboardInterrupt else "failed")
    assert len(completions.requests) == 1
    agent._continue_run()
    assert len(_tool_messages(agent)) == 3


@pytest.mark.parametrize("inside_handler", [True, False])
def test_type_error_preserves_handler_entry_outcome(make_agent, inside_handler):
    arguments = '{"path":"sample"}' if inside_handler else '{"unexpected":1}'
    agent, completions = make_agent([
        _response(tool_calls=[_call("read", arguments, "read_file")]),
        _response(content="done"),
    ])
    calls = []
    def handler(path):
        calls.append(path)
        raise TypeError("handler failed after entry")
    agent.feature_manager = FeatureManager()
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.register(ToolSpec("read_file", "", {}, handler))
    agent._llm_tool_cycle()
    assert calls == (["sample"] if inside_handler else [])
    result = agent._run_state.tool_batch.results["read"]
    assert result.outcome == ("EXECUTED_FAILURE" if inside_handler else "INVALID_ARGUMENTS")
    assert ("参数无效" in result.content) is (not inside_handler)
    assert len(_tool_messages(agent)) == 1
    assert len(completions.requests) == 2


@pytest.mark.parametrize("entry,site", [
    ("run", "todo"), ("run", "run_started"), ("cycle", "trace_start"),
    ("cycle", "policy_reset"), ("continue", "run_continued"),
])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_initialization_guards_cover_other_bootstrap_operations(make_agent, entry, site, error_type):
    agent, completions = make_agent([
        _response(tool_calls=[_call("A"), _call("B", '{"patterns":["B"]}')]),
        _response(content="done"),
    ])
    agent.todo = TodoManager()
    executed = []
    def execute(_name, args):
        executed.append(args["patterns"][0])
        if len(executed) == 1:
            agent._request_run_pause()
        return "real-result"
    agent._execute_tool = execute
    if entry == "continue":
        agent._llm_tool_cycle()
        state = agent._run_state
        budget = state.model_turns_used
    def fail(*_args, **_kwargs):
        raise error_type("bootstrap fault")
    if site == "todo":
        agent.todo.reset = fail
    elif site == "trace_start":
        agent.trace.start_task.side_effect = fail
    elif site == "policy_reset":
        agent.runtime_policy.reset = fail
    else:
        original_record = agent.session_recorder.record
        def record(event, **data):
            if event == site:
                fail()
            original_record(event, **data)
        agent.session_recorder.record = record
    with pytest.raises(error_type, match="bootstrap fault"):
        if entry == "run":
            agent.run("new task")
        elif entry == "cycle":
            agent._llm_tool_cycle()
        else:
            agent._continue_run()
    state = agent._run_state
    if entry == "continue":
        assert state.status.value == "paused"
        assert state.model_turns_used == budget
        assert state.tool_batch.next_call_index == 1
        assert executed == ["needle"]
        agent.session_recorder.record = original_record
        assert agent._continue_run() == "done"
        assert executed == ["needle", "B"]
        assert agent.trace.start_task.call_count == 1
    else:
        assert state.status.value == ("failed" if error_type is RuntimeError else "cancelled")
        assert not agent.has_active_run
        assert executed == []
        assert len(completions.requests) == 0
        assert agent.trace.end_task.call_count == int(site == "policy_reset")


@pytest.mark.parametrize("after_append", [True, False])
def test_replan_finalization_write_fault_does_not_duplicate_feedback(make_agent, after_append):
    agent, completions = make_agent([
        _response(tool_calls=[_call("A")]), _response(content="done"),
    ])
    _queue_governance(agent, [RuntimeDecision.REPLAN])
    executions = []
    def execute(_name, _args):
        executions.append("A")
        agent._request_run_pause()
        return "result-A"
    agent._execute_tool = execute
    agent._llm_tool_cycle()
    batch = agent._run_state.tool_batch
    assert batch.status.value == "completed"
    assert batch.finalized is False
    class FaultOnReplan(list):
        failed = False
        def append(self, message):
            if message.role == "user" and not self.failed:
                self.failed = True
                if after_append:
                    super().append(message)
                raise KeyboardInterrupt("replan append")
            super().append(message)
    agent.messages = FaultOnReplan(agent.messages)
    with pytest.raises(KeyboardInterrupt, match="replan append"):
        agent._continue_run()
    assert agent._run_state.status.value == "paused"
    assert len(batch.pending_replan_messages) == int(not after_append)
    assert len(completions.requests) == 1
    assert agent._continue_run() == "done"
    assert batch.finalized is True
    assert sum("Progress Governance" in (m.content or "") for m in agent.messages) == 1
    assert len(_tool_messages(agent)) == 1
    assert executions == ["A"]
    assert agent._continue_run() == "done"


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_duplicate_call_recorder_fault_retains_current_skipped_result(make_agent, error_type):
    agent, completions = make_agent([_response(tool_calls=[_call("A"), _call("B"), _call("C")])])
    executions = []
    agent._execute_tool = lambda _name, _args: executions.append("A") or "real-A"
    original = agent.session_recorder.record
    failed = False
    def record(event, **data):
        nonlocal failed
        if event == "tool_call" and data.get("call_id") == "B" and not failed:
            failed = True
            raise error_type("duplicate record fault")
        original(event, **data)
    agent.session_recorder.record = record
    agent._llm_tool_cycle()
    results = agent._run_state.tool_batch.results
    assert results["A"].content == "real-A"
    assert results["B"].outcome == "SKIPPED_DUPLICATE"
    assert results["B"].content.startswith("[SKIPPED_DUPLICATE]")
    assert results["C"].outcome == "NOT_EXECUTED"
    assert [m.tool_call_id for m in _tool_messages(agent)] == ["A", "B", "C"]
    assert executions == ["A"]
    assert len(completions.requests) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_new_run_can_start_after_bootstrap_failure(make_agent, error_type):
    agent, completions = make_agent([_response(content="done")])
    agent.todo = TodoManager()
    agent.trace.start_task.side_effect = [error_type("trace bootstrap"), "new-trace"]
    with pytest.raises(error_type, match="trace bootstrap"):
        agent.run("failed task")
    previous = agent._run_state
    assert previous.status.value == ("failed" if error_type is RuntimeError else "cancelled")
    assert agent._continue_run() == previous.terminal_result
    assert len(completions.requests) == 0
    assert agent.run("new task") == "done"
    assert agent._run_state.run_id != previous.run_id
    assert agent._run_state.status.value == "completed"
    assert len(completions.requests) == 1
