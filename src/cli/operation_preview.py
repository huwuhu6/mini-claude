"""Read-only previews; permission bindings always retain the original arguments."""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from core.display_safety import redact, safe_text
from core.permission_grants import file_target
from core.tools.file_edits import prepare_file_edits


def operation_preview(operation):
    args = operation.arguments
    if operation.tool_name == "bash":
        return "Shell 命令（完整命令授权，不是命令前缀）：\n" + safe_text(args.get("command", ""))
    if operation.tool_name not in {"edit_file", "write_file"}:
        return json.dumps(redact(args), ensure_ascii=False, indent=2)
    try:
        target = file_target(operation)
        relative = target.relative_to(Path(operation.workspace))
        if target.exists() and (not target.is_file() or target.stat().st_size > 4 * 1024 * 1024):
            raise ValueError("文件非普通文件或超过预览大小限制")
        with target.open("r", encoding="utf-8", newline="") if target.exists() else _empty_content() as handle:
            before = handle.read()
        if operation.tool_name == "edit_file":
            if not target.exists():
                raise ValueError("edit_file 的目标文件不存在")
            after, _ = prepare_file_edits(before, args["edits"])
            action = f"修改 · {len(args['edits'])} 处编辑"
        else:
            after = args["content"]
            action = "覆盖已有文件" if target.exists() else "新建文件"
        diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                    fromfile=f"修改前/{relative}", tofile=f"修改后/{relative}", lineterm=""))
        added = sum(line.startswith("+") and not line.startswith("+++") for line in diff)
        removed = sum(line.startswith("-") and not line.startswith("---") for line in diff)
        return safe_text(f"{relative} · {action} · +{added} / -{removed}\n"
                         + ("\n".join(diff) if diff else "内容没有变化。"))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Never present a snippet-only diff as a verified file transformation.
        return safe_text(f"无法确认文件预览：{exc}\n原始操作参数（展示脱敏）：\n"
                         + json.dumps(redact(args), ensure_ascii=False, indent=2))


def _empty_content():
    from io import StringIO
    return StringIO("")
