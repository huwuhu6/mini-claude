"""
Runtime Trace System — append-only, hook-based observability for the agent loop.

Provides three-tier tracing (TaskTrace → TurnTrace → ToolTrace) with
a minimal-intrusion hook API:

    trace = TraceManager(trace_dir=workdir / ".traces")

    # Per user input:
    trace.start_task()
    ...
    trace.end_task("SUCCESS")   # → writes .traces/task_<id>.json
"""
from .models import ToolTrace, TurnTrace, TaskTrace, TaskFinalStatus
from .manager import TraceManager
from .writer import TraceWriter
