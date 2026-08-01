import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if "http_client" in sys.modules:
    del sys.modules["http_client"]

from unittest.mock import patch, Mock
import requests
from http_client import fetch_with_retry

# Test 1: 第一次成功
with patch("http_client.requests.get") as mock_get:
    mock_get.return_value = Mock(status_code=200, text="success")
    result = fetch_with_retry("http://example.com")
    assert result == "success", f"Test 1 failed: expected 'success', got {result}"
    assert mock_get.call_count == 1, f"Test 1 failed: expected 1 call, got {mock_get.call_count}"

# Test 2: 失败 2 次后成功
with patch("http_client.requests.get") as mock_get:
    mock_get.side_effect = [
        requests.ConnectionError("ConnectionError"),
        requests.Timeout("Timeout"),
        Mock(status_code=200, text="finally"),
    ]
    result = fetch_with_retry("http://example.com")
    assert result == "finally", f"Test 2 failed: expected 'finally', got {result}"
    assert mock_get.call_count == 3, f"Test 2 failed: expected 3 calls, got {mock_get.call_count}"

# Test 3: 全部失败，抛出异常
with patch("http_client.requests.get") as mock_get:
    mock_get.side_effect = requests.RequestException("Always fails")
    try:
        fetch_with_retry("http://example.com", max_retries=2)
        print("[FAIL] Test 3: expected exception but got none")
        sys.exit(1)
    except Exception as e:
        if "Always fails" not in str(e):
            print(f"[FAIL] Test 3: wrong exception message: {e}")
            sys.exit(1)

# Test 4: 非 200 状态码应重试
with patch("http_client.requests.get") as mock_get:
    mock_get.side_effect = [
        Mock(status_code=500, text="error"),
        Mock(status_code=200, text="ok"),
    ]
    result = fetch_with_retry("http://example.com")
    assert result == "ok", f"Test 4 failed: expected 'ok', got {result}"
    assert mock_get.call_count == 2, f"Test 4 failed: expected 2 calls, got {mock_get.call_count}"

print("[PASS] 所有测试用例通过")
sys.exit(0)
