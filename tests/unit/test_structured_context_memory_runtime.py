"""Deterministic runtime wiring tests for transient structured file memory."""
import shutil
import sys
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.context_memory import StructuredContextMemory
from core.features import FeatureManager
from core.tools.base_tools import BaseTools, ToolResult
from models.config import ConfigManager, FeaturesConfig
from providers.base import Message


class _Features:
    def __init__(self, memory_enabled: bool):
        self.memory_enabled = memory_enabled

    def is_enabled(self, name: str) -> bool:
        return name == "memory" and self.memory_enabled


@pytest.fixture
def workspace():
    path = Path.cwd() / f"test-context-memory-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _agent(workspace, *, memory_enabled=True):
    agent = object.__new__(MiniClaudeAgent)
    agent.memory = StructuredContextMemory()
    agent._memory_file_fingerprints = {}
    agent.feature_manager = _Features(memory_enabled)
    agent.tools = BaseTools(workspace)
    agent.workdir = workspace.resolve()
    agent.agent_note = types.SimpleNamespace(read=lambda: "")
    agent._compression_notice_pending = False
    agent.todo = types.SimpleNamespace(has_open_items=lambda: False)
    agent._drain_background_notifications = lambda: None
    agent._check_inbox = lambda: None
    agent.messages = [Message(role="user", content="inspect the file")]
    return agent


def test_memory_feature_defaults_on_and_registers_in_feature_manager():
    agent = object.__new__(MiniClaudeAgent)
    agent.config = types.SimpleNamespace(features=FeaturesConfig())
    agent.feature_manager = FeatureManager()

    agent._register_features()

    assert agent.config.features.memory is True
    assert agent.feature_manager.is_enabled("memory") is True


def test_default_yaml_enables_memory_and_reserves_multi_agent_features():
    config = ConfigManager(ROOT / "configs" / "default.yaml").get_config()

    feature_defaults = FeaturesConfig()

    assert feature_defaults.tasks is False
    assert feature_defaults.team is False
    assert config.features.memory is True
    assert config.features.tasks is False
    assert config.features.team is False


def test_successful_read_records_actual_range_and_bounded_observation(workspace):
    path = workspace / "app.py"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    agent = _agent(workspace)
    result = agent.tools.read_file("app.py", start_line=2, end_line=3)

    agent._update_structured_memory("read_file", {"path": "app.py"}, result, True)

    observation = agent.memory.observations[0]
    assert (observation.path, observation.start_line, observation.end_line) == ("app.py", 2, 3)
    assert "Read lines 2-3 of 4" in observation.observation
    assert observation.freshness


def test_successful_write_invalidates_existing_file_observations(workspace):
    path = workspace / "app.py"
    path.write_text("before\n", encoding="utf-8")
    agent = _agent(workspace)
    read_result = agent.tools.read_file("app.py")
    agent._update_structured_memory("read_file", {"path": "app.py"}, read_result, True)

    path.write_text("after\n", encoding="utf-8")
    agent._update_structured_memory(
        "write_file", {"path": "app.py"}, ToolResult("written"), True,
    )

    assert agent.memory.observations == ()
    assert agent.memory.recent_files == ("app.py",)


def test_path_aliases_share_one_canonical_runtime_identity_then_invalidate(workspace):
    path = workspace / "app.py"
    path.write_text("before\n", encoding="utf-8")
    agent = _agent(workspace)
    result = agent.tools.read_file("./app.py")

    agent._update_structured_memory("read_file", {"path": "./app.py"}, result, True)
    agent._update_structured_memory(
        "edit_file", {"path": "src/../app.py"}, ToolResult("edited"), True,
    )

    assert {
        agent._canonical_workspace_file(alias)[0]
        for alias in ("app.py", "./app.py", "src/../app.py")
    } == {"app.py"}
    assert agent.memory.recent_files == ("app.py",)
    assert agent.memory.observations == ()


def test_unchanged_recent_file_does_not_rehash_on_each_hot_context_render(workspace, monkeypatch):
    path = workspace / "app.py"
    path.write_text("line\n", encoding="utf-8")
    agent = _agent(workspace)
    result = agent.tools.read_file("app.py")
    agent._update_structured_memory("read_file", {"path": "app.py"}, result, True)

    original_open = Path.open
    reads = 0

    def counting_open(self, *args, **kwargs):
        nonlocal reads
        if self == path and args and args[0] == "rb":
            reads += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    agent._get_dynamic_hot_context()
    agent._get_dynamic_hot_context()

    assert reads == 0


def test_external_file_drift_invalidates_before_transient_render(workspace):
    path = workspace / "app.py"
    path.write_text("before\n", encoding="utf-8")
    agent = _agent(workspace)
    read_result = agent.tools.read_file("app.py")
    agent._update_structured_memory("read_file", {"path": "app.py"}, read_result, True)

    path.write_text("after\n", encoding="utf-8")
    hot_text = agent._get_dynamic_hot_context()

    assert agent.memory.observations == ()
    assert "structured-file-memory" in hot_text
    assert "before" not in hot_text


def test_memory_is_transient_and_does_not_break_existing_tool_chain(workspace):
    path = workspace / "app.py"
    path.write_text("line\n", encoding="utf-8")
    agent = _agent(workspace)
    result = agent.tools.read_file("app.py")
    agent._update_structured_memory("read_file", {"path": "app.py"}, result, True)
    agent.messages = [
        Message(role="assistant", content="", tool_calls=[{
            "id": "call-1", "function": {"name": "read_file", "arguments": "{}"},
        }]),
        Message(role="tool", content="result", tool_call_id="call-1"),
    ]

    hot_text = agent._get_dynamic_hot_context()
    request = agent._build_request_messages(hot_text)

    assert len(agent.messages) == 2
    assert agent.messages[0].tool_calls[0]["id"] == "call-1"
    assert agent.messages[1].tool_call_id == "call-1"
    assert request[-1].role == "user"
    assert "<structured-file-memory>" in request[-1].content


def test_memory_false_preserves_baseline_behavior(workspace):
    path = workspace / "app.py"
    path.write_text("line\n", encoding="utf-8")
    agent = _agent(workspace, memory_enabled=False)
    result = agent.tools.read_file("app.py")

    agent._update_structured_memory("read_file", {"path": "app.py"}, result, True)

    assert agent.memory.recent_files == ()
    assert agent._get_dynamic_hot_context() == ""
    assert agent._build_request_messages("") is agent.messages
