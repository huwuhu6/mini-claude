"""Rich status lifecycle and bounded terminal records, without timing or models."""
from io import StringIO

import pytest
from rich.cells import cell_len

from cli.ui import TerminalUI


def test_status_updates_in_place_then_emits_one_result(monkeypatch):
    stream = StringIO()
    ui = TerminalUI(stream)
    ui.handle_event("model_request_started", {"iteration": 1})
    status = ui._progress
    ui.handle_event("tool_start", {"name": "bash", "summary": "echo safe"})
    assert ui._progress is status
    ui.handle_event("tool_execution_started", {"name": "bash", "command": "echo safe", "summary": "echo safe"})
    assert ui._progress is status and "echo safe" in str(status.status)
    assert stream.getvalue() == ""
    stopped = []
    monkeypatch.setattr(status, "stop", lambda: stopped.append(True))
    ui.handle_event("tool_result", {"name": "bash", "success": True})
    assert stopped == [True] and ui._progress is None
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1 and "✔ 执行完成: bash" in lines[0]
    assert "echo safe" in lines[0]


@pytest.mark.parametrize("boundary", ["print_control", "print_answer", "read_input", "begin_turn"])
def test_status_stops_before_output_or_input(boundary, monkeypatch):
    ui = TerminalUI(StringIO())
    ui._show_progress("正在执行: bash echo safe")
    status = ui._progress
    stopped = []
    monkeypatch.setattr(status, "stop", lambda: stopped.append(True))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt: "user text")
    action = getattr(ui, boundary)
    action("message") if boundary in {"print_control", "print_answer"} else action()
    assert stopped == [True] and ui._progress is None


def test_narrow_cjk_status_and_failure_are_single_line(monkeypatch):
    monkeypatch.setenv("COLUMNS", "42")
    stream = StringIO()
    ui = TerminalUI(stream)
    command = "echo 中文路径 " * 20 + "\nsecond command"
    ui.handle_event("tool_execution_started", {"name": "bash", "command": command, "summary": command})
    status = str(ui._progress.status)
    assert "\n" not in status and status.endswith("…")
    assert cell_len(status) <= 38
    ui.handle_event("tool_result", {"name": "bash", "success": False, "error": "连接超时 api_key=secret"})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1 and cell_len(lines[0]) <= 42
    assert "连接超时" in lines[0] and "secret" not in lines[0]
    assert ui._progress is None


def test_unknown_and_cancelled_never_render_as_success():
    stream = StringIO()
    ui = TerminalUI(stream)
    for outcome in ("UNKNOWN", "CANCELLED", "NOT_EXECUTED", "SKIPPED_DUPLICATE"):
        ui.handle_event("tool_result", {"name": "bash", "outcome": outcome, "success": True})
    text = stream.getvalue()
    assert "✔" not in text and "执行完成" not in text
    assert len(text.splitlines()) == 4


def test_rich_markup_in_model_note_is_literal():
    stream = StringIO()
    ui = TerminalUI(stream)
    ui.handle_event("assistant_note", {"text": "[red]a plan[/red]"})
    assert "[red]a plan[/red]" in stream.getvalue()


def test_long_note_redaction_does_not_expand_past_terminal_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "30")
    stream = StringIO()
    ui = TerminalUI(stream)
    ui.handle_event("assistant_note", {"text": "计划 api_key=secret " + "详细说明" * 20})
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1 and cell_len(lines[0]) <= 30
    assert "secret" not in lines[0]


def test_no_color_terminal_still_starts_and_stops_status(monkeypatch):
    class TTY(StringIO):
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    ui = TerminalUI(TTY())
    # Spy on lifecycle without starting a real refresh thread.
    from rich.status import Status
    events = []
    monkeypatch.setattr(Status, "start", lambda self: events.append("start"))
    monkeypatch.setattr(Status, "stop", lambda self: events.append("stop"))
    ui.handle_event("tool_execution_started", {"name": "bash", "command": "echo safe"})
    ui.handle_event("tool_result", {"name": "bash", "success": True})
    assert events == ["start", "stop"]
    assert "\x1b" not in ui.stream.getvalue()
