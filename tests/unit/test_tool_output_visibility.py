from pathlib import Path

from core.tools.base_tools import BaseTools
from core.tracing import TraceManager


def test_medium_shell_window_remains_visible_and_traceable():
    tools = BaseTools.__new__(BaseTools)
    tools.workdir = Path.cwd()
    content = "\n".join(f"source line {line}" for line in range(1, 57))

    visible, metadata = tools._format_tool_output_with_visibility(content)

    assert visible == content
    assert metadata["truncated"] is False
    assert metadata["original_lines"] == 56
    assert metadata["selected_line_ranges"] == [[1, 56]]
    assert not (Path.cwd() / ".agent" / "logs").exists()

    trace = TraceManager()
    trace.start_task()
    trace.start_turn(0)
    trace.record_tool_call(
        tool_name="bash", args_hash="test", success=True,
        output_visibility=metadata,
    )
    trace._close_turn()
    serialized = trace.current_task.to_dict()["turns"][0]["tools"][0]
    assert serialized["output_visibility"] == metadata
