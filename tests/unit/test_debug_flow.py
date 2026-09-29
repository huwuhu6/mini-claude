from core.debug_viewer import DebugViewer


def test_flow_summary_reports_request_and_truncation_without_payloads():
    viewer = DebugViewer.__new__(DebugViewer)
    viewer._events = lambda _session_id=None: [
        {"type": "user_input", "session_id": "test", "content": "secret"},
        {"type": "model_request_started", "turn": 1},
        {
            "type": "tool_result", "tool": "bash", "success": True,
            "result": "secret output", "output_visibility": {
                "truncated": True, "visible_chars": 400, "original_chars": 5000,
                "saved_path": ".agent/logs/cmd_private.log",
            },
        },
        {"type": "final", "status": "RESPONSE"},
    ]

    rendered = viewer.render("flow")

    assert "第 1 轮 模型请求" in rendered
    assert "bash：执行" in rendered
    assert "400/5000" in rendered
    assert "secret" not in rendered
    assert "cmd_private.log" not in rendered
    assert "非任务成功判定" in rendered
