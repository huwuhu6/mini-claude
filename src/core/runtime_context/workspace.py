"""
RuntimeContext — top-level workspace binding for the agent.

Aggregates:
  - workspace_root
  - current_task_id
  - ShellSession (persistent cwd)
  - PathResolver (relative path resolution)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .shell_session import ShellSession
from .path_resolver import PathResolver


@dataclass
class RuntimeContext:
    """Runtime workspace binding for the Coding Agent.

    Created once at agent init and wired into all tool handlers.
    """
    workspace_root: Path
    current_task_id: str = ""
    shell_session: Optional[ShellSession] = None
    path_resolver: Optional[PathResolver] = None

    def __post_init__(self):
        self.workspace_root = self.workspace_root.resolve()
        if self.shell_session is None:
            self.shell_session = ShellSession(self.workspace_root)
        if self.path_resolver is None:
            self.path_resolver = PathResolver(self.workspace_root)

    @property
    def cwd(self) -> Path:
        """Current working directory (from shell session)."""
        if self.shell_session:
            return self.shell_session.cwd
        return self.workspace_root

    def resolve_path(self, path: str) -> Path:
        """Resolve a path against workspace_root via PathResolver."""
        if self.path_resolver:
            return self.path_resolver.resolve(path)
        return Path(path).resolve()
