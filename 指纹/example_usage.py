"""
使用示例 - 通过 anyrouter 调用 Claude API
"""
from claude_client import ClaudeClient


def example_basic():
    """基础调用"""
    client = ClaudeClient(
        api_key="sk-ant-your-key-here",
        base_url="https://your-anyrouter-domain.com",  # 替换为你的 anyrouter 地址
    )

    response = client.messages_create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello, Claude!"}],
        max_tokens=256,
    )
    print(response)
    client.close()


def example_streaming():
    """流式调用"""
    with ClaudeClient(
        api_key="sk-ant-your-key-here",
        base_url="https://your-anyrouter-domain.com",
    ) as client:
        for event in client.messages_create(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Write a haiku about code"}],
            max_tokens=256,
            stream=True,
        ):
            if event["event"] == "content_block_delta":
                delta = event["data"].get("delta", {})
                if delta.get("type") == "text_delta":
                    print(delta["text"], end="", flush=True)
        print()


def example_verify_fingerprint():
    """验证 TLS 指纹"""
    with ClaudeClient(api_key="test") as client:
        fp = client.verify_fingerprint()
        print("TLS 指纹验证结果:")
        for k, v in fp.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    print("=== 指纹验证 ===")
    example_verify_fingerprint()

    # 取消注释以测试实际调用
    # print("\n=== 基础调用 ===")
    # example_basic()
    # print("\n=== 流式调用 ===")
    # example_streaming()
