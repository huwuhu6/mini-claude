"""Append-only JSONL session records and a logging bridge."""

from __future__ import annotations

import json
from contextlib import contextmanager
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
        self._round_id = 0
        self._step = 0
        self.redact_display = False

    def start_round(self) -> int:
        """Start one user -> agent completion interaction."""
        with self._lock:
            self._round_id += 1
            self._step = 0
            return self._round_id

    @contextmanager
    def provider_request(self, *, source: str, parameters: dict, run_id=None,
                         turn=None, attempt: int = 0, estimated_prompt_tokens=None):
        """Persist request boundaries; these observations never authorize replay."""
        context = {
            'request_id': uuid.uuid4().hex, 'run_id': run_id, 'turn': turn,
            'source': source, 'attempt': attempt,
            'parameters': {k: parameters[k] for k in ('max_tokens', 'reasoning_effort', 'temperature')
                           if k in parameters},
            'estimated_prompt_tokens': estimated_prompt_tokens,
        }
        self.record('provider_request_started', **context)
        parsed = {}
        try:
            yield parsed
        except BaseException as error:
            # Exception messages and response bodies may contain credentials.
            self.record('provider_request_finished', **context, status='error',
                        error_type=type(error).__name__, usage=None)
            raise
        else:
            raw = parsed.get('reported_usage', parsed.get('usage'))
            usage = {k: v for k, v in raw.items()
                     if k in {'prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_tokens',
                              'reasoning_tokens', 'input_tokens', 'output_tokens'}
                     and type(v) is int and v >= 0} if isinstance(raw, dict) else {}
            self.record('provider_request_finished', **context, status='received',
                        finish_reason=parsed.get('finish_reason'), usage=usage or None,
                        usage_origin='reported' if 'reported_usage' in parsed else 'normalized')

    def record(self, event_type: str, **data: Any) -> None:
        if self.redact_display:
            from core.display_safety import redact
            data = redact(data)
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if self._round_id:
                    self._step += 1
                    round_id = self._round_id
                    step = self._step
                else:
                    round_id = None
                    step = None
                event = {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "timestamp": time.time(),
                    "session_id": self.session_id,
                    "round_id": round_id,
                    "step": step,
                    "type": event_type,
                    **data,
                }
                with self.path.open("a", encoding="utf-8") as handle:
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
