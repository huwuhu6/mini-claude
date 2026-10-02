import os
import ast
import codecs
import re
import tempfile
import logging
import time
import uuid
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING, Any, Dict
from dataclasses import dataclass, field

from core.runtime_context.command_policy import CommandPolicy
from core.runtime_context.workspace_authority import WorkspaceAuthority

if TYPE_CHECKING:
    from core.runtime_context.shell_session import ShellSession

logger = logging.getLogger(__name__)

# Shared output/resource limits.  Tool-specific limits below are derived from
# these values so a caller cannot trade one unbounded output path for another.
TOOL_OUTPUT_MAX_CHARS = 120_000
TOOL_OUTPUT_MAX_BYTES = 300 * 1024
TOOL_OUTPUT_PREVIEW_CHARS = 4_000
FILE_READ_CHUNK_BYTES = 64 * 1024

# Backend limits for read_file.  These are hard bounds even when the model
# supplies an explicit end_line; max_lines remains only a caller-facing hint.
READ_FILE_MAX_LINES = 200
READ_FILE_MAX_CHARS = 100_000
READ_FILE_MAX_BYTES = 256 * 1024
SEARCH_CODE_MAX_CONTEXT_LINES = 50
SEARCH_CODE_MAX_MATCHES = 200


def _truncate_utf8(text: str, max_chars: int, max_bytes: int) -> str:
    """Truncate text by both character and UTF-8 byte limits."""
    limited = (text or '')[:max_chars]
    encoded = limited.encode('utf-8')
    if len(encoded) <= max_bytes:
        return limited
    return encoded[:max_bytes].decode('utf-8', errors='ignore')


def _bound_tool_output(text: str, notice: str = '') -> str:
    """Keep a tool result below the absolute shared output bound."""
    text = text or ''
    if (len(text) <= TOOL_OUTPUT_MAX_CHARS
            and len(text.encode('utf-8')) <= TOOL_OUTPUT_MAX_BYTES):
        return text
    suffix = f"\n\n{notice}" if notice else ''
    bounded = _truncate_utf8(
        text,
        max(0, TOOL_OUTPUT_MAX_CHARS - len(suffix)),
        max(0, TOOL_OUTPUT_MAX_BYTES - len(suffix.encode('utf-8'))),
    )
    return bounded + suffix


@dataclass
class ToolResult:
    """Tool output with execution facts kept separate from display text."""
    content: str
    success: bool = True
    execution_success: Optional[bool] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    segment_exit_codes: Optional[List[int]] = None
    output_visibility: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.execution_success is None:
            self.execution_success = self.success
        if self.segment_exit_codes is None:
            self.segment_exit_codes = []


# Shared policy instance
_COMMAND_POLICY = CommandPolicy()


def _is_relative_to(path: Path, base: Path) -> bool:
    """Check if path is relative to base, with Python version compatibility."""
    try:
        return path.is_relative_to(base)
    except AttributeError:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


