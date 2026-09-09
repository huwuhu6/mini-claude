"""Provider transport diagnostics must retain root cause without secrets."""

import os
import sys

sys.path.insert(0, "src")
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
