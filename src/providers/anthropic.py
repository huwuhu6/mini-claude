import json
import logging
from typing import List, Dict, Any, Optional
from anthropic import Anthropic

from .base import LLMProvider, Message, ToolDefinition

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        api_key = config.get('api_key', '')
        base_url = config.get('base_url', 'https://api.anthropic.com')

        self.client = Anthropic(
            api_key=api_key,
            base_url=base_url
        )
        logger.info(f"Anthropic 提供者已初始化，模型: {self.model}")

    def create_message(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Create a message using Anthropic API.

        Args:
            messages: List of messages in the conversation
            tools: Optional list of available tools
            system: Optional system prompt
            **kwargs: Additional parameters

        Returns:
            Response from Anthropic API
        """
        try:
            # Format messages for Anthropic API
            formatted_messages = []
            for msg in messages:
                if msg.role == 'assistant' and msg.tool_calls:
                    # Build content blocks with text + tool_use
                    content_blocks = []
                    if msg.content:
                        content_blocks.append({"type": "text", "text": msg.content})
                    for tc in msg.tool_calls:
                        args = tc.get('function', {}).get('arguments', {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except (json.JSONDecodeError, TypeError):
                                args = {"raw": args}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get('id', ''),
                            "name": tc.get('function', {}).get('name', ''),
                            "input": args,
                        })
                    formatted_messages.append({'role': 'assistant', 'content': content_blocks})
                elif msg.role == 'tool' and msg.tool_call_id:
                    formatted_messages.append({
                        'role': 'user',
                        'content': [{
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }]
                    })
                else:
                    message_dict = {'role': msg.role, 'content': msg.content}
                    if msg.name:
                        message_dict['name'] = msg.name
                    formatted_messages.append(message_dict)

            # Prepare parameters
            params = {
                'model': self.model,
                'messages': formatted_messages,
                'max_tokens': self.max_tokens,
                'temperature': self.temperature,
                'stream': False
            }

            # System prompt (Anthropic uses top-level system parameter)
            if system:
                params['system'] = system

            # Add tools if provided
            if tools:
                formatted_tools = []
                for tool in tools:
                    formatted_tools.append({
                        'name': tool.name,
                        'description': tool.description,
                        'input_schema': tool.input_schema
                    })
                params['tools'] = formatted_tools

            # Add any additional parameters
            params.update(kwargs)

            # Make the API call
            response = self.client.messages.create(**params)

            logger.debug(f"已收到 Anthropic API 响应，使用量: {response.usage}")
            return response

        except Exception as e:
            logger.error(f"使用 Anthropic 创建消息时出错: {e}")
            raise

    def get_cost_estimate(self, messages: List[Message], model: str = None) -> float:
        """
        Estimate the cost for Anthropic API.

        Anthropic pricing (approximate):
        - Claude 3 Sonnet: $3.00 / 1M input tokens, $15.00 / 1M output tokens
        - Claude 3 Haiku: $0.25 / 1M input tokens, $1.25 / 1M output tokens

        Args:
            messages: List of messages to estimate cost for
            model: Optional model override

        Returns:
            Estimated cost in USD
        """
        try:
            # Use specified model or fall back to configured model
            model_name = model or self.model

            # Calculate tokens (rough estimate: 1 token ≈ 4 characters)
            total_chars = sum(len(msg.content) for msg in messages)
            input_tokens = total_chars // 4

            # Estimate output tokens (assume 50% of input for estimation)
            output_tokens = input_tokens // 2

            # Anthropic pricing (per million tokens)
            if 'sonnet' in model_name.lower():
                input_price = 3.00  # $3.00 per 1M input tokens
                output_price = 15.00  # $15.00 per 1M output tokens
            elif 'haiku' in model_name.lower():
                input_price = 0.25  # $0.25 per 1M input tokens
                output_price = 1.25  # $1.25 per 1M output tokens
            else:
                # Default to Sonnet pricing for other models
                input_price = 3.00
                output_price = 15.00

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
        Check if Anthropic API is available.

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
            logger.warning(f"Anthropic 提供者不可用: {e}")
            return False

    def parse_response(self, response: Any) -> Dict[str, Any]:
        """
        Parse the Anthropic API response into a standardized format.

        Args:
            response: Raw response from Anthropic API

        Returns:
            Parsed response dictionary
        """
        try:
            # Get the first content block (usually the text response)
            content_blocks = response.content
            content = ""
            tool_calls = []

            for block in content_blocks:
                if block.type == 'text':
                    content = block.text
                elif block.type == 'tool_use':
                    tool_calls.append({
                        'id': block.id,
                        'type': 'function',
                        'function': {
                            'name': block.name,
                            'arguments': block.input
                        }
                    })

            parsed = {
                'content': content,
                'tool_calls': tool_calls,
                'usage': {
                    'input_tokens': response.usage.input_tokens,
                    'output_tokens': response.usage.output_tokens,
                    'total_tokens': response.usage.input_tokens + response.usage.output_tokens
                }
            }

            return parsed

        except Exception as e:
            logger.error(f"解析 Anthropic 响应时出错: {e}")
            return {
                'content': '',
                'tool_calls': [],
                'usage': {}
            }