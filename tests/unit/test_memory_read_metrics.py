import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.evaluation.metrics import compute_file_read_metrics
from core.tracing.manager import TraceManager


def _trace_with_reads(reads):
    return {
        "turns": [{"tools": [
            {
                "file_read_path": path,
                "file_read_start_line": start,
                "file_read_end_line": end,
                "file_read_freshness": freshness,
            }
            for path, start, end, freshness in reads
        ]}],
    }


def test_redundant_read_requires_same_path_range_and_unchanged_freshness():
    metrics = compute_file_read_metrics(_trace_with_reads([
        ("app.py", 1, 20, "v1"),
        ("app.py", 1, 20, "v1"),
        ("app.py", 1, 20, "v2"),
        ("app.py", 21, 40, "v2"),
    ]))

    assert metrics == {
        "read_file_count": 4,
        "redundant_read_count": 1,
        "redundant_read_ratio": 0.25,
    }


def test_trace_persists_file_read_facts_and_task_level_metrics(tmp_path):
    trace = TraceManager(trace_dir=tmp_path)
    trace.start_task(task_id="reads")
    trace.start_turn(0)
    trace.record_tool_call("read_file", "first", True)
    trace.record_file_read("app.py", 1, 20, "v1")
    trace.record_tool_call("read_file", "repeat", True)
    trace.record_file_read("app.py", 1, 20, "v1")
    output = trace.end_task("SUCCESS")

    data = json.loads(Path(output).read_text(encoding="utf-8"))
    assert data["read_file_count"] == 2
    assert data["redundant_read_count"] == 1
    assert data["redundant_read_ratio"] == 0.5
    assert compute_file_read_metrics(data)["redundant_read_ratio"] == 0.5
    assert data["turns"][0]["tools"][1]["file_read_path"] == "app.py"
