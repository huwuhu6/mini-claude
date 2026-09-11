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
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Detect cd commands
_RE_CD = re.compile(r'(?:^|\s*&&\s*)cd\s+(.+?)(?:\s*&&\s*.*)?$', re.DOTALL)
_SEGMENT_MARKER = "__MINICLAUDE_SEGMENT_EXIT_"


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
                "execution_success": False,
                "cwd": str(self.cwd),
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "timed_out": False,
                "cancelled": False,
                "segment_exit_codes": [],
            }

        # Record history
        self.command_history.append(command)

        # Pure cd command: cwd tracking is sufficient, skip subprocess exec
        if cmd_stripped.startswith("cd ") and "&&" not in cmd_stripped:
            return {
                "content": f"[Exit Code: 0]\n(ShellSession: cwd → {self.cwd})",
                "success": True,
                "execution_success": True,
                "cwd": str(self.cwd),
                "stdout": "(ShellSession: cwd → %s)" % self.cwd,
                "stderr": "",
                "exit_code": 0,
                "timed_out": False,
                "cancelled": False,
                "segment_exit_codes": [],
            }

        # Determine effective cwd for subprocess (cwd_override takes precedence)
        effective_cwd = cwd_override if cwd_override is not None else old_cwd

        # For non-cd (or chained) commands, use effective_cwd so relative paths resolve
        try:
            logger.debug(
                f"[ShellSession:{self.session_id}] cwd={effective_cwd} | {command[:100]}"
            )
            instrumented_command = self._instrument_composite(command)
            temp_script = None
            try:
                if sys.platform == "win32" and instrumented_command != command:
                    # Delayed expansion is required for per-segment
                    # !errorlevel! markers.  A temporary .cmd file preserves
                    # nested quotes that would otherwise be escaped by
                    # subprocess's argv conversion before CMD sees them.
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".cmd", delete=False,
                        encoding="utf-8", newline="\r\n",
                    ) as script:
                        script.write("@echo off\r\n")
                        script.write(instrumented_command)
                        script.write("\r\n")
                        temp_script = script.name
                    r = subprocess.run(
                        ["cmd.exe", "/D", "/V:ON", "/C", temp_script],
                        shell=False,
                        cwd=str(effective_cwd),
                        capture_output=True,
                        timeout=timeout,
                    )
                else:
                    r = subprocess.run(
                        command,
                        shell=True,
                        cwd=str(effective_cwd),
                        capture_output=True,
                        timeout=timeout,
                    )
            finally:
                if temp_script:
                    try:
                        Path(temp_script).unlink()
                    except OSError:
                        logger.debug("无法清理 shell instrumentation script: %s", temp_script)

            stdout = self._decode(r.stdout)
            stderr = self._decode(r.stderr)
            stdout, stdout_codes = self._strip_segment_markers(stdout)
            stderr, stderr_codes = self._strip_segment_markers(stderr)
            segment_exit_codes = stdout_codes + stderr_codes
            raw = stdout + stderr
            try:
                out = raw.strip()
            except AttributeError:
                out = str(raw).strip()

            result = f"[Exit Code: {r.returncode}]\n"
            result += out if out else "(Command executed silently with no output or errors.)"

            logger.debug(f"[ShellSession] exit={r.returncode}")
            return {
                "content": result,
                "success": r.returncode == 0,
                "execution_success": r.returncode == 0,
                "cwd": str(self.cwd),
                "exit_code": r.returncode,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "timed_out": False,
                "cancelled": False,
                "segment_exit_codes": segment_exit_codes,
            }

        except subprocess.TimeoutExpired:
            logger.warning(f"[ShellSession] 超时 {timeout}s")
            return {
                "content": f"错误: 执行超时（{timeout} 秒）",
                "success": False,
                "execution_success": False,
                "cwd": str(self.cwd),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": True,
                "cancelled": False,
                "segment_exit_codes": [],
            }
        except KeyboardInterrupt:
            # Ctrl+C should cancel the current command, not tear down the
            # whole Agent before it can close the trace and session record.
            logger.warning("[ShellSession] 命令被用户中断")
            return {
                "content": "[用户中断] 命令已被用户取消。",
                "success": False,
                "execution_success": False,
                "cwd": str(self.cwd),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "cancelled": True,
                "segment_exit_codes": [],
            }
        except Exception as e:
            logger.error(f"[ShellSession] 错误: {e}")
            return {
                "content": f"错误: {str(e)}",
                "success": False,
                "execution_success": False,
                "cwd": str(self.cwd),
                "exit_code": None,
                "stdout": "",
                "stderr": str(e),
                "timed_out": False,
                "cancelled": False,
                "segment_exit_codes": [],
            }

    @staticmethod
    def _decode(value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("gbk", errors="replace")

    @staticmethod
    def _strip_segment_markers(text: str) -> tuple[str, list[int]]:
        codes: list[int] = []
        kept: list[str] = []
        pattern = re.compile(rf"^{re.escape(_SEGMENT_MARKER)}\d+=(-?\d+)\s*$")
        for line in text.splitlines():
            match = pattern.match(line.strip())
            if match:
                codes.append(int(match.group(1)))
            else:
                kept.append(line)
        return "\n".join(kept), codes

    @classmethod
    def _instrument_composite(cls, command: str) -> str:
        """Capture non-final command statuses without changing final status.

        Windows CMD commonly masks ``A & echo ...`` with the echo's zero
        status.  Delayed expansion lets us insert invisible-to-the-LLM status
        markers after unquoted command separators.  POSIX ``;`` chains get
        the equivalent ``$?`` marker.  ``&`` on POSIX is left untouched:
        launch status is not the background process's completion status.
        """
        separators = cls._top_level_separators(command)
        if not separators:
            return command
        pieces: list[str] = []
        cursor = 0
        marker_index = 0
        for position, separator in separators:
            pieces.append(command[cursor:position])
            if sys.platform == "win32" and separator == "&":
                pieces.append(
                    f" & echo {_SEGMENT_MARKER}{marker_index}=!errorlevel! & "
                )
                marker_index += 1
            elif sys.platform != "win32" and separator == ";":
                pieces.append(
                    f"; printf '{_SEGMENT_MARKER}{marker_index}=%s\\n' \"$?\"; "
                )
                marker_index += 1
            else:
                pieces.append(command[position:position + len(separator)])
            cursor = position + len(separator)
        pieces.append(command[cursor:])
        instrumented = "".join(pieces)
        if sys.platform == "win32" and any(s == "&" for _, s in separators):
            return "setlocal EnableDelayedExpansion & " + instrumented
        return instrumented

    @staticmethod
    def _top_level_separators(command: str) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        quote: Optional[str] = None
        escaped = False
        index = 0
        while index < len(command):
            char = command[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "^" and sys.platform == "win32":
                escaped = True
                index += 1
                continue
            if char in {'"', "'"}:
                quote = None if quote == char else char if quote is None else quote
                index += 1
                continue
            if quote is None and char in {"&", ";"}:
                previous = command[index - 1] if index else ""
                following = command[index + 1] if index + 1 < len(command) else ""
                if previous != ">" and following not in {"&", "|", ">"}:
                    result.append((index, char))
            index += 1
        return result

    # ── CWD Management ─────────────────────────────────────────────

    def _update_cwd(self, command: str) -> None:
        """Track cd-commands and update the session cwd."""
        # Handle simple "cd <dir>" at the start or after &&
        parts = command.split("&&")
        for part in parts:
            trimmed = part.strip()
            if trimmed.startswith("cd "):
                target_text = trimmed[3:].strip()
                if sys.platform == "win32" and re.match(r"^/d(?:\s|$)", target_text, re.IGNORECASE):
                    target_text = target_text[2:].strip()
                target = target_text.strip().strip('"').strip("'")
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
