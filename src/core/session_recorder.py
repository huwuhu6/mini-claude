"""Append-only JSONL session records and a logging bridge."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional


class SessionRecorder:
    """Write one structured event per line for one interactive session."""

    def __init__(self, sessions_dir: Path, session_id: Optional[str] = None):
        self.session_id = session_id or f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.sessions_dir = sessions_dir
        self.path = sessions_dir / f"session_{self.session_id}.jsonl"
        self._lock = threading.Lock()

    def record(self, event_type: str, **data: Any) -> None:
        event = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "timestamp": time.time(),
            "session_id": self.session_id,
            "type": event_type,
            **data,
        }
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                json.dump(event, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
        except OSError:
            # Observability must not break the Agent task.
            pass


class SessionLogHandler(logging.Handler):
    """Keep warnings and errors visible in the structured session record."""

    def __init__(self, recorder: SessionRecorder):
        super().__init__(level=logging.WARNING)
        self.recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.recorder.record(
                "log",
                level=record.levelname,
                logger=record.name,
                message=record.getMessage(),
            )
        except Exception:
            pass
