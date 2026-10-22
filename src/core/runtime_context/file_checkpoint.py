"""File observations for recovery checks; observations are not execution facts."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def file_fingerprint(path: Path) -> dict:
    """Fail closed on unreadable files; include identity and metadata, not just bytes."""
    path = Path(path)
    result = {"path": str(path.resolve())}
    try:
        if not path.exists():
            return {**result, "kind": "missing"}
        if not path.is_file():
            return {**result, "kind": "other"}
        before = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
        stable = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                value.st_mtime_ns, value.st_ctime_ns, value.st_mode)
        if stable(before) != stable(after):
            return {**result, "kind": "unavailable"}
        return {**result, "kind": "file", "sha256": digest,
                "identity": [after.st_dev, after.st_ino, after.st_size,
                             after.st_mtime_ns, after.st_ctime_ns, after.st_mode]}
    except OSError:
        return {**result, "kind": "unavailable"}


def pending_file_targets(batch, tools, trusted=None) -> dict:
    if batch is None:
        return {}
    import json
    targets = {}
    for call in batch.tool_calls[batch.next_call_index:]:
        function = call["function"]
        if function["name"] not in {"write_file", "edit_file"}:
            continue
        try:
            args = json.loads(function["arguments"])
            if type(args) is not dict or type(args.get("path")) is not str:
                continue  # Normal argument validation will reject it before dispatch.
            path = tools.safe_path(args["path"])
        except (ValueError, TypeError, OSError):
            continue  # Never read a path outside the current authority.
        observation = file_fingerprint(path)
        expected, reason = None, "工具实现没有受支持的文件核验协议"
        if trusted and trusted.get(function["name"]):
            try:
                if function["name"] == "write_file" and type(args.get("content")) is str:
                    # Match open(..., newline=None), including Windows translation.
                    payload = args["content"].replace("\n", os.linesep).encode("utf-8")
                elif function["name"] == "edit_file" and observation["kind"] == "file":
                    from core.tools.file_edits import prepare_file_edits
                    original = path.read_bytes()
                    if hashlib.sha256(original).hexdigest() != observation["sha256"]:
                        raise ValueError("读取期间目标变化")
                    content, _ = prepare_file_edits(original.decode("utf-8"), args["edits"])
                    payload = content.encode("utf-8")
                else:
                    raise ValueError("参数或目标不满足文件核验协议")
                expected, reason = hashlib.sha256(payload).hexdigest(), ""
            except (ValueError, TypeError, KeyError, OSError, AttributeError):
                reason = "无法可靠计算文件预期内容"
        targets[call["id"]] = {**observation, "expected_sha256": expected, "verification_reason": reason}
    return targets


def classify_file_observation(prepared, current):
    """Only classify present content. Never infer execution or retry permission."""
    before, expected = prepared["before"], prepared["expected_sha256"]
    if expected is None or current["kind"] not in {"file", "missing"} or current["path"] != before["path"]:
        return "UNVERIFIABLE"
    if current.get("sha256") == expected:
        return "POSTCONDITION_SATISFIED"
    if current["kind"] == before["kind"] and current.get("sha256") == before.get("sha256"):
        return "PRECONDITION_MATCH"
    return "CONFLICT"


def require_resumable_scope(batch) -> None:
    if batch and any(call["function"]["name"] in {"bash", "powershell", "pwsh", "cmd"}
                     for call in batch.tool_calls[batch.next_call_index:]):
        raise ValueError("Shell 调用缺少可验证的作用域，不能恢复原调用；请归档后重新规划并审批")
