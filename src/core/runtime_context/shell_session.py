"""
ShellSession — persistent shell session with cwd tracking.

Maintains a logical shell state (cwd, env) across tool calls.
Commands are executed via subprocess.run() with cwd=session.cwd.
"""
from __future__ import annotations
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Detect cd commands
_RE_CD = re.compile(r'(?:^|\s*&&\s*)cd\s+(.+?)(?:\s*&&\s*.*)?$', re.DOTALL)


class ShellSession:
    """Persistent shell session with automatic cwd tracking.

    Usage:
        session = ShellSession(workspace_root=Path("/project"))
        session.execute("cd src")          # cwd → /project/src
        session.execute("ls")              # runs in /project/src
        session.execute("cd .. && ls")     # cwd → /project
    """

    def __init__(self, workspace_root: Path):
        self.cwd = Path(workspace_root).resolve()
        self.env: Dict[str, str] = os.environ.copy()
        self.session_id: str = uuid.uuid4().hex[:8]
        self.command_history: List[str] = []

    # ── Public API ─────────────────────────────────────────────────

    def execute(self, command: str, timeout: int = 120,
                cwd_override: Optional[Path] = None) -> Dict[str, Any]:
        """Execute a command in the persistent shell session.

        Args:
            command: Shell command string.
            timeout: Execution timeout in seconds.
            cwd_override: One-time subprocess cwd override (does NOT
                          modify persistent session state).

        Returns:
            Dict with keys: content (str), success (bool), cwd (str).
        """
        cmd_stripped = command.strip()

        # Capture cwd BEFORE any cd updates (subprocess cd needs old cwd)
        old_cwd = self.cwd
        try:
            self._update_cwd(command)
        except FileNotFoundError as e:
            return {
                "content": f"[Exit Code: 1]\n{str(e)}",
                "success": False,
                "cwd": str(self.cwd),
            }

        # Record history
        self.command_history.append(command)

        # Pure cd command: cwd tracking is sufficient, skip subprocess exec
        if cmd_stripped.startswith("cd ") and "&&" not in cmd_stripped:
            return {
                "content": f"[Exit Code: 0]\n(ShellSession: cwd → {self.cwd})",
                "success": True,
                "cwd": str(self.cwd),
            }

        # Determine effective cwd for subprocess (cwd_override takes precedence)
        effective_cwd = cwd_override if cwd_override is not None else old_cwd

        # For non-cd (or chained) commands, use effective_cwd so relative paths resolve
        try:
            logger.debug(
                f"[ShellSession:{self.session_id}] cwd={effective_cwd} | {command[:100]}"
            )
            r = subprocess.run(
                command,
                shell=True,
                cwd=str(effective_cwd),
                capture_output=True,
                timeout=timeout,
            )

            raw = r.stdout + r.stderr
            try:
                out = raw.decode("utf-8")
            except UnicodeDecodeError:
                out = raw.decode("gbk", errors="replace")

            out = out.strip()
            if len(out) > 50000:
                out = out[:50000] + f"\n... (已截断，剩余 {len(out) - 50000} 个字符)"

            result = f"[Exit Code: {r.returncode}]\n"
            result += out if out else "(Command executed silently with no output or errors.)"

            logger.debug(f"[ShellSession] exit={r.returncode}")
            return {
                "content": result,
                "success": r.returncode == 0,
                "cwd": str(self.cwd),
            }

        except subprocess.TimeoutExpired:
            logger.warning(f"[ShellSession] 超时 {timeout}s")
            return {
                "content": f"错误: 执行超时（{timeout} 秒）",
                "success": False,
                "cwd": str(self.cwd),
            }
        except Exception as e:
            logger.error(f"[ShellSession] 错误: {e}")
            return {
                "content": f"错误: {str(e)}",
                "success": False,
                "cwd": str(self.cwd),
            }

    # ── CWD Management ─────────────────────────────────────────────

    def _update_cwd(self, command: str) -> None:
        """Track cd-commands and update the session cwd."""
        # Handle simple "cd <dir>" at the start or after &&
        parts = command.split("&&")
        for part in parts:
            trimmed = part.strip()
            if trimmed.startswith("cd "):
                target = trimmed[3:].strip().strip('"').strip("'")
                if not target:
                    continue
                self._apply_cd(target)

    def _apply_cd(self, target: str) -> None:
        """Resolve and apply a cd target to the session cwd.

        Raises:
            FileNotFoundError: If the target directory does not exist.
        """
        p = Path(target)
        if not p.is_absolute():
            p = (self.cwd / p).resolve()
        else:
            p = p.resolve()

        if p.exists() and p.is_dir():
            self.cwd = p
            logger.info(f"ShellSession: cwd → {self.cwd}")
        else:
            raise FileNotFoundError(
                f"cd: {target}: No such file or directory"
            )

    def reset(self) -> None:
        """Reset session to workspace root."""
        self.cwd = self._original_root
        self.command_history.clear()

    @property
    def _original_root(self) -> Path:
        """Return the original workspace root (inferred from first cwd)."""
        return self.cwd
