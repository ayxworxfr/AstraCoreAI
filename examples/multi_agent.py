"""并发会话示例：同时发起多个独立对话，并基于结果续接会话。

流程：asyncio.gather 并发两个独立问答 → 取第一个的会话续接追问。

用法：
    python examples/multi_agent.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.shared.observability.logger import get_logger, setup_logging
from astracore.sdk import AstraCoreClient

load_dotenv()
setup_logging()
logger = get_logger("astracore.examples.multi_agent")


async def main() -> None:
    async with AstraCoreClient() as client:
        # 两个独立会话，各自持有自己的 session_id
        conv1 = client.conversation()
        conv2 = client.conversation()

        # 1. 并发发起两个独立对话
        logger.info("=== 并发独立会话 ===")
        q1 = "用一句话解释什么是大语言模型。"
        q2 = "用一句话解释什么是向量数据库。"

        r1, r2 = await asyncio.gather(
            conv1.send(q1),
            conv2.send(q2),
        )
        logger.info("Q: %s", q1)
        logger.info("A: %s", r1.content)
        logger.info("Q: %s", q2)
        logger.info("A: %s", r2.content)

        # 2. 续接第一个会话追问（conv1 自动持有 session_id，无需手动传递）
        logger.info("=== 续接会话（基于第一轮回复）===")
        q3 = "给出一个实际应用例子。"
        r3 = await conv1.send(q3)
        logger.info("Q: %s", q3)
        logger.info("A: %s", r3.content)


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
