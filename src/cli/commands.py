"""Agent-specific slash commands exposed by the interactive CLI."""

from __future__ import annotations

import logging
from typing import Any, Callable

from cli.console import Command, ConsoleCommandSystem
from models.task import TaskStatus
from models.teammate import TeammateStatus

logger = logging.getLogger(__name__)


def register_agent_commands(console: ConsoleCommandSystem, agent: Any) -> None:
    """Bind the runtime's user-facing commands to a CLI console."""
    commands: tuple[tuple[str, str, Callable, str, list[str], str], ...] = (
        ('status', '查看当前会话、状态和保存位置', _status, '[--details]', [], 'system'),
        ('stats', '查看状态详情（建议使用 /status --details）', _stats, '', [], 'system'),
        ('config', '查看当前配置', _config, '', [], 'system'),
        ('tasks', '查看任务列表', _tasks, '[status]', ['task'], 'tasks'),
        ('team', '查看队友列表', _team, '[status]', [], 'team'),
        ('inbox', '查看收件箱', _inbox, '', [], 'team'),
        ('features', '查看或调整功能开关', _features,
         '[enable/disable] [name]', [], 'system'),
        ('compact', '压缩当前上下文', _compact, '', [], 'general'),
        ('providers', '查看模型提供者', _providers, '', [], 'system'),
        ('add_workdir', '明确授权额外工作目录',
         _add_workdir, '<path>', ['addpath'], 'system'),
        ('whitelist', '查看已授权的目录',
         _whitelist, '', ['workdirs'], 'system'),
        ('skills', '查看技能模块', _skills,
         '[name]', ['skill'], 'system'),
        ('clear', '开始新对话（建议使用 /new）', _clear, '', [], 'general'),
    )
    for name, help_text, handler, args_help, aliases, category in commands:
        console.register(Command(
            name, help_text,
            lambda args, context, callback=handler: callback(agent, args, context),
            args_help=args_help, aliases=aliases, category=category,
            advanced=name not in {'status', 'compact'},
        ))


def create_agent_console(agent: Any) -> ConsoleCommandSystem:
    console = ConsoleCommandSystem()
    register_agent_commands(console, agent)
    if getattr(agent, "_hitl_enabled", False):
        from cli.hitl_commands import register_hitl_commands
        register_hitl_commands(console, agent)
    else:
        console.register(Command('new', '开始新对话', lambda args, ctx: _clear(agent, args, ctx)))
    return console


def execute_agent_command(
    console: ConsoleCommandSystem, agent: Any, command: str
) -> str:
    """Execute a CLI command and preserve command session events."""
    # Approval displays are ephemeral: no parameters/results in command logs.
    try:
        parsed = console.parse(command)
    except ValueError:
        parsed = None  # execute() owns the existing malformed-input feedback.
    name = parsed[0] if parsed else ''
    definition = console.get_command(name)
    if definition and definition.category == 'hitl':
        agent.session_recorder.start_round()
        agent.session_recorder.record("cli_control", command=name)
        return console.execute(command, {'agent': agent})
    recorder = agent.session_recorder
    recorder.start_round()
    logger.info("USER_INPUT: %s", command)
    recorder.record("user_input", content=command)
    result = console.execute(command, {'agent': agent})
    logger.info("COMMAND_RESULT: command=%s result=%s", command, result)
    recorder.record("command_result", command=command, result=result)
    return result


