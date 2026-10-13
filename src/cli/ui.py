"""Terminal presentation, independent from execution state and approval facts."""
from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from datetime import datetime
from typing import Any, TextIO

try:
    from rich.console import Console
    from rich.text import Text
    from rich.theme import Theme
except ImportError:  # Older editable installs must still show completed answers.
    Console = Text = Theme = None

from core.display_safety import safe_text
from cli.operation_preview import operation_preview


def confirm_exit(read_line=input) -> bool:
    try:
        answer = read_line("\n确认退出 Mini-Claude？[y/N] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        if Console is not None:
            Console().print()
        else:
            sys.stdout.write("\n")
        return False
    return answer in ("y", "yes")


def fit_line(text, width):
    """Bound Chinese/wide text by terminal cells rather than character count."""
    result, used = [], 0
    for char in safe_text(text).expandtabs(4):
        size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if used + size > width - 1:
            return "".join(result) + "…"
        result.append(char)
        used += size
    return "".join(result)


def wrap_lines(text, width):
    """Wrap previews without hiding command arguments or file contents."""
    lines = []
    for line in safe_text(text).expandtabs(4).splitlines():
        chunk, used = [], 0
        for char in line:
            size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
            if used + size > max(1, width - 1) and chunk:
                lines.append(''.join(chunk))
                chunk, used = [], 0
            chunk.append(char)
            used += size
        lines.append(''.join(chunk))
    return lines


class TerminalUI:
    _DIM, _CYAN, _BLUE = "muted", "accent", "accent"
    _YELLOW, _GREEN, _RED = "warning", "success", "error"

    def __init__(self, stream: TextIO = sys.stdout):
        self.stream = stream
        self.color = bool(getattr(stream, "isatty", lambda: False)()) and not os.getenv("NO_COLOR")
        self.console = Console(file=stream, force_terminal=bool(getattr(stream, "isatty", lambda: False)()),
                               color_system="auto" if self.color else None, no_color=not self.color,
                               markup=False, highlight=False,
                               theme=Theme({"muted": "dim", "accent": "steel_blue1",
                                            "success": "dark_sea_green4", "warning": "khaki3",
                                            "error": "indian_red1"})) if Console is not None else None
        self._prompt_session = None
        self.prompt_toolkit_available = False
        self._session_info = {}
        self._progress = None
        self._tool_summary = ""
        self._model_iteration = 0

    def _paint(self, text, color):
        # Legacy entrypoint callers expect a string; styling lives in Console.
        return text

    def prompt(self):
        return self._paint("你 > ", self._CYAN)

    def multiline_prompt(self):
        return self._paint("... > ", self._CYAN)

    def read_input(self):
        self._clear_progress()
        if not sys.stdin.isatty():
            return input(self.prompt())
        try:
            from prompt_toolkit import PromptSession
        except ImportError:
            return input(self.prompt())
        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        self.prompt_toolkit_available = True
        return self._prompt_session.prompt("你 > ", multiline=True, prompt_continuation="... > ",
                                          bottom_toolbar=lambda: self._status_line())

    def _status_line(self):
        info = self._session_info
        workspace = os.path.basename(info.get("workspace", ""))
        identity = (info.get("conversation_id") or "新会话")[:8]
        protection = "只读 UNKNOWN · " if info.get("execution_blocked") else ""
        return fit_line(f"{protection}{workspace} · 会话 {identity} · {'审批开启' if info.get('hitl') else '审批关闭'}"
                        f" · {info.get('status', 'ready').upper()} · /status 查看保存位置",
                        shutil.get_terminal_size().columns)

    def show_session(self, info):
        self._session_info = dict(info)
        self.print_control(self._status_line())

    def begin_turn(self):
        self._model_iteration = 0
        self._tool_summary = ""
        self._clear_progress()
        self._write("\n── 本轮任务 " + "─" * 24, self._CYAN)

    def print_control(self, text):
        self._clear_progress()
        self._write(safe_text(text))

    def print_answer(self, text):
        self._clear_progress()
        self._write("\n╭─ Mini-Claude · 回答", self._BLUE)
        # Rendering is display-only: messages and checkpoints retain the source.
        # Pipes keep Markdown fences/indentation intact for copying and scripts.
        source = safe_text(text)
        if getattr(self.stream, "isatty", lambda: False)():
            try:
                from rich.markdown import Markdown
            except ImportError:
                # An older CLI installation may not yet have the new dependency.
                # Display failure must not turn completed tool work into a run error.
                self._write("Markdown 渲染依赖未安装，使用普通文本显示；请更新当前 CLI 环境的项目依赖。")
                self._write(source)
            else:
                if self.console is not None:
                    self.console.print(Markdown(source, code_theme="monokai", hyperlinks=False))
                else:
                    self._write(source)
        else:
            self._write(source)
        self._write("╰─ 本轮结束\n", self._DIM)

    def show_history(self, messages):
        if not messages:
            return
        self.print_control("\n── 已恢复的最近对话（压缩部分以摘要保留） ──")
        for message in messages:
            label = "你" if message["role"] == "user" else "Mini-Claude"
            lines = safe_text(message["content"]).splitlines()
            self._write(f"{label}：", self._CYAN if label == "你" else self._BLUE)
            for line in lines[:8]:
                self._write("  " + fit_line(line, shutil.get_terminal_size().columns - 2))
            if len(lines) > 8:
                self._write("  … 此段仅回显前 8 行；完整上下文已加载。")

    def choose_session(self, rows):
        options = []
        for index, row in enumerate(rows, 1):
            when = datetime.fromtimestamp(max(row["updated_at"], row["opened_at"])).strftime("%m-%d %H:%M")
            current = " · 当前" if row.get("current") else ""
            title = " ".join(str(row.get("title") or "未完成任务").split())
            options.append((str(index), f"{index}. {when} [{row['status'].upper()}]{current} {title} · {row['conversation_id']}"))
        while True:
            try:
                choice = self._select(options, search=True, header="╭─ 历史会话 · 最近活动在前",
                                      footer="↑↓ 选择 · Enter 打开 · Esc 取消 · / 搜索标题或 ID")
            except (EOFError, KeyboardInterrupt):
                return None
            if not choice:
                return None
            if choice.isascii() and choice.isdigit() and 1 <= int(choice) <= len(rows):
                return rows[int(choice) - 1]["conversation_id"]
            self.print_control("请输入列表中的序号；尚未加载任何会话。")

    def approval_choice(self, operation, reason, *, allow_rules=True):
        self._clear_progress()
        header = [f"╭─ 需要授权 · {operation.tool_name} · 尚未执行", "工作区：" + operation.workspace]
        if operation.cwd != operation.workspace:
            header.append("执行目录：" + operation.cwd)
        header.append("原因：" + reason)
        file = operation.tool_name in {"edit_file", "write_file"}
        if operation.tool_name == "bash":
            header.append("Shell 可产生外部副作用；授权不提供沙箱隔离。")
        header.append(operation_preview(operation))
        options = [("1", "1 / y  允许一次并继续")]
        if allow_rules:
            target = "修改此文件" if file else "执行此完整操作"
            options += [("2", "2      本会话允许" + target), ("3", "3      此工作区允许" + target + "…")]
        options += [("4", "4 / n  拒绝并结束本轮"), ("5", "5      换一种方式…")]
        return self._select(options, default=None, header="\n".join(header),
                            footer="↑↓ 选择 · Enter 确认（未选择时保持暂停） · d 详情 · Esc/p 暂停 · q 保存暂停并退出",
                            shortcuts={"y": "1", "n": "4", "d": "d", "p": "p", "q": "q"})

    _operation_preview = staticmethod(operation_preview)

    def show_approval_details(self, operation, text):
        self._select([("", "返回审批")], header=operation_preview(operation) + "\n" + text,
                     footer="PgUp/PgDn 滚动 · Enter/Esc 返回", default=0)

    def confirm_rule(self, operation, *, persistent):
        file = operation.tool_name in {"edit_file", "write_file"}
        target = str(operation.arguments.get("path", "")) if file else "此完整操作、参数及执行目录"
        header = f"╭─ 确认授权范围\n目标：{target}\n"
        header += "此工作区保存，跨会话有效；可用 /permissions 撤销。" if persistent else "仅当前对话有效；退出并恢复同一对话后保留，新对话不继承。"
        header += "\n文件规则不包含修改内容，也不授权 Shell。" if file else "\n不扩大为命令前缀、脚本解释器或所有 Shell 操作。"
        return self._select([("1", "1 / y  确认保存规则"), ("2", "2 / n  取消")], default=1, header=header,
                            footer="↑↓ 选择 · Enter 确认 · Esc 取消",
                            shortcuts={"y": "1", "yes": "1", "n": "2", "no": "2"}) == "1"

    def read_approval_feedback(self):
        return input("请说明替代方式（空输入取消） > ").strip()

    def continuation_choice(self, state):
        choice = self._select([("1", "1 / y  继续已决定的操作"), ("2", "2 / n  保持暂停")], default=1,
                            header=f"审批已记录为 {state}；尚未执行。",
                            footer="↑↓ 选择 · Enter 确认 · Esc 保持暂停",
                            shortcuts={"y": "1", "n": "2"})
        return "resume" if choice == "1" else ""

    def _select(self, options, *, default=0, search=False, header="", footer="", shortcuts=None):
        self._clear_progress()
        keyboard = sys.stdin.isatty() and getattr(self.stream, "isatty", lambda: False)()
        if keyboard:
            try:
                from prompt_toolkit import PromptSession
                from prompt_toolkit.key_binding import KeyBindings
                from prompt_toolkit.formatted_text import FormattedText
            except ImportError:
                keyboard = False
        if not keyboard:
            if header:
                self.print_control(header)
            for _, label in options:
                self._write(label)
            if footer:
                self._write(footer)
            answer = input("选择（空输入保持暂停/取消） > ").strip().lower()
            return (shortcuts or {}).get(answer, answer)
        bindings = KeyBindings()
        index, visible, query, offset = default, list(options), "", 0
        @bindings.add("up")
        @bindings.add("down")
        def move(event):
            nonlocal index
            if visible:
                index = (0 if event.key_sequence[0].key == "down" else len(visible) - 1) if index is None else (index + (-1 if event.key_sequence[0].key == "up" else 1)) % len(visible)
            event.app.invalidate()
        @bindings.add("enter")
        def accept(event):
            event.app.exit(result=visible[index][0] if visible and index is not None else "")
        @bindings.add("escape")
        def cancel(event):
            event.app.exit(result="")
        @bindings.add("<any>")
        def key(event):
            nonlocal query, visible, index
            value = event.data.lower()
            if search:
                query += value
                visible = [row for row in options if query.lstrip("/") in row[1].lower()]
                index = 0
            else:
                value = (shortcuts or {}).get(value, value)
                if value in {row[0] for row in options} | set((shortcuts or {}).values()):
                    event.app.exit(result=value)
            event.app.invalidate()
        @bindings.add("backspace")
        def erase(event):
            nonlocal query, visible, index
            if not search:
                return
            query = query[:-1]
            visible = [row for row in options if query.lstrip("/") in row[1].lower()]
            index = 0
            event.app.invalidate()
        @bindings.add("pageup")
        @bindings.add("pagedown")
        def scroll(event):
            nonlocal offset
            total = len(wrap_lines(header, shutil.get_terminal_size().columns))
            offset = max(0, min(max(0, total - 1), offset + (-5 if event.key_sequence[0].key == "pageup" else 5)))
            event.app.invalidate()
        def render():
            width, height = shutil.get_terminal_size()
            footer_lines = wrap_lines(footer, width) if footer else []
            budget = max(3, height - min(len(visible), 8) - len(footer_lines) - (2 if search else 0) - 3)
            lines = wrap_lines(header, width)
            shown = lines[offset:offset + budget]
            if len(lines) > budget:
                shown.append(f"… 预览 {offset + 1}–{min(offset + budget, len(lines))}/{len(lines)} 行 · PgUp/PgDn 滚动")
            if search:
                shown += ["搜索：" + query.lstrip("/"), "未找到匹配会话。" if not visible else ""]
            # Keep a window around the cursor when the history list is long.
            first = max(0, (index or 0) - 4) if len(visible) > 8 else 0
            shown += [("❯ " if n == index else "  ") + label for n, (_, label) in enumerate(visible) if first <= n < first + 8]
            shown += footer_lines
            fragments = []
            for line in shown:
                style = "fg:#87af87" if line.startswith("+") else "fg:#d78787" if line.startswith("-") else "fg:#afaf87" if line.startswith("╭") else ""
                fragments.append((style if self.color else "", fit_line(line, width) + "\n"))
            return FormattedText(fragments)
        return PromptSession(erase_when_done=True).prompt(render, key_bindings=bindings)

    def _clear_progress(self):
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    def _show_progress(self, text):
        if self.console is None:
            return
        width = self.console.width
        text = Text(fit_line(" ".join(safe_text(text).split()), max(1, width - 4)), style="muted")
        if self._progress is None:
            self._progress = self.console.status(text, spinner="dots", spinner_style="accent")
            # Pipes retain only final records. NO_COLOR still permits a spinner.
            if self.console.is_terminal:
                self._progress.start()
        else:
            self._progress.update(text)

    def handle_event(self, event: str, data: dict[str, Any]):
        if event in {"model_request_started", "thinking"}:
            self._model_iteration = data.get("iteration", 0)
            self._show_progress(f"  思考中 · 模型轮次 {self._model_iteration}")
        elif event == "assistant_note":
            self._clear_progress()
            text = " ".join(str(data.get("text", "")).split())
            if text:
                width = self.console.width if self.console is not None else shutil.get_terminal_size().columns
                note = fit_line("  · " + text, width)
                self._write(Text(note, style="muted") if Text is not None else note)
        elif event == "tool_start":
            self._tool_summary = data.get("summary", "")
        elif event == "tool_execution_started":
            name = data.get("name", "工具")
            self._tool_summary = data.get("summary", self._tool_summary)
            summary = data.get("command") or self._tool_summary
            self._show_progress(f"正在执行: {name} · {summary}")
        elif event == "tool_result":
            self._clear_progress()
            name = data.get("name", "tool")
            outcome = data.get("outcome", "")
            if outcome == "UNKNOWN":
                label, color = "结果不确定", self._RED
            elif outcome == "CANCELLED":
                label, color = "执行取消，需核验影响", self._YELLOW
            elif outcome == "SKIPPED_DUPLICATE":
                label, color = "跳过重复调用", self._DIM
            elif data.get("blocked") or outcome == "NOT_EXECUTED" or outcome.startswith("BLOCKED_"):
                label, color = "未执行", self._YELLOW
            elif data.get("success"):
                label, color = "执行完成", self._GREEN
            else:
                label, color = "执行失败", self._RED
            summary = self._tool_summary
            workspace = self._session_info.get("workspace")
            if workspace and summary.startswith(workspace + os.sep):
                summary = summary[len(workspace) + 1:]
            line = f"• {'✔' if label == '执行完成' else '✖' if label == '执行失败' else '–'} {label}: {name} · {summary}"
            width = self.console.width if self.console is not None else shutil.get_terminal_size().columns
            if label != "执行完成":
                # Only use facts supplied by the event, never infer an error
                # from the command or expose the full tool output here.
                reason = data.get("error") or data.get("message") or data.get("reason")
                if reason:
                    # Reserve space for the reason even when the command is long.
                    line = fit_line(" ".join(safe_text(line).split()), max(1, width // 2))
                    line += " — " + " ".join(safe_text(reason).split())
            line = fit_line(" ".join(safe_text(line).split()), width)
            if label == "执行完成" and Text is not None:
                result = Text(line, style="muted")
                result.stylize("success", 2, 3)
                self._write(result)
            else:
                # Do not redact again after cell-width clipping: truncating a
                # masked assignment can make another redaction expand the line.
                self._write(Text(line, style=color) if Text is not None else line)
            self._tool_summary = ""
        elif event == "runtime_error":
            self.print_control("运行错误：" + str(data.get("message", "")))
        elif event == "task_cancelled":
            self.print_control("当前任务已停止，返回输入。")

    def _write(self, text, style=None):
        if self.console is not None:
            self.console.print(text if isinstance(text, Text) else safe_text(text), style=style, soft_wrap=True)
        else:
            self.stream.write(safe_text(text) + "\n")
            self.stream.flush()
