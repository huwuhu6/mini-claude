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
    approvals = parser.add_mutually_exclusive_group()
    approvals.add_argument("--hitl", dest="hitl", action="store_true", help="启用审批（默认开启）")
    approvals.add_argument("--no-hitl", dest="hitl", action="store_false", help="显式关闭审批与会话持久化")
    parser.set_defaults(hitl=True)
    parser.add_argument("--checkpoint-db", type=Path, help="原 Checkpoint 数据库路径；默认使用工作区的数据目录")
    parser.add_argument("--init-checkpoints", action="store_true", help="显式允许首次创建 Checkpoint 数据库")
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument("--resume", nargs="?", const="", metavar="CONVERSATION_ID", help="选择历史会话，或打开指定会话")
    sessions.add_argument("--continue", dest="continue_session", action="store_true", help="继续当前工作区最近的会话")
    parser.add_argument("--config", type=Path, help="Agent 配置文件（恢复时需保持兼容）")
    args = parser.parse_args(argv)
    if not args.hitl and (args.checkpoint_db or args.init_checkpoints or args.resume is not None or args.continue_session):
        parser.error("关闭审批时不能初始化数据库或恢复持久化会话")
    return args


def _select_session(agent, *, latest=False):
    rows = agent.list_conversations()
    if not rows:
        raise RuntimeError('当前工作区没有历史会话；请直接运行 mini-claude 开始新会话')
    if latest:
        return rows[0]['conversation_id']
    current = agent.get_session_info()['conversation_id'] if hasattr(agent, 'get_session_info') else None
    rows = [dict(row, current=row['conversation_id'] == current) for row in rows]
    return TerminalUI().choose_session(rows)


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


