"""Deterministic regression tests for Context Foundation invariants."""

import copy
import runpy
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.compression import Compressor
from core.tools.base_tools import (
    BaseTools,
    READ_FILE_MAX_BYTES,
    READ_FILE_MAX_CHARS,
    READ_FILE_MAX_LINES,
)
from providers.base import Message


def _tool_chain(tool_name="bash", call_id="call-1", content="result"):
    return [
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": '{"value": "' + ("x" * 4000) + '"}',
                    },
                }
            ],
        ),
        Message(role="tool", content=content, tool_call_id=call_id),
    ]


def _microcompact_messages(tool_name="bash", content=None):
    messages = [Message(role="system", content="system"), Message(role="user", content="task")]
    messages.extend(_tool_chain(tool_name, content=content or ("output " + "x" * 500)))
    messages.extend(Message(role="user", content=f"filler-{i}") for i in range(6))
    return messages


def test_estimate_tokens_includes_large_tool_call_arguments_without_changing_plain_messages():
    compressor = Compressor()
    plain = Message(role="assistant", content="hello world")
    with_large_call = Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": '{"content": "' + ("code " * 1500) + '"}',
                },
            }
        ],
    )

    assert compressor.estimate_tokens([with_large_call]) > 100
    if compressor_module_encoding_available():
        import core.compression as compression_module

        assert compressor.estimate_tokens([plain]) == (
            len(compression_module._ENCODING.encode(plain.content)) + 4
        )
    else:
        assert compressor.estimate_tokens([plain]) == len(plain.content) // 4


def compressor_module_encoding_available():
    import core.compression as compression_module

    return compression_module._TIKTOKEN_AVAILABLE and compression_module._ENCODING is not None


@pytest.mark.parametrize("tool_name", ["bash", "search_code", "count_occurrences"])
def test_microcompact_never_invents_success_for_unknown_tool_status(tool_name):
    messages = _microcompact_messages(tool_name, content="unstructured output " + "x" * 500)

    compacted = Compressor().microcompact(messages)
    tool_message = next(message for message in compacted if message.role == "tool")

    assert "recorded as successful" not in tool_message.content.lower()
    assert "successful" not in tool_message.content.lower()
    assert "status was not encoded" in tool_message.content


def test_microcompact_preserves_explicit_success_and_failure_facts():
    success = _microcompact_messages(
        content="[Command executed with exit code 0]\n" + "x" * 500
    )
    failure = _microcompact_messages(
        content="[Command executed with exit code 7]\n" + "x" * 500
    )

    success_result = next(message for message in Compressor().microcompact(success) if message.role == "tool")
    failure_result = next(message for message in Compressor().microcompact(failure) if message.role == "tool")

    assert "completed" in success_result.content
    assert "failed" not in success_result.content.lower()
    assert "failed" in failure_result.content.lower()
    assert "recorded as successful" not in failure_result.content.lower()


def test_auto_microcompact_reports_changed_once_then_noop():
    from agent.mini_claude_agent import MiniClaudeAgent

    compressor = Compressor()
    messages = _microcompact_messages()
    compressor.microcompact_token_threshold = compressor.estimate_tokens(messages)

    agent = object.__new__(MiniClaudeAgent)
    agent.compressor = compressor
    agent.messages = messages
    agent.feature_manager = types.SimpleNamespace(is_enabled=lambda name: True)

    assert agent._check_auto_compress() is True
    unchanged = copy.deepcopy(agent.messages)
    assert agent._check_auto_compress() is False
    assert agent.messages == unchanged


