"""
Console Command System - REPL command interface for the agent.
"""
from __future__ import annotations
import logging
import shlex
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)


class Command:
    """A console command definition."""

    def __init__(self, name: str, help_text: str, handler: Callable,
                 args_help: str = "", aliases: Optional[List[str]] = None,
                 category: str = "general"):
        self.name = name
        self.help_text = help_text
        self.handler = handler
        self.args_help = args_help
        self.aliases = aliases or []
        self.category = category

    def full_help(self) -> str:
        parts = [f"/{self.name}"]
        if self.aliases:
            parts.append(f" ({', '.join('/'+a for a in self.aliases)})")
        parts.append(f" - {self.help_text}")
        if self.args_help:
            parts.append(f"\n   用法: /{self.name} {self.args_help}")
        return ''.join(parts)


class ConsoleCommandSystem:
    """REPL command processing with registration, help, and tab-completion."""

    def __init__(self):
        self._commands: Dict[str, Command] = {}
        self._history: List[str] = []
        self._max_history: int = 100
        self._register_defaults()

    def _register_defaults(self):
        """Register built-in commands."""
        self.register(Command(
            'help', 'Show available commands', self._cmd_help,
            args_help='[command name]', category='general'
        ))
        self.register(Command(
            'exit', 'Exit the application', self._cmd_exit,
            aliases=['quit'], category='general'
        ))
        self.register(Command(
            'clear', 'Clear conversation history', self._cmd_clear,
            category='general'
        ))

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def get_command(self, name: str) -> Optional[Command]:
        return self._commands.get(name)

    def parse(self, text: str) -> Optional[tuple]:
        """Parse input text. Returns (command_name, args) or None if not a command."""
        text = text.strip()
        if not text.startswith('/'):
            return None
        parts = shlex.split(text[1:])  # remove leading /
        cmd_name = parts[0].lower() if parts else ''
        args = parts[1:] if len(parts) > 1 else []
        return (cmd_name, args)

    def execute(self, text: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Parse and execute a command. Returns response text."""
        parsed = self.parse(text)
        if not parsed:
            return ""  # not a command

        cmd_name, args = parsed
        cmd = self.get_command(cmd_name)

        if not cmd:
            similar = self._find_similar(cmd_name)
            msg = f"未知命令: /{cmd_name}"
            if similar:
                msg += f"。您是不是想输入: /{similar}?"
            return msg

        # Add to history
        self._history.append(text)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        try:
            result = cmd.handler(args, context or {})
            return str(result) if result is not None else ""
        except Exception as e:
            logger.error(f"命令 /{cmd_name} 执行失败: {e}")
            return f"执行 /{cmd_name} 时出错: {e}"

    def get_all_commands(self) -> List[Command]:
        unique = {}
        for cmd in self._commands.values():
            unique[cmd.name] = cmd
        return list(unique.values())

    def get_help_text(self, cmd_name: str = "") -> str:
        if cmd_name:
            cmd = self.get_command(cmd_name)
            return cmd.full_help() if cmd else f"未知命令: {cmd_name}"
        lines = ["可用命令:\n"]
        for cmd in sorted(self.get_all_commands(), key=lambda c: c.name):
            lines.append(f"  {cmd.full_help()}")
        return '\n'.join(lines)

    def get_history(self) -> List[str]:
        return list(self._history)

    def get_completions(self, prefix: str) -> List[str]:
        prefix = prefix.lower()
        matches = [f"/{n}" for n in self._commands if n.startswith(prefix)]
        return sorted(set(matches))

    def _cmd_help(self, args: List[str], ctx: Dict[str, Any]) -> str:
        return self.get_help_text(args[0] if args else "")

    def _cmd_exit(self, args: List[str], ctx: Dict[str, Any]) -> str:
        raise SystemExit("User requested exit")

    def _cmd_clear(self, args: List[str], ctx: Dict[str, Any]) -> str:
        # Clear is handled by the caller (they manage the conversation history)
        return "对话历史已清除。"

    def _find_similar(self, name: str) -> Optional[str]:
        """Find a similar command name using simple edit distance."""
        best = None
        best_score = float('inf')
        for cmd_name in self._commands:
            if cmd_name.startswith(name) or name.startswith(cmd_name):
                return cmd_name  # exact prefix match
            score = sum(a != b for a, b in zip(name, cmd_name))
            if score < best_score:
                best_score = score
                best = cmd_name
        if best_score <= 2:  # at most 2 edits away
            return best
        return None