def _status(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    lines = [f"=== {agent.config.agent.name} v{agent.config.agent.version} ==="]
    if _args not in ([], ['--details']):
        return '用法: /status [--details]'
    info = agent.get_session_info()
    lines += [f"  会话 ID: {info['conversation_id'] or '尚未创建（首个任务开始时生成）'}",
              f"  标题: {info['title']}", f"  当前 Run: {info['run_id'] or '-'}",
              f"  状态: {info['status'].upper()}", f"  工作区: {info['workspace']}",
              f"  执行目录: {info['cwd']}", f"  模型: {agent.config.llm.provider} / {agent.config.llm.model}",
              f"  审批: {'开启' if info['hitl'] else '关闭'}",
              f"  Checkpoint: {info['checkpoint_db'] or '未启用'}",
              f"  运行数据: {info['data_root']}", f"  本次诊断日志: {info['session_log']}",
              f"  Trace 目录: {info['traces']}"]
    lines.append(f"  消息数: {len(agent.messages)}")
    if info.get('execution_blocked'):
        lines += ["  执行保护: UNKNOWN；只读核验，禁止重放或覆盖原任务。",
                  "  不确定调用: " + ', '.join(info.get('unknown_call_ids', [])),
                  "  后续处理: /approval <Run ID> 查看；人工检查后 /archive <原因>。"]
    lines.append(f"  估计 tokens: {agent.compressor.estimate_tokens(agent.messages)}")
    if _args:
        lines += [f"  Recorder ID: {info['recorder_id']}", f"  Trace ID: {info['trace_id'] or '-'}",
                  "  已启用功能: " + ', '.join(agent.feature_manager.get_enabled_features())]
    return '\n'.join(lines)


def _stats(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    return _status(agent, ['--details'], _context)


def _config(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    lines = ["=== 配置信息 ==="]
    lines.append(f"  代理: {agent.config.agent.name} ({agent.config.agent.version})")
    lines.append(f"  LLM: {agent.config.llm.provider} / {agent.config.llm.model}")
    lines.append(f"  最大 tokens: {agent.config.llm.max_tokens}")
    lines.append(f"  温度: {agent.config.llm.temperature}")
    features = agent.feature_manager
    lines.append(
        f"  功能: subagent={features.is_enabled('subagent')}, "
        f"compression={features.is_enabled('compression')}, "
        f"background={features.is_enabled('background')}, "
        f"skills={features.is_enabled('skills')}"
    )
    return '\n'.join(lines)


def _tasks(agent: Any, args: list[str], _context: dict[str, Any]) -> str:
    status_filter = None
    if args:
        try:
            status_filter = TaskStatus(args[0])
        except ValueError:
            available = ', '.join(status.value for status in TaskStatus)
            return f"未知任务状态: {args[0]}\n可用值: {available}"
    tasks = agent.task_manager.list(status=status_filter)
    if not tasks:
        return "没有找到任务。"
    tasks.sort(key=lambda task: (-task.priority, task.created_at))
    return '\n'.join(task.to_short_string() for task in tasks)


def _team(agent: Any, args: list[str], _context: dict[str, Any]) -> str:
    status_filter = None
    if args:
        try:
            status_filter = TeammateStatus(args[0])
        except ValueError:
            return f"未知状态: {args[0]}。可用值: idle, working, busy, error, shutdown"
    teammates = agent.team_manager.list(status=status_filter)
    if not teammates:
        return "没有队友。"
    return '\n'.join(teammate.to_short_string() for teammate in teammates)


def _inbox(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    messages = agent.message_bus.read_inbox(agent.config.agent.name)
    if not messages:
        return "收件箱为空。"
    return '\n'.join(
        f"[{message.sender}] ({message.msg_type.value}): {message.content[:200]}"
        for message in messages
    )


def _features(agent: Any, args: list[str], _context: dict[str, Any]) -> str:
    if not args:
        lines = ["=== 功能列表 ==="]
        for feature in agent.feature_manager.list_features():
            status = '开' if agent.feature_manager.is_enabled(feature.name) else '关'
            lines.append(f"  {feature.name:20s} [{status:3s}]  {feature.description}")
        return '\n'.join(lines)

    action = args[0]
    if action in ('enable', 'disable') and len(args) >= 2:
        name = args[1]
        ok = (agent.feature_manager.enable(name) if action == 'enable'
              else agent.feature_manager.disable(name))
        if not ok:
            return f"无法{action}功能 '{name}'。"
        if name == 'skills' and action == 'enable':
            count = agent.skill_loader.refresh()
            if count:
                logger.info("已加载 %s 个技能模块", count)
        agent.refresh_system_prompt()
        return f"功能 '{name}' 已{action}。"
    return "用法: /features [enable|disable <名称>]"


def _compact(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    if getattr(agent, "has_active_run", False) or getattr(agent, "has_unfinished_conversation", False):
        return "当前运行尚未完成；请继续执行后再压缩消息。"
    if len(agent.messages) < 4:
        return "消息数量不足以进行压缩（至少需要 4 条）。"
    old_count = len(agent.messages)
    old_tokens = agent.compressor.estimate_tokens(agent.messages)
    agent.messages = agent.compressor.compress(agent.messages)
    new_tokens = agent.compressor.estimate_tokens(agent.messages)
    return (
        f"已压缩: {old_count} -> {len(agent.messages)} 条消息 "
        f"({old_tokens} -> {new_tokens} 估计 tokens)"
    )


def _providers(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    lines = ["=== 提供者 ==="]
    info = agent.provider_manager.get_provider_info()
    if not info:
        return "未配置任何提供者。"
    for name, provider in info.items():
        status = '正常' if provider['available'] else '失败'
        lines.append(f"  {name}: {provider['type']} ({provider['model']}) [{status}]")
    return '\n'.join(lines)


def _add_workdir(agent: Any, args: list[str], _context: dict[str, Any]) -> str:
    if not args:
        return "用法: /add_workdir <路径>\n添加工作目录到路径校验白名单"
    return agent.tools.add_allowed_path(args[0])


def _whitelist(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    return agent.tools.list_allowed_paths()


def _skills(agent: Any, args: list[str], _context: dict[str, Any]) -> str:
    if not agent.feature_manager.is_enabled('skills'):
        return "技能系统未启用。通过 /features enable skills 启用。"
    if not args:
        all_skills = agent.skill_loader.get_all()
        if not all_skills:
            return "没有可用的技能模块。请在 skills/ 目录中创建 SKILL.md 文件。"
        lines = ["=== 技能模块 ==="]
        for skill in sorted(all_skills, key=lambda item: item.name):
            description = skill.description or '(无描述)'
            category = f"[{skill.category}]" if skill.category else ""
            lines.append(f"  {skill.name:20s} {category:12s} {description}")
        return '\n'.join(lines)
    name = args[0]
    content = agent.skill_loader.get_skill_content(name)
    if content is None:
        available = ', '.join(skill.name for skill in agent.skill_loader.get_all()) or '(无)'
        return f"未找到技能: {name}\n可用: {available}"
    return f"=== {name} ===\n{content}"


def _clear(agent: Any, _args: list[str], _context: dict[str, Any]) -> str:
    if getattr(agent, "has_active_run", False) or getattr(agent, "has_unfinished_conversation", False):
        return "当前运行尚未完成；请继续执行后再清除消息历史。"
    if getattr(agent, '_checkpoint_store', None) is not None:
        agent.start_new_conversation()
    else:
        agent.messages.clear()
    if hasattr(agent, '_last_assistant_note'):
        agent._last_assistant_note = None
    if hasattr(agent, '_compression_notice_pending'):
        agent._compression_notice_pending = False
    return "对话历史已清除。"
