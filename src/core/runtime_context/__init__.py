"""
Runtime Context — Workspace Binding + Persistent Shell Session.

Provides:
  - RuntimeContext (workspace_root, shell_session, path_resolver)
  - PathResolver (relative → absolute path resolution)
  - ShellSession (persistent cwd, env, command history)
  - CommandPolicy (rule-based, replaces blanket shell control char ban)

Usage:
    ctx = RuntimeContext(workspace_root=Path("/projects/myapp"))
    ctx.shell_session.execute("cd src")
    ctx.shell_session.execute("python main.py")      # runs in /projects/myapp/src
    abs_path = ctx.path_resolver.resolve("main.py")   # → /projects/myapp/src/main.py
"""
from .workspace import RuntimeContext
from .path_resolver import PathResolver
from .shell_session import ShellSession
from .command_policy import CommandPolicy
from .preflight import PreflightResult, run_preflight
from .environment_guard import EnvironmentBlock, EnvironmentBlocker
from .workspace_state import WorkspaceMutation, WorkspaceStateGuard

__all__ = [
    'RuntimeContext',
    'PathResolver',
    'ShellSession',
    'CommandPolicy',
    'PreflightResult', 'run_preflight',
    'EnvironmentBlock', 'EnvironmentBlocker',
    'WorkspaceMutation', 'WorkspaceStateGuard',
]
