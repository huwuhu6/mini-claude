import os
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
