"""Basic chat example using AstraCore SDK."""

import asyncio
import os

from astracore.adapters.llm.anthropic import AnthropicAdapter
from astracore.core.domain.message import Message, MessageRole
from astracore.sdk import AstraCoreClient, AstraCoreConfig
from astracore.sdk.config import LLMConfig


def _is_redis_connection_error(exc: Exception) -> bool:
    """判断是否为 Redis 连接失败。"""
    current: BaseException | None = exc
    while current is not None:
        module_name = type(current).__module__.lower()
        class_name = type(current).__name__.lower()
        message = str(current).lower()
        if (
            module_name.startswith("redis")
            and "connection" in class_name
            or "redis.exceptions.connectionerror" in f"{module_name}.{class_name}"
            or ("localhost:6379" in message and "connect" in message)
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


async def _run_direct_llm_chat(
    api_key: str,
    model: str,
) -> tuple[str, str]:
    """不依赖记忆系统，直接请求 LLM。"""
    adapter = AnthropicAdapter(api_key=api_key, default_model=model)
    request = Message(role=MessageRole.USER, content="Hello! Can you explain what you are?")
    response = await adapter.generate(messages=[request], model=model, max_tokens=512)
    return response.content, response.model


async def main() -> None:
    """Run basic chat example."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "test-key")
    model = os.getenv("MODEL", "claude-sonnet-4-6")

    config = AstraCoreConfig(
        llm=LLMConfig(
            provider="anthropic",
            api_key=api_key,
            default_model=model,
        )
    )

    client = AstraCoreClient(config)

    print("=== Basic Chat Example ===\n")

    try:
        response = await client.chat(
            message="Hello! Can you explain what you are?",
        )
        print(f"Assistant: {response.content}\n")

        print("=== Streaming Chat ===\n")
        print("Assistant: ", end="", flush=True)

        async for event in client.chat_stream(
            message="Tell me a short story about AI",
        ):
            if event.content:
                print(event.content, end="", flush=True)
        print("\n")
        return
    except Exception as exc:
        if not _is_redis_connection_error(exc):
            raise
        print("检测到 Redis 未启动，自动切换为直连 LLM 模式...\n")

    fallback_content, fallback_model = await _run_direct_llm_chat(api_key=api_key, model=model)
    print(f"Assistant({fallback_model}): {fallback_content}\n")

    print("提示：如需完整 Memory 流程，请先启动 Redis（默认 localhost:6379）。")


if __name__ == "__main__":
    asyncio.run(main())
