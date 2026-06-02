"""
CommandPolicy — rule-based command security policy.

Replaces the blanket shell-control-character ban with targeted rules:

  ALLOWED:
    cd && command     — directory navigation chaining
    mkdir && cd       — create-and-enter
    python && pytest  — build-and-test

  BLOCKED:
    curl|bash         — remote code execution via pipe
    wget|sh           — remote code execution via pipe
    rm -rf /          — destructive filesystem operation
    fork bomb         — resource exhaustion
    background &      — process backgrounding (single &)
    powershell/wmic   — high-risk executors

Design: pure regex rules, no AST parser.
"""
from __future__ import annotations
import re
from typing import Optional


class CommandPolicy:
    """Rule-based command validation policy."""

    # ── Blocked executor patterns (same as original BaseTools) ──────
    HIGH_RISK_EXECUTORS: list[re.Pattern] = [
        re.compile(r'\bpowershell\b'), re.compile(r'\bpwsh\b'),
        re.compile(r'\bwmic\b'),
        re.compile(r'\bcmd\s+/c\b'), re.compile(r'\bcmd\.exe\s+/c\b'),
        re.compile(r'\bstart\s+/\w'), re.compile(r'\brunas\b'),
    ]

    # ── Destructive filesystem operations ──────────────────────────
    DESTRUCTIVE: list[re.Pattern] = [
        re.compile(r'\brm\s+.*-rf\s+/'),   # rm -rf /
        re.compile(r'\brm\s+.*-r\s+/'),
        re.compile(r'\brmdir\s+/s'),
        re.compile(r'\bdel\s+/[fq]'),
        re.compile(r'\bformat\b'),
        re.compile(r'\bmkfs\b'), re.compile(r'\bfdisk\b'),
        re.compile(r'\bdd\s+if='),
        re.compile(r'\bshutdown\b'), re.compile(r'\breboot\b'),
        re.compile(r':\(\)\s*\{'),  # fork bomb
    ]

    # ── Pipe-to-shell (remote code execution) ──────────────────────
    PIPE_BOMB: list[re.Pattern] = [
        re.compile(r'curl\s+.*?\|.*?\b(bash|sh|zsh|python|perl)\b'),
        re.compile(r'wget\s+.*?\|.*?\b(bash|sh|zsh|python|perl)\b'),
        re.compile(r'Invoke-WebRequest.*?\|.*?\b(bash|sh)\b'),
    ]

    # ── Background single-ampersand (block &, allow &&) ────────────
    BACKGROUND = re.compile(r'(?<![&])&(?![&])')

    # ── Shell sub / command substitution ────────────────────────────
    SUBSTITUTION = re.compile(r'\$\(|\x60')  # $(…) and backtick; allow $PATH etc.

    def check(self, command: str) -> Optional[str]:
        """Validate a command string against the policy.

        Returns:
            None if the command is allowed.
            Error string if the command is blocked.
        """
        # Allow empty / whitespace-only commands
        if not command or not command.strip():
            return "错误: 命令为空"

        cmd_lower = command.lower()

        # 1. High-risk executors
        for pat in self.HIGH_RISK_EXECUTORS:
            if pat.search(cmd_lower):
                return f"错误: 禁止调用高危执行器: {pat.search(cmd_lower).group()}"

        # 2. Destructive operations
        for pat in self.DESTRUCTIVE:
            if pat.search(cmd_lower):
                return "错误: 检测到高危文件操作，已被拦截"

        # 3. Pipe-to-shell
        for pat in self.PIPE_BOMB:
            if pat.search(cmd_lower):
                return "错误: 禁止从远程 URL 直接管道到 Shell"

        # 4. Background single-ampersand (allow &&)
        if self.BACKGROUND.search(command):
            return "错误: 禁止后台运行 (&)"

        # 5. Shell substitution ($() or backticks)
        if self.SUBSTITUTION.search(command):
            return "错误: 禁止使用命令替换 ($() 或 ``)"

        return None
