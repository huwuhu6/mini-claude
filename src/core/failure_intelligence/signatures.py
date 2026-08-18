"""
Signatures — Rule-based failure classification and strategy fingerprint inference.

Two main functions:
    1. FailureSignatureMatcher.match(text) -> (FailureCategory, Recoverability, confidence, hint)
    2. infer_strategy_fingerprint(tool_name, args) -> str

Both are pure, stateless, and pattern-match against known error strings.
No ML, no external services.
"""
from __future__ import annotations
import re
from typing import Dict, Any, Optional, Tuple

from .models import FailureCategory, Recoverability


# ── Failure Classification Rules ─────────────────────────────────────────

_ClassificationRule = Tuple[
    re.Pattern,
    FailureCategory,
    Recoverability,
    str,    # root_cause_hint
    float,  # confidence
]

_CLASSIFICATION_RULES: list[_ClassificationRule] = [
    (re.compile(r'\[WinError 5\]|\[Errno 13\]'),
     FailureCategory.PERMISSION_DENIED,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "The operating system denied access to the requested resource.", 0.95),

    # ── PERMISSION_DENIED (highest priority) ─────────────────
    (re.compile(r'Permission denied|permission denied|Access is denied|'
                r'AccessDenied|EACCES|EPERM|拒绝访问'),
     FailureCategory.PERMISSION_DENIED,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "当前用户没有足够权限执行此操作", 0.95),

    # ── NETWORK_UNREACHABLE ─────────────────────────────────
    (re.compile(r'Failed to establish a new connection|Cannot connect|'
                r'ENOTFOUND|ETIMEDOUT|EHOSTUNREACH|ECONNREFUSED|'
                r'connection refused|Connection refused|'
                r'Network is unreachable|network is unreachable|'
                r'cannot reach|no route to host|No route to host|'
                r'socket\.gaierror|\[Errno -2\]|\[Errno -3\]|\[Errno -5\]|'
                r'Name or service not known|Temporary failure in name resolution|'
                r'\[WinError 10060\]|\[WinError 10061\]|'
                r'getaddrinfo failed|连接超时|无法连接到'),
     FailureCategory.NETWORK_UNREACHABLE,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "网络不可达或 DNS 解析失败，无法连接到目标服务器", 0.90),

    # ── TIMEOUT ─────────────────────────────────────────────
    (re.compile(r'timed out|Timeout|timeout|Read timed out|'
                r'Connection timed out|\[WinError 10060\]|'
                r'connect timed out|operation timed out|'
                r'Response timeout|超时'),
     FailureCategory.TIMEOUT,
     Recoverability.PARTIALLY_RECOVERABLE,
     "操作超时，可能是网络延迟或服务器响应过慢", 0.85),

    # ── PACKAGE_NOT_FOUND ───────────────────────────────────
    (re.compile(r'Could not find a version|No matching distribution|'
                r'Could not resolve dependencies|Could not find artifact|'
                r'no matching package named|failed to select a version for the requirement|'
                r'cannot find module providing package|no matching versions for query|'
                r'npm\s+ERR!\s+(?:code\s+)?E404|yarn\s+error:.*Couldn.t find package|'
                r'not found in repository|package not found|Package not found|'
                r'404 Not Found|404 Client Error|'
                r'找不到.*版本|没有找到.*包|no such package'),
     FailureCategory.PACKAGE_NOT_FOUND,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "找不到指定的软件包，可能包名错误或源中不存在", 0.90),

    # ── FILE_NOT_FOUND ──────────────────────────────────────
    (re.compile(r'No such file|FileNotFoundError|file not found|'
                r'does not exist|doesn\'t exist|cannot find|Cannot find|'
                r'不存在|找不到.*文件|文件.*不存在'),
     FailureCategory.FILE_NOT_FOUND,
     Recoverability.SELF_HEALABLE,
     "文件不存在，需要检查路径或先创建文件", 0.90),

    # ── SYNTAX_ERROR ────────────────────────────────────────
    (re.compile(r'SyntaxError|Syntax error|syntax error|invalid syntax|'
                r'unexpected token|Unexpected token|'
                r'invalid syntax|语法错误'),
     FailureCategory.SYNTAX_ERROR,
     Recoverability.SELF_HEALABLE,
     "语法错误，需要修正命令或代码格式", 0.90),

    # ── COMMAND_NOT_FOUND ───────────────────────────────────
    (re.compile(r'exit code \d+|Command \'|command not found|'
                r'not recognized|is not recognized|'
                r'不是内部或外部命令|不是可执行文件|'
                r'未找到命令'),
     FailureCategory.COMMAND_NOT_FOUND,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "命令不存在或执行失败，需要检查命令名称或安装依赖", 0.75),

    # ── OUT_OF_MEMORY ───────────────────────────────────────
    (re.compile(r'Killed|Out of memory|Cannot allocate memory|'
                r'OutOfMemory|MemoryError|内存不足|内存耗尽'),
     FailureCategory.OUT_OF_MEMORY,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "内存不足，需要释放内存或增加可用资源", 0.95),

    # ── DISK_FULL ───────────────────────────────────────────
    (re.compile(r'Disk quota exceeded|No space left|disk full|Disk full|'
                r'disk quota|空间不足|磁盘已满'),
     FailureCategory.DISK_FULL,
     Recoverability.USER_INTERVENTION_REQUIRED,
     "磁盘空间不足，需要清理磁盘或扩展存储", 0.95),

    # ── TOOL_CRASH (generic tool exception) ─────────────────
    (re.compile(r'错误:|Error:|Traceback|CRITICAL SYSTEM FAILURE'),
     FailureCategory.TOOL_CRASH,
     Recoverability.PARTIALLY_RECOVERABLE,
     "工具执行时发生了意外错误", 0.70),
]


