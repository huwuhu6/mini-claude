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
from cli.commands import create_agent_console, execute_agent_command
from cli.console import ConsoleCommandSystem
from cli.ui import TerminalUI, confirm_exit
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
    parser.add_argument("--hitl", action="store_true", help="显式启用本地 HITL（需要 SQLite Checkpoint）")
    parser.add_argument("--checkpoint-db", type=Path, help="原 Checkpoint 数据库路径；默认使用工作区的数据目录")
    parser.add_argument("--init-checkpoints", action="store_true", help="显式允许首次创建 Checkpoint 数据库")
    parser.add_argument("--resume", metavar="RUN_ID", help="启动后显式恢复指定 Run（不会重新生成原调用）")
    parser.add_argument("--config", type=Path, help="Agent 配置文件（恢复时需保持兼容）")
    args = parser.parse_args(argv)
    if not args.hitl and (args.checkpoint_db or args.init_checkpoints or args.resume):
        parser.error("--checkpoint-db / --init-checkpoints / --resume 要求 --hitl")
    return args


def _read_multiline(ui: TerminalUI, read_line=input) -> str:
    """Read pasted text until the explicit ``/end`` marker."""
    print(ui._paint("多行输入模式：粘贴或输入内容，单独输入 /end 提交。", ui._DIM))
    lines: list[str] = []
    while True:
        line = read_line(ui.multiline_prompt())
        if line.strip() == "/end":
            return "\n".join(lines).strip()
        lines.append(line)


def _dispatch_input(
    agent: MiniClaudeAgent,
    console: ConsoleCommandSystem,
    user_input: str,
) -> str:
    if user_input.startswith('/'):
        return execute_agent_command(console, agent, user_input)
    result = agent.chat(user_input)
    if getattr(agent, "_hitl_enabled", False):
        from cli.hitl_commands import describe_run
        from core.display_safety import safe_text
        state = getattr(agent, "_run_state", None)
        if state and state.status.value == 'waiting_approval':
            detail = describe_run(agent, state.run_id)
            return safe_text(result) + '\n' + detail if result.startswith('错误:') else detail
        return safe_text(result)
    return result


def _run_repl(agent: MiniClaudeAgent, resume_run: str | None = None) -> None:
    ui = TerminalUI()
    console = create_agent_console(agent)
    if getattr(agent, "_hitl_enabled", False):
        from core.display_safety import redact
        agent.set_ui_event_handler(lambda event, data: ui.handle_event(event, redact(data)))
    else:
        agent.set_ui_event_handler(ui.handle_event)
    print(f"\n{ui._paint(agent.config.agent.name, ui._BLUE)} v{agent.config.agent.version}")
    print(ui._paint("输入 exit 退出，输入 /help 查看命令。\n", ui._DIM))

    if resume_run:
        ui.print_answer(_dispatch_input(agent, console, '/resume ' + resume_run))

    while True:
        try:
            user_input = ui.read_input()
            if not ui.prompt_toolkit_available and user_input.strip().lower() in ("/paste", "/multiline"):
                user_input = _read_multiline(ui)
            else:
                user_input = user_input.strip()
        except KeyboardInterrupt:
            if confirm_exit():
                print("再见。")
                break
            print("已取消退出，继续输入。")
            continue
        except EOFError:
            print("\n再见。")
            break

        try:
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("再见。")
                break

            response = _dispatch_input(agent, console, user_input)
            if response:
                ui.print_answer(response)
        except KeyboardInterrupt:
            # Agent normally converts this into a cancelled response. This
            # fallback keeps the REPL usable if an outer boundary is hit.
            if getattr(agent, "_hitl_enabled", False):
                print("\n操作已中断；持久化状态以 /approval <Run ID> 为准，不自动重试。")
            else:
                print("\n当前任务已停止，返回输入。")
            logger.warning("REPL task interrupted by user")
            continue
        except SystemExit:
            break
        except Exception as exc:
            if getattr(agent, "_hitl_enabled", False):
                from core.display_safety import safe_text
                print(f"\n错误（{type(exc).__name__}）：{safe_text(exc)}；请查看原 Run，不能自动重试。")
                logger.warning("HITL CLI operation failed: %s", type(exc).__name__)
            else:
                print(f"\n错误：{exc}")
                logger.exception("REPL failed")



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

    agent = None
    filters = []
    try:
        database = (args.checkpoint_db or RuntimeDataPaths.for_workspace(workspace).checkpoints).expanduser().resolve()
        existing = database.is_file()
        if args.hitl and not existing and (args.resume or not args.init_checkpoints):
            raise RuntimeError("Checkpoint 数据库不存在；恢复不会创建空库。首次新任务请显式使用 --init-checkpoints。")
        options = {"workspace_root": workspace, "workspace_confirmed": True}
        if args.config:
            options['config_path'] = args.config
        agent = MiniClaudeAgent(**options)
        if args.hitl:
            agent.enable_checkpoints(database, require_existing=not args.init_checkpoints or bool(args.resume))
            agent.enable_hitl()
            agent.session_recorder.redact_display = True
            from core.display_safety import DisplayLogFilter, safe_text
            for handler in logging.getLogger().handlers:
                filter_ = DisplayLogFilter()
                handler.addFilter(filter_)
                filters.append((handler, filter_))
            print(f"HITL 已启用；Checkpoint: {safe_text(database)}（{'已有数据库' if existing else '显式新建数据库'}）。")
            print("使用 /runs 查询 Run ID；批准 / 拒绝仅记录决定，之后必须显式 /resume。")
        _run_repl(agent, args.resume)
    except Exception as exc:
        if not args.hitl:
            raise
        from core.display_safety import safe_text
        print(f"HITL 启动失败（{type(exc).__name__}）：{safe_text(exc)}；未降级为无审批执行。", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        try:
            if agent is not None:
                agent.shutdown()
        finally:
            for handler, filter_ in filters:
                handler.removeFilter(filter_)


if __name__ == "__main__":
    main()
