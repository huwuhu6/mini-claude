"""
TraceAnalyzer — loads Runtime Trace JSON files and computes process metrics.

Two loading strategies:
  1. load_latest_trace() — auto-detect the most recent task_*.json in .traces/
  2. load_trace(path)    — load a specific trace file by path

Usage:
    analyzer = TraceAnalyzer(workdir)
    if analyzer.load_latest_trace():
        metrics = analyzer.compute_metrics()
        print(metrics["duplicate_tool_ratio"])
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .metrics import (
    compute_duplicate_tool_ratio,
    compute_avg_tools_per_turn,
    compute_reflection_recovery_rate,
    compute_compression_survival,
    compute_rollback_occurred,
    compute_degradation_score,
)

logger = logging.getLogger(__name__)


class TraceAnalyzer:
    """Load a Runtime Trace and compute derived process-quality metrics."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.trace_dir = workdir / ".traces"
        self.trace_data: Optional[Dict[str, Any]] = None

    # ── Loading ─────────────────────────────────────────────────────────

    def load_latest_trace(self) -> bool:
        """Load the most recently written trace file from .traces/.

        Returns:
            True if a trace was loaded, False otherwise.
        """
        if not self.trace_dir.is_dir():
            logger.debug(f"TraceAnalyzer: no .traces/ dir at {self.trace_dir}")
            return False

        files = sorted(
            self.trace_dir.glob("task_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not files:
            logger.debug("TraceAnalyzer: no task_*.json files found")
            return False

        return self._load_file(files[-1])

    def load_trace(self, trace_path: Path) -> bool:
        """Load a specific trace file by path.

        Returns:
            True if the trace was loaded, False on I/O error or missing file.
        """
        return self._load_file(trace_path)

    def load_trace_by_id(self, task_id: str) -> bool:
        """Load a trace by its task_id (looks up .traces/task_{task_id}.json)."""
        return self._load_file(self.trace_dir / f"task_{task_id}.json")

    def _load_file(self, path: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.trace_data = json.load(f)
            logger.debug(f"TraceAnalyzer: loaded trace from {path}")
            return True
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            logger.warning(f"TraceAnalyzer: failed to load {path}: {e}")
            self.trace_data = None
            return False

    # ── Metrics ─────────────────────────────────────────────────────────

    def compute_metrics(self) -> Dict[str, Any]:
        """Compute all derived metrics from the loaded trace.

        Returns:
            Dict with keys matching each metric function name.
            Values are the computed metric or None if data was insufficient.
            Returns empty dict if no trace has been loaded.
        """
        if self.trace_data is None:
            return {}

        data = self.trace_data
        return {
            "duplicate_tool_ratio":      compute_duplicate_tool_ratio(data),
            "avg_tools_per_turn":        compute_avg_tools_per_turn(data),
            "reflection_recovery_rate":  compute_reflection_recovery_rate(data),
            "compression_survival":      compute_compression_survival(data),
            "rollback_occurred":         compute_rollback_occurred(data),
            "degradation_score":         compute_degradation_score(data),
            "reflection_count":          data.get("reflection_count", 0),
            "loop_guard_trigger_count":  data.get("loop_guard_trigger_count", 0),
            "compression_count":         data.get("compression_count", 0),
            "rollback_count":            data.get("rollback_count", 0),
            "final_status":              data.get("final_status", ""),
        }
