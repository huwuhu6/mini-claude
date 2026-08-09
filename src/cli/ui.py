"""Small terminal UI for separating user, progress, tools, and answers."""

from __future__ import annotations

import os
import sys
from typing import Any, TextIO


def confirm_exit(read_line=input) -> bool:
    """Ask for confirmation after Ctrl+C is pressed while entering text."""
    try:
        answer = read_line("\n确认退出 Mini-Claude？[y/N] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


class TerminalUI:
    """Render concise runtime events without changing agent behavior."""

    _RESET = "\033[0m"
    _DIM = "\033[2m"
    _CYAN = "\033[36m"
    _BLUE = "\033[34m"
    _YELLOW = "\033[33m"
    _GREEN = "\033[32m"
    _RED = "\033[31m"

    def __init__(self, stream: TextIO = sys.stdout):
        self.stream = stream
        self.color = bool(getattr(stream, "isatty", lambda: False)()) and not os.getenv("NO_COLOR")
        self._prompt_session = None
        self.prompt_toolkit_available = False

    def _paint(self, text: str, color: str) -> str:
        if not self.color:
            return text
        return f"{color}{text}{self._RESET}"

    def prompt(self) -> str:
        return self._paint("你 > ", self._CYAN)

    def multiline_prompt(self) -> str:
        return self._paint("... > ", self._CYAN)

    def read_input(self) -> str:
        """Read an editable multiline prompt, with a stdlib fallback."""
        if not getattr(sys.stdin, "isatty", lambda: False)():
            return input(self.prompt())

        try:
            from prompt_toolkit import PromptSession
        except ImportError:
            return input(self.prompt())

        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        self.prompt_toolkit_available = True
        return self._prompt_session.prompt(
            "你 > ",
            multiline=True,
            prompt_continuation="... > ",
        )

    def print_answer(self, text: str) -> None:
        print(self._paint("mini-claude", self._BLUE), file=self.stream)
        print(text, file=self.stream)

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "thinking":
            iteration = data.get("iteration", 0)
            self._write(self._paint(f"  · 分析中 · 第 {iteration} 轮", self._DIM))
        elif event == "assistant_note":
            text = str(data.get("text", "")).strip()
            if text:
                # Keep the complete intermediate note visible. The terminal
                # itself handles visual wrapping; truncating here hides the
                # reason behind the next tool call.
                for line in text.splitlines() or [""]:
                    self._write(self._paint(f"  · {line}", self._DIM))
        elif event == "tool_start":
            name = data.get("name", "tool")
            summary = data.get("summary", "")
            suffix = f"  {summary}" if summary else ""
            self._write(self._paint(f"  -> {name}{suffix}", self._YELLOW))
        elif event == "tool_result":
            name = data.get("name", "tool")
            if data.get("blocked"):
                self._write(self._paint(f"  [!] {name}  已拦截", self._RED))
            elif data.get("success"):
                self._write(self._paint(f"  [ok] {name}  已完成", self._GREEN))
            else:
                self._write(self._paint(f"  [x] {name}  失败", self._RED))
        elif event == "runtime_error":
            self._write(self._paint(f"  [!] 运行错误：{data.get('message', '')}", self._RED))
        elif event == "task_cancelled":
            self._write(self._paint("  [!] 当前任务已停止，返回输入。", self._YELLOW))

    def _write(self, text: str) -> None:
        print(text, file=self.stream, flush=True)