def test_full_compression_moves_tail_boundary_before_tool_chain():
    boundary_chain = _tool_chain(call_id="boundary-call", content="important execution result")
    messages = [Message(role="system", content="system"), Message(role="user", content="intro")]
    messages.extend([Message(role="user", content=f"middle-{i}") for i in range(2)])
    messages.extend(boundary_chain)
    messages.extend(Message(role="user", content=f"tail-{i}") for i in range(14))

    compressed = Compressor().compress(messages)
    assistant = next(message for message in compressed if message.role == "assistant" and message.tool_calls)
    tool = next(message for message in compressed if message.role == "tool")

    assert tool.tool_call_id == "boundary-call"
    assert assistant.tool_calls[0]["id"] == "boundary-call"
    assert compressed.index(assistant) + 1 == compressed.index(tool)
    assert all(
        message.role != "tool"
        or (
            index > 0
            and compressed[index - 1].role == "assistant"
            and any(tc["id"] == message.tool_call_id for tc in compressed[index - 1].tool_calls or [])
        )
        for index, message in enumerate(compressed)
    )


@pytest.mark.parametrize("line_count", [50, 100, 200])
def test_read_file_normal_windows_are_unchanged(tmp_path, line_count):
    path = tmp_path / "normal.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(1, line_count + 1)), encoding="utf-8")

    result = BaseTools(tmp_path).read_file("normal.txt")

    assert result.success
    assert f"LINES: 1-{line_count} of {line_count}" in result.content
    assert f"line-{line_count}" in result.content


def test_read_file_explicit_end_line_cannot_bypass_hard_line_bound(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(1, 501)), encoding="utf-8")

    result = BaseTools(tmp_path).read_file("large.txt", end_line=10_000)

    assert result.success
    assert "LINES: 1-200 of 500" in result.content
    assert "line-200" in result.content
    assert "line-201" not in result.content
    assert "实际返回第 1-200 行，文件共 500 行" in result.content
    assert "start_line=201" in result.content


def test_read_file_hard_character_and_byte_bound_handles_single_long_line(tmp_path):
    path = tmp_path / "minified.json"
    path.write_text("x" * (READ_FILE_MAX_CHARS * 2), encoding="utf-8")

    result = BaseTools(tmp_path).read_file("minified.json", end_line=999_999)

    assert result.success
    assert len(result.content.encode("utf-8")) < READ_FILE_MAX_BYTES + 1000
    assert "LINES: 1-1 of 1" in result.content
    assert "内容被截断" in result.content
    assert "字符/字节上限" in result.content
    assert "start_line=2" in result.content


def test_read_file_pagination_continues_after_hard_line_window(tmp_path):
    path = tmp_path / "paged.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(1, 451)), encoding="utf-8")
    tools = BaseTools(tmp_path)

    first = tools.read_file("paged.txt")
    second = tools.read_file("paged.txt", start_line=201, end_line=400)
    third = tools.read_file("paged.txt", start_line=401, end_line=450)

    assert "line-200" in first.content and "line-201" not in first.content
    assert "line-201" in second.content and "line-400" in second.content
    assert "line-401" in third.content and "line-450" in third.content


def test_read_file_schema_states_actual_window_contract():
    from agent.mini_claude_agent import MiniClaudeAgent

    agent = object.__new__(MiniClaudeAgent)
    agent.feature_manager = types.SimpleNamespace(
        is_enabled=lambda name: True,
        filter_tools=lambda tools: tools,
    )
    read_file = next(tool for tool in agent._get_llm_tools() if tool["name"] == "read_file")

    assert str(READ_FILE_MAX_LINES) in read_file["description"]
    assert "start_line/end_line" in read_file["description"]
    assert "硬上限" in read_file["description"]
    assert "继续读取" in read_file["description"]


def test_tiktoken_initialization_failure_falls_back_without_import_failure(monkeypatch):
    fake_tiktoken = types.ModuleType("tiktoken")

    def fail_get_encoding(_name):
        raise RuntimeError("encoding cache unavailable")

    fake_tiktoken.get_encoding = fail_get_encoding
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)

    namespace = runpy.run_path(
        str(ROOT / "src" / "core" / "compression.py"),
        run_name="context_compression_fallback_test",
    )

    assert namespace["_TIKTOKEN_AVAILABLE"] is False
    compressor = namespace["Compressor"]()
    assert compressor.estimate_tokens([Message(role="user", content="abcd")]) == 1
