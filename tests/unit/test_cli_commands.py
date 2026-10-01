import shutil
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from agent.mini_claude_agent import MiniClaudeAgent
from cli.commands import create_agent_console, execute_agent_command
from cli.console import Command, ConsoleCommandSystem
from cli.entrypoint import _dispatch_input
from core.features import FeatureDefinition, FeatureManager
from models.config import FeaturesConfig
from skills.loader import SkillLoader


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



def test_features_list_omits_reserved_multi_agent_infrastructure():
    agent = _minimal_command_agent()
    agent.config = SimpleNamespace(features=FeaturesConfig())
    agent.feature_manager = FeatureManager()
    MiniClaudeAgent._register_features(agent)

    result = create_agent_console(agent).execute("/features")
    listed = {line.split()[0] for line in result.splitlines()[1:]}

    assert {"subagent", "compression", "memory", "background", "skills"} <= listed
    assert "tasks" not in listed
    assert "team" not in listed


def test_features_enable_skills_refreshes_and_updates_prompt():
    features = FeatureManager()
    features.register_feature(FeatureDefinition("skills", enabled=False))
    loader = SimpleNamespace(
        refreshes=0,
        refresh=lambda: (setattr(loader, "refreshes", loader.refreshes + 1) or 1),
        discover=lambda: pytest.fail("enable skills should call refresh"),
        descriptions=lambda: "sample: skill description" if loader.refreshes else "",
    )
    agent = _minimal_command_agent()
    agent.feature_manager = features
    agent.skill_loader = loader

    def refresh_prompt():
        enabled = ", ".join(features.get_enabled_features()) or "base"
        skill = loader.descriptions() if features.is_enabled("skills") else ""
        agent.system_prompt = f"Enabled features: {enabled}\n{skill}"

    agent.refresh_system_prompt = refresh_prompt
    console = create_agent_console(agent)

    assert "已enable" in console.execute("/features enable skills")
    assert loader.refreshes == 1
    assert "skills" in agent.system_prompt
    assert "sample: skill description" in agent.system_prompt

    assert "已disable" in console.execute("/features disable skills")
    assert "skills" not in agent.system_prompt
    assert "sample: skill description" not in agent.system_prompt


def _config_agent(feature_manager):
    agent = _minimal_command_agent()
    agent.feature_manager = feature_manager
    agent.config = SimpleNamespace(
        agent=SimpleNamespace(name="MiniClaude", version="1"),
        llm=SimpleNamespace(provider="test", model="test", max_tokens=10, temperature=0),
        features=SimpleNamespace(subagent=True, compression=True, background=False, skills=True),
    )
    return agent


def test_config_reports_runtime_feature_states():
    features = FeatureManager()
    for name, enabled in (
        ("subagent", True), ("compression", True),
        ("background", True), ("skills", False),
    ):
        features.register_feature(FeatureDefinition(name, enabled=enabled))
    agent = _config_agent(features)
    agent.skill_loader = SimpleNamespace(refresh=lambda: 0)
    agent.refresh_system_prompt = lambda: None
    console = create_agent_console(agent)

    result = console.execute("/config")
    assert "background=True" in result
    assert "skills=False" in result
    assert "memory=" not in result
    assert "tasks=" not in result
    assert "team=" not in result

    assert "已enable" in console.execute("/features enable skills")
    assert "已disable" in console.execute("/features disable background")
    result = console.execute("/config")
    assert "background=False" in result
    assert "skills=True" in result


def test_enabling_skills_refreshes_removed_files_and_prompt():
    skills_dir = Path.cwd() / f"test-cli-skills-{uuid4().hex}"
    skills_dir.mkdir()
    skill_path = skills_dir / "stale-skill.md"
    skill_path.write_text(
        "---\nname: stale-skill\ndescription: stale skill description\n---\ncontent",
        encoding="utf-8",
    )
    try:
        features = FeatureManager()
        features.register_feature(FeatureDefinition("skills", enabled=True))
        loader = SkillLoader(skills_dir)
        assert loader.refresh() == 1
        assert loader.get_skill_content("stale-skill") == "content"

        agent = _minimal_command_agent()
        agent.feature_manager = features
        agent.skill_loader = loader

        def refresh_prompt():
            description = loader.descriptions() if features.is_enabled("skills") else ""
            agent.system_prompt = description

        agent.refresh_system_prompt = refresh_prompt
        agent.refresh_system_prompt()
        assert "stale-skill" in agent.system_prompt

        skill_path.unlink()
        console = create_agent_console(agent)
        assert "已disable" in console.execute("/features disable skills")
        assert "已enable" in console.execute("/features enable skills")

        assert loader.get_all() == []
        assert "stale-skill" not in agent.system_prompt
    finally:
        shutil.rmtree(skills_dir)


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
