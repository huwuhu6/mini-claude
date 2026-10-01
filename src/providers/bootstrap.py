"""Resolve provider configuration and register the primary provider."""

import logging
import os

from models.config import LLMConfig
from .manager import ProviderManager

logger = logging.getLogger(__name__)


def configure_primary_provider(manager: ProviderManager, llm: LLMConfig) -> None:
    """Resolve the configured provider's credentials and register it if usable."""
    if llm.provider == 'deepseek':
        api_key = os.getenv('DEEPSEEK_API_KEY', llm.api_key or '')
        base_url = os.getenv('DEEPSEEK_BASE_URL',
                             llm.base_url or 'https://api.deepseek.com')
    elif llm.provider == 'dashscope':
        api_key = os.getenv('DASHSCOPE_API_KEY', llm.api_key or '')
        base_url = os.getenv(
            'DASHSCOPE_COMPATIBLE_BASE_URL',
            os.getenv(
                'AI_BASE_URL',
                llm.base_url or 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            ),
        )
    elif llm.provider == 'anthropic':
        api_key = os.getenv('ANTHROPIC_API_KEY', llm.api_key or '')
        base_url = os.getenv('ANTHROPIC_BASE_URL',
                             llm.base_url or 'https://api.anthropic.com')
    else:
        api_key = ''
        base_url = ''

    provider_config = {
        'model': llm.model,
        'max_tokens': llm.max_tokens,
        'temperature': llm.temperature,
        'api_key': api_key,
        'base_url': base_url,
        'timeout': llm.timeout_ms / 1000.0,
        'stream': llm.stream,
    }

    if api_key:
        try:
            manager.create_provider(llm.provider, provider_config, is_primary=True)
            logger.info(f"提供者 '{llm.provider}' 创建成功")
        except Exception as e:
            logger.warning(f"创建提供者 '{llm.provider}' 失败: {e}")
            logger.info("正在无提供者模式下运行（功能受限）")
