import logging
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

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        logger.info(f"Deepseek 提供者已初始化，模型: {self.model}")

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
            logger.error(f"使用 Deepseek 创建消息时出错: {e}")
            raise

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
        try:
            message = response.choices[0].message
            content = message.content or ""

            parsed = {
                'content': content,
                'tool_calls': [],
                'usage': {
                    'prompt_tokens': response.usage.prompt_tokens,
                    'completion_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                }
            }

            # Parse tool calls if present
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tool_call in message.tool_calls:
                    parsed['tool_calls'].append({
                        'id': tool_call.id,
                        'type': tool_call.type,
                        'function': {
                            'name': tool_call.function.name,
                            'arguments': tool_call.function.arguments
                        }
                    })

            return parsed

        except Exception as e:
            logger.error(f"解析 Deepseek 响应时出错: {e}")
            return {
                'content': '',
                'tool_calls': [],
                'usage': {}
            }