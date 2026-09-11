"""Deterministic regressions from the Mainline Context Final Audit."""

import builtins
import json
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.compression import CompressedTranscript, Compressor
import core.tools.base_tools as base_tools_module
from core.tools.base_tools import (
    FILE_READ_CHUNK_BYTES,
    READ_FILE_MAX_BYTES,
    READ_FILE_MAX_CHARS,
    READ_FILE_MAX_LINES,
    SEARCH_CODE_MAX_CONTEXT_LINES,
    TOOL_OUTPUT_MAX_BYTES,
    TOOL_OUTPUT_MAX_CHARS,
    BaseTools,
)
from providers.base import Message
from providers.deepseek import DeepseekProvider


def _large_tool_call(call_id="call-1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"content": "' + ("x" * 5000) + '"}',
        },
    }


class _CountingSummaryProvider:
    def __init__(self):
        self.calls = 0

    def create_message(self, messages, **kwargs):
        self.calls += 1
        return {"content": "must not be called"}

    def parse_response(self, response):
        return response


@pytest.mark.parametrize("message_count", [4, 10, 15, 17])
def test_full_compression_is_noop_when_middle_is_empty(message_count):
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="intro"),
    ]
    messages.extend(
        Message(role="user", content=f"message-{i}")
        for i in range(message_count - 2)
    )
    messages[2].tool_calls = [_large_tool_call()]

    compressor = Compressor({
        "microcompact_token_threshold": 1,
        "full_compression_token_threshold": 2,
    })
    provider = _CountingSummaryProvider()
    compressor.set_provider(provider)

    first = compressor.compress(messages)
    second = compressor.compress(first)

    assert first is messages
    assert second is messages
    assert first == messages
    assert provider.calls == 0
    assert compressor.get_transcripts() == []


def test_empty_middle_does_not_report_auto_compression():
    agent = object.__new__(MiniClaudeAgent)
    agent.compressor = Compressor({
        "microcompact_token_threshold": 1,
        "full_compression_token_threshold": 2,
    })
    agent.messages = [Message(role="user", content=f"message-{i}") for i in range(17)]
    agent.feature_manager = types.SimpleNamespace(is_enabled=lambda name: True)

    assert agent._check_auto_compress() is False


def _provider_parser():
    return object.__new__(DeepseekProvider)


def _raw_response(message, usage=None):
    response = {"choices": [{"message": message}]}
    if usage is not None:
        response["usage"] = usage
    return response


@pytest.mark.parametrize(
    "raw_response",
    [
        {},
        {"choices": []},
        {"choices": [{"message": None}]},
        {"choices": [{"message": "not a message object"}]},
        {"choices": [{"message": {}}]},
        _raw_response({"content": "ok", "tool_calls": [None]}),
        _raw_response({"content": "ok", "tool_calls": [{"id": "x", "function": None}]}),
        _raw_response({
            "content": "ok",
            "tool_calls": [{"id": "x", "function": {"name": "bash", "arguments": 1}}],
        }),
        _raw_response({
            "content": "ok",
            "tool_calls": [{"id": "x", "function": {"name": "", "arguments": "{}"}}],
        }),
    ],
)
def test_openai_compatible_malformed_response_fails_closed(raw_response):
    with pytest.raises(ValueError):
        _provider_parser().parse_response(raw_response)


def test_openai_compatible_valid_content_and_missing_usage_are_accepted():
    parsed = _provider_parser().parse_response(_raw_response({"content": "hello"}))

    assert parsed["content"] == "hello"
    assert parsed["tool_calls"] == []
    assert parsed["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }


def test_openai_compatible_valid_tool_calls_are_preserved():
    raw = _raw_response({
        "content": None,
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "bash", "arguments": "{\"command\": \"pwd\"}"},
        }],
    }, usage={"total_tokens": 4})

    parsed = _provider_parser().parse_response(raw)

    assert parsed["content"] == ""
    assert parsed["tool_calls"][0]["function"]["name"] == "bash"
    assert parsed["usage"]["total_tokens"] == 4


class _TraceStub:
    def __init__(self):
        self.end_statuses = []

    def start_task(self, **kwargs):
        return "task-1"

    def start_turn(self, iteration):
        return None

    def record_runtime_error(self, message):
        return None

    def end_task(self, status, *args):
        self.end_statuses.append(status)


