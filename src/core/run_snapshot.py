"""Versioned JSON contract, not a second runtime model or a persistence store."""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from typing import Any

from core.run_state import RunState, RunStatus, ToolBatchState, ToolBatchStatus, ToolCallResultState
from providers.base import Message

SCHEMA_VERSION = 4
CHECKPOINT_SNAPSHOT_VERSION = 5  # Bound projections cannot use standalone JSON resume.
HITL_SNAPSHOT_VERSION = 6  # Result/effect facts; earlier v1/v2/v3 remain readable.
STANDALONE_SNAPSHOT_VERSIONS = {1, SCHEMA_VERSION}
BOUND_SNAPSHOT_VERSIONS = {2, 3, CHECKPOINT_SNAPSHOT_VERSION, HITL_SNAPSHOT_VERSION}
HITL_SNAPSHOT_VERSIONS = {3, HITL_SNAPSHOT_VERSION}
READABLE_SNAPSHOT_VERSIONS = STANDALONE_SNAPSHOT_VERSIONS | BOUND_SNAPSHOT_VERSIONS
SNAPSHOT_FIELDS = {"schema_version", "workspace", "compatibility", "run", "messages",
                   "history", "todo", "agent_note", "memory", "context", "diagnostics"}
# UNKNOWN/cancellation/not-executed terminal closures are never resumable pauses.
PAUSED_RESULT_OUTCOMES = {"EXECUTED_SUCCESS", "EXECUTED_FAILURE", "SKIPPED_DUPLICATE",
    "INVALID_ARGUMENTS", "BLOCKED_PRECHECK", "BLOCKED_NOT_EXECUTED",
    "BLOCKED_FEATURE_DISABLED", "BLOCKED_UNKNOWN_TOOL", "BLOCKED_PERMISSION_DENIED"}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(f"运行快照无效: {reason}")


def fields(value: Any, keys: set[str], name: str) -> None:
    require(type(value) is dict and set(value) == keys, f"{name} 字段不完整或不兼容")


def integer(value: Any, name: str, minimum: int = 0) -> None:
    require(type(value) is int and value >= minimum, f"{name} 必须为有效整数")


def text(value: Any, name: str, *, nonempty: bool = False) -> None:
    require(type(value) is str and (not nonempty or bool(value.strip())), f"{name} 必须为文本")


def decode_tool_result(raw):
    legacy = {"call_id", "outcome", "content"}
    require(type(raw) is dict and set(raw) in (legacy, legacy | {"result", "effect"},
            legacy | {"result", "effect", "non_execution_reason"}), "工具结果字段不完整或不兼容")
    if "result" in raw:
        require(type(raw["result"]) is str and type(raw["effect"]) is str, "结果事实类型无效")
        # Prior unproven NONE labels cannot authorize a new execution. Keep the
        # original journal bytes; only the detached interpretation is conservative.
        if "non_execution_reason" not in raw and raw["effect"] == "NONE" and raw["result"] == "FAILED" and raw["outcome"] not in {"UNKNOWN", "CANCELLED", "EXECUTED_SUCCESS", "EXECUTED_FAILURE", "RECORDED_BEFORE_TERMINAL"}:
            raw = {**raw, "effect": "UNCERTAIN"}
    if "non_execution_reason" in raw:
        require(raw["non_execution_reason"] is None or type(raw["non_execution_reason"]) is str,
                "未执行原因码类型无效")
    return ToolCallResultState(**raw)


def same_tool_result(left, right):
    return decode_tool_result(left) == decode_tool_result(right)


def json_copy(value: Any) -> Any:
    """Reject Python objects (including custom encoders) and detach every container."""
    def check(item):
        if type(item) is dict:
            require(all(type(k) is str for k in item), "JSON 对象键必须为文本")
            for child in item.values():
                check(child)
        elif type(item) is list:
            for child in item:
                check(child)
        else:
            require(type(item) in {str, int, float, bool, type(None)}, "只支持 JSON 基本类型")
            if type(item) is float:
                require(math.isfinite(item), "不允许非有限数值")
    try:
        check(value)
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (RecursionError, TypeError, OverflowError) as exc:
        raise ValueError("运行快照必须为有限 JSON 数据") from exc


def encode_run(state: RunState) -> dict:
    data = asdict(state)
    data["status"] = state.status.value
    batch = state.tool_batch
    if batch is not None:
        data["tool_batch"]["status"] = batch.status.value
        data["tool_batch"]["resolved_call_ids"] = sorted(batch.resolved_call_ids)
        data["tool_batch"]["seen_signatures"] = [list(sig) for sig in sorted(batch.seen_signatures)]
    return data


