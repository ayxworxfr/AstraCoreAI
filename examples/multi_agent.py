"""并发会话示例：演示同时发起多个独立对话，以及基于结果续接会话。

流程：asyncio.gather 并发两个独立问答 → 取第一个的 session_id 续接追问。

用法：
    python examples/multi_agent.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.sdk import AstraCoreClient

load_dotenv()


async def main() -> None:
    async with AstraCoreClient() as client:
        # 1. 并发发起两个独立对话
        print("=== 并发独立会话 ===\n")
        q1 = "用一句话解释什么是大语言模型。"
        q2 = "用一句话解释什么是向量数据库。"

        r1, r2 = await asyncio.gather(
            client.chat(q1),
            client.chat(q2),
        )
        print(f"Q: {q1}\nA: {r1.content}\n")
        print(f"Q: {q2}\nA: {r2.content}\n")

        # 2. 基于第一个会话续接追问
        print("=== 续接会话（基于第一轮回复）===\n")
        r3 = await client.chat(
            "给出一个实际应用例子。",
            session_id=r1.session_id,
        )
        print(f"Q: 给出一个实际应用例子。\nA: {r3.content}\n")


def test_main():
    asyncio.run(main())


if __name__ == "__main__":
    test_main()