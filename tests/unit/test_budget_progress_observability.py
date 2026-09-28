"""Budget hints and trace labels must not turn exploration into a policy veto."""

import json
from pathlib import Path
from types import SimpleNamespace

from agent.mini_claude_agent import MiniClaudeAgent
from core.loop_controller import RuntimeDecision, RuntimePolicy, RuntimePolicyAdapter
from core.tools.base_tools import ToolResult
from core.tracing import TraceManager


def test_changed_observation_is_not_claimed_as_task_progress(tmp_path):
    adapter = RuntimePolicyAdapter(RuntimePolicy())
    first = adapter.observe(
        turn=1, tool_name="read_file", intent_key="read:a",
        args_fingerprint="a", result_text="first finding",
        workspace_before={}, workspace_after={},
    )
    second = adapter.observe(
        turn=2, tool_name="read_file", intent_key="read:b",
        args_fingerprint="b", result_text="different finding",
        workspace_before={}, workspace_after={},
    )

    assert first.action is second.action is RuntimeDecision.ALLOW
    assert second.observation_changed is True
    assert second.progress_detected is False
    assert second.progress_reason == ()
    assert second.stagnation_reason == ()
    assert second.recovery_stage.value == "OBSERVING"

    trace = TraceManager(trace_dir=tmp_path)
    trace.start_task(task_id="observation")
    trace.start_turn(0)
    trace.record_tool_call("read_file", "b", success=True)
    trace.annotate_current_tool(
        observation_changed=second.observation_changed,
        progress_detected=second.progress_detected,
        progress_reason=list(second.progress_reason),
        recovery_stage=second.recovery_stage.value,
    )
    payload = json.loads(Path(trace.end_task("SUCCESS")).read_text(encoding="utf-8"))
    tool = payload["turns"][0]["tools"][0]
    assert tool["observation_changed"] is True
    assert tool["progress_detected"] is False


def test_workspace_change_is_distinct_from_observation_change():
    adapter = RuntimePolicyAdapter(RuntimePolicy())
    result = adapter.observe(
        turn=1, tool_name="write_file", intent_key="write:a",
        args_fingerprint="write", result_text="saved",
        workspace_before={}, workspace_after={"a.py": "new hash"},
    )
    assert result.action is RuntimeDecision.ALLOW
    assert result.progress_detected is True
    assert result.progress_reason == ("WORKSPACE_CHANGED",)
    assert result.recovery_stage.value == "HEALTHY"


def test_budget_hint_is_transient_and_never_blocks_tools(tmp_path):
    agent = MiniClaudeAgent(
        workspace_root=tmp_path, workspace_confirmed=True,
        runtime_data_root=tmp_path / "runtime",
    )

    class Provider:
        def __init__(self):
            self.requests = []

        def create_message(self, messages, tools, **kwargs):
            self.requests.append(list(messages))
            return {
                "content": "", "usage": {},
                "tool_calls": [{"id": f"call-{len(self.requests)}", "function": {
                    "name": "read_file", "arguments": '{"path":"sample.txt"}',
                }}],
            }

        def parse_response(self, response):
            return response

    provider = Provider()
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    agent._execute_tool = lambda *args, **kwargs: ToolResult(
        content="sample", success=True, execution_success=True,
    )
    try:
        assert "4 轮上限" in agent._llm_tool_cycle(max_iterations=4)
        assert len(provider.requests) == 4
        assert all("<execution-budget>" not in (m.content or "")
                   for m in provider.requests[0])
        assert any("Model call 2/4" in (m.content or "")
                   for m in provider.requests[1])
        assert any("0 calls remain after this one" in (m.content or "")
                   for m in provider.requests[3])
        assert all("<execution-budget>" not in (m.content or "")
                   for m in agent.messages)
        trace_path = next((tmp_path / "runtime" / "traces").glob("task_*.json"))
        task_trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert task_trace["total_tool_calls"] == 4
        assert task_trace["final_status"] == "LOOP_ABORTED"
    finally:
        agent.shutdown()
