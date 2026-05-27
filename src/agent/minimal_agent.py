#!/usr/bin/env python3
"""
Minimal Agent - A simple implementation to test basic functionality.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'src'))

from models.config import ConfigManager
from providers.manager import ProviderManager
from providers.base import Message, ToolDefinition
from core.tools.base_tools import BaseTools

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MinimalAgent:
    """Minimal agent implementation."""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config = self.config_manager.get_config()
        self.provider_manager = ProviderManager()
        self.tools = BaseTools(Path.cwd())
        self.messages = []

        # Setup providers
        self._setup_providers()

    def _setup_providers(self):
        """Setup LLM providers."""
        # Create provider from config
        llm_config = {
            'model': self.config.llm.model,
            'max_tokens': self.config.llm.max_tokens,
            'temperature': self.config.llm.temperature,
            'api_key': self.config.llm.api_key,
            'base_url': self.config.llm.base_url
        }

        # Create the configured provider
        try:
            provider_name = self.provider_manager.create_provider(
                self.config.llm.provider,
                llm_config,
                is_primary=True
            )
            logger.info(f"已创建提供者: {provider_name}")
        except Exception as e:
            logger.error(f"创建提供者失败: {e}")
            sys.exit(1)

    def format_tools_for_llm(self) -> List[Dict[str, Any]]:
        """Format tools for LLM API call."""
        tools = [
            {
                'name': 'bash',
                'description': 'Run a shell command.',
                'input_schema': {
                    'type': 'object',
                    'properties': {'command': {'type': 'string'}},
                    'required': ['command']
                }
            },
            {
                'name': 'read_file',
                'description': 'Read file contents.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'limit': {'type': 'integer'}
                    },
                    'required': ['path']
                }
            },
            {
                'name': 'write_file',
                'description': 'Write content to file.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'content': {'type': 'string'}
                    },
                    'required': ['path', 'content']
                }
            },
            {
                'name': 'edit_file',
                'description': 'Replace exact text in file.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string'},
                        'old_text': {'type': 'string'},
                        'new_text': {'type': 'string'}
                    },
                    'required': ['path', 'old_text', 'new_text']
                }
            }
        ]

        # Filter tools based on features
        if not self.config.features.background:
            tools = [t for t in tools if t['name'] != 'bash']
        if not self.config.features.compression:
            tools = [t for t in tools if t['name'] not in ['read_file', 'write_file', 'edit_file']]

        return tools

    def _init_tool_handlers(self) -> Dict[str, Any]:
        """Initialize tool dispatch dictionary."""
        return {
            'bash': lambda args: self.tools.run_bash(args['command']),
            'read_file': lambda args: self.tools.read_file(args['path'], args.get('start_line'), args.get('end_line')),
            'write_file': lambda args: self.tools.write_file(args['path'], args['content']),
            'edit_file': lambda args: self.tools.edit_file(args['path'], args['edits']),
        }

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool using dictionary-based dispatch and return the result."""
        handlers = self._init_tool_handlers()
        handler = handlers.get(tool_name)
        if not handler:
            return f"未知工具: {tool_name}"

        try:
            result = handler(arguments)
            return result.content if result.success else f"错误: {result.content}"
        except KeyError as e:
            logger.error(f"工具 {tool_name} 缺少参数: {e}")
            return f"错误: 工具 {tool_name} 缺少必要参数: {e}"
        except Exception as e:
            logger.error(f"执行工具 {tool_name} 出错: {e}")
            return f"错误: {str(e)}"

    def run(self, user_input: str) -> str:
        """Run the agent with user input."""
        # Add user message
        self.messages.append(Message(role='user', content=user_input))

        # Get tools
        tools = self.format_tools_for_llm()
        tool_definitions = [ToolDefinition(**t) for t in tools]

        # Get provider
        provider = self.provider_manager.get_primary_provider()
        if not provider:
            return "错误: 没有可用的提供者"

        try:
            # Create message
            response = provider.create_message(self.messages, tool_definitions)

            # Parse response
            parsed = provider.parse_response(response)
            content = parsed['content']
            tool_calls = parsed.get('tool_calls', [])

            # Add assistant response to history
            self.messages.append(Message(role='assistant', content=content))

            # Execute tools
            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call['function']['name']
                arguments = tool_call['function']['arguments']
                result = self.execute_tool(tool_name, arguments)
                tool_results.append(f"[{tool_name}]\n{result}")

            # Combine results
            if tool_results:
                return f"{content}\n\n" + "\n\n".join(tool_results)
            else:
                return content

        except Exception as e:
            logger.error(f"代理运行出错: {e}")
            return f"错误: {str(e)}"


def main():
    """Main entry point."""
    print("最小代理 - 基本测试")
    print("输入 'exit' 退出")
    print("-" * 40)

    agent = MinimalAgent()

    while True:
        try:
            user_input = input("\n用户: ").strip()
            if user_input.lower() in ('exit', 'quit'):
                break

            if not user_input:
                continue

            print("代理: ", end="", flush=True)
            response = agent.run(user_input)
            print(response)

        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"错误: {e}")


if __name__ == "__main__":
    main()