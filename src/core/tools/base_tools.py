import os
import ast
import re
import tempfile
import logging
from pathlib import Path
from typing import Optional, List, TYPE_CHECKING
from dataclasses import dataclass

from core.runtime_context.command_policy import CommandPolicy
from cli.authority import WorkspaceAuthority

if TYPE_CHECKING:
    from core.runtime_context.shell_session import ShellSession

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    content: str
    success: bool = True


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
        return ToolResult(
            content=result["content"],
            success=result["success"],
        )

    @staticmethod
    def _read_lines(file_path: Path, encoding: str):
        """Read file with the given encoding and return (lines_list, total_lines).

        Raises UnicodeDecodeError when the encoding cannot decode the file.
        """
        with open(file_path, 'r', encoding=encoding) as f:
            lines = f.read().splitlines(keepends=False)
        return lines, len(lines)

    def read_file(self, path: str, start_line: int = None,
                  end_line: int = None, max_lines: int = 200) -> ToolResult:
        """
        Read a window of file content with anchor-comment delimiters.

        Pure code output (no per-line prefix) to maximise Prompt Cache
        stability — line numbers shift after edits, destroying cache hits.

        When ``end_line`` is not provided the result is capped at
        ``start_line + max_lines - 1`` to prevent accidental token floods.
        Pass ``end_line`` explicitly to override this limit.

        Args:
            path: File path
            start_line: First line number to include (1-based, inclusive).
                        Defaults to 1 when omitted.
            end_line: Last line number to include (1-based, inclusive).
                      Defaults to total line count when omitted
                      (soft-capped by ``max_lines``).
            max_lines: Maximum lines to return when ``end_line`` is omitted.
                       Default 200. Ignored when ``end_line`` is explicit.

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

            # ── Read with encoding fallback ──────────────────────────
            try:
                lines, total_lines = self._read_lines(file_path, 'utf-8')
            except UnicodeDecodeError:
                try:
                    lines, total_lines = self._read_lines(file_path, 'latin-1')
                except Exception as e:
                    return ToolResult(
                        f"读取文件时出错（编码问题）: {str(e)}", success=False,
                    )
            except Exception as e:
                logger.error(f"读取文件 {path} 出错: {e}")
                return ToolResult(f"错误: {str(e)}", success=False)

            # Resolve slice boundaries (1-based inclusive)
            start = 1 if start_line is None else max(1, int(start_line))

            if end_line is not None:
                end = min(total_lines, int(end_line))
            else:
                end = min(total_lines, start + max_lines - 1)

            # Validation: start must not exceed end
            if start > end:
                return ToolResult(
                    f"错误: start_line ({start}) > end_line ({end})，"
                    f"行号范围非法。请检查传入的参数。",
                    success=False,
                )

            # Slice the line list (convert 1-based → 0-based indexing)
            chunk = lines[start - 1:end]

            # Build anchor-delimited output (no per-line prefix)
            output_parts = [
                f"--- FILE: {path} (LINES: {start}-{end} of {total_lines}) ---",
            ]
            output_parts.append('\n'.join(chunk))

            # Truncation notice when max_lines capped an un-specified end
            if end_line is None and end < total_lines:
                output_parts.append(
                    f"... (内容被截断，仅显示 {start}-{end} 行，共 {total_lines} 行。"
                    f"请指定 end_line 继续读取)"
                )

            output_parts.append(f"--- END FILE: {path} ---")
            output = '\n'.join(output_parts)

            logger.debug(
                f"读取文件: {path} [{start}-{end}/{total_lines} 行] "
                f"({len(output)} 个字符)"
            )

            return ToolResult(output)

        except Exception as e:
            logger.error(f"读取文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

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
        """Expand user path patterns into a deduplicated list of resolved file paths.

        Supports three forms:
          - Glob pattern (contains ``*``, ``?``, or ``[``) → Path.glob()
          - Directory path                              → os.walk()
          - Single file path                            → direct inclusion
        """
        files: List[Path] = []
        seen: set = set()

        for p in paths:
            # ── Glob pattern ──────────────────────────────────────
            if any(c in p for c in '*?['):
                matched_any = False
                for match in self.workdir.glob(p.replace('\\', '/')):
                    if not match.is_file():
                        continue
                    resolved = (self.workdir / match).resolve()
                    if resolved in seen:
                        continue
                    if not _is_relative_to(resolved, self.workdir):
                        continue
                    if not self._is_search_ignored(resolved):
                        files.append(resolved)
                        seen.add(resolved)
                    matched_any = True
                if not matched_any:
                    logger.warning(f"glob 模式 '{p}' 在 {self.workdir} 中未匹配到任何文件")
                continue

            resolved = (self.workdir / p).resolve()
            if resolved in seen:
                continue

            # ── Directory → recursive walk ────────────────────────
            if resolved.is_dir():
                for root, dirs, filenames in os.walk(resolved):
                    dirs[:] = [d for d in dirs if d not in BaseTools.IGNORE_DIRS]
                    for fn in filenames:
                        fp = Path(root) / fn
                        if fp.suffix in BaseTools.IGNORE_EXTS:
                            continue
                        if fp not in seen:
                            files.append(fp)
                            seen.add(fp)
            # ── Single file ──────────────────────────────────────
            elif resolved.is_file():
                if not self._is_search_ignored(resolved):
                    files.append(resolved)
                    seen.add(resolved)

        return files

    def search_code(
        self,
        paths: List[str],
        patterns: List[str],
        context_lines: int = 0,
        case_sensitive: bool = False,
        max_matches: int = 50,
        include_filename: bool = True,
        include_line_number: bool = True,
    ) -> ToolResult:
        """Search files for regex patterns (pure-Python, cross-platform).

        Args:
            paths:         File/directory/glob path specifiers.
            patterns:      Regex patterns (OR logic — a line matching any
                           one of them is a hit).
            context_lines: Lines of context shown before & after each match
                           (like ``grep -C``).  0 = no context.
            case_sensitive: Whether matching is case-sensitive.
            max_matches:   Hard cap on total matches returned.
            include_filename, include_line_number:
                           Control the ``file:line:`` prefix on output lines.

        Returns:
            ToolResult with a human-readable match report.
        """
        # ── Validate inputs ───────────────────────────────────────
        if not paths:
            return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)
        if not patterns:
            return ToolResult("错误: 需要至少提供一个模式 (patterns)", success=False)

        try:
            context_lines = max(0, int(context_lines))
            max_matches = max(1, int(max_matches))
        except (ValueError, TypeError):
            return ToolResult("错误: 数值参数无效", success=False)

        # ── Compile regexes ───────────────────────────────────────
        flags = 0 if case_sensitive else re.IGNORECASE
        compiled: list = []
        for pat in patterns:
            try:
                compiled.append(re.compile(pat, flags))
            except re.error as e:
                return ToolResult(f"错误: 无效的正则表达式 '{pat}': {e}", success=False)

        # ── Expand paths ──────────────────────────────────────────
        try:
            search_files = self._expand_search_paths(paths)
        except ValueError as e:
            return ToolResult(str(e), success=False)

        if not search_files:
            return ToolResult(
                f"在路径 {paths} 中未找到可搜索的文件", success=False,
            )

        # ── Search ────────────────────────────────────────────────
        total_matches = 0
        truncated = False
        output_lines: List[str] = []
        file_warnings: List[str] = []

        for file_path in search_files:
            if total_matches >= max_matches:
                truncated = True
                break

            # ── Size gate ─────────────────────────────────────────
            try:
                if file_path.stat().st_size > BaseTools.MAX_FILE_SIZE:
                    file_warnings.append(
                        f"跳过 {file_path} (>{BaseTools.MAX_FILE_SIZE // 1024 // 1024}MB)"
                    )
                    continue
            except OSError as e:
                file_warnings.append(str(e))
                continue

            # ── Binary gate ───────────────────────────────────────
            if BaseTools._is_binary_file(file_path):
                continue

            # ── Read ──────────────────────────────────────────────
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='latin-1') as f:
                        lines = f.readlines()
                except Exception as e:
                    file_warnings.append(f"无法读取 {file_path}: {e}")
                    continue
            except Exception as e:
                file_warnings.append(f"无法读取 {file_path}: {e}")
                continue

            # ── Locate all matching lines ─────────────────────────
            file_matches: list = []
            for i, line in enumerate(lines):
                text = line.rstrip('\n\r')
                for cp in compiled:
                    if cp.search(text):
                        file_matches.append((i, text))
                        break

            # ── Emit with context ─────────────────────────────────
            for mi, (line_idx, matched_text) in enumerate(file_matches):
                if total_matches >= max_matches:
                    truncated = True
                    break

                # Group separator
                if context_lines > 0 and mi > 0:
                    output_lines.append("---")

                if context_lines > 0:
                    start = max(0, line_idx - context_lines)
                    end = min(len(lines), line_idx + context_lines + 1)

                    for ci in range(start, end):
                        if total_matches >= max_matches:
                            truncated = True
                            break

                        raw = lines[ci].rstrip('\n\r')
                        if len(raw) > 200:
                            raw = raw[:200] + "..."

                        prefix = ""
                        if include_filename:
                            prefix += str(file_path)
                        if include_line_number:
                            prefix += f":{ci + 1}" if prefix else str(ci + 1)

                        marker = ">" if ci == line_idx else ""
                        if prefix:
                            output_lines.append(f"{prefix}:{marker}{raw}")
                        else:
                            output_lines.append(f"{marker}{raw}")

                        if ci == line_idx:
                            total_matches += 1
                else:
                    # Compact one-line-per-match output
                    prefix = ""
                    if include_filename:
                        prefix += str(file_path)
                    if include_line_number:
                        prefix += f":{line_idx + 1}" if prefix else str(line_idx + 1)

                    display = matched_text
                    if len(display) > 200:
                        display = display[:200] + "..."

                    out = f"{prefix}:{display}" if prefix else display
                    output_lines.append(out)
                    total_matches += 1

        # ── Build result text ─────────────────────────────────────
        result_parts: List[str] = []

        if total_matches == 0:
            result_parts.append(f"未找到匹配: {patterns}")
        else:
            status = f" (已截断至 {max_matches} 条)" if truncated else ""
            result_parts.append(
                f"找到 {total_matches} 处匹配{status}: {patterns}"
            )
            if truncated:
                result_parts.append(f"前 {max_matches} 条匹配:")
            result_parts.extend(output_lines)
            if truncated:
                result_parts.append("")
                result_parts.append(
                    "提示: 使用更精确的模式或更少的文件来缩小搜索范围"
                )

        if file_warnings:
            result_parts.append("")
            result_parts.append("警告:")
            result_parts.extend(file_warnings)

        result_parts.append(
            ""
            "[Tip: Use search_code for lightweight pattern searches; "
            "write scripts only if necessary]"
        )

        return ToolResult("\n".join(result_parts))

    # ── count_occurrences ────────────────────────────────────────────

    def count_occurrences(
        self,
        paths: List[str],
        patterns: List[str],
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
            return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)
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
                # re.findall returns all matches (including overlapping? no — non-overlapping)
                # Use finditer for correctness with overlapping patterns
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
                lines.append(f'Pattern "{pat_str}": 0 matches')
            else:
                file_count = len(info["files"])
                lines.append(
                    f'Pattern "{pat_str}": {info["total"]} matches '
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
