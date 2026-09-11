"""Deterministic regression tests for Context Baseline Modernization."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

# Keep the focused test runnable directly, like the existing Context tests.
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.compression import Compressor
from core.tracing.manager import TraceManager
from models.config import CompressionConfig, ConfigManager
from providers.base import Message
from providers.deepseek import DeepseekProvider


def test_default_compression_thresholds_are_explicit_and_valid():
    config = ConfigManager(Path("configs/default.yaml")).get_config()

    assert config.compression.context_window_tokens == 1_000_000
    assert config.compression.microcompact_token_threshold == 250_000
    assert config.compression.full_compression_token_threshold == 500_000
    assert config.compression.microcompact_token_threshold < config.compression.full_compression_token_threshold
    assert config.compression.full_compression_token_threshold <= config.compression.context_window_tokens


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_window_tokens", 0),
        ("microcompact_token_threshold", 500_000),
        ("full_compression_token_threshold", 1_000_001),
    ],
)
def test_invalid_compression_thresholds_fail_clearly(field, value):
    values = {
        "context_window_tokens": 1_000_000,
        "microcompact_token_threshold": 250_000,
        "full_compression_token_threshold": 500_000,
    }
    values[field] = value

    with pytest.raises(ValueError):
        CompressionConfig(**values)


def test_removed_token_threshold_is_rejected_with_migration_message():
    manager = ConfigManager.__new__(ConfigManager)

    with pytest.raises(ValueError, match="token_threshold has been removed"):
        manager._dict_to_config({"compression": {"token_threshold": 100_000}})


def test_threshold_boundaries_are_explicit(monkeypatch):
    compressor = Compressor({
        "context_window_tokens": 1_000_000,
        "microcompact_token_threshold": 250_000,
        "full_compression_token_threshold": 500_000,
    })
    messages = [Message(role="tool", content="large") for _ in range(10)]
    monkeypatch.setattr(compressor, "_has_microcompact_candidates", lambda _: True)

    monkeypatch.setattr(compressor, "estimate_tokens", lambda _: 249_999)
    assert not compressor.should_microcompact(messages)
    monkeypatch.setattr(compressor, "estimate_tokens", lambda _: 250_000)
    assert compressor.should_microcompact(messages)

    monkeypatch.setattr(compressor, "estimate_tokens", lambda _: 499_999)
    assert not compressor.should_compress(messages)
    monkeypatch.setattr(compressor, "estimate_tokens", lambda _: 500_000)
    assert compressor.should_compress(messages)


def test_request_estimate_includes_system_tools_messages_and_hot_context():
    compressor = Compressor()
    durable = [Message(role="user", content="durable request")]
    system = "system constraint " * 100
    tools = [{
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "write source",
            "parameters": {"type": "object", "properties": {"content": {"type": "string"}}},
        },
    }]
    base = compressor.estimate_prompt_tokens(durable, system_prompt="", tools=None)
    with_system = compressor.estimate_prompt_tokens(durable, system_prompt=system, tools=None)
    with_tools = compressor.estimate_prompt_tokens(durable, system_prompt="", tools=tools)

    request_messages = [Message(role="user", content="durable request\n\n<dynamic_context>hot state</dynamic_context>")]
    with_hot = compressor.estimate_prompt_tokens(request_messages, system_prompt=system, tools=tools)

    assert with_system > base
    assert with_tools > base
    assert with_hot > with_system
    assert with_hot > with_tools
    # The hot block is already in the actual request message and is counted
    # once; callers must not add it as a second independent component.
    assert with_hot == compressor.estimate_prompt_tokens(request_messages, system, tools)


def test_summary_provider_receives_complete_middle_without_tail_only():
    captured = {}

    class FakeSummaryProvider:
        def create_message(self, messages, **kwargs):
            captured["prompt"] = messages[0].content
            return {"content": "summary", "usage": {
                "prompt_tokens": 321,
                "completion_tokens": 12,
                "total_tokens": 333,
                "cached_tokens": 100,
            }}

        def parse_response(self, response):
            return response

    compressor = Compressor()
    compressor.set_provider(FakeSummaryProvider())
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="intro"),
        Message(role="user", content="CRITICAL_EARLY_CONSTRAINT_DO_NOT_MODIFY_DATABASE"),
        Message(role="assistant", content="later history " + "x" * 20_000),
    ] + [Message(role="user", content=f"tail {i}") for i in range(15)]

    compressed = compressor.compress(messages)
    assert len(compressed) < len(messages)
    assert "CRITICAL_EARLY_CONSTRAINT_DO_NOT_MODIFY_DATABASE" in captured["prompt"]
    assert len(captured["prompt"]) > 12_000
    assert compressor.consume_last_summary_usage() == {
        "prompt_tokens": 321,
        "completion_tokens": 12,
        "total_tokens": 333,
        "cached_tokens": 100,
    }


def test_summary_input_over_context_window_fails_closed():
    class FakeSummaryProvider:
        def create_message(self, messages, **kwargs):
            pytest.fail("oversized summary must not call provider")

        def parse_response(self, response):
            return response

    compressor = Compressor({
        "context_window_tokens": 100,
        "microcompact_token_threshold": 25,
        "full_compression_token_threshold": 50,
    })
    compressor.set_provider(FakeSummaryProvider())
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="intro"),
        Message(role="user", content="x" * 1_000),
    ] + [Message(role="user", content=f"tail {i}") for i in range(15)]

    assert compressor.compress(messages) == messages


def test_summary_context_check_reserves_summary_output_tokens():
    class FakeSummaryProvider:
        def create_message(self, messages, **kwargs):
            pytest.fail("summary input plus output reserve must not call provider")

        def parse_response(self, response):
            return response

    compressor = Compressor({
        "context_window_tokens": 700,
        "microcompact_token_threshold": 25,
        "full_compression_token_threshold": 50,
    })
    compressor.set_provider(FakeSummaryProvider())
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="intro"),
        Message(role="user", content="x" * 400),
    ] + [Message(role="user", content=f"tail {i}") for i in range(15)]

    assert compressor.compress(messages) == messages


def _raw_response(usage=None):
    response = {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}
    if usage is not None:
        response["usage"] = usage
    return response


def test_openai_compatible_usage_parses_cached_tokens_and_missing_details():
    provider = object.__new__(DeepseekProvider)

    parsed = provider.parse_response(_raw_response({
        "prompt_tokens": 100_000,
        "completion_tokens": 50,
        "total_tokens": 100_050,
        "prompt_tokens_details": {"cached_tokens": 80_000},
    }))
    assert parsed["usage"] == {
        "prompt_tokens": 100_000,
        "completion_tokens": 50,
        "total_tokens": 100_050,
        "cached_tokens": 80_000,
    }

    assert provider.parse_response(_raw_response({"prompt_tokens": 4}))["usage"]["cached_tokens"] == 0
    assert provider.parse_response(_raw_response())["usage"]["cached_tokens"] == 0
    assert provider.parse_response(_raw_response({
        "prompt_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 50},
    }))["usage"]["cached_tokens"] == 10


def test_trace_cache_aggregation_is_token_weighted_and_summary_is_distinct(tmp_path):
    trace = TraceManager(trace_dir=tmp_path)
    trace.start_task(task_id="cache-test")
    trace.start_turn(0)
    trace.record_provider_usage({
        "prompt_tokens": 1_000,
        "completion_tokens": 10,
        "total_tokens": 1_010,
        "cached_tokens": 0,
    }, estimated_prompt_tokens=1_100)
    trace.start_turn(1)
    trace.record_provider_usage({
        "prompt_tokens": 100_000,
        "completion_tokens": 20,
        "total_tokens": 100_020,
        "cached_tokens": 90_000,
    }, estimated_prompt_tokens=100_100)
    trace.record_provider_usage({
        "prompt_tokens": 321,
        "completion_tokens": 12,
        "total_tokens": 333,
        "cached_tokens": 100,
    }, source="summary")
    path = trace.end_task("SUCCESS")

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["prompt_tokens"] == 101_321
    assert data["actual_prompt_tokens"] == 101_321
    assert data["completion_tokens"] == 42
    assert data["main_prompt_tokens"] == 101_000
    assert data["cached_tokens"] == 90_100
    assert data["uncached_prompt_tokens"] == 11_221
    assert data["cache_hit_rate"] == pytest.approx(90_100 / 101_321)
    assert data["main_cache_hit_rate"] == pytest.approx(90_000 / 101_000)
    assert data["estimated_prompt_tokens"] == 101_200
    assert data["summary_prompt_tokens"] == 321
    assert data["summary_total_tokens"] == 333
    assert data["summary_cache_hit_rate"] == pytest.approx(100 / 321)
    assert data["total_tokens"] == 101_363
    assert data["turns"][1]["summary_cached_tokens"] == 100


def test_agent_metrics_record_summary_usage_without_losing_main_usage():
    from agent.mini_claude_agent import MiniClaudeAgent

    class FakeSummaryProvider:
        def create_message(self, messages, **kwargs):
            return {"content": "summary", "usage": {
                "prompt_tokens": 40,
                "completion_tokens": 5,
                "total_tokens": 45,
                "cached_tokens": 10,
            }}

        def parse_response(self, response):
            return response

    compressor = Compressor({
        "context_window_tokens": 1_000,
        "microcompact_token_threshold": 1,
        "full_compression_token_threshold": 2,
    })
    compressor.set_provider(FakeSummaryProvider())
    agent = object.__new__(MiniClaudeAgent)
    agent.compressor = compressor
    agent.messages = [
        Message(role="system", content="system"),
        Message(role="user", content="intro"),
    ] + [Message(role="user", content=f"middle-{i}") for i in range(3)] + [
        Message(role="user", content=f"tail-{i}") for i in range(15)
    ]
    agent.feature_manager = types.SimpleNamespace(is_enabled=lambda name: True)
    agent.last_metrics = agent._empty_usage_metrics()
    agent.trace = TraceManager(trace_dir=None)
    agent.trace.start_task(task_id="agent-usage")
    agent.trace.start_turn(0)

    assert agent._check_auto_compress(estimated_prompt_tokens=2) is True
    agent._record_provider_usage({
        "prompt_tokens": 100,
        "completion_tokens": 8,
        "total_tokens": 108,
        "cached_tokens": 80,
    }, estimated_prompt_tokens=120)

    assert agent.last_metrics["summary_total_tokens"] == 45
    assert agent.last_metrics["total_tokens"] == 153
    assert agent.last_metrics["actual_prompt_tokens"] == 140
    assert agent.last_metrics["completion_tokens"] == 13
    assert agent.last_metrics["main_prompt_tokens"] == 100
    assert agent.last_metrics["prompt_tokens"] == 140
    assert agent.last_metrics["cached_tokens"] == 90
    assert agent.last_metrics["cache_hit_rate"] == pytest.approx(90 / 140)
    assert agent.last_metrics["main_cache_hit_rate"] == pytest.approx(0.8)
    assert agent.trace.current_turn.summary_total_tokens == 45
    assert agent.trace.current_turn.token_usage == 153
    agent.trace.end_task("SUCCESS")


def test_cache_metrics_clamp_provider_overreporting():
    from agent.mini_claude_agent import MiniClaudeAgent

    agent = object.__new__(MiniClaudeAgent)
    agent.last_metrics = agent._empty_usage_metrics()
    agent.trace = TraceManager(trace_dir=None)
    agent.trace.start_task(task_id="cache-clamp")
    agent.trace.start_turn(0)

    agent._record_provider_usage({
        "prompt_tokens": 100,
        "completion_tokens": 1,
        "total_tokens": 101,
        "cached_tokens": 150,
    })

    assert agent.last_metrics["cached_tokens"] == 100
    assert agent.last_metrics["uncached_prompt_tokens"] == 0
    assert agent.last_metrics["cache_hit_rate"] == 1.0
    assert agent.trace.current_turn.cached_tokens == 100
    assert agent.trace.current_turn.cache_hit_rate == 1.0
    agent.trace.end_task("SUCCESS")


def test_tiktoken_initialization_failure_keeps_compression_importable(monkeypatch):
    import core.compression as compression_module

    failing_tiktoken = types.SimpleNamespace(
        get_encoding=lambda name: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    original_tiktoken = sys.modules.get("tiktoken")
    monkeypatch.setitem(sys.modules, "tiktoken", failing_tiktoken)
    try:
        reloaded = importlib.reload(compression_module)
        assert reloaded._TIKTOKEN_AVAILABLE is False
        assert reloaded.Compressor().estimate_tokens([Message(role="user", content="hello")]) >= 1
    finally:
        if original_tiktoken is None:
            sys.modules.pop("tiktoken", None)
        else:
            sys.modules["tiktoken"] = original_tiktoken
        importlib.reload(compression_module)
