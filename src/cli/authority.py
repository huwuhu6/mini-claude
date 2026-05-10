"""
WorkspaceAuthority — unified path permission model.

Replaces the scattered whitelist logic in base_tools.py with a single
authority boundary: primary_root + additional_roots.

Usage:
    authority = WorkspaceAuthority(primary_root=Path("/project"))
    authority.is_authorized(Path("/project/src/file.py"))   # True
    authority.is_authorized(Path("/etc/passwd"))             # False
    authority.add_root("/outside/path")                      # extend boundary
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional


def _is_relative_to(path: Path, base: Path) -> bool:
    """Check if *path* is under *base*, with Python version compatibility."""
    try:
        return path.is_relative_to(base)
    except AttributeError:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


class WorkspaceAuthority:
    """Ownership boundary for the agent workspace.

    Attributes:
        primary_root: The single workspace root (auto-granted).
        additional_roots: User-authorized paths outside primary_root.
    """

    def __init__(self, primary_root: Path):
        self.primary_root = primary_root.resolve()
        self.additional_roots: List[Path] = []

    # ── Core Check ─────────────────────────────────────────────────

    def is_authorized(self, path: Path) -> bool:
        """Return True if *path* falls within the authority boundary."""
        resolved = path.resolve()
        if _is_relative_to(resolved, self.primary_root):
            return True
        for root in self.additional_roots:
            if _is_relative_to(resolved, root):
                return True
        return False

    def check(self, path: str) -> Path:
        """Validate and resolve *path* against the authority boundary.

        Returns:
            Resolved Path if authorized.

        Raises:
            ValueError: If the path is outside the workspace boundary.
        """
        p = Path(path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.primary_root / p).resolve()

        if not self.is_authorized(resolved):
            raise ValueError(f"路径超出工作区权限: {path}")
        return resolved

    # ── Boundary Management ────────────────────────────────────────

    def add_root(self, path_str: str) -> str:
        """Add a directory to additional_roots.  Returns user-facing message."""
        resolved = Path(path_str).resolve()
        if not resolved.exists():
            return f"错误: 路径不存在: {path_str}"
        if not resolved.is_dir():
            return f"错误: 路径不是目录: {path_str}"
        if resolved in self.additional_roots:
            return f"路径已在权限中: {resolved}"
        self.additional_roots.append(resolved)
        return f"已添加路径到权限: {resolved}"

    def remove_root(self, path_str: str) -> str:
        """Remove a directory from additional_roots.  Returns user-facing message."""
        resolved = Path(path_str).resolve()
        if resolved in self.additional_roots:
            self.additional_roots.remove(resolved)
            return f"已从权限移除路径: {resolved}"
        return f"路径不在权限中: {resolved}"

    def list_roots(self) -> str:
        """List all authorized roots.  Returns user-facing message."""
        if not self.additional_roots:
            return f"权限列表为空（仅 primary_root: {self.primary_root}）"
        lines = ["=== 工作区权限 ==="]
        lines.append(f"  primary: {self.primary_root}")
        for i, p in enumerate(self.additional_roots, 1):
            lines.append(f"  additional {i}: {p}")
        return '\n'.join(lines)
