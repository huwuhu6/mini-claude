"""
SubAgent System - Spawn isolated sub-agents for parallel work.
"""
from __future__ import annotations
import logging
import json
import uuid
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

from providers.base import LLMProvider, Message, ToolDefinition
from providers.manager import ProviderManager
from core.tools.base_tools import BaseTools, ToolResult
from core.loop_guard import LoopGuard

logger = logging.getLogger(__name__)


class SubAgentType(Enum):
    EXPLORE = "explore"          # Fast read-only search
    GENERAL = "general-purpose"  # Full tool access
    PLAN = "plan"                # Architecture planning
    REVIEW = "review"            # Code review


@dataclass
class SubAgentResult:
    task_id: str = ""
    content: str = ""
    success: bool = True
    error: str = ""
    usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'content': self.content,
            'success': self.success,
            'error': self.error,
            'usage': self.usage,
        }


class SubAgent:
    """An isolated sub-agent with its own tools and session context."""

    def __init__(self, agent_type: SubAgentType = SubAgentType.GENERAL,
                 workdir: Optional[Path] = None,
                 provider: Optional[LLMProvider] = None,
                 model: str = "",
                 authority=None):
        self.agent_type = agent_type
        self.workdir = workdir or Path.cwd()
        self.tools = BaseTools(self.workdir, authority=authority)
        self.provider = provider
        self.model = model or getattr(provider, 'model', '')
        self.messages: List[Message] = []
        self.loop_guard = LoopGuard()

    def get_tools(self) -> List[Dict[str, Any]]:
        """Get tools available based on agent type."""
        all_tools = [
            {
                'name': 'bash',
                'description': 'Run a shell command.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'command': {'type': 'string', 'description': 'The command to run'},
                    },
                    'required': ['command']
                }
            },
            {
                'name': 'read_file',
                'description': 'Read file contents with optional line limit.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file'},
                        'limit': {'type': 'integer', 'description': 'Maximum lines to read'},
                    },
                    'required': ['path']
                }
            },
            {
                'name': 'write_file',
                'description': 'Write content to a file.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file'},
                        'content': {'type': 'string', 'description': 'Content to write'},
                    },
                    'required': ['path', 'content']
                }
            },
            {
                'name': 'edit_file',
                'description': 'Replace exact text in a file.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file'},
                        'old_text': {'type': 'string', 'description': 'Text to replace'},
                        'new_text': {'type': 'string', 'description': 'Replacement text'},
                    },
                    'required': ['path', 'old_text', 'new_text']
                }
            },
        ]

        # Explore type get only read tools
        if self.agent_type == SubAgentType.EXPLORE:
            return [t for t in all_tools if t['name'] in ('read_file',)]

        return all_tools

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool and return result string (dict dispatch)."""
        handlers = {
            'bash':       lambda a: self.tools.run_bash(a['command']),
            'read_file':  lambda a: self.tools.read_file(a['path'], a.get('limit')),
            'write_file': lambda a: self.tools.write_file(a['path'], a['content']),
            'edit_file':  lambda a: self.tools.edit_file(a['path'], a['old_text'], a['new_text']),
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            result = handler(arguments)
            return result.content if isinstance(result, ToolResult) else str(result)
        except Exception as e:
            return f"Error: {str(e)}"

    def run(self, prompt: str, max_iterations: int = 30,
            fail_after_tool_calls: int = 0) -> SubAgentResult:
        """Execute the sub-agent with a multi-turn tool loop.

        Mirrors ``MiniClaudeAgent._llm_tool_cycle``: loops up to
        *max_iterations* iterations, calling the LLM and executing any
        returned tool calls until the LLM produces a plain text
        response (no tool_calls) or the iteration budget is exhausted.

        If *fail_after_tool_calls* > 0, the subagent will inject a
        ``RuntimeError`` after that many tool calls have been executed
        (counting across all iterations), simulating an abrupt failure.
        """
        task_id = str(uuid.uuid4())[:8]
        self.messages.append(Message(role='user', content=prompt))

        if not self.provider:
            return SubAgentResult(
                task_id=task_id, content="",
                success=False, error="No provider available",
            )

        try:
            tools = self.get_tools()
            tool_defs = [ToolDefinition(**t) for t in tools]
            total_usage: Dict[str, int] = {}
            final_content = ""
            tool_call_total = 0

            for iteration in range(max_iterations):
                # ── LLM call ─────────────────────────────────
                response = self.provider.create_message(self.messages, tool_defs)
                parsed = self._parse_provider_response(response)
                content = parsed.get('content', '')
                tool_calls = parsed.get('tool_calls', [])
                usage = parsed.get('usage', {})

                # Accumulate usage across iterations
                for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                    total_usage[k] = total_usage.get(k, 0) + usage.get(k, 0)

                # ── No tool calls → final response ──────────
                if not tool_calls:
                    self.messages.append(Message(role='assistant', content=content))
                    final_content = content
                    break

                # ── Store assistant message with tool_calls ─
                self.messages.append(Message(
                    role='assistant', content=content, tool_calls=tool_calls,
                ))

                # ── Execute each tool, store results ─────────
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    tname = fn.get('name', '')
                    args_raw = fn.get('arguments', '{}')
                    args_parse_error = None
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except json.JSONDecodeError as e:
                            args = {}
                            args_parse_error = (
                                f"JSON 解析失败 — LLM 返回了非法参数格式。"
                                f"原始内容: {args_raw[:200]}。"
                                f"解析错误: {e}。"
                                f"请严格按照工具 schema 要求的 JSON 格式重新调用。"
                            )
                    else:
                        args = args_raw

                    if args_parse_error:
                        result_str = args_parse_error
                    else:
                        # ── Loop guard: intercept before execution ─
                        loop_msg = self.loop_guard.check(tname, args)
                        if loop_msg:
                            result_str = loop_msg
                        else:
                            result_str = self.execute_tool(tname, args)
                    # Record every call (executed or intercepted) for pattern tracking
                    if not args_parse_error:
                        self.loop_guard.record(tname, args)
                    logger.debug(f"  [sub:{task_id}] {tname}: {result_str[:120]}")
                    self.messages.append(Message(
                        role='tool', content=result_str,
                        tool_call_id=tc.get('id', ''),
                    ))
                    tool_call_total += 1

                # ── Fault injection: force-fail after N tool calls ─
                if (fail_after_tool_calls
                        and tool_call_total >= fail_after_tool_calls):
                    raise RuntimeError(
                        f"SubAgent fault injection: forced failure after "
                        f"{tool_call_total} tool calls"
                    )

                final_content = content  # last assistant content
            else:
                # Max iterations exhausted → explicit failure
                logger.warning(f"子代理 {task_id} 达到最大迭代次数 ({max_iterations})")
                final_content = (
                    final_content
                    or "(subagent reached maximum iterations without final response)"
                )
                return SubAgentResult(
                    task_id=task_id,
                    content=final_content,
                    success=False,
                    usage=total_usage,
                    error=f"迭代预算耗尽（{max_iterations}轮），"
                          f"子代理未能在限定轮数内返回最终结果。",
                )

            # Normal completion: loop exited via break (no tool_calls)
            return SubAgentResult(
                task_id=task_id,
                content=final_content,
                success=True,
                usage=total_usage,
            )

        except Exception as e:
            logger.error(f"子代理 {task_id} 出错: {e}")
            return SubAgentResult(
                task_id=task_id, content="",
                success=False, error=str(e),
            )

    def _parse_provider_response(self, response: Any) -> Dict[str, Any]:
        """Try to parse response, handling both Deepseek and Anthropic formats."""
        # If provider has parse_response method
        if hasattr(self.provider, 'parse_response'):
            return self.provider.parse_response(response)
        # Fallback: try to extract from raw response
        try:
            if hasattr(response, 'choices'):
                msg = response.choices[0].message
                content = msg.content or ""
                tool_calls = []
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append({
                            'id': tc.id,
                            'type': tc.type,
                            'function': {
                                'name': tc.function.name,
                                'arguments': tc.function.arguments,
                            }
                        })
                usage = {}
                if hasattr(response, 'usage'):
                    u = response.usage
                    usage = {
                        'prompt_tokens': getattr(u, 'prompt_tokens', 0),
                        'completion_tokens': getattr(u, 'completion_tokens', 0),
                        'total_tokens': getattr(u, 'total_tokens', 0),
                    }
                return {'content': content, 'tool_calls': tool_calls, 'usage': usage}
        except Exception:
            pass
        return {'content': str(response), 'tool_calls': [], 'usage': {}}


class SubAgentManager:
    """Manages creation and execution of sub-agents."""

    def __init__(self, provider_manager: ProviderManager, workdir: Optional[Path] = None,
                 authority=None):
        self.provider_manager = provider_manager
        self.workdir = workdir or Path.cwd()
        self._authority = authority
        self._results: Dict[str, SubAgentResult] = {}

    def spawn(self, agent_type: SubAgentType = SubAgentType.GENERAL,
              provider_name: Optional[str] = None,
              workdir: Optional[Path] = None) -> SubAgent:
        """Create a new sub-agent instance.

        If *workdir* is given the subagent's tools operate inside that
        directory; otherwise the manager's default workdir is used.
        """
        provider = None
        if provider_name:
            provider = self.provider_manager.providers.get(provider_name)
        if not provider:
            provider = self.provider_manager.get_primary_provider()
        model = getattr(provider, 'model', '') if provider else ''
        return SubAgent(
            agent_type=agent_type,
            workdir=workdir or self.workdir,
            provider=provider,
            model=model,
            authority=self._authority,
        )

    def run(self, prompt: str, agent_type: SubAgentType = SubAgentType.GENERAL,
            provider_name: Optional[str] = None,
            workdir: Optional[Path] = None,
            max_iterations: int = 30,
            fail_after_tool_calls: int = 0) -> SubAgentResult:
        """Create and run a sub-agent in one step."""
        agent = self.spawn(agent_type, provider_name, workdir=workdir)
        result = agent.run(prompt, max_iterations=max_iterations,
                           fail_after_tool_calls=fail_after_tool_calls)
        self._results[result.task_id] = result
        return result

    def get_result(self, task_id: str) -> Optional[SubAgentResult]:
        return self._results.get(task_id)
