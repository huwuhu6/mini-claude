"""
Workspace Confirmation — Runtime Ownership Boundary.

The confirmation is NOT a trivial y/n prompt.
It is a deliberate consent gate: the user explicitly authorises
the agent to create files, modify code, and run commands in the
given directory before any runtime action occurs.
"""
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def confirm_workspace(path: Path) -> bool:
    """Prompt the user to confirm the workspace directory.

    Returns:
        True if the user typed 'y' or 'yes' (case-insensitive).
        False for any other input (including EOF / keyboard interrupt).
    """
    resolved = path.resolve()

    print()
    print("━" * 50)
    print("  Mini-Claude Runtime")
    print("━" * 50)
    print(f"\n  Workspace: {resolved}")
    print("\n  该 Runtime 将可能：")
    print("  • 创建文件")
    print("  • 修改代码")
    print("  • 执行 shell 命令")
    print("  • 安装依赖")
    print()
    print("  确认使用该目录作为 Runtime Workspace？")

    try:
        answer = input("  [y/n] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in ("y", "yes"):
        logger.info(f"Workspace confirmed: {resolved}")
        return True

    logger.info(f"Workspace rejected by user: {resolved}")
    return False
