"""基础聊天示例：同步对话、流式对话、多轮会话连贯。

用法：
    python examples/basic_chat.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.shared.observability.logger import get_logger, setup_logging
from astracore.sdk import AstraCoreClient

load_dotenv()
setup_logging()
logger = get_logger("astracore.examples.basic_chat")


async def main() -> None:
    async with AstraCoreClient() as client:
        conv = client.conversation()

        # 1. 同步对话
        logger.info("=== 同步对话 ===")
        result = await conv.send("你好，用一句话介绍一下自己。")
        logger.info("Q: 你好，用一句话介绍一下自己。")
        logger.info("A: %s", result.content)
        logger.info("模型: %s", result.model)

        # 2. 流式对话（同一会话自动续接）
        logger.info("=== 流式对话（同一会话）===")
        logger.info("Q: 继续用一句话，说说你能帮我做什么。")
        print("A: ", end="", flush=True)
        async for chunk in conv.stream("继续用一句话，说说你能帮我做什么。"):
            print(chunk, end="", flush=True)
        print()


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
