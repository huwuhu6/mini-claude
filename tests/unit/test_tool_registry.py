import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.tools.registry import ToolRegistry, ToolSpec
from providers.base import ToolDefinition as ProviderToolDef


MAIN_AGENT_TOOL_NAMES = [
    "bash",
    "run_background",
    "get_background_status",
    "get_background_logs",
    "stop_background",
    "health_check",
    "read_file",
    "write_file",
    "edit_file",
    "update_agent_note",
    "load_skill",
    "task",
    "search_code",
    "count_occurrences",
    "list_files",
]


def _agent_without_initialization():
    agent = object.__new__(MiniClaudeAgent)
    agent.feature_manager = type(
        "FeatureManagerStub",
        (),
        {
            "filter_tools": staticmethod(lambda tools: tools),
            "is_enabled": staticmethod(lambda _name: True),
        },
    )()
    agent.tool_registry = ToolRegistry()
    agent._register_tools()
    return agent


def test_main_agent_tool_names_match_current_exposed_set():
    agent = _agent_without_initialization()

    assert [tool["name"] for tool in agent._get_llm_tools()] == MAIN_AGENT_TOOL_NAMES


def test_every_exposed_tool_has_schema_and_handler():
    agent = _agent_without_initialization()
    definitions = agent._get_llm_tools()

    for definition in definitions:
        assert definition["input_schema"]["type"] == "object"
        assert agent.tool_registry.has_handler(definition["name"])

    assert "TodoWrite" not in [tool["name"] for tool in definitions]


def test_registry_unknown_tool_raises_key_error():
    registry = ToolRegistry()

    with pytest.raises(KeyError, match="unknown_tool"):
        registry.execute("unknown_tool", {})


def test_agent_unknown_tool_keeps_legacy_error_result():
    agent = _agent_without_initialization()

    assert agent._execute_tool("unknown_tool", {}) == "错误: 未知工具 'unknown_tool'"


def test_agent_handler_argument_errors_keep_legacy_result():
    agent = _agent_without_initialization()

    result = agent._execute_tool("read_file", {"unexpected": True})

    assert result.startswith("错误: 工具 'read_file' 参数无效。")


def test_registry_definitions_match_provider_tool_format():
    agent = _agent_without_initialization()

    provider_defs = [ProviderToolDef(**tool) for tool in agent.tool_registry.definitions()]

    assert [tool.name for tool in provider_defs] == MAIN_AGENT_TOOL_NAMES


def test_read_file_dispatches_through_registry():
    agent = _agent_without_initialization()
    agent.tools = type(
        "ToolsStub",
        (),
        {
            "read_file": staticmethod(
                lambda path, start_line=None, end_line=None:
                    type("Result", (), {"content": path})()
            ),
        },
    )()

    result = agent.tool_registry.execute("read_file", {"path": "sample.py"})

    assert result == "sample.py"
