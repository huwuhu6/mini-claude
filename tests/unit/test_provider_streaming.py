"""Stream assembly is atomic: no partial answer or tool call reaches the Agent."""

import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

sys.path.insert(0, "src")

from agent.mini_claude_agent import MiniClaudeAgent
from models.config import ConfigManager
from providers.base import Message
from providers.bootstrap import configure_primary_provider
from providers.deepseek import DeepseekProvider
from providers.manager import ProviderManager
from core.tracing import TraceManager


def _chunk(delta=None, finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=([] if delta is None else [SimpleNamespace(
            delta=SimpleNamespace(**delta), finish_reason=finish_reason,
        )]),
        usage=usage,
    )


def _provider(chunks, *, stream=True):
    provider = DeepseekProvider({"model": "test", "api_key": "local-test", "stream": stream})
    observed = []
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: observed.append(kwargs) or iter(chunks),
    )))
    return provider, observed


def test_stream_assembles_content_reasoning_and_usage_without_leaking_reasoning():
    provider, observed = _provider([
        _chunk({"content": "hel", "reasoning_content": "private "}),
        _chunk({"content": "lo", "reasoning_content": "thought"}, finish_reason="stop"),
        _chunk(usage={"prompt_tokens": 100, "completion_tokens": 20,
                      "total_tokens": 120, "prompt_tokens_details": {"cached_tokens": 40}}),
    ])

    result = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))

    assert observed[0]["stream"] is True
    assert observed[0]["stream_options"] == {"include_usage": True}
    assert result["content"] == "hello"
    assert result["finish_reason"] == "stop"
    assert result["reasoning_content_chars"] == len("private thought")
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 20,
                                "total_tokens": 120, "cached_tokens": 40,
                                "reasoning_tokens": 0}
    assert "private thought" not in str(result)


def test_stream_assembles_interleaved_tool_calls_in_index_order():
    provider, _ = _provider([
        _chunk({"tool_calls": [
            {"index": 1, "id": "call-b", "type": "function",
             "function": {"name": "read_", "arguments": '{"path":"'}},
            {"index": 0, "id": "call-a", "type": "function",
             "function": {"name": "ba", "arguments": '{"command":"'}},
        ]}),
        _chunk({"tool_calls": [
            {"index": 0, "function": {"name": "sh", "arguments": "pwd\"}"}},
            {"index": 1, "function": {"name": "file", "arguments": "a.py\"}"}},
        ]}, finish_reason="tool_calls"),
    ])

    result = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))

    assert [call["id"] for call in result["tool_calls"]] == ["call-a", "call-b"]
    assert [call["function"]["name"] for call in result["tool_calls"]] == ["bash", "read_file"]
    assert [call["function"]["arguments"] for call in result["tool_calls"]] == [
        '{"command":"pwd"}', '{"path":"a.py"}',
    ]


def test_stream_concatenates_id_fragments_and_ignores_empty_placeholders():
    provider, _ = _provider([
        _chunk({"tool_calls": [{"index": 0, "id": "call_", "function": {
            "name": "ba", "arguments": '{"command":"',
        }}]}),
        _chunk({"tool_calls": [{"index": 0, "id": "abc", "function": {
            "name": "sh", "arguments": "pwd",
        }}]}),
        _chunk({"tool_calls": [{"index": 0, "id": "", "function": {
            "name": "", "arguments": '"}',
        }}]}, finish_reason="tool_calls"),
    ])

    parsed = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))
    assert parsed["tool_calls"] == [{"id": "call_abc", "type": "function",
                                     "function": {"name": "bash", "arguments": '{"command":"pwd"}'}}]


def test_stream_repeated_complete_id_is_not_appended_again():
    provider, _ = _provider([
        _chunk({"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "name": "bash", "arguments": "{",
        }}]}),
        _chunk({"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "arguments": "}",
        }}]}, finish_reason="tool_calls"),
    ])
    parsed = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))
    assert parsed["tool_calls"][0]["id"] == "call-1"


def test_stream_rejects_duplicate_final_tool_ids():
    provider, _ = _provider([_chunk({"tool_calls": [
        {"index": 0, "id": "call-1", "function": {"name": "bash", "arguments": "{}"}},
        {"index": 1, "id": "call-1", "function": {"name": "bash", "arguments": "{}"}},
    ]}, finish_reason="tool_calls")])
    with pytest.raises(ValueError, match="重复 id"):
        provider.create_message([Message(role="user", content="hi")])


def test_real_openai_sdk_stream_is_assembled_from_local_sse():
    events = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ready"},
                      "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
            "id": "call-", "type": "function", "function": {
                "name": "bash", "arguments": '{"command":"pwd"}',
            }}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0,
            "id": "1", "function": {"name": "", "arguments": ""},
        }]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 5,
                                  "total_tokens": 25}},
    ]
    body = ''.join(f'data: {json.dumps(event)}\n\n' for event in events) + 'data: [DONE]\n\n'
    requests = []

    def send(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=body, request=request)

    provider = DeepseekProvider({"model": "test", "api_key": "local-test", "stream": True})
    provider.client = OpenAI(
        api_key="local-test", base_url="https://example.test/v1", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(send)),
    )

    parsed = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert parsed["content"] == "ready"
    assert parsed["tool_calls"][0]["id"] == "call-1"
    assert parsed["tool_calls"][0]["function"]["arguments"] == '{"command":"pwd"}'
    assert parsed["usage"]["total_tokens"] == 25


