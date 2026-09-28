"""Tool attempt timing must survive the policy adapter and trace serialization."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from agent.mini_claude_agent import MiniClaudeAgent
from core.loop_controller import AttemptHistory, RuntimePolicy, RuntimePolicyAdapter
from core.tools.base_tools import ToolResult
from core.tracing import TraceManager


def test_runtime_observer_preserves_measured_tool_duration(tmp_path):
    history = AttemptHistory()
    observer = RuntimePolicyAdapter(RuntimePolicy(history))

    result = observer.observe(
        turn=1,
        tool_name="read_file",
        intent_key="read_file:READ:app.py:L1-10",
        args_fingerprint="read-app",
        success=True,
        result_text="file content",
        duration_ms=37.25,
    )

    assert result.event.duration_ms == 37.25
    assert history.all()[0].to_dict()["duration_ms"] == 37.2
    plain = RuntimePolicyAdapter(RuntimePolicy()).observe(
        turn=1,
        tool_name="read_file",
        intent_key="read_file:READ:app.py:L1-10",
        args_fingerprint="read-app",
        success=True,
        result_text="file content",
    )
    assert result.action == plain.action

    trace = TraceManager(trace_dir=tmp_path)
    trace.start_task(task_id="duration")
    trace.start_turn(0)
    trace.record_attempt_event(result.event.to_dict())
    data = json.loads(Path(trace.end_task("SUCCESS")).read_text(encoding="utf-8"))
    assert data["attempt_events"][0]["duration_ms"] == 37.2


def test_unexecuted_attempt_keeps_zero_duration():
    history = AttemptHistory()
    observer = RuntimePolicyAdapter(RuntimePolicy(history))

    result = observer.observe(
        turn=1,
        tool_name="bash",
        intent_key="bash:EXECUTE:echo",
        args_fingerprint="blocked",
        success=False,
        command_blocked=True,
        block_reason="policy block",
        result_text="not executed",
    )

    assert result.event.duration_ms == 0


def test_agent_tool_cycle_records_nonzero_attempt_duration(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "runtime"
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
        runtime_data_root=data_root,
    )

    class FakeProvider:
        last_error_diagnostic = {}

        def __init__(self):
            self.calls = 0

        def create_message(self, *args, **kwargs):
            assert {"write_file", "edit_file"}.issubset(
                {tool.name for tool in args[1]}
            )
            self.calls += 1
            if self.calls == 1:
                return {
                    "content": "",
                    "tool_calls": [{"id": "call-1", "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "sample.txt"}),
                    }}],
                    "usage": {},
                }
            return {"content": "done", "tool_calls": [], "usage": {}}

        def parse_response(self, response):
            return response

    def fake_tool(*args, **kwargs):
        time.sleep(0.01)
        return ToolResult(content="sample content", success=True, execution_success=True)

    provider = FakeProvider()
    agent.provider_manager = SimpleNamespace(get_primary_provider=lambda: provider)
    agent._execute_tool = fake_tool
    try:
        assert agent.chat("Read the sample file") == "done"
        trace_path = next((data_root / "traces").glob("task_*.json"))
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert trace["attempt_events"][0]["duration_ms"] >= 10
        assert trace["turns"][0]["tools"][0]["latency_ms"] >= 10
    finally:
        agent.shutdown()
