"""Command-line entry point for mini-claude."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

_src = Path(__file__).resolve().parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from agent.mini_claude_agent import MiniClaudeAgent
from cli.confirmation import confirm_workspace
from cli.ui import TerminalUI
from core.debug_viewer import DebugViewer
from core.runtime_data import RuntimeDataPaths

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mini-Claude Runtime - workspace-bound AI agent",
    )
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip workspace confirmation (for CI and automation)",
    )
    parser.add_argument(
        "--debug",
        choices=("latest", "errors", "flow"),
        help="View a recorded session without starting the Agent",
    )
    parser.add_argument("--session", help="Session id used with --debug")
    return parser.parse_args(argv)


def _run_repl(agent: MiniClaudeAgent) -> None:
    ui = TerminalUI()
    agent.set_ui_event_handler(ui.handle_event)
    print(f"\n{ui._paint(agent.config.agent.name, ui._BLUE)} v{agent.config.agent.version}")
    print(ui._paint("输入 exit 退出，输入 /help 查看命令。\n", ui._DIM))

    while True:
        try:
            user_input = input(ui.prompt()).strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("再见。")
                break

            response = agent.chat(user_input)
            if response:
                ui.print_answer(response)
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

    if args.debug:
        paths = RuntimeDataPaths.for_workspace(workspace)
        print(DebugViewer(paths.sessions).render(args.debug, args.session))
        return

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