def test_stream_without_usage_does_not_invent_actual_tokens():
    provider, _ = _provider([_chunk({"content": "ok"}, finish_reason="stop")])
    parsed = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))
    assert parsed["usage"] == {"prompt_tokens": 0, "completion_tokens": 0,
                               "total_tokens": 0, "cached_tokens": 0,
                               "reasoning_tokens": 0}


def test_stream_preserves_output_limit_finish_reason():
    provider, _ = _provider([_chunk({"content": "partial"}, finish_reason="length")])
    parsed = provider.parse_response(provider.create_message([Message(role="user", content="hi")]))
    assert parsed["finish_reason"] == "length"


def test_missing_reasoning_content_is_recorded_as_unavailable():
    provider = DeepseekProvider({"model": "test", "api_key": "local-test"})
    response = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}

    assert provider.parse_response(response)["reasoning_content_chars"] is None


@pytest.mark.parametrize("chunks", [
    [_chunk({"tool_calls": [{"index": 0, "id": "call-a", "function": {
        "name": "bash", "arguments": '{"command":"rm',
    }}]})],  # Connection closed before finish_reason.
    [_chunk({"tool_calls": [{"index": 0, "function": {
        "name": "bash", "arguments": "{}",
    }}]}, finish_reason="tool_calls")],  # Missing required call ID.
])
def test_incomplete_tool_call_never_returns_a_usable_response(chunks):
    provider, _ = _provider(chunks)
    if len(chunks) == 1 and chunks[0].choices[0].finish_reason is None:
        with pytest.raises(ValueError, match="未正常结束"):
            provider.create_message([Message(role="user", content="hi")])
    else:
        with pytest.raises(ValueError, match="tool_call"):
            provider.create_message([Message(role="user", content="hi")])


def test_transport_error_mid_stream_is_not_returned_as_partial_success():
    def interrupted():
        yield _chunk({"content": "partial", "tool_calls": [{"index": 0,
            "id": "call-a", "function": {"name": "bash", "arguments": "{}"}}]})
        raise ConnectionError("stream interrupted")

    provider, _ = _provider(interrupted())
    with pytest.raises(ConnectionError, match="stream interrupted"):
        provider.create_message([Message(role="user", content="hi")])
    assert provider.last_error_diagnostic["error_category"] == "CONNECTION"


def test_agent_does_not_execute_partial_stream_tool_call(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace, workspace_confirmed=True, runtime_data_root=runtime,
    )
    provider = DeepseekProvider({"model": "test", "api_key": "local-test", "stream": True})

    def interrupted():
        yield _chunk({"tool_calls": [{"index": 0, "id": "call-1", "function": {
            "name": "bash", "arguments": '{"command":"echo partial"}',
        }}]})
        raise ConnectionError("stream interrupted")

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: interrupted(),
    )))
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    agent._execute_tool = lambda *args, **kwargs: pytest.fail("partial tool call executed")
    try:
        result = agent.chat("Complete the task")
        trace_path = next((runtime / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert result.startswith("错误:")
        assert trace["final_status"] == "FAILED"
        assert trace["total_tool_calls"] == 0
        assert trace["turns"][0]["provider_stream"] is True
        assert not [message for message in agent.messages if message.role == "assistant"]
    finally:
        agent.shutdown()


def test_nonstream_opt_out_preserves_response_contract():
    config_manager = ConfigManager()
    disabled = config_manager._dict_to_config({"llm": {"stream": False}})
    assert disabled.llm.stream is False
    assert ConfigManager().get_config().llm.stream is True

    provider = DeepseekProvider({"model": "test", "api_key": "local-test", "stream": False})
    expected = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    observed = []
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: observed.append(kwargs) or expected,
    )))
    assert provider.create_message([Message(role="user", content="hi")]) is expected
    assert observed[0]["stream"] is False
    assert "stream_options" not in observed[0]


@pytest.mark.parametrize("enabled", [True, False])
def test_bootstrap_passes_stream_configuration_to_provider(monkeypatch, enabled):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "local-test")

    llm = ConfigManager().get_config().llm
    llm.provider = "dashscope"
    llm.model = "test"
    llm.stream = enabled
    provider_manager = ProviderManager()
    configure_primary_provider(provider_manager, llm)

    assert provider_manager.get_primary_provider().stream is enabled


def test_provider_stream_mode_is_saved_on_turn_trace():
    trace = TraceManager()
    trace.start_task()
    trace.start_turn(1)
    trace.record_provider_stream(True)
    assert trace.current_turn.to_dict()["provider_stream"] is True
