"""Skill + 工具联动示例：演示为会话绑定 Skill 并同时启用工具。

流程：
  1. 获取可用 Skill 列表
  2. 选择一个 Skill（默认取第一个非 default 的，或由命令行指定名称）
  3. 绑定该 Skill，启用工具，流式打印两轮对话
  4. 可选：第三轮开启联网搜索

用法：
    python examples/skill_with_tools.py
    python examples/skill_with_tools.py 代码助手
    python examples/skill_with_tools.py 代码助手 --web
"""

import asyncio
import sys
from uuid import UUID, uuid4

from dotenv import load_dotenv

from astracore.core.ports.llm import StreamEventType
from astracore.sdk import AstraCoreClient

load_dotenv()


def _resolve_example_args(argv: list[str] | None = None) -> tuple[str | None, bool]:
    """解析示例参数；被 pytest 收集时忽略 pytest/debug 参数。"""

    if "pytest" in sys.modules:
        return None, False

    args = list(sys.argv[1:] if argv is None else argv)
    web_requested = "--web" in args
    name_hint = next((arg for arg in args if not arg.startswith("-")), None)
    return name_hint, web_requested


def _pick_skill(skills: list[dict], name_hint: str | None = None) -> dict | None:
    """按名称关键词选 Skill；未指定则取排序第二的 Skill（演示效果更明显）。"""
    if name_hint:
        matches = [s for s in skills if name_hint.lower() in s["name"].lower()]
        return matches[0] if matches else None
    sorted_skills = sorted(skills, key=lambda s: s["order"])
    return sorted_skills[1] if len(sorted_skills) > 1 else (sorted_skills[0] if sorted_skills else None)


async def _stream_print(client: AstraCoreClient, **kwargs: object) -> None:
    async for event in client.chat_stream(**kwargs):  # type: ignore[arg-type]
        if event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
            print(f"\n  ⚙ 调用工具: {event.tool_call.name}", flush=True)
        elif event.event_type == StreamEventType.TEXT_DELTA:
            print(event.content, end="", flush=True)


async def main() -> None:
    async with AstraCoreClient() as client:
        profile = client.config.llm.get_profile()
        tools_ok = profile.capabilities.tools
        name_hint, web_requested = _resolve_example_args()

        # 1. 获取 Skill 列表
        skills = await client.list_skills()
        if not skills:
            print("没有可用的 Skill，请确认服务已正常启动并完成 Skill 同步。")
            return

        # 2. 选择 Skill
        skill = _pick_skill(skills, name_hint)
        if skill is None:
            print(f"未找到包含 '{name_hint}' 的 Skill")
            return

        skill_id = UUID(skill["id"])
        session_id = uuid4()

        print("=== Skill + 工具联动示例 ===\n")
        print(f"已选 Skill: 【{skill['name']}】")
        print(f"描述: {skill['description'] or '（无）'}\n")

        if not tools_ok:
            print(f"⚠ 当前 profile '{profile.id}' 不支持工具调用，use_tools 将被忽略\n")

        # 3. 第一轮：Skill + 工具
        q1 = "列出当前目录下有哪些文件，并简单说明一下。"
        print(f"Q: {q1}")
        print("A: ", end="", flush=True)
        await _stream_print(
            client,
            message=q1,
            session_id=session_id,
            skill_id=skill_id,
            use_tools=tools_ok,
        )
        print("\n")

        # 4. 第二轮：同一会话续接
        q2 = "基于上面的文件，有什么需要特别注意的地方？"
        print(f"Q: {q2}")
        print("A: ", end="", flush=True)
        await _stream_print(
            client,
            message=q2,
            session_id=session_id,
            skill_id=skill_id,
            use_tools=tools_ok,
        )
        print("\n")

        # 5. 可选：联网搜索
        if web_requested and tools_ok:
            q3 = "最近有什么关于这个项目用到的技术栈的新进展？"
            print(f"Q: {q3}")
            print("A: ", end="", flush=True)
            await _stream_print(
                client,
                message=q3,
                session_id=session_id,
                skill_id=skill_id,
                use_tools=True,
                enable_web=True,
            )
            print("\n")


def test_main():
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
