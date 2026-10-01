"""Runtime contract for keeping unfinished task/team collaboration dormant."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.features import FeatureManager
from core.runtime_context.preflight import PreflightResult
from models.config import FeaturesConfig
from providers.base import Message


def _registered_agent():
    agent = object.__new__(MiniClaudeAgent)
    agent.config = SimpleNamespace(
        features=FeaturesConfig(),
        agent=SimpleNamespace(name="MiniClaude", version="test"),
    )
    agent.feature_manager = FeatureManager()
    agent._register_features()
    return agent


def test_production_features_and_prompt_exclude_dormant_multi_agent_features():
    agent = _registered_agent()
    active = set(agent.feature_manager.get_enabled_features())

    assert {"bash", "read_file", "write_file", "edit_file", "subagent",
            "compression", "memory", "background", "skills"} <= active
    assert "tasks" not in active
    assert "team" not in active

    agent.workdir = Path("/workspace/project")
    agent.preflight = PreflightResult(
        network_access="OFFLINE",
        detected_toolchains={"Python": "3.14"},
        workspace_root=str(agent.workdir),
        workspace_read_write=True,
    )
    agent.refresh_system_prompt()
    enabled_line = next(
        line for line in agent.system_prompt.splitlines()
        if line.startswith("Enabled features: ")
    )
    assert "tasks" not in enabled_line
    assert "team" not in enabled_line


def test_run_appends_user_message_and_enters_llm_cycle_directly():
    agent = object.__new__(MiniClaudeAgent)
    agent.messages = []
    observed = {}

    def llm_tool_cycle(*, require_tool_call=False):
        observed["messages"] = list(agent.messages)
        observed["require_tool_call"] = require_tool_call
        return "response"

    agent._llm_tool_cycle = llm_tool_cycle

    result = agent.run("continue", require_tool_call=True)

    assert result == "response"
    assert agent._current_user_prompt == "continue"
    assert observed["messages"] == [Message(role="user", content="continue")]
    assert observed["require_tool_call"] is True
    assert not hasattr(MiniClaudeAgent, "_run_simple")
    assert not hasattr(MiniClaudeAgent, "_run_with_tasks")


def test_hot_context_skips_inbox_but_keeps_other_runtime_sources():
    agent = object.__new__(MiniClaudeAgent)
    agent.agent_note = SimpleNamespace(read=lambda: "saved note")
    agent._compression_notice_pending = False
    agent.todo = SimpleNamespace(has_open_items=lambda: False)
    agent._drain_background_notifications = lambda: "background result"
    agent.memory = SimpleNamespace(render=lambda: "recent file observation")
    agent.feature_manager = SimpleNamespace(
        is_enabled=lambda name: name == "memory",
    )
    agent._refresh_structured_memory_freshness = lambda: None
    agent._check_inbox = lambda: pytest.fail("hot context must not consume team inbox")

    context = agent._get_dynamic_hot_context()

    assert "saved note" in context
    assert "background result" in context
    assert "recent file observation" in context
    assert "<inbox>" not in context
