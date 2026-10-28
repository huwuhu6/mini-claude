"""In-memory execution state for one Main Agent run and its current tool batch."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import uuid


class RunStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ToolBatchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


class ToolBatchAdvance(str, Enum):
    """Control result after recording one terminal Tool Result."""

    CALL_COMPLETED = "call_completed"
    BATCH_COMPLETED = "batch_completed"
    PAUSED = "paused"


@dataclass(frozen=True)
class ToolCallResultState:
    call_id: str
    outcome: str
    content: str
    result: str | None = None
    effect: str | None = None
    non_execution_reason: str | None = None

    def __post_init__(self):
        from core.tool_outcome import legacy_facts, validate_facts, validate_non_execution
        if self.result is None:
            object.__setattr__(self, "result", legacy_facts(self.outcome)[0])
        if self.effect is None:
            object.__setattr__(self, "effect", "UNCERTAIN")
        validate_facts(self.result, self.effect)
        if self.effect == "NONE":
            validate_non_execution(self.outcome, self.non_execution_reason)
        elif self.non_execution_reason is not None:
            raise ValueError("未执行原因码只能用于 NONE")
        if self.outcome in {"UNKNOWN", "CANCELLED"} and (self.effect == "NONE" or self.result == "SUCCESS"):
            raise ValueError("未知执行事实不能降级为已成功或未启动")
        if self.outcome in {"EXECUTED_SUCCESS", "EXECUTED_FAILURE", "RECORDED_BEFORE_TERMINAL"} and self.effect == "NONE":
            raise ValueError("handler 已启动，不能记录 NONE")
        if self.outcome == "EXECUTED_SUCCESS" and self.result != "SUCCESS":
            raise ValueError("执行结果与 SUCCESS 冲突")
        if self.outcome == "EXECUTED_FAILURE" and self.result != "FAILED":
            raise ValueError("执行结果与 FAILED 冲突")
        if self.outcome not in {"EXECUTED_SUCCESS", "EXECUTED_FAILURE", "RECORDED_BEFORE_TERMINAL", "UNKNOWN", "CANCELLED"} and (self.result != "FAILED" or self.effect not in {"NONE", "UNCERTAIN"}):
            raise ValueError("未执行记录不能声明执行效果")


@dataclass
class ToolBatchState:
    """Ordered provider batch plus the cursor and outcomes needed to continue it."""

    tool_calls: list[dict[str, Any]]
    assistant_message_index: int
    model_iteration: int
    assistant_content: str = ""
    next_call_index: int = 0
    results: dict[str, ToolCallResultState] = field(default_factory=dict)
    resolved_call_ids: set[str] = field(default_factory=set)
    seen_signatures: set[tuple[str, Any]] = field(default_factory=set)
    pending_replan_messages: list[str] = field(default_factory=list)
    status: ToolBatchStatus = ToolBatchStatus.PENDING
    finalized: bool = False  # Results complete and deferred conversation feedback flushed.

    def __post_init__(self) -> None:
        # Keep the original provider payload stable while the runtime advances it.
        self.tool_calls = deepcopy(self.tool_calls)

    @property
    def pending_call_ids(self) -> list[str]:
        return [
            str(call.get("id", ""))
            for call in self.tool_calls[self.next_call_index:]
        ]

    @property
    def has_pending_calls(self) -> bool:
        return self.next_call_index < len(self.tool_calls)

    def start(self) -> None:
        if self.status is not ToolBatchStatus.PENDING:
            raise RuntimeError(f"工具批次不能从当前状态启动: {self.status.value}")
        self.status = ToolBatchStatus.RUNNING

    def record_result(
        self,
        call_id: str,
        outcome: str,
        content: str,
        *,
        pause_requested: bool = False,
        result: str | None = None,
        effect: str | None = None,
        non_execution_reason: str | None = None,
    ) -> ToolBatchAdvance:
        if self.status is not ToolBatchStatus.RUNNING:
            raise RuntimeError(f"工具批次当前不可推进: {self.status.value}")
        if not self.has_pending_calls:
            raise RuntimeError("工具批次已完成，拒绝重复推进")
        expected_id = str(self.tool_calls[self.next_call_index].get("id", ""))
        if call_id != expected_id:
            raise RuntimeError(
                f"工具批次必须按原始顺序推进，expected {expected_id!r}, got {call_id!r}"
            )
        # The Tool Result message is appended immediately before this state
        # transition. ``resolved_call_ids`` may therefore already contain it.
        if call_id in self.results:
            raise RuntimeError(f"Tool Call {call_id} 已有结果，拒绝重复记录")

        self.results[call_id] = ToolCallResultState(call_id, outcome, content, result, effect, non_execution_reason)
        self.resolved_call_ids.add(call_id)
        self.next_call_index += 1

        if not self.has_pending_calls:
            self.status = ToolBatchStatus.COMPLETED
            return ToolBatchAdvance.BATCH_COMPLETED
        if pause_requested:
            self.status = ToolBatchStatus.PAUSED
            return ToolBatchAdvance.PAUSED
        return ToolBatchAdvance.CALL_COMPLETED

    def mark_terminal(self, *, failed: bool = False) -> None:
        self.status = ToolBatchStatus.FAILED if failed else ToolBatchStatus.TERMINATED


@dataclass
class RunState:
    """Mutable process-local state for one user-requested Main Agent run."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    max_iterations: int = 50
    require_tool_call: bool = False
    status: RunStatus = RunStatus.RUNNING
    model_turns_used: int = 0
    no_tool_retry_count: int = 0
    tool_call_seen: bool = False
    pause_requested: bool = False
    trace_started: bool = False
    trace_task_id: str | None = None
    tool_batch: ToolBatchState | None = None
    terminal_result: str | None = None
    # Imported workspace precondition, retained through preparation failures.
    workspace_checkpoint_digest: str | None = None
    approval_id: str | None = None
    output_limit_retries: int = 0
    output_limit_reserved_tokens: int = 0
    output_limit_attempt: int = 0
    output_limit_last_request: dict[str, Any] = field(default_factory=dict)
    # Confirmed choice for later model turns; pending reservations stay separate.
    output_limit_effective_effort: str | None = None

    @property
    def has_pending_tool_batch(self) -> bool:
        return bool(
            self.tool_batch
            and self.tool_batch.has_pending_calls
            and self.tool_batch.status in {
                ToolBatchStatus.PENDING,
                ToolBatchStatus.RUNNING,
                ToolBatchStatus.PAUSED,
            }
        )

    def request_pause(self) -> None:
        if self.status is not RunStatus.RUNNING:
            raise RuntimeError(f"当前运行不能请求暂停: {self.status.value}")
        self.pause_requested = True

    def resume(self) -> None:
        if self.status is not RunStatus.PAUSED:
            raise RuntimeError("当前运行没有可继续的暂停状态")
        self.pause_requested = False
        self.status = RunStatus.RUNNING
        if self.has_pending_tool_batch and self.tool_batch is not None:
            self.tool_batch.status = ToolBatchStatus.RUNNING

    def record_tool_result(
        self,
        call_id: str,
        outcome: str,
        content: str,
        *, result: str | None = None, effect: str | None = None,
        non_execution_reason: str | None = None,
    ) -> ToolBatchAdvance:
        if self.status is not RunStatus.RUNNING or self.tool_batch is None:
            raise RuntimeError("当前运行没有可推进的工具批次")
        advance = self.tool_batch.record_result(
            call_id,
            outcome,
            content,
            pause_requested=self.pause_requested,
            result=result, effect=effect, non_execution_reason=non_execution_reason,
        )
        # The first completed call consumes the import boundary. Later tool writes
        # are ordinary run effects and must not be compared to the old workspace.
        self.workspace_checkpoint_digest = None
        self.approval_id = None
        if advance is ToolBatchAdvance.PAUSED:
            self.pause_requested = False
            self.status = RunStatus.PAUSED
        elif self.pause_requested:
            # If this was the last call, the batch is closed but the run must
            # still stop before requesting the next model turn.
            self.pause_requested = False
            self.status = RunStatus.PAUSED
        elif not self.tool_batch.has_pending_calls:
            self.pause_requested = False
        return advance

    def finish(self, status: RunStatus, result: str) -> str:
        self.status = status
        self.terminal_result = result
        self.approval_id = None
        self.pause_requested = False
        return result
