"""
TraceWriter — persists TaskTrace to .traces/ as pretty-printed JSON files.

Design decisions:
- One file per task: .traces/task_<id>.json
- UTF-8, pretty-printed, no BOM
- Never overwrites existing files (id collision is effectively zero with uuid4:8)
- IOError does NOT propagate — trace failures are non-fatal
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional
from .models import TaskTrace

logger = logging.getLogger(__name__)


class TraceWriter:
    """Writes TaskTrace to .traces/ directory as pretty JSON files."""

    def __init__(self, trace_dir: Optional[Path] = None):
        self.trace_dir = trace_dir or Path.cwd() / ".traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def write_task(self, trace: TaskTrace) -> str:
        """Write a TaskTrace to .traces/task_<task_id>.json.

        Returns:
            The file path string if successful, empty string on failure.
        """
        if not trace.task_id:
            logger.warning("TraceWriter: empty task_id, skipping write")
            return ""

        file_path = self.trace_dir / f"task_{trace.task_id}.json"
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(trace.to_dict(), f, indent=2, ensure_ascii=False)
            logger.debug(f"Trace written: {file_path} ({len(trace.turns)} turns)")
            return str(file_path)
        except OSError as e:
            logger.error(f"TraceWriter: cannot write {file_path}: {e}")
            return ""
        except TypeError as e:
            logger.error(f"TraceWriter: serialization error for {file_path}: {e}")
            return ""