class BaseTools:
    """Base tools for the agent."""

    # ── search_code ignore rules ──────────────────────────────────────
    IGNORE_DIRS: frozenset = frozenset({
        '.git', '__pycache__', 'node_modules', '.venv', 'venv',
        'dist', 'build', '.claude', '.pytest_cache', '.mypy_cache',
        '.egg-info', '.tox', '.env',
    })
    IGNORE_EXTS: frozenset = frozenset({
        '.pyc', '.pyo', '.so', '.dll', '.exe', '.o', '.a', '.lib',
        '.dylib', '.nupkg', '.class',
    })
    MAX_FILE_SIZE: int = 1 * 1024 * 1024  # 1 MB

    def __init__(self, workdir: Path, authority: Optional[WorkspaceAuthority] = None,
                 shell_session: Optional['ShellSession'] = None):
        self.workdir = workdir
        self._allowed_paths: List[Path] = []  # Path whitelist
        self._authority = authority
        self.shell_session = shell_session
        logger.info(f"BaseTools 已初始化，工作目录: {workdir}")

    # ── Path Whitelist Management ──────────────────────────────────

    def add_allowed_path(self, path: str) -> str:
        """Add a directory path to the validation whitelist."""
        if self._authority:
            return self._authority.add_root(path)
        resolved = Path(path).resolve()
        if not resolved.exists():
            return f"错误: 路径不存在: {path}"
        if not resolved.is_dir():
            return f"错误: 路径不是目录: {path}"
        if resolved in self._allowed_paths:
            return f"路径已在白名单中: {resolved}"
        self._allowed_paths.append(resolved)
        logger.info(f"已添加路径到白名单: {resolved}")
        return f"已添加路径到白名单: {resolved}"

    def remove_allowed_path(self, path: str) -> str:
        """Remove a directory from the validation whitelist."""
        if self._authority:
            return self._authority.remove_root(path)
        resolved = Path(path).resolve()
        if resolved in self._allowed_paths:
            self._allowed_paths.remove(resolved)
            logger.info(f"已从白名单移除路径: {resolved}")
            return f"已从白名单移除路径: {resolved}"
        return f"路径不在白名单中: {resolved}"

    def list_allowed_paths(self) -> str:
        """List all paths in the whitelist."""
        if self._authority:
            return self._authority.list_roots()
        if not self._allowed_paths:
            return "白名单为空"
        lines = ["=== 路径白名单 ==="]
        for i, p in enumerate(self._allowed_paths, 1):
            lines.append(f"  {i}. {p}")
        return '\n'.join(lines)

    def safe_path(self, path: str) -> Path:
        """
        Validate and resolve a path safely.

        When a WorkspaceAuthority is bound, delegates to the authority
        for unified permission checking.  Otherwise falls back to the
        legacy workdir + whitelist model.

        Args:
            path: Input path

        Returns:
            Resolved Path object

        Raises:
            ValueError: If path escapes the workspace
        """
        if self._authority:
            return self._authority.check(path)

        path_obj = (self.workdir / path).resolve()

        # Check primary workdir
        if _is_relative_to(path_obj, self.workdir):
            return path_obj

        # Check whitelist
        for allowed in self._allowed_paths:
            if _is_relative_to(path_obj, allowed):
                return path_obj

        raise ValueError(f"路径超出工作区白名单: {path}")

    # ── Core Tools ─────────────────────────────────────────────────

    def run_bash(self, command: str, timeout: int = 120,
                  cwd: Optional[Path] = None) -> ToolResult:
        """
        Run a shell command via ShellSession (unified execution engine).

        Args:
            command: The command to run
            timeout: Timeout in seconds
            cwd: Explicit working directory (overrides ShellSession cwd for
                 this call only).  Rarely needed — use ``cd`` inside command.

        Returns:
            ToolResult with output
        """
        # ── Command Policy (first line of defence) ──
        block_msg = _COMMAND_POLICY.check(command)
        if block_msg:
            return ToolResult(content=block_msg, success=False)

        # ── Delegate to ShellSession (single subprocess.run source) ──
        if self.shell_session is None:
            raise RuntimeError(
                "BaseTools.run_bash: ShellSession not injected — "
                "all callers must provide a shell_session instance."
            )

        cwd_override = cwd.resolve() if cwd is not None else None
        result = self.shell_session.execute(
            command, timeout=timeout, cwd_override=cwd_override,
        )
        segment_exit_codes = list(result.get("segment_exit_codes", []))
        display_content = result["content"]
        if any(code != 0 for code in segment_exit_codes):
            display_content = (
                "[Partial process failure detected: one or more command segments failed]\n"
                f"[Final shell exit code: {result.get('exit_code')}]\n"
                f"{display_content}"
            )
        formatted, visibility = self._format_tool_output_with_visibility(
            display_content,
            success=result["success"],
            exit_code=result.get("exit_code"),
        )
        if any(code != 0 for code in segment_exit_codes):
            visibility.update({
                "partial_process_failure": True,
                "segment_exit_codes": segment_exit_codes,
            })
        return ToolResult(
            content=formatted,
            success=result["success"],
            execution_success=result.get("execution_success", result["success"]),
            exit_code=result.get("exit_code"),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            timed_out=result.get("timed_out", False),
            cancelled=result.get("cancelled", False),
            segment_exit_codes=segment_exit_codes,
            output_visibility=visibility,
        )

    def format_tool_output(self, content: str, success: bool = True,
                           exit_code: Optional[int] = None) -> str:
        """Persist oversized tool output and return a bounded inspection window."""
        formatted, _ = self._format_tool_output_with_visibility(content, success, exit_code)
        return formatted

    def _format_tool_output_with_visibility(
        self, content: str, success: bool = True,
        exit_code: Optional[int] = None,
    ) -> tuple[str, Dict[str, Any]]:
        """Return model-visible text and metadata describing any omitted output."""
        output = content
        if content.startswith("[Exit Code: ") and "\n" in content:
            output = content.split("\n", 1)[1]
        lines = output.splitlines()
        total_lines = len(lines)
        total_chars = len(output)
        if total_lines <= READ_FILE_MAX_LINES and total_chars <= TOOL_OUTPUT_PREVIEW_CHARS:
            return content, {
                "truncated": False,
                "original_lines": total_lines,
                "original_chars": total_chars,
                "visible_chars": total_chars,
                "selected_line_ranges": [[1, total_lines]] if total_lines else [],
                "saved_path": "",
            }

        logs_dir = self.workdir.resolve() / ".agent" / "logs"
        log_name = f"cmd_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.log"
        log_path = logs_dir / log_name
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8", newline="") as log_file:
                log_file.write(output)
            saved_path = log_path.relative_to(self.workdir.resolve()).as_posix()
        except OSError as exc:
            logger.warning("长工具输出落盘失败: %s", exc)
            saved_path = f"<failed: {exc}>"

        code = exit_code if exit_code is not None else (0 if success else 1)
        head = _truncate_utf8(
            "\n".join(lines[:10]) or "(empty)",
            TOOL_OUTPUT_PREVIEW_CHARS,
            TOOL_OUTPUT_PREVIEW_CHARS * 4,
        )
        tail = _truncate_utf8(
            "\n".join(lines[-20:]) or "(empty)",
            TOOL_OUTPUT_PREVIEW_CHARS,
            TOOL_OUTPUT_PREVIEW_CHARS * 4,
        )
        formatted = (
            f"[Command executed with exit code {code}]\n"
            f"[Output is too long (Total {total_lines} lines / {total_chars} chars). "
            "Truncated for context efficiency.]\n"
            f"[Full output saved to: {saved_path}]\n\n"
            "--- Head (first 10 lines) ---\n"
            f"{head}\n\n"
            "--- Tail (last 20 lines) ---\n"
            f"{tail}\n\n"
            "[Tip]: Use `search_code` or `read_file` with line ranges on the saved "
            "log file to inspect specific errors or sections."
        )
        bounded = _bound_tool_output(
            formatted,
            "⚠️ 工具输出预览达到硬上限，已进一步截断；请使用保存的日志路径分段读取。",
        )
        head_end = min(10, total_lines)
        tail_start = max(1, total_lines - 19)
        selected_ranges = (
            [[1, total_lines]] if tail_start <= head_end + 1
            else [[1, head_end], [tail_start, total_lines]]
        )
        return bounded, {
            "truncated": True,
            "original_lines": total_lines,
            "original_chars": total_chars,
            "visible_chars": len(head) + len(tail),
            "selected_line_ranges": selected_ranges,
            "saved_path": saved_path if not saved_path.startswith("<failed:") else "",
        }

    @staticmethod
    def _read_file_window(file_path: Path, encoding: str,
                          start_line: int, end_line: int):
        """Stream a file while retaining only the requested bounded window.

        The whole file is still scanned to report its line count, but neither
        a full ``read()`` nor an unbounded line buffer is used.  Incremental
        decoding keeps UTF-8 validation behavior while limiting retained text
        from a single pathological line.
        """
        selected: List[str] = []
        total_lines = 0
        current_line = 1
        decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
        captured: List[str] = []
        captured_chars = 0
        window_chars = 0
        line_started = False
        selected_line_truncated = False
        window_truncated = False

        def feed(part: bytes) -> None:
            nonlocal captured_chars, window_chars, line_started
            nonlocal selected_line_truncated
            if part:
                line_started = True
            decoded = decoder.decode(part, final=False)
            if not (start_line <= current_line <= end_line):
                return
            remaining = READ_FILE_MAX_CHARS - window_chars - captured_chars
            if remaining > 0:
                piece = decoded[:remaining]
                captured.append(piece)
                captured_chars += len(piece)
            if len(decoded) > remaining:
                selected_line_truncated = True

        def finish_line() -> None:
            nonlocal total_lines, current_line, decoder, window_truncated
            nonlocal captured, captured_chars, window_chars, line_started
            nonlocal selected_line_truncated
            decoded_tail = decoder.decode(b'', final=True)
            if start_line <= current_line <= end_line:
                remaining = READ_FILE_MAX_CHARS - window_chars - captured_chars
                if remaining > 0:
                    captured.append(decoded_tail[:remaining])
                    captured_chars += min(len(decoded_tail), remaining)
                if len(decoded_tail) > remaining:
                    selected_line_truncated = True
                window_truncated = window_truncated or selected_line_truncated
                text = ''.join(captured)
                if text.endswith('\r'):
                    text = text[:-1]
                selected.append(text)
                window_chars += len(text)
            total_lines += 1
            current_line += 1
            decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
            captured = []
            captured_chars = 0
            line_started = False
            selected_line_truncated = False

        with open(file_path, 'rb') as file_handle:
            while True:
                chunk = file_handle.read(FILE_READ_CHUNK_BYTES)
                if not chunk:
                    break
                parts = chunk.split(b'\n')
                for index, part in enumerate(parts):
                    feed(part)
                    if index < len(parts) - 1:
                        finish_line()

        if line_started:
            finish_line()

        # The flag is meaningful only for the selected window.  The final
        # _limit_read_output pass remains the byte guard.
        body = '\n'.join(selected)
        return selected, total_lines, window_truncated

    def read_file(self, path: str, start_line: int = None,
                  end_line: int = None,
                  max_lines: int = READ_FILE_MAX_LINES) -> ToolResult:
        """
        Read a window of file content with anchor-comment delimiters.

        Pure code output (no per-line prefix) to maximise Prompt Cache
        stability — line numbers shift after edits, destroying cache hits.

        The result is always capped at the backend hard limits, including when
        ``end_line`` is explicitly provided, to prevent accidental token floods.

        Args:
            path: File path
            start_line: First line number to include (1-based, inclusive).
                        Defaults to 1 when omitted.
            end_line: Last line number to include (1-based, inclusive).
                      The hard line limit still applies.
            max_lines: Requested maximum lines, never allowed to exceed the
                       backend hard limit. Default 200.

        Returns:
            ToolResult with an anchor header/footer and clean code body.

        Raises:
            ValueError: If start_line > end_line (propagated via ToolResult).
        """
        try:
            file_path = self.safe_path(path)

            if not file_path.exists():
                return ToolResult(f"Error: File not found: {path}", success=False)

            if not file_path.is_file():
                return ToolResult(f"错误: 路径不是文件: {path}", success=False)

            # Resolve the bounded requested window before streaming.  The
            # final total line count is discovered during the same scan.
            start = 1 if start_line is None else max(1, int(start_line))
            line_limit = min(max(1, int(max_lines)), READ_FILE_MAX_LINES)
            requested_end_hint = (
                start + line_limit - 1
                if end_line is None
                else min(start + line_limit - 1, int(end_line))
            )

            # ── Read with encoding fallback ──────────────────────────
            try:
                lines, total_lines, window_truncated = self._read_file_window(
                    file_path, 'utf-8', start, requested_end_hint,
                )
            except UnicodeDecodeError:
                try:
                    lines, total_lines, window_truncated = self._read_file_window(
                        file_path, 'latin-1', start, requested_end_hint,
                    )
                except Exception as e:
                    return ToolResult(
                        f"读取文件时出错（编码问题）: {str(e)}", success=False,
                    )
            except Exception as e:
                logger.error(f"读取文件 {path} 出错: {e}")
                return ToolResult(f"错误: {str(e)}", success=False)

            requested_end = (
                total_lines if end_line is None
                else min(total_lines, int(end_line))
            )
            end = min(total_lines, requested_end, start + line_limit - 1)

            # Validation: start must not exceed end
            if start > end:
                return ToolResult(
                    f"错误: start_line ({start}) > end_line ({end})，"
                    f"行号范围非法。请检查传入的参数。",
                    success=False,
                )

            # The streaming reader already retained exactly this bounded
            # window; no full-file list or second slice is needed.
            chunk = lines[:end - start + 1]

            # Build anchor-delimited output (no per-line prefix)
            output_parts = [
                f"--- FILE: {path} (LINES: {start}-{end} of {total_lines}) ---",
            ]
            body = '\n'.join(chunk)
            limited_body = self._limit_read_output(body)
            output_parts.append(limited_body)

            line_truncated = end < requested_end
            char_truncated = window_truncated or len(limited_body) < len(body)
            if line_truncated or char_truncated:
                reasons = []
                if line_truncated:
                    reasons.append(f"行数上限为 {line_limit} 行")
                if char_truncated:
                    reasons.append(
                        f"字符/字节上限为 {READ_FILE_MAX_CHARS} 字符、{READ_FILE_MAX_BYTES} 字节"
                    )
                next_start = end + 1
                next_end = next_start + line_limit - 1
                output_parts.append(
                    f"... (内容被截断，实际返回第 {start}-{end} 行，文件共 {total_lines} 行；"
                    f"{ '；'.join(reasons) }。"
                    f"请使用 start_line={next_start}, end_line={next_end} 继续读取。)"
                )

            output_parts.append(f"--- END FILE: {path} ---")
            output = _bound_tool_output(
                '\n'.join(output_parts),
                "⚠️ read_file 输出达到硬上限，已截断；请使用提示中的行窗口继续读取。",
            )

            logger.debug(
                f"读取文件: {path} [{start}-{end}/{total_lines} 行] "
                f"({len(output)} 个字符)"
            )

            return ToolResult(output)

        except Exception as e:
            logger.error(f"读取文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

    @staticmethod
    def _limit_read_output(content: str) -> str:
        """Apply character and UTF-8 byte limits without splitting a codepoint."""
        return _truncate_utf8(content, READ_FILE_MAX_CHARS, READ_FILE_MAX_BYTES)

    def write_file(self, path: str, content: str) -> ToolResult:
        """
        Write content to file.

        Args:
            path: File path
            content: Content to write

        Returns:
            ToolResult with status
        """
        try:
            file_path = self.safe_path(path)

            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"已写入 {len(content)} 字节到 {path}")

            return ToolResult(f"成功写入 {len(content)} 字节到 {path}")

        except Exception as e:
            logger.error(f"写入文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

    def edit_file(self, path: str, edits: list) -> ToolResult:
        """
        Apply multiple search/replace edits atomically using a shadow buffer
        with reverse-offset ordering.

        Designed for **surgical local edits only** — creating new files or
        fully overwriting files must use ``write_file`` instead.

        Architecture:
        1. Read the original content once — the **reference copy** that
           all pre-validation runs against.
        2. Normalise ``\\r\\n`` → ``\\n`` so CRLF/LF differences never cause
           a spurious match failure.  The original line-ending style is
           restored before the final write.
        3. Pre-validate **every** edit against the **unchanged** reference
           copy, recording exact byte offsets and line numbers.
        4. Execute all replacements in **reverse offset order** (from the
           bottom of the file upward) so earlier edits never shift the
           positions needed by later ones.
        5. Write atomically via a temporary file + ``os.replace()``.

        Args:
            path: File path (must exist — use ``write_file`` for creation).
            edits: List of {"search": str, "replace": str, ...} dicts.
                   Each edit optionally accepts ``approx_line_start`` (int) —
                   an approximate 1-based line number that scopes the search
                   to a ±50-line window around the estimate, drastically
                   reducing false uniqueness failures in large files with
                   similar code blocks.

        Returns:
            ToolResult with a per-edit summary of every applied change.
        """
        try:
            file_path = self.safe_path(path)

            # edit_file is for surgical edits only — require existing file
            if not file_path.exists():
                return ToolResult(
                    f"错误: 文件不存在: {path}。新建文件请使用 write_file 工具。",
                    success=False,
                )

            # ── Phase 1: Load content ─────────────────────────────
            with open(file_path, 'r', encoding='utf-8') as f:
                working_content = f.read()

            # Detect and normalise line endings (CRLF → LF) so that
            # \r\n / \n mismatch never causes a spurious match failure.
            original_line_ending = "\r\n" if "\r\n" in working_content else "\n"
            working_content = working_content.replace("\r\n", "\n")

            # ── Phase 2: Pre-validate ALL edits against original ──
            prepared = []  # [{search, replace, offset, line_no}]

            for i, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    return ToolResult(
                        f"错误: 第 {i+1} 处编辑格式无效，应为 object",
                        success=False,
                    )
                search_raw = edit.get('search')
                replace_raw = edit.get('replace')
                if not search_raw:
                    return ToolResult(
                        f"错误: 第 {i+1} 处编辑缺少 'search' 或 search 为空。\n"
                        f"edit_file 只接受精确局部修改，需要提供具体代码片段。"
                        f"新建文件或全量覆盖请使用 write_file。",
                        success=False,
                    )
                if replace_raw is None:
                    return ToolResult(
                        f"错误: 第 {i+1} 处编辑缺少 'replace' 字段",
                        success=False,
                    )
                search = search_raw.replace("\r\n", "\n")
                replace = replace_raw.replace("\r\n", "\n")

                # ── Upper-bound guard ──────────────────────────────
                if len(search) > 2000 or len(replace) > 2000:
                    logger.warning(
                        f"[Harness 拦截] edit_file 载荷过大: "
                        f"search={len(search)}, replace={len(replace)}"
                    )
                    return ToolResult(
                        f"【系统安全拦截】第 {i+1} 处修改失败。\n"
                        f"你的 'search' 块 ({len(search)} 字符) 或 "
                        f"'replace' 块 ({len(replace)} 字符) 超出限制 "
                        f"(2000)！请将修改范围压缩至核心函数 "
                        f"（建议 50 行以内）后重新调用。\n"
                        f"当前事务已回滚，未做任何修改。",
                        success=False,
                    )

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
                    return ToolResult(
                        f"【Harness 事务拦截】第 {i+1} 处修改匹配失败。\n"
                        f"无法在文件中定位您的 'search' 片段，请重新使用 "
                        f"read_file 核对该段代码的精准缩进与换行符。{hint}\n"
                        f"当前文件已自动整体回滚，未做任何修改。",
                        success=False,
                    )
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
                    return ToolResult(
                        f"【Harness 事务拦截】第 {i+1} 处修改匹配到 {count} 处 "
                        f"{hint}。请提供更多上下文使 search 字符串在文件中唯一。\n"
                        f"当前文件已自动整体回滚，未做任何修改。",
                        success=False,
                    )

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

            # ── Phase 4: Atomic disk write via temp file ──────────
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                'w', dir=file_path.parent, delete=False, encoding='utf-8',
            ) as tf:
                tf.write(working_content)
                temp_name = tf.name

            try:
                os.replace(temp_name, str(file_path))
            except Exception:
                if os.path.exists(temp_name):
                    os.remove(temp_name)
                raise

            # ── Phase 5: Build response ────────────────────────────
            edit_summaries.reverse()  # restore original edit order
            summary_lines = [f"编辑成功 {path}，共完成 {len(edits)} 处修改:"]
            for idx, (s_p, ln, r_p) in enumerate(edit_summaries, 1):
                summary_lines.append(f"  {idx}. 第 {ln} 行: \"{s_p}\" → \"{r_p}\"")
            result = '\n'.join(summary_lines)

            logger.info(f"编辑成功: {path} ({len(edits)} 处修改)")
            return ToolResult(result)

        except Exception as e:
            logger.error(f"编辑文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

    def list_files(self, path: str = ".", max_depth: int = 2,
                    max_files: int = 200) -> ToolResult:
        """
        List files in directory with depth control and ignore rules.

        ``max_depth=0`` means non-recursive (current directory only).
        ``max_depth>=1`` recurses up to that many levels relative to ``path``.
        Hard-coded ignore list (``IGNORE_DIRS``) is always applied.

        Args:
            path:      Directory path to list.
            max_depth: Recursion depth relative to ``path`` (0 = non-recursive).
                       Default 2.
            max_files: Hard cap on entries returned.  Default 200.

        Returns:
            ToolResult with sorted file/directory listing.
        """
        try:
            dir_path = self.safe_path(path)

            if not dir_path.exists():
                return ToolResult(f"错误: 目录不存在: {path}", success=False)
            if not dir_path.is_dir():
                return ToolResult(f"错误: 路径不是目录: {path}", success=False)

            # ── Walk with depth tracking ────────────────────────────
            entries: List[str] = []
            truncated = False
            base_depth = len(dir_path.parents)

            for root, dirs, filenames in os.walk(dir_path):
                # Compute current depth
                cur_depth = len(Path(root).parents) - base_depth

                # Ignore directories: always skip blacklisted dirs
                dirs[:] = [d for d in dirs if d not in BaseTools.IGNORE_DIRS]

                if cur_depth > 0 and cur_depth > max_depth:
                    # Exceeds depth limit — stop recursion, no marker needed
                    # (subdirectory was already listed at the parent level)
                    dirs[:] = []
                    continue

                # Root level: list contents
                if cur_depth == 0:
                    # List subdirectories
                    for d in sorted(dirs):
                        if len(entries) >= max_files:
                            truncated = True
                            break
                        entries.append(f"  {d}/")
                    # List files
                    for fn in sorted(filenames):
                        if len(entries) >= max_files:
                            truncated = True
                            break
                        entries.append(f"  {fn}")
                else:
                    # Subdirectory: prefix with relative path
                    rel = Path(root).relative_to(dir_path)
                    for fn in sorted(filenames):
                        if len(entries) >= max_files:
                            truncated = True
                            break
                        entries.append(f"  {rel / fn}")

                    if truncated:
                        break

            result = "\n".join(entries)
            if truncated:
                result += f"\n---\n已截断至 {max_files} 条（完整结果需缩小范围）"

            logger.debug(f"已列出 {path} 中的 {len(entries)} 个文件")
            return ToolResult(result)

        except Exception as e:
            logger.error(f"列出文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

    # ── search_code implementation ────────────────────────────────────

    @staticmethod
    def _is_search_ignored(path: Path) -> bool:
        """Check if a path matches ignore rules (directories or extensions)."""
        if path.suffix in BaseTools.IGNORE_EXTS:
            return True
        for part in path.parts:
            if part in BaseTools.IGNORE_DIRS:
                return True
        return False

    @staticmethod
    def _is_binary_file(path: Path) -> bool:
        """Detect binary files via null-byte check in the first 8 KB."""
        try:
            with open(path, 'rb') as f:
                return b'\x00' in f.read(8192)
        except Exception:
            return True

    def _expand_search_paths(self, paths: List[str]) -> List[Path]:
        """Expand paths only after validating roots and every resolved candidate."""
        files: List[Path] = []
        seen: set = set()

        for p in paths:
            explicit_agent = self._explicit_agent_path(p)

            if any(c in p for c in '*?['):
                normalized = p.replace('\\', '/')
                pattern_path = Path(normalized)
                if not pattern_path.is_absolute():
                    pattern_path = self.workdir / pattern_path
                parts = pattern_path.parts
                wildcard_index = next(
                    (i for i, part in enumerate(parts) if any(c in part for c in '*?[')),
                    len(parts),
                )
                if wildcard_index == len(parts):
                    continue
                anchor = Path(*parts[:wildcard_index])
                if not anchor.parts:
                    anchor = self.workdir
                pattern = Path(*parts[wildcard_index:]).as_posix()
                # The fixed prefix itself must be authorized before globbing.
                safe_anchor = self.safe_path(str(anchor))
                matches = sorted(
                    safe_anchor.glob(pattern), key=lambda path: path.as_posix()
                )
                for match in matches:
                    if not match.is_file():
                        continue
                    try:
                        resolved = self.safe_path(str(match))
                    except ValueError:
                        # A glob may encounter an in-workspace symlink that
                        # points out. Never read its target.
                        continue
                    if resolved in seen or self._is_search_ignored(resolved):
                        continue
                    if not explicit_agent and self._is_agent_runtime_path(resolved):
                        continue
                    files.append(resolved)
                    seen.add(resolved)
                continue

            resolved = self.safe_path(p)
            if resolved in seen:
                continue

            if resolved.is_dir():
                for root, dirs, filenames in os.walk(resolved):
                    dirs[:] = sorted(
                        d for d in dirs
                        if d not in BaseTools.IGNORE_DIRS
                        and (explicit_agent or d != ".agent")
                    )
                    for fn in sorted(filenames):
                        fp = Path(root) / fn
                        if self._is_search_ignored(fp):
                            continue
                        if not explicit_agent and self._is_agent_runtime_path(fp):
                            continue
                        try:
                            resolved_file = self.safe_path(str(fp))
                        except ValueError:
                            # Ignore an escaped symlink encountered during a
                            # broad walk; explicit paths still fail closed.
                            continue
                        if resolved_file.is_file() and resolved_file not in seen:
                            files.append(resolved_file)
                            seen.add(resolved_file)
            elif resolved.is_file():
                if (not self._is_search_ignored(resolved)
                        and (explicit_agent or not self._is_agent_runtime_path(resolved))):
                    files.append(resolved)
                    seen.add(resolved)

        return files

    def _explicit_agent_path(self, path: str) -> bool:
        normalized = path.replace('\\', '/').split('/')
        return ".agent" in normalized

    def _is_agent_runtime_path(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.workdir.resolve())
        except ValueError:
            return False
        return ".agent" in relative.parts

    def search_code(
        self,
        paths: Optional[List[str]] = None,
        patterns: List[str] = None,
        context_lines: int = 0,
        case_sensitive: bool = False,
        max_matches: int = 50,
        include_filename: bool = True,
        include_line_number: bool = True,
    ) -> ToolResult:
        """Return matching lines for line-oriented regex searches.

        Multiple patterns use OR semantics. Patterns are applied separately
        to each line, so matches cannot span newline boundaries. Context blocks
        are emitted atomically to keep bounded output from showing partial
        code regions as if they were complete.
        """
        if not paths:
            paths = ["."]
        if not patterns:
            return ToolResult("Error: at least one pattern is required.", success=False)

        try:
            context_lines = min(
                max(0, int(context_lines)), SEARCH_CODE_MAX_CONTEXT_LINES,
            )
            max_matches = max(1, min(SEARCH_CODE_MAX_MATCHES, int(max_matches)))
        except (ValueError, TypeError):
            return ToolResult("Error: numeric search options are invalid.", success=False)

        # ── Compile regexes ───────────────────────────────────────
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled: list = []
        for pat in patterns:
            try:
                compiled.append(re.compile(pat, flags))
            except re.error as e:
                return ToolResult(f"Error: invalid regular expression {pat!r}: {e}", success=False)

        # ── Expand paths ──────────────────────────────────────────
        try:
            search_files = self._expand_search_paths(paths)
        except ValueError as e:
            return ToolResult(str(e), success=False)

        if not search_files:
            return ToolResult(
                f"No searchable files found under paths {paths!r}.", success=False,
            )

        # Each entry is one indivisible display block and the matching-line
        # count it represents. Context blocks can contain several hits.
        blocks: List[tuple[str, int]] = []
        matched_lines = 0
        searched_files = 0
        incomplete_reasons: set[str] = set()
        skipped_files: List[Dict[str, str]] = []
        skipped_file_count = 0
        line_preview_clipped = False

        def skip_file(path: Path, reason: str) -> None:
            nonlocal skipped_file_count
            skipped_file_count += 1
            if len(skipped_files) < 20:
                skipped_files.append({
                    "path": str(path)[:240], "reason": reason,
                })

        for file_path in search_files:
            try:
                if file_path.stat().st_size > BaseTools.MAX_FILE_SIZE:
                    skip_file(file_path, "file_size_limit")
                    continue
            except OSError:
                skip_file(file_path, "stat_error")
                continue

            if BaseTools._is_binary_file(file_path):
                skip_file(file_path, "binary_file")
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='latin-1') as f:
                        lines = f.readlines()
                except Exception:
                    skip_file(file_path, "decode_error")
                    continue
            except Exception:
                skip_file(file_path, "read_error")
                continue

            searched_files += 1
            file_matches: List[tuple[int, str]] = []
            for i, line in enumerate(lines):
                text = line.rstrip('\n\r')
                for cp in compiled:
                    if cp.search(text):
                        file_matches.append((i, text))
                        break

            if not file_matches:
                continue

            if context_lines == 0:
                for line_idx, matched_text in file_matches:
                    if matched_lines >= max_matches:
                        incomplete_reasons.add("max_matches")
                        break
                    matched_lines += 1
                    if len(matched_text) > 200:
                        line_preview_clipped = True
                    display = matched_text[:200] + ("... [line preview clipped]" if len(matched_text) > 200 else "")
                    prefix = f"{line_idx + 1}: " if include_line_number else ""
                    rendered_line = f"> {prefix}{display}"
                    rendered = f"{file_path}\n{rendered_line}" if include_filename else rendered_line
                    blocks.append((rendered, 1))
                if "max_matches" in incomplete_reasons:
                    break
                continue

            intervals = []
            for idx, _ in file_matches:
                start = max(0, idx - context_lines)
                end = min(len(lines) - 1, idx + context_lines)
                intervals.append([start, end, idx])

            merged_blocks = []
            if intervals:
                intervals.sort(key=lambda x: x[0])
                current_block = {
                    'start': intervals[0][0], 'end': intervals[0][1],
                    'hits': {intervals[0][2]},
                }
                for next_int in intervals[1:]:
                    if next_int[0] <= current_block['end'] + 1:
                        current_block['end'] = max(current_block['end'],
                                                    next_int[1])
                        current_block['hits'].add(next_int[2])
                    else:
                        merged_blocks.append(current_block)
                        current_block = {
                            'start': next_int[0], 'end': next_int[1],
                            'hits': {next_int[2]},
                        }
                merged_blocks.append(current_block)

            file_header_printed = False
            for block in merged_blocks:
                hit_count = len(block['hits'])
                if matched_lines + hit_count > max_matches:
                    incomplete_reasons.add("max_matches")
                    break

                block_lines = [str(file_path)] if include_filename and not file_header_printed else []
                file_header_printed = file_header_printed or include_filename
                for ci in range(block['start'], block['end'] + 1):
                    raw = lines[ci].rstrip('\n\r')
                    if len(raw) > 200:
                        line_preview_clipped = True
                        raw = raw[:200] + "... [line preview clipped]"

                    line_prefix = f"{ci + 1}: " if include_line_number else ""
                    marker = "> " if ci in block['hits'] else "  "
                    block_lines.append(f"{marker}{line_prefix}{raw}")
                matched_lines += hit_count
                blocks.append(("\n".join(block_lines), hit_count))
            if "max_matches" in incomplete_reasons:
                break

        def render() -> str:
            visible_matches = sum(count for _, count in blocks)
            header = (f"Search complete: {visible_matches} matching lines across "
                      f"{searched_files} searched files.")
            if incomplete_reasons:
                header = (f"Search incomplete: returned {visible_matches} matching lines "
                          f"from {searched_files} searched files.")
            parts = [header]
            if blocks:
                parts.append("\n\n".join(text for text, _ in blocks))
            if incomplete_reasons or skipped_file_count:
                parts.append("Search visibility:")
                parts.append(f"- truncated_or_incomplete: {bool(incomplete_reasons or skipped_file_count)}")
                if incomplete_reasons:
                    parts.append(f"- reasons: {', '.join(sorted(incomplete_reasons))}")
                    if "max_matches" in incomplete_reasons or "output_budget" in incomplete_reasons:
                        omitted_lower_bound = max(
                            1 if "max_matches" in incomplete_reasons else 0,
                            candidate_matches - visible_matches,
                        )
                        parts.append(
                            f"- additional_matching_lines_omitted: at least {omitted_lower_bound}"
                        )
                if skipped_file_count:
                    parts.append(f"- skipped_files: {skipped_file_count}")
                    for item in skipped_files:
                        parts.append(f"  - {item['path']} ({item['reason']})")
                    omitted = skipped_file_count - len(skipped_files)
                    if omitted:
                        parts.append(f"  - {omitted} additional skipped files omitted")
            return "\n".join(parts)

        candidate_matches = sum(count for _, count in blocks)
        if line_preview_clipped:
            incomplete_reasons.add("line_preview")
        if skipped_file_count:
            incomplete_reasons.add("skipped_files")
        output_text = render()
        while (len(output_text) > TOOL_OUTPUT_MAX_CHARS
               or len(output_text.encode('utf-8')) > TOOL_OUTPUT_MAX_BYTES):
            if not blocks:
                # The summary/footer is bounded independently below.
                output_text = output_text[:TOOL_OUTPUT_MAX_CHARS]
                incomplete_reasons.add("output_budget")
                break
            blocks.pop()  # Drop a complete line/context block, never a fragment.
            incomplete_reasons.add("output_budget")
            output_text = render()

        visible_matches = sum(count for _, count in blocks)
        omitted_match_lower_bound = max(
            1 if "max_matches" in incomplete_reasons else 0,
            candidate_matches - visible_matches,
        )
        output_visibility = {
            "truncated": bool(incomplete_reasons or skipped_file_count),
            "truncation_reasons": sorted(incomplete_reasons),
            "returned_matching_lines": visible_matches,
            "additional_matching_lines_omitted_lower_bound": omitted_match_lower_bound,
            "searched_files": searched_files,
            "max_matches": max_matches,
            "skipped_files": skipped_files,
            "skipped_file_count": skipped_file_count,
            "skipped_files_omitted": max(0, skipped_file_count - len(skipped_files)),
        }
        return ToolResult(output_text, output_visibility=output_visibility)

    # ── count_occurrences ────────────────────────────────────────────

    def count_occurrences(
        self,
        paths: Optional[List[str]] = None,
        patterns: List[str] = None,
        case_sensitive: bool = False,
    ) -> ToolResult:
        """Count occurrences of regex patterns across files (compact output).

        Reuses ``_expand_search_paths`` for file traversal.  Returns per-pattern
        totals and per-file breakdowns — no matching lines, just counts.

        Typical output::

            Pattern "user_id": 10 matches across 3 files
              src/main.py: 6
              src/utils.py: 3
              src/models.py: 1
            Pattern "uid": 0 matches
        """
        if not paths:
            paths = ["."]
        if not patterns:
            return ToolResult("错误: 需要至少提供一个模式 (patterns)", success=False)

        flags = 0 if case_sensitive else re.IGNORECASE
        compiled: list = []
        for pat in patterns:
            try:
                compiled.append(re.compile(pat, flags))
            except re.error as e:
                return ToolResult(f"错误: 无效的正则表达式 '{pat}': {e}", success=False)

        try:
            search_files = self._expand_search_paths(paths)
        except ValueError as e:
            return ToolResult(str(e), success=False)

        if not search_files:
            return ToolResult(
                f"在路径 {paths} 中未找到可搜索的文件", success=False,
            )

        # ── Per-pattern aggregation ──────────────────────────────────
        # result[pattern_str] = {"total": int, "files": {path_str: count}}
        result: dict = {}
        for pat_str in patterns:
            result[pat_str] = {"total": 0, "files": {}}

        for file_path in search_files:
            try:
                if file_path.stat().st_size > BaseTools.MAX_FILE_SIZE:
                    continue
            except OSError:
                continue
            if BaseTools._is_binary_file(file_path):
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except (UnicodeDecodeError, OSError):
                continue

            for pat_str, cp in zip(patterns, compiled):
                # Python regex iteration counts non-overlapping occurrences.
                count = sum(1 for _ in cp.finditer(content))
                if count:
                    result[pat_str]["total"] += count
                    fpath_str = str(file_path)
                    result[pat_str]["files"][fpath_str] = (
                        result[pat_str]["files"].get(fpath_str, 0) + count
                    )

        # ── Build compact output ─────────────────────────────────────
        lines: List[str] = []
        for pat_str in patterns:
            info = result[pat_str]
            if info["total"] == 0:
                lines.append(f'Pattern "{pat_str}": 0 occurrences')
            else:
                file_count = len(info["files"])
                lines.append(
                    f'Pattern "{pat_str}": {info["total"]} occurrences '
                    f"across {file_count} file(s)"
                )
                for fpath in sorted(info["files"]):
                    lines.append(f"  {fpath}: {info['files'][fpath]}")
            lines.append("---")

        # Strip trailing ---
        if lines:
            lines.pop()

        return ToolResult("\n".join(lines))

    # ── syntax_check ─────────────────────────────────────────────────
    # (Commented out — LLM should use language-native tools instead,
    #  e.g. python -c "import ast; ast.parse(open('f.py').read())"
    #  / javac File.java / npx tsc --noEmit / go vet / cargo check)

    # def syntax_check(self, paths: List[str]) -> ToolResult:
    #     """Check Python source files for syntax errors via ``ast.parse``.
    #
    #     Only ``.py`` files are inspected.  Non-Python files are silently
    #     skipped.  Returns a compact pass/fail report with per-file errors.
    #
    #     Typical output (success)::
    #
    #         Syntax check: 5 files checked, 0 errors
    #
    #     Typical output (failure)::
    #
    #         Syntax check: 3 files checked, 1 error
    #           src/broken.py:42 - unmatched ')'
    #     """
    #     if not paths:
    #         return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)
    #
    #     import ast
    #
    #     try:
    #         search_files = self._expand_search_paths(paths)
    #     except ValueError as e:
    #         return ToolResult(str(e), success=False)
    #
    #     # ── Filter for .py files only ────────────────────────────────
    #     py_files = [f for f in search_files if f.suffix == ".py"]
    #
    #     if not py_files:
    #         return ToolResult("没有找到 Python 文件", success=False)
    #
    #     errors: List[Dict[str, Any]] = []
    #
    #     for file_path in py_files:
    #         try:
    #             with open(file_path, 'r', encoding='utf-8') as f:
    #                 source = f.read()
    #             ast.parse(source, filename=str(file_path))
    #         except SyntaxError as e:
    #             errors.append({
    #                 "file": str(file_path),
    #                 "line": e.lineno or 0,
    #                 "message": e.msg,
    #             })
    #         except UnicodeDecodeError:
    #             continue
    #         except Exception as e:
    #             errors.append({
    #                 "file": str(file_path),
    #                 "line": 0,
    #                 "message": str(e),
    #             })
    #
    #     checked = len(py_files)
    #     if not errors:
    #         return ToolResult(
    #             f"Syntax check: {checked} file(s) checked, 0 errors",
    #             success=True,
    #         )
    #
    #     err_lines: List[str] = [
    #         f"Syntax check: {checked} file(s) checked, {len(errors)} error(s)",
    #     ]
    #     for err in errors:
    #         err_lines.append(
    #             f"  {err['file']}:{err['line']} - {err['message']}"
    #         )
    #
    #     return ToolResult("\n".join(err_lines), success=False)
