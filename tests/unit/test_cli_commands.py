from types import SimpleNamespace

import pytest

from agent.mini_claude_agent import MiniClaudeAgent
from cli.commands import create_agent_console, execute_agent_command
from cli.console import Command, ConsoleCommandSystem
from cli.entrypoint import _dispatch_input
from core.features import FeatureDefinition, FeatureManager


class Recorder:
    def __init__(self):
        self.rounds = 0
        self.events = []

    def start_round(self):
        self.rounds += 1

    def record(self, event_type, **data):
        self.events.append((event_type, data))


def test_generic_console_help_alias_history_unknown_and_malformed_input():
    console = ConsoleCommandSystem()
    console.register(Command(
        "status", "Show status", lambda _args, _context: "ready",
        aliases=["st"],
    ))

    assert "可用命令" in console.execute("/help")
    assert console.execute("/st") == "ready"
    assert console.get_history() == ["/help", "/st"]
    assert "未知命令" in console.execute("/statuz")
    assert "/st" in console.execute("/statuz")
    assert "命令解析失败" in console.execute('/skills "unfinished')
    with pytest.raises(SystemExit):
        console.execute("/exit")
    assert console.get_command("clear") is None


def _minimal_command_agent():
    agent = SimpleNamespace(
        messages=["old conversation"],
        _last_assistant_note="previous note",
        _compression_notice_pending=True,
        session_recorder=Recorder(),
    )
    return agent


def test_agent_command_registration_preserves_commands_and_aliases():
    console = create_agent_console(_minimal_command_agent())
    names = {command.name for command in console.get_all_commands()}
    assert names == {
        "help", "exit", "status", "stats", "config", "tasks", "team", "inbox",
        "features", "compact", "providers", "add_workdir", "whitelist", "skills",
        "clear",
    }
    for alias in ("quit", "task", "addpath", "workdirs", "skill"):
        assert console.get_command(alias) is not None


def test_clear_really_clears_only_conversation_state():
    agent = _minimal_command_agent()
    tasks = object()
    agent.task_manager = tasks
    console = create_agent_console(agent)

    assert console.execute("/clear") == "对话历史已清除。"
    assert agent.messages == []
    assert agent._last_assistant_note is None
    assert agent._compression_notice_pending is False
    assert agent.task_manager is tasks


def test_tasks_invalid_status_has_explicit_values():
    agent = _minimal_command_agent()
    agent.task_manager = SimpleNamespace(list=lambda **_kwargs: pytest.fail("should not list"))
    result = create_agent_console(agent).execute("/tasks nonsense")
    assert "未知任务状态: nonsense" in result
    assert "pending, running, completed, failed, blocked, cancelled" in result


def test_features_enable_skills_discovers_and_refreshes_prompt():
    features = FeatureManager()
    features.register_feature(FeatureDefinition("skills", enabled=False))
    loader = SimpleNamespace(
        discoveries=0,
        discover=lambda: (setattr(loader, "discoveries", loader.discoveries + 1) or ["sample"]),
        descriptions=lambda: "sample: skill description" if loader.discoveries else "",
    )
    agent = _minimal_command_agent()
    agent.feature_manager = features
    agent.skill_loader = loader

    def refresh_prompt():
        enabled = ", ".join(features.get_enabled_features()) or "base"
        skill = loader.descriptions() if features.is_enabled("skills") else ""
        agent.system_prompt = f"Enabled features: {enabled}\n{skill}"

    agent._load_system_prompt = refresh_prompt
    console = create_agent_console(agent)

    assert "已enable" in console.execute("/features enable skills")
    assert loader.discoveries == 1
    assert "skills" in agent.system_prompt
    assert "sample: skill description" in agent.system_prompt

    assert "已disable" in console.execute("/features disable skills")
    assert "skills" not in agent.system_prompt
    assert "sample: skill description" not in agent.system_prompt


def test_command_execution_records_input_and_result():
    agent = _minimal_command_agent()
    console = ConsoleCommandSystem()
    console.register(Command("status", "Status", lambda _args, _ctx: "ok"))

    assert execute_agent_command(console, agent, "/status") == "ok"
    assert agent.session_recorder.rounds == 1
    assert [event for event, _data in agent.session_recorder.events] == [
        "user_input", "command_result",
    ]
    assert agent.session_recorder.events[0][1]["content"] == "/status"
    assert agent.session_recorder.events[1][1]["result"] == "ok"


def test_exit_records_input_without_command_result():
    agent = _minimal_command_agent()
    with pytest.raises(SystemExit):
        execute_agent_command(ConsoleCommandSystem(), agent, "/exit")
    assert [event for event, _data in agent.session_recorder.events] == ["user_input"]


def test_cli_dispatches_slash_commands_and_plain_text_to_separate_paths():
    agent = _minimal_command_agent()
    agent.chat_inputs = []
    agent.chat = lambda value: (agent.chat_inputs.append(value) or "agent")
    console = ConsoleCommandSystem()
    console.register(Command("status", "Status", lambda _args, _ctx: "command"))

    assert _dispatch_input(agent, console, "/status") == "command"
    assert agent.chat_inputs == []
    assert _dispatch_input(agent, console, "fix this bug") == "agent"
    assert agent.chat_inputs == ["fix this bug"]


def test_agent_chat_treats_slash_text_as_an_agent_instruction():
    agent = MiniClaudeAgent.__new__(MiniClaudeAgent)
    agent.session_recorder = Recorder()
    agent._last_assistant_note = None
    agent.run_inputs = []
    agent.run = lambda value, require_tool_call=False: (
        agent.run_inputs.append((value, require_tool_call)) or "model response"
    )

    assert agent.chat("/status") == "model response"
    assert agent.run_inputs == [("/status", False)]
    assert [event for event, _data in agent.session_recorder.events] == [
        "user_input", "final",
    ]
