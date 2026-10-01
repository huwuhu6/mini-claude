import logging
import sys

import pytest

sys.path.insert(0, "src")

from models.config import LLMConfig
from providers.bootstrap import configure_primary_provider


class RecordingProviderManager:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def create_provider(self, provider_type, config, is_primary=False):
        self.calls.append((provider_type, config, is_primary))
        if self.error:
            raise self.error


@pytest.fixture(autouse=True)
def clear_provider_environment(monkeypatch):
    for name in (
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DASHSCOPE_API_KEY",
        "DASHSCOPE_COMPATIBLE_BASE_URL", "AI_BASE_URL", "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("provider, key_env, url_env, default_url", [
    ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    ("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
])
def test_provider_env_overrides_config_and_keeps_defaults(
    monkeypatch, provider, key_env, url_env, default_url
):
    llm = LLMConfig(
        provider=provider, model="test-model", api_key="config-key",
        base_url="https://config.test", timeout_ms=12500, stream=False,
    )
    monkeypatch.setenv(key_env, "env-key")
    monkeypatch.setenv(url_env, "https://env.test")
    manager = RecordingProviderManager()

    configure_primary_provider(manager, llm)

    assert manager.calls == [(provider, {
        "model": "test-model",
        "max_tokens": llm.max_tokens,
        "temperature": llm.temperature,
        "api_key": "env-key",
        "base_url": "https://env.test",
        "timeout": 12.5,
        "stream": False,
    }, True)]


@pytest.mark.parametrize("provider, default_url", [
    ("deepseek", "https://api.deepseek.com"),
    ("anthropic", "https://api.anthropic.com"),
])
def test_provider_config_and_default_url_are_used_without_env(
    provider, default_url
):
    manager = RecordingProviderManager()
    configure_primary_provider(manager, LLMConfig(
        provider=provider, api_key="config-key", base_url="",
    ))
    assert manager.calls[0][1]["api_key"] == "config-key"
    assert manager.calls[0][1]["base_url"] == default_url

    manager = RecordingProviderManager()
    configure_primary_provider(manager, LLMConfig(
        provider=provider, api_key="config-key", base_url="https://config.test",
    ))
    assert manager.calls[0][1]["base_url"] == "https://config.test"


@pytest.mark.parametrize("env, config_url, expected", [
    ({"DASHSCOPE_COMPATIBLE_BASE_URL": "https://compatible.test", "AI_BASE_URL": "https://ai.test"}, "https://config.test", "https://compatible.test"),
    ({"AI_BASE_URL": "https://ai.test"}, "https://config.test", "https://ai.test"),
    ({}, "https://config.test", "https://config.test"),
    ({}, "", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
])
def test_dashscope_url_priority(monkeypatch, env, config_url, expected):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    manager = RecordingProviderManager()

    configure_primary_provider(manager, LLMConfig(
        provider="dashscope", api_key="config-key", base_url=config_url,
    ))

    assert manager.calls[0][1]["api_key"] == "env-key"
    assert manager.calls[0][1]["base_url"] == expected


def test_dashscope_config_api_key_is_used_without_env():
    manager = RecordingProviderManager()

    configure_primary_provider(manager, LLMConfig(
        provider="dashscope", api_key="config-key",
    ))

    assert manager.calls[0][1]["api_key"] == "config-key"
    assert manager.calls[0][1]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_missing_api_key_does_not_create_provider():
    manager = RecordingProviderManager()

    configure_primary_provider(manager, LLMConfig(provider="deepseek"))

    assert manager.calls == []


def test_provider_creation_failure_keeps_no_provider_fallback(caplog):
    caplog.set_level(logging.INFO)
    manager = RecordingProviderManager(error=RuntimeError("local construction error"))

    configure_primary_provider(
        manager,
        LLMConfig(provider="deepseek", api_key="config-key"),
    )

    assert len(manager.calls) == 1
    assert "正在无提供者模式下运行（功能受限）" in caplog.text