def call_ids(calls: Any) -> list[str]:
    require(type(calls) is list and bool(calls), "Tool Calls 必须为非空有序列表")
    ids = []
    for call in calls:
        require(type(call) is dict and "id" in call and "function" in call, "缺少原始 Tool Call")
        text(call["id"], "Tool Call ID", nonempty=True)
        require(call.get("type", "function") == "function", "不支持的 Tool Call 类型")
        fn = call["function"]
        require(type(fn) is dict and {"name", "arguments"} <= set(fn), "缺少原始调用参数")
        text(fn["name"], "工具名", nonempty=True)
        # Preserve malformed argument JSON too: the loop must generate its own feedback.
        text(fn["arguments"], "原始参数")
        ids.append(call["id"])
    require(len(ids) == len(set(ids)), "批次内存在重复 Tool Call ID")
    return ids


def decode_run(data: Any, *, _terminal: bool = False, _pre_model: bool = False, _approval: bool = False) -> RunState:
    # v1/v2 snapshots produced before HITL have no approval field.
    if type(data) is dict and set(data) == set(RunState.__dataclass_fields__) - {"approval_id"}:
        data = {**data, "approval_id": None}
    fields(data, set(RunState.__dataclass_fields__), "RunState")
    text(data["run_id"], "Run ID", nonempty=True)
    waiting = data["status"] == RunStatus.WAITING_APPROVAL.value
    require(not waiting or (_approval and not _terminal and not _pre_model), "审批等待只能通过持久化审批边界恢复")
    require(data["approval_id"] is None or (_approval and type(data["approval_id"]) is str and bool(data["approval_id"])), "审批身份无效或不能独立恢复")
    require(not waiting or data["approval_id"] is not None, "等待状态缺少审批身份")
    if _terminal:
        require(data["status"] in {"completed", "terminated", "failed", "cancelled"}, "Checkpoint 终结状态错误")
        text(data["terminal_result"], "终结结果")
    else:
        require(data["status"] == RunStatus.PAUSED.value or waiting, "只恢复安全暂停中的 Run")
        require(data["terminal_result"] is None, "暂停 Run 不得含终结结果")
    integer(data["max_iterations"], "模型预算", 1)
    integer(data["model_turns_used"], "已用模型轮次", 0 if _terminal or _pre_model else 1)
    integer(data["no_tool_retry_count"], "无工具重试")
    require(data["no_tool_retry_count"] <= 1, "无工具重试超过现有运行契约")
    require(data["model_turns_used"] <= data["max_iterations"], "已用轮次超过预算")
    for key in ("require_tool_call", "tool_call_seen", "pause_requested", "trace_started"):
        require(type(data[key]) is bool, f"{key} 必须为布尔值")
    require((_terminal or (_pre_model and data["trace_started"]) or (data["tool_call_seen"] and data["trace_started"])) and not data["pause_requested"],
            "不是已确认的工具边界暂停")
    if not _terminal or data["trace_task_id"] is not None:
        text(data["trace_task_id"], "原 Trace ID", nonempty=True)
    digest = data["workspace_checkpoint_digest"]
    require(digest is None or (type(digest) is str and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest)), "工作区前置摘要格式错误")
    raw = data["tool_batch"]
    if (_terminal or _pre_model) and raw is None:
        return RunState(**{**data, "status": RunStatus(data["status"])})
    fields(raw, set(ToolBatchState.__dataclass_fields__), "ToolBatchState")
    ids = call_ids(raw["tool_calls"])
    cursor = raw["next_call_index"]
    integer(cursor, "批次游标")
    require(not waiting or cursor < len(ids), "等待审批没有当前未执行调用")
    integer(raw["assistant_message_index"], "Assistant 位置")
    integer(raw["model_iteration"], "批次模型轮次")
    text(raw["assistant_content"], "Assistant 内容")
    require(cursor <= len(ids), "游标越界")
    require((raw["model_iteration"] < data["model_turns_used"] if _terminal or _pre_model
             else raw["model_iteration"] == data["model_turns_used"] - 1), "批次轮次不一致")
    expected_status = ToolBatchStatus.PAUSED if cursor < len(ids) else ToolBatchStatus.COMPLETED
    if _terminal:
        require(cursor == len(ids) and raw["status"] in {"completed", "failed", "terminated"}, "终结批次未闭合")
        expected_status = ToolBatchStatus(raw["status"])
    else:
        require(raw["status"] == expected_status.value, "游标与批次阶段不一致")
    if _pre_model:
        require(cursor == len(ids) and raw["finalized"], "模型边界仍有未完成工具或收尾")
    require(type(raw["finalized"]) is bool, "finalized 必须为布尔值")
    require(not raw["finalized"] or cursor == len(ids), "待执行批次不能已收尾")
    require(type(raw["pending_replan_messages"]) is list
            and all(type(m) is str for m in raw["pending_replan_messages"]), "REPLAN 必须为文本列表")
    require(not raw["finalized"] or not raw["pending_replan_messages"], "收尾标记与 REPLAN 冲突")
    prefix = ids[:cursor]
    results = raw["results"]
    require(type(results) is dict and set(results) == set(prefix), "结果集合与游标不一致")
    decoded_results = {}
    for call_id, result in results.items():
        decoded_result = decode_tool_result(result)
        require(result["call_id"] == call_id, "结果关联了错误 ID")
        require(type(result["outcome"]) is str and result["outcome"] in (PAUSED_RESULT_OUTCOMES | {"UNKNOWN", "NOT_EXECUTED", "CANCELLED", "RECORDED_BEFORE_TERMINAL"}
                    if _terminal else PAUSED_RESULT_OUTCOMES),
                "结果不是可继续边界的已知执行事实")
        text(result["content"], "结果内容")
        decoded_results[call_id] = decoded_result
    resolved = raw["resolved_call_ids"]
    require(type(resolved) is list and all(type(i) is str for i in resolved)
            and len(resolved) == len(prefix) and set(resolved) == set(prefix), "已处理 ID 不一致")
    signatures = raw["seen_signatures"]
    require(type(signatures) is list and all(type(s) is list and len(s) == 2
            and all(type(v) is str for v in s) for s in signatures), "去重签名格式错误")
    seen = {tuple(s) for s in signatures}
    expected_seen = {(c["function"]["name"], c["function"]["arguments"])
                     for c in raw["tool_calls"][:cursor]}
    require(len(seen) == len(signatures) and (seen <= expected_seen if _terminal else seen == expected_seen), "去重状态与已处理调用不一致")
    batch = ToolBatchState(**{**raw, "status": expected_status, "results": decoded_results,
                              "resolved_call_ids": set(resolved), "seen_signatures": seen})
    return RunState(**{**data, "status": RunStatus(data["status"]), "tool_batch": batch})


