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

        self.results[call_id] = ToolCallResultState(call_id, outcome, content)
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
    ) -> ToolBatchAdvance:
        if self.status is not RunStatus.RUNNING or self.tool_batch is None:
            raise RuntimeError("当前运行没有可推进的工具批次")
        advance = self.tool_batch.record_result(
            call_id,
            outcome,
            content,
            pause_requested=self.pause_requested,
        )
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
        self.pause_requested = False
        return result
