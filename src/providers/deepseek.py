import json
import logging
import os
import re
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from openai import OpenAI

from .base import LLMProvider, Message, ToolDefinition

logger = logging.getLogger(__name__)


class DeepseekProvider(LLMProvider):
    """Deepseek API provider using OpenAI-compatible interface."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        api_key = config.get('api_key', '')
        base_url = config.get('base_url', 'https://api.deepseek.com')
        self.base_url = base_url
        self.timeout = float(config.get('timeout', config.get('timeout_ms', 60000) / 1000.0))
        self.provider_name = str(config.get('provider_name', 'deepseek'))
        self.last_error_diagnostic: Dict[str, Any] = {}

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
            max_retries=0,
        )
        logger.info(f"{self.provider_name} 提供者已初始化，模型: {self.model}")

    def create_message(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Create a message using Deepseek API.

        Args:
            messages: List of messages in the conversation
            tools: Optional list of available tools
            system: Optional system prompt
            **kwargs: Additional parameters

        Returns:
            Response from Deepseek API
        """
        try:
            # Format messages for OpenAI API
            formatted_messages = []
            for msg in messages:
                message_dict = {'role': msg.role, 'content': msg.content}
                if msg.name:
                    message_dict['name'] = msg.name
                if msg.tool_calls:
                    message_dict['tool_calls'] = msg.tool_calls
                if msg.tool_call_id:
                    message_dict['tool_call_id'] = msg.tool_call_id
                formatted_messages.append(message_dict)

            # Prepend system prompt if provided
            if system:
                formatted_messages.insert(0, {'role': 'system', 'content': system})

            # Prepare parameters
            params = {
                'model': self.model,
                'messages': formatted_messages,
                'max_tokens': self.max_tokens,
                'temperature': self.temperature,
                'stream': False
            }

            # Add tools if provided
            if tools:
                formatted_tools = []
                for tool in tools:
                    formatted_tools.append({
                        'type': 'function',
                        'function': {
                            'name': tool.name,
                            'description': tool.description,
                            'parameters': tool.input_schema
                        }
                    })
                params['tools'] = formatted_tools

            # Add any additional parameters
            params.update(kwargs)

            # Make the API call
            response = self.client.chat.completions.create(**params)

            logger.debug("已收到 Deepseek API 响应")
            return response

        except Exception as e:
            self.last_error_diagnostic = self._diagnose_error(e)
            logger.error(
                "Deepseek request failed: %s",
                json.dumps(self.last_error_diagnostic, ensure_ascii=False, sort_keys=True),
            )
            raise

    def _diagnose_error(self, error: BaseException) -> Dict[str, Any]:
        """Return a secret-free, structured transport diagnostic."""
        endpoint_host = urlparse(self.base_url).hostname or "unknown"
        proxy_present = any(os.environ.get(name) for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        ))
        chain: list[dict[str, str]] = []
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(chain) < 8:
            seen.add(id(current))
            message = str(current)
            if self.config.get("api_key"):
                message = message.replace(str(self.config["api_key"]), "[REDACTED]")
            message = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,]+", r"\1[REDACTED]", message)
            chain.append({"type": type(current).__name__, "message": message[:500]})
            current = current.__cause__ or current.__context__

        status = getattr(error, "status_code", None)
        if status is not None:
            category = "HTTP_STATUS"
        elif any("timeout" in item["message"].lower() for item in chain):
            category = "TIMEOUT"
        elif any("connect" in item["type"].lower() or "connection" in item["message"].lower() for item in chain):
            category = "CONNECTION"
        else:
            category = "PROVIDER_ERROR"
        return {
            "provider": self.provider_name,
            "model": self.model,
            "endpoint_host": endpoint_host,
            "timeout_seconds": self.timeout,
            "proxy_present": proxy_present,
            "http_status": status,
            "error_category": category,
            "exception_chain": chain,
        }

    def get_cost_estimate(self, messages: List[Message], model: str = None) -> float:
        """
        Estimate the cost for Deepseek API.

        Deepseek pricing (approximate):
        - Input: $0.14 / 1M tokens
        - Output: $0.28 / 1M tokens

        Args:
            messages: List of messages to estimate cost for
            model: Optional model override

        Returns:
            Estimated cost in USD
        """
        try:
            # Calculate tokens (rough estimate: 1 token ≈ 4 characters)
            total_chars = sum(len(msg.content) for msg in messages)
            input_tokens = total_chars // 4

            # Estimate output tokens (assume 50% of input for estimation)
            output_tokens = input_tokens // 2

            # Deepseek pricing (per million tokens)
            input_price = 0.14  # $0.14 per 1M input tokens
            output_price = 0.28  # $0.28 per 1M output tokens

            # Calculate cost
            input_cost = (input_tokens / 1_000_000) * input_price
            output_cost = (output_tokens / 1_000_000) * output_price

            total_cost = input_cost + output_cost
            return total_cost

        except Exception as e:
            logger.warning(f"估算成本时出错: {e}")
            return 0.0

    def is_available(self) -> bool:
        """
        Check if Deepseek API is available.

        Returns:
            True if available
        """
        try:
            # Simple health check with a minimal request
            test_messages = [
                Message(role='user', content='Hello')
            ]
            response = self.create_message(test_messages)
            return response is not None
        except Exception as e:
            logger.warning(f"Deepseek 提供者不可用: {e}")
            return False

    def parse_response(self, response: Any) -> Dict[str, Any]:
        """
        Parse the Deepseek API response into a standardized format.

        Args:
            response: Raw response from Deepseek API

        Returns:
            Parsed response dictionary
        """
        missing = object()

        def field(value: Any, name: str, default: Any = missing) -> Any:
            if isinstance(value, dict):
                return value.get(name, default)
            return getattr(value, name, default)

        choices = field(response, 'choices')
        if not isinstance(choices, (list, tuple)) or not choices:
            raise ValueError("OpenAI-compatible response 缺少有效 choices")

        message = field(choices[0], 'message')
        if message is missing or message is None:
            raise ValueError("OpenAI-compatible response 缺少有效 message")

        raw_content = field(message, 'content')
        raw_tool_calls = field(message, 'tool_calls')
        if raw_content is missing and raw_tool_calls is missing:
            raise ValueError("Provider message 不是有效的消息对象")

        content = '' if raw_content is missing else raw_content
        if content is None:
            content = ''
        if not isinstance(content, str):
            raise ValueError("Provider message.content 必须是字符串或 null")

        if raw_tool_calls is missing:
            raw_tool_calls = None
        if raw_tool_calls is None:
            raw_tool_calls = []
        if not isinstance(raw_tool_calls, (list, tuple)):
            raise ValueError("Provider message.tool_calls 必须是数组")

        tool_calls = []
        for index, tool_call in enumerate(raw_tool_calls):
            if tool_call is None:
                raise ValueError(f"Provider tool_call[{index}] 为空")
            call_id = field(tool_call, 'id')
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError(f"Provider tool_call[{index}] 缺少有效 id")

            function = field(tool_call, 'function')
            if function is missing or function is None:
                raise ValueError(f"Provider tool_call[{index}] 缺少 function")
            name = field(function, 'name')
            arguments = field(function, 'arguments')
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Provider tool_call[{index}] 缺少有效 function.name")
            if not isinstance(arguments, str):
                raise ValueError(
                    f"Provider tool_call[{index}].function.arguments 必须是字符串"
                )

            call_type = field(tool_call, 'type', 'function')
            if not isinstance(call_type, str) or not call_type.strip():
                raise ValueError(f"Provider tool_call[{index}] type 无效")
            tool_calls.append({
                'id': call_id,
                'type': call_type,
                'function': {'name': name, 'arguments': arguments},
            })

        # Usage is observability metadata.  A semantically valid response is
        # still usable when a compatible provider omits it.
        usage = field(response, 'usage', None)

        def usage_value(name: str) -> int:
            value = field(usage, name, 0) if usage is not None else 0
            return value if isinstance(value, int) and value >= 0 else 0

        prompt_details = field(usage, 'prompt_tokens_details', None)

        def cached_usage_value() -> int:
            value = field(prompt_details, 'cached_tokens', 0)
            return value if isinstance(value, int) and value >= 0 else 0

        prompt_tokens = usage_value('prompt_tokens')
        cached_tokens = min(cached_usage_value(), prompt_tokens)

        return {
            'content': content,
            'tool_calls': tool_calls,
            'usage': {
                'prompt_tokens': prompt_tokens,
                'completion_tokens': usage_value('completion_tokens'),
                'total_tokens': usage_value('total_tokens'),
                'cached_tokens': cached_tokens,
            },
        }
