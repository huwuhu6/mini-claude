"""
CLI Entrypoint for mini-claude.

Usage:
    mini-claude                    # defaults to Path.cwd()
    mini-claude .
    mini-claude ./project
    mini-claude /abs/path
    mini-claude . --yes            # skip confirmation (CI/automation)
    mini-claude . -y               # same as --yes
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure src is importable when running via `python -m cli.entrypoint`
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from cli.confirmation import confirm_workspace
from agent.mini_claude_agent import MiniClaudeAgent

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Mini-Claude Runtime — workspace-bound AI agent",
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="Workspace directory path (default: current directory)",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="跳过工作区确认（用于 CI / 自动化场景）",
    )
    return parser.parse_args(argv)


def _run_repl(agent: MiniClaudeAgent) -> None:
    """Interactive REPL loop — same as the original agent main()."""
    print(f"\n=== {agent.config.agent.name} v{agent.config.agent.version} ===")
    print("输入 'exit' 或 '/exit' 退出，'/help' 查看命令\n")
    agent.print_status()

    while True:
        try:
            user_input = input("\n你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("再见！")
                break
            response = agent.chat(user_input)
            if response:
                print(f"\n代理: {response}")
        except (KeyboardInterrupt, EOFError):
            print("\n\n再见！")
            break
        except SystemExit:
            break
        except Exception as e:
            print(f"\n错误: {e}")
            logger.exception("主循环出错")

    agent.shutdown()


def main(argv: Optional[list[str]] = None) -> None:
    """Entrypoint: resolve workspace, confirm, launch agent."""
    args = parse_args(argv)

    # ── Resolve workspace path ──────────────────────────────────
    workspace = Path(args.path).resolve()

    # ── Non-interactive guard ───────────────────────────────────
    if not sys.stdin.isatty() and not args.yes:
        print(
            "Non-interactive environment requires --yes",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Runtime Ownership Boundary ──────────────────────────────
    confirmed = args.yes or confirm_workspace(workspace)
    if not confirmed:
        print("Runtime 未启动。")
        sys.exit(0)

    # ── Launch agent ────────────────────────────────────────────
    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
    )
    _run_repl(agent)


if __name__ == "__main__":
    main()
