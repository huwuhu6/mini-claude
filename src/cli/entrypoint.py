"""Command-line entry point for mini-claude."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional


# Allow ``python -m cli.entrypoint`` to work directly from a source checkout.
_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from cli.confirmation import confirm_workspace
from agent.mini_claude_agent import MiniClaudeAgent

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mini-Claude Runtime - workspace-bound AI agent",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Workspace directory path (default: current directory)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip workspace confirmation (for CI and automation)",
    )
    return parser.parse_args(argv)


def _run_repl(agent: MiniClaudeAgent) -> None:
    print(f"\n{agent.config.agent.name} v{agent.config.agent.version}")
    print("输入 exit 退出，输入 /help 查看命令。\n")

    while True:
        try:
            user_input = input("> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("再见。")
                break

            response = agent.chat(user_input)
            if response:
                print(f"\n{response}")
        except (KeyboardInterrupt, EOFError):
            print("\n再见。")
            break
        except SystemExit:
            break
        except Exception as exc:
            print(f"\n错误：{exc}")
            logger.exception("REPL failed")

    agent.shutdown()


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    workspace = Path(args.path).resolve()

    if not sys.stdin.isatty() and not args.yes:
        print("非交互环境需要使用 --yes。", file=sys.stderr)
        raise SystemExit(1)

    if not (args.yes or confirm_workspace(workspace)):
        print("未启动。")
        raise SystemExit(0)

    agent = MiniClaudeAgent(
        workspace_root=workspace,
        workspace_confirmed=True,
    )
    _run_repl(agent)


if __name__ == "__main__":
    main()
