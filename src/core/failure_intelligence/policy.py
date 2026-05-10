"""
FailureEscalationPolicy — Decides when to stop retrying and escalate.

Core logic:
    If a failure category has recurred >= N times AND strategy diversity
    is low AND recoverability indicates user intervention is needed:
        → ESCALATE (stop retrying, inject system-level escalation message)
"""
from __future__ import annotations
from typing import Tuple

from .models import FailureSignature, FailureCategory, Recoverability


class FailureEscalationPolicy:
    """Rule-based escalation policy for the agent loop."""

    # Thresholds
    SAME_CATEGORY_ESCALATION = 3       # ≥3 same-category failures → escalate
    SAME_CATEGORY_HIGH_WATER = 5       # ≥5 same-category → escalate regardless of diversity
    MIN_STRATEGY_DIVERSITY = 2         # <2 distinct strategies → escalate

    # Categories that always warrant escalation on repetition
    USER_INTERVENTION_CATEGORIES = {
        FailureCategory.NETWORK_UNREACHABLE,
        FailureCategory.PERMISSION_DENIED,
        FailureCategory.PACKAGE_NOT_FOUND,
        FailureCategory.OUT_OF_MEMORY,
        FailureCategory.DISK_FULL,
    }

    def should_escalate(
        self,
        signature: FailureSignature,
        same_category_count: int,
        strategy_diversity: int,
    ) -> Tuple[bool, str]:
        """Determine if the agent should stop retrying and escalate.

        Args:
            signature: The FailureSignature from the latest failure.
            same_category_count: How many times this failure category has occurred.
            strategy_diversity: How many distinct strategies have been tried.

        Returns:
            (should_escalate: bool, reason: str)
        """
        if signature.category == FailureCategory.UNKNOWN:
            return False, ""

        cat = signature.category

        # Rule 1: High water mark — regardless of diversity or recoverability
        if same_category_count >= self.SAME_CATEGORY_HIGH_WATER:
            return True, (
                f"同一类型失败已发生 {same_category_count} 次 ({cat.value})，"
                f"Runtime 判定继续重试无效"
            )

        # Rule 2: Same category ≥ threshold + low diversity + user intervention needed
        if (same_category_count >= self.SAME_CATEGORY_ESCALATION
                and strategy_diversity < self.MIN_STRATEGY_DIVERSITY
                and cat in self.USER_INTERVENTION_CATEGORIES):
            strategies_tried = strategy_diversity if strategy_diversity > 0 else "未知"
            return True, (
                f"检测到持续{signature.root_cause_hint or cat.value}。\n"
                f"已尝试 {same_category_count} 次，策略数: {strategies_tried}\n"
                f"Runtime 判断当前问题属于用户干预类型，继续重试大概率无效。"
            )

        # Rule 3: Non-recoverable failure — escalate immediately
        if signature.recoverability == Recoverability.NON_RECOVERABLE:
            return True, f"不可恢复错误: {signature.root_cause_hint or cat.value}"

        return False, ""


# ── Escalation Message Builder ──────────────────────────────────────────

def build_escalation_message(signature: FailureSignature, reason: str) -> str:
    """Generate a system-level escalation message to inject into the conversation."""
    cat = signature.category.value if signature.category else "UNKNOWN"
    rec = signature.recoverability.value if signature.recoverability else "UNKNOWN"
    return (
        f"[系统级 Escalation — Failure Intelligence]\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"类型: {cat}\n"
        f"可恢复性: {rec}\n"
        f"根因: {signature.root_cause_hint}\n"
        f"\n"
        f"{reason}\n"
        f"\n"
        f"建议操作:\n"
        f"  → 请用户检查环境配置或网络设置\n"
        f"  → 或提供新的操作指令绕过此问题\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"[如需继续尝试，请使用全新策略直接执行]"
    )
