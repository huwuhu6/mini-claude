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


def pending_file_targets(batch, tools) -> dict:
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
        targets[call["id"]] = observation
    return targets


def require_resumable_scope(batch) -> None:
    if batch and any(call["function"]["name"] in {"bash", "powershell", "pwsh", "cmd"}
                     for call in batch.tool_calls[batch.next_call_index:]):
        raise ValueError("Shell 调用缺少可验证的作用域，不能恢复原调用；请归档后重新规划并审批")
