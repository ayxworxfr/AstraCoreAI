"""结构化记忆示例：Context Engineering 两级注入、手动 CRUD、Project 绑定、自动提取。

AstraCoreAI 记忆系统采用 Context Engineering 架构：
  Tier-1  user/global scope → system prompt（稳定用户画像，每次请求注入一次）
  Tier-2  session/project scope → 合成消息对（动态会话上下文，每轮语义检索后注入）

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


async def demo_tier1_user_profile(client: AstraCoreClient) -> list[str]:
    """Tier-1：创建 user scope 记忆，验证它出现在 system prompt 中。"""
    logger.info("=== Tier-1：用户画像注入 System Prompt ===")

    # preference → 天然 Tier-1，描述用户特征
    pref = await client.memory.create(
        scope="user",
        memory_type="preference",
        subject="回答风格",
        content="用户偏好简洁的中文回答，避免过多术语解释。",
        importance=4,
    )
    logger.info("已创建 preference 记忆 id=%s", pref.id)

    # procedure → 天然 Tier-1，描述 AI 应该怎么做（区别于 preference 描述用户）
    proc = await client.memory.create(
        scope="user",
        memory_type="procedure",
        subject="代码确认规范",
        content="询问代码问题时，先确认用户的语言版本和运行环境。",
        importance=4,
    )
    logger.info("已创建 procedure 记忆 id=%s", proc.id)

    # 验证：Tier-1 记忆会出现在下次对话的 system prompt 里，
    # 查询确认它们已存入
    user_mems = await client.memory.list(scope=MemoryScope.USER)
    logger.info("当前 user scope 记忆数：%d", len(user_mems))
    for m in user_mems:
        logger.info("  [%s] %s: %s", m.type.value, m.subject, m.content[:60])

    return [pref.id, proc.id]


async def demo_tier2_session_state(client: AstraCoreClient) -> tuple[str, str]:
    """Tier-2：创建 session scope 状态记忆，验证它被注入为合成消息对。"""
    logger.info("=== Tier-2：会话状态注入合成消息 ===")

    conv = client.conversation()

    # 手动写入游戏状态（state 类型天然 Tier-2）
    # 实际场景中也可以由对话自动提取，此处手动演示
    game_state = await client.memory.create(
        scope="session",
        memory_type="state",
        subject="谁是卧底-游戏状态",
        content=(
            "游戏：谁是卧底；平民词：薯片；卧底词：虾条；"
            "用户身份：平民；卧底：小刚（虾条）；当前轮次：1/3"
        ),
        session_id=conv.session_id,
        importance=5,
        locked=True,  # 锁定，防止 LLM 自动覆盖
    )
    logger.info("已创建 session state 记忆 id=%s  session=%s", game_state.id, conv.session_id)

    # 发送消息 — build_turn_context 会检索上面的 state 记忆并以合成消息对注入
    # 使得 AI 知道游戏当前状态，不需要重复说明
    result = await conv.send("现在到谁发言了？")
    logger.info("AI 回复（应含游戏状态感知）：%s", result.content[:200])

    return game_state.id, str(conv.session_id)


async def demo_project_scope(client: AstraCoreClient) -> tuple[str, str]:
    """Project scope：创建项目约束记忆，绑定会话，验证跨会话共享。"""
    logger.info("=== Project scope：跨会话技术约束 ===")

    project = await client.projects.create(
        name="示例项目",
        root_paths=["D:/project/study/AstraCoreAI"],
        description="AstraCore AI 框架示例",
    )
    logger.info("已创建 Project id=%s  name=%s", project.id, project.name)

    constraint = await client.memory.create(
        scope="project",
        memory_type="constraint",
        subject="技术栈约束",
        content="项目使用 Python 3.11+，所有异步 I/O 必须用 async/await，禁止同步阻塞调用。",
        project_id=project.id,
        importance=5,
        locked=True,
    )
    logger.info("已为 Project 添加 constraint 记忆 id=%s", constraint.id)

    # 绑定会话到 Project——Tier-2 检索时会同时召回 project scope 记忆
    conv = client.conversation(project_id=project.id)
    logger.info("会话 %s 已绑定 Project，下轮对话将注入项目约束", conv.session_id)

    return project.id, str(conv.session_id)


async def demo_auto_extraction(client: AstraCoreClient) -> None:
    """自动提取：对话结束后 LLM 批量提取 0-N 条记忆。"""
    logger.info("=== 自动记忆提取（批量 _ExtractionBatch）===")

    conv = client.conversation()
    logger.info("发送包含可提取信息的消息...")

    result = await conv.send(
        "我叫张三，是这个项目的主要维护者，偏好使用 Clean Architecture，"
        "决定用 SQLite 作为默认存储，不引入 PostgreSQL。"
    )
    logger.info("AI 回复：%s", result.content[:120])

    # 后台提取是异步的，稍等片刻
    await asyncio.sleep(2)

    session_mems = await client.memory.list(
        scope=MemoryScope.SESSION,
        session_id=conv.session_id,
    )
    logger.info("自动提取到 %d 条 session 记忆：", len(session_mems))
    for m in session_mems:
        logger.info("  [%s] %s: %s", m.type.value, m.subject, m.content[:60])

    await conv.clear()


async def main() -> None:
    async with AstraCoreClient() as client:
        # 1. Tier-1：稳定用户画像 → system prompt
        tier1_ids = await demo_tier1_user_profile(client)

        # 2. Tier-2：动态会话状态 → 合成消息对
        game_mem_id, _session_id = await demo_tier2_session_state(client)

        # 3. Project scope：跨会话技术约束
        project_id, _conv_id = await demo_project_scope(client)

        # 4. 自动批量提取
        await demo_auto_extraction(client)

        # ----------------------------------------------------------------
        # 清理演示数据
        # ----------------------------------------------------------------
        logger.info("=== 清理 ===")
        for mem_id in [*tier1_ids, game_mem_id]:
            await client.memory.delete(mem_id)
        # delete_project 级联删除该 Project 下所有记忆与会话绑定
        await client.projects.delete(project_id)

        remaining = await client.memory.list(scope=MemoryScope.USER)
        logger.info("清理后 user scope 记忆数：%d", len(remaining))
        projects = await client.projects.list_all()
        logger.info("清理后 Project 数量：%d", len(projects))


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
