"""工具调用示例：流式工具事件、内置工具与自定义工具注册。

用法：
    python examples/tool_calling.py
    python examples/tool_calling.py --web   # 追加联网搜索演示（需配置 TAVILY_API_KEY）
"""

import asyncio

from dotenv import load_dotenv

from astracore.core.ports.llm import StreamEventType
from astracore.core.ports.tool import ToolParameter, ToolParameterType
from astracore.runtime.observability.logger import get_logger, setup_logging
from astracore.sdk import AstraCoreClient

load_dotenv()
setup_logging()
logger = get_logger("astracore.examples.tool_calling")


def _fake_weather(city: str) -> str:
    """模拟天气查询（演示自定义工具注册）。"""
    return f"{city}：晴，22°C，空气质量良好（模拟数据）"


async def main() -> None:
    async with AstraCoreClient() as client:
        profile = client.config.llm.get_profile()
        if not profile.capabilities.tools:
            logger.warning("当前 profile '%s' 不支持工具调用，跳过本示例", profile.id)
            return

        # 1. 流式工具调用——通过 stream_events 观察工具事件
        logger.info("=== 流式工具调用 ===")
        conv = client.conversation(use_tools=True)
        q = "今天几点了？帮我计算一下 2 的 10 次方。"
        logger.info("Q: %s", q)
        print("A: ", end="", flush=True)
        async for event in conv.stream_events(q):
            if event.event_type == StreamEventType.TOOL_CALL and event.tool_call:
                logger.info("→ 调用工具: %s", event.tool_call.name)
            elif event.event_type == StreamEventType.TOOL_RESULT:
                result_text = str(event.metadata.get("result", ""))[:80]
                logger.info("← 工具结果: %s", result_text)
            elif event.event_type == StreamEventType.TEXT_DELTA and event.content:
                print(event.content, end="", flush=True)
        print()

        # 2. 自定义工具注册
        logger.info("=== 自定义工具 ===")
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
        weather_conv = client.conversation(use_tools=True)
        q2 = "北京天气怎么样？"
        result = await weather_conv.send(q2)
        logger.info("Q: %s", q2)
        logger.info("A: %s", result.content)

        # 3. 联网搜索（可选，--web 参数触发）
        import sys as _sys  # noqa: PLC0415
        if "--web" in _sys.argv:
            logger.info("=== 联网搜索 ===")
            web_conv = client.conversation(use_tools=True, enable_web=True)
            q3 = "Python 3.13 有哪些新特性？"
            logger.info("Q: %s", q3)
            print("A: ", end="", flush=True)
            async for chunk in web_conv.stream(q3):
                print(chunk, end="", flush=True)
            print()


def test_main() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    test_main()
