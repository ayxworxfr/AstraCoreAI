"""工具调用示例：演示流式工具事件、内置工具与自定义工具注册。

用法：
    python examples/tool_calling.py
    python examples/tool_calling.py --web   # 追加联网搜索演示（需配置 TAVILY_API_KEY）
"""

import asyncio
import sys

from dotenv import load_dotenv

from astracore.core.ports.llm import StreamEventType
from astracore.core.ports.tool import ToolParameter, ToolParameterType
from astracore.sdk import AstraCoreClient

load_dotenv()


def _fake_weather(city: str) -> str:
    """模拟天气查询（演示自定义工具注册）。"""
    return f"{city}：晴，22°C，空气质量良好（模拟数据）"


async def main() -> None:
    async with AstraCoreClient() as client:
        profile = client.config.llm.get_profile()
        if not profile.capabilities.tools:
            print(f"⚠ 当前 profile '{profile.id}' 不支持工具调用，跳过本示例")
            return

        # 1. 流式工具调用——打印工具事件
        print("=== 流式工具调用 ===\n")
        print("Q: 今天几点了？帮我计算一下 2 的 10 次方。")
        print("A: ", end="", flush=True)
        async for event in client.chat_stream(
            "今天几点了？帮我计算一下 2 的 10 次方。",
            use_tools=True,
        ):
            if event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                print(f"\n  → 调用工具: {event.tool_call.name}", flush=True)
            elif event.event_type == StreamEventType.TOOL_RESULT:
                result_text = str(event.metadata.get("result", ""))[:80]
                print(f"  ← 工具结果: {result_text}", flush=True)
            elif event.event_type == StreamEventType.TEXT_DELTA:
                print(event.content, end="", flush=True)
        print("\n")

        # 2. 自定义工具注册
        print("=== 自定义工具 ===\n")
        client.register_tool(
            name="get_weather",
            func=_fake_weather,
            description="获取指定城市的天气信息。",
            parameters=[
                ToolParameter(
                    name="city",
                    type=ToolParameterType.STRING,
                    description="城市名称，例如北京、上海",
                    required=True,
                )
            ],
        )
        result = await client.chat("北京天气怎么样？", use_tools=True)
        print(f"Q: 北京天气怎么样？\nA: {result.content}\n")

        # 3. 联网搜索（可选）
        if "--web" in sys.argv:
            print("=== 联网搜索 ===\n")
            q = "Python 3.13 有哪些新特性？"
            print(f"Q: {q}")
            print("A: ", end="", flush=True)
            async for event in client.chat_stream(q, use_tools=True, enable_web=True):
                if event.event_type == StreamEventType.TEXT_DELTA:
                    print(event.content, end="", flush=True)
            print("\n")


def test_main():
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
