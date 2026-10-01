from pathlib import Path

from agent.mini_claude_agent import MiniClaudeAgent
from core.runtime_context.workspace_authority import WorkspaceAuthority
from core.tools.base_tools import BaseTools


def test_runtime_modules_import_workspace_authority():
    assert WorkspaceAuthority is not None
    assert BaseTools is not None
    assert MiniClaudeAgent is not None


def test_workspace_authority_allows_primary_root_and_rejects_outside(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    authority = WorkspaceAuthority(primary_root=workspace)

    assert authority.is_authorized(workspace / "inside.txt")
    assert not authority.is_authorized(tmp_path / "outside.txt")
    assert authority.check("inside.txt") == (workspace / "inside.txt").resolve()

    try:
        authority.check(str(tmp_path / "outside.txt"))
    except ValueError as exc:
        assert str(exc) == f"路径超出工作区权限: {tmp_path / 'outside.txt'}"
    else:
        raise AssertionError("workspace 外路径应被拒绝")


def test_workspace_authority_allows_added_root(tmp_path: Path):
    workspace = tmp_path / "workspace"
    additional = tmp_path / "additional"
    workspace.mkdir()
    additional.mkdir()
    authority = WorkspaceAuthority(primary_root=workspace)

    authority.add_root(str(additional))

    assert authority.is_authorized(additional / "file.txt")
    assert authority.check(str(additional / "file.txt")) == (additional / "file.txt").resolve()
