"""Human-readable views over session JSONL files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


class DebugViewer:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir

    def _path(self, session_id: str | None = None) -> Path | None:
        if session_id:
            path = self.sessions_dir / f"session_{session_id}.jsonl"
            return path if path.exists() else None
        paths = sorted(self.sessions_dir.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
        return paths[-1] if paths else None

    def _events(self, session_id: str | None = None) -> Iterable[dict[str, Any]]:
        path = self._path(session_id)
        if not path:
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def render(self, mode: str = "latest", session_id: str | None = None) -> str:
        events = list(self._events(session_id))
        if not events:
            return "没有找到会话记录。"

        if mode == "flow":
            # Keep this summary useful without displaying potentially sensitive payloads.
            lines = [f"会话：{events[0].get('session_id', '-')}" ]
            counts = {"执行": 0, "失败": 0, "拦截": 0}
            for event in events:
                kind = event.get("type")
                turn = event.get("turn")
                prefix = f"第 {turn} 轮" if turn is not None else ""
                if kind in {"model_request_started", "thinking"}:
                    lines.append(f"{prefix} 模型请求".strip())
                elif kind == "tool_result":
                    status = "拦截" if event.get("blocked") else "执行" if event.get("success") else "失败"
                    counts[status] += 1
                    visibility = event.get("output_visibility") or {}
                    suffix = ""
                    if visibility.get("truncated"):
                        suffix = (
                            f"（输出仅预览 {visibility.get('visible_chars', '?')}/"
                            f"{visibility.get('original_chars', '?')} 字符）"
                        )
                    lines.append(f"  {event.get('tool', 'tool')}：{status}{suffix}")
                elif kind == "runtime_error":
                    lines.append("运行错误（详情见原始记录）")
                elif kind == "final":
                    status = event.get("status", "-")
                    lines.append("响应已返回（非任务成功判定）" if status == "RESPONSE" else f"结束：{status}")
            lines.insert(1, "工具结果：执行 {执行} / 失败 {失败} / 拦截 {拦截}".format(**counts))
            return "\n".join(lines)

        if mode == "errors":
            events = [
                event for event in events
                if event.get("type") == "log"
                or event.get("type") in {"tool_result", "runtime_error"}
                and not event.get("success", True)
            ]
            if not events:
                return "当前会话没有发现错误或拦截。"

        lines = [f"会话：{events[0].get('session_id', '-')}"]
        for event in events:
            event_type = event.get("type")
            time_text = str(event.get("time", ""))[11:19]
            if mode == "errors" and event_type == "log":
                lines.append(f"{time_text} [{event.get('level')}] {event.get('logger')}: {event.get('message')}")
            elif event_type == "user_input":
                lines.append(f"{time_text} 你：{event.get('content', '')}")
            elif event_type in {"model_request_started", "thinking"}:
                lines.append(f"{time_text} 模型请求：第 {event.get('turn')} 轮")
            elif event_type == "assistant_note":
                lines.append(f"{time_text} 模型说明：{event.get('content', '')}")
            elif event_type == "tool_call":
                lines.append(f"{time_text} 工具：{event.get('tool')} {event.get('args', '')}")
            elif event_type == "tool_result":
                status = "成功" if event.get("success") else "失败/拦截"
                lines.append(f"{time_text} 结果：{event.get('tool')} {status}")
            elif event_type == "final":
                lines.append(f"{time_text} 完成：{event.get('status', '')}")
            elif event_type == "runtime_error":
                lines.append(f"{time_text} 运行错误：{event.get('message', '')}")
        return "\n".join(lines)
