import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.features import FeatureManager
from core.tools.registry import ToolRegistry
from models.config import FeaturesConfig
from models.todo import TodoManager
from providers.base import Message


def _registered_agent():
    agent = object.__new__(MiniClaudeAgent)
    agent.config = SimpleNamespace(features=FeaturesConfig())
    agent.feature_manager = FeatureManager()
    agent._register_features()
    agent.tool_registry = ToolRegistry()
    agent._register_tools()
    agent.todo = TodoManager()
    return agent


def _item(content, status="pending"):
    return {
        "content": content,
        "status": status,
        "activeForm": f"Working on {content}",
    }


def test_todowrite_is_registered_and_not_hidden_by_feature_flags():
    agent = _registered_agent()
    for feature_name in agent.feature_manager.get_enabled_features():
        agent.feature_manager.disable(feature_name)

    tools = {tool["name"]: tool for tool in agent._get_llm_tools()}

    assert "TodoWrite" in tools
    assert tools["TodoWrite"]["input_schema"]["properties"]["items"]["maxItems"] == 20
    assert "task" not in tools
    assert not agent.feature_manager.is_enabled("tasks")
    assert not agent.feature_manager.is_enabled("team")


def test_todowrite_handler_updates_manager_and_returns_stable_result():
    agent = _registered_agent()
    items = [_item("Inspect interfaces", "in_progress"), _item("Implement change")]

    result = agent.tool_registry.execute("TodoWrite", {"items": items})

    assert result == "Todo state successfully updated in the system background."
    assert agent.todo.items == items


def test_todo_manager_allows_at_most_one_in_progress_item():
    manager = TodoManager()

    with pytest.raises(ValueError, match="Only one in_progress allowed"):
        manager.update([
            _item("First", "in_progress"),
            _item("Second", "in_progress"),
        ])


def test_todo_manager_rejects_more_than_twenty_items():
    manager = TodoManager()

    with pytest.raises(ValueError, match="Max 20 todos"):
        manager.update([_item(f"Task {index}") for index in range(21)])


def test_todo_manager_rejects_invalid_status_without_replacing_current_state():
    manager = TodoManager()
    manager.update([_item("Keep this task")])

    with pytest.raises(ValueError, match="invalid status"):
        manager.update([_item("Invalid task", "blocked")])

    assert manager.items == [_item("Keep this task")]


def test_new_agent_run_clears_todos_from_previous_task():
    agent = _registered_agent()
    agent.todo.update([_item("Previous task", "in_progress")])
    agent.messages = []
    observed = {}

    def stop_before_provider(*, require_tool_call=False):
        observed["items"] = list(agent.todo.items)
        return "done"

    agent._llm_tool_cycle = stop_before_provider

    assert agent.run("A new task") == "done"
    assert observed["items"] == []
    assert agent.messages == [Message(role="user", content="A new task")]


def _hot_context_agent(items):
    agent = object.__new__(MiniClaudeAgent)
    agent.todo = TodoManager()
    agent.todo.update(items)
    agent.agent_note = SimpleNamespace(read=lambda: "")
    agent._compression_notice_pending = False
    agent.feature_manager = SimpleNamespace(is_enabled=lambda _name: False)
    agent._drain_background_notifications = lambda: None
    agent.memory = None
    return agent


def test_open_todos_are_transient_hot_context_and_completed_todos_do_not_nag():
    agent = _hot_context_agent([_item("Implement change", "in_progress")])

    open_context = agent._get_dynamic_hot_context()

    assert "<todo-status>" in open_context
    assert "Implement change" in open_context
    assert "<nag>" not in open_context

    agent.todo.update([_item("Implement change", "completed")])
    completed_context = agent._get_dynamic_hot_context()

    assert "<todo-status>" not in completed_context
    assert "<nag>" not in completed_context


def test_todowrite_result_and_hot_context_preserve_persisted_message_history():
    agent = _registered_agent()
    agent.agent_note = SimpleNamespace(read=lambda: "")
    agent._compression_notice_pending = False
    agent._drain_background_notifications = lambda: None
    agent.memory = None
    agent.messages = [Message(role="user", content="Implement the feature")]
    items = [_item("Add the feature", "in_progress")]

    call_result = agent._execute_tool("TodoWrite", {"items": items})
    agent.messages.extend([
        Message(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "TodoWrite",
                    "arguments": '{"items":[{"content":"Add the feature"}]}',
                },
            }],
        ),
        Message(role="tool", content=call_result, tool_call_id="call-1"),
    ])
    history_before = copy.deepcopy(agent.messages)

    hot_context = agent._get_dynamic_hot_context()
    request_messages = agent._build_request_messages(hot_context)

    assert call_result == "Todo state successfully updated in the system background."
    assert agent.messages == history_before
    assert request_messages is not agent.messages
    assert "Add the feature" in request_messages[-1].content
    assert "Add the feature" not in agent.messages[-1].content
