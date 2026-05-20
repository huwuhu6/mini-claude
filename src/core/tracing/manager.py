"""
TraceManager — orchestrates runtime tracing with minimal intrusion.

Usage (from MiniClaudeAgent):
    self.trace = TraceManager(trace_dir=self.workdir / ".traces")

    # At the start of _llm_tool_cycle:
    self.trace.start_task()

    # Each iteration:
    self.trace.start_turn(iteration)

    # After compression check:
    self.trace.record_compression()

    # Each tool call:
    self.trace.record_tool_call(name, args_hash, success, ...)

    # When loop guard triggers:
    self.trace.record_reflection()

    # At each exit point of _llm_tool_cycle:
    self.trace.end_task("SUCCESS" / "FAILED" / "LOOP_ABORTED")

    # When Shadow Workspace rollback happens:
    self.trace.record_rollback()
"""
from __future__ import annotations
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from .models import ToolTrace, TurnTrace, TaskTrace
from .writer import TraceWriter

logger = logging.getLogger(__name__)


class TraceManager:
    """Orchestrates runtime tracing — bridges agent events to persisted traces."""

    def __init__(self, trace_dir: Optional[Path] = None):
        self.writer = TraceWriter(trace_dir)
        self.current_task: Optional[TaskTrace] = None
        self.current_turn: Optional[TurnTrace] = None

    # ── Task Lifecycle ─────────────────────────────────────────────────

    def start_task(self, task_id: str = "", user_prompt: str = "",
                    workspace_root: str = "",
                    workspace_confirmed: bool = False) -> str:
        """Begin a new task-level trace.  Returns task_id."""
        tid = task_id or str(uuid.uuid4())[:8]
        self.current_task = TaskTrace(
            task_id=tid, started_at=time.time(),
            user_prompt=user_prompt[:500],
            workspace_root=workspace_root,
            workspace_confirmed=workspace_confirmed,
        )
        self.current_turn = None
        logger.debug(f"Trace: task started [{tid}]")
        return tid

    def end_task(self, status: str) -> str:
        """Close the current task and write to disk.

        Returns:
            Trace file path (empty string on failure).
        """
        task = self.current_task
        if task is None:
            return ""

        # Close any open turn gracefully
        if self.current_turn is not None:
            self._close_turn()

        task.finished_at = time.time()
        task.final_status = status

        path = self.writer.write_task(task)
        if path:
            logger.info(f"Trace: task ended [{task.task_id}] status={status}")
        else:
            logger.warning(f"Trace: task [{task.task_id}] write failed")

        self.current_task = None
        self.current_turn = None
        return path

    # ── Turn Lifecycle ─────────────────────────────────────────────────

    def start_turn(self, iteration: int) -> None:
        """Begin a new turn trace (inside _llm_tool_cycle loop)."""
        if self.current_turn is not None:
            # Safety: close previous turn that wasn't properly ended
            self._close_turn()
        self.current_turn = TurnTrace(
            iteration=iteration,
            started_at=time.time(),
            message_count=0,
        )

    def _close_turn(self) -> None:
        """Finalise the current turn and append it to the task trace."""
        turn = self.current_turn
        task = self.current_task
        if turn is None or task is None:
            return

        turn.finished_at = time.time()
        task.turns.append(turn)
        task.total_turns += 1
        task.total_tool_calls += turn.tool_calls_count
        task.total_tokens += turn.token_usage
        self.current_turn = None

    # ── Tool Recording ─────────────────────────────────────────────────

    def record_tool_call(
        self,
        tool_name: str,
        args_hash: str,
        success: bool,
        loop_guard_blocked: bool = False,
        error_message: str = "",
        result_preview: str = "",
        started_at: Optional[float] = None,
        finished_at: Optional[float] = None,
        # Failure Intelligence fields
        failure_category: str = "",
        recoverability: str = "",
        strategy_fingerprint: str = "",
        escalated: bool = False,
        # Runtime Context fields
        cwd: str = "",
        workspace_root: str = "",
        session_id: str = "",
        # V3 Circuit Breaker
        circuit_breaker_triggered: bool = False,
    ) -> None:
        """Record a single tool call into the current turn.

        Must be called inside a turn (between start_turn / end_task).
        """
        turn = self.current_turn
        task = self.current_task
        if turn is None or task is None:
            return

        now = time.time()
        s = started_at or now
        f = finished_at or now
        latency = (f - s) * 1000.0

        trace = ToolTrace(
            tool_name=tool_name,
            args_hash=args_hash,
            started_at=s,
            finished_at=f,
            latency_ms=latency,
            success=success,
            loop_guard_blocked=loop_guard_blocked,
            error_message=error_message[:200],
            result_preview=result_preview[:200],
            failure_category=failure_category,
            recoverability=recoverability,
            strategy_fingerprint=strategy_fingerprint,
            escalated=escalated,
            circuit_breaker_triggered=circuit_breaker_triggered,
            cwd=cwd,
            workspace_root=workspace_root,
            session_id=session_id,
        )
        turn.tools.append(trace)
        turn.tool_calls_count += 1

        if loop_guard_blocked:
            task.loop_guard_trigger_count += 1

    # ── Event Counters (lightweight, no turn required for task-level) ──

    def record_compression(self) -> None:
        """Record that compression was triggered in the current turn."""
        if self.current_turn:
            self.current_turn.compression_triggered = True
        if self.current_task:
            self.current_task.compression_count += 1

    def record_reflection(self) -> None:
        """Record that a forced-reflection message was injected (loop guard)."""
        if self.current_turn:
            self.current_turn.reflection_triggered = True
        if self.current_task:
            self.current_task.reflection_count += 1

    def record_rollback(self) -> None:
        """Record a Shadow-Workspace rollback event."""
        if self.current_task:
            self.current_task.rollback_count += 1
            if self.current_task.final_status != "ROLLED_BACK":
                self.current_task.final_status = "ROLLED_BACK"

    def record_circuit_breaker(self) -> None:
        """Record that the V3 circuit breaker was triggered."""
        if self.current_task:
            self.current_task.circuit_breaker_trigger_count += 1

    def record_tokens(self, tokens: int) -> None:
        """Accumulate token usage for the current turn."""
        if self.current_turn:
            self.current_turn.token_usage += tokens

    def set_message_count(self, count: int) -> None:
        """Set the current turn's message count (snapshot before LLM call)."""
        if self.current_turn:
            self.current_turn.message_count = count
