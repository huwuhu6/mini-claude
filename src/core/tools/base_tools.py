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

    def read_file(self, path: str, start_line: int = None,
                  end_line: int = None) -> ToolResult:
        """
        Read a window of file content with anchor-comment delimiters.

        Pure code output (no per-line prefix) to maximise Prompt Cache
        stability — line numbers shift after edits, destroying cache hits.

        Args:
            path: File path
            start_line: First line number to include (1-based, inclusive).
                        Defaults to 1 when omitted.
            end_line: Last line number to include (1-based, inclusive).
                      Defaults to total line count when omitted.

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

            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines(keepends=False)

            total_lines = len(lines)

            # Resolve slice boundaries (1-based inclusive)
            start = 1 if start_line is None else max(1, int(start_line))
            end = total_lines if end_line is None else min(total_lines, int(end_line))

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
            output = (
                f"--- FILE: {path} (LINES: {start}-{end} of {total_lines}) ---\n"
                + '\n'.join(chunk)
                + f"\n--- END FILE: {path} ---"
            )

            logger.debug(
                f"读取文件: {path} [{start}-{end}/{total_lines} 行] "
                f"({len(output)} 个字符)"
            )

            return ToolResult(output)

        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()
                lines = content.splitlines(keepends=False)
                total_lines = len(lines)
                start = 1 if start_line is None else max(1, int(start_line))
                end = total_lines if end_line is None else min(total_lines, int(end_line))
                if start > end:
                    return ToolResult(
                        f"错误: start_line ({start}) > end_line ({end})，行号范围非法。",
                        success=False,
                    )
                chunk = lines[start - 1:end]
                return ToolResult(
                    f"--- FILE: {path} (LINES: {start}-{end} of {total_lines}) ---\n"
                    + '\n'.join(chunk)
                    + f"\n--- END FILE: {path} ---"
                )
            except Exception as e:
                return ToolResult(f"读取文件时出错（编码问题）: {str(e)}", success=False)
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

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for fuzzy search fallback:
        1. Normalize line endings (\\r\\n → \\n, \\r → \\n)
        2. Strip trailing whitespace from each line
        """
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        lines = text.split('\n')
        lines = [line.rstrip() for line in lines]
        return '\n'.join(lines)

    def edit_file(self, path: str, edits: list) -> ToolResult:
        """
        Apply multiple search/replace edits atomically using a shadow buffer.

        Two-phase commit:
          1. Verify ALL edits against the in-memory shadow buffer first.
          2. Only on full success write to disk once.

        Each edit goes through two matching levels:
          - Level 1 — strict exact match on the raw buffer text
          - Level 2 — normalised match (unified line endings, trailing
                      whitespace stripped) as fallback

        If ANY edit fails both levels the entire operation is rolled back
        and the file is left untouched.

        Args:
            path: File path
            edits: List of {"search": str, "replace": str} dicts

        Returns:
            ToolResult with status
        """
        try:
            file_path = self.safe_path(path)

            if not file_path.exists():
                return ToolResult(f"错误: 文件不存在: {path}", success=False)

            # ── Phase 1: Load shadow buffer ──────────────────────────
            with open(file_path, 'r', encoding='utf-8') as f:
                working_content = f.read()

            normalized_content = self._normalize_text(working_content)

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

                # ── Level 1: strict exact match ──────────────────────
                if search in working_content:
                    working_content = working_content.replace(search, replace, 1)
                    normalized_content = self._normalize_text(working_content)
                    continue

                # ── Level 2: normalised fallback ─────────────────────
                normalized_search = self._normalize_text(search)
                if normalized_search in normalized_content:
                    working_content = normalized_content.replace(
                        normalized_search, replace, 1,
                    )
                    normalized_content = self._normalize_text(working_content)
                    logger.info(
                        f"第 {i+1} 处修改通过归一化匹配 "
                        f"(消除换行符/行尾空格差异)"
                    )
                    continue

                # ── Rollback: neither level matched ──────────────────
                return ToolResult(
                    f"【Harness 事务拦截】第 {i+1} 处修改匹配失败。\n"
                    f"无法定位您的 'search' 片段，请重新使用 read_file "
                    f"核对该段代码的精准缩进与换行符。\n"
                    f"当前文件已自动整体回滚，未做任何修改。",
                    success=False,
                )

            # ── Phase 2: Commit — single disk write ──────────────────
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(working_content)

            logger.info(f"已编辑文件: {path} ({len(edits)} 处修改)")
            return ToolResult(f"成功编辑 {path}，共完成 {len(edits)} 处修改")

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
    ) -> ToolResult:
        """Verify whether a symbol rename is structurally complete.

        Unlike ``count_occurrences`` (which also hits comments, docstrings
        and string literals), this tool uses **AST analysis** to find only
        real identifier usages.  Comments, docstrings, and string constants
        are naturally ignored by the parser.

        The tool also performs automatic syntax validation — a rename is
        only reported as complete when all modified files parse cleanly.

        Args:
            old_symbols: Symbols that should no longer appear as identifiers.
            new_symbols: Symbols expected to appear (informational / positive
                         confirmation).
            paths:       Files or directories to inspect.

        Returns:
            ToolResult with a structured JSON verdict.
        """
        if not paths:
            return ToolResult("错误: 需要至少提供一个路径 (paths)", success=False)
        if not old_symbols:
            return ToolResult("错误: 需要至少提供一个旧符号 (old_symbols)", success=False)
        if not new_symbols:
            return ToolResult("错误: 需要至少提供一个新符号 (new_symbols)", success=False)

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

        # ── Build verdict ─────────────────────────────────────────
        if syntax_errors:
            # Syntax errors take precedence
            result = {
                "success": False,
                "remaining_identifiers": [],
                "syntax_ok": False,
                "message": "Syntax error(s) detected — rename cannot be validated.",
                "errors": syntax_errors,
            }
            return ToolResult(json.dumps(result, indent=2, ensure_ascii=False), success=False)

        if remaining:
            result = {
                "success": False,
                "remaining_identifiers": remaining,
                "syntax_ok": True,
                "message": "Remaining identifier usages detected.",
            }
            return ToolResult(json.dumps(result, indent=2, ensure_ascii=False), success=False)

        # ── Success ──────────────────────────────────────────────
        new_info = (
            f" New symbols appear in {sum(new_counts.values())} location(s)."
            if new_counts
            else ""
        )
        result = {
            "success": True,
            "remaining_identifiers": [],
            "syntax_ok": True,
            "message": f"Rename task appears structurally complete.{new_info}",
        }
        return ToolResult(json.dumps(result, indent=2, ensure_ascii=False), success=True)
