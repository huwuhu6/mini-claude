import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class Message:
    role: str
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


@dataclass
class ToolResult:
    tool_use_id: str
    content: str


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any]


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model = config.get('model', 'default')
        self.max_tokens = config.get('max_tokens', 8000)
        self.temperature = config.get('temperature', 0.7)

    @abstractmethod
    def create_message(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        system: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Create a message with the LLM provider.

        Args:
            messages: List of messages in the conversation
            tools: Optional list of available tools
            system: Optional system prompt
            **kwargs: Additional provider-specific parameters

        Returns:
            The response from the LLM provider
        """
        pass

    @abstractmethod
    def parse_response(self, response: Any) -> Dict[str, Any]:
        """
        Parse the provider response into a standardized format.

        Returns:
            Dict with keys: 'content', 'tool_calls' (list), 'usage' (dict)
        """
        pass

    @abstractmethod
    def get_cost_estimate(self, messages: List[Message], model: str = None) -> float:
        """
        Estimate the cost of a message.

        Args:
            messages: List of messages to estimate cost for
            model: Optional model override

        Returns:
            Estimated cost in USD
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the provider is available and working.

        Returns:
            True if the provider is available
        """
        pass

    def validate_message(self, message: Message) -> bool:
        """
        Validate a message according to provider requirements.

        Args:
            message: Message to validate

        Returns:
            True if message is valid
        """
        return (
            isinstance(message, Message) and
            message.role in ['user', 'assistant', 'system'] and
            isinstance(message.content, str) and
            len(message.content.strip()) > 0
        )

    def format_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """
        Format messages for the specific provider.

        Args:
            messages: List of Message objects

        Returns:
            Formatted messages list
        """
        formatted = []
        for msg in messages:
            if not self.validate_message(msg):
                raise ValueError(f"Invalid message: {msg}")

            formatted_msg = {
                'role': msg.role,
                'content': msg.content
            }

            if msg.name:
                formatted_msg['name'] = msg.name
            if msg.tool_calls:
                formatted_msg['tool_calls'] = msg.tool_calls
            if msg.tool_call_id:
                formatted_msg['tool_call_id'] = msg.tool_call_id

            formatted.append(formatted_msg)

        return formatted