def test_provider_parse_failure_enters_agent_failed_path_before_append():
    provider = _provider_parser()
    provider.last_error_diagnostic = {}
    provider.create_message = lambda *args, **kwargs: {"choices": []}
    trace = _TraceStub()

    agent = object.__new__(MiniClaudeAgent)
    agent.provider_manager = types.SimpleNamespace(get_primary_provider=lambda: provider)
    agent._get_llm_tools = lambda: []
    agent._check_auto_compress = lambda: False
    agent._get_dynamic_hot_context = lambda **kwargs: ""
    agent._emit_ui_event = lambda *args, **kwargs: None
    agent._current_user_prompt = "task"
    agent._workspace_confirmed = False
    agent.config = types.SimpleNamespace(
        llm=types.SimpleNamespace(max_tokens=10, temperature=0),
    )
    agent.messages = [Message(role="user", content="task")]
    agent.runtime_context = types.SimpleNamespace(
        current_task_id=None,
        workspace_root=Path("."),
    )
    agent.preflight = types.SimpleNamespace(to_dict=lambda: {})
    agent.runtime_policy = types.SimpleNamespace(reset=lambda: None)
    agent.workspace_state_guard = types.SimpleNamespace(reset=lambda: None)
    agent.runtime_observer = types.SimpleNamespace(reset=lambda: None)
    agent.completion_guard = types.SimpleNamespace(reset=lambda: None)
    agent.trace = trace
    agent.session_recorder = types.SimpleNamespace(record=lambda *args, **kwargs: None)

    result = agent._llm_tool_cycle(max_iterations=1)

    assert result.startswith("错误:")
    assert trace.end_statuses == ["FAILED"]
    assert agent.last_metrics["api_errors"] == 1
    assert all(message.role != "assistant" for message in agent.messages)


def test_bash_output_hard_bound_handles_a_single_huge_line(tmp_path):
    tools = BaseTools(tmp_path)
    content = "x" * (TOOL_OUTPUT_MAX_CHARS * 4)

    result = tools.format_tool_output(content, success=False, exit_code=7)

    assert len(result) <= TOOL_OUTPUT_MAX_CHARS
    assert len(result.encode("utf-8")) <= TOOL_OUTPUT_MAX_BYTES
    assert "exit code 7" in result
    assert "Truncated" in result
    assert len(result) < len(content)


