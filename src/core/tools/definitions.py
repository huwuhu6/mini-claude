"""Shared ToolSpec factories for tools used by multiple agent types."""
from __future__ import annotations

from typing import Any, Callable

from core.tools.base_tools import READ_FILE_MAX_LINES
from core.tools.registry import ToolSpec


ToolHandler = Callable[..., Any]


def bash_spec(
    handler: ToolHandler,
    description: str = (
        "Run shell commands for builds/tests, system inspection, binary analysis, and shell operations. "
        "For ordinary source-file reading or search, prefer read_file or search_code for bounded observations."
    ),
) -> ToolSpec:
    return ToolSpec(
        name="bash",
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run"},
            },
            "required": ["command"],
        },
        handler=handler,
    )


def read_file_spec(handler: ToolHandler) -> ToolSpec:
    return ToolSpec(
        name="read_file",
        description=(
            f"Read a known file (source, configuration, or text) by contiguous 1-based line range. "
            f"Returns at most {READ_FILE_MAX_LINES} lines and is subject to character/byte limits; "
            "read later ranges to continue through long files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the known file"},
                "start_line": {
                    "type": "integer",
                    "description": "Inclusive 1-based first line; omit to start at the beginning",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Inclusive 1-based last line; backend limits may truncate the range",
                },
            },
            "required": ["path"],
        },
        handler=handler,
    )


def write_file_spec(handler: ToolHandler) -> ToolSpec:
    return ToolSpec(
        name="write_file",
        description="Write content to a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
    )


def edit_file_spec(handler: ToolHandler) -> ToolSpec:
    return ToolSpec(
        name="edit_file",
        description=(
            "Apply precise text replacements to an existing file. CRITICAL RULE: Keep "
            "replacements focused on the affected function body or block (usually under "
            "20 lines). Avoid copying entire large classes. Think of this as a unified "
            "diff. If your search/replace blocks are overly large, the system will "
            "actively REJECT the edit. For new files or full overwrites, use write_file "
            "instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file (must exist)"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "search": {
                                "type": "string",
                                "description": (
                                    "Exact text fragment to find in the existing file. Must "
                                    "be non-empty and appear exactly once — for new files "
                                    "or full overwrites use write_file. Max 2000 characters. "
                                    "Do NOT copy entire large classes."
                                ),
                            },
                            "replace": {"type": "string", "description": "Replacement text"},
                            "approx_line_start": {
                                "type": "integer",
                                "description": (
                                    "可选的预估行号（1-based）。提供此值时，搜索范围将锁定在 "
                                    "±50 行的局部窗口内。适合大文件中存在多处相似代码块时，"
                                    "辅助后端精准定位，避免全局 count>1 拦截。"
                                ),
                            },
                        },
                        "required": ["search", "replace"],
                    },
                    "description": (
                        "Array of search/replace pairs. Applied in order, atomically. If ANY "
                        "search fails, ALL edits roll back."
                    ),
                },
            },
            "required": ["path", "edits"],
        },
        handler=handler,
    )
