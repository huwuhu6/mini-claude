"""Offline presentation checks; no Provider or execution state involved."""
import io
import re
import builtins

from cli.ui import TerminalUI


class TerminalBuffer(io.StringIO):
    def isatty(self):
        return True


SAMPLE = """## 验证结果

**已完成**，请查看 `demo.txt`。

- 读取文件
- 保留原有内容

> 只修改目标文件。

| 文件 | 状态 |
| --- | --- |
| demo.txt | 成功 |

```python
def hello():
    return "你好"
```

[文档](https://example.com/docs)
"""


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_terminal_renders_markdown_blocks(monkeypatch):
    import cli.ui as module
    from rich.console import Console
    # StringIO is not a Win32 console; emulate a modern ANSI terminal explicitly.
    monkeypatch.setattr(module, "Console", lambda **kwargs: Console(legacy_windows=False, **kwargs))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", "100")
    stream = TerminalBuffer()
    TerminalUI(stream).print_answer(SAMPLE)
    output = plain(stream.getvalue())
    assert "## 验证结果" not in output
    assert "**已完成**" not in output
    assert "```python" not in output
    assert "| --- |" not in output
    for text in ("验证结果", "已完成", "读取文件", "保留原有内容", "只修改目标文件", "demo.txt", "成功", "def hello():", 'return "你好"', "文档"):
        assert text in output
    assert "\x1b[" in stream.getvalue()
    assert output.count("Mini-Claude · 回答") == 1
    assert output.count("本轮结束") == 1


def test_redirect_keeps_markdown_source():
    stream = io.StringIO()
    TerminalUI(stream).print_answer(SAMPLE)
    assert SAMPLE in stream.getvalue()
    assert "\x1b" not in stream.getvalue()


def test_no_color_still_renders_structure(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TerminalBuffer()
    TerminalUI(stream).print_answer("## 标题\n\n**正文**")
    assert "\x1b" not in stream.getvalue()
    assert "**正文**" not in stream.getvalue()
    assert "正文" in stream.getvalue()


def test_control_output_is_not_markdown():
    stream = TerminalBuffer()
    TerminalUI(stream).print_control("## 状态 **PENDING**")
    assert stream.getvalue() == "## 状态 **PENDING**\n"


def test_answer_sanitizes_before_rendering(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    stream = TerminalBuffer()
    source = "## 安全显示\n\napi_key=sk-secret-value\n\n\x1b[2J正文"
    TerminalUI(stream).print_answer(source)
    output = stream.getvalue()
    assert "sk-secret-value" not in output
    assert "\x1b" not in output
    assert "正文" in output
    assert source.endswith("\x1b[2J正文")  # Presentation does not mutate the caller's source.


def test_missing_rich_keeps_answer_instead_of_failing_run(monkeypatch):
    import cli.ui as module
    monkeypatch.setattr(module, "Console", None)
    original_import = builtins.__import__

    def missing_rich(name, *args, **kwargs):
        if name == "rich" or name.startswith("rich."):
            raise ModuleNotFoundError("No module named 'rich'", name="rich")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_rich)
    stream = TerminalBuffer()
    TerminalUI(stream).print_answer(SAMPLE)
    output = stream.getvalue()
    assert SAMPLE in output
    assert output.count("本轮结束") == 1
    assert "普通文本" in output
