"""Result/effect vocabulary shared by tools and durable execution records."""
from enum import Enum

RESULTS = frozenset({"SUCCESS", "FAILED", "UNKNOWN"})
EFFECTS = frozenset({"NONE", "APPLIED", "PARTIAL", "UNCERTAIN"})


class NonExecutionReason(str, Enum):
    ARGUMENTS_REJECTED = "ARGUMENTS_REJECTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    FEATURE_DISABLED = "FEATURE_DISABLED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    PRECHECK_REJECTED = "PRECHECK_REJECTED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
    BATCH_ABORTED = "BATCH_ABORTED"


_REASONS_BY_OUTCOME = {
    "INVALID_ARGUMENTS": {NonExecutionReason.ARGUMENTS_REJECTED},
    "BLOCKED_PERMISSION_DENIED": {NonExecutionReason.PERMISSION_DENIED, NonExecutionReason.APPROVAL_REJECTED},
    "BLOCKED_FEATURE_DISABLED": {NonExecutionReason.FEATURE_DISABLED},
    "BLOCKED_UNKNOWN_TOOL": {NonExecutionReason.UNKNOWN_TOOL},
    "BLOCKED_PRECHECK": {NonExecutionReason.PRECHECK_REJECTED},
    "SKIPPED_DUPLICATE": {NonExecutionReason.DUPLICATE_SKIPPED},
    "BLOCKED_NOT_EXECUTED": {NonExecutionReason.PRECHECK_REJECTED, NonExecutionReason.BATCH_ABORTED},
    "NOT_EXECUTED": {NonExecutionReason.BATCH_ABORTED},
}


def non_execution_facts(reason: NonExecutionReason):
    """Only explicit pre-handler branches may supply this proof."""
    if not isinstance(reason, NonExecutionReason):
        raise ValueError("未执行原因必须来自白名单枚举")
    return {"result": "FAILED", "effect": "NONE", "non_execution_reason": reason.value}


def validate_non_execution(outcome, reason):
    try:
        reason = NonExecutionReason(reason)
    except (ValueError, TypeError) as exc:
        raise ValueError("NONE 缺少合法的未执行原因码") from exc
    if reason not in _REASONS_BY_OUTCOME.get(outcome, set()):
        raise ValueError("未执行原因码与 outcome 不匹配")


def legacy_facts(outcome):
    if outcome == "EXECUTED_SUCCESS":
        return "SUCCESS", "UNCERTAIN"
    if outcome == "EXECUTED_FAILURE":
        return "FAILED", "UNCERTAIN"
    if outcome in {"UNKNOWN", "CANCELLED", "RECORDED_BEFORE_TERMINAL"}:
        return "UNKNOWN", "UNCERTAIN"
    # A historical label is not positive evidence of where execution stopped.
    return "FAILED", "UNCERTAIN"


def validate_facts(result, effect):
    if result not in RESULTS or effect not in EFFECTS:
        raise ValueError("工具 result/effect 无效")
    if result == "UNKNOWN" and effect == "NONE":
        raise ValueError("UNKNOWN 不能解释为 handler 未启动")
