"""Pure edit preparation shared by execution and approval previews."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EditPreparationError(ValueError):
    """A rejected edit has no filesystem effects."""


def prepare_file_edits(working_content: str, edits: list):
    # Detect and normalise line endings (CRLF → LF) so that
    # \r\n / \n mismatch never causes a spurious match failure.
    original_line_ending = "\r\n" if "\r\n" in working_content else "\n"
    working_content = working_content.replace("\r\n", "\n")

    # ── Phase 2: Pre-validate ALL edits against original ──
    prepared = []  # [{search, replace, offset, line_no}]

    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise EditPreparationError(
                f"错误: 第 {i+1} 处编辑格式无效，应为 object",)
        search_raw = edit.get('search')
        replace_raw = edit.get('replace')
        if not search_raw:
            raise EditPreparationError(
                f"错误: 第 {i+1} 处编辑缺少 'search' 或 search 为空。\n"
                f"edit_file 只接受精确局部修改，需要提供具体代码片段。"
                f"新建文件或全量覆盖请使用 write_file。",)
        if replace_raw is None:
            raise EditPreparationError(
                f"错误: 第 {i+1} 处编辑缺少 'replace' 字段",)
        search = search_raw.replace("\r\n", "\n")
        replace = replace_raw.replace("\r\n", "\n")

        # ── Upper-bound guard ──────────────────────────────
        if len(search) > 2000 or len(replace) > 2000:
            logger.warning(
                f"[Harness 拦截] edit_file 载荷过大: "
                f"search={len(search)}, replace={len(replace)}"
            )
            raise EditPreparationError(
                f"【系统安全拦截】第 {i+1} 处修改失败。\n"
                f"你的 'search' 块 ({len(search)} 字符) 或 "
                f"'replace' 块 ({len(replace)} 字符) 超出限制 "
                f"(2000)！请将修改范围压缩至核心函数 "
                f"（建议 50 行以内）后重新调用。\n"
                f"当前事务已回滚，未做任何修改。",)

        # ── Resolve search scope (global or windowed) ────────
        approx_line_start = edit.get('approx_line_start')
        SEARCH_WINDOW = 50

        if approx_line_start is not None:
            lines = working_content.splitlines(True)
            total = len(lines)
            ws = max(1, int(approx_line_start) - SEARCH_WINDOW)
            we = min(total, int(approx_line_start) + SEARCH_WINDOW)
            global_before = len(''.join(lines[:ws - 1]))
            scope_text = ''.join(lines[ws - 1:we])
        else:
            scope_text = working_content
            ws = we = global_before = None

        # ── Uniqueness check — the ONLY safety gate ──────
        count = scope_text.count(search)
        if count == 0:
            hint = (
                f"（在 {ws}-{we} 行范围内查找，"
                f"approx_line_start={approx_line_start} "
                f"估计可能有偏差）"
                if approx_line_start is not None
                else ""
            )
            raise EditPreparationError(
                f"【Harness 事务拦截】第 {i+1} 处修改匹配失败。\n"
                f"无法在文件中定位您的 'search' 片段，请重新使用 "
                f"read_file 核对该段代码的精准缩进与换行符。{hint}\n"
                f"当前文件已自动整体回滚，未做任何修改。",)
        if count > 1:
            local_off = scope_text.find(search)
            ex_line_raw = scope_text[:local_off].count('\n') + 1
            ex_line_global = ex_line_raw + (ws - 1) if ws else ex_line_raw
            hint = (
                f"（在 {ws}-{we} 行范围内仍匹配到 {count} 处，"
                f"请补充更多上下文或调整 approx_line_start）"
                if approx_line_start is not None
                else f"（例如第 {ex_line_global} 行附近）"
            )
            raise EditPreparationError(
                f"【Harness 事务拦截】第 {i+1} 处修改匹配到 {count} 处 "
                f"{hint}。请提供更多上下文使 search 字符串在文件中唯一。\n"
                f"当前文件已自动整体回滚，未做任何修改。",)

        # ── Calculate absolute byte offset ────────────────────
        if approx_line_start is not None:
            local_offset = scope_text.find(search)
            offset = global_before + local_offset
        else:
            offset = working_content.find(search)
        line_no = working_content[:offset].count('\n') + 1
        prepared.append({
            'search': search, 'replace': replace,
            'offset': offset, 'line_no': line_no,
        })

    # ── Phase 3: Execute in reverse-offset order ──────────
    # Apply from file bottom → top so each earlier (higher-up)
    # edit's offset stays correct regardless of text-length shifts.
    prepared.sort(key=lambda x: x['offset'], reverse=True)

    edit_summaries = []
    for edit in prepared:
        s, r, off, ln = (
            edit['search'], edit['replace'],
            edit['offset'], edit['line_no'],
        )
        working_content = (
            working_content[:off] + r + working_content[off + len(s):]
        )
        edit_summaries.append((
            s[:40].replace('\n', ' '),
            ln,
            r[:40].replace('\n', ' '),
        ))

    # Restore original line-ending style to preserve file convention
    if original_line_ending == "\r\n":
        working_content = working_content.replace("\n", "\r\n")

    return working_content, edit_summaries
