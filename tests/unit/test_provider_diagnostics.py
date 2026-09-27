"""Provider transport diagnostics must retain root cause without secrets."""

import os
import sys
from pathlib import Path

import httpx
import pytest
from openai import APITimeoutError, OpenAI

sys.path.insert(0, "src")
from models.config import ConfigManager, LLMConfig
from providers.base import Message
from providers.deepseek import DeepseekProvider


def test_deepseek_diagnostic_is_sanitized(monkeypatch):
    secret = "secret-key-for-test"
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    provider = DeepseekProvider({
        "model": "deepseek-chat",
        "api_key": secret,
        "base_url": "https://api.deepseek.com",
        "timeout": 12,
    })
    root = ConnectionError(f"Authorization: Bearer {secret}")
    error = RuntimeError("request failed")
    error.__cause__ = root
    diagnostic = provider._diagnose_error(error)
    assert diagnostic["endpoint_host"] == "api.deepseek.com"
    assert diagnostic["timeout_seconds"] == 12
    assert diagnostic["proxy_present"] is True
    assert all(secret not in item["message"] for item in diagnostic["exception_chain"])
    assert all("authorization" not in item["message"].lower() or "[REDACTED]" in item["message"]
               for item in diagnostic["exception_chain"])


def test_default_provider_timeout_allows_slow_responses():
    config = ConfigManager(Path("configs/default.yaml")).get_config()
    assert config.llm.timeout_ms == 60_000
    assert LLMConfig().timeout_ms == config.llm.timeout_ms


def test_transient_read_timeout_is_retried_once(monkeypatch):
    monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)
    attempts = []

    def send(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow response", request=request)
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0,
            "model": "test", "choices": [{"index": 0, "message": {
                "role": "assistant", "content": "ok"
            }, "finish_reason": "stop"}],
        })

    provider = DeepseekProvider({"model": "test", "api_key": "test", "timeout": 60})
    provider.client = OpenAI(
        api_key="test", base_url="https://example.test/v1",
        timeout=60, max_retries=provider.client.max_retries,
        http_client=httpx.Client(transport=httpx.MockTransport(send)),
    )
    response = provider.create_message([Message(role="user", content="hello")])
    assert response.choices[0].message.content == "ok"
    assert len(attempts) == 2
    assert provider.client.max_retries == 1


def test_persistent_read_timeout_is_bounded_and_classified(monkeypatch):
    monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)
    attempts = []

    def send(request):
        attempts.append(request)
        raise httpx.ReadTimeout("slow response", request=request)

    provider = DeepseekProvider({"model": "test", "api_key": "test", "timeout": 60})
    provider.client = OpenAI(
        api_key="test", base_url="https://example.test/v1",
        timeout=60, max_retries=provider.client.max_retries,
        http_client=httpx.Client(transport=httpx.MockTransport(send)),
    )
    with pytest.raises(APITimeoutError):
        provider.create_message([Message(role="user", content="hello")])
    assert len(attempts) == 2
    assert provider.last_error_diagnostic["error_category"] == "TIMEOUT"
    assert provider.last_error_diagnostic["timeout_seconds"] == 60
