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
        "same_version_reread_count": 2,
        "overlap_reread_lines": 20,
        "overlap_reread_ratio": 0.25,
    }


def test_same_version_and_overlap_metrics_use_interval_union_and_canonical_paths():
    metrics = compute_file_read_metrics(_trace_with_reads([
        ("./src/../app.py", 1, 10, "v1"),
        ("app.py", 5, 15, "v1"),       # overlaps 5-10
        ("app.py", 7, 8, "v1"),        # wholly covered, adds 2 not 2+4
        ("app.py", 16, 20, "v1"),      # adjacent but not overlapping
        ("app.py", 1, 10, "v2"),       # a new version is not a reread
    ]))

    assert metrics["read_file_count"] == 5
    assert metrics["redundant_read_count"] == 0
    assert metrics["same_version_reread_count"] == 3
    assert metrics["overlap_reread_lines"] == 8
    assert metrics["overlap_reread_ratio"] == 8 / 38


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
