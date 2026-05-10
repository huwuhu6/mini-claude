#!/usr/bin/env python3
"""
Script to test LLM provider functionality.
"""

import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_deepseek_provider():
    """Test Deepseek provider directly."""
    print("\n=== 测试 Deepseek 提供者 ===")

    try:
        from src.providers.deepseek import DeepseekProvider

        # Test config
        config = {
            'model': 'deepseek-chat',
            'max_tokens': 1000,
            'temperature': 0.7,
            'api_key': os.getenv('DEEPSEEK_API_KEY', ''),
            'base_url': 'https://api.deepseek.com'
        }

        if not config['api_key']:
            print("[错误] 未设置 DEEPSEEK_API_KEY")
            return False

        provider = DeepseekProvider(config)
        print("[成功] DeepseekProvider 创建成功")

        # Test availability
        available = provider.is_available()
        print(f"提供者可用: {available}")

        if available:
            # Test message creation
            from src.providers.base import Message
            messages = [Message(role='user', content='Hello')]

            response = provider.create_message(messages)
            print("[成功] API 调用成功")

            # Parse response
            parsed = provider.parse_response(response)
            print("响应: " + parsed['content'][:50].encode('ascii', 'ignore').decode() + "...")
            print("使用的 tokens: " + str(parsed['usage']['total_tokens']))

        return True

    except Exception as e:
        print(f"测试 Deepseek 时出错: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_anthropic_provider():
    """Test Anthropic provider directly."""
    print("\n=== 测试 Anthropic 提供者 ===")

    try:
        from src.providers.anthropic import AnthropicProvider

        # Test config
        config = {
            'model': 'claude-3-sonnet-20240229',
            'max_tokens': 1000,
            'temperature': 0.7,
            'api_key': os.getenv('ANTHROPIC_API_KEY', ''),
            'base_url': 'https://api.anthropic.com'
        }

        if not config['api_key']:
            print("ANTHROPIC_API_KEY 未设置，跳过测试")
            return True

        provider = AnthropicProvider(config)
        print("[成功] AnthropicProvider 创建成功")

        # Test availability
        available = provider.is_available()
        print(f"提供者可用: {available}")

        if available:
            # Test message creation
            from src.providers.base import Message
            messages = [Message(role='user', content='Hello')]

            response = provider.create_message(messages)
            print("[成功] API 调用成功")

            # Parse response
            parsed = provider.parse_response(response)
            print("响应: " + parsed['content'][:50].encode('ascii', 'ignore').decode() + "...")
            print("使用的 tokens: " + str(parsed['usage']['total_tokens']))

        return True

    except Exception as e:
        print("[错误] 测试 Anthropic 时出错: " + str(e))
        import traceback
        traceback.print_exc()
        return False

def test_provider_manager():
    """Test Provider Manager."""
    print("\n=== 测试 Provider Manager ===")

    try:
        from src.providers.manager import ProviderManager

        manager = ProviderManager()
        print("[成功] ProviderManager 创建成功")

        # Check health
        health = manager.check_health()
        print(f"提供者健康状态: {health}")

        return True

    except Exception as e:
        print("[错误] 测试 ProviderManager 时出错: " + str(e))
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function."""
    print("=== Mini Claude - LLM 提供者测试 ===")

    # Check environment variables
    print("\n环境变量:")
    deepseek_key = os.getenv('DEEPSEEK_API_KEY')
    anthropic_key = os.getenv('ANTHROPIC_API_KEY')

    print(f"DEEPSEEK_API_KEY: {'已设置' if deepseek_key else '未设置'}")
    print(f"ANTHROPIC_API_KEY: {'已设置' if anthropic_key else '未设置'}")

    # Run tests
    tests = [
        test_deepseek_provider,
        test_anthropic_provider,
        test_provider_manager
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print("[错误] 测试因异常失败: " + str(e))
            results.append(False)

    # Summary
    print("\n" + "="*50)
    print("测试总结:")
    for i, (test, result) in enumerate(zip(tests, results)):
        status = "[成功] 通过" if result else "[错误] 失败"
        print(f"  {test.__name__}: {status}")

    total = sum(results)
    print(f"\n总计: {total}/{len(results)} 个测试通过")

    if total == len(results):
        print("\n[成功] 所有测试通过！")
    else:
        print(f"\n[警告] {len(results) - total} 个测试失败")

if __name__ == "__main__":
    main()