import requests


def fetch_with_retry(url: str, max_retries: int = 3, timeout: int = 5) -> str:
    """
    向指定 URL 发送 GET 请求，并在请求失败时自动重试。

    行为要求：
    1. 使用 requests.get 发送请求，传入 timeout 参数。
    2. 如果请求抛出异常（如 ConnectionError、Timeout），等待 1 秒后重试。
    3. 最多重试 max_retries 次（包括第一次尝试，总共最多 max_retries 次请求）。
    4. 如果所有尝试都失败，抛出最后一次的异常。
    5. 如果请求成功（状态码 200），返回 response.text。
    6. 如果返回非 200 状态码，视为失败，同样进行重试。

    Args:
        url: 请求的 URL。
        max_retries: 最大尝试次数，默认为 3。
        timeout: 每次请求的超时秒数，默认为 5。

    Returns:
        成功时返回响应的文本内容。

    Raises:
        最后一次请求失败的异常。
    """
    pass