def _present_response(agent, ui, response) -> bool:
    """真实终端自动接续单次决定；持久化与执行仍由 Runtime 分别负责。"""
    from cli.hitl_commands import describe_run
    import getpass
    def notice(text):
        getattr(ui, 'print_control', ui.print_answer)(text)
    if response.startswith('已加载会话只读历史'):
        notice(response)
        return False
    if not getattr(agent, '_hitl_enabled', False) or not sys.stdin.isatty():
        if response:
            ui.print_answer(response)
        return False
    if response.startswith('错误:'):
        notice(response)
        return False
    while True:
        state = getattr(agent, '_run_state', None)
        if not state or state.status.value != 'waiting_approval':
            if response:
                if response.startswith('已加载会话上下文'):
                    notice(response)
                else:
                    ui.print_answer(response)
            return False
        run_id = state.run_id
        approval = agent.get_pending_approval(run_id)
        if approval.state.value != 'PENDING':
            if approval.state.value in {'APPROVED', 'REJECTED'} and hasattr(ui, 'continuation_choice'):
                try:
                    if ui.continuation_choice(approval.state.value) == 'resume':
                        response = agent.resume_checkpoint(run_id)
                        continue
                except (EOFError, KeyboardInterrupt):
                    return True
            notice(describe_run(agent, run_id))
            return False
        try:
            from core.permission_grants import can_remember
            reusable = approval.decision.rule_id != 'file.sensitive' and can_remember(approval.decision.operation)
            choice = ui.approval_choice(approval.decision.operation, approval.decision.reason, allow_rules=reusable)
        except (EOFError, KeyboardInterrupt):
            choice = 'q'
        if choice == 'd':
            if hasattr(ui, 'show_approval_details'):
                ui.show_approval_details(approval.decision.operation, describe_run(agent, run_id))
            else:
                ui.print_answer(describe_run(agent, run_id))
            continue
        if choice in {'', 'p', 'q', 'exit', 'quit'}:
            notice("任务已保持暂停；重启后运行 mini-claude --continue 即可重新打开。")
            return choice in {'q', 'exit', 'quit'}
        if choice not in {'1', 'y', 'yes', '2', '3', '4', 'n', 'no', '5'}:
            notice('请选择菜单操作；尚未记录审批决定。')
            continue
        if choice in {'2', '3'} and not reusable:
            notice('此操作仅支持单次确认；尚未记录决定。')
            continue
        approved = choice in {'1', 'y', 'yes', '2', '3'}
        if choice in {'2', '3'} and not ui.confirm_rule(approval.decision.operation, persistent=choice == '3'):
            continue
        feedback = None
        if not approved:
            note = ui.read_approval_feedback() if choice == '5' else '用户拒绝原操作并结束本轮'
            if not note:
                continue
            feedback = {'mode': 'replan' if choice == '5' else 'stop', 'note': note}
        # 精确绑定刚刚展示的 ID。记录失败时绝不继续执行或自动重试。
        agent.decide_approval(run_id, approval.approval_id,
            approved=approved, actor='local-terminal:' + getpass.getuser(), feedback=feedback)
        if choice in {'2', '3'}:
            agent.remember_approval(run_id, approval.approval_id, 'conversation' if choice == '2' else 'workspace')
        if hasattr(ui, 'print_control'):
            scope = '本会话规则' if choice == '2' else '工作区规则' if choice == '3' else '仅此次调用'
            ui.print_control(f"已{'允许 · ' + scope if approved else '拒绝'} · {approval.decision.operation.tool_name}")
        response = agent.resume_checkpoint(run_id)


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
    if hasattr(ui, 'show_session'):
        ui.show_session(agent.get_session_info())

    if resume_run:
        from core.checkpoint_store import CheckpointError
        from core.display_safety import safe_text
        try:
            response = agent.open_conversation(resume_run)
            if hasattr(ui, 'show_history'):
                ui.show_session(agent.get_session_info())
                ui.show_history(agent.get_conversation_preview())
                if response.startswith('已加载会话上下文'):
                    response = response.split('\n', 1)[0]
            if _present_response(agent, ui, response):
                return
        except (CheckpointError, ValueError) as exc:
            ui.print_answer(f"恢复受阻：{safe_text(exc)}。不会重试原操作。\n"
                "可查看 /runs；选中的 UNKNOWN / 失效任务经人工检查后可用 /archive <处理原因> 归档。")

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

            control = user_input.startswith('/')
            if not control and hasattr(ui, 'begin_turn'):
                ui.begin_turn()
            response = _dispatch_input(agent, console, user_input)
            resuming = control and user_input.split()[0].lower() == '/resume'
            if resuming and response.startswith('已加载会话上下文') and hasattr(ui, 'show_history'):
                ui.show_history(agent.get_conversation_preview())
                response = response.split('\n', 1)[0]
            # Status/permissions are inspect-only, not a request to resume a dialog.
            if control and not resuming and hasattr(ui, 'print_control'):
                ui.print_control(response)
            elif _present_response(agent, ui, response):
                break
            if hasattr(ui, 'show_session'):
                ui._session_info = agent.get_session_info()
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
        restoring = args.resume is not None or args.continue_session
        if args.hitl and not existing and (restoring or (args.checkpoint_db and not args.init_checkpoints)):
            raise RuntimeError("Checkpoint 数据库不存在；恢复不会创建空库。首次新任务请显式使用 --init-checkpoints。")
        options = {"workspace_root": workspace, "workspace_confirmed": True}
        if args.config:
            options['config_path'] = args.config
        agent = MiniClaudeAgent(**options)
        if args.hitl:
            agent.enable_checkpoints(database, require_existing=existing or restoring)
            agent.enable_hitl()
            agent.session_recorder.redact_display = True
            from core.display_safety import DisplayLogFilter, safe_text
            for handler in logging.getLogger().handlers:
                filter_ = DisplayLogFilter()
                handler.addFilter(filter_)
                filters.append((handler, filter_))
            print(f"Provider: {safe_text(agent.config.llm.provider)} / {safe_text(agent.config.llm.model)}")
            print(f"工作区：{safe_text(workspace)} · 审批：标准确认 · 会话：自动保存")
        selected = args.resume
        if args.continue_session or args.resume == '':
            selected = _select_session(agent, latest=args.continue_session)
            if selected is None:
                return
        _run_repl(agent, selected)
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
