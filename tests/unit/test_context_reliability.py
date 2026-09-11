"""Deterministic regressions for Context Foundation reliability semantics."""

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
from providers.base import Message


def _long_history():
    return [Message(role="system", content="system"), Message(role="user", content="intro")] + [
        Message(role="user", content=f"middle-{i}") for i in range(4)
    ] + [Message(role="user", content=f"tail-{i}") for i in range(16)]


class _SummaryProvider:
    def __init__(self, response=None, error=None, parse_error=None):
        self.response = response
        self.error = error
        self.parse_error = parse_error

    def create_message(self, messages, **kwargs):
        if self.error:
            raise self.error
        return self.response

    def parse_response(self, response):
        if self.parse_error:
            raise self.parse_error
        return response


def test_full_compression_commits_only_after_provider_summary_succeeds():
    compressor = Compressor()
    compressor.set_provider(_SummaryProvider(response={"content": "kept summary"}))

    compressed = compressor.compress(_long_history())

    assert len(compressed) < len(_long_history())
    assert "kept summary" in compressed[2].content
    assert len(compressor.get_transcripts()) == 1


@pytest.mark.parametrize(
    "provider",
    [
        _SummaryProvider(error=TimeoutError("temporary outage")),
        _SummaryProvider(response={"content": ""}),
        _SummaryProvider(response={"unexpected": True}),
        _SummaryProvider(response={"content": "ignored"}, parse_error=ValueError("bad response")),
    ],
)
def test_full_compression_fail_closed_preserves_history_and_transcript_state(provider):
    compressor = Compressor()
    compressor.set_provider(provider)
    original = _long_history()

    result = compressor.compress(original)

    assert result == original
    assert result is original
    assert compressor.get_transcripts() == []


def test_full_compression_without_provider_keeps_statistical_fallback():
    compressor = Compressor()

    result = compressor.compress(_long_history())

    assert len(result) < len(_long_history())
    assert "user messages" in result[2].content
    assert len(compressor.get_transcripts()) == 1


def test_auto_compression_does_not_report_failed_full_compression_as_success():
    compressor = Compressor({
        "microcompact_token_threshold": 1,
        "full_compression_token_threshold": 2,
    })
    compressor.set_provider(_SummaryProvider(error=TimeoutError("temporary outage")))

    agent = object.__new__(MiniClaudeAgent)
    agent.compressor = compressor
    agent.messages = _long_history()
    agent.feature_manager = types.SimpleNamespace(is_enabled=lambda name: True)
    original = list(agent.messages)

    assert agent._check_auto_compress() is False
    assert agent.messages == original
    assert compressor.get_transcripts() == []


def test_transcript_retention_loads_previous_process_and_prunes_oldest(tmp_path):
    compressor_a = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 2})
    compressor_a._save_transcript(CompressedTranscript(id="old", summary="old", created_at=1))
    compressor_a._save_transcript(CompressedTranscript(id="middle", summary="middle", created_at=2))

    compressor_b = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 2})
    assert {item.id for item in compressor_b.get_transcripts()} == {"old", "middle"}

    compressor_b._save_transcript(CompressedTranscript(id="new", summary="new", created_at=3))

    assert {item.id for item in compressor_b.get_transcripts()} == {"middle", "new"}
    assert not (tmp_path / "transcript_old.json").exists()
    assert (tmp_path / "transcript_middle.json").exists()
    assert (tmp_path / "transcript_new.json").exists()


def test_transcript_retention_ignores_corrupt_and_unrelated_files(tmp_path):
    (tmp_path / "transcript_corrupt.json").write_text("{not json", encoding="utf-8")
    unrelated = tmp_path / "notes.json"
    unrelated.write_text(json.dumps({"keep": True}), encoding="utf-8")

    compressor = Compressor({"transcript_dir": str(tmp_path), "max_transcripts": 1})

    assert compressor.get_transcripts() == []
    assert (tmp_path / "transcript_corrupt.json").exists()
    assert unrelated.exists()
