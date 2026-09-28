"""Incomplete or empty provider responses must not become successful tasks."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.mini_claude_agent import MiniClaudeAgent
from core.tracing.manager import TraceManager
from models.config import ConfigManager
from providers.base import Message
from providers.deepseek import DeepseekProvider


def _response(content, finish_reason, tool_calls=None, completion_tokens=10):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
        },
    }


def _run_response(tmp_path, response):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=data_root,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    provider.create_message = lambda *args, **kwargs: response
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    agent._execute_tool = lambda *args, **kwargs: pytest.fail("incomplete tool call executed")
    try:
        result = agent.chat("Complete the task")
        trace_path = next((data_root / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assistant_messages = [message for message in agent.messages if message.role == "assistant"]
        return result, trace, assistant_messages
    finally:
        agent.shutdown()


def test_parser_preserves_openai_compatible_finish_reason():
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    assert provider.parse_response(_response("done", "stop"))["finish_reason"] == "stop"
    assert provider.parse_response(_response("", "length"))["finish_reason"] == "length"
    assert provider.parse_response(_response("done", None))["finish_reason"] is None


def test_output_limit_fails_closed_and_keeps_actual_usage(tmp_path):
    result, trace, messages = _run_response(
        tmp_path, _response("", "length", completion_tokens=8000),
    )

    assert "输出达到上限" in result
    assert trace["final_status"] == "FAILED"
    assert trace["terminal_reason"] == "PROVIDER_OUTPUT_LIMIT"
    assert trace["turns"][0]["provider_finish_reason"] == "length"
    assert trace["prompt_tokens"] == 100
    assert trace["completion_tokens"] == 8000
    assert messages == []


@pytest.mark.parametrize("content,tool_calls", [
    ("partial answer", None),
    ("", [{"id": "call-1", "type": "function", "function": {
        "name": "bash", "arguments": '{"command":"echo incomplete"}',
    }}]),
])
def test_output_limit_does_not_commit_partial_assistant_or_execute_tools(
    tmp_path, content, tool_calls,
):
    result, trace, messages = _run_response(
        tmp_path, _response(content, "length", tool_calls),
    )

    assert "输出达到上限" in result
    assert trace["final_status"] == "FAILED"
    assert trace["total_tool_calls"] == 0
    assert messages == []


def test_output_limit_does_not_publish_partial_assistant_note(tmp_path):
    partial_call = [{"id": "call-1", "type": "function", "function": {
        "name": "bash", "arguments": '{"command":"echo incomplete"}',
    }}]
    _run_response(tmp_path, _response("I will inspect this", "length", partial_call))
    session_path = next((tmp_path / "runtime" / "sessions").glob("session_*.jsonl"))
    events = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert not any(event["type"] == "assistant_note" for event in events)


def test_default_dashscope_output_budget_allows_reasoning_and_visible_reply():
    config_path = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"
    config = ConfigManager(config_path).get_config()

    assert config.llm.provider == "dashscope"
    assert config.llm.model == "deepseek-v4-flash-0731"
    assert config.llm.max_tokens == 16384


def test_empty_visible_response_without_finish_reason_is_not_success(tmp_path):
    result, trace, messages = _run_response(tmp_path, _response(" ", None))

    assert "未返回可见回复" in result
    assert trace["final_status"] == "FAILED"
    assert trace["terminal_reason"] == "EMPTY_PROVIDER_RESPONSE"
    assert messages == []


def test_normal_final_answer_still_succeeds(tmp_path):
    result, trace, messages = _run_response(tmp_path, _response("done", "stop"))

    assert result == "done"
    assert trace["final_status"] == "SUCCESS"
    assert trace["turns"][0]["provider_finish_reason"] == "stop"
    assert [message.content for message in messages] == ["done"]


def test_session_records_request_start_without_fabricating_thinking(tmp_path):
    _run_response(tmp_path, _response("done", "stop"))
    session_path = next((tmp_path / "runtime" / "sessions").glob("session_*.jsonl"))
    events = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    request_events = [event for event in events if event["type"] == "model_request_started"]
    assert len(request_events) == 1
    assert request_events[0]["turn"] == 1
    assert "content" not in request_events[0]
    assert not any(event["type"] == "thinking" for event in events)


def test_reasoning_diagnostics_without_persisting_reasoning_text(tmp_path):
    response = _response("", "length", completion_tokens=8000)
    private_reasoning = "private model reasoning"
    response["choices"][0]["message"]["reasoning_content"] = private_reasoning
    response["usage"]["completion_tokens_details"] = {"reasoning_tokens": 8000}

    result, trace, _ = _run_response(tmp_path, response)

    assert "输出达到上限" in result
    assert trace["final_status"] == "FAILED"
    assert trace["turns"][0]["reasoning_content_chars"] == len(private_reasoning)
    assert trace["turns"][0]["main_reasoning_tokens"] == 8000
    assert trace["completion_tokens"] == 8000  # A subset, not an extra cost.
    assert private_reasoning not in json.dumps(trace)


def test_missing_reasoning_diagnostics_are_unavailable_not_zero(tmp_path):
    _, trace, _ = _run_response(tmp_path, _response("done", "stop"))

    assert "reasoning_content_chars" not in trace["turns"][0]
    assert "main_reasoning_tokens" not in trace["turns"][0]


def test_reasoning_usage_is_clamped_to_completion_tokens():
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    response = _response("done", "stop", completion_tokens=10)
    response["usage"]["completion_tokens_details"] = {"reasoning_tokens": 20}

    assert provider.parse_response(response)["usage"]["reasoning_tokens"] == 10


def test_empty_reasoning_field_is_distinct_from_missing_field():
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    response = _response("done", "stop")
    assert provider.parse_response(response)["reasoning_content_chars"] is None

    response["choices"][0]["message"]["reasoning_content"] = ""
    assert provider.parse_response(response)["reasoning_content_chars"] == 0


def test_reasoning_only_length_response_still_reaches_fail_closed_guard(tmp_path):
    response = _response("", "length", completion_tokens=8000)
    response["choices"][0]["message"] = {
        "role": "assistant", "reasoning_content": "unavailable private reasoning",
    }

    result, trace, _ = _run_response(tmp_path, response)

    assert "输出达到上限" in result
    assert trace["terminal_reason"] == "PROVIDER_OUTPUT_LIMIT"
    assert trace["turns"][0]["reasoning_content_chars"] > 0


def test_main_and_summary_reasoning_usage_remain_separate():
    trace = TraceManager(trace_dir=None)
    trace.start_task(task_id="reasoning-usage")
    trace.start_turn(0)
    trace.record_provider_usage({
        "prompt_tokens": 50, "completion_tokens": 10,
        "total_tokens": 60, "reasoning_tokens": 4,
    }, source="summary")
    trace.record_provider_usage({
        "prompt_tokens": 100, "completion_tokens": 20,
        "total_tokens": 120, "reasoning_tokens": 8,
    }, source="main")

    turn = trace.current_turn
    assert turn.summary_reasoning_tokens == 4
    assert turn.main_reasoning_tokens == 8
    assert turn.completion_tokens == 30  # Reasoning is already included.
    assert turn.token_usage == 180


def test_reasoning_effort_is_opt_in_and_forwarded_to_compatible_provider(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  reasoning_effort: low\n", encoding="utf-8")
    assert ConfigManager(config_path).get_config().llm.reasoning_effort == "low"
    assert ConfigManager(tmp_path / "missing.yaml").get_config().llm.reasoning_effort is None

    observed = []
    for effort in (None, "low"):
        provider = DeepseekProvider({
            "model": "test", "api_key": "local-test", "reasoning_effort": effort,
        })
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: observed.append(kwargs) or _response("done", "stop"),
        )))
        provider.create_message([Message(role="user", content="hello")])

    assert "reasoning_effort" not in observed[0]
    assert observed[1]["reasoning_effort"] == "low"


def test_trace_records_effective_safe_request_config(tmp_path):
    _, trace, _ = _run_response(tmp_path, _response("done", "stop"))

    assert trace["request_config"]["max_tokens"] == 16384
    assert trace["request_config"]["reasoning_effort"] is None
    assert trace["request_config"]["stream"] is True
    assert "api_key" not in trace["request_config"]


def test_agent_configures_optional_reasoning_effort_without_changing_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n  provider: dashscope\n  model: deepseek-v4-flash-0731\n"
        "  api_key: local-test\n  reasoning_effort: low\n",
        encoding="utf-8",
    )
    agent = MiniClaudeAgent(
        config_path=config_path, workspace_root=workspace,
        workspace_confirmed=True, runtime_data_root=tmp_path / "runtime",
    )
    try:
        provider = agent.provider_manager.get_primary_provider()
        assert isinstance(provider, DeepseekProvider)
        assert provider.reasoning_effort == "low"
    finally:
        agent.shutdown()


def test_iteration_limit_reports_model_turn_limit_not_tool_count(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace, workspace_confirmed=True,
        runtime_data_root=data_root,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    provider.create_message = lambda *args, **kwargs: pytest.fail("unexpected provider call")
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    try:
        result = agent._llm_tool_cycle(max_iterations=0)
        trace_path = next((data_root / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
    finally:
        agent.shutdown()

    assert "模型调用已达 0 轮上限" in result
    assert "工具执行次数过多" not in result
    assert trace["final_status"] == "LOOP_ABORTED"
    assert trace["terminal_reason"] == "GLOBAL_ITERATION_LIMIT"
