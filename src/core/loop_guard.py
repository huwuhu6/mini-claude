"""
Loop Guard — Industrial-grade infinite-loop detection and forced meta-reflection.

Prevents LLM agents from blindly retrying the same failing tool call by
tracking recent tool invocations and physically intercepting suspected loops
before they execute.
"""
from __future__ import annotations
import json
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

# Canonical type for a recorded tool call
ToolCallRecord = Tuple[str, str]  # (tool_name, canonicalized_args_json)


def canonicalize_args(args: Dict[str, Any]) -> str:
    """Produce a stable, order-independent JSON fingerprint of tool arguments."""
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def build_loop_block_message(tool_name: str, args: Dict[str, Any]) -> str:
    """Generate the stern system warning injected as a simulated tool result."""
    args_display = json.dumps(args, ensure_ascii=False)
    return (
        f"⛔ [系统安全拦截 — 防死循环保护]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"检测到你正在重复使用相同的工具和参数：\n"
        f"  工具名称: {tool_name}\n"
        f"  调用参数: {args_display}\n"
        f"\n"
        f"该调用已被系统物理拦截——工具未被执行。\n"
        f"请勿盲目重试！\n"
        f"\n"
        f"你必须在下一次回复中最先输出一个 <reflection> 标签，"
        f"在其中深刻分析：\n"
        f"  1. 为什么之前的尝试反复失败？\n"
        f"  2. 当前策略的根本问题是什么？\n"
        f"  3. 有哪些与之前完全不同的替代方案？\n"
        f"\n"
        f"完成反思后，请提出一个与之前所有尝试本质上不同的新策略。\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


class LoopGuard:
    """Tracks recent tool calls and detects infinite-loop patterns.

    Detection rules (either triggers interception):
    1. **Consecutive duplicate** — current call is byte-identical to the
       immediately preceding call.
    2. **Frequency threshold** — the same (tool_name, args) pair appears
       at least *N* times within the last *max_recent* recorded calls
       (default: 2 occurrences within the last 3 calls).

    When a loop is detected the guard returns a blocking message that
    should be fed back to the LLM as a simulated tool result.  The real
    tool is **never executed** for the intercepted call.
    """

    def __init__(self, max_recent: int = 3, min_occurrences: int = 2):
        """
        Args:
            max_recent: Size of the sliding window (number of recent calls to inspect).
            min_occurrences: How many times the same call must appear within the
                             window to trigger rule 2.
        """
        self.max_recent = max_recent
        self.min_occurrences = min_occurrences
        self.recent_calls: List[ToolCallRecord] = []

    def check(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Inspect the proposed call before execution.

        Returns:
            ``None`` if the call is safe to execute, otherwise a blocking
            warning message (to be used as the simulated tool result).
        """
        if not self.recent_calls:
            return None

        current_sig = canonicalize_args(args)

        # ── Rule 1: exact consecutive duplicate ─────────────────
        prev_name, prev_sig = self.recent_calls[-1]
        if prev_name == tool_name and prev_sig == current_sig:
            logger.warning(
                f"防死循环拦截(连续重复): {tool_name} {current_sig[:120]}"
            )
            return build_loop_block_message(tool_name, args)

        # ── Rule 2: ≥ min_occurrences in the sliding window ─────
        window = self.recent_calls[-self.max_recent:]
        match_count = sum(
            1 for name, sig in window
            if name == tool_name and sig == current_sig
        )
        if match_count >= self.min_occurrences:
            logger.warning(
                f"防死循环拦截(频率阈值 {match_count}/{self.max_recent}): "
                f"{tool_name} {current_sig[:120]}"
            )
            return build_loop_block_message(tool_name, args)

        return None

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        """Record a tool call (whether executed or intercepted)."""
        sig = canonicalize_args(args)
        self.recent_calls.append((tool_name, sig))
        # Keep the sliding window bounded
        if len(self.recent_calls) > self.max_recent * 3:
            self.recent_calls = self.recent_calls[-self.max_recent:]

    def clear(self) -> None:
        """Reset the call history (e.g., on new conversation turn)."""
        self.recent_calls.clear()
