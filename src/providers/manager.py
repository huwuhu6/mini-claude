import logging
from typing import Dict, Any, Optional, List
from .base import LLMProvider, Message, ToolDefinition
from .deepseek import DeepseekProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages multiple LLM providers with routing and failover support."""

    def __init__(self):
        self.providers: Dict[str, LLMProvider] = {}
        self.primary_provider: Optional[str] = None
        self.fallback_order: List[str] = []

    def register_provider(
        self,
        name: str,
        provider: LLMProvider,
        is_primary: bool = False,
        fallback_priority: int = 0
    ):
        """
        Register a provider.

        Args:
            name: Unique name for the provider
            provider: The provider instance
            is_primary: Whether this should be the primary provider
            fallback_priority: Priority for fallback (lower numbers first)
        """
        self.providers[name] = provider

        if is_primary or self.primary_provider is None:
            self.primary_provider = name

        # Update fallback order
        if name not in self.fallback_order:
            if fallback_priority == 0:
                self.fallback_order.append(name)
            else:
                # Insert at the correct position
                pos = len(self.fallback_order)
                for i, provider_name in enumerate(self.fallback_order):
                    provider = self.providers[provider_name]
                    provider_fallback_priority = getattr(provider, 'fallback_priority', 0)
                    if fallback_priority < provider_fallback_priority:
                        pos = i
                        break
                self.fallback_order.insert(pos, name)

        logger.info(f"已注册提供者 '{name}'，主提供者: {is_primary}")

    def create_provider(
        self,
        provider_type: str,
        config: Dict[str, Any],
        is_primary: bool = False,
        fallback_priority: int = 0
    ) -> str:
        """
        Create and register a new provider.

        Args:
            provider_type: Type of provider ('deepseek' or 'anthropic')
            config: Provider configuration
            is_primary: Whether to set as primary
            fallback_priority: Priority for fallback

        Returns:
            The name of the registered provider
        """
        name = f"{provider_type}_provider"

        if provider_type == "deepseek":
            provider = DeepseekProvider(config)
        elif provider_type == "anthropic":
            try:
                from .anthropic import AnthropicProvider
                provider = AnthropicProvider(config)
            except ImportError:
                raise ImportError("Anthropic 提供者需要安装 'anthropic' 包。请运行: pip install anthropic")
        else:
            raise ValueError(f"未知的提供者类型: {provider_type}")

        self.register_provider(name, provider, is_primary, fallback_priority)
        return name

    def get_primary_provider(self) -> Optional[LLMProvider]:
        """Get the primary provider."""
        if self.primary_provider and self.primary_provider in self.providers:
            return self.providers[self.primary_provider]
        return None

    def get_available_providers(self) -> List[str]:
        """Get list of available providers."""
        available = []
        for name, provider in self.providers.items():
            if provider.is_available():
                available.append(name)
        return available

    def create_message(
        self,
        messages: List[Message],
        tools: Optional[List[ToolDefinition]] = None,
        preferred_provider: Optional[str] = None,
        **kwargs
    ) -> Any:
        """
        Create a message using the specified or primary provider.

        Args:
            messages: List of messages
            tools: Optional list of tools
            preferred_provider: Preferred provider name
            **kwargs: Additional parameters

        Returns:
            Response from the provider
        """
        # Try preferred provider first
        if preferred_provider and preferred_provider in self.providers:
            provider = self.providers[preferred_provider]
            if provider.is_available():
                try:
                    return provider.create_message(messages, tools, **kwargs)
                except Exception as e:
                    logger.warning(f"提供者 {preferred_provider} 失败: {e}")

        # Try primary provider
        primary = self.get_primary_provider()
        if primary and primary.is_available():
            try:
                return primary.create_message(messages, tools, **kwargs)
            except Exception as e:
                logger.warning(f"主提供者失败: {e}")

        # Try fallback providers
        for provider_name in self.fallback_order:
            provider = self.providers.get(provider_name)
            if provider and provider.is_available() and provider_name != preferred_provider:
                try:
                    return provider.create_message(messages, tools, **kwargs)
                except Exception as e:
                    logger.warning(f"备用提供者 {provider_name} 失败: {e}")

        raise Exception("No available providers")

    def get_cost_estimate(
        self,
        messages: List[Message],
        model: str = None,
        provider_name: Optional[str] = None
    ) -> float:
        """
        Get cost estimate for a message.

        Args:
            messages: List of messages
            model: Optional model override
            provider_name: Specific provider to use

        Returns:
            Estimated cost
        """
        if provider_name and provider_name in self.providers:
            return self.providers[provider_name].get_cost_estimate(messages, model)

        primary = self.get_primary_provider()
        if primary:
            return primary.get_cost_estimate(messages, model)

        return 0.0

    def check_health(self) -> Dict[str, bool]:
        """
        Check health of all providers.

        Returns:
            Dictionary of provider names to availability status
        """
        health_status = {}
        for name, provider in self.providers.items():
            try:
                health_status[name] = provider.is_available()
            except Exception as e:
                logger.error(f"检查 {name} 健康状态时出错: {e}")
                health_status[name] = False
        return health_status

    def get_provider_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all providers.

        Returns:
            Dictionary with provider details
        """
        info = {}
        for name, provider in self.providers.items():
            info[name] = {
                'type': provider.__class__.__name__,
                'model': provider.model,
                'max_tokens': provider.max_tokens,
                'temperature': provider.temperature,
                'available': provider.is_available()
            }
        return info