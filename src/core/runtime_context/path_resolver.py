"""
PathResolver — resolves relative paths against workspace_root.

Rules:
  - Relative path  → workspace_root / relative_path
  - Absolute path  → kept as-is
  - "~" / "~user" → resolved via Path.home()
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional


class PathResolver:
    """Resolve tool-provided paths against a fixed workspace root."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def resolve(self, path: str) -> Path:
        """Resolve a user-supplied path against workspace_root.

        Args:
            path: A file path (relative or absolute).

        Returns:
            Resolved absolute Path.
        """
        p = Path(path)

        # Home-directory expansion
        if path.startswith("~"):
            return p.expanduser().resolve()

        # Absolute path → keep as-is
        if p.is_absolute():
            return p.resolve()

        # Relative path → join with workspace_root
        return (self.workspace_root / p).resolve()
