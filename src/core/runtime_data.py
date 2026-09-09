"""Locations for Mini-Claude data kept outside user workspaces."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeDataPaths:
    """Stable per-project storage locations for runtime artifacts."""

    root: Path
    sessions: Path
    traces: Path
    tasks: Path
    team: Path
    inbox: Path
    logs: Path

    @classmethod
    def for_workspace(
        cls, workspace: Path, data_root: Path | None = None
    ) -> "RuntimeDataPaths":
        """Return runtime paths for *workspace*.

        ``data_root`` is an explicit escape hatch for isolated evaluation
        runs.  It is deliberately not inferred from cwd and does not change
        the normal CLI layout when omitted.
        """
        workspace = workspace.resolve()
        if data_root is None:
            configured = os.getenv("MINI_CLAUDE_DATA_DIR")
            if configured:
                data_root = Path(configured).expanduser()
            elif sys.platform == "win32":
                data_root = Path(r"D:\02_study\code\mini-claude-project-data")
            else:
                data_root = Path.home() / ".local" / "share" / "mini-claude"

            digest = hashlib.sha256(str(workspace).lower().encode("utf-8")).hexdigest()[:8]
            project_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in workspace.name)
            root = data_root / f"{project_name}-{digest}"
        else:
            root = Path(data_root).expanduser().resolve()
        return cls(
            root=root,
            sessions=root / "sessions",
            traces=root / "traces",
            tasks=root / "tasks",
            team=root / "team",
            inbox=root / "inbox",
            logs=root / "logs",
        )
