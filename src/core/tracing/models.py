"""
Trace Data Models — dataclass-based runtime traces for the agent loop.

Three-tier hierarchy:
  TaskTrace  (one per agent.chat() call)
   └── TurnTrace  (one per LLM iteration inside _llm_tool_cycle)
        └── ToolTrace  (one per tool_call executed)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class TaskFinalStatus(Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    LOOP_ABORTED = "LOOP_ABORTED"
    ROLLED_BACK = "ROLLED_BACK"
    CIRCUIT_BROKEN = "CIRCUIT_BROKEN"  # V3: hard circuit breaker terminated


@dataclass
class ToolTrace:
    """Trace for a single tool call execution."""
    tool_name: str = ""
    args_hash: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    loop_guard_blocked: bool = False
    error_message: str = ""
    result_preview: str = ""
    # Failure Intelligence fields
    failure_category: str = ""
    recoverability: str = ""
    strategy_fingerprint: str = ""
    escalated: bool = False
    # V3 Circuit Breaker
    circuit_breaker_triggered: bool = False
    # Runtime Context fields
    cwd: str = ""
    workspace_root: str = ""
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            'tool_name': self.tool_name,
            'args_hash': self.args_hash,
            'started_at': round(self.started_at, 3),
            'finished_at': round(self.finished_at, 3),
            'latency_ms': round(self.latency_ms, 1),
            'success': self.success,
            'loop_guard_blocked': self.loop_guard_blocked,
            'error_message': self.error_message[:200],
            'result_preview': self.result_preview[:200],
            'failure_category': self.failure_category,
            'recoverability': self.recoverability,
            'strategy_fingerprint': self.strategy_fingerprint,
            'escalated': self.escalated,
            'circuit_breaker_triggered': self.circuit_breaker_triggered,
            'cwd': self.cwd,
            'workspace_root': self.workspace_root,
            'session_id': self.session_id,
        }


@dataclass
class TurnTrace:
    """Trace for a single LLM iteration inside _llm_tool_cycle."""
    iteration: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    message_count: int = 0
    tool_calls_count: int = 0
    token_usage: int = 0
    assistant_content: str = ""
    compression_triggered: bool = False
    reflection_triggered: bool = False
    tools: List[ToolTrace] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'iteration': self.iteration,
            'started_at': round(self.started_at, 3),
            'finished_at': round(self.finished_at, 3),
            'message_count': self.message_count,
            'tool_calls_count': self.tool_calls_count,
            'token_usage': self.token_usage,
            'assistant_content': self.assistant_content,
            'compression_triggered': self.compression_triggered,
            'reflection_triggered': self.reflection_triggered,
            'tools': [t.to_dict() for t in self.tools],
        }


@dataclass
class TaskTrace:
    """Top-level trace for one agent.chat() execution."""
    task_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    total_turns: int = 0
    total_tool_calls: int = 0
    total_tokens: int = 0
    compression_count: int = 0
    rollback_count: int = 0
    loop_guard_trigger_count: int = 0
    reflection_count: int = 0
    circuit_breaker_trigger_count: int = 0
    final_status: str = ""
    user_prompt: str = ""
    workspace_root: str = ""
    workspace_confirmed: bool = False
    require_tool_call: bool = False
    no_tool_retry_count: int = 0
    runtime_error: str = ""
    turns: List[TurnTrace] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'started_at': round(self.started_at, 3),
            'finished_at': round(self.finished_at, 3),
            'total_turns': self.total_turns,
            'total_tool_calls': self.total_tool_calls,
            'total_tokens': self.total_tokens,
            'compression_count': self.compression_count,
            'rollback_count': self.rollback_count,
            'loop_guard_trigger_count': self.loop_guard_trigger_count,
            'reflection_count': self.reflection_count,
            'circuit_breaker_trigger_count': self.circuit_breaker_trigger_count,
            'final_status': self.final_status,
            'user_prompt': self.user_prompt,
            'workspace_root': self.workspace_root,
            'workspace_confirmed': self.workspace_confirmed,
            'require_tool_call': self.require_tool_call,
            'no_tool_retry_count': self.no_tool_retry_count,
            'runtime_error': self.runtime_error,
            'turns': [t.to_dict() for t in self.turns],
        }
