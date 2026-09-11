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
from typing import Any, Dict, Optional

from .models import ToolTrace, TurnTrace, TaskTrace
from .writer import TraceWriter

logger = logging.getLogger(__name__)

_SHORT_RESULT_PREVIEW_LIMIT = 200
_LONG_RESULT_PREVIEW_LIMIT = 4000


def _trace_result_preview(result: str) -> str:
    """Keep the full bounded window for fileized output in audit traces."""
    if (
        "[Output is too long" in result
        and "--- Head (first 10 lines) ---" in result
        and "--- Tail (last 20 lines) ---" in result
    ):
        return result[:_LONG_RESULT_PREVIEW_LIMIT]
    return result[:_SHORT_RESULT_PREVIEW_LIMIT]


class TraceManager:
    """Orchestrates runtime tracing — bridges agent events to persisted traces."""

    def __init__(self, trace_dir: Optional[Path] = None):
        self.writer = TraceWriter(trace_dir)
        self.current_task: Optional[TaskTrace] = None
        self.current_turn: Optional[TurnTrace] = None

    # ── Task Lifecycle ─────────────────────────────────────────────────

    def start_task(self, task_id: str = "", user_prompt: str = "",
                    workspace_root: str = "",
                    workspace_confirmed: bool = False,
                    require_tool_call: bool = False,
                    environment: Optional[Dict[str, Any]] = None) -> str:
        """Begin a new task-level trace.  Returns task_id."""
        tid = task_id or str(uuid.uuid4())[:8]
        self.current_task = TaskTrace(
            task_id=tid, started_at=time.time(),
            user_prompt=user_prompt[:500],
            workspace_root=workspace_root,
            workspace_confirmed=workspace_confirmed,
            require_tool_call=require_tool_call,
            environment=dict(environment or {}),
        )
        self.current_turn = None
        logger.debug(f"Trace: task started [{tid}]")
        return tid

    def record_no_tool_retry(self, count: int) -> None:
        """Record the bounded retry caused by a missing tool call."""
        if self.current_task is not None:
            self.current_task.no_tool_retry_count = count

    def record_runtime_error(self, error: str) -> None:
        """Record the terminal exception from the agent loop."""
        if self.current_task is not None:
            self.current_task.runtime_error = error[:500]

    def record_provider_diagnostic(self, diagnostic: Dict[str, Any]) -> None:
        """Attach sanitized provider transport facts without secrets."""
        if self.current_task is not None:
            self.current_task.provider_diagnostic = dict(diagnostic)

    def end_task(self, status: str, terminal_reason: str = "") -> str:
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
        task.terminal_reason = terminal_reason or {
            "BLOCKED_ENVIRONMENT": "ENVIRONMENT_BLOCK",
            "CIRCUIT_BROKEN": "HARD_CIRCUIT_BREAKER",
            "LOOP_ABORTED": "GLOBAL_ITERATION_LIMIT",
        }.get(status, "")

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
        execution_success: Optional[bool] = None,
        observed_failure: bool = False,
        semantic_status: str = "",
        observation: str = "",
        exit_code: Optional[int] = None,
        segment_exit_codes: Optional[list[int]] = None,
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
        guard_type: str = "",
        guard_reason: str = "",
        # Progress-aware governance fields
        intent_key: str = "",
        observation_fingerprint: str = "",
        semantic_state: str = "",
        progress_detected: bool = False,
        progress_reason: Optional[list[str]] = None,
        stagnation_reason: Optional[list[str]] = None,
        recovery_stage: str = "",
        open_blocker_count: int = 0,
        oscillation_detected: bool = False,
        verification_improved: bool = False,
        completion_guard_triggered: bool = False,
        governance_decision: str = "",
        workspace_before_digest: str = "",
        workspace_after_digest: str = "",
        changed_paths: Optional[list[str]] = None,
        evidence_ids: Optional[list[str]] = None,
        governance_evidence_ids: Optional[list[str]] = None,
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
            execution_success=success if execution_success is None else execution_success,
            observed_failure=observed_failure,
            semantic_status=semantic_status,
            observation=observation,
            exit_code=exit_code,
            segment_exit_codes=list(segment_exit_codes or []),
            loop_guard_blocked=loop_guard_blocked,
            guard_type=guard_type,
            guard_reason=guard_reason,
            error_message=error_message[:200],
            result_preview=_trace_result_preview(result_preview),
            failure_category=failure_category,
            recoverability=recoverability,
            strategy_fingerprint=strategy_fingerprint,
            escalated=escalated,
            circuit_breaker_triggered=circuit_breaker_triggered,
            cwd=cwd,
            workspace_root=workspace_root,
            session_id=session_id,
            intent_key=intent_key,
            observation_fingerprint=observation_fingerprint,
            semantic_state=semantic_state,
            progress_detected=progress_detected,
            progress_reason=list(progress_reason or []),
            stagnation_reason=list(stagnation_reason or []),
            recovery_stage=recovery_stage,
            open_blocker_count=open_blocker_count,
            oscillation_detected=oscillation_detected,
            verification_improved=verification_improved,
            completion_guard_triggered=completion_guard_triggered,
            governance_decision=governance_decision,
            workspace_before_digest=workspace_before_digest,
            workspace_after_digest=workspace_after_digest,
            changed_paths=list(changed_paths or []),
            evidence_ids=list(evidence_ids or []),
            governance_evidence_ids=list(governance_evidence_ids or []),
        )
        turn.tools.append(trace)
        turn.tool_calls_count += 1

        if loop_guard_blocked:
            task.loop_guard_trigger_count += 1

    def record_attempt_event(self, event: Dict[str, Any]) -> None:
        """Persist the unified runtime fact alongside the tool trace."""
        if self.current_task is not None:
            self.current_task.attempt_events.append(dict(event))

    def update_last_attempt_event(self, event: Dict[str, Any]) -> None:
        """Refresh derived decision facts without duplicating an attempt."""
        if self.current_task is not None and self.current_task.attempt_events:
            self.current_task.attempt_events[-1] = dict(event)

    def annotate_current_tool(self, **fields: Any) -> None:
        """Attach post-execution governance evidence to the latest tool trace."""
        if not self.current_turn or not self.current_turn.tools:
            return
        trace = self.current_turn.tools[-1]
        for name, value in fields.items():
            if hasattr(trace, name):
                setattr(trace, name, value)

    def record_completion_guard(
        self, decision: str, open_blockers: int, *, governance_action: str = "",
        evidence_ids: Optional[list[str]] = None,
    ) -> None:
        if self.current_task:
            self.current_task.completion_guard_trigger_count += 1
            self.current_task.completion_guard_triggered = True
            self.current_task.governance_decision = decision
            self.current_task.governance_action = governance_action
            self.current_task.governance_evidence_ids = list(evidence_ids or [])
            self.current_task.open_blocker_count = open_blockers
        if self.current_turn:
            self.current_turn.reflection_triggered = True
            self.current_turn.completion_guard_triggered = True

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

    def record_provider_usage(
        self,
        usage: Dict[str, Any],
        *,
        estimated_prompt_tokens: int = 0,
        source: str = "main",
    ) -> None:
        """Record provider-reported usage and its request estimate.

        ``source`` distinguishes the main request from the separate Summary
        Provider request.  Both contribute to total token usage, while their
        detailed counters remain separate.
        """
        turn = self.current_turn
        task = self.current_task
        if turn is None or task is None:
            return

        def value(name: str) -> int:
            raw = usage.get(name, 0) if isinstance(usage, dict) else 0
            return raw if isinstance(raw, int) and raw >= 0 else 0

        prompt = value("prompt_tokens")
        completion = value("completion_tokens")
        total = value("total_tokens")
        cached = min(value("cached_tokens"), prompt)
        turn.token_usage += total
        turn.actual_prompt_tokens += prompt
        turn.completion_tokens += completion
        turn.prompt_tokens += prompt
        turn.cached_tokens += cached
        turn.uncached_prompt_tokens = max(turn.prompt_tokens - turn.cached_tokens, 0)
        turn.cache_hit_rate = (
            turn.cached_tokens / turn.prompt_tokens
            if turn.prompt_tokens else 0.0
        )
        task.prompt_tokens += prompt
        task.cached_tokens += cached
        task.actual_prompt_tokens += prompt
        task.completion_tokens += completion
        task.uncached_prompt_tokens = max(task.prompt_tokens - task.cached_tokens, 0)
        task.cache_hit_rate = (
            task.cached_tokens / task.prompt_tokens
            if task.prompt_tokens else 0.0
        )

        if source == "summary":
            turn.summary_prompt_tokens += prompt
            turn.summary_completion_tokens += completion
            turn.summary_total_tokens += total
            turn.summary_cached_tokens += cached
            task.summary_prompt_tokens += prompt
            task.summary_completion_tokens += completion
            task.summary_total_tokens += total
            task.summary_cached_tokens += cached
            task.summary_uncached_prompt_tokens = max(
                task.summary_prompt_tokens - task.summary_cached_tokens, 0,
            )
            task.summary_cache_hit_rate = (
                task.summary_cached_tokens / task.summary_prompt_tokens
                if task.summary_prompt_tokens else 0.0
            )
            turn.summary_uncached_prompt_tokens = max(
                turn.summary_prompt_tokens - turn.summary_cached_tokens, 0,
            )
            turn.summary_cache_hit_rate = (
                turn.summary_cached_tokens / turn.summary_prompt_tokens
                if turn.summary_prompt_tokens else 0.0
            )
            return

        estimate = max(int(estimated_prompt_tokens), 0)
        turn.estimated_prompt_tokens += estimate
        turn.main_prompt_tokens += prompt
        turn.main_cached_tokens += cached
        turn.main_uncached_prompt_tokens = max(
            turn.main_prompt_tokens - turn.main_cached_tokens, 0,
        )
        turn.main_cache_hit_rate = (
            turn.main_cached_tokens / turn.main_prompt_tokens
            if turn.main_prompt_tokens else 0.0
        )
        task.estimated_prompt_tokens += estimate
        task.main_prompt_tokens += prompt
        task.main_cached_tokens += cached
        task.main_uncached_prompt_tokens = max(
            task.main_prompt_tokens - task.main_cached_tokens, 0,
        )
        task.main_cache_hit_rate = (
            task.main_cached_tokens / task.main_prompt_tokens
            if task.main_prompt_tokens else 0.0
        )

    def set_message_count(self, count: int) -> None:
        """Set the current turn's message count (snapshot before LLM call)."""
        if self.current_turn:
            self.current_turn.message_count = count

    def record_assistant_content(self, content: str) -> None:
        """Record the LLM's text response for the current turn."""
        if self.current_turn:
            self.current_turn.assistant_content = content
