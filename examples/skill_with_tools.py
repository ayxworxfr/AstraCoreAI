"""Skill + 工具联动示例：为会话绑定 Skill 并同时启用工具。

流程：
  1. 获取可用 Skill 列表
  2. 选择一个 Skill（默认取排序第二个，或由命令行指定名称）
  3. 绑定该 Skill，启用工具，流式打印两轮对话
  4. 可选：第三轮开启联网搜索

用法：
    python examples/skill_with_tools.py
    python examples/skill_with_tools.py 代码助手
    python examples/skill_with_tools.py 代码助手 --web
"""

import asyncio
import sys  # sys.argv 仍需要
from uuid import UUID

from dotenv import load_dotenv

from astracore.core.ports.llm import StreamEventType
from astracore.runtime.observability.logger import get_logger, setup_logging
from astracore.sdk import AstraCoreClient, Conversation

load_dotenv()
setup_logging()
logger = get_logger("astracore.examples.skill_with_tools")


def _resolve_args() -> tuple[str | None, bool]:
    """解析示例参数；被 pytest 收集时忽略 pytest/debug 参数。"""
    if "pytest" in sys.modules:
        return None, False
    args = list(sys.argv[1:])
    web_requested = "--web" in args
    name_hint = next((a for a in args if not a.startswith("-")), None)
    return name_hint, web_requested


def _pick_skill(skills: list[dict], name_hint: str | None) -> dict | None:
    """按名称关键词选 Skill；未指定则取排序第二的 Skill。"""
    if name_hint:
        matches = [s for s in skills if name_hint.lower() in s["name"].lower()]
        return matches[0] if matches else None
    sorted_skills = sorted(skills, key=lambda s: s["order"])
    return sorted_skills[1] if len(sorted_skills) > 1 else (sorted_skills[0] if sorted_skills else None)


async def _stream_turn(conv: Conversation, question: str, **overrides: object) -> None:
    """流式打印一轮对话，显示工具调用和技能路由信息。"""
    logger.info("Q: %s", question)
    print("A: ", end="", flush=True)
    async for event in conv.stream_events(question, **overrides):
        if event.event_type == StreamEventType.SKILL_MATCH:
            anchor = event.metadata.get("anchor")
            routed = event.metadata.get("routed", [])
            if anchor:
                logger.info("[Skill] 主技能: %s", anchor)
            if routed:
                logger.info("[Skill] 副技能: %s", ", ".join(routed))
        elif event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
            logger.info("⚙ 调用工具: %s", event.tool_call.name)
        elif event.event_type == StreamEventType.TEXT_DELTA and event.content:
            print(event.content, end="", flush=True)
    print()


async def main() -> None:
    async with AstraCoreClient() as client:
        profile = client.config.llm.get_profile()
        tools_ok = profile.capabilities.tools
        name_hint, web_requested = _resolve_args()

        skills = await client.list_skills()
        if not skills:
            logger.warning("没有可用的 Skill，请确认服务已正常启动并完成 Skill 同步。")
            return

        skill = _pick_skill(skills, name_hint)
        if skill is None:
            logger.warning("未找到包含 '%s' 的 Skill", name_hint)
            return

        skill_id = UUID(skill["id"])
        logger.info("=== Skill + 工具联动示例 ===")
        logger.info("已选 Skill: 【%s】", skill["name"])
        logger.info("描述: %s", skill["description"] or "（无）")

        if not tools_ok:
            logger.warning("当前 profile '%s' 不支持工具调用，use_tools 将被忽略", profile.id)

        conv = client.conversation(skill_id=skill_id, use_tools=tools_ok)

        # 第一轮：Skill + 工具
        await _stream_turn(conv, "列出当前目录下有哪些文件，并简单说明一下。")

        # 第二轮：同一会话续接
        await _stream_turn(conv, "基于上面的文件，有什么需要特别注意的地方？")

        # 第三轮：可选联网搜索
        if web_requested and tools_ok:
            await _stream_turn(
                conv,
                "最近有什么关于这个项目用到的技术栈的新进展？",
                enable_web=True,
            )


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
