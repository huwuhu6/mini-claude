import sys
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.features import FeatureManager
from core.subagent import SubAgent, SubAgentType
from core.tools.base_tools import BaseTools, ToolResult
from core.tools.registry import ToolRegistry, ToolSpec
from models.config import FeaturesConfig
from providers.base import ToolDefinition as ProviderToolDef


@pytest.fixture
def workspace():
    path = Path.cwd() / f"test-subagent-tools-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


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
    "TodoWrite",
]


def _agent_without_initialization(feature_manager=None):
    agent = object.__new__(MiniClaudeAgent)
    agent.feature_manager = feature_manager or type(
        "FeatureManagerStub", (),
        {"filter_tools": staticmethod(lambda tools: tools),
         "is_enabled": staticmethod(lambda _name: True)},
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

    assert "TodoWrite" in [tool["name"] for tool in definitions]


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


def _definitions_by_name(registry):
    return {tool["name"]: tool for tool in registry.definitions()}


def test_shared_main_and_subagent_tools_have_identical_schemas(workspace):
    main = _agent_without_initialization()
    subagent = SubAgent(SubAgentType.GENERAL, workdir=workspace)
    main_defs = _definitions_by_name(main.tool_registry)
    sub_defs = _definitions_by_name(subagent.tool_registry)

    for name in ("bash", "read_file", "write_file", "edit_file"):
        assert main_defs[name]["input_schema"] == sub_defs[name]["input_schema"]


def test_subagent_types_keep_their_existing_tool_sets_and_never_get_task(workspace):
    expected_general = ["bash", "read_file", "write_file", "edit_file"]
    general = SubAgent(SubAgentType.GENERAL, workdir=workspace)
    plan = SubAgent(SubAgentType.PLAN, workdir=workspace)
    review = SubAgent(SubAgentType.REVIEW, workdir=workspace)
    explore = SubAgent(SubAgentType.EXPLORE, workdir=workspace)

    assert [t["name"] for t in general.tool_registry.definitions()] == expected_general
    assert [t["name"] for t in plan.tool_registry.definitions()] == expected_general
    assert [t["name"] for t in review.tool_registry.definitions()] == expected_general
    assert [t["name"] for t in explore.tool_registry.definitions()] == ["read_file"]
    for subagent in (general, plan, review, explore):
        assert not subagent.tool_registry.has_handler("task")


def test_main_and_subagents_have_independent_registries(workspace):
    main = _agent_without_initialization()
    general = SubAgent(SubAgentType.GENERAL, workdir=workspace)
    explore = SubAgent(SubAgentType.EXPLORE, workdir=workspace)

    assert main.tool_registry is not general.tool_registry
    assert general.tool_registry is not explore.tool_registry
    general.tool_registry.register(ToolSpec("subagent_only", "", {}, lambda: "ok"))
    assert not main.tool_registry.has_handler("subagent_only")
    assert not explore.tool_registry.has_handler("subagent_only")


def test_subagent_read_file_dispatch_uses_schema_parameters(workspace, monkeypatch):
    received = {}

    def read_file(self, path, start_line=None, end_line=None):
        received.update(path=path, start_line=start_line, end_line=end_line)
        return ToolResult("read")

    monkeypatch.setattr(BaseTools, "read_file", read_file)
    subagent = SubAgent(SubAgentType.EXPLORE, workdir=workspace)
    definition = _definitions_by_name(subagent.tool_registry)["read_file"]

    assert set(definition["input_schema"]["properties"]) == {
        "path", "start_line", "end_line",
    }
    assert "limit" not in definition["input_schema"]["properties"]
    assert subagent.execute_tool(
        "read_file", {"path": "sample.py", "start_line": 3, "end_line": 8},
    ) == "read"
    assert received == {"path": "sample.py", "start_line": 3, "end_line": 8}


def test_subagent_edit_file_dispatch_uses_schema_parameters(workspace, monkeypatch):
    received = {}

    def edit_file(self, path, edits):
        received.update(path=path, edits=edits)
        return ToolResult("edited")

    monkeypatch.setattr(BaseTools, "edit_file", edit_file)
    subagent = SubAgent(SubAgentType.GENERAL, workdir=workspace)
    definition = _definitions_by_name(subagent.tool_registry)["edit_file"]
    edits = [{"search": "before", "replace": "after"}]

    assert set(definition["input_schema"]["properties"]) == {"path", "edits"}
    assert subagent.execute_tool("edit_file", {"path": "sample.py", "edits": edits}) == "edited"
    assert received == {"path": "sample.py", "edits": edits}


def test_subagent_unknown_tool_keeps_legacy_result(workspace):
    subagent = SubAgent(SubAgentType.GENERAL, workdir=workspace)

    assert subagent.execute_tool("unknown_tool", {}) == "Unknown tool: unknown_tool"


def test_subagent_run_sends_registry_definitions_to_provider(workspace):
    class RecordingProvider:
        model = "test-model"

        def create_message(self, _messages, tools):
            self.tools = tools
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="done", tool_calls=[]),
                )],
            )

    provider = RecordingProvider()
    subagent = SubAgent(SubAgentType.GENERAL, workdir=workspace, provider=provider)

    result = subagent.run("inspect", max_iterations=1)

    assert result.success
    assert [tool.name for tool in provider.tools] == [
        "bash", "read_file", "write_file", "edit_file",
    ]
    assert all(isinstance(tool, ProviderToolDef) for tool in provider.tools)


def test_main_agent_feature_flags_gate_production_tool_wiring():
    main = object.__new__(MiniClaudeAgent)
    main.config = SimpleNamespace(features=FeaturesConfig())
    main.feature_manager = FeatureManager()
    main.tool_registry = ToolRegistry()
    main._register_features()
    main._register_tools()

    tools_by_feature = {
        "bash": {"bash"},
        "read_file": {"read_file"},
        "write_file": {"write_file"},
        "edit_file": {"edit_file"},
        "subagent": {"task"},
        "background": {
            "run_background", "get_background_status", "get_background_logs",
            "stop_background", "health_check",
        },
        "skills": {"load_skill"},
        "compression": {"update_agent_note"},
    }
    initially_visible = {tool["name"] for tool in main._get_llm_tools()}
    all_mapped_tools = set().union(*tools_by_feature.values())
    assert all_mapped_tools <= initially_visible

    for feature_name, feature_tools in tools_by_feature.items():
        assert main.feature_manager.disable(feature_name)
        visible = {tool["name"] for tool in main._get_llm_tools()}
        assert feature_tools.isdisjoint(visible)
        assert all_mapped_tools - feature_tools <= visible

        assert main.feature_manager.enable(feature_name)
        visible = {tool["name"] for tool in main._get_llm_tools()}
        assert feature_tools <= visible