class FailureSignatureMatcher:
    """Stateless rule engine: match error text → FailureCategory + Recoverability."""

    @classmethod
    def match(cls, result_text: str) -> Tuple[FailureCategory, Recoverability, float, str]:
        """Classify error text using ordered rule patterns.

        Returns:
            (category, recoverability, confidence, root_cause_hint)
            UNKNOWN / UNKNOWN / 0.0 / "" if no rule matched.
        """
        if not result_text:
            return FailureCategory.UNKNOWN, Recoverability.UNKNOWN, 0.0, ""

        for pattern, category, rec, hint, conf in _CLASSIFICATION_RULES:
            if pattern.search(result_text):
                return category, rec, conf, hint

        return FailureCategory.UNKNOWN, Recoverability.UNKNOWN, 0.0, ""


# ── Strategy Fingerprint Inference ──────────────────────────────────────

_StrategyRule = Tuple[str, re.Pattern, str]  # (tool_name, pattern, strategy_fp)

_STRATEGY_RULES: list[_StrategyRule] = [
    # pip / pip3 install
    ("bash", re.compile(r'pip\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'pip3\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'pip install'), 'NETWORK_PACKAGE_INSTALL'),
    # npm / conda / brew / apt / choco
    ("bash", re.compile(r'npm\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'conda\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'brew\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'apt(-get)?\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'choco\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    ("bash", re.compile(r'yum\s+install'), 'NETWORK_PACKAGE_INSTALL'),
    # pip list / show (package query)
    ("bash", re.compile(r'pip\s+list|pip3\s+list'), 'PACKAGE_QUERY'),
    ("bash", re.compile(r'pip\s+show|pip3\s+show'), 'PACKAGE_QUERY'),
    # Network download (curl / wget)
    ("bash", re.compile(r'(curl|wget|Invoke-WebRequest)\s'), 'NETWORK_DOWNLOAD'),
    # Shell navigation
    ("bash", re.compile(r'(^|\s)(cd|pushd|popd|ls|dir|pwd)\s'), 'SHELL_NAVIGATION'),
    # File read
    ("bash", re.compile(r'(^|\s)(cat|head|tail|more|type|findstr)\s'), 'FILE_READ'),
    # File system modify
    ("bash", re.compile(r'(^|\s)(mkdir|rmdir|rm|cp|mv|copy|move|del|rename|mkfile)\s'),
     'FILE_SYSTEM_MODIFY'),
    # Code execution
    ("bash", re.compile(r'(python|py|python3|node|ruby|perl)\s'), 'CODE_EXECUTION'),
    # Code compile
    ("bash", re.compile(r'(gcc|g\+\+|make|cmake|clang|rustc|go\s+build)\s'), 'CODE_COMPILE'),
    # VCS
    ("bash", re.compile(r'(^|\s)git\s'), 'VCS_OPERATION'),
    # Binary location
    ("bash", re.compile(r'(^|\s)(which|where)\s'), 'BINARY_LOCATION'),
    # OS query
    ("bash", re.compile(r'(uname|cat /etc/|ver|systeminfo)'), 'OS_QUERY'),
    # Privilege escalation
    ("bash", re.compile(r'(^|\s)(sudo|runas)\s'), 'PRIVILEGE_ESCALATION'),
    # Environment query
    ("bash", re.compile(r'(env|set|echo \$|echo %)'), 'ENV_QUERY'),
    # Tool configure (changing flags)
    ("bash", re.compile(r'--\w[\w-]*='), 'TOOL_FLAG_TUNE'),
]

# File-based tools map directly to LOCAL_FILE_IO
_FILE_TOOL_STRATEGY = {
    "read_file": "LOCAL_FILE_IO",
    "write_file": "LOCAL_FILE_IO",
    "read_file_lines": "LOCAL_FILE_IO",
    "edit_file": "LOCAL_FILE_IO",
}


def infer_strategy_fingerprint(tool_name: str, args: Dict[str, Any]) -> str:
    """Infer the high-level strategy fingerprint from tool name and args.

    Returns a short string like "NETWORK_PACKAGE_INSTALL", "LOCAL_FILE_IO",
    or "UNKNOWN_STRATEGY".
    """
    # File IO tools
    strategy = _FILE_TOOL_STRATEGY.get(tool_name)
    if strategy:
        return strategy

    # Bash commands: match against argument patterns
    if tool_name == "bash":
        cmd = args.get("command", "") if isinstance(args, dict) else str(args)
        for tool_name_pattern, pattern, fp in _STRATEGY_RULES:
            if pattern.search(cmd):
                return fp

    # Fallback: infer from tool name structure
    if "file" in tool_name or "path" in tool_name:
        return "LOCAL_FILE_IO"
    if "search" in tool_name or "query" in tool_name:
        return "SEARCH_QUERY"
    if "task" in tool_name or "subagent" in tool_name:
        return "SUBAGENT_DELEGATION"

    return "UNKNOWN_STRATEGY"