def decode_messages(data: Any, state: RunState, *, _pre_model: bool = False) -> list[Message]:
    require(type(data) is list, "messages 必须为列表")
    messages = []
    for raw in data:
        fields(raw, set(Message.__dataclass_fields__), "Message")
        require(type(raw["role"]) is str and raw["role"] in {"user", "assistant", "system", "tool"}, "消息角色错误")
        text(raw["content"], "消息内容")
        for key in ("name", "tool_call_id"):
            require(raw[key] is None or type(raw[key]) is str, f"{key} 类型错误")
        if raw["role"] != "assistant":
            require(raw["tool_calls"] is None, "只有 Assistant 可以包含调用")
        if raw["role"] != "tool":
            require(raw["tool_call_id"] is None, "只有工具结果可以关联调用 ID")
        messages.append(Message(**raw))
    batch = state.tool_batch
    if batch is not None:
        index = batch.assistant_message_index
        require(index < len(messages), "当前批次没有 Assistant 消息")
        assistant = messages[index]
        require(assistant.role == "assistant" and assistant.tool_calls == batch.tool_calls
                and assistant.content == batch.assistant_content, "当前 Assistant 与批次原始响应不一致")
        end = index + 1 + batch.next_call_index
        require(end <= len(messages), "缺少已完成 Tool Result")
        for i, call in enumerate(batch.tool_calls[:batch.next_call_index]):
            message = messages[index + i + 1]
            require(message.role == "tool" and message.tool_call_id == call["id"]
                    and message.content == batch.results[call["id"]].content, "结果内容或顺序冲突")
        if not batch.finalized:
            require(end == len(messages), "未收尾批次尾部存在多余消息")
        elif state.status is RunStatus.PAUSED and not _pre_model:
            require(all(m.role == "user" for m in messages[end:]),
                    "已收尾暂停点之后只能有延后的用户重规划反馈")
    # Every previous batch must close; only this batch may have a pending suffix.
    expected = []
    position = 0
    for message in messages:
        if message.role == "tool":
            require(position < len(expected) and message.tool_call_id == expected[position],
                    "孤立、重复或错序 Tool Result")
            position += 1
            continue
        require(position == len(expected), "普通消息插入未完成工具结果之间")
        expected, position = [], 0
        if message.role == "assistant" and message.tool_calls is not None:
            expected = call_ids(message.tool_calls)
    remaining = expected[position:]
    require(remaining == (batch.pending_call_ids if batch is not None else []), "消息中待执行调用与游标不一致")
    return messages
