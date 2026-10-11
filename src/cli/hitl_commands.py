"""Trusted local terminal controls, using Runtime APIs exclusively."""
from __future__ import annotations
import getpass
import json

from cli.console import Command
from core.display_safety import redact, safe_text


def describe_run(agent, run_id):
    info = agent.get_checkpoint_info(run_id)
    lines = [f"Run ID: {safe_text(run_id)}", f"Run 状态: {info['status'].upper()}",
        f"Workspace: {safe_text(info['workspace'])}", f"有效 cwd: {safe_text(info['cwd'])}"]
    if info['workspace'] != str(agent.workdir.resolve()):
        lines.append("当前 Workspace 与 Checkpoint 不一致；不能借旧审批扩大权限。")
    if info['status'] == 'unknown' or any(c['state'] == 'UNKNOWN' for c in info['calls']):
        lines.append("UNKNOWN：工具副作用无法确认，恢复受阻；禁止通过批准重试，必须人工检查。")
    elif any(c['state'] == 'RUNNING' for c in info['calls']):
        lines.append("存在 RUNNING 调用，可能有其他执行者；Runtime 恢复会检查独占锁及未知副作用。")
    current = next((a for a in info['approvals'] if a.approval_id == info['approval_id']), None)
    for approval in info['approvals']:
        data = approval.to_dict()
        op = approval.decision.operation
        lines.extend([f"Approval ID: {safe_text(approval.approval_id)}",
            f"审批状态: {approval.state.value}（{'当前调用' if approval is current else '历史记录'}）", f"Tool: {safe_text(op.tool_name)}",
            f"Provider Call ID: {safe_text(op.provider_call_id)}",
            f"调用位置: turn={op.turn}, ordinal={op.ordinal}",
            f"审批 cwd: {safe_text(op.cwd)}",
            "操作参数（敏感字段仅展示脱敏，授权仍绑定原值）:",
            json.dumps(redact(op.arguments), ensure_ascii=False, indent=2),
            f"审批原因: {safe_text(approval.decision.reason)}",
            f"策略: {safe_text(approval.decision.rule_id)}"])
        if data['invalidation_reason']:
            lines.append("失效原因: " + safe_text(data['invalidation_reason']))
        if data['expires_at'] is not None:
            lines.append(f"到期时间（Unix）: {data['expires_at']}")
            if info['checked_at'] >= data['expires_at']:
                lines.append("已到期；旧批准不能执行，Runtime 将按协议处理失效。")
    if info['status'] == 'waiting_approval':
        lines.append("WAITING_APPROVAL：当前 Run 已暂停，后续工具和模型请求均未继续。")
        lines.append("风险提示：Shell / 文件修改可产生外部副作用；HITL 不提供 Shell 沙箱。")
        lines.append(f"查看 /approval {safe_text(run_id)}。")
        if current and current.state.value == 'PENDING':
            lines.append(f"决定 /approve 或 /reject {safe_text(run_id)} <Approval ID>；执行 /resume {safe_text(run_id)}。")
        elif current and current.state.value in {'APPROVED', 'REJECTED'}:
            lines.append(f"已记录 {current.state.value}；显式 /resume {safe_text(run_id)} 由 Runtime 重新校验或关闭批次并重新规划。")
        else:
            lines.append("审批已失效或消费；旧批准不能再次授权，当前没有自动核销或补偿入口。")
        lines.append("记录决定不会执行工具；可正常 exit，重新启动后使用同一数据库和 Run ID。")
    if info['status'] in {'completed', 'terminated', 'failed', 'cancelled'}:
        lines.append("Run 已终结；重复 Resume 不会重新执行工具。")
        if info['result']:
            lines.append("已保存结果: " + safe_text(info['result']))
    return '\n'.join(lines)


def _runs(agent, args):
    if args:
        return "用法: /runs"
    rows = agent.list_checkpoint_runs()
    lines = ["Checkpoint Runs（必须用明确 Run ID 操作）:"]
    for info in rows:
        marker = " [Workspace 不一致]" if info['workspace'] != str(agent.workdir.resolve()) else ""
        lines.append(f"{safe_text(info['run_id'])}  {info['status'].upper()}{marker}  Approval ID: {safe_text(info['approval_id'] or '-')}  Workspace: {safe_text(info['workspace'])}")
    return '\n'.join(lines) if rows else "当前数据库没有保存的 Run。"


def _approval(agent, args):
    return describe_run(agent, args[0]) if len(args) == 1 else "用法: /approval <Run ID>"


def _decide(agent, args, approved):
    name = 'approve' if approved else 'reject'
    if len(args) != 2:
        return f"用法: /{name} <Run ID> <Approval ID>"
    info = agent.get_checkpoint_info(args[0])
    if info['workspace'] != str(agent.workdir.resolve()):
        return "错误: 当前 Workspace 与原 Run 不一致；请在原 Workspace 中操作。"
    record = agent.decide_approval(args[0], args[1], approved=approved,
        actor='local-terminal:' + getpass.getuser())
    return f"已记录 {record.state.value}：{safe_text(record.approval_id)}；尚未执行工具。请显式 /resume {args[0]}。"


def _resume(agent, args):
    if len(args) != 1:
        return "用法: /resume <Run ID>"
    result = agent.resume_checkpoint(args[0])
    info = agent.get_checkpoint_info(args[0])
    if info['status'] == 'waiting_approval' or info['status'] in {'completed', 'terminated', 'failed', 'cancelled'}:
        return describe_run(agent, args[0])
    return safe_text(result)


def register_hitl_commands(console, agent):
    definitions = (
        ('runs', '查看已保存 Run 与待审批身份', _runs, ''),
        ('approval', '查看 Run、实际操作和审批状态', _approval, '<Run ID>'),
        ('approve', '批准原操作（不会执行）', lambda a, args: _decide(a, args, True), '<Run ID> <Approval ID>'),
        ('reject', '拒绝原操作（不会执行；恢复后重新规划）', lambda a, args: _decide(a, args, False), '<Run ID> <Approval ID>'),
        ('resume', '通过 SQLite 显式恢复原 Run', _resume, '<Run ID>'),
    )
    def invoke(callback, args):
        try:
            return callback(agent, args)
        except Exception as exc:
            return f"操作失败（{type(exc).__name__}）：{safe_text(exc)}；请用 /approval <Run ID> 核验持久化状态，不自动重试。"
    for name, help_text, callback, usage in definitions:
        console.register(Command(name, help_text,
            lambda args, context, callback=callback: invoke(callback, args),
            args_help=usage, category='hitl'))
