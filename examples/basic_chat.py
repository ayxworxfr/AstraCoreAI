"""基础聊天示例：演示同步对话与流式对话，以及会话连贯。

用法：
    python examples/basic_chat.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.core.ports.llm import StreamEventType
from astracore.sdk import AstraCoreClient

load_dotenv()


async def main() -> None:
    async with AstraCoreClient() as client:
        # 1. 同步对话
        print("=== 同步对话 ===\n")
        result = await client.chat("你好，用一句话介绍一下自己。")
        print(f"回复: {result.content}")
        print(f"模型: {result.model}\n")

        # 2. 流式对话（续接同一会话）
        print("=== 流式对话（同一会话）===\n")
        print("回复: ", end="", flush=True)
        async for event in client.chat_stream(
            "继续用一句话，说说你能帮我做什么。",
            session_id=result.session_id,
        ):
            if event.event_type == StreamEventType.TEXT_DELTA:
                print(event.content, end="", flush=True)
        print("\n")


def test_main():
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
