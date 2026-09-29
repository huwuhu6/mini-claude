"""Notes must survive the first destructive compression pass."""

from types import SimpleNamespace
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid

from agent.mini_claude_agent import MiniClaudeAgent
from core.agent_note import AgentNote
from core.compression import Compressor
from providers.base import Message


def _tool_history(count=5):
    messages = [Message(role="user", content="使用 JDK 21")]
    for index in range(count):
        call_id = f"call_{index}"
        messages.append(Message(
            role="assistant", content="", tool_calls=[{
                "id": call_id,
                "function": {"name": "bash", "arguments": "{}"},
            }],
        ))
        messages.append(Message(
            role="tool", content=f"重要诊断 {index}: " + "x" * 1500,
            tool_call_id=call_id,
        ))
    return messages


@contextmanager
def _note_directory():
    path = Path.cwd() / f"test-agent-note-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_note_is_bounded_and_session_scoped():
    with _note_directory() as data_root:
        _check_note_bounds(data_root)


def _check_note_bounds(data_root):
    first = AgentNote(data_root)
    second = AgentNote(data_root)
    assert first.path != second.path
    assert first.read() == ""

    agent = MiniClaudeAgent.__new__(MiniClaudeAgent)
    agent.agent_note = first
    assert agent._handle_update_agent_note("JDK 21").success
    assert first.read() == "JDK 21"
    assert second.read() == ""
    assert not agent._handle_update_agent_note("x" * (AgentNote.MAX_CHARS + 1)).success
    assert first.read() == "JDK 21"


def test_warning_precedes_microcompression_and_note_survives():
    with _note_directory() as data_root:
        _check_microcompression(data_root)


def _check_microcompression(data_root):
    agent = MiniClaudeAgent.__new__(MiniClaudeAgent)
    agent.agent_note = AgentNote(data_root)
    agent.feature_manager = SimpleNamespace(is_enabled=lambda name: True)
    agent.messages = _tool_history()
    measured = Compressor().estimate_tokens(agent.messages)
    agent.compressor = Compressor({"token_threshold": int(measured / 0.8)})
    agent._compression_notice_pending = False
    agent.todo = SimpleNamespace(has_open_items=lambda: False)
    agent._drain_background_notifications = lambda: None
    agent._check_inbox = lambda: None

    assert not agent._check_auto_compress()
    assert "重要诊断 0" in agent.messages[2].content
    assert "update_agent_note" in agent._get_dynamic_hot_context()

    assert agent._handle_update_agent_note("用户约束：JDK 21\n已验证：重要诊断 0").success
    assert agent._check_auto_compress()
    assert "重要诊断 0" not in agent.messages[2].content
    assert "JDK 21" in agent._get_dynamic_hot_context()
    assert not agent._check_auto_compress()
    assert not agent._compression_notice_pending


def test_full_compression_also_waits_for_warning():
    with _note_directory() as data_root:
        _check_full_compression(data_root)


def _check_full_compression(data_root):
    agent = MiniClaudeAgent.__new__(MiniClaudeAgent)
    agent.agent_note = AgentNote(data_root)
    agent.feature_manager = SimpleNamespace(is_enabled=lambda name: True)
    agent.compressor = Compressor({"token_threshold": 1000})
    agent.messages = _tool_history(10)
    agent._compression_notice_pending = False

    assert not agent._check_auto_compress()
    assert len(agent.messages) == 21
    assert agent._check_auto_compress()
    assert not agent._compression_notice_pending
    assert len(agent.messages) < 21
