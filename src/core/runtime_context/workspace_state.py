"""Language-neutral workspace snapshots and no-op mutation detection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class WorkspaceMutation:
    changed_paths: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)


class WorkspaceStateGuard:
    """Detect consecutive write/read operations without useful state change."""

    _IGNORED_DIRS = frozenset({
        ".git", ".agent", ".claude", ".traces", ".pytest_cache", "__pycache__",
        "node_modules", ".venv", "venv", "dist", "build", ".mypy_cache",
    })
    _WRITE_COMMAND = re.compile(
        r"(?:>>?|\b(?:mkdir|rmdir|rm|del|copy|cp|move|mv|rename|ren|touch|tee)\b|"
        r"\b(?:sed|perl)\b[^\r\n]*\s-i(?:\s|$)|"
        r"\b(?:Set-Content|Add-Content|Out-File|New-Item|Remove-Item|"
        r"Copy-Item|Move-Item|Rename-Item)\b|"
        r"\b(?:write_text|write_bytes|unlink|makedirs|mkdir|remove|rename|"
        r"replace|rmtree|WriteFile|writeFile|appendFile|unlinkSync|renameSync)"
        r"\s*\(|\bopen\s*\([^\r\n]*,\s*['\"][wax][bt+]*['\"])",
        re.IGNORECASE,
    )
    _WRITE_TOOLS = frozenset({"edit_file", "write_file"})
    _SHELL_TOOLS = frozenset({"bash", "powershell", "pwsh", "cmd"})

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self._write_stalls = 0
        self._last_read_key: Optional[str] = None
        self._read_stalls = 0

    def reset(self) -> None:
        self._write_stalls = 0
        self._last_read_key = None
        self._read_stalls = 0

    def is_write_operation(self, tool_name: str, args: dict) -> bool:
        if tool_name in self._WRITE_TOOLS:
            return True
        if tool_name not in self._SHELL_TOOLS:
            return False
        command = str(args.get("command", "")) if isinstance(args, dict) else ""
        return bool(self._WRITE_COMMAND.search(command))

    def is_read_operation(self, tool_name: str) -> bool:
        return tool_name in {"read_file", "read_file_lines", "list_files", "search_code"}

    def snapshot(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        if not self.workspace_root.exists():
            return result
        for path in self.workspace_root.rglob("*"):
            if not path.is_file() or any(
                part in self._IGNORED_DIRS for part in path.relative_to(self.workspace_root).parts
            ):
                continue
            try:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                relative = path.relative_to(self.workspace_root).as_posix()
                result[relative] = digest.hexdigest()
            except OSError:
                continue
        return result

    @staticmethod
    def mutation(before: Dict[str, str], after: Dict[str, str]) -> WorkspaceMutation:
        changed = tuple(sorted(
            path for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        ))
        return WorkspaceMutation(changed)

    def observe_write(self, mutation: WorkspaceMutation) -> Optional[str]:
        self._last_read_key = None
        self._read_stalls = 0
        if mutation.changed:
            self._write_stalls = 0
            return None
        self._write_stalls += 1
        return None

    @property
    def write_stalled(self) -> bool:
        return self._write_stalls >= 2

    def pending_write_message(self) -> Optional[str]:
        if not self.write_stalled:
            return None
        return (
            "[System Guard: State Stalled Detected]\n"
            "Warning: 2 consecutive write operations produced 0 workspace diff.\n"
            "Action Required: Repetitive actions aborted. Re-read the file with "
            "surrounding context or change your technical approach."
        )

    def observe_read(self, tool_name: str, args: dict) -> Optional[str]:
        self._write_stalls = 0
        key = json.dumps(
            {"tool": tool_name, "args": args}, sort_keys=True, ensure_ascii=False,
        )
        if key == self._last_read_key:
            self._read_stalls += 1
        else:
            self._last_read_key = key
            self._read_stalls = 1
        if self._read_stalls < 3:
            return None
        return (
            "[System Guard: State Stalled Detected]\n"
            "Warning: 3 consecutive queries repeated the same target without new information.\n"
            "Action Required: Re-read with a different scope or change your technical approach."
        )
