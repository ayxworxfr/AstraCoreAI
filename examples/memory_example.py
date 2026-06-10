"""结构化记忆示例：手动 CRUD、Project 绑定、自动记忆提取。

用法：
    python examples/memory_example.py
"""

import asyncio

from dotenv import load_dotenv

from astracore.modules.memory.domain import MemoryScope, MemoryType
from astracore.shared.observability.logger import get_logger, setup_logging
from astracore.sdk import AstraCoreClient

load_dotenv()
setup_logging()
logger = get_logger("astracore.examples.memory")


async def main() -> None:
    async with AstraCoreClient() as client:
        # ----------------------------------------------------------------
        # 1. 手动创建与查询结构化记忆
        # ----------------------------------------------------------------
        logger.info("=== 手动创建记忆 ===")
        mem = await client.memory.create(
            scope="user",
            memory_type="preference",
            subject="回答风格",
            content="用户偏好简洁的中文回答，避免过多术语解释。",
            importance=4,
        )
        logger.info("已创建记忆 id=%s  subject=%s", mem.id, mem.subject)

        updated = await client.memory.update(mem.id, importance=5, confidence=0.95)
        logger.info("已更新  importance=%d  confidence=%.2f", updated.importance, updated.confidence)

        memories = await client.memory.list(
            scope=MemoryScope.USER,
            memory_type=MemoryType.PREFERENCE,
        )
        logger.info("查询到 %d 条用户偏好记忆", len(memories))

        # ----------------------------------------------------------------
        # 2. Project 边界与会话绑定
        # ----------------------------------------------------------------
        logger.info("=== Project 绑定 ===")
        project = await client.projects.create(
            name="示例项目",
            root_paths=["D:/project/study/AstraCoreAI"],
            description="AstraCore AI 框架示例",
        )
        logger.info("已创建 Project  id=%s  name=%s", project.id, project.name)

        # 将会话绑定到 Project——后续对话中会自动注入该 Project 范围的记忆
        conv = client.conversation(project_id=project.id)
        logger.info("会话已绑定 Project，session_id=%s", conv.session_id)

        constraint_mem = await client.memory.create(
            scope="project",
            memory_type="constraint",
            subject="技术栈约束",
            content="项目使用 Python 3.11+，所有异步 I/O 必须用 async/await。",
            project_id=project.id,
            importance=5,
            locked=True,
        )
        logger.info("已为 Project 添加 constraint 记忆  id=%s", constraint_mem.id)

        # ----------------------------------------------------------------
        # 3. 对话后自动提取记忆（需要真实 LLM）
        # ----------------------------------------------------------------
        logger.info("=== 自动记忆提取（对话触发）===")
        logger.info("发送一条包含可提取信息的消息…")
        result = await conv.send(
            "我叫张三，是这个项目的主要维护者，偏好使用 Clean Architecture。"
        )
        logger.info("A: %s", result.content[:120])
        logger.info("（对话结束后 SDK 会自动提取结构化记忆）")

        # 稍等片刻让后台提取完成（实际上 asyncio.shield 已保证提取）
        await asyncio.sleep(1)

        session_memories = await client.memory.list(
            scope=MemoryScope.SESSION,
            session_id=conv.session_id,
        )
        logger.info("本轮对话后 session 记忆数：%d", len(session_memories))
        for m in session_memories:
            logger.info("  [%s] %s: %s", m.type.value, m.subject, m.content[:60])

        # ----------------------------------------------------------------
        # 4. 清理演示数据
        # ----------------------------------------------------------------
        logger.info("=== 清理 ===")
        await client.memory.delete(mem.id)
        # delete_project 会级联删除该 Project 下的所有记忆与会话绑定，
        # 无需提前单独删除 constraint_mem。
        await client.projects.delete(project.id)
        await conv.clear()
        logger.info("已清除手动记忆、Project（含级联记忆）与会话记忆")

        projects = await client.projects.list_all()
        logger.info("当前 Project 数量：%d", len(projects))


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