def test_search_code_clamps_context_lines_and_bounds_result(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text(
        "\n".join(
            f"line-{i} MATCH" if i == 500 else f"line-{i}"
            for i in range(1, 1001)
        ),
        encoding="utf-8",
    )

    result = BaseTools(tmp_path).search_code(
        paths=["large.txt"],
        patterns=["MATCH"],
        context_lines=1_000_000,
        max_matches=1,
    )

    assert SEARCH_CODE_MAX_CONTEXT_LINES == 50
    assert "line-1" not in result.content
    assert "line-1000" not in result.content
    assert "line-450" in result.content
    assert "line-550" in result.content
    assert len(result.content) <= TOOL_OUTPUT_MAX_CHARS
    assert len(result.content.encode("utf-8")) <= TOOL_OUTPUT_MAX_BYTES


class _ReadSpy:
    def __init__(self, wrapped, reads):
        self.wrapped = wrapped
        self.reads = reads

    def __enter__(self):
        self.wrapped.__enter__()
        return self

    def __exit__(self, *args):
        return self.wrapped.__exit__(*args)

    def read(self, size=-1):
        self.reads.append(size)
        return self.wrapped.read(size)


def test_read_file_uses_bounded_streaming_reads(monkeypatch, tmp_path):
    path = tmp_path / "many_lines.txt"
    path.write_text("\n".join(f"line-{i}" for i in range(1, 10001)), encoding="utf-8")
    real_open = builtins.open
    reads = []

    def spy_open(*args, **kwargs):
        return _ReadSpy(real_open(*args, **kwargs), reads)

    monkeypatch.setattr(builtins, "open", spy_open)
    result = BaseTools(tmp_path).read_file("many_lines.txt", start_line=2, end_line=3)

    assert result.success
    assert "line-2" in result.content and "line-3" in result.content
    assert reads and all(0 < size <= FILE_READ_CHUNK_BYTES for size in reads)
    assert -1 not in reads


def test_read_file_handles_utf8_and_crlf_across_chunk_boundaries(monkeypatch, tmp_path):
    path = tmp_path / "mixed.txt"
    path.write_bytes("1234\r\n中文\n末尾".encode("utf-8"))
    monkeypatch.setattr(base_tools_module, "FILE_READ_CHUNK_BYTES", 5)

    result = BaseTools(tmp_path).read_file("mixed.txt")

    assert result.success
    assert "LINES: 1-3 of 3" in result.content
    assert "1234\n中文\n末尾" in result.content
    assert "末尾" in result.content


def test_read_file_boundary_errors_are_not_off_by_one(tmp_path):
    path = tmp_path / "three.txt"
    path.write_text("one\ntwo\nthree", encoding="utf-8")
    tools = BaseTools(tmp_path)

    beyond_eof = tools.read_file("three.txt", start_line=4)
    reversed_range = tools.read_file("three.txt", start_line=3, end_line=2)

    assert not beyond_eof.success
    assert not reversed_range.success


def test_read_file_result_keeps_absolute_output_bounds_for_long_line(tmp_path):
    path = tmp_path / "single_line.txt"
    path.write_text("x" * (READ_FILE_MAX_CHARS * 3), encoding="utf-8")

    result = BaseTools(tmp_path).read_file("single_line.txt", end_line=999999)

    assert result.success
    assert len(result.content) <= TOOL_OUTPUT_MAX_CHARS
    assert len(result.content.encode("utf-8")) <= TOOL_OUTPUT_MAX_BYTES
    assert len(result.content.encode("utf-8")) < READ_FILE_MAX_BYTES + 10000
    assert "内容被截断" in result.content


def _write_transcript(path, transcript_id, summary, created_at):
    path.write_text(json.dumps({
        "id": transcript_id,
        "summary": summary,
        "message_count": 1,
        "original_token_estimate": 1,
        "created_at": created_at,
    }), encoding="utf-8")


def test_transcript_retention_is_enforced_during_initialization(tmp_path):
    for index in range(3):
        _write_transcript(
            tmp_path / f"transcript_id-{index}.json",
            f"id-{index}",
            f"summary-{index}",
            index,
        )

    zero = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 0})
    assert zero.get_transcripts() == []
    assert not list(tmp_path.glob("transcript_*.json"))


def test_transcript_duplicate_ids_have_deterministic_canonical_file(tmp_path):
    _write_transcript(tmp_path / "transcript_dup.json", "dup", "old", 1)
    _write_transcript(tmp_path / "transcript_dup-copy.json", "dup", "new", 2)

    compressor = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 1})

    transcripts = compressor.get_transcripts()
    assert len(transcripts) == 1
    assert transcripts[0].id == "dup"
    assert transcripts[0].summary == "new"
    assert not (tmp_path / "transcript_dup.json").exists()
    assert (tmp_path / "transcript_dup-copy.json").exists()


def test_transcript_save_failure_is_not_published_in_memory(monkeypatch, tmp_path):
    compressor = Compressor({"transcript_dir": str(tmp_path)})

    def fail_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(builtins, "open", fail_open)
    saved = compressor._save_transcript(
        CompressedTranscript(id="failed", summary="summary"),
    )

    assert saved is False
    assert compressor.get_transcripts() == []
    assert not (tmp_path / "transcript_failed.json").exists()


def test_transcript_delete_failure_remains_observable_and_indexed(monkeypatch, tmp_path, caplog):
    old_path = tmp_path / "transcript_old.json"
    new_path = tmp_path / "transcript_new.json"
    _write_transcript(old_path, "old", "old", 1)
    _write_transcript(new_path, "new", "new", 2)

    original_unlink = Path.unlink

    def fail_old_unlink(self, *args, **kwargs):
        if self == old_path:
            raise OSError("permission denied")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_old_unlink)
    compressor = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 1})

    assert {item.id for item in compressor.get_transcripts()} == {"old", "new"}
    assert old_path.exists()
    assert "删除旧 Transcript" in caplog.text
