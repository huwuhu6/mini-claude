import os
import ast
import json
import re
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from core.runtime_context.command_policy import CommandPolicy
from cli.authority import WorkspaceAuthority

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    content: str
    success: bool = True


class _SymbolFinder(ast.NodeVisitor):
    """AST visitor that collects identifier nodes matching ``old_symbols``.

    Only finds **real identifier usages** — comments, docstrings, and string
    literals are naturally excluded because the Python parser strips them.
    """

    def __init__(self, old_symbols):
        self.old_symbols = set(old_symbols)
        self.found: list = []  # [(symbol_str, lineno)]

    def visit_Name(self, node):
        if node.id in self.old_symbols:
            self.found.append((node.id, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in self.old_symbols:
            self.found.append((node.attr, node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        if node.name in self.old_symbols:
            self.found.append((node.name, node.lineno))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        if node.name in self.old_symbols:
            self.found.append((node.name, node.lineno))
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        if node.name in self.old_symbols:
            self.found.append((node.name, node.lineno))
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            name = alias.name.split(".")[0]
            if name in self.old_symbols:
                ln = getattr(alias, "lineno", node.lineno)
                self.found.append((name, ln))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            name = alias.name.split(".")[0]
            if name in self.old_symbols:
                ln = getattr(alias, "lineno", node.lineno)
                self.found.append((name, ln))
        self.generic_visit(node)


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

    def __init__(self, workdir: Path, authority: Optional[WorkspaceAuthority] = None):
        self.workdir = workdir
        self._allowed_paths: List[Path] = []  # Path whitelist
        self._authority = authority
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
        Run a bash command safely.

        Args:
            command: The command to run
            timeout: Timeout in seconds
            cwd: Working directory (defaults to self.workdir)

        Returns:
            ToolResult with output
        """
        # ── Command Policy (replaces blanket shell control char ban) ──
        block_msg = _COMMAND_POLICY.check(command)
        if block_msg:
            return ToolResult(content=block_msg, success=False)

        effective_cwd = cwd or self.workdir

        try:
            logger.debug(f"正在执行命令: {command[:100]}...")
            # 移除 text=True，捕获二进制流以处理 Windows 的 GBK 编码问题
            r = subprocess.run(command, shell=True, cwd=str(effective_cwd),
                               capture_output=True, timeout=timeout)

            raw_output = r.stdout + r.stderr
            try:
                output_str = raw_output.decode('utf-8')
            except UnicodeDecodeError:
                output_str = raw_output.decode('gbk', errors='replace')

            out = output_str.strip()

            # Truncate output if too long
            if len(out) > 50000:
                out = out[:50000] + f"\n... (已截断，剩余 {len(out) - 50000} 个字符)"

            # 必须返回 Exit Code，防止模型忽略 CMD 的静默失败
            result = f"[Exit Code: {r.returncode}]\n"
            if out:
                result += out
            else:
                result += "(Command executed silently with no output or errors.)"

            logger.debug(f"命令执行完成，返回码: {r.returncode}")

            return ToolResult(
                content=result,
                success=r.returncode == 0
            )

        except subprocess.TimeoutExpired:
            logger.warning(f"命令超时已超过 {timeout} 秒")
            return ToolResult(
                content=f"错误: 执行超时（{timeout} 秒）",
                success=False
            )
        except Exception as e:
            logger.error(f"执行命令出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

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
        Apply multiple search/replace edits atomically using a shadow buffer.

        Two-phase commit:
          1. Verify ALL edits against the in-memory shadow buffer first.
          2. Only on full success write to disk once.

        Each edit requires the ``search`` string to appear exactly once
        in the file — duplicate or missing matches abort the entire
        transaction.

        Args:
            path: File path
            edits: List of {"search": str, "replace": str} dicts

        Returns:
            ToolResult with a per-edit summary of every applied change.
        """
        try:
            file_path = self.safe_path(path)

            if not file_path.exists():
                return ToolResult(f"错误: 文件不存在: {path}", success=False)

            # ── Phase 1: Load shadow buffer ──────────────────────────
            with open(file_path, 'r', encoding='utf-8') as f:
                working_content = f.read()

            edit_summaries = []  # [(search_preview, line_no, replace_preview)]

            for i, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    return ToolResult(
                        f"错误: 第 {i+1} 处编辑格式无效，应为 object",
                        success=False,
                    )
                search = edit.get('search')
                replace = edit.get('replace')
                if search is None or replace is None:
                    return ToolResult(
                        f"错误: 第 {i+1} 处编辑缺少 'search' 或 'replace' 字段",
                        success=False,
                    )

                # ── Lower-bound guard (context sufficiency) ─────────────
                if len(search) < 15:
                    return ToolResult(
                        f"【系统安全拦截】第 {i+1} 处修改失败。\n"
                        f"search 块过短（{len(search)} 字符，要求至少 15），"
                        f"请包含足够的上下文代码以保证匹配的唯一性。\n"
                        f"当前事务已回滚，未做任何修改。",
                        success=False,
                    )

                # ── Payload Size Circuit Breaker ──────────────────────
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

                # ── Uniqueness check (critical for correctness) ───────
                count = working_content.count(search)
                if count == 0:
                    return ToolResult(
                        f"【Harness 事务拦截】第 {i+1} 处修改匹配失败。\n"
                        f"无法在文件中定位您的 'search' 片段，请重新使用 "
                        f"read_file 核对该段代码的精准缩进与换行符。\n"
                        f"当前文件已自动整体回滚，未做任何修改。",
                        success=False,
                    )
                if count > 1:
                    offset = working_content.find(search)
                    example_line = working_content[:offset].count('\n') + 1
                    return ToolResult(
                        f"【Harness 事务拦截】第 {i+1} 处修改匹配到 {count} 处 "
                        f"（例如第 {example_line} 行附近）。请提供更多上下文使 "
                        f"search 字符串在文件中唯一。\n"
                        f"当前文件已自动整体回滚，未做任何修改。",
                        success=False,
                    )

                # ── Exactly one match — safe to replace ───────────────
                offset = working_content.find(search)
                line_num = working_content[:offset].count('\n') + 1
                working_content = working_content.replace(search, replace, 1)

                # Capture preview for success summary
                s_preview = search[:50].replace('\n', ' ')
                r_preview = replace[:50].replace('\n', ' ')
                edit_summaries.append((s_preview, line_num, r_preview))

            # ── Phase 2: Commit — single disk write ──────────────────
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(working_content)

            # Build rich per-edit success summary
            summary_lines = [f"成功编辑 {path}，共完成 {len(edits)} 处修改:"]
            for idx, (s_p, ln, r_p) in enumerate(edit_summaries, 1):
                summary_lines.append(f"  {idx}. 第 {ln} 行: \"{s_p}\" → \"{r_p}\"")
            result = '\n'.join(summary_lines)

            logger.info(f"已编辑文件: {path} ({len(edits)} 处修改)")
            return ToolResult(result)

        except Exception as e:
            logger.error(f"编辑文件 {path} 出错: {e}")
            return ToolResult(f"错误: {str(e)}", success=False)

    def list_files(self, path: str = ".", recursive: bool = False) -> ToolResult:
        """
        List files in directory.

        Args:
            path: Directory path
            recursive: Whether to list recursively

        Returns:
            ToolResult with file list
        """
        try:
            dir_path = self.safe_path(path)

            if not dir_path.exists():
                return ToolResult(f"错误: 目录不存在: {path}", success=False)

            if not dir_path.is_dir():
                return ToolResult(f"错误: 路径不是目录: {path}", success=False)

            if recursive:
                files = []
                for item in dir_path.rglob('*'):
                    if item.is_file():
                        rel_path = item.relative_to(dir_path)
                        files.append(f"  {rel_path}")
                    else:
                        rel_path = item.relative_to(dir_path)
                        files.append(f"  {rel_path}/")
            else:
                files = []
                for item in dir_path.iterdir():
                    if item.is_file():
                        files.append(f"  {item.name}")
                    else:
                        files.append(f"  {item.name}/")

            result = "\n".join(sorted(files))
            logger.debug(f"已列出 {path} 中的 {len(files)} 个文件")

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

    def syntax_check(self, paths: List[str]) -> ToolResult:
        """Check Python source files for syntax errors via ``ast.parse``.

        Only ``.py`` files are inspected.  Non-Python files are silently
        skipped.  Returns a compact pass/fail report with per-file errors.

        Typical output (success)::

            Syntax check: 5 files checked, 0 errors

        Typical output (failure)::

            Syntax check: 3 files checked, 1 error
              src/broken.py:42 - unmatched ')'
        """
        if not paths:
            return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)

        import ast

        try:
            search_files = self._expand_search_paths(paths)
        except ValueError as e:
            return ToolResult(str(e), success=False)

        # ── Filter for .py files only ────────────────────────────────
        py_files = [f for f in search_files if f.suffix == ".py"]

        if not py_files:
            return ToolResult("没有找到 Python 文件", success=False)

        errors: List[Dict[str, Any]] = []

        for file_path in py_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                ast.parse(source, filename=str(file_path))
            except SyntaxError as e:
                errors.append({
                    "file": str(file_path),
                    "line": e.lineno or 0,
                    "message": e.msg,
                })
            except UnicodeDecodeError:
                continue
            except Exception as e:
                errors.append({
                    "file": str(file_path),
                    "line": 0,
                    "message": str(e),
                })

        checked = len(py_files)
        if not errors:
            return ToolResult(
                f"Syntax check: {checked} file(s) checked, 0 errors",
                success=True,
            )

        err_lines: List[str] = [
            f"Syntax check: {checked} file(s) checked, {len(errors)} error(s)",
        ]
        for err in errors:
            err_lines.append(
                f"  {err['file']}:{err['line']} - {err['message']}"
            )

        return ToolResult("\n".join(err_lines), success=False)

    # ── verify_symbol_rename ─────────────────────────────────────────

    def verify_symbol_rename(
        self,
        old_symbols: List[str],
        new_symbols: List[str],
        paths: List[str],
        scope: str = "code_only",
        targets: Optional[List[Dict[str, str]]] = None,
    ) -> ToolResult:
        """Verify whether a symbol rename is structurally complete.

        Uses AST analysis to find real identifier usages.

        When ``scope="code_only"`` (default), occurrences in docstrings,
        comments, and string literals are reported separately as
        ``ignored_matches`` — they do NOT affect the success verdict.

        When ``scope="all"``, all occurrences (including non-code) are
        treated as meaningful remaining identifiers.

        Args:
            old_symbols: Symbols that should no longer appear as identifiers.
            new_symbols: Symbols expected to appear (positive confirmation).
            paths:       Files or directories to inspect.
            scope:       Verification scope — "code_only" (default) or "all".

        When ``targets`` is provided (list of ``{"file", "function",
        "old", "new"}`` dicts), verification is scoped to only those
        specific functions.  Old symbols in other functions do NOT
        affect the verdict.

            targets:     Optional list of target dicts for function-level
                         scoped verification.
        Returns:
            ToolResult with a structured JSON verdict.
        """
        if not paths:
            return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)
        if not old_symbols:
            return ToolResult("错误: 需要至少提供一个旧符号 (old_symbols)", success=False)
        if not new_symbols:
            return ToolResult("错误: 需要至少提供一个新符号 (new_symbols)", success=False)
        if scope not in ("code_only", "all"):
            return ToolResult(
                f"错误: scope 必须是 'code_only' 或 'all'，收到 '{scope}'",
                success=False,
            )

        # ── Targeted verification (when targets is provided) ──────────
        if targets is not None:
            t_remaining: List[Dict[str, Any]] = []
            t_syntax_errors: List[Dict[str, Any]] = []
            t_new_counts: Dict[str, int] = {}

            for target in targets:
                if not isinstance(target, dict):
                    return ToolResult(
                        "错误: targets 元素应为 object 类型",
                        success=False,
                    )
                for key in ("file", "function", "old", "new"):
                    if key not in target:
                        return ToolResult(
                            f"错误: target 缺少必要字段 '{key}'",
                            success=False,
                        )

                file_str = target["file"]
                fun_name = target["function"]
                target_old = target["old"]
                target_new = target["new"]

                try:
                    file_path = self.safe_path(file_str)
                except ValueError as e:
                    return ToolResult(
                        f"错误: 文件路径无法解析: {file_str} — {e}",
                        success=False,
                    )

                if not file_path.exists():
                    return ToolResult(
                        f"错误: 文件不存在: {file_str}",
                        success=False,
                    )
                if not file_path.is_file():
                    return ToolResult(
                        f"错误: 路径不是文件: {file_str}",
                        success=False,
                    )

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        source = f.read()
                except Exception as e:
                    t_syntax_errors.append({
                        "file": str(file_path), "line": 0, "message": str(e),
                    })
                    continue

                try:
                    tree = ast.parse(source, filename=str(file_path))
                except SyntaxError as e:
                    t_syntax_errors.append({
                        "file": str(file_path),
                        "line": e.lineno or 0,
                        "message": e.msg,
                    })
                    continue

                # Locate target function in AST
                fn_node = None
                for node in ast.walk(tree):
                    if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and node.name == fun_name):
                        fn_node = node
                        break

                if fn_node is None:
                    # Function not found — assume already renamed/removed
                    continue

                fn_start = fn_node.lineno
                fn_end = fn_node.end_lineno

                # Find old symbols within function range
                finder = _SymbolFinder([target_old])
                finder.visit(tree)
                for symbol, lineno in finder.found:
                    if fn_start <= lineno <= fn_end:
                        t_remaining.append({
                            "symbol": symbol,
                            "file": str(file_path),
                            "line": lineno,
                        })

                # Count new symbols within function range
                new_finder = _SymbolFinder([target_new])
                new_finder.visit(tree)
                for symbol, lineno in new_finder.found:
                    if fn_start <= lineno <= fn_end:
                        t_new_counts[symbol] = t_new_counts.get(symbol, 0) + 1

            # ── Build verdict for targeted verification ──
            if t_syntax_errors:
                result = {
                    "success": False,
                    "meaningful_remaining": [],
                    "syntax_ok": False,
                    "message": "Syntax error(s) detected - rename cannot be validated.",
                    "errors": t_syntax_errors,
                }
                return ToolResult(
                    json.dumps(result, indent=2, ensure_ascii=False), success=False,
                )

            if t_remaining:
                result = {
                    "success": False,
                    "confidence": "low",
                    "task_complete_likely": False,
                    "meaningful_remaining": t_remaining,
                    "syntax_ok": True,
                    "targets_verified": len(targets),
                    "message": "Target function(s) still contain old symbols.",
                }
                return ToolResult(
                    json.dumps(result, indent=2, ensure_ascii=False), success=False,
                )

            # Success: all target functions clean
            new_info = ""
            if t_new_counts:
                items = ", ".join(
                    f"'{k}' appears in {v} location(s)"
                    for k, v in t_new_counts.items()
                )
                new_info = f" New symbols: {items}."

            result = {
                "success": True,
                "confidence": "high",
                "task_complete_likely": True,
                "meaningful_remaining": [],
                "syntax_ok": True,
                "targets_verified": len(targets),
                "message": (
                    f"All {len(targets)} target function(s) verified. "
                    f"No remaining old symbols in targeted scope.{new_info}"
                    f" Additional verification is likely unnecessary."
                ),
            }
            return ToolResult(
                json.dumps(result, indent=2, ensure_ascii=False), success=True,
            )

        try:
            search_files = self._expand_search_paths(paths)
        except ValueError as e:
            return ToolResult(str(e), success=False)

        py_files = [f for f in search_files if f.suffix == ".py"]

        if not py_files:
            return ToolResult("没有找到 Python 文件", success=False)

        remaining: List[Dict[str, Any]] = []
        syntax_errors: List[Dict[str, Any]] = []
        new_counts: Dict[str, int] = {}
        all_ignored: Dict[str, int] = {"docstring": 0, "comments": 0, "string_literal": 0}

        for file_path in py_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    source = f.read()
            except Exception as e:
                syntax_errors.append({
                    "file": str(file_path),
                    "line": 0,
                    "message": str(e),
                })
                continue

            # ── Syntax validation ────────────────────────────────
            try:
                tree = ast.parse(source, filename=str(file_path))
            except SyntaxError as e:
                syntax_errors.append({
                    "file": str(file_path),
                    "line": e.lineno or 0,
                    "message": e.msg,
                })
                continue

            # ── Find remaining old-symbol identifiers ─────────────
            finder = _SymbolFinder(old_symbols)
            finder.visit(tree)
            for symbol, lineno in finder.found:
                remaining.append({
                    "symbol": symbol,
                    "file": str(file_path),
                    "line": lineno,
                })

            # ── Count new-symbol identifiers (positive signal) ───
            new_finder = _SymbolFinder(new_symbols)
            new_finder.visit(tree)
            for symbol, _lineno in new_finder.found:
                new_counts[symbol] = new_counts.get(symbol, 0) + 1

            # ── Classify non-code matches (docstrings/comments/strings) ──
            if scope == "code_only":
                d, c, s = self._classify_non_code(source, tree, old_symbols)
                all_ignored["docstring"] += d
                all_ignored["comments"] += c
                all_ignored["string_literal"] += s

        # ── Build verdict (scope-aware) ──────────────────────────────
        if syntax_errors:
            result = {
                "success": False,
                "meaningful_remaining": [],
                "syntax_ok": False,
                "message": "Syntax error(s) detected — rename cannot be validated.",
                "errors": syntax_errors,
            }
            return ToolResult(json.dumps(result, indent=2, ensure_ascii=False), success=False)

        if scope == "code_only":
            # Non-code matches (docstrings/comments/strings) do NOT count
            has_meaningful = bool(remaining)
            if has_meaningful:
                result = {
                    "success": False,
                    "confidence": "low",
                    "task_complete_likely": False,
                    "meaningful_remaining": remaining,
                    "syntax_ok": True,
                    "ignored_matches": all_ignored,
                    "message": "Meaningful code identifiers still contain old symbols.",
                }
                return ToolResult(
                    json.dumps(result, indent=2, ensure_ascii=False), success=False,
                )

            # Success: no meaningful code identifiers remain
            ignored_detail = []
            if all_ignored["docstring"]:
                ignored_detail.append(f"docstrings ({all_ignored['docstring']})")
            if all_ignored["comments"]:
                ignored_detail.append(f"comments ({all_ignored['comments']})")
            if all_ignored["string_literal"]:
                ignored_detail.append(f"string literals ({all_ignored['string_literal']})")

            msg = "Rename task structurally verified."
            if ignored_detail:
                msg += (f" Remaining occurrences are only in "
                        f"{'; '.join(ignored_detail)}. No action needed.")
            msg += " Additional verification is likely unnecessary for this low-risk refactor task."

            new_info = (
                f" New symbols appear in {sum(new_counts.values())} location(s)."
                if new_counts else ""
            )
            result = {
                "success": True,
                "confidence": "high",
                "task_complete_likely": True,
                "meaningful_remaining": [],
                "syntax_ok": True,
                "ignored_matches": all_ignored,
                "message": msg + new_info,
            }
            return ToolResult(
                json.dumps(result, indent=2, ensure_ascii=False), success=True,
            )

        # scope == "all": all occurrences count (code + non-code)
        has_any = bool(remaining) or any(all_ignored.values())
        if has_any:
            result = {
                "success": False,
                "meaningful_remaining": remaining,
                "syntax_ok": True,
                "ignored_matches": all_ignored,
                "message": "Remaining identifier usages detected.",
            }
            return ToolResult(
                json.dumps(result, indent=2, ensure_ascii=False), success=False,
            )

        new_info = (
            f" New symbols appear in {sum(new_counts.values())} location(s)."
            if new_counts else ""
        )
        result = {
            "success": True,
            "confidence": "high",
            "task_complete_likely": True,
            "meaningful_remaining": [],
            "syntax_ok": True,
            "ignored_matches": all_ignored,
            "message": f"Rename task structurally verified.{new_info}",
        }
        return ToolResult(
            json.dumps(result, indent=2, ensure_ascii=False), success=True,
        )

    @staticmethod
    def _classify_non_code(source: str, tree, old_symbols):
        """Classify old symbol occurrences in docstrings, comments, and string literals.

        Returns:
            Tuple of (docstring_count, comment_count, string_literal_count).
        """
        import tokenize, io

        # ── Identify docstring AST nodes ─────────────────────────
        docstring_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                if (node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                    docstring_nodes.add(node.body[0].value)

        # ── Count in docstrings ──────────────────────────────────
        doc_count = 0
        for ds_node in docstring_nodes:
            for sym in old_symbols:
                doc_count += ds_node.value.count(sym)

        # ── Count in string literals (non-docstring) ─────────────
        str_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node not in docstring_nodes:
                    for sym in old_symbols:
                        str_count += node.value.count(sym)
            elif isinstance(node, ast.JoinedStr):
                for vn in node.values:
                    if (isinstance(vn, ast.Constant)
                        and isinstance(vn.value, str)
                        and vn not in docstring_nodes):
                        for sym in old_symbols:
                            str_count += vn.value.count(sym)

        # ── Count in comments (via tokenize) ────────────────────
        com_count = 0
        try:
            tokens = tokenize.generate_tokens(io.StringIO(source).readline)
            for token in tokens:
                if token.type == tokenize.COMMENT:
                    for sym in old_symbols:
                        com_count += token.string.count(sym)
        except Exception:
            pass

        return doc_count, com_count, str_count
