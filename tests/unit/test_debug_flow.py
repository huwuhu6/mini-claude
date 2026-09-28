"""The compact session view must not turn governance telemetry into task facts."""

import json
from io import StringIO

from cli.entrypoint import parse_args
from cli.ui import TerminalUI
from core.debug_viewer import DebugViewer


def test_flow_view_separates_executed_failed_and_blocked_without_payloads(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    events = [
        {"session_id": "sample", "type": "user_input", "content": "private prompt"},
        {"session_id": "sample", "type": "thinking", "turn": 1},
        {"session_id": "sample", "type": "tool_call", "tool": "bash", "args": {"command": "secret"}},
        {"session_id": "sample", "type": "tool_result", "tool": "bash", "success": True,
         "blocked": False, "result": "private output"},
        {"session_id": "sample", "type": "tool_result", "tool": "bash", "success": False,
         "blocked": False, "result": "private failure"},
        {"session_id": "sample", "type": "tool_result", "tool": "bash", "success": False,
         "blocked": True, "result": "private policy"},
        {"session_id": "sample", "type": "final", "status": "RESPONSE", "content": "private final"},
    ]
    (sessions / "session_sample.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )

    output = DebugViewer(sessions).render("flow", "sample")
    assert "执行 1 / 失败 1 / 拦截 1" in output
    assert "第 1 轮 模型请求" in output
    assert "响应已返回（非任务成功判定）" in output
    assert "secret" not in output
    assert "private" not in output
    assert parse_args([str(tmp_path), "--debug", "flow"]).debug == "flow"


def test_flow_view_shows_truncation_without_saved_path_or_content(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    event = {
        "session_id": "sample",
        "type": "tool_result",
        "tool": "bash",
        "success": True,
        "result": "private output",
        "output_visibility": {
            "truncated": True,
            "original_chars": 5000,
            "visible_chars": 1200,
            "saved_path": ".agent/logs/private.log",
        },
    }
    (sessions / "session_sample.jsonl").write_text(json.dumps(event), encoding="utf-8")

    output = DebugViewer(sessions).render("flow", "sample")
    assert "bash：执行（输出仅预览 1200/5000 字符）" in output
    assert "private" not in output


def test_request_marker_and_visible_note_have_distinct_meanings(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    events = [
        {"session_id": "sample", "type": "model_request_started", "turn": 1},
        {"session_id": "sample", "type": "assistant_note", "turn": 1,
         "content": "I will inspect the file."},
        {"session_id": "sample", "type": "tool_call", "tool": "read_file",
         "args": {"path": "a.py"}},
    ]
    (sessions / "session_sample.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )

    latest = DebugViewer(sessions).render("latest", "sample")
    flow = DebugViewer(sessions).render("flow", "sample")
    assert "模型请求：第 1 轮" in latest
    assert "模型说明：I will inspect the file." in latest
    assert "工具：read_file" in latest
    assert "分析：" not in latest
    assert "第 1 轮 模型请求" in flow
    assert "I will inspect the file." not in flow

    output = StringIO()
    TerminalUI(output).handle_event("model_request_started", {"iteration": 1})
    assert "请求模型 · 第 1 轮" in output.getvalue()
    assert "分析中" not in output.getvalue()


def test_historical_thinking_marker_is_labeled_as_request(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "session_old.jsonl").write_text(
        json.dumps({"session_id": "old", "type": "thinking", "turn": 2}),
        encoding="utf-8",
    )
    assert "模型请求：第 2 轮" in DebugViewer(sessions).render("latest", "old")
