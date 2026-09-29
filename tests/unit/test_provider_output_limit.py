"""Provider output truncation must never become a successful task."""

import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

from agent.mini_claude_agent import MiniClaudeAgent
from providers.deepseek import DeepseekProvider


def _response(content, finish_reason, tool_calls=None, completion_tokens=8000):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": completion_tokens},
        },
    }


@pytest.mark.parametrize("content,tool_calls", [
    ("", None),
    ("partial answer", None),
    ("", [{"id": "call-1", "type": "function", "function": {
        "name": "bash", "arguments": '{"command":"echo incomplete"}',
    }}]),
    ("", [{"id": "partial-call"}]),
])
def test_length_response_fails_without_committing_or_executing(tmp_path, content, tool_calls):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=runtime,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    provider.create_message = lambda *args, **kwargs: _response(content, "length", tool_calls)
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    agent._execute_tool = lambda *args, **kwargs: pytest.fail("截断的工具调用不应执行")
    try:
        result = agent.chat("Complete the task")
        trace_path = next((runtime / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert "输出达到上限" in result
        assert trace["final_status"] == "FAILED"
        assert trace["terminal_reason"] == "PROVIDER_OUTPUT_LIMIT"
        assert trace["turns"][0]["provider_finish_reason"] == "length"
        assert trace["prompt_tokens"] == 100
        assert trace["completion_tokens"] == 8000
        assert trace["main_reasoning_tokens"] == 8000
        assert not any(message.role == "assistant" for message in agent.messages)
    finally:
        agent.shutdown()


def test_normal_finish_reason_still_succeeds(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=runtime,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    provider.create_message = lambda *args, **kwargs: _response("done", "stop", completion_tokens=5)
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    try:
        assert agent.chat("Complete the task") == "done"
        trace_path = next((runtime / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert trace["final_status"] == "SUCCESS"
        assert trace["turns"][0]["provider_finish_reason"] == "stop"
    finally:
        agent.shutdown()


def test_empty_visible_response_is_not_success(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=runtime,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    provider.create_message = lambda *args, **kwargs: _response("", "stop")
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    try:
        assert "任务未完成" in agent.chat("Complete the task")
        trace_path = next((runtime / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert trace["final_status"] == "FAILED"
        assert trace["terminal_reason"] == "EMPTY_PROVIDER_RESPONSE"
    finally:
        agent.shutdown()